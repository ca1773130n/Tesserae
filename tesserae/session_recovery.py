"""The ``recovers`` edge — 'this succeeded after that failed', as structure.

Roadmap step 6. One causal edge, derived from two OBSERVED outcomes in the same
session: a tool call that reported failure, and a later call to the SAME tool on
the SAME operand that reported success, with no success on that operand in
between. The succeeding :class:`~tesserae.research_graph.ResearchNode` Event is
the source; the failing one is the target; both turn ids are named in the
evidence and ``metadata["basis"]`` records how the pair was derived.

WHY THIS IS NARROW, AND WHY IT STAYS THAT WAY
---------------------------------------------
A survey of four leading agent-memory systems found that not one of them derives
a causal edge. Two infer their strongest link from co-occurrence — one records
that an insight was in context when another was distilled, the other admits its
episodic hyperedge means "happened at the same time". One takes an LLM's word
for an open vocabulary of relation labels with no verification. One has no edges
at all. So the state of the art here is co-occurrence with better naming, and
the failure mode this module exists to avoid is shipping a ``caused_by`` that is
really ``happened_near``. In a graph the two are indistinguishable, and the
wrong one is read as evidence.

WHAT THE OPERAND IS, AND WHY IT IS NOT THE COMMAND
--------------------------------------------------
Measured over 103 real transcripts (2,330 tool results, 94 raw failures) re-parsed
with the step 5 parsers, four candidate anchor keys, strict pairing throughout:

===========================  ======  ==================================
key                          pairs   hand-verified
===========================  ======  ==================================
tool name only                   34  ~garbage — 1,233 of Codex's 1,286
                                     results are ONE name, so this reads
                                     "a shell command failed and later
                                     some shell command succeeded"
program name (``python``)        16  ~30% — see the refusals below
exact command string              0  zero BY CONSTRUCTION: an agent that
                                     retries a command CHANGES it. That
                                     change is what fixing means.
argv after the program           10  10 of 10 genuine
===========================  ======  ==================================

The key this module uses is the last one: **what was asked for, minus how it was
launched**. Leading ``VAR=VAL`` assignments and the program token are stripped;
everything after them must match exactly. That is not a tuned threshold, it is
the observation that a fix changes the invocation while the intent stays put:

    FAIL  PYTHONDONTWRITEBYTECODE=1 python   -c "<expr>"   -> exit 127
    OK    PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -c "<expr>"   -> exit 0

...and the same rule refuses the false pair that sits three results away in the
very same transcript, where a program-name key would happily publish "the
failing test run was recovered":

    FAIL  .venv/bin/python -m pytest -p no:cacheprovider tests/test_federation_eval.py
    OK    .venv/bin/python -c "<an unrelated import check>"

The test never passed in that session. All ten surviving pairs are the same
honest shape (``python`` -> ``python3`` / ``.venv/bin/python``, exit 127 -> 0),
and the price is recall: a genuine recovery whose ARGUMENTS changed — a syntax
error fixed inside a heredoc — is missed. Recall loss is the acceptable failure
here; a false causal edge is not.

WHAT THIS DOES NOT DO
---------------------
* No ``caused_by``, no ``blocked_by``. The corpus offers 94 failures across 32 of
  103 sessions and, after honest filtering, on the order of ten credible
  recoveries. Nothing in that justifies a wider causal vocabulary, and the
  measurement that would license one is not available (see below).
* No file-dimension inference from silence. "Edit failed on X, then Edit
  succeeded on X" is the most credible recovery a session can contain and it is
  UNOBSERVABLE on Claude: 90 of its 95 ``Edit`` results are ``unreported``, so
  the success side does not exist. A file anchor is implemented and correct, but
  it fires only on a real ``is_error: false`` — never on the absence of a
  failure signal. It produced 0 pairs on the corpus.
* No promotion of the 212 existing ``CausalClaim`` nodes.
  ``classify_claim_type`` reaches CAUSAL_CLAIM on the bare substring ``"by "``;
  21 of those 212 rest on that alone, with match contexts including "motion of
  nearby", "followed by", and "stated by". That precision does not survive
  becoming structure.

EFFECT ON THE COMPILED GRAPH: UNMEASURED
----------------------------------------
Roadmap steps 4 and 5 merged after the last compile, so the live graph carries
zero producer-minted Events (all 226 have ``extractor=None`` — document
extraction), zero Events with a ``status``, and zero ``SessionFailure`` nodes.
The anchors this module keys on do not exist in the compiled artifact yet, and
the stored session records are equally pre-step-5 (0 turns with role
``tool_result``). Every number above was measured against raw transcripts
re-parsed with the step 5 parsers, never against ``graph.json``.

Conventions, inherited from :mod:`tesserae.session_event`: LLM-free,
wall-clock-free, RNG-free, byte-idempotent, additive, degrade-never-raise, and
``/Users/<name>`` never reaches a node body.
"""

