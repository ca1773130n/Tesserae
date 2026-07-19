"""Deterministic structural pass for the session graph extractor.

Reads normalized :class:`HarnessSession` records (already produced by
``tesserae.harness_sessions.discover_harness_sessions``) and emits a
:class:`ResearchGraph` slice containing:

* one ``Session`` node per harness session that matches the current
  ``project_root`` (private — no vault page; carries the lightweight
  metadata envelope so MCP can answer "what did we do last Tuesday?");
* one ``discussed_in`` edge from every doc node whose ``source_path``
  matches an entry in the session's ``files_touched`` list;
* one ``SessionDecision`` node per entry in the session's existing
  ``decisions`` field, each with a ``derived_from_session`` edge back
  to the parent ``Session``.

Crucially, this pass runs unconditionally on every compile. It costs
zero LLM calls and produces real graph reachability — "which sessions
touched this paper?" is answerable even when no LLM backend is
configured. The richer Insight / Question / Hypothesis / Takeaway /
TODO findings are added by :mod:`tesserae.session_graph_llm` when a
backend is available.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Set

from .agent_identity import ORG_ROOT, AgentRegistry, resolve_agent_key
from .harness_sessions import HarnessSession, session_matches_project
from .research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchGraphBuilder,
    ResearchNode,
    ResearchNodeType,
    stable_id,
)
from .session_graph_path_index import DocPathIndex

# Cap on ``top_concepts`` per structural ExpertiseProfile — enough for a
# capability card, small enough that one hot session can't balloon the node.
_TOP_CONCEPT_LIMIT = 10


def extract_structural(
    sessions: Iterable[HarnessSession],
    path_index: DocPathIndex,
    project_root: Path | str,
    registry: Optional[AgentRegistry] = None,
) -> ResearchGraph:
    """Return a graph slice covering the project-scoped sessions.

    The returned graph contains ``Session`` + ``SessionDecision`` nodes
    and the ``discussed_in`` / ``derived_from_session`` edges between
    them and the doc nodes already resolvable from ``path_index``, plus
    the agent-layer substrate (spec 2026-07-19 §12 Phase 1): one
    ``Agent`` node per role-grade agent_key observed across the session
    set, ``performed_by`` edges Session → Agent, ``reports_to`` edges
    Agent → parent Agent from the org registry (implicit ``org:root``
    included), and one structural ``ExpertiseProfile`` per observed
    agent (§8.2). All of it is a pure function of the session envelopes
    plus the registry file, so it lives inside the CMP-03 byte-
    idempotent compile. The caller is responsible for merging this
    slice with the document graph (typically via
    :func:`tesserae.project.merge_graphs`).
    """
    builder = ResearchGraphBuilder()
    project_root_path = Path(project_root).resolve()
    agent_registry = (
        registry if registry is not None else AgentRegistry.for_project(project_root_path)
    )
    observations = _AgentObservations()

    for session in sessions:
        # Privacy invariant: only process sessions whose project_root
        # matches. ``discover_harness_sessions`` should already have
        # filtered upstream, but doing it again here is cheap and
        # protects against callers that fed us pre-loaded sessions
        # from a different scope.
        if not session_matches_project(session, project_root_path):
            continue

        session_node = builder.add_node(
            name=_session_display_name(session),
            node_type=ResearchNodeType.SESSION,
            id_seed=f"harness:{session.id}",
            source_path=None,
            metadata=_session_envelope_metadata(session),
        )

        # `discussed_in` edges from resolved Papers/Concepts/etc. → Session.
        for touched in session.files_touched or []:
            node_id = path_index.lookup(touched)
            if not node_id:
                continue
            # Pseudo-node for the resolved doc on the source side. Builder's
            # add_edge takes ResearchNode objects; we synthesise a minimal
            # one. The graph already has the real doc node — merging will
            # collapse our pseudo onto it via id-dedup.
            doc_pseudo = ResearchNode(
                id=node_id,
                # name/type don't matter for merge; the real node wins.
                name="",
                type=ResearchNodeType.SOURCE_DOCUMENT,
            )
            builder.add_edge(doc_pseudo, "discussed_in", session_node)

        # Structural SessionDecisions from the field
        # ``discover_harness_sessions`` already populates.
        #
        # Stamp memory-decay timestamps from the parent Session so these
        # nodes age correctly under ``tesserae.memory.decay``. Without
        # this, structural decisions from year-old sessions would score
        # 1.0 (treated as freshly minted) and crowd genuinely fresh
        # findings out of ``fresh_insights``. Mirrors the LLM extractor
        # in ``session_graph.py``.
        session_anchor_ts = _session_anchor_timestamp(session)
        for decision_text in session.decisions or []:
            text = (decision_text or "").strip()
            if not text:
                continue
            decision_id_seed = (
                f"session:{session.id}:decision:{_short_hash(text)}"
            )
            decision_metadata: dict = {
                "session_id": session.id,
                "extractor": "session-structural",
            }
            # Deterministic decay anchor ONLY. ``access_count`` /
            # ``last_accessed_at`` are mutable sidecar state and must NEVER be
            # stamped onto node.metadata — ``ResearchNode.model_dump``
            # serializes the whole metadata dict into graph.json, so they would
            # leak wall-clock state and break byte-idempotence. They live
            # exclusively in the ``node_memory`` sidecar.
            if session_anchor_ts:
                decision_metadata["first_seen_at"] = session_anchor_ts
            builder.add_node(
                name=text,
                node_type=ResearchNodeType.SESSION_DECISION,
                id_seed=decision_id_seed,
                metadata=decision_metadata,
            )
            decision_node = ResearchNode(
                id=stable_id(
                    ResearchNodeType.SESSION_DECISION.value, decision_id_seed
                ),
                name=text,
                type=ResearchNodeType.SESSION_DECISION,
            )
            builder.add_edge(decision_node, "derived_from_session", session_node)

        # Role-grade agent attribution (spec §3.1). The parent session's own
        # agent plus one agent per subagent descriptor; the subagent work has
        # no Session node of its own, so its ``performed_by`` edge hangs off
        # the parent Session. Keys iterate sorted for deterministic edge order.
        session_clock = session.ended_at or session.started_at or ""
        parent_key = resolve_agent_key(session, agent_registry)
        observations.observe(
            parent_key,
            label=session.agent_label,
            session_id=session.id,
            files=session.files_touched or [],
            clock=session_clock,
            # DISTINCT stripped texts, matching the minted SessionDecision
            # nodes above (same-text decisions within a session collapse to
            # one hash-seeded node) — so the profile's finding_counts routing
            # signal (§8.2) always agrees with the queryable graph.
            decision_count=len(
                {(d or "").strip() for d in session.decisions or []} - {""}
            ),
            path_index=path_index,
        )
        performer_keys = {parent_key}
        subagents = (session.metadata or {}).get("subagents")
        if isinstance(subagents, list):
            for descriptor in subagents:
                if not isinstance(descriptor, Mapping):
                    continue
                sub_key = resolve_agent_key(session, agent_registry, subagent=descriptor)
                performer_keys.add(sub_key)
                observations.observe(
                    sub_key,
                    label=str(descriptor.get("type") or "") or None,
                    session_id=session.id,
                    files=descriptor.get("files_touched") or [],
                    clock=str(
                        descriptor.get("ended_at")
                        or descriptor.get("started_at")
                        or session_clock
                    ),
                    decision_count=0,
                    path_index=path_index,
                )
        for agent_key in sorted(performer_keys):
            builder.add_edge(session_node, "performed_by", _agent_pseudo(agent_key))

    _mint_agent_layer(builder, observations, agent_registry)
    return builder.build()


def _short_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


class _AgentObservations:
    """Per-agent accumulators for the structural agent layer.

    Everything gathered here is a pure function of the session envelopes:
    session membership, structural finding counts, doc-node mention counts
    (via ``path_index``), the corpus clock (max ``ended_at or started_at``
    over the agent's scope — spec §7.1, never wall clock), and candidate
    display labels.
    """

    def __init__(self) -> None:
        self.session_ids: Dict[str, Set[str]] = {}
        self.finding_counts: Dict[str, Dict[str, int]] = {}
        self.concept_counts: Dict[str, Dict[str, int]] = {}
        self.clocks: Dict[str, str] = {}
        self.labels: Dict[str, Set[str]] = {}

    def observe(
        self,
        agent_key: str,
        *,
        label: Optional[str],
        session_id: str,
        files: object,
        clock: str,
        decision_count: int,
        path_index: DocPathIndex,
    ) -> None:
        self.session_ids.setdefault(agent_key, set()).add(session_id)
        if label and label.strip():
            self.labels.setdefault(agent_key, set()).add(label.strip())
        if decision_count:
            counts = self.finding_counts.setdefault(agent_key, {})
            kind = ResearchNodeType.SESSION_DECISION.value
            counts[kind] = counts.get(kind, 0) + decision_count
        if isinstance(files, list):
            concepts = self.concept_counts.setdefault(agent_key, {})
            for touched in files:
                node_id = path_index.lookup(str(touched))
                if node_id:
                    concepts[node_id] = concepts.get(node_id, 0) + 1
        if clock and clock.strip():
            # ISO-8601 strings order lexicographically, so max() is the
            # corpus clock without any datetime parsing.
            self.clocks[agent_key] = max(self.clocks.get(agent_key, ""), clock.strip())


def _agent_pseudo(agent_key: str) -> ResearchNode:
    """Minimal Agent node carrying only the stable id, for edge endpoints.

    Mirrors the ``doc_pseudo`` / ``decision_node`` pattern above: builder's
    add_edge takes ResearchNode objects but only reads ``.id``; the real
    Agent node (minted in :func:`_mint_agent_layer`) wins on merge.
    """
    return ResearchNode(
        id=stable_id(ResearchNodeType.AGENT.value, f"agent:{agent_key}"),
        name="",
        type=ResearchNodeType.AGENT,
    )


def _mint_agent_layer(
    builder: ResearchGraphBuilder,
    observations: _AgentObservations,
    registry: AgentRegistry,
) -> None:
    """Mint Agent nodes, ``reports_to`` chains, and structural profiles.

    Every observed agent is walked up its registry parent chain to the
    implicit ``org:root`` (spec §3.2 zero-config default), minting the
    intermediate registry-declared agents along the way so the org chart
    is fully queryable in-graph. All iteration is over sorted keys —
    identical inputs must yield an identical structural slice.
    """
    if not observations.session_ids:
        return

    # Fail-loud registry read (spec §3.2): a corrupt registry aborts the
    # compile rather than silently flattening the org chart.
    registry_agents = registry.load().get("agents") or {}

    # Observed agents plus their transitive registry parents, and the
    # child → parent pairs for reports_to edges.
    all_keys: Set[str] = set(observations.session_ids)
    reports_pairs: Set[tuple] = set()
    for agent_key in sorted(observations.session_ids):
        current = agent_key
        seen = {current}
        while current != ORG_ROOT:
            parent = registry.effective_parent(current)
            if parent in seen:
                # Defensive only: registry load() rejects self-parents and
                # parent cycles (agent_identity._validate), so this can fire
                # only if that validation regresses — never hang the compile,
                # never mint a cycle-closing reports_to edge.
                break
            reports_pairs.add((current, parent))
            all_keys.add(parent)
            seen.add(parent)
            current = parent

    for agent_key in sorted(all_keys):
        entry = registry_agents.get(agent_key)
        builder.add_node(
            name=agent_key,
            node_type=ResearchNodeType.AGENT,
            id_seed=f"agent:{agent_key}",
            metadata=_agent_metadata(
                agent_key, entry, observations.labels.get(agent_key)
            ),
        )

    for child, parent in sorted(reports_pairs):
        builder.add_edge(_agent_pseudo(child), "reports_to", _agent_pseudo(parent))

    # One structural ExpertiseProfile per OBSERVED agent (§8.2) — the
    # capability card a manager/router reads. Registry-declared agents with
    # no sessions in scope get no profile (there is nothing to profile).
    for agent_key in sorted(observations.session_ids):
        top_concepts = [
            node_id
            for node_id, _count in sorted(
                observations.concept_counts.get(agent_key, {}).items(),
                key=lambda kv: (-kv[1], kv[0]),
            )[:_TOP_CONCEPT_LIMIT]
        ]
        profile_metadata: dict = {
            "agent": agent_key,
            "session_count": len(observations.session_ids[agent_key]),
            "finding_counts": dict(
                sorted(observations.finding_counts.get(agent_key, {}).items())
            ),
            "top_concepts": top_concepts,
        }
        # Corpus clock of the agent's scope (§7.1) — omitted entirely when no
        # session carried a timestamp, never defaulted to "now".
        clock = observations.clocks.get(agent_key)
        if clock:
            profile_metadata["distilled_through"] = clock
        builder.add_node(
            name=f"Expertise: {agent_key}",
            node_type=ResearchNodeType.EXPERTISE_PROFILE,
            id_seed=f"profile:{agent_key}",
            metadata=profile_metadata,
        )


def _agent_metadata(
    agent_key: str,
    registry_entry: Optional[Mapping[str, object]],
    observed_labels: Optional[Set[str]],
) -> dict:
    """Agent node metadata per the §4 closed allowlist.

    ``harness`` / ``account`` / ``role`` are the key's own components when it
    is envelope-shaped (``h:a:r``); registry-declared agents with free-form
    keys just omit them (the lint allowlist permits subsets). The label
    prefers the registry declaration, then the sorted-first observed label,
    then the key itself — all deterministic.
    """
    metadata: dict = {"agent_key": agent_key}
    if agent_key == ORG_ROOT:
        metadata["label"] = "Org root"
        return metadata
    parts = agent_key.split(":")
    if len(parts) == 3:
        metadata["harness"], metadata["account"], metadata["role"] = parts
    label = ""
    if isinstance(registry_entry, Mapping):
        label = str(registry_entry.get("label") or "")
    if not label and observed_labels:
        label = sorted(observed_labels)[0]
    metadata["label"] = label or agent_key
    return metadata


def _session_anchor_timestamp(session: HarnessSession) -> str | None:
    """Pick the best timestamp to anchor decay for a session's children.

    Prefers ``started_at`` (when the session began producing the
    decisions); falls back to ``ended_at``. Returns ``None`` only when
    both are missing — in which case the caller omits decay metadata
    rather than minting a misleading "now" anchor for a historical
    session.
    """
    for candidate in (session.started_at, session.ended_at):
        if candidate and str(candidate).strip():
            return str(candidate)
    return None


def _session_display_name(session: HarnessSession) -> str:
    """Human-readable name for a Session node (e.g. ``2026-05-19 weekly digest``)."""
    title = (session.title or session.slug or session.id).strip()
    date = session.date
    if date and date != "undated" and date not in title:
        return f"{date} — {title}" if title else date
    return title or session.id


def _session_envelope_metadata(session: HarnessSession) -> dict:
    """The Session envelope's metadata.

    Deliberately omits ``raw_transcript_path`` (filesystem-local; not
    needed for graph queries) and the full transcript turns (those
    only travel to the LLM extractor at extraction time, never into
    the graph). ``redacted_preview`` is kept as a short human-readable
    teaser for MCP responses.
    """
    payload = {
        "session_id": session.id,
        "harness": session.harness,
        "agent_label": session.agent_label,
        "project_name": session.project_name,
        "project_root": session.project_root,
        "started_at": session.started_at,
        "ended_at": session.ended_at,
        "branch": session.branch,
        "commit_before": session.commit_before,
        "commit_after": session.commit_after,
        "model": session.model,
        "title": session.title,
        "summary": session.summary,
        "message_count": session.message_count,
        "tool_call_count": session.tool_call_count,
        "files_touched": list(session.files_touched or []),
        "tools_used": list(session.tools_used or []),
        "redacted_preview": session.redacted_preview,
    }
    # Drop empty strings so the metadata payload stays terse.
    return {k: v for k, v in payload.items() if v not in ("", None)}
