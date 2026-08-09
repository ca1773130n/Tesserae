"""Per-transition ``Event`` memory layer (AgentRunbook component 1).

This is the *event pool* of the AgentRunbook-style multi-granularity memory
ported from LongMemEval-V2 (see
``docs/superpowers/specs/2026-06-13-agentrunbook-memory-design.md``). For every
SIGNIFICANT transition in a harness session — a tool call or a substantive
assistant action turn, not pure chatter — this module mints exactly one
``Event`` node capturing ``{turn_id, actor, action, brief state-change}`` and
links consecutive events with ``precedes`` edges so the dynamic state of a
session can be replayed in order.

Conventions inherited from the rest of ``tesserae`` (mirrors
``tesserae.memory.supersede`` / ``tesserae.community_summaries``):

* **LLM-FREE, degrade-never-raise.** No field is enriched by a model. The
  ``json_client`` parameter is accepted for API symmetry with the other memory
  passes and is never used — see ``extract_events``. This function NEVER raises
  on bad input. (An earlier version of this paragraph described the client as
  enriching the one-line state-change description; it never did, and the pass
  being default-on for session-bearing projects now rests on it not doing so.)
* **Byte-idempotent.** Every minted node id / body / ``first_seen_at`` is
  content-derived. No ``datetime.now()``, no RNG, stable turn-order traversal.
  A rerun produces byte-identical nodes and edges (the project's known
  byte-idempotence blind spot — see the memory note). Measured over a
  481-session corpus: two runs agree byte for byte, and so does a run given a
  client that raises on any attribute access.
* **No home directories.** Turn text is copied into node names and
  descriptions, and those are serialized into ``graph.json`` and every
  projection of it. ``/Users/<name>`` and ``/home/<name>`` are rewritten to
  ``~`` first — the same refusal :mod:`tesserae.okf` makes in §6.2 — by a rule
  that reads only the text, never ``$HOME``, so the bytes do not depend on
  which machine compiled them. See :func:`tesserae.redaction.redact_home_paths`.
* **Additive.** ``extract_events`` only mints new nodes/edges; it mutates
  nothing. An empty / ``None`` session, or a session with no significant turns,
  returns ``([], [])``.
"""

from __future__ import annotations

import logging
import os
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from .redaction import redact_home_paths
from .research_graph import (
    ResearchEdge,
    ResearchNode,
    ResearchNodeType,
    stable_id,
    truncate,
)

logger = logging.getLogger(__name__)

# Edge kinds minted by this pass (both already in ``ALLOWED_EDGE_TYPES``).
PRECEDES_EDGE = "precedes"
"""Orders consecutive ``Event`` nodes within a session (turn order)."""

DERIVED_FROM_EDGE = "derived_from"
"""Links a session-finding node to the Event(s) at its ``turn_ids``."""

# Roles whose turns can become events. ``tool`` and ``tool_result`` turns are
# always significant (an invocation and its OUTCOME are two distinct state
# transitions — see the outcome stamps below); ``assistant`` turns are
# significant only when they carry enough text to describe an action (filtered
# below). Pure ``user`` / ``system`` chatter is skipped.
_ACTION_ROLES = frozenset({"assistant", "tool", "tool_result"})

# Minimum stripped-text length for an assistant turn to count as a substantive
# action (shorter ones are acknowledgements / chatter). Tool turns bypass this.
_MIN_ASSISTANT_ACTION_LEN = 16

#: Falsy spellings shared with the other pass gates.
_FALSY = {"0", "false", "no", "off"}


