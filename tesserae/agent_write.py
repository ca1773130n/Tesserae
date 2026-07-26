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
    ResearchEdge,
    ResearchGraph,
    ResearchGraphBuilder,
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
    "agent_anchor_id",
    "record_agent_write",
    "replay_agent_writes",
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
            evidence=f"written by agent {agent_key} (write {write_id})",
        )
    for raw in record.get("edges") or []:
        source = by_ref.get(str(raw["source"]))
        target = by_ref.get(str(raw["target"]))
        if source is None or target is None:
            # Unreachable via record_agent_write (validate_write refuses these),
            # but a hand-edited JSONL must not take a compile down.
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


def replay_agent_writes(path: str | Path) -> ResearchGraph:
    """Replay the JSONL overlay into a graph. Pure function of the file bytes.

    Records are replayed **sorted by write_id**, not in append order, so two
    agents appending in either interleaving converge on the same merge order —
    and therefore the same ``prefer_research_node`` winners and the same
    ``graph.json`` bytes.
    """
    file_path = Path(path)
    if not file_path.exists():
        return ResearchGraph(nodes=[], edges=[])
    records: Dict[str, Dict[str, Any]] = {}
    for line in file_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        record = json.loads(line)
        records[str(record.get("write_id") or "")] = record
    nodes: Dict[str, Any] = {}
    edges: Dict[Tuple[str, str, str], Any] = {}
    for write_id in sorted(records):
        slice_graph = _graph_from_record(records[write_id])
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
    same-type dedup pass uses — so a write lands on the curated node instead of
    forking beside it. Ambiguity (two existing nodes sharing the key) refuses,
    for the same reason ``verify_claim`` refuses: guessing an entity is how a
    5-hop chain drops to 44% trustworthy.
    """
    if graph is None:
        return None
    try:
        node_enum = ResearchNodeType(node_type)
    except ValueError:
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
    """
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
    # Report the id the write will ACTUALLY land on: the existing node when the
    # payload resolves onto one, else the id the builder would mint.
    minted = {}
    known = set()
    for raw in validated.nodes:
        aligned = resolve_existing_id(graph, raw["type"], raw["name"])
        if aligned:
            known.add(aligned)
        minted[raw["name"]] = aligned or stable_id(
            raw["type"], normalize_display_name(raw["name"])
        )

    # Short flock on a DEDICATED file: a read-dedupe-append costs ~1 ms and
    # must not queue behind a multi-minute compile.
    # ponytail: on Windows ``compile_lock`` degrades to a no-op (no ``fcntl``),
    # so concurrent appends are unprotected there — the same pre-existing
    # exposure ``compile.lock`` already carries, not made worse.
    with compile_lock(
        lock_dir or file_path.parent, wait_seconds=10.0, name="agent-writes.lock"
    ):
        seen = set()
        if file_path.exists():
            for line in file_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    try:
                        seen.add(str(json.loads(line).get("write_id") or ""))
                    except json.JSONDecodeError:
                        continue
        status = "duplicate" if write_id in seen else "recorded"
        if status == "recorded":
            with file_path.open("a", encoding="utf-8") as handle:
                handle.write(_canonical_json(validated.as_record(written_at)) + "\n")

    return {
        "write_id": write_id,
        "status": status,
        "agent": validated.agent,
        "agent_node_id": anchor,
        # ``existing`` is the cheap entity-resolution guard: an agent that
        # typo'd a reference sees ``false`` immediately instead of silently
        # forking a node five hops from where it meant to attach.
        "nodes": [
            {
                "name": raw["name"],
                "type": raw["type"],
                "id": minted[raw["name"]],
                "existing": minted[raw["name"]] in known,
            }
            for raw in validated.nodes
        ],
        "edge_count": len(validated.edges),
    }
