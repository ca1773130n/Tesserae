"""OKF (Open Knowledge Format) v0.2 import/export.

OKF (Google Cloud, Apache-2.0 draft): a directory tree of Markdown files, each
with YAML frontmatter whose only REQUIRED field is a non-empty ``type``.
Relationships are relative Markdown links between *Concept IDs* (a file's path
minus ``.md``). Reserved files at ANY level: ``index.md`` (§8) and ``log.md``
(§9). Consumers must tolerate unknown types/keys and broken links (§11).

Tesserae WRITES v0.2 and READS both v0.1 and v0.2.

v0.2 adds optional provenance/trust/lifecycle families. Tesserae emits only the
ones it can derive honestly from the compiled graph:

* ``title``/``description``/``resource`` (§4.1)
* ``generated: {by, at}`` (§5.2) — ``by`` is always ``process:``/``<agent>/…``,
  NEVER ``human:``; ``at`` is the existing source-derived timestamp ladder.
* ``sources`` + ``usage_window`` (§5.1) from ``source_path`` and
  ``discussed_in`` edges.
* ``status: deprecated`` + ``stale_after`` (§5.4/§5.5) from ``supersedes`` edges.

Deliberately NOT emitted: ``verified`` (§5.2) and any trust tier above
"unverified" (§5.3), and the whole Attested Computation family (§10). Nothing in
the graph is a recorded verification event with an actor and a timestamp — edge
provenance classes describe how strongly the graph licenses a triple, which is a
different axis from a per-concept confirmation. Emitting either would launder
trust or advertise an attestation contract Tesserae cannot honour. The CONSUMER
side does carry both: a foreign bundle's ``verified``/attestation contract
round-trips verbatim.

Tesserae round-trips its OWN bundles losslessly via an ``x_tesserae`` frontmatter
namespace (real node id + typed edges); foreign OKF bundles import best-effort
(unknown ``type`` -> ``Concept`` with the original kept in ``metadata.okf_type``;
untyped body links -> ``references`` edges; every unrecognised frontmatter key
kept in ``metadata.okf`` per §4.1's round-trip SHOULD).

ponytail: this module is the whole OKF surface — a focused reader/writer beats
bending the Obsidian projector, which is wikilink/dataview-specific.
"""

from __future__ import annotations

import hashlib
import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import yaml

from .markdown_projection import directory_for_node, slugify, unique_slugs
from .research_graph import (
    ALLOWED_EDGE_TYPES,
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
)
from .temporal import _boundary_precedes_start, _source_ts

OKF_VERSION = "0.2"

_RELATIONS_MARKER = "<!-- okf:relations -->"
_FOREIGN_EDGE_TYPE = "references"  # untyped OKF relative link -> this edge type
_FOREIGN_NODE_TYPE = ResearchNodeType.CONCEPT  # unknown OKF type -> this
# §3.1: index.md/log.md are reserved *at any level of the hierarchy*, not just
# at the bundle root — a subdirectory index.md is a listing, never a concept.
_RESERVED_FILENAMES = {"index.md", "log.md"}
# Frontmatter keys Tesserae itself owns on read; everything else on a FOREIGN
# document is unknown-to-us and preserved verbatim (§4.1).
_OWNED_FM_KEYS = {"type", "title", "name", "x_tesserae"}
_METADATA_OKF_KEY = "okf"  # node.metadata namespace holding the preserved bucket

_DESCRIPTION_CAP = 200  # chars; §4.1 wants "a single sentence"
_LEADING_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s")