def event_pass_enabled(
    cfg: Optional[dict] = None,
    env: Optional[Mapping[str, str]] = None,
) -> bool:
    """Decide whether to mint Event nodes from session turns. DEFAULT-ON.

    Opt-OUT, in the shape of ``memory.supersede.supersede_pass_enabled``:
    ``TESSERAE_SESSION_EVENT_PASS`` or ``cfg["session_events"]["enabled"]``
    disables the pass when set to a falsy spelling; env wins over config.

    This pass had NO switch of its own until roadmap step 4 — it shared
    ``distillation_enabled`` with the LLM Runbook/Gotcha distillation, so the
    only way to get these deterministic, template-only nodes was to also buy an
    LLM pass with a disk cache and non-deterministic output. They are different
    kinds of thing and now have different switches. Default-on is safe for
    exactly the reason the LLM pass's default-off is necessary: every field
    here, description included, is derived from the turn with no LLM, no
    wall-clock and no RNG, so a rerun over unchanged sessions is byte-identical.

    Default-on is not free, and the cost is volume rather than correctness: the
    pass mints one node per significant turn, monotonically, for every session
    a project ever records. On a 481-session corpus that measured 10,824 Event
    nodes (+23% of graph size, +33.7% of rendered mass). Hence the opt-out.
    """
    env = env if env is not None else os.environ
    raw = (env.get("TESSERAE_SESSION_EVENT_PASS") or "").strip().lower()
    if raw:
        return raw not in _FALSY

    section = (cfg or {}).get("session_events")
    if isinstance(section, Mapping):
        flag = section.get("enabled")
        if isinstance(flag, str):
            return flag.strip().lower() not in _FALSY
        if flag is not None:
            return bool(flag)
    return True


def extract_events(
    session: object,
    *,
    findings: Optional[Sequence[ResearchNode]] = None,
    json_client: object = None,
) -> Tuple[List[ResearchNode], List[ResearchEdge]]:
    """Mint per-transition ``Event`` nodes from a harness session.

    Walks ``session.metadata["turns"]`` deterministically (turn order). For
    each SIGNIFICANT transition (a tool-call turn, or an assistant turn with
    substantive text) one ``Event`` node is minted carrying ``{turn_id, actor,
    action, state-change}`` in its name / description / metadata. Consecutive
    events are linked with ``precedes`` edges.

    When ``findings`` is supplied — a list of session-finding
    :class:`ResearchNode` objects whose ``metadata["turn_ids"]`` records which
    transcript turns they were extracted from — a ``derived_from`` edge is
    emitted from each finding to every Event minted at one of its turn ids
    (matched by turn id). This is the "integrated into session-finding nodes"
    requirement. Both sides index ``session.metadata["turns"]`` positionally and
    that is the ONLY thing making the match correct — see
    :func:`_significant_turns`.

    ``json_client`` is accepted for API symmetry with the other memory passes
    but is currently UNUSED: every Event field — including the ``description``,
    which is serialized into graph.json — is derived deterministically from the
    turn, with no LLM, no wall-clock and no RNG, so a rerun over an unchanged
    session yields byte-identical output. (A future enrichment must be
    content-keyed and cached, like ``memory.distill``, to preserve that.)

    Returns ``([], [])`` for an empty / ``None`` session or no significant
    turns.
    """
    if session is None:
        return [], []

    session_id = str(getattr(session, "id", "") or "")
    session_started_at = str(getattr(session, "started_at", "") or "")

    turns = _significant_turns(session)
    if not turns:
        return [], []

    nodes: List[ResearchNode] = []
    edges: List[ResearchEdge] = []
    # ``turn_id -> [event_id, ...]`` so findings can wire ``derived_from`` edges
    # by turn id. A list because (defensively) two significant turns could map
    # to the same index only if a producer ever duplicates ids; normally 1:1.
    events_by_turn: Dict[int, List[str]] = {}
    prev_event_id: Optional[str] = None

    for turn_id, seed_key, turn in turns:
        actor = _actor(turn)
        action = _action(turn)
        # Node identity is a content hash of (session_id, seed_key, action) —
        # stable, no wall-clock / RNG. ``stable_id`` already sha1's the seed.
        # ``seed_key``, NOT ``turn_id``: see :func:`_significant_turns`.
        id_seed = f"{session_id}|{seed_key}|{action}"
        node_id = stable_id(ResearchNodeType.EVENT.value, id_seed)
        # The Event ``description`` is fully DETERMINISTIC (template only). It is
        # serialized into graph.json, so it must not depend on a non-cached LLM
        # call: an ambient LLM would otherwise produce different bytes across two
        # identical compiles (the project's byte-idempotence blind spot). Events
        # are per-transition and numerous, so a per-event LLM call is also too
        # costly; the deterministic template is sufficient.
        description = _template_description(turn, actor, action)

        timestamp = str(turn.get("timestamp") or "") or session_started_at
        metadata: Dict[str, object] = {
            "session_id": session_id,
            "turn_id": turn_id,
            "actor": actor,
            "action": action,
            "extractor": "session-event",
        }
        tool_name = str(turn.get("name") or "").strip()
        if tool_name:
            metadata["tool"] = tool_name
        # The OUTCOME of the transition. See :func:`turn_outcome` for why only
        # a tool result gets one and why "nothing was reported" is said aloud.
        metadata.update(turn_outcome(turn))
        # ``first_seen_at`` derives from the turn / session timestamp ONLY —
        # never ``datetime.now()`` — so graph.json stays byte-stable.
        if timestamp:
            metadata["first_seen_at"] = timestamp

        node = ResearchNode(
            id=node_id,
            name=_event_name(turn_id, actor, action),
            type=ResearchNodeType.EVENT,
            description=description,
            metadata=metadata,
        )
        nodes.append(node)
        events_by_turn.setdefault(turn_id, []).append(node_id)

        if prev_event_id is not None and prev_event_id != node_id:
            edges.append(
                ResearchEdge(
                    source=prev_event_id,
                    target=node_id,
                    type=PRECEDES_EDGE,
                )
            )
        prev_event_id = node_id

    edges.extend(_finding_derived_from_edges(findings, events_by_turn))
    return nodes, edges


