"""Native RAG-Anything graph importer.

Reads a `manifest.json` produced by `raganything_refresh` and projects
its parsed `content_list` into Tesserae's controlled `ResearchGraph`,
preserving stable RAG-Anything ↔ Tesserae id mappings and provenance.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Optional

from .research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchGraphBuilder,
    ResearchNode,
    ResearchNodeType,
    truncate,
)

logger = logging.getLogger(__name__)

_MULTIMODAL_BLOCK_TYPES = ("image", "table", "equation")

#: Display label per multimodal block kind for Artifact node names.
_ARTIFACT_LABELS = {"image": "Figure", "table": "Table", "equation": "Equation"}


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


def _artifact_content(
    summary: Mapping[str, object],
    *,
    project_root: Path,
    parsed_dir: str,
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Resolve a multimodal block to ``(content_hash, asset_rel, skip_reason)``.

    The hash input is the block's CONTENT and nothing else — image bytes,
    ``table_body`` verbatim, ``latex`` verbatim. No caption, no page index,
    no path: the hash IS the node's identity seed, and a re-parse into a
    different working_dir must not move node ids (roadmap step 9's
    non-negotiable — the same leak class the byte-idempotence suite guards).

    ``asset_rel`` is the resolved image path relative to ``project_root``
    (forward slashes), returned ONLY when the asset lives under the root —
    an out-of-tree path never lands in graph.json. Non-image kinds return
    ``None`` for it. ``skip_reason`` is set when no content is resolvable;
    the caller skips minting and records the skip loudly.

    Hashing is the import path's FIRST disk dereference of ``img_path``;
    per-figure reads are cheap, but an adversarial manifest can point at a
    huge file — acceptable for now.
    """
    kind = str(summary.get("type") or "")
    if kind == "image":
        raw = str(summary.get("img_path") or "")
        if not raw:
            return None, None, "image block has no img_path"
        candidates: list[Path] = []
        raw_path = Path(raw)
        if raw_path.is_absolute():
            candidates.append(raw_path)
        elif parsed_dir:
            # MinerU img_paths are relative to the parsed output dir, and the
            # producer always writes parsed_dir — so when it is declared, it
            # is the ONLY legitimate resolution. Falling back to the project
            # root here would let an unrelated same-named repo file win and
            # mint an Artifact whose identity describes the wrong bytes
            # (adversarial-review finding): a missing parsed asset must be a
            # loud skip, never a silent wrong-file hit.
            candidates.append(project_root / parsed_dir / raw)
        else:
            # No parsed_dir declared (hand-written manifests): the project
            # root is the only base there is.
            candidates.append(project_root / raw)
        for candidate in candidates:
            if not candidate.is_file():
                continue
            try:
                digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
            except OSError as exc:
                return None, None, f"unreadable image asset: {exc}"
            try:
                rel = str(
                    candidate.resolve().relative_to(project_root.resolve())
                ).replace("\\", "/")
            except ValueError:
                rel = None  # outside the project root — never store the path
            return digest, rel, None
        return None, None, "image asset not found"
    if kind == "table":
        body = str(summary.get("table_body") or "")
        if not body:
            return None, None, "table block has no table_body"
        return hashlib.sha256(body.encode("utf-8")).hexdigest(), None, None
    if kind == "equation":
        latex = str(summary.get("latex") or "")
        if not latex:
            return None, None, "equation block has no latex"
        return hashlib.sha256(latex.encode("utf-8")).hexdigest(), None, None
    return None, None, f"unsupported block kind {kind!r}"


def _collect_text(content_list: Iterable[Mapping[str, object]]) -> str:
    chunks: list[str] = []
    for block in content_list:
        if str(block.get("type") or "").lower() == "text":
            text = str(block.get("text") or "").strip()
            if text:
                chunks.append(text)
    return "\n\n".join(chunks)