from __future__ import annotations

import json
import os
import shlex
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from .redaction import redact_home_paths
from .research_graph import ResearchEdge, ResearchNode, truncate
from .session_event import OUTCOME_ERROR, OUTCOME_OK, turn_outcome

RECOVERS_EDGE = "recovers"
"""``succeeded --recovers--> failed``. In ``CAUSAL_EDGE_TYPES``."""

#: Keys that hold a shell command in a tool invocation's arguments. ``command``
#: is Claude's ``Bash``; ``cmd`` is Codex's ``exec_command``, which is 1,233 of
#: its 1,286 results.
_COMMAND_KEYS: Tuple[str, ...] = ("command", "cmd")

#: Keys that hold a file path. ``path`` covers Codex ``view_image``.
_FILE_KEYS: Tuple[str, ...] = ("file_path", "notebook_path", "filePath", "path")

#: Programs whose non-zero exit is an ANSWER, not a failure. ``rg``/``grep``
#: exit 1 on "no match" and 2 on "one of the path arguments does not exist"
#: while still doing the job they were asked to do — 10 of the corpus's 94 raw
#: failures are exactly this, and every false anchor manufactures a false
#: ``recovers`` downstream. ``diff``/``cmp``/``test`` are the same shape.
_ANSWER_BY_EXIT_PROGRAMS = frozenset(
    {"rg", "grep", "egrep", "fgrep", "ag", "ack", "diff", "cmp", "test"}
)
_ANSWER_EXIT_CODES = frozenset({1, 2})

#: 128+N: the process was KILLED by signal N, it did not decide to fail. 143 is
#: the harness's own 10-minute timeout on a backgrounded command. Killing a
#: command is not evidence that the next command fixed anything.
#:
#: Unexercised by the measured corpus (Codex's failure codes are 1x35, 127x13,
#: 2x7, 255x2; Claude reports no exit code at all, so its three observed
#: timeouts arrive as a bare ``is_error`` and this guard cannot see them). It is
#: kept as a forward guard and covered by a synthetic fixture, and its zero
#: rejections on real data are stated rather than implied.
_SIGNAL_EXIT_CODES = frozenset({129, 130, 131, 134, 137, 139, 141, 143})

#: How far apart, in tool results, a failure and its recovery may sit.
#:
#: The no-intervening-success rule is doing the real work — measured, it
#: discards 90.4% of naive candidates (270 -> 26 on a program-name key). It is
#: necessary and NOT sufficient: the survivors under that looser key still
#: included a pair 24 results apart and one 201 apart, in a different release
#: cycle. Under the operand key every observed pair has a gap of 5 or less, so
#: this bound excluded nothing on the measured corpus; it exists so a
#: session-spanning coincidence cannot mint an edge, and the actual gap is
#: recorded on every edge so a reader can discount one.
_MAX_RECOVERY_GAP = 50

#: The anchor is copied into edge metadata and evidence, so it is bounded for
#: the same reason a tool result's text is.
_ANCHOR_DISPLAY_LIMIT = 200


def detect_recoveries(
    session: object,
    events: Sequence[ResearchNode],
) -> List[ResearchEdge]:
    """Mint ``recovers`` edges between the ``Event`` nodes of one session.

    ``events`` is what :func:`tesserae.session_event.extract_events` returned
    for this same session; the edges are wired by ``metadata["turn_id"]``, so an
    Event that was not minted (a filtered turn) simply anchors nothing rather
    than producing a dangling edge.

    Session-local by construction: nothing here looks at another session, at the
    clock, or at a model. Returns ``[]`` for anything it cannot read.
    """
    turns = _turns_of(session)
    if not turns:
        return []
    events_by_turn = _events_by_turn(events)
    if not events_by_turn:
        return []
    session_id = str(getattr(session, "id", "") or "")

    invocations = _invocations_by_call_id(turns)
    edges: List[ResearchEdge] = []
    # anchor key -> (result ordinal, turn id, outcome, anchor)
    pending: Dict[Tuple[str, str, str], Tuple[int, int, Mapping[str, object], str]] = {}
    result_ordinal = -1

    for turn_id, turn in enumerate(turns):
        if not isinstance(turn, Mapping):
            continue
        if str(turn.get("role") or "").lower() != "tool_result":
            continue
        result_ordinal += 1

        outcome = turn_outcome(turn)
        status = outcome.get("status")
        # ``unreported`` is neither: it cannot open an anchor and — the part
        # that matters — it cannot CLOSE one either. Reading the absence of a
        # failure signal as success is the single inference step 5 refused, and
        # it is the one that would make the file dimension appear to work.
        if status not in (OUTCOME_OK, OUTCOME_ERROR):
            continue

        key = _anchor_key(turn, invocations)
        if key is None:
            continue

        if status == OUTCOME_OK:
            anchored = pending.pop(key, None)
            if anchored is None:
                continue
            fail_ordinal, fail_turn_id, fail_outcome, anchor = anchored
            gap = result_ordinal - fail_ordinal
            if gap > _MAX_RECOVERY_GAP:
                continue
            edge = _recovery_edge(
                events_by_turn=events_by_turn,
                session_id=session_id,
                tool=key[0],
                kind=key[1],
                anchor=anchor,
                fail_turn_id=fail_turn_id,
                fail_outcome=fail_outcome,
                recovery_turn_id=turn_id,
                gap=gap,
            )
            if edge is not None:
                edges.append(edge)
            continue

        # A failure. Only the FIRST unrecovered one on this anchor is held, so
        # a repeated failure does not create a second pending anchor that the
        # one success would have to satisfy twice.
        if _is_failure_of_intent(key, outcome, invocations, turn):
            pending.setdefault(key, (result_ordinal, turn_id, dict(outcome), key[2]))

    return edges