# ---------------------------------------------------------------------------
# Turn selection (deterministic, source-derived)
# ---------------------------------------------------------------------------


def _significant_turns(
    session: object,
) -> List[Tuple[int, str, Mapping[str, object]]]:
    """Return ``[(turn_id, seed_key, turn), ...]`` for significant transitions.

    TWO positional keys, because they answer two different questions and
    conflating them is what broke this pass when tool results arrived.

    ``turn_id`` is the 0-based index of the turn in ``session.metadata["turns"]``
    — the index space a finding's ``turn_ids`` refer to. That space is defined
    by :func:`tesserae.session_graph._iter_normalised_turns`, which is what
    renders the transcript for the extracting model; it yields one entry per
    entry of ``metadata["turns"]``, so the two spaces are the same list.
    ``derived_from`` is resolved by this number, so if it drifts by even one,
    a finding is attached to a DIFFERENT turn's Event and the graph publishes a
    provenance edge that reads as evidence and is not.

    ``seed_key`` is the positional component of the node id, and it deliberately
    is NOT ``turn_id``: it counts only turns that are not tool results, so it
    equals the ``turn_id`` this pass used before results were ingested at all.
    Inserting a turn renumbers every turn after it, and the id seed carries the
    number, so a positional id churns under insertion by construction —
    measured on the ingest corpus, keying the id on ``turn_id`` moved 1,741 of
    3,213 existing Event ids (54.2%), and every ``derived_from``, citation and
    pinned reference to those ids would have broken silently. Counting past the
    inserted role instead keeps all 3,213. A tool result — which has no
    pre-existing id to preserve — is keyed ``<n>r<k>``: ``n`` conversation turns
    precede it and it is the ``k``-th result in the run after them.

    Pure chatter (user/system messages, trivially short assistant
    acknowledgements) is skipped, but both keys are preserved.
    """
    metadata = getattr(session, "metadata", None)
    raw = metadata.get("turns") if isinstance(metadata, Mapping) else None
    if not isinstance(raw, list):
        return []

    out: List[Tuple[int, str, Mapping[str, object]]] = []
    conversation_ordinal = 0
    result_run = 0
    for turn_id, turn in enumerate(raw):
        role = (
            str(turn.get("role") or "").lower() if isinstance(turn, Mapping) else ""
        )
        if role == "tool_result":
            seed_key = f"{conversation_ordinal}r{result_run}"
            result_run += 1
        else:
            seed_key = str(conversation_ordinal)
            conversation_ordinal += 1
            result_run = 0
        if not isinstance(turn, Mapping):
            continue
        if role not in _ACTION_ROLES:
            continue
        text = str(turn.get("text") or "").strip()
        tool_name = str(turn.get("name") or "").strip()
        # A tool turn IS a transition even with terse text, and so is its
        # result — an image-only or empty tool_result still records that the
        # call returned. An assistant turn must carry substantive prose.
        if role in ("tool", "tool_result"):
            if not (tool_name or text):
                continue
        else:  # assistant
            if len(text) < _MIN_ASSISTANT_ACTION_LEN:
                continue
        out.append((turn_id, seed_key, turn))
    return out