def okf_trust_tier(verified: object) -> str:
    """Trust tier for a concept's ``verified`` field (§5.3).

    ``"unverified"`` when absent, ``"human-reviewed"`` when any actor carries
    the ``human:`` prefix (§7), else ``"machine-confirmed"``. A bare mapping is
    treated as a one-element list, which §11 makes a consumer MUST.

    Inferred, never stored — §5.1 is explicit that OKF records signals, not
    verdicts.
    """
    if isinstance(verified, dict):
        verified = [verified]
    if not isinstance(verified, (list, tuple)) or not verified:
        return "unverified"
    for entry in verified:
        by = entry.get("by") if isinstance(entry, dict) else None
        if isinstance(by, str) and by.startswith("human:"):
            return "human-reviewed"
    return "machine-confirmed"


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


def _date_of(ts: object) -> Optional[str]:
    """Leading ``YYYY-MM-DD`` of a timestamp string, else ``None``."""
    if not isinstance(ts, str):
        return None
    m = _LEADING_DATE.match(ts.strip())
    return m.group(1) if m else None


def _first_sentence(text: str) -> str:
    """First sentence of ``text``, capped — feeds ``description`` (§4.1, §8)."""
    para = (text or "").strip().split("\n\n", 1)[0].replace("\n", " ").strip()
    if not para:
        return ""
    sentence = _SENTENCE_END.split(para, 1)[0].strip()
    if len(sentence) > _DESCRIPTION_CAP:
        sentence = sentence[: _DESCRIPTION_CAP - 1].rstrip() + "…"
    return sentence


def _project_roots(graph: ResearchGraph) -> List[str]:
    """Project roots declared by the graph's own Session nodes, sorted.

    Derived from the graph rather than passed in, so the bundle stays a PURE
    projection: no argument, no ``os.getcwd()``, no wall clock.
    """
    roots = {
        str((n.metadata or {}).get("project_root") or "").rstrip("/")
        for n in graph.nodes
        if n.type == ResearchNodeType.SESSION
    }
    return sorted(r for r in roots if r)


def _relative_resource(source_path: object, roots: Sequence[str]) -> Optional[str]:
    """``source_path`` made project-root-relative, else ``None``.

    An absolute ``/Users/...`` path emitted raw would be read by a conformant
    consumer as a *bundle*-relative path (§6.2) AND leak a home directory, so a
    path we cannot relativise is omitted rather than guessed at.
    """
    if not isinstance(source_path, str) or not source_path.strip():
        return None
    path = source_path.strip()
    if not os.path.isabs(path):
        return path.replace(os.sep, "/")
    for root in roots:
        if path == root:
            continue
        if path.startswith(root + "/"):
            return path[len(root) + 1:].replace(os.sep, "/")
    return None


def _generated_by(node: ResearchNode) -> str:
    """Actor (§7) that produced this concept's content. NEVER ``human:``.

    Tesserae writes concepts by extraction and compilation; no rung of this
    ladder is a person, so none may claim the ``human:`` prefix that §5.3 keys
    the top trust tier on.

    ``process:tesserae-<extractor>`` rather than §7's ``<producer>/<version>``
    for the extractor rung on purpose: a version-bearing actor would rewrite
    every concept file on every release for no semantic change.
    """
    meta = node.metadata or {}
    agent_key = meta.get("agent_key")
    if isinstance(agent_key, str) and agent_key.strip():
        return f"{agent_key.strip()}/tesserae-agent-write"
    extractor = meta.get("extractor")
    if isinstance(extractor, str) and extractor.strip():
        return f"process:tesserae-{extractor.strip()}"
    return "process:tesserae-compile"


def _resource_uri(node: ResearchNode) -> Optional[str]:
    """Canonical URI of the asset the concept describes (§4.1)."""
    meta = node.metadata or {}
    arxiv = meta.get("arxiv_id")
    if isinstance(arxiv, str) and arxiv.strip():
        return f"https://arxiv.org/abs/{arxiv.strip()}"
    for key in ("repo_url", "github_repo"):
        val = meta.get(key)
        if isinstance(val, str) and val.strip().startswith(("http://", "https://")):
            return val.strip()
    return None