# ---------------------------------------------------------------------------
# The anchor
# ---------------------------------------------------------------------------


def _anchor_key(
    result_turn: Mapping[str, object],
    invocations: Mapping[str, Mapping[str, object]],
) -> Optional[Tuple[str, str, str]]:
    """``(tool, kind, operand)`` for a tool result, or ``None``.

    ``None`` is returned for every result whose operand cannot be established,
    and that includes the truncated ones. 130 of 1,044 Claude invocations are
    cut mid-JSON at the 1,200-char cap; "unparseable" MUST mean no key rather
    than an empty key, because an empty key is shared by every other
    unparseable call and would silently join unrelated work into one anchor.
    """
    call_id = result_turn.get("call_id")
    if not isinstance(call_id, str) or not call_id:
        # Sessions imported before the producer carried the harness's own id
        # anchor nothing. The alternative — pairing by position — is wrong for
        # 58% of Codex results, which arrive in batches.
        return None
    invocation = invocations.get(call_id)
    if invocation is None:
        return None
    tool = str(result_turn.get("name") or "").strip()
    if not tool or tool != str(invocation.get("name") or "").strip():
        return None

    arguments = _parsed_arguments(invocation)
    if arguments is None:
        return None

    command = _first_string(arguments, _COMMAND_KEYS)
    if command is not None:
        operand = _command_operand(command)
        if operand:
            return (tool, "command", operand)
        return None

    path = _first_string(arguments, _FILE_KEYS)
    if path:
        return (tool, "file", redact_home_paths(" ".join(path.split())))
    return None


def _parsed_arguments(invocation: Mapping[str, object]) -> Optional[Dict[str, object]]:
    """The invocation's arguments as a dict, or ``None`` if they do not parse."""
    text = invocation.get("text")
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _first_string(arguments: Mapping[str, object], keys: Sequence[str]) -> Optional[str]:
    for key in keys:
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return value
        if isinstance(value, list) and value and all(isinstance(x, str) for x in value):
            # ``["bash", "-lc", "<script>"]``: the script is the operand.
            if len(value) >= 3 and value[1] in ("-lc", "-c", "-l"):
                return value[2]
            return " ".join(value)
    return None


def _command_operand(command: str) -> Optional[str]:
    """WHAT the command asked for, with HOW it was launched removed.

    Leading ``VAR=VAL`` assignments and the program token are dropped; the rest
    must match exactly. A command with nothing after the program (``make``,
    ``pytest``) yields ``None`` — a bare program name is not an operand, it is
    the program-name key that measured ~30% precision.
    """
    try:
        tokens = shlex.split(command)
    except ValueError:
        # Unbalanced quotes — common in truncated text. Fall back to whitespace
        # splitting rather than guessing at the intent.
        tokens = command.split()
    index = 0
    while index < len(tokens) and _is_env_assignment(tokens[index]):
        index += 1
    rest = tokens[index + 1 :]
    if not rest:
        return None
    return redact_home_paths(" ".join(rest))


def _is_env_assignment(token: str) -> bool:
    name, sep, _ = token.partition("=")
    if not sep or not name:
        return False
    if not (name[0].isalpha() or name[0] == "_"):
        return False
    return all(ch.isalnum() or ch == "_" for ch in name)


# ---------------------------------------------------------------------------
# Is this failure a failure?
# ---------------------------------------------------------------------------