# ---------------------------------------------------------------------------
# Deterministic field derivation
# ---------------------------------------------------------------------------


def _actor(turn: Mapping[str, object]) -> str:
    role = str(turn.get("role") or "").lower()
    return role or "agent"


#: The three things a tool result can say about how it went, and they are
#: exhaustive. ``unreported`` is a VALUE, not a missing key: a result that
#: carried no outcome signal is a real, common, and materially different state
#: from one that succeeded, and 54 of the 1,286 Codex results on the ingest
#: corpus (apply_patch, MCP tools) plus every ``is_error``-less Claude result
#: are in it. Leaving the key out lets a reader supply the default themselves,
#: and the default a reader supplies is "fine".
OUTCOME_OK = "ok"
OUTCOME_ERROR = "error"
OUTCOME_UNREPORTED = "unreported"


def turn_outcome(turn: Mapping[str, object]) -> Dict[str, object]:
    """The ``{status, exit_code}`` stamps a turn has EARNED, and no others.

    Only a ``tool_result`` turn can carry an outcome — it is the only turn that
    IS one. Every other role returns ``{}``, so an Event minted from an ordinary
    turn keeps the metadata it had before outcomes existed, byte for byte.

    For a tool result the answer is a TRI-STATE and it is always stated:

    * ``exit_code`` is stamped only where the harness reported one, and
      ``status`` follows it. Measured on the ingest corpus: 1,232 of 1,286
      Codex ``function_call_output`` payloads (95.8%) carry a
      ``Process exited with code N`` header; **no** Claude tool result carries
      an exit code anywhere — not in the ``tool_result`` block and not in the
      row-level ``toolUseResult`` sibling. "This ran and exited N" is a
      Codex-only claim.
    * ``is_error`` gives a status without an exit code. Claude sets the key on
      431 of its 1,044 results (41.3%) and sets it TRUE on 37 (3.5%).
    * neither signal is ``unreported`` — said out loud, never implied by
      omission. The absence of ``is_error`` on a Claude result is not success:
      the key is simply omitted for most tools, so the 613 results without it
      include every failure those tools had no way to report.

    A non-``bool`` ``is_error`` and a ``bool`` ``exit_code`` are BOTH treated as
    no signal. ``True`` is an ``int`` in Python, so an unguarded exit-code check
    reads a flag as "exited 1"; a truthy-but-not-bool ``is_error`` (the string
    ``"false"``, say) reads as a failure. Both would manufacture an outcome out
    of a type confusion, which is the one thing this function must never do.
    """
    if str(turn.get("role") or "").lower() != "tool_result":
        return {}
    exit_code = turn.get("exit_code")
    is_error = turn.get("is_error")
    if isinstance(exit_code, int) and not isinstance(exit_code, bool):
        return {
            "exit_code": exit_code,
            "status": OUTCOME_OK if exit_code == 0 else OUTCOME_ERROR,
        }
    if isinstance(is_error, bool):
        return {"status": OUTCOME_ERROR if is_error else OUTCOME_OK}
    return {"status": OUTCOME_UNREPORTED}


