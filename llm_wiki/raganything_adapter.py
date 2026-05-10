"""Native RAG-Anything graph importer.

Reads a `manifest.json` produced by `raganything_refresh` and projects
its parsed `content_list` into LLM-Wiki's controlled `ResearchGraph`,
preserving stable RAG-Anything ↔ LLM-Wiki id mappings and provenance.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Optional

from .research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchGraphBuilder,
    ResearchNode,
    ResearchNodeType,
)


_MULTIMODAL_BLOCK_TYPES = ("image", "table", "equation")


@dataclass(frozen=True)
class RagAnythingImportResult:
    graph: ResearchGraph
    manifest: dict


def _artifact_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rel(root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve())).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def _doc_external_ref(artifact_rel: str, doc_id: str) -> dict:
    return {
        "system": "rag-anything",
        "id": doc_id,
        "type": "document",
        "artifact": artifact_rel,
    }


def _block_summary(block: Mapping[str, object]) -> dict:
    btype = str(block.get("type") or "").lower()
    summary: dict = {"type": btype, "page": block.get("page_idx")}
    if btype == "image":
        summary["img_path"] = block.get("img_path")
        summary["caption"] = list(block.get("img_caption") or [])
    elif btype == "table":
        summary["table_body"] = block.get("table_body") or block.get("table_html")
        summary["caption"] = list(block.get("table_caption") or [])
    elif btype == "equation":
        summary["latex"] = block.get("latex") or block.get("text")
        summary["caption"] = list(block.get("equation_caption") or [])
    elif btype == "text":
        summary["text"] = block.get("text")
    return summary


def _collect_text(content_list: Iterable[Mapping[str, object]]) -> str:
    chunks: list[str] = []
    for block in content_list:
        if str(block.get("type") or "").lower() == "text":
            text = str(block.get("text") or "").strip()
            if text:
                chunks.append(text)
    return "\n\n".join(chunks)


class RagAnythingGraphAdapter:
    """Project a `manifest.json` into LLM-Wiki graph nodes/edges."""

    def __init__(self, project_root: str | Path) -> None:
        self.project_root = Path(project_root).resolve()

    def import_artifact(self, artifact: str | Path) -> RagAnythingImportResult:
        artifact_path = Path(artifact)
        if not artifact_path.is_absolute():
            artifact_path = self.project_root / artifact_path
        artifact_path = artifact_path.resolve()
        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
        artifact_rel = _rel(self.project_root, artifact_path)
        graph, manifest = self.import_payload(
            payload,
            artifact_rel=artifact_rel,
            artifact_sha256=_artifact_sha256(artifact_path),
        )
        return RagAnythingImportResult(graph=graph, manifest=manifest)

    def import_payload(
        self,
        payload: Mapping[str, object],
        *,
        artifact_rel: str = ".llm-wiki/external/raganything/manifest.json",
        artifact_sha256: str = "",
    ) -> tuple[ResearchGraph, dict]:
        documents = payload.get("documents") if isinstance(payload, dict) else None
        if not isinstance(documents, list):
            documents = []
        builder = ResearchGraphBuilder()
        doc_to_node: dict[str, ResearchNode] = {}

        for doc in documents:
            if not isinstance(doc, dict):
                continue
            doc_id = str(doc.get("id") or doc.get("sha256") or "")
            if not doc_id:
                continue
            path = str(doc.get("path") or "")
            content_list = doc.get("content_list") if isinstance(doc.get("content_list"), list) else []
            blocks = [
                _block_summary(b) for b in content_list
                if isinstance(b, dict) and str(b.get("type") or "").lower() in _MULTIMODAL_BLOCK_TYPES
            ]
            description = _collect_text(content_list)
            metadata = {
                "parser": "raganything",
                "parser_version": str(payload.get("parser_version") or ""),
                "external_system": "rag-anything",
                "external_id": doc_id,
                "external_refs": [_doc_external_ref(artifact_rel, doc_id)],
                "multimodal_blocks": blocks,
            }
            equations = [b for b in blocks if b["type"] == "equation"]
            if equations:
                metadata["equations"] = equations
            node = builder.add_node(
                path or doc_id,
                ResearchNodeType.SOURCE_FILE,
                description=description or None,
                source_path=path or None,
                metadata=metadata,
                id_seed=f"raganything:{doc_id}",
            )
            doc_to_node[doc_id] = node

        graph = builder.build()
        manifest = {
            "artifact": artifact_rel,
            "artifact_sha256": artifact_sha256,
            "imported_documents": {doc_id: node.id for doc_id, node in sorted(doc_to_node.items())},
        }
        return graph, manifest


def merge_raganything_graph(
    graph: ResearchGraph,
    *,
    project_root: str | Path,
    artifact: str | Path,
    sync_manifest_path: Optional[str | Path] = None,
) -> tuple[ResearchGraph, dict]:
    """Merge a RAG-Anything manifest into an existing graph and optionally persist sync manifest."""
    adapter = RagAnythingGraphAdapter(project_root)
    result = adapter.import_artifact(artifact)
    nodes_by_id: dict[str, ResearchNode] = {n.id: n for n in graph.nodes}
    for node in result.graph.nodes:
        nodes_by_id[node.id] = node
    edges_by_key: dict[tuple[str, str, str], ResearchEdge] = {
        (e.source, e.type, e.target): e for e in graph.edges
    }
    for edge in result.graph.edges:
        edges_by_key[(edge.source, edge.type, edge.target)] = edge

    merged = ResearchGraph(
        nodes=list(nodes_by_id.values()),
        edges=list(edges_by_key.values()),
    )

    if sync_manifest_path is not None:
        path = Path(sync_manifest_path)
        if not path.is_absolute():
            path = Path(project_root) / path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result.manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    return merged, result.manifest
