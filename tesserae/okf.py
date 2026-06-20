"""OKF (Open Knowledge Format) v0.1 import/export.

OKF v0.1 (Google Cloud, Apache-2.0 draft, ~2026-06): a directory tree of
Markdown files, each with YAML frontmatter whose only REQUIRED field is a
non-empty ``type``. Relationships are relative Markdown links between *Concept
IDs* (a file's path minus ``.md``). Reserved files: ``index.md`` (entry point)
and ``log.md`` (changelog). Consumers must tolerate unknown types/keys and
broken links.

Tesserae round-trips its OWN bundles losslessly via an ``x_tesserae`` frontmatter
namespace (real node id + typed edges); foreign OKF bundles import best-effort
(unknown ``type`` -> ``Concept`` with the original kept in ``metadata.okf_type``;
untyped body links -> ``references`` edges).

ponytail: this module is the whole OKF surface — a focused reader/writer beats
bending the Obsidian projector, which is wikilink/dataview-specific.
"""

from __future__ import annotations

import hashlib
import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

from .markdown_projection import directory_for_node, unique_slugs
from .research_graph import (
    ALLOWED_EDGE_TYPES,
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
)

_RELATIONS_MARKER = "<!-- okf:relations -->"
_FOREIGN_EDGE_TYPE = "references"  # untyped OKF relative link -> this edge type
_FOREIGN_NODE_TYPE = ResearchNodeType.CONCEPT  # unknown OKF type -> this
_RESERVED = {"index", "log"}


# --------------------------------------------------------------------------- #
# Export                                                                       #
# --------------------------------------------------------------------------- #

def _concept_ids(graph: ResearchGraph) -> Dict[str, str]:
    """``node_id -> OKF concept id`` (relative path, no ``.md``), deterministic.

    Stub tombstones are excluded (they have no content); everything else is
    exported so a Tesserae bundle round-trips to the same graph.
    """
    slug_by_id = unique_slugs(graph.nodes)
    out: Dict[str, str] = {}
    taken: Dict[str, str] = {}
    # ``unique_slugs`` maps same-name nodes to ONE canonical slug, so two
    # distinct nodes can collide on the same concept path and one would
    # overwrite the other's file (lossy round-trip). Disambiguate collisions
    # with a short stable hash of the node id; sorted for deterministic output.
    for node in sorted(graph.nodes, key=lambda n: n.id):
        if node.type == ResearchNodeType.STUB:
            continue
        rel_dir = directory_for_node(node)
        slug = slug_by_id[node.id]
        base = f"{rel_dir}/{slug}" if rel_dir else slug
        concept = base
        if taken.get(concept, node.id) != node.id:
            concept = f"{base}-{hashlib.sha1(node.id.encode('utf-8')).hexdigest()[:8]}"
        taken[concept] = node.id
        out[node.id] = concept
    return out


def _frontmatter(data: dict) -> str:
    return "---\n" + yaml.safe_dump(data, sort_keys=True, allow_unicode=True) + "---\n\n"


def _rel_link(from_concept: str, to_concept: str) -> str:
    """POSIX relative path from one concept's file to another's ``.md``."""
    from_dir = os.path.dirname(from_concept)
    rel = os.path.relpath(to_concept + ".md", from_dir or ".")
    return rel.replace(os.sep, "/")


def write_okf_bundle(graph: ResearchGraph, out_dir: str | Path) -> List[Path]:
    """Write ``graph`` as an OKF v0.1 bundle under ``out_dir``. Deterministic."""
    root = Path(out_dir)
    root.mkdir(parents=True, exist_ok=True)
    # Clear prior *.md so a re-export is a deterministic projection of the
    # CURRENT graph (a deleted node must not linger as a stale concept file).
    for stale in root.rglob("*.md"):
        try:
            stale.unlink()
        except OSError:
            pass
    cid = _concept_ids(graph)
    node_by_id = {n.id: n for n in graph.nodes}
    out_edges: Dict[str, List[ResearchEdge]] = defaultdict(list)
    for e in graph.edges:
        if e.source in cid and e.target in cid:
            out_edges[e.source].append(e)

    written: List[Path] = []
    for node in sorted(graph.nodes, key=lambda n: n.id):
        if node.id not in cid:
            continue
        concept = cid[node.id]
        path = root / f"{concept}.md"
        path.parent.mkdir(parents=True, exist_ok=True)

        edges = sorted(out_edges[node.id], key=lambda e: (e.type, cid[e.target]))
        x_edges = [
            {"target": cid[e.target], "type": e.type,
             **({"evidence": e.evidence} if e.evidence else {}),
             **({"metadata": e.metadata} if e.metadata else {})}
            for e in edges
        ]
        x_tess: dict = {"id": node.id}
        if node.aliases:
            x_tess["aliases"] = list(node.aliases)
        if node.source_path:
            x_tess["source_path"] = node.source_path
        if node.metadata:
            x_tess["metadata"] = node.metadata
        if x_edges:
            x_tess["edges"] = x_edges

        fm = {"type": node.type.value, "name": node.name, "x_tesserae": x_tess}

        lines = [(node.description or "").strip(), "", _RELATIONS_MARKER, "## Relations", ""]
        for e in edges:
            tgt = node_by_id.get(e.target)
            tname = tgt.name if tgt else e.target
            lines.append(f"- {e.type}: [{tname}]({_rel_link(concept, cid[e.target])})")
        body = "\n".join(lines).rstrip() + "\n"
        path.write_text(_frontmatter(fm) + body, encoding="utf-8")
        written.append(path)

    written.append(_write_index(root, graph, cid))
    written.append(_write_log(root, graph, cid))
    return written