def _action(turn: Mapping[str, object]) -> str:
    """A short, deterministic label for the transition.

    Tool turns use the tool name (e.g. ``Edit``); assistant turns use a
    truncated, whitespace-collapsed slice of their text. This string feeds the
    node id seed, so it must be deterministic and never depend on the LLM.
    """
    role = str(turn.get("role") or "").lower()
    tool_name = str(turn.get("name") or "").strip()
    text = redact_home_paths(str(turn.get("text") or "").strip())
    if role in ("tool", "tool_result") and tool_name:
        return tool_name
    if text:
        return truncate(text, 80)
    return tool_name or "action"


def _event_name(turn_id: int, actor: str, action: str) -> str:
    return truncate(f"Event {turn_id}: {actor} · {action}", 96)


def _template_description(turn: Mapping[str, object], actor: str, action: str) -> str:
    """Deterministic one-line state-change description (the always-safe path)."""
    role = str(turn.get("role") or "").lower()
    tool_name = str(turn.get("name") or "").strip()
    text = redact_home_paths(str(turn.get("text") or "").strip())
    if role == "tool_result" and tool_name:
        # The result text is truncated to 120 chars here exactly as an
        # invocation's is, so a 2 MB tool result renders no larger a block than
        # any other Event and charter.mass() stays a function of Event COUNT.
        detail = truncate(text, 120)
        outcome = turn_outcome(turn)
        if "exit_code" in outcome:
            verb = f"{tool_name} exited {outcome['exit_code']}"
        elif outcome.get("status") == OUTCOME_ERROR:
            verb = f"{tool_name} failed"
        elif outcome.get("status") == OUTCOME_OK:
            verb = f"{tool_name} succeeded"
        else:
            # ``unreported`` — "returned", never "succeeded".
            verb = f"{tool_name} returned"
        return f"{verb}: {detail}" if detail else verb
    if role == "tool" and tool_name:
        detail = truncate(text, 120) if text else ""
        if detail:
            return f"{actor} invoked {tool_name}: {detail}"
        return f"{actor} invoked {tool_name}"
    return f"{actor} {truncate(text or action, 120)}"


# ---------------------------------------------------------------------------
# Finding ↔ Event wiring
# ---------------------------------------------------------------------------


def _finding_derived_from_edges(
    findings: Optional[Sequence[ResearchNode]],
    events_by_turn: Mapping[int, List[str]],
) -> List[ResearchEdge]:
    """Emit ``finding --derived_from--> event`` edges, matched by turn id.

    A finding's ``metadata["turn_ids"]`` lists the transcript turns it was
    extracted from; for each of those that minted an Event, link the finding to
    that Event. Edges are produced in a deterministic order (finding order,
    then sorted turn id, then event id) so reruns are byte-identical.
    """
    if not findings:
        return []
    edges: List[ResearchEdge] = []
    seen: set = set()
    for finding in findings:
        finding_id = getattr(finding, "id", None)
        if not finding_id:
            continue
        metadata = getattr(finding, "metadata", None)
        if not isinstance(metadata, Mapping):
            continue
        raw_turn_ids = metadata.get("turn_ids")
        if not isinstance(raw_turn_ids, (list, tuple)):
            continue
        turn_ids: List[int] = []
        for value in raw_turn_ids:
            try:
                turn_ids.append(int(value))
            except (TypeError, ValueError):
                continue
        for turn_id in sorted(set(turn_ids)):
            for event_id in events_by_turn.get(turn_id, []):
                key = (finding_id, event_id)
                if key in seen:
                    continue
                seen.add(key)
                edges.append(
                    ResearchEdge(
                        source=finding_id,
                        target=event_id,
                        type=DERIVED_FROM_EDGE,
                        metadata={"via_turn_id": turn_id},
                    )
                )
    return edges