class RagAnythingGraphAdapter:
    """Project a `manifest.json` into Tesserae graph nodes/edges."""

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
        artifact_rel: str = ".tesserae/external/raganything/manifest.json",
        artifact_sha256: str = "",
    ) -> tuple[ResearchGraph, dict]:
        documents = payload.get("documents") if isinstance(payload, dict) else None
        if not isinstance(documents, list):
            documents = []
        builder = ResearchGraphBuilder()
        doc_to_node: dict[str, ResearchNode] = {}
        skipped_blocks: list[dict] = []

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
            # "Figure 2" / "Table 3": 1-based within its kind, in content_list
            # order. Counted here over EVERY multimodal block, before the
            # unresolvable ones are dropped below — a document numbers its own
            # figures over all of them, so counting the survivors would shift
            # every ordinal after the first skip and the misnumbering would be
            # invisible (the graph stays well-formed, every figure just points
            # at the wrong caption).
            kind_counts: dict[str, int] = {}
            for summary in blocks:
                kind_counts[summary["type"]] = kind_counts.get(summary["type"], 0) + 1
                summary["ordinal"] = kind_counts[summary["type"]]
            # Resolve each block to its content hash BEFORE the document node
            # is minted: the hash lands on the block summary as the join key
            # between metadata['multimodal_blocks'] (kept verbatim for
            # back-compat) and the first-class Artifact node minted below.
            parsed_dir = str(doc.get("parsed_dir") or "")
            block_content: list[tuple[dict, Optional[str], Optional[str]]] = []
            for summary in blocks:
                content_hash, asset_rel, skip_reason = _artifact_content(
                    summary, project_root=self.project_root, parsed_dir=parsed_dir
                )
                if content_hash is None:
                    # Loud degrade, not silent and not fatal: the block stays
                    # in multimodal_blocks, the skip is logged AND recorded in
                    # the sync manifest, and no path- or caption-derived
                    # pseudo-identity is ever minted in place of the hash.
                    logger.warning(
                        "raganything: not minting Artifact for %s block in %s: %s",
                        summary.get("type"), doc_id, skip_reason,
                    )
                    skipped_blocks.append({
                        "doc_id": doc_id,
                        "type": str(summary.get("type") or ""),
                        "img_path": str(summary.get("img_path") or ""),
                        "reason": str(skip_reason or ""),
                    })
                    continue
                summary["content_hash"] = content_hash
                block_content.append((summary, content_hash, asset_rel))
            description = _collect_text(content_list)
            metadata = {
                "parser": "raganything",
                "parser_version": str(payload.get("parser_version") or ""),
                "external_system": "rag-anything",
                "external_id": doc_id,
                "external_refs": [_doc_external_ref(artifact_rel, doc_id)],
                "multimodal_blocks": blocks,
            }
            # Content-derived and deterministic — and it is what
            # federation.identity_key keys SourceDocument merges on, which
            # until now never fired for raganything documents.
            doc_sha = str(doc.get("sha256") or "").strip()
            if doc_sha:
                metadata["content_hash"] = doc_sha
            equations = [b for b in blocks if b["type"] == "equation"]
            if equations:
                metadata["equations"] = equations
            # Bug B fix: raganything-projected documents are research-layer
            # source documents, not code-graph SourceFile nodes. Emitting
            # SOURCE_DOCUMENT puts them in the main graph (instead of
            # code_graph.json via partition_graph) and gives them a public
            # wiki page + a ``sources`` group in the visual graph payload.
            node = builder.add_node(
                path or doc_id,
                ResearchNodeType.SOURCE_DOCUMENT,
                description=description,
                source_path=path or None,
                metadata=metadata,
                id_seed=f"raganything:{doc_id}",
            )
            doc_to_node[doc_id] = node

            # First-class Artifact evidence nodes (roadmap step 9): one per
            # multimodal block with resolvable content, id seeded from the
            # CONTENT hash — byte-identical content in one or many documents
            # collapses to ONE node (graph identity == federation identity),
            # with a part_of edge to each owning document. The edge mirrors
            # ``_add_evidence``'s span→paper shape; ``evidenced_by`` from
            # citing claims is a consumer concern (no claims exist here).
            seen_artifact_edges: set[tuple[str, str]] = set()
            for summary, content_hash, asset_rel in block_content:
                kind = str(summary.get("type") or "")
                caption_list = [str(c) for c in (summary.get("caption") or [])]
                first_caption = caption_list[0].strip() if caption_list else ""
                label = _ARTIFACT_LABELS.get(kind, "Artifact")
                name = (
                    f"{label}: {truncate(first_caption, 72)}"
                    if first_caption
                    else f"{label} {content_hash[:12]}"
                )
                if kind == "table":
                    artifact_description = str(summary.get("table_body") or "")
                elif kind == "equation":
                    artifact_description = str(summary.get("latex") or "")
                else:
                    artifact_description = "\n".join(caption_list)
                artifact_metadata: dict = {
                    "parser": "raganything",
                    "kind": kind,
                    "content_hash": content_hash,
                    "page": summary.get("page"),
                    "caption": caption_list,
                }
                if asset_rel:
                    artifact_metadata["asset_path"] = asset_rel
                # The owning document's path may be ABSOLUTE for registered
                # out-of-tree sources (raganything_refresh stores those
                # verbatim). The document node keeps that pre-existing
                # behaviour, but an Artifact node must never carry a
                # machine-specific path (adversarial-review finding — the
                # exact leak class its content-hashed id exists to prevent).
                artifact_source_path = (
                    path if path and not Path(path).is_absolute() else None
                )
                artifact_node = builder.add_node(
                    name,
                    ResearchNodeType.ARTIFACT,
                    description=artifact_description,
                    source_path=artifact_source_path,
                    metadata=artifact_metadata,
                    id_seed=f"raganything:artifact:{kind}:{content_hash}",
                )
                edge_key = (artifact_node.id, node.id)
                if edge_key not in seen_artifact_edges:
                    seen_artifact_edges.add(edge_key)
                    # ordinal/page/caption are facts about the (artifact,
                    # document) PAIR, but the node is doc-agnostic by design
                    # (content-hashed id), so on a shared artifact
                    # prefer_research_node keeps whichever document merged
                    # first and every later owner's page silently loses. The
                    # edge is per-owner by construction — one per owning
                    # document — so it can carry them without lying, and when
                    # the same bytes appear twice in ONE document the earlier
                    # position wins (``seen_artifact_edges``, deterministic).
                    # The node keeps its copies for back-compat; this adds, it
                    # does not move. ``evidence`` stays None: every edge.evidence
                    # in this codebase is a verbatim span that licensed the
                    # assertion, and a caption asserts nothing.
                    builder.add_edge(
                        artifact_node,
                        "part_of",
                        node,
                        metadata={
                            "kind": kind,
                            "ordinal": summary.get("ordinal"),
                            "page": summary.get("page"),
                            "caption": caption_list,
                        },
                    )

        graph = builder.build()
        manifest = {
            "artifact": artifact_rel,
            "artifact_sha256": artifact_sha256,
            "imported_documents": {doc_id: node.id for doc_id, node in sorted(doc_to_node.items())},
        }
        if skipped_blocks:
            manifest["skipped_blocks"] = sorted(
                skipped_blocks,
                key=lambda entry: (entry["doc_id"], entry["type"], entry["img_path"]),
            )
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