def _is_failure_of_intent(
    key: Tuple[str, str, str],
    outcome: Mapping[str, object],
    invocations: Mapping[str, Mapping[str, object]],
    result_turn: Mapping[str, object],
) -> bool:
    """``status == "error"`` is not, on its own, a failure.

    Roughly 14% of the corpus's raw failure signals are not failures of intent:
    search tools answering "no match", and processes the harness killed. Those
    are excluded HERE, before pairing, because a false anchor does not stay a
    false anchor — it becomes a false causal edge.
    """
    exit_code = outcome.get("exit_code")
    if isinstance(exit_code, int) and not isinstance(exit_code, bool):
        if exit_code in _SIGNAL_EXIT_CODES:
            return False
        if exit_code in _ANSWER_EXIT_CODES and _program_of(key, invocations, result_turn) in _ANSWER_BY_EXIT_PROGRAMS:
            return False
    return True


def _program_of(
    key: Tuple[str, str, str],
    invocations: Mapping[str, Mapping[str, object]],
    result_turn: Mapping[str, object],
) -> str:
    if key[1] != "command":
        return ""
    call_id = result_turn.get("call_id")
    invocation = invocations.get(call_id) if isinstance(call_id, str) else None
    if invocation is None:
        return ""
    arguments = _parsed_arguments(invocation)
    if arguments is None:
        return ""
    command = _first_string(arguments, _COMMAND_KEYS)
    if not command:
        return ""
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    index = 0
    while index < len(tokens) and _is_env_assignment(tokens[index]):
        index += 1
    if index >= len(tokens):
        return ""
    return os.path.basename(tokens[index])


# ---------------------------------------------------------------------------
# Edge construction
# ---------------------------------------------------------------------------


def _recovery_edge(
    *,
    events_by_turn: Mapping[int, str],
    session_id: str,
    tool: str,
    kind: str,
    anchor: str,
    fail_turn_id: int,
    fail_outcome: Mapping[str, object],
    recovery_turn_id: int,
    gap: int,
) -> Optional[ResearchEdge]:
    source = events_by_turn.get(recovery_turn_id)
    target = events_by_turn.get(fail_turn_id)
    if not source or not target or source == target:
        return None

    shown = truncate(anchor, _ANCHOR_DISPLAY_LIMIT)
    exit_code = fail_outcome.get("exit_code")
    if isinstance(exit_code, int) and not isinstance(exit_code, bool):
        failed_as = f"exited {exit_code}"
    else:
        failed_as = "reported an error"

    metadata: Dict[str, object] = {
        "session_id": session_id,
        "tool": tool,
        "anchor": shown,
        "anchor_kind": kind,
        # How this edge was derived, per EDGE — not per edge type. A reader who
        # distrusts one basis can discount one edge.
        "basis": f"observed-failure-then-success/{kind}-operand",
        "gap": gap,
        "failure_turn_id": fail_turn_id,
        "recovery_turn_id": recovery_turn_id,
        "failure_status": fail_outcome.get("status"),
        "extractor": "session-recovery",
    }
    if isinstance(exit_code, int) and not isinstance(exit_code, bool):
        metadata["failure_exit_code"] = exit_code

    evidence = (
        f"turn {fail_turn_id}: {tool} {failed_as} on {kind} {shown!r}; "
        f"turn {recovery_turn_id}: {tool} succeeded on the same {kind}, "
        f"{gap} result(s) later, with no success on it in between."
    )
    return ResearchEdge(
        source=source,
        target=target,
        type=RECOVERS_EDGE,
        evidence=evidence,
        metadata=metadata,
    )


# ---------------------------------------------------------------------------
# Reading the session
# ---------------------------------------------------------------------------


def _turns_of(session: object) -> List[object]:
    if session is None:
        return []
    metadata = getattr(session, "metadata", None)
    raw = metadata.get("turns") if isinstance(metadata, Mapping) else None
    return list(raw) if isinstance(raw, list) else []


def _events_by_turn(events: Sequence[ResearchNode]) -> Dict[int, str]:
    """``turn_id -> event id``, first Event wins (``extract_events`` mints 1:1)."""
    out: Dict[int, str] = {}
    for event in events or []:
        metadata = getattr(event, "metadata", None)
        node_id = getattr(event, "id", None)
        if not isinstance(metadata, Mapping) or not node_id:
            continue
        turn_id = metadata.get("turn_id")
        if isinstance(turn_id, int) and not isinstance(turn_id, bool):
            out.setdefault(turn_id, str(node_id))
    return out


def _invocations_by_call_id(turns: Sequence[object]) -> Dict[str, Mapping[str, object]]:
    """``call_id -> the tool turn that issued it``.

    The harness's own identifier, stored by ``harness_sessions._tool_call_turn``.
    First occurrence wins; a re-used id is a producer bug, not something to
    guess about.
    """
    out: Dict[str, Mapping[str, object]] = {}
    for turn in turns:
        if not isinstance(turn, Mapping):
            continue
        if str(turn.get("role") or "").lower() != "tool":
            continue
        call_id = turn.get("call_id")
        if isinstance(call_id, str) and call_id:
            out.setdefault(call_id, turn)
    return out
