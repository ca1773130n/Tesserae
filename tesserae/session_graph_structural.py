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
from typing import Iterable, List

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


def extract_structural(
    sessions: Iterable[HarnessSession],
    path_index: DocPathIndex,
    project_root: Path | str,
) -> ResearchGraph:
    """Return a graph slice covering the project-scoped sessions.

    The returned graph contains only ``Session`` + ``SessionDecision``
    nodes and the ``discussed_in`` / ``derived_from_session`` edges
    between them and the doc nodes already resolvable from
    ``path_index``. The caller is responsible for merging this slice
    with the document graph (typically via
    :func:`tesserae.project.merge_graphs`).
    """
    builder = ResearchGraphBuilder()
    project_root_path = Path(project_root).resolve()

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
        for decision_text in session.decisions or []:
            text = (decision_text or "").strip()
            if not text:
                continue
            decision_id_seed = (
                f"session:{session.id}:decision:{_short_hash(text)}"
            )
            builder.add_node(
                name=text,
                node_type=ResearchNodeType.SESSION_DECISION,
                id_seed=decision_id_seed,
                metadata={
                    "session_id": session.id,
                    "extractor": "session-structural",
                },
            )
            decision_node = ResearchNode(
                id=stable_id(
                    ResearchNodeType.SESSION_DECISION.value, decision_id_seed
                ),
                name=text,
                type=ResearchNodeType.SESSION_DECISION,
            )
            builder.add_edge(decision_node, "derived_from_session", session_node)

    return builder.build()


def _short_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


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
