"""LLM-backed extraction of structured findings from session transcripts.

Reads the normalized turns inside a :class:`HarnessSession`'s metadata
(NOT the raw transcript file on disk — that's intentional, see the
spec's "Transcript source" decision), sends them to an
:class:`LLMJsonClient` with a JSON-schema-bearing prompt, parses the
response into typed :class:`Finding` records, and returns them for
the orchestrator to mint into the graph.

Doesn't touch the graph directly; that's the orchestrator's job
(:class:`tesserae.session_graph.SessionGraphExtractor`). This module
is purely "transcript → list of structured findings".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from .harness_sessions import HarnessSession
from .llm_json import LLMJsonClient

logger = logging.getLogger(__name__)


# The six finding kinds. Mirrors the six new ``Session<Kind>``
# ResearchNodeType entries added in Phase 1. The LLM must emit one of
# these literal strings as the ``kind`` field on every finding.
ALLOWED_FINDING_KINDS = (
    "insight",
    "decision",
    "question",
    "todo",
    "hypothesis",
    "takeaway",
)


@dataclass
class Finding:
    """One structured extraction from a session transcript."""

    kind: str
    body: str
    turn_ids: List[int] = field(default_factory=list)
    references: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Prompt scaffolding
# ---------------------------------------------------------------------------


_PROMPT_SYSTEM = """You are an extractor that reads agent/user conversation transcripts and \
produces a structured list of findings as JSON. Findings fall into six kinds:

- "insight"     — a learned fact, observed pattern, or non-obvious connection that emerged in the conversation
- "decision"    — an explicit choice the user and agent agreed on
- "question"    — an unresolved question raised during the conversation
- "todo"        — an actionable follow-up
- "hypothesis"  — a testable assumption that hasn't been verified yet
- "takeaway"    — a condensed key point worth remembering after the session

Each finding MUST cite (1) the turn IDs from the transcript that the finding is \
derived from, and (2) the node IDs from the supplied "available doc node IDs" \
list that the finding refers to. Cite only IDs from that list — do not invent.

Output schema (strictly):

{
  "findings": [
    {
      "kind": "<one of: insight | decision | question | todo | hypothesis | takeaway>",
      "body": "<single-line statement of the finding, <= 240 chars>",
      "turn_ids": [<int>, <int>, ...],
      "references": ["<doc_node_id>", "<doc_node_id>", ...]
    },
    ...
  ]
}

Constraints:
- Return JSON only. No prose, no markdown fences.
- The "findings" array may be empty if nothing of substance was discussed.
- Do NOT speculate. If nothing decision-shaped exists, omit the decision rather than inventing one.
- Each finding body should be self-contained — a reader who hasn't seen the transcript should grasp the point."""


def _build_user_message(
    *,
    transcript_turns: Sequence[dict],
    doc_id_context: Sequence[Tuple[str, str]],
) -> str:
    """Assemble the per-call user message with transcript + doc ID context."""
    turn_lines: List[str] = []
    for idx, turn in enumerate(transcript_turns):
        role = str(turn.get("role") or "?")
        text = str(turn.get("text") or "").strip()
        if not text:
            continue
        turn_lines.append(f"[turn_id={idx}] [{role}] {text}")

    doc_lines = [f"  - {nid}  ({display})" for nid, display in doc_id_context]
    doc_block = (
        "Available doc node IDs (cite only from this list):\n"
        + ("\n".join(doc_lines) if doc_lines else "  (none)")
    )

    return (
        f"{doc_block}\n\n"
        "Transcript:\n"
        f"{chr(10).join(turn_lines) if turn_lines else '(empty)'}\n\n"
        "Return the JSON findings list now."
    )


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------


