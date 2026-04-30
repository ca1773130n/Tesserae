"""Incremental batch ingestion helpers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Protocol, Sequence

from .research_graph import ResearchGraph, link_paper_repo_pairs, prefer_research_node


class ExtractorLike(Protocol):
    def extract_file(self, path: str | Path, source_kind: str = "SourceDocument") -> ResearchGraph: ...


@dataclass
class BatchIngestResult:
    graph: ResearchGraph
    graphs: List[ResearchGraph] = field(default_factory=list)
    processed: int = 0
    skipped: int = 0
    manifest_path: Optional[Path] = None
    processed_paths: List[str] = field(default_factory=list)
    skipped_paths: List[str] = field(default_factory=list)

    def model_dump(self) -> Dict[str, object]:
        return {
            "processed": self.processed,
            "skipped": self.skipped,
            "manifest_path": str(self.manifest_path) if self.manifest_path else None,
            "processed_paths": self.processed_paths,
            "skipped_paths": self.skipped_paths,
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
    ) -> BatchIngestResult:
        manifest = self._load_manifest()
        graphs: List[ResearchGraph] = []
        processed_paths: List[str] = []
        skipped_paths: List[str] = []
        processed = 0
        skipped = 0

        for path in paths:
            file_path = Path(path)
            digest = sha256_text(read_markdown_text(file_path))
            key = str(file_path)
            if changed_only and manifest.get(key, {}).get("sha256") == digest:
                skipped += 1
                skipped_paths.append(key)
                continue
            if limit is not None and processed >= limit:
                break
            graph = self.extractor.extract_file(file_path, source_kind=source_kind)
            graphs.append(graph)
            processed += 1
            processed_paths.append(key)
            manifest[key] = {"sha256": digest, "source_kind": source_kind}

        self._write_manifest(manifest)
        return BatchIngestResult(
            graph=merge_graphs(graphs),
            graphs=graphs,
            processed=processed,
            skipped=skipped,
            manifest_path=self.manifest_path,
            processed_paths=processed_paths,
            skipped_paths=skipped_paths,
        )

    def _load_manifest(self) -> Dict[str, Dict[str, object]]:
        if not self.manifest_path.exists():
            return {}
        payload = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        files = payload.get("files", payload if isinstance(payload, dict) else {})
        return files if isinstance(files, dict) else {}

    def _write_manifest(self, manifest: Dict[str, Dict[str, object]]) -> None:
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        self.manifest_path.write_text(json.dumps({"files": manifest}, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


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
