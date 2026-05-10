"""Native Understand Anything graph importer.

Understand Anything remains an independent companion tool. This adapter imports
its ``.understand-anything/knowledge-graph.json`` artifact into LLM-Wiki's
controlled ``ResearchGraph`` while preserving external provenance and stable
UA ↔ LLM-Wiki id mappings.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

from .research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchGraphBuilder,
    ResearchNode,
    ResearchNodeType,
    normalize_display_name,
    prefer_research_node,
    stable_id,
)


_UA_NODE_TYPE_MAP: dict[str, ResearchNodeType] = {
    "file": ResearchNodeType.SOURCE_FILE,
    "source_file": ResearchNodeType.SOURCE_FILE,
    "sourcefile": ResearchNodeType.SOURCE_FILE,
    "module": ResearchNodeType.CODE_MODULE,
    "package": ResearchNodeType.CODE_MODULE,
    "class": ResearchNodeType.CODE_CLASS,
    "component": ResearchNodeType.CODE_CLASS,
    "function": ResearchNodeType.CODE_FUNCTION,
    "method": ResearchNodeType.CODE_FUNCTION,
    "dependency": ResearchNodeType.DEPENDENCY,
    "library": ResearchNodeType.DEPENDENCY,
    "concept": ResearchNodeType.CONCEPT,
    "topic": ResearchNodeType.CONCEPT,
    "feature": ResearchNodeType.CAPABILITY,
    "capability": ResearchNodeType.CAPABILITY,
    "pattern": ResearchNodeType.ARCHITECTURE_PATTERN,
    "architecture_pattern": ResearchNodeType.ARCHITECTURE_PATTERN,
}

_UA_EDGE_TYPE_MAP: dict[str, str] = {
    "contains": "contains",
    "defines": "defines",
    "imports": "imports",
    "calls": "calls",
    "uses": "uses",
    "used_by": "uses",
    "depends_on": "uses",
    "documents": "documents",
    "related_to": "shares_concept_with",
    "similar_to": "shares_concept_with",
}

_CONCEPTISH_TYPES = {
    ResearchNodeType.CONCEPT,
    ResearchNodeType.TECHNICAL_TERM,
    ResearchNodeType.METHODOLOGICAL_CONCEPT,
    ResearchNodeType.MATHEMATICAL_CONCEPT,
    ResearchNodeType.ALGORITHM,
    ResearchNodeType.ARCHITECTURE_PATTERN,
    ResearchNodeType.CAPABILITY,
    ResearchNodeType.TASK,
}


@dataclass(frozen=True)
class UnderstandAnythingImportResult:
    graph: ResearchGraph
    manifest: dict


def _artifact_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rel(root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve())).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def _ua_node_id(raw: Mapping[str, object]) -> str:
    for key in ("id", "nodeId", "key"):
        value = raw.get(key)
        if value:
            return str(value)
    name = str(raw.get("name") or raw.get("label") or "unnamed")
    ntype = str(raw.get("type") or "node")
    path = str(raw.get("filePath") or raw.get("path") or "")
    return f"{ntype}:{path}:{name}"


def _ua_node_type(raw_type: object) -> ResearchNodeType:
    key = str(raw_type or "concept").strip().lower().replace("-", "_").replace(" ", "_")
    return _UA_NODE_TYPE_MAP.get(key, ResearchNodeType.CONCEPT)


def _ua_edge_type(raw_type: object) -> str:
    key = str(raw_type or "related_to").strip().lower().replace("-", "_").replace(" ", "_")
    return _UA_EDGE_TYPE_MAP.get(key, "shares_concept_with")


def _external_ref(artifact_rel: str, ua_id: str, ua_type: str) -> dict:
    return {
        "system": "understand-anything",
        "id": ua_id,
        "type": ua_type,
        "artifact": artifact_rel,
    }


def _metadata_with_ref(raw: Mapping[str, object], *, artifact_rel: str, ua_id: str) -> dict:
    ua_type = str(raw.get("type") or "node")
    meta = {
        "external_system": "understand-anything",
        "external_id": ua_id,
        "ua_type": ua_type,
        "external_refs": [_external_ref(artifact_rel, ua_id, ua_type)],
    }
    for src_key, dst_key in (
        ("filePath", "file_path"),
        ("path", "file_path"),
        ("language", "language"),
        ("layer", "ua_layer"),
        ("confidence", "confidence"),
    ):
        value = raw.get(src_key)
        if value not in (None, "") and dst_key not in meta:
            meta[dst_key] = value
    raw_meta = raw.get("metadata")
    if isinstance(raw_meta, dict):
        meta["ua_metadata"] = raw_meta
    return meta


def _merge_external_refs(existing: ResearchNode, incoming: ResearchNode) -> ResearchNode:
    merged = prefer_research_node(existing, incoming)
    refs: list[object] = []
    for node in (existing, incoming):
        node_refs = node.metadata.get("external_refs")
        if isinstance(node_refs, list):
            refs.extend(node_refs)
    deduped: list[object] = []
    seen: set[str] = set()
    for ref in refs:
        key = json.dumps(ref, sort_keys=True, ensure_ascii=False) if isinstance(ref, dict) else str(ref)
        if key not in seen:
            seen.add(key)
            deduped.append(ref)
    metadata = dict(merged.metadata)
    if deduped:
        metadata["external_refs"] = deduped
    return ResearchNode(
        id=merged.id,
        name=merged.name,
        type=merged.type,
        aliases=merged.aliases,
        description=merged.description,
        source_path=merged.source_path,
        metadata=metadata,
    )


def _add_or_merge_node(nodes: dict[str, ResearchNode], node: ResearchNode) -> ResearchNode:
    existing = nodes.get(node.id)
    if existing is None and node.type in _CONCEPTISH_TYPES:
        node_key = normalize_display_name(node.name).casefold()
        for candidate in nodes.values():
            names = {candidate.name, *candidate.aliases}
            if candidate.type in _CONCEPTISH_TYPES and any(normalize_display_name(name).casefold() == node_key for name in names):
                existing = candidate
                break
    if existing:
        merged = _merge_external_refs(existing, node)
        nodes.pop(existing.id, None)
        nodes[merged.id] = merged
        return merged
    nodes[node.id] = node
    return node


class UnderstandAnythingGraphAdapter:
    """Import Understand Anything JSON as LLM-Wiki graph nodes/edges."""

    def __init__(self, project_root: str | Path) -> None:
        self.project_root = Path(project_root).resolve()

    def import_artifact(self, artifact: str | Path) -> UnderstandAnythingImportResult:
        artifact_path = Path(artifact)
        if not artifact_path.is_absolute():
            artifact_path = self.project_root / artifact_path
        artifact_path = artifact_path.resolve()
        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
        artifact_rel = _rel(self.project_root, artifact_path)
        graph, manifest = self.import_payload(payload, artifact_rel=artifact_rel, artifact_sha256=_artifact_sha256(artifact_path))
        return UnderstandAnythingImportResult(graph=graph, manifest=manifest)

    def import_payload(
        self,
        payload: Mapping[str, object],
        *,
        artifact_rel: str = ".understand-anything/knowledge-graph.json",
        artifact_sha256: str = "",
    ) -> tuple[ResearchGraph, dict]:
        nodes_raw = payload.get("nodes", []) if isinstance(payload, dict) else []
        edges_raw = payload.get("edges", []) if isinstance(payload, dict) else []
        builder = ResearchGraphBuilder()
        ua_to_node: dict[str, ResearchNode] = {}

        if isinstance(nodes_raw, list):
            for raw in nodes_raw:
                if not isinstance(raw, dict):
                    continue
                ua_id = _ua_node_id(raw)
                name = str(raw.get("name") or raw.get("label") or ua_id)
                node_type = _ua_node_type(raw.get("type"))
                description = str(raw.get("summary") or raw.get("description") or "")
                id_seed = normalize_display_name(name) if node_type in _CONCEPTISH_TYPES else f"ua:{ua_id}"
                node = builder.add_node(
                    name,
                    node_type,
                    description=description,
                    source_path=artifact_rel,
                    metadata=_metadata_with_ref(raw, artifact_rel=artifact_rel, ua_id=ua_id),
                    id_seed=id_seed,
                )
                ua_to_node[ua_id] = node

        if isinstance(edges_raw, list):
            for raw in edges_raw:
                if not isinstance(raw, dict):
                    continue
                source_id = str(raw.get("source") or raw.get("from") or "")
                target_id = str(raw.get("target") or raw.get("to") or "")
                source = ua_to_node.get(source_id)
                target = ua_to_node.get(target_id)
                if not source or not target:
                    continue
                raw_type = str(raw.get("type") or raw.get("relationship") or "related_to")
                edge_type = _ua_edge_type(raw_type)
                builder.add_edge(
                    source,
                    edge_type,
                    target,
                    evidence=str(raw.get("summary") or raw.get("evidence") or "") or None,
                    metadata={
                        "external_system": "understand-anything",
                        "ua_edge_type": raw_type,
                        "artifact": artifact_rel,
                    },
                )

        graph = builder.build()
        manifest = {
            "artifact": artifact_rel,
            "artifact_sha256": artifact_sha256,
            "imported_nodes": {ua_id: node.id for ua_id, node in sorted(ua_to_node.items())},
            "imported_edges": [
                {"source": edge.source, "type": edge.type, "target": edge.target}
                for edge in graph.edges
            ],
        }
        return graph, manifest


def merge_understand_anything_graph(
    graph: ResearchGraph,
    *,
    project_root: str | Path,
    artifact: str | Path,
    sync_manifest_path: Optional[str | Path] = None,
) -> tuple[ResearchGraph, dict]:
    """Merge a UA artifact into an existing graph and optionally write manifest."""
    adapter = UnderstandAnythingGraphAdapter(project_root)
    result = adapter.import_artifact(artifact)
    nodes: dict[str, ResearchNode] = {}
    for node in graph.nodes:
        _add_or_merge_node(nodes, node)
    ua_id_to_merged: dict[str, str] = {}
    for ua_id, node_id in result.manifest.get("imported_nodes", {}).items():
        imported = next((node for node in result.graph.nodes if node.id == node_id), None)
        if imported is None:
            continue
        merged = _add_or_merge_node(nodes, imported)
        ua_id_to_merged[str(ua_id)] = merged.id

    edges: dict[tuple[str, str, str], ResearchEdge] = {}
    for edge in graph.edges:
        edges[(edge.source, edge.type, edge.target)] = edge
    for edge in result.graph.edges:
        source = nodes.get(edge.source)
        target = nodes.get(edge.target)
        # Concept merges can replace the imported node with an existing node id.
        source_id = source.id if source else edge.source
        target_id = target.id if target else edge.target
        remapped = ResearchEdge(
            source=source_id,
            target=target_id,
            type=edge.type,
            evidence=edge.evidence,
            metadata=edge.metadata,
        )
        edges[(remapped.source, remapped.type, remapped.target)] = remapped

    manifest = dict(result.manifest)
    manifest["imported_nodes"] = ua_id_to_merged
    merged_graph = ResearchGraph(nodes=list(nodes.values()), edges=list(edges.values()))
    if sync_manifest_path is not None:
        manifest_path = Path(sync_manifest_path)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return merged_graph, manifest
