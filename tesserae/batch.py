"""Incremental batch ingestion helpers."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Protocol

from .research_graph import ResearchGraph, link_paper_repo_pairs, prefer_research_node


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

        try:
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
                        progress.advance()
                    continue
                if limit is not None and processed >= limit:
                    break
                graph = self.extractor.extract_text(content, str(file_path), source_kind)
                graphs.append(graph)
                processed += 1
                processed_paths.append(key)
                entry: Dict[str, object] = {"sha256": digest, "source_kind": source_kind}
                # Only present when true — an entry for a clean extraction stays
                # byte-identical to what prior versions wrote.
                if getattr(self.extractor, "last_was_fallback", False):
                    entry["fallback"] = True
                    fallback_paths.append(key)
                manifest[key] = entry
                self._write_manifest(manifest)
                if progress is not None:
                    progress.advance()
        finally:
            self._write_manifest(manifest)
            if progress is not None:
                progress.extract_done(processed)
        if fallback_paths:
            print(
                f"  {len(fallback_paths)} doc(s) fell back to deterministic "
                f"extraction; re-attempt with `compile --retry-fallbacks`.",
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