def _write_index(root: Path, graph: ResearchGraph, cid: Dict[str, str]) -> Path:
    node_by_id = {n.id: n for n in graph.nodes}
    lines = ["---", "type: index", "name: Knowledge Base", "---", "", "# Knowledge Base", ""]
    for nid in sorted(cid, key=lambda i: cid[i]):
        n = node_by_id[nid]
        lines.append(f"- [{n.name}]({cid[nid]}.md) ({n.type.value})")
    path = root / "index.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _write_log(root: Path, graph: ResearchGraph, cid: Dict[str, str]) -> Path:
    # ponytail: derive the log from in-graph Session/Event timeline data only —
    # never wall-clock now() — so the bundle stays reproducible.
    sessions = sorted(
        (n for n in graph.nodes if n.type == ResearchNodeType.SESSION and n.id in cid),
        key=lambda n: (str((n.metadata or {}).get("started_at") or ""), n.id),
    )
    lines = ["---", "type: log", "name: Changelog", "---", "", "# Changelog", ""]
    for n in sessions:
        when = str((n.metadata or {}).get("started_at") or "").strip()
        lines.append(f"- {when + ' — ' if when else ''}[{n.name}]({cid[n.id]}.md)")
    path = root / "log.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# Import                                                                       #
# --------------------------------------------------------------------------- #

def _split_frontmatter(text: str) -> Tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.DOTALL)
    if not m:
        return {}, text
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return {}, text
    return (fm if isinstance(fm, dict) else {}), m.group(2)


def _coerce_node_type(value: str) -> Tuple[ResearchNodeType, Optional[str]]:
    try:
        return ResearchNodeType(value), None
    except ValueError:
        return _FOREIGN_NODE_TYPE, value  # keep original in metadata.okf_type


def _strip_relations(body: str) -> str:
    return body.split(_RELATIONS_MARKER, 1)[0].strip()


def _first_h1(body: str) -> str:
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def _body_link_concepts(body: str, concept: str, root: Path) -> List[str]:
    """Relative ``.md`` links in ``body`` resolved to target concept ids."""
    out: List[str] = []
    from_dir = os.path.dirname(concept)
    for href in re.findall(r"\]\(([^)]+\.md)\)", body):
        if "://" in href:
            continue
        target = os.path.normpath(os.path.join(from_dir, href))[:-3]  # drop .md
        out.append(target.replace(os.sep, "/"))
    return out


def read_okf_bundle(in_dir: str | Path) -> ResearchGraph:
    """Parse an OKF v0.1 bundle into a ResearchGraph. Tolerant of malformed files.

    Tesserae-authored files (``x_tesserae`` present) round-trip losslessly;
    foreign files map ``type`` -> the matching node kind or ``Concept`` and
    body links -> ``references`` edges. Files missing a non-empty ``type`` and
    the reserved ``index.md``/``log.md`` are skipped.
    """
    root = Path(in_dir)
    root_resolved = root.resolve()
    parsed = []
    concept_to_node: Dict[str, str] = {}
    for f in sorted(root.rglob("*.md")):
        concept = str(f.relative_to(root).with_suffix("")).replace(os.sep, "/")
        if concept in _RESERVED:
            continue
        # Never follow a symlink (or any path) that escapes the bundle root —
        # a crafted bundle must not coax import into reading arbitrary files.
        try:
            if f.is_symlink() or root_resolved not in f.resolve().parents:
                continue
        except OSError:
            continue
        try:
            fm, body = _split_frontmatter(f.read_text(encoding="utf-8"))
        except OSError:
            continue
        if not str(fm.get("type") or "").strip():
            continue  # OKF: type is required — tolerate by skipping
        x = fm.get("x_tesserae") if isinstance(fm.get("x_tesserae"), dict) else None
        node_id = str((x or {}).get("id") or concept)
        concept_to_node[concept] = node_id
        parsed.append((concept, fm, body, x, node_id))

    nodes: List[ResearchNode] = []
    edges: List[ResearchEdge] = []
    seen: set[str] = set()
    for concept, fm, body, x, node_id in parsed:
        if node_id in seen:
            continue
        seen.add(node_id)
        ntype, foreign = _coerce_node_type(str(fm["type"]))
        name = str(fm.get("name") or _first_h1(body) or node_id)
        description = _strip_relations(body)
        if x is not None:
            metadata = dict(x.get("metadata") or {})
            nodes.append(ResearchNode(
                id=node_id, name=name, type=ntype,
                aliases=list(x.get("aliases") or []),
                description=description,
                source_path=x.get("source_path"),
                metadata=metadata,
            ))
            for e in x.get("edges") or []:
                t, tgt = e.get("type"), e.get("target")
                if t in ALLOWED_EDGE_TYPES and tgt in concept_to_node:
                    edges.append(ResearchEdge(
                        source=node_id, target=concept_to_node[tgt], type=t,
                        evidence=e.get("evidence"),
                        metadata=dict(e.get("metadata") or {}),
                    ))
        else:
            meta = {"okf_type": foreign} if foreign else {}
            nodes.append(ResearchNode(id=node_id, name=name, type=ntype, description=description, metadata=meta))
            for tgt_concept in _body_link_concepts(body, concept, root):
                if tgt_concept in concept_to_node and concept_to_node[tgt_concept] != node_id:
                    edges.append(ResearchEdge(
                        source=node_id, target=concept_to_node[tgt_concept], type=_FOREIGN_EDGE_TYPE,
                    ))

    # Dedup edges on identity (source, type, target).
    seen_e: set = set()
    uniq: List[ResearchEdge] = []
    for e in edges:
        k = (e.source, e.type, e.target)
        if k not in seen_e:
            seen_e.add(k)
            uniq.append(e)
    return ResearchGraph(nodes=nodes, edges=uniq)