def _sources_block(
    node: ResearchNode,
    roots: Sequence[str],
    usage: Dict[str, List[str]],
    node_by_id: Dict[str, ResearchNode],
    author_of: Dict[str, str],
) -> Tuple[Optional[list], Optional[dict]]:
    """``(sources, usage_window)`` for ``node`` (§5.1), or ``(None, None)``."""
    rel = _relative_resource(node.source_path, roots)
    if rel is None:
        return None, None
    entry: dict = {
        "id": slugify(os.path.splitext(os.path.basename(rel))[0]) or "source",
        "resource": rel,
        "title": os.path.basename(rel),
    }
    author_id = author_of.get(node.id)
    if author_id:
        person = node_by_id.get(author_id)
        entry["author"] = f"human:{slugify(person.name if person else author_id)}"
    meta = node.metadata or {}
    for key in ("frontmatter_date", "analysis_date"):
        day = _date_of(meta.get(key))
        if day:
            # §5.1 recency signal from the DOCUMENT's own date. Never
            # os.stat().st_mtime: environment state in a projection is exactly
            # the byte-idempotence leak this repo has regressed on repeatedly.
            entry["last_modified"] = day
            break

    window: Optional[dict] = None
    session_ids = usage.get(node.id) or []
    if session_ids:
        # WHAT IS COUNTED: distinct agent/work sessions whose transcript touched
        # this document. NOT human page reads. §5.1 already warns the signal is
        # coarse and cross-kind-incomparable; naming it here so nobody reads it
        # as popularity.
        entry["usage_count"] = len(session_ids)
        days = []
        for sid in session_ids:
            smeta = (node_by_id.get(sid).metadata or {}) if sid in node_by_id else {}
            days.extend(d for d in (_date_of(smeta.get("started_at")), _date_of(smeta.get("ended_at"))) if d)
        if days:
            window = {"from": min(days), "to": max(days)}
    return [entry], window


def write_okf_bundle(graph: ResearchGraph, out_dir: str | Path) -> List[Path]:
    """Write ``graph`` as an OKF v0.2 bundle under ``out_dir``. Deterministic."""
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

    roots = _project_roots(graph)
    usage: Dict[str, List[str]] = defaultdict(list)
    superseded_by: Dict[str, List[str]] = defaultdict(list)
    authors: Dict[str, List[str]] = defaultdict(list)
    for e in graph.edges:  # sorted downstream; graph.edges order never decides output
        if e.type == "discussed_in":
            usage[e.source].append(e.target)
        elif e.type == "supersedes":
            superseded_by[e.target].append(e.source)
        elif e.type == "authored_by":
            authors[e.source].append(e.target)
    usage = {k: sorted(set(v)) for k, v in usage.items()}
    # §5.1 authority signal only when unambiguous — a paper with 30 authors has
    # no single one, and picking any of them would be a silent misattribution.
    author_of = {k: v[0] for k, v in authors.items() if len(set(v)) == 1}

    descriptions: Dict[str, str] = {}
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

        meta = node.metadata or {}
        # A foreign type imported as Concept re-exports under its ORIGINAL type
        # (§4.1 forbids a consumer rejecting unknown types, so degrading one to
        # "Concept" on the way out would be a gratuitous downgrade).
        okf_type = meta.get("okf_type")
        fm: dict = {
            "type": str(okf_type) if isinstance(okf_type, str) and okf_type.strip() else node.type.value,
            "title": node.name,
        }
        description = _first_sentence(node.description or "")
        descriptions[node.id] = description
        if description:
            fm["description"] = description
        resource = _resource_uri(node)
        if resource:
            fm["resource"] = resource
        generated: dict = {"by": _generated_by(node)}
        at = _source_ts(node)
        if at:
            generated["at"] = at
        fm["generated"] = generated
        sources, window = _sources_block(node, roots, usage, node_by_id, author_of)
        if sources:
            fm["sources"] = sources
        if window:
            fm["usage_window"] = window
        supersede_ids = sorted(set(superseded_by.get(node.id, ())))
        if supersede_ids:
            fm["status"] = "deprecated"  # §5.4: kept for links and history
            own_ts = _source_ts(node)
            ends = [t for t in (_source_ts(node_by_id.get(i)) for i in supersede_ids) if t]
            end = min(ends) if ends else None
            # Under-claim rather than emit a boundary that precedes the node's
            # own timestamp — the same degenerate-interval guard temporal.py
            # applies to derived validity ends.
            if end and not _boundary_precedes_start(own_ts, end):
                day = _date_of(end)
                if day:
                    fm["stale_after"] = day  # §5.5: absolute date, not a TTL
        fm["x_tesserae"] = x_tess
        # §4.1 round-trip SHOULD: a foreign producer's own frontmatter wins over
        # anything we derived, so importing and re-exporting someone else's
        # bundle never overwrites their trust/provenance claims with ours.
        preserved = meta.get(_METADATA_OKF_KEY)
        if isinstance(preserved, dict):
            fm.update({k: v for k, v in preserved.items() if k not in _OWNED_FM_KEYS})

        lines = [(node.description or "").strip(), "", _RELATIONS_MARKER, "## Relations", ""]
        for e in edges:
            tgt = node_by_id.get(e.target)
            tname = tgt.name if tgt else e.target
            lines.append(f"- {e.type}: [{tname}]({_rel_link(concept, cid[e.target])})")
        body = "\n".join(lines).rstrip() + "\n"
        path.write_text(_frontmatter(fm) + body, encoding="utf-8")
        written.append(path)

    written.append(_write_index(root, graph, cid, descriptions))
    written.append(_write_log(root, graph, cid))
    return written


