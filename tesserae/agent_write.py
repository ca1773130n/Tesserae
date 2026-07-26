"""Append-only typed overlay for agent-authored graph writes.

**The problem this solves.** Today an agent that learns something can only get
it into the graph by writing markdown and paying for a full LLM extraction pass
— and anything it writes straight into ``graph.json`` is erased by the next
compile. ``graph_write`` gives agents a typed, validated, zero-LLM write path
whose results survive recompilation.

**Where the writes live, and why they survive.** This codebase already has an
answer for "nodes no markdown file owns": the *producer* contract.
``code_graph`` / ``raganything`` / ``session_graph`` / ``vault_overlay`` all
re-derive their nodes from a stable input every compile and record
``__<name>__`` provenance rows, which makes the incremental differ treat them
as producer-owned and refuse to tombstone them
(``ProjectWiki.compile``: ``src.startswith("__")`` → excluded from
``stale_ids``). Agent writes become a 5th producer, ``__agent_write__``, whose
stable input is ``.tesserae/agent-writes.jsonl``.

**Strictness.** ``graph_from_llm_payload`` deliberately *drops* bad edges — the
right call for a 137-doc LLM compile, the wrong call for a typed API where a
dropped edge is a silent lie about what was recorded. So this module refuses
instead of coercing, and refuses loudly:

* node ``type`` outside ``ALLOWED_NODE_TYPES``
* node ``type`` inside the producer-owned deny set (an agent minting a
  ``Session`` node collides with ids ``__session_graph__`` already owns, giving
  one node two ``__`` provenance sources)
* edge ``type`` outside ``ALLOWED_EDGE_TYPES``
* an edge endpoint that resolves to no node in the payload
* an edge with empty ``evidence``
* ``provenance`` missing ``agent``, or missing **every** external anchor
  (``url`` / ``file`` / ``commit`` / ``session_id``)

The last rule is the "some evidence must originate outside the graph"
constraint made mechanical: a verifier reading these nodes later can always
walk back to something the graph did not author.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .llm_extractor import GraphJSONValidationError
from .llm_extractor import validate_research_graph
from .locking import compile_lock
from .research_graph import (
    AGENT_LAYER_TYPES,
    ALLOWED_EDGE_TYPES,
    ALLOWED_NODE_TYPES,
    CODE_SYMBOL_TYPES,
    DISTILLED_MEMORY_TYPES,
    SESSION_FINDING_TYPES,
    ResearchEdge,
    ResearchGraph,
    ResearchGraphBuilder,
    ResearchNode,
    ResearchNodeType,
    normalize_display_name,
    stable_id,
)
# Private on purpose upstream, but it IS the compile's entity-resolution key —
# aligning agent writes on anything else would fork nodes the dedup pass then
# collapses with an arbitrary winner. Same package, one import, no fork.
from .research_graph import _aggressive_dedup_key
from .batch import sha256_text

__all__ = [
    "AGENT_WRITE_SOURCE",
    "align_overlay",
    "resolve_existing_id",
    "DENIED_NODE_TYPES",
    "EXTERNAL_ANCHORS",
    "NEVER_ALIGNED_TYPES",
    "agent_anchor_id",
    "record_agent_write",
    "replay_agent_writes",
    "resolve_write_nodes",
    "validate_write",
]


# Provenance label; the ``__`` prefix is what makes the incremental differ
# treat these nodes as producer-owned (see module docstring).
AGENT_WRITE_SOURCE = "__agent_write__"

# At least one of these must be present in ``provenance`` — the mechanical form
# of "evidence originates outside the graph".
EXTERNAL_ANCHORS: Tuple[str, ...] = ("url", "file", "commit", "session_id")

# Node types an agent may NOT mint, because another producer already owns their
# id space. Derived from the existing named sets rather than a literal list, so
# a future CODE_* / agent-layer type is denied automatically the day it is
# added to the vocabulary.
DENIED_NODE_TYPES: frozenset = frozenset(
    {t.value for t in CODE_SYMBOL_TYPES}
    | {t.value for t in AGENT_LAYER_TYPES}
    | {
        ResearchNodeType.SESSION.value,
        ResearchNodeType.SYNTHESIS.value,
        ResearchNodeType.COMMUNITY_SUMMARY.value,
        ResearchNodeType.STUB.value,
        ResearchNodeType.EVENT.value,
    }
)

# Node types ``_merge_same_type_aliased_duplicates`` deliberately EXEMPTS from
# aggressive same-name dedup (``research_graph.py``: session findings, Event,
# distilled memory, code symbols, agent-layer). Two of these with identical text
# are legitimately separate provenance — merging them loses the link back to the
# session / cluster / module that produced each one. ``resolve_existing_id``
# uses the same key that pass uses, so it must honour the same refusals;
# otherwise an agent's independent observation is fused onto (and erased by)
# some session's finding that happens to share wording. ``DENIED_NODE_TYPES``
# already blocks the code/agent/Event families at the door, but SessionInsight,
# Gotcha, Runbook & co. are writable on purpose.
NEVER_ALIGNED_TYPES: frozenset = frozenset(
    SESSION_FINDING_TYPES
    | DISTILLED_MEMORY_TYPES
    | CODE_SYMBOL_TYPES
    | AGENT_LAYER_TYPES
    | {ResearchNodeType.EVENT}
)


def agent_anchor_id(agent_key: str) -> str:
    """Stable id of the ``Agent`` node an agent's writes attach to.

    Byte-identical to what ``session_graph_structural._agent_pseudo`` mints, so
    a write anchors onto the agent's EXISTING identity node after the session
    graph merges rather than forking a phantom one.
    """
    return stable_id(ResearchNodeType.AGENT.value, f"agent:{agent_key}")


def _canonical_json(payload: Any) -> str:
    return json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )


@dataclass(frozen=True)
class ValidatedWrite:
    agent: str
    nodes: List[Dict[str, Any]]
    edges: List[Dict[str, Any]]
    provenance: Dict[str, Any]

    @property
    def write_id(self) -> str:
        """Content hash — no wall clock, so the same finding written twice is
        the same id and the second write is a ``duplicate``, not a new record.
        """
        return sha256_text(
            _canonical_json(
                {
                    "agent": self.agent,
                    "nodes": self.nodes,
                    "edges": self.edges,
                    "provenance": self.provenance,
                }
            )
        )[:16]

    def as_record(self, written_at: str = "") -> Dict[str, Any]:
        return {
            "write_id": self.write_id,
            "agent": self.agent,
            "nodes": self.nodes,
            "edges": self.edges,
            "provenance": self.provenance,
            # Stored for the timeline; deliberately NOT part of ``write_id``.
            "written_at": written_at,
        }


def validate_write(payload: Mapping[str, Any], agent_key: str) -> ValidatedWrite:
    """Strict pre-pass. Refuses; never coerces, never silently drops."""
    agent_key = str(agent_key or "").strip()
    if not agent_key:
        raise GraphJSONValidationError("graph_write requires a non-empty 'agent'")

    raw_nodes = payload.get("nodes")
    if not isinstance(raw_nodes, list) or not raw_nodes:
        raise GraphJSONValidationError("graph_write requires a non-empty nodes list")
    raw_edges = payload.get("edges", []) or []
    if not isinstance(raw_edges, list):
        raise GraphJSONValidationError("graph_write edges must be a list")

    provenance = payload.get("provenance")
    if not isinstance(provenance, Mapping):
        raise GraphJSONValidationError("graph_write requires a provenance object")
    if not str(provenance.get("agent") or "").strip():
        raise GraphJSONValidationError("graph_write provenance requires 'agent'")
    if not any(str(provenance.get(k) or "").strip() for k in EXTERNAL_ANCHORS):
        raise GraphJSONValidationError(
            "graph_write provenance requires at least one external anchor "
            f"({', '.join(EXTERNAL_ANCHORS)}) — a claim whose only support is "
            "the graph itself cannot be verified against anything outside it"
        )

    nodes: List[Dict[str, Any]] = []
    names: Dict[str, str] = {}
    for raw in raw_nodes:
        if not isinstance(raw, Mapping):
            raise GraphJSONValidationError("graph_write: every node must be an object")
        name = str(raw.get("name") or "").strip()
        type_name = str(raw.get("type") or "").strip()
        if not name:
            raise GraphJSONValidationError("graph_write: every node needs a name")
        if type_name not in ALLOWED_NODE_TYPES:
            raise GraphJSONValidationError(
                f"graph_write: unsupported node type: {type_name!r}"
            )
        if type_name in DENIED_NODE_TYPES:
            raise GraphJSONValidationError(
                f"graph_write: node type {type_name!r} is owned by a compile "
                "producer and cannot be written by an agent (its ids are "
                "re-derived every compile; a second owner would give one node "
                "two provenance sources)"
            )
        aliases = raw.get("aliases") or []
        if not isinstance(aliases, list) or not all(
            isinstance(a, str) for a in aliases
        ):
            raise GraphJSONValidationError(
                f"graph_write: node aliases must be a list of strings: {name}"
            )
        metadata = raw.get("metadata") or {}
        if not isinstance(metadata, dict):
            raise GraphJSONValidationError(
                f"graph_write: node metadata must be an object: {name}"
            )
        nodes.append(
            {
                "name": name,
                "type": type_name,
                "description": str(raw.get("description") or ""),
                "aliases": sorted(str(a) for a in aliases),
                "metadata": dict(metadata),
            }
        )
        names[name] = type_name
        key = str(raw.get("key") or "").strip()
        if key:
            names[key] = type_name

    edges: List[Dict[str, Any]] = []
    for raw in raw_edges:
        if not isinstance(raw, Mapping):
            raise GraphJSONValidationError("graph_write: every edge must be an object")
        edge_type = str(raw.get("type") or "").strip()
        if edge_type not in ALLOWED_EDGE_TYPES:
            raise GraphJSONValidationError(
                f"graph_write: unsupported edge type: {edge_type!r}. Unlike the "
                "LLM extraction path, a typed write never drops an edge silently"
            )
        source = str(raw.get("source") or "").strip()
        target = str(raw.get("target") or "").strip()
        if source not in names or target not in names:
            missing = source if source not in names else target
            raise GraphJSONValidationError(
                f"graph_write: edge endpoint {missing!r} is not one of the nodes "
                "in this payload"
            )
        evidence = str(raw.get("evidence") or "").strip()
        if not evidence:
            raise GraphJSONValidationError(
                f"graph_write: edge {source!r} -{edge_type}-> {target!r} needs "
                "non-empty evidence"
            )
        edges.append(
            {
                "source": source,
                "target": target,
                "type": edge_type,
                "evidence": evidence,
            }
        )

    return ValidatedWrite(
        agent=agent_key,
        nodes=nodes,
        edges=edges,
        provenance={str(k): provenance[k] for k in sorted(provenance)},
    )


def _graph_from_record(record: Mapping[str, Any]) -> ResearchGraph:
    """Build one record's slice. Same builder + validator the LLM path uses.

    Deliberately NOT routed through ``graph_from_llm_payload``: that helper
    injects an "Untitled Source" node when the payload has no node of the
    ``source_kind``'s type, and ``source_kind_to_node_type("Agent", None)``
    resolves to ``SourceDocument``, not ``Agent`` — so it would fabricate a
    phantom document per write. We mint the ``Agent`` anchor ourselves with the
    id_seed the session graph already uses (``ResearchGraphBuilder`` supports
    ``id_seed``; ``graph_from_llm_payload`` does not expose it).
    """
    builder = ResearchGraphBuilder()
    agent_key = str(record.get("agent") or "")
    write_id = str(record.get("write_id") or "")
    provenance = dict(record.get("provenance") or {})
    anchor = builder.add_node(
        name=f"agent:{agent_key}",
        node_type=ResearchNodeType.AGENT,
        id_seed=f"agent:{agent_key}",
        metadata={"agent_key": agent_key},
    )
    by_ref: Dict[str, Any] = {}
    for raw in record.get("nodes") or []:
        node = builder.add_node(
            name=str(raw["name"]),
            node_type=ResearchNodeType(str(raw["type"])),
            aliases=list(raw.get("aliases") or []),
            description=str(raw.get("description") or ""),
            metadata={
                **dict(raw.get("metadata") or {}),
                # Provenance travels ON the node so a later read can see who
                # asserted it and against what outside anchor, without needing
                # the JSONL.
                "agent_write_id": write_id,
                "agent_key": agent_key,
                "agent_write_provenance": provenance,
            },
        )
        by_ref[str(raw["name"])] = node
        builder.add_edge(
            node,
            "performed_by",
            anchor,
            # Content must be a pure function of the FINDING, never of the write
            # event. Two writes whose nodes later collapse (cross-type dedup
            # fuses ``Claim:x`` into ``Paper:x``) produce two ``performed_by``
            # edges that collide on ``(source, type, target)``; if their
            # evidence differed, the survivor would be decided by merge-input
            # order — which differs between a full and an incremental compile,
            # so graph.json would oscillate forever. The write id is already on
            # the node as ``agent_write_id``; it does not belong here.
            evidence=f"written by agent {agent_key}",
        )
    for raw in record.get("edges") or []:
        source = by_ref.get(str(raw["source"]))
        target = by_ref.get(str(raw["target"]))
        if source is None or target is None:
            # Unreachable via record_agent_write (validate_write refuses these).
            # Raising is right HERE — it is what makes the write path refuse —
            # and ``replay_agent_writes`` catches it so a hand-edited JSONL
            # cannot take a compile down.
            raise GraphJSONValidationError(
                f"agent write {write_id}: edge endpoint missing from record"
            )
        builder.add_edge(
            source,
            str(raw["type"]),
            target,
            evidence=str(raw.get("evidence") or ""),
        )
    graph = builder.build()
    validate_research_graph(graph)
    return graph


def _read_records(file_path: Path) -> Dict[str, Dict[str, Any]]:
    """Parse the JSONL overlay, skipping unreadable lines with a warning.

    Skip-and-warn, exactly like ``BatchIngestRunner._load_manifest`` does for a
    corrupt manifest. One truncated / hand-edited line must never be able to
    brick EVERY future compile of the whole corpus: the read path is not the
    place to enforce write-path validity, and the write path already refuses
    malformed payloads at the door.
    """
    records: Dict[str, Dict[str, Any]] = {}
    if not file_path.exists():
        return records
    for lineno, line in enumerate(
        file_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, ValueError) as exc:
            print(
                f"warning: unreadable agent-write line {file_path}:{lineno}: "
                f"{exc}; skipping",
                file=sys.stderr,
            )
            continue
        if not isinstance(record, Mapping):
            print(
                f"warning: agent-write line {file_path}:{lineno} is not an "
                "object; skipping",
                file=sys.stderr,
            )
            continue
        records[str(record.get("write_id") or "")] = dict(record)
    return records


def replay_agent_writes(path: str | Path) -> ResearchGraph:
    """Replay the JSONL overlay into a graph. Pure function of the file bytes.

    Records are replayed **sorted by write_id**, not in append order, so two
    agents appending in either interleaving converge on the same merge order —
    and therefore the same ``prefer_research_node`` winners and the same
    ``graph.json`` bytes.
    """
    file_path = Path(path)
    records = _read_records(file_path)
    nodes: Dict[str, Any] = {}
    edges: Dict[Tuple[str, str, str], Any] = {}
    for write_id in sorted(records):
        # Same reason: a hand-written record carrying an out-of-vocabulary type,
        # a dangling endpoint or any other builder-rejected shape is dropped
        # with a warning rather than raised. ``record_agent_write`` cannot
        # produce one; only an editor or a partial write can.
        try:
            slice_graph = _graph_from_record(records[write_id])
        except (GraphJSONValidationError, ValueError, KeyError, TypeError) as exc:
            print(
                f"warning: unusable agent write {write_id or '<no id>'} in "
                f"{file_path}: {exc}; skipping",
                file=sys.stderr,
            )
            continue
        for node in slice_graph.nodes:
            nodes.setdefault(node.id, node)
        for edge in slice_graph.edges:
            edges.setdefault((edge.source, edge.type, edge.target), edge)
    return ResearchGraph(nodes=list(nodes.values()), edges=list(edges.values()))


def resolve_existing_id(
    graph: Optional[ResearchGraph], node_type: str, name: str
) -> Optional[str]:
    """Id of the node in ``graph`` an agent write should attach to, if any.

    Matches on ``(type, aggressive dedup key)`` — the SAME key the compile's
    same-type dedup pass uses, *including its exemptions* (see
    ``NEVER_ALIGNED_TYPES``) — so a write lands on the curated node instead of
    forking beside it, and never fuses onto a node the compile itself refuses to
    merge. Ambiguity (two existing nodes sharing the key) refuses, for the same
    reason ``verify_claim`` refuses: guessing an entity is how a 5-hop chain
    drops to 44% trustworthy.
    """
    if graph is None:
        return None
    try:
        node_enum = ResearchNodeType(node_type)
    except ValueError:
        return None
    if node_enum in NEVER_ALIGNED_TYPES:
        return None
    key = _aggressive_dedup_key(normalize_display_name(name))
    if not key:
        return None
    hits = sorted(
        {
            n.id
            for n in graph.nodes
            if n.type == node_enum and _aggressive_dedup_key(n.name or "") == key
        }
    )
    return hits[0] if len(hits) == 1 else None


def align_overlay(overlay: ResearchGraph, graph: ResearchGraph) -> ResearchGraph:
    """Redirect overlay nodes onto their existing counterparts in ``graph``.

    Without this the overlay can FORK a curated node: an agent that writes
    ``"image generators are generalist vision learners"`` mints
    ``Paper:image-generators-...`` while extraction seeded the id from the arXiv
    id (``Paper:arxiv-2604-20329``). Both survive ``merge_graphs``'s id dedup,
    and the same-type collapse then picks the winner with
    ``max((len(name), id))`` — a pure lexicographic coin-flip that the agent's
    node can WIN, renaming a curated Paper. Aligning first makes "extraction
    wins payload conflicts" true by construction instead of by luck.

    Deterministic: ``resolve_existing_id`` sorts and refuses on ambiguity, and
    the redirect map is applied in overlay node order (itself write_id-sorted).

    A redirected node is NOT dropped. It is re-emitted under the curated id
    carrying (a) the agent provenance keys and (b) its own display name as an
    alias — mirroring ``_merge_same_type_aliased_duplicates``'s ``aliases_to_add``.
    Dropping it wholesale is what made this module's central claim ("provenance
    travels ON the node") false on exactly the ``existing: true`` path the API
    advertises as the good outcome. The re-emitted node deliberately reuses the
    curated node's ``name``/``type`` so ``prefer_research_node`` cannot rename
    the curated node no matter which side it picks.
    """
    existing_by_id = {n.id: n for n in graph.nodes}
    redirect: Dict[str, str] = {}
    for node in overlay.nodes:
        # The Agent anchor already uses the session graph's id — never redirect
        # it by display name.
        if node.type == ResearchNodeType.AGENT:
            continue
        target = resolve_existing_id(graph, node.type.value, node.name)
        if target and target != node.id:
            redirect[node.id] = target
    if not redirect:
        return overlay
    nodes = [n for n in overlay.nodes if n.id not in redirect]
    carried: Dict[str, List[ResearchNode]] = {}
    for node in overlay.nodes:
        target_id = redirect.get(node.id)
        if target_id:
            carried.setdefault(target_id, []).append(node)
    for target_id in sorted(carried):
        curated = existing_by_id[target_id]
        # Lowest ``agent_write_id`` wins the scalar provenance keys — the same
        # "first write by write_id order wins" rule ``replay_agent_writes``
        # already applies to same-id nodes, and order-free so a merge-order
        # difference between a full and an incremental compile cannot change it.
        losers = sorted(
            carried[target_id],
            key=lambda n: (str(n.metadata.get("agent_write_id") or ""), n.id),
        )
        metadata: Dict[str, Any] = {}
        aliases: set = set()
        for loser in reversed(losers):  # so losers[0] is applied last and wins
            for key in ("agent_write_id", "agent_key", "agent_write_provenance"):
                if key in loser.metadata:
                    metadata[key] = loser.metadata[key]
            for candidate in [loser.name, *(loser.aliases or [])]:
                if candidate and candidate != curated.name:
                    aliases.add(candidate)
        nodes.append(
            ResearchNode(
                id=target_id,
                name=curated.name,
                type=curated.type,
                aliases=sorted(aliases),
                description="",
                metadata=metadata,
            )
        )
    edges = []
    seen = set()
    for edge in overlay.edges:
        source = redirect.get(edge.source, edge.source)
        target = redirect.get(edge.target, edge.target)
        if source == target or (source, edge.type, target) in seen:
            continue
        seen.add((source, edge.type, target))
        edges.append(
            ResearchEdge(
                source=source,
                target=target,
                type=edge.type,
                evidence=edge.evidence,
                metadata=dict(edge.metadata),
            )
        )
    return ResearchGraph(nodes=nodes, edges=edges)


def _resolve_node_ids(
    nodes: List[Mapping[str, Any]], graph: Optional[ResearchGraph]
) -> List[Dict[str, Any]]:
    """Where each written node sits in ``graph`` *right now*.

    ``existing`` is the cheap entity-resolution guard: an agent that typo'd a
    reference sees ``false`` immediately instead of silently forking a node five
    hops from where it meant to attach.

    ``provisional`` is the honest half: a minted id is a PREDICTION, not a
    promise. The very case ``align_overlay`` exists for — an agent writes a
    paper by title before extraction has seen the PDF, extraction then seeds the
    id from the arXiv id — moves the node to a different id on the next compile,
    leaving the returned id dereferencing nothing. Callers must re-resolve via
    :func:`resolve_write_nodes` (keyed on the durable ``write_id``) rather than
    persist an id.
    """
    resolved: List[Dict[str, Any]] = []
    known = {n.id for n in graph.nodes} if graph is not None else set()
    for raw in nodes:
        name = str(raw.get("name") or "")
        type_name = str(raw.get("type") or "")
        aligned = resolve_existing_id(graph, type_name, name)
        node_id = aligned or stable_id(type_name, normalize_display_name(name))
        resolved.append(
            {
                "name": name,
                "type": type_name,
                "id": node_id,
                "existing": bool(aligned),
                "provisional": node_id not in known,
            }
        )
    return resolved


def resolve_write_nodes(
    path: str | Path, write_id: str, graph: Optional[ResearchGraph]
) -> Optional[List[Dict[str, Any]]]:
    """Current node ids for a recorded write, or ``None`` if it is not recorded.

    ``write_id`` is the only durable handle ``graph_write`` hands back — node
    ids are resolved against the graph as it stood at write time and can move
    when a later compile aligns them onto a curated node. Re-resolving here is
    cheap and always answers about the graph passed in; returning ``None`` for
    an unknown write is deliberate — a resolver that guesses is worse than one
    that says NOT_FOUND.
    """
    record = _read_records(Path(path)).get(str(write_id))
    if record is None:
        return None
    raw_nodes = record.get("nodes")
    if not isinstance(raw_nodes, list):
        return []
    return _resolve_node_ids(
        [n for n in raw_nodes if isinstance(n, Mapping)], graph
    )


def record_agent_write(
    path: str | Path,
    payload: Mapping[str, Any],
    agent_key: str,
    *,
    graph: Optional[ResearchGraph] = None,
    written_at: str = "",
    lock_dir: Optional[str | Path] = None,
) -> Dict[str, Any]:
    """Validate + append one write. Returns the MCP response body."""
    validated = validate_write(payload, agent_key)
    write_id = validated.write_id
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)

    anchor = agent_anchor_id(validated.agent)
    # Build the slice once so a payload that cannot be constructed is refused
    # BEFORE anything is appended.
    _graph_from_record(validated.as_record())
    # Report the id the write will land on AS OF NOW: the existing node when the
    # payload resolves onto one, else the id the builder would mint — flagged
    # ``provisional`` when it is not in the graph yet (see ``_resolve_node_ids``).
    resolved = _resolve_node_ids(validated.nodes, graph)

    # Short flock on a DEDICATED file: a read-dedupe-append costs ~1 ms and
    # must not queue behind a multi-minute compile.
    # ponytail: on Windows ``compile_lock`` degrades to a no-op (no ``fcntl``),
    # so concurrent appends are unprotected there — the same pre-existing
    # exposure ``compile.lock`` already carries, not made worse.
    with compile_lock(
        lock_dir or file_path.parent, wait_seconds=10.0, name="agent-writes.lock"
    ):
        status = "duplicate" if write_id in _read_records(file_path) else "recorded"
        if status == "recorded":
            with file_path.open("a", encoding="utf-8") as handle:
                handle.write(_canonical_json(validated.as_record(written_at)) + "\n")

    return {
        "write_id": write_id,
        "status": status,
        "agent": validated.agent,
        "agent_node_id": anchor,
        # See ``_resolve_node_ids``: ``existing`` is the entity-resolution
        # guard, ``provisional`` is the "this id may move on the next compile"
        # warning. ``write_id`` is the durable handle; ``resolve_write_nodes``
        # turns it back into current ids.
        "nodes": resolved,
        "edge_count": len(validated.edges),
    }
