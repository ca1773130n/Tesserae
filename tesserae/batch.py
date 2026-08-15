"""Incremental batch ingestion helpers."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Protocol

from .llm_json import cache_tally
from .research_graph import ResearchGraph, link_paper_repo_pairs, prefer_research_node

#: Documents extracted concurrently. Each one is a blocking CLI subprocess
#: (``codex exec`` / ``claude -p``) taking ~a minute, so a sequential loop makes
#: wall-clock the literal sum of every model round-trip — measured at ~2h40m for
#: 161 documents. 4 is deliberately modest: the ceiling is the provider account's
#: rate limit, not this machine. ``TESSERAE_EXTRACT_CONCURRENCY=1`` restores the
#: old strictly-sequential behaviour.
_DEFAULT_EXTRACT_CONCURRENCY = 4


def _extract_concurrency() -> int:
    raw = (os.environ.get("TESSERAE_EXTRACT_CONCURRENCY") or "").strip()
    if not raw:
        return _DEFAULT_EXTRACT_CONCURRENCY
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        print(f"warning: ignoring TESSERAE_EXTRACT_CONCURRENCY={raw!r} (not an integer); "
              f"using {_DEFAULT_EXTRACT_CONCURRENCY}.", file=sys.stderr)
        return _DEFAULT_EXTRACT_CONCURRENCY


class ExtractorLike(Protocol):
    def extract_file(self, path: str | Path, source_kind: str = "SourceDocument") -> ResearchGraph: ...
    def extract_text(self, content: str, source_path: Optional[str], source_kind: str = "SourceDocument") -> ResearchGraph: ...


@dataclass
class BatchIngestResult:
    graph: ResearchGraph
    graphs: List[ResearchGraph] = field(default_factory=list)
    processed: int = 0
    skipped: int = 0
    manifest_path: Optional[Path] = None
    processed_paths: List[str] = field(default_factory=list)
    skipped_paths: List[str] = field(default_factory=list)
    #: Docs whose typed extraction failed and were served by the deterministic
    #: baseline instead — the `compile --retry-fallbacks` work list.
    fallback_paths: List[str] = field(default_factory=list)

    def model_dump(self) -> Dict[str, object]:
        return {
            "processed": self.processed,
            "skipped": self.skipped,
            "manifest_path": str(self.manifest_path) if self.manifest_path else None,
            "processed_paths": self.processed_paths,
            "skipped_paths": self.skipped_paths,
            "fallback_paths": self.fallback_paths,
        }


class BatchIngestRunner:
    def __init__(self, extractor: ExtractorLike, manifest_path: str | Path) -> None:
        self.extractor = extractor
        self.manifest_path = Path(manifest_path)

    def run(
        self,
        paths: Iterable[str | Path],
        source_kind: str = "SourceDocument",
        changed_only: bool = False,
        limit: Optional[int] = None,
        progress: Optional["CompileProgress"] = None,
        retry_fallbacks: bool = False,
    ) -> BatchIngestResult:
        manifest = self._load_manifest()
        graphs: List[ResearchGraph] = []
        processed_paths: List[str] = []
        skipped_paths: List[str] = []
        fallback_paths: List[str] = []
        processed = 0
        skipped = 0

        # Materialize so the live progress bar knows the total up front.
        path_list = [Path(p) for p in paths]
        if progress is not None:
            progress.scan(len(path_list))
            progress.extract_start(len(path_list))

        # Decide the work-list FIRST, in path order, so concurrency below can
        # never change which documents are extracted — only how fast.
        todo: List[tuple[Path, str, str]] = []  # (path, key, digest)
        for file_path in path_list:
            content = read_markdown_text(file_path)
            digest = sha256_text(content)
            key = str(file_path)
            prior = manifest.get(key, {})
            # A doc whose typed extraction fell back to deterministic is
            # content-identical to one that extracted cleanly, so plain
            # changed-only skips it forever — the degradation is permanent
            # until the file itself changes. ``retry_fallbacks`` re-attempts
            # exactly those, mirroring ``distill --retry-fallbacks``.
            if (
                changed_only
                and prior.get("sha256") == digest
                and not (retry_fallbacks and prior.get("fallback") is True)
            ):
                skipped += 1
                skipped_paths.append(key)
                if progress is not None:
                    progress.advance(path=key, outcome="skip")
                continue
            if limit is not None and len(todo) >= limit:
                break
            todo.append((file_path, key, digest))

        workers = _extract_concurrency()
        results: List[Optional[tuple[ResearchGraph, str, Dict[str, object], bool]]] = [None] * len(todo)
        lock = threading.Lock()

        def _extract_one(index: int) -> None:
            file_path, key, digest = todo[index]
            content = read_markdown_text(file_path)
            # Bracket the extraction to learn what this document COST. The
            # tallies are thread-local and this whole function runs on one
            # worker thread, so the delta is this document's alone even at
            # TESSERAE_EXTRACT_CONCURRENCY > 1. A document large enough to be
            # chunked makes several calls; "cache" therefore means EVERY call it
            # made was served from disk, which is the only reading under which
            # the label predicts the document was free.
            hits_before, misses_before = cache_tally()
            graph = self.extractor.extract_text(content, str(file_path), source_kind)
            hits_after, misses_after = cache_tally()
            if misses_after > misses_before:
                outcome = "llm"
            elif hits_after > hits_before:
                outcome = "cache"
            else:
                # The cache was never consulted: the deterministic extractor, or
                # TESSERAE_LLM_CACHE=0. Report no label rather than guess one.
                outcome = None
            entry: Dict[str, object] = {"sha256": digest, "source_kind": source_kind}
            # Only present when true — an entry for a clean extraction stays
            # byte-identical to what prior versions wrote. The flag is
            # thread-local in SelectiveClaudeResearchExtractor, so this reads
            # THIS call's outcome even with workers > 1.
            fell_back = bool(getattr(self.extractor, "last_was_fallback", False))
            if fell_back:
                entry["fallback"] = True
            results[index] = (graph, key, entry, fell_back)
            with lock:
                # Persist incrementally so a killed compile keeps the ground it
                # took. Ordering doesn't matter: the manifest is a dict keyed by
                # path and serialized with sort_keys.
                manifest[key] = entry
                self._write_manifest(manifest)
                if progress is not None:
                    progress.advance(path=key, outcome=outcome)

        try:
            if workers > 1 and len(todo) > 1:
                # Extraction is one blocking subprocess per document, so threads
                # (not processes) are the right tool — the GIL is released for
                # the entire wait.
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    for fut in as_completed([pool.submit(_extract_one, i) for i in range(len(todo))]):
                        fut.result()  # re-raise worker exceptions on this thread
            else:
                for i in range(len(todo)):
                    _extract_one(i)
        finally:
            self._write_manifest(manifest)
            if progress is not None:
                progress.extract_done(sum(1 for r in results if r is not None))

        # Collect in PATH order, never completion order: merge_graphs resolves
        # duplicate node ids via prefer_research_node, so a different order is a
        # different graph. This is what keeps a parallel run byte-identical to a
        # sequential one.
        for item in results:
            if item is None:
                continue
            graph, key, _entry, fell_back = item
            graphs.append(graph)
            processed += 1
            processed_paths.append(key)
            if fell_back:
                fallback_paths.append(key)
        if fallback_paths:
            print(
                # ``--retry-fallbacks`` only NARROWS the changed-only work list
                # (see the skip above); on its own it is a whole-corpus
                # re-extract, so the hint must name both flags or it sends the
                # operator into an hours-long full compile.
                f"  {len(fallback_paths)} doc(s) fell back to deterministic "
                f"extraction; re-attempt with "
                f"`compile --changed-only --retry-fallbacks`.",
                file=sys.stderr,
            )
        return BatchIngestResult(
            graph=merge_graphs(graphs),
            graphs=graphs,
            processed=processed,
            skipped=skipped,
            manifest_path=self.manifest_path,
            processed_paths=processed_paths,
            skipped_paths=skipped_paths,
            fallback_paths=fallback_paths,
        )

    def _load_manifest(self) -> Dict[str, Dict[str, object]]:
        if not self.manifest_path.exists():
            return {}
        try:
            payload = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError) as exc:
            print(f"warning: corrupt manifest {self.manifest_path}: {exc}; starting fresh", file=sys.stderr)
            return {}
        files = payload.get("files", payload if isinstance(payload, dict) else {})
        return files if isinstance(files, dict) else {}

    def _write_manifest(self, manifest: Dict[str, Dict[str, object]]) -> None:
        # PID + random suffix so concurrent writers don't collide on a
        # shared `<x>.tmp` and crash one of them with FileNotFoundError
        # on os.replace (same class of bug fixed in
        # tesserae/session_graph.py::_write_cache).
        import secrets
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.manifest_path.with_suffix(
            f".tmp.{os.getpid()}.{secrets.token_hex(4)}"
        )
        try:
            tmp.write_text(json.dumps({"files": manifest}, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            os.replace(tmp, self.manifest_path)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass


def merge_graphs(graphs: Iterable[ResearchGraph]) -> ResearchGraph:
    nodes = {}
    edges = {}
    for graph in graphs:
        for node in graph.nodes:
            existing = nodes.get(node.id)
            nodes[node.id] = prefer_research_node(existing, node) if existing else node
        for edge in graph.edges:
            edges[(edge.source, edge.type, edge.target)] = edge
    merged = ResearchGraph(nodes=list(nodes.values()), edges=list(edges.values()))
    return link_paper_repo_pairs(merged)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_markdown_text(path: str | Path) -> str:
    """Read markdown robustly, replacing rare invalid byte sequences.

    The research corpus can contain scraped `raw.md` files with malformed UTF-8;
    replacing invalid bytes keeps batch ingestion moving while preserving a stable
    content hash for changed-only manifests.
    """
    return Path(path).read_text(encoding="utf-8", errors="replace")