def _write_index(
    root: Path, graph: ResearchGraph, cid: Dict[str, str], descriptions: Dict[str, str]
) -> Path:
    """Bundle-root ``index.md`` (§8).

    Frontmatter is exactly ``okf_version`` — §8 permits *no other* key here and
    §12 makes the bundle root the only place it may appear at all.
    """
    node_by_id = {n.id: n for n in graph.nodes}
    lines = [
        "---",
        f"okf_version: '{OKF_VERSION}'",
        "---",
        "",
        "# Knowledge Base",
        "",
    ]
    for nid in sorted(cid, key=lambda i: cid[i]):
        n = node_by_id[nid]
        desc = descriptions.get(nid) or n.type.value
        lines.append(f"* [{n.name}]({cid[nid]}.md) - {desc}")
    path = root / "index.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _write_log(root: Path, graph: ResearchGraph, cid: Dict[str, str]) -> Path:
    """Bundle-root ``log.md`` (§9): ``## YYYY-MM-DD`` groups, newest first.

    ponytail: derive the log from in-graph Session timeline data only — never
    wall-clock now() — so the bundle stays reproducible. Sessions with no
    parseable start date are dropped rather than filed under a made-up day,
    because §9 makes the ISO date heading a MUST.
    """
    by_day: Dict[str, List[Tuple[str, str]]] = defaultdict(list)
    for n in graph.nodes:
        if n.type != ResearchNodeType.SESSION or n.id not in cid:
            continue
        day = _date_of((n.metadata or {}).get("started_at"))
        if day:
            by_day[day].append((n.name, cid[n.id]))
    lines = ["# Changelog", ""]  # §8/§9: log.md carries NO frontmatter
    for day in sorted(by_day, reverse=True):
        lines.append(f"## {day}")
        for name, concept in sorted(by_day[day], key=lambda item: (item[1], item[0])):
            lines.append(f"* **Session**: [{name}]({concept}.md)")
        lines.append("")
    path = root / "log.md"
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
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


_CITATIONS_HEADING = re.compile(r"^#{1,6}\s+Citations\s*$", re.IGNORECASE)