def _chunk_turns(
    turns: Sequence[dict],
    *,
    max_turns_per_chunk: int,
    overlap: int = 5,
) -> List[Sequence[dict]]:
    """Split a long turn list into overlapping windows.

    Overlap of ``overlap`` turns lets cross-chunk-boundary findings still
    have context. The starting index of each chunk is
    ``i * (max_turns_per_chunk - overlap)`` so windows step by
    ``max_turns_per_chunk - overlap``.
    """
    if not turns:
        return []
    if len(turns) <= max_turns_per_chunk:
        return [turns]

    step = max(1, max_turns_per_chunk - max(0, overlap))
    chunks: List[Sequence[dict]] = []
    i = 0
    while i < len(turns):
        chunks.append(turns[i : i + max_turns_per_chunk])
        i += step
        # Avoid emitting a tiny tail chunk that's fully contained in the
        # previous one due to the overlap.
        if i + overlap >= len(turns) and chunks and len(chunks[-1]) >= max_turns_per_chunk:
            tail = turns[i:]
            if tail and len(tail) > overlap:
                chunks.append(tail)
            break
    return chunks


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------


def extract_with_llm(
    session: HarnessSession,
    transcript_turns: Sequence[dict],
    doc_id_context: Sequence[Tuple[str, str]],
    client: LLMJsonClient,
    *,
    max_turns_per_chunk: int = 30,
    overlap: int = 5,
    cache_key: Optional[str] = None,
    guidance: str = "",
) -> List[Finding]:
    """Run the LLM extraction pass over a single session's transcript.

    Returns a list of validated :class:`Finding` objects. Returns an
    empty list when:
      * the transcript is empty,
      * every LLM call returns ``None`` (no client / all retries
        exhausted), or
      * every returned finding is invalid (unknown kind, empty body,
        all references hallucinated).

    Invalid references are dropped per-finding rather than discarding
    the whole finding — this matches the spec's "drop unknowns" policy
    and avoids silently losing valid extractions because the LLM also
    cited one nonexistent doc id.
    """
    if not transcript_turns:
        return []

    known_doc_ids = {nid for nid, _ in doc_id_context}
    system_prompt = _PROMPT_SYSTEM
    if guidance:
        system_prompt = (
            _PROMPT_SYSTEM
            + "\n\n## Project-specific extraction guidance "
            "(learned from prior human corrections)\n" + guidance
        )
    chunks = _chunk_turns(
        transcript_turns,
        max_turns_per_chunk=max_turns_per_chunk,
        overlap=overlap,
    )

    findings: List[Finding] = []
    for chunk_idx, chunk in enumerate(chunks):
        user = _build_user_message(
            transcript_turns=chunk,
            doc_id_context=doc_id_context,
        )
        response = client.complete_json(
            system=system_prompt,
            user=user,
            schema_name="session-finding-v1",
            cache_key=cache_key,
        )
        if response is None:
            logger.info(
                "session %s chunk %d/%d: client returned None; skipping chunk",
                session.id, chunk_idx + 1, len(chunks),
            )
            continue
        if isinstance(response, list):
            raw = response
        elif isinstance(response, dict):
            raw = response.get("findings") or []
        else:
            raw = []

        for item in raw:
            f = _validate_finding(item, known_doc_ids, session_id=session.id)
            if f is not None:
                findings.append(f)

    return findings


def _validate_finding(
    item,
    known_doc_ids: set,
    *,
    session_id: str,
) -> Optional[Finding]:
    """Convert an LLM-returned dict to a :class:`Finding`, or None if invalid."""
    if not isinstance(item, dict):
        return None
    kind = str(item.get("kind") or "").strip().lower()
    body = str(item.get("body") or "").strip()
    if kind not in ALLOWED_FINDING_KINDS:
        logger.debug(
            "session %s: dropping finding with unknown kind=%r", session_id, kind
        )
        return None
    if not body:
        return None
    # Normalize turn_ids — accept ints or numeric strings; drop anything else.
    turn_ids: List[int] = []
    for t in item.get("turn_ids") or []:
        try:
            turn_ids.append(int(t))
        except (TypeError, ValueError):
            continue
    # references: keep only IDs we recognise. Empty list is fine — the
    # orchestrator will fall back to structural linkage via files_touched.
    references: List[str] = []
    dropped: List[str] = []
    for r in item.get("references") or []:
        rid = str(r or "").strip()
        if not rid:
            continue
        if rid in known_doc_ids:
            references.append(rid)
        else:
            dropped.append(rid)
    if dropped:
        logger.debug(
            "session %s: dropped %d unknown references on %s finding: %s",
            session_id, len(dropped), kind, dropped[:5],
        )
    return Finding(kind=kind, body=body, turn_ids=turn_ids, references=references)