def _split_legacy_citations(body: str) -> Tuple[str, List[dict]]:
    """Peel a legacy v0.1 ``# Citations`` list off ``body`` (§13.1).

    Returns ``(body_without_the_section, sources_entries)``. v0.2 moves
    provenance to frontmatter, so leaving the list in the description would
    swallow it as prose and lose it as provenance.
    """
    lines = body.splitlines()
    start = next((i for i, ln in enumerate(lines) if _CITATIONS_HEADING.match(ln.strip())), None)
    if start is None:
        return body, []
    end = len(lines)
    entries: List[dict] = []
    for i in range(start + 1, len(lines)):
        stripped = lines[i].strip()
        if stripped.startswith("#"):
            end = i
            break
        if stripped.startswith(("- ", "* ")):
            item = stripped[2:].strip()
            if item:
                entries.append({"resource": item})
    if not entries:
        return body, []
    return "\n".join(lines[:start] + lines[end:]), entries


def _preserved_frontmatter(fm: dict, body: str) -> dict:
    """Foreign frontmatter keys Tesserae does not own, kept verbatim (§4.1).

    Normalises the two v0.1/v0.2 shape rules a consumer MUST honour: a bare
    ``verified`` mapping becomes a one-element list (§5.2, §11), and a legacy
    ``# Citations`` body list becomes ``sources`` when no ``sources`` key is
    present (§13.1).
    """
    kept = {k: v for k, v in fm.items() if k not in _OWNED_FM_KEYS}
    if isinstance(kept.get("verified"), dict):
        kept["verified"] = [kept["verified"]]
    if "sources" not in kept:
        _, citations = _split_legacy_citations(body)
        if citations:
            kept["sources"] = citations
    return kept


def _updated_at(fm: dict) -> Optional[str]:
    """``generated.at``, else a legacy v0.1 ``timestamp`` (§13.1).

    Normalised into ``metadata["updated_at"]`` — a rung the shared timestamp
    ladder already reads — so an imported concept gets real temporal ordering
    instead of sorting as undated.
    """
    generated = fm.get("generated")
    if isinstance(generated, dict):
        at = generated.get("at")
        if isinstance(at, str) and at.strip():
            return at.strip()
    legacy = fm.get("timestamp")
    if isinstance(legacy, str) and legacy.strip():
        return legacy.strip()
    return None


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
    """Parse an OKF v0.1 **or** v0.2 bundle into a ResearchGraph.

    Tolerant per §11: never rejects a document for an unknown ``type``, unknown
    frontmatter keys, missing optional families, or broken cross-links.

    Tesserae-authored files (``x_tesserae`` present) round-trip losslessly;
    foreign files map ``type`` -> the matching node kind or ``Concept``, body
    links -> ``references`` edges, and every unrecognised frontmatter key into
    ``metadata["okf"]`` (§4.1). Files missing a non-empty ``type`` and the
    reserved ``index.md``/``log.md`` are skipped.
    """
    root = Path(in_dir)
    root_resolved = root.resolve()
    parsed = []
    concept_to_node: Dict[str, str] = {}
    for f in sorted(root.rglob("*.md")):
        concept = str(f.relative_to(root).with_suffix("")).replace(os.sep, "/")
        if f.name in _RESERVED_FILENAMES:
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
        # §4.1 names the key `title`; `name` is a Tesserae v0.1 invention kept
        # BEHIND it so our own older bundles still win over the body's first h1.
        name = str(fm.get("title") or fm.get("name") or _first_h1(body) or node_id)
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
            # FOREIGN document. x_tesserae is our lossless channel; without it,
            # every key we do not own has to be preserved by hand (§4.1).
            description, _ = _split_legacy_citations(description)
            description = description.strip()
            meta: Dict[str, object] = {"okf_type": foreign} if foreign else {}
            preserved = _preserved_frontmatter(fm, body)
            if preserved:
                meta[_METADATA_OKF_KEY] = preserved
            updated_at = _updated_at(fm)
            if updated_at:
                meta["updated_at"] = updated_at
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
