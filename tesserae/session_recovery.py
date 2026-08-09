"""The ``recovers`` edge — 'this succeeded after that failed', as structure.

Roadmap step 6. One causal edge, derived from two OBSERVED outcomes in the same
session: a tool call that reported failure, and a call ISSUED AFTER that failure
landed — to the same tool, the same program family, the same working directory
and the same operand — that reported success, with no success on that operand
observed in between. The succeeding
:class:`~tesserae.research_graph.ResearchNode` Event is the source; the failing
one is the target; both turn ids are named in the evidence and
``metadata["basis"]`` names every dimension the two calls had to agree on.

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
launched** — but "minus how it was launched" is not "minus the program". Leading
``VAR=VAL`` assignments are stripped and everything after the program must match
exactly, while the program itself is reduced to a FAMILY rather than deleted.
That distinction is the whole difference between a key and a coincidence:

    FAIL  PYTHONDONTWRITEBYTECODE=1 python   -c "<expr>"   -> exit 127
    OK    PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -c "<expr>"   -> exit 0

is one program reached two ways, and is a recovery, while

    FAIL  python scripts/build.py --release   -> exit 1
    OK    cat    scripts/build.py --release   -> exit 0

has the same argv and is a *read* of a file being published as a *fix* of it.
Deleting the program token made those two indistinguishable. The family is an
explicit, measured equivalence set with exactly one member — ``python``,
``python3``, ``python3.12`` and ``.venv/bin/python`` are one program, which is 9
of the 9 edges this corpus yields — and every other program keys on its own
basename. Adding a family is something to do on evidence, never on the
assumption that two names probably mean the same thing.

The same rule refuses the false pair that sits three results away in the very
same transcript, where a program-name key would happily publish "the failing
test run was recovered":

    FAIL  .venv/bin/python -m pytest -p no:cacheprovider tests/test_federation_eval.py
    OK    .venv/bin/python -c "<an unrelated import check>"

The test never passed in that session. All surviving pairs are the same honest
shape (``python`` -> ``python3`` / ``.venv/bin/python``, exit 127 -> 0), and the
price is recall: a genuine recovery whose ARGUMENTS changed — a syntax error
fixed inside a heredoc — is missed. Recall loss is the acceptable failure here;
a false causal edge is not.

THREE MORE THINGS THAT HAVE TO AGREE, AND ONE THAT HAS TO BE ORDERED
--------------------------------------------------------------------
* **The working directory.** Codex sends ``workdir`` on 1,223 of its 1,225 shell
  invocations. ``-m pytest tests/x.py`` in two checkouts is two different files.
  An invocation that reports no workdir keys as the empty string — "nowhere
  reported" is not a directory, and must not act as a wildcard.
* **Which call answered which.** The pair is joined by the harness's own call id,
  never by position: 746 of Codex's 1,286 results (58%) were issued while another
  call was outstanding.
* **When the recovering call was ISSUED.** Because of that same batching, a call
  can be in flight when the failure comes back and still produce the later, tidy
  -looking success. The agent had not seen the failure yet, so it cannot be the
  response to it. The recovering INVOCATION turn must sit after the failing
  RESULT turn.
* **Which failure the edge points at.** Among the failures the recovering call
  could have seen, it is the LAST — the attempt it actually followed. Measuring
  from the first also threw away every retry loop longer than the gap bound, in
  which the success is one result after the attempt that preceded it.

On the 103-transcript corpus, tightening all four changed the yield by nothing:
the same 9 edges across the same 7 sessions, byte-identical. Each of them is a
guard against a shape this corpus does not happen to contain, in the same way
``_SIGNAL_EXIT_CODES`` is.

WHAT A PUBLISHED STRING IS
--------------------------
``metadata["anchor"]``, ``metadata["workdir"]``, ``metadata["tool"]``,
``metadata["program_family"]`` and the evidence sentence are transcript text,
and they become bytes in ``graph.json``, the markdown projection and any
exported site. All FIVE, not the first two — an earlier version routed the
anchor and the workdir and left the other two raw, on the assumption that a tool
name and a program basename come from a fixed vocabulary. Neither does: a
basename is whatever the first token happened to be, so a URL invoked directly
yields ``?token=…``. They therefore pass
:func:`tesserae.redaction.redact_published_text` — the same credential and
home-path rules ingest applies to a turn — and they pass it at PUBLICATION
rather than in the key. Redacting before keying would give ``deploy --token=A``
and ``deploy --token=B`` one shared ``[REDACTED]`` anchor, which is the
truncation collapse rebuilt: unrelated work silently joined into one operand.
Redaction runs before truncation, so nothing straddling the cap survives as a
fragment. No credential-shaped token appears in any of the 1,811 anchors this
corpus produces; the routing is there because being downstream of a redactor is
not the same as being redacted.

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
* No keying on a SCOPE. ``path`` is not in :data:`_FILE_KEYS`: 34 of the 35
  invocations that send it are Claude's ``Grep``, where ``pattern`` is what was
  asked for and ``path`` is merely the directory searched, so keying on it is
  the program collision again in the file dimension.
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
import re
import shlex
from typing import Dict, List, Mapping, NamedTuple, Optional, Sequence, Tuple

from .redaction import redact_home_paths, redact_published_text
from .research_graph import ResearchEdge, ResearchNode, truncate
from .session_event import OUTCOME_ERROR, OUTCOME_OK, turn_outcome

RECOVERS_EDGE = "recovers"
"""``succeeded --recovers--> failed``. In ``CAUSAL_EDGE_TYPES``."""

#: Keys that hold a shell command in a tool invocation's arguments. ``command``
#: is Claude's ``Bash``; ``cmd`` is Codex's ``exec_command``, which is 1,233 of
#: its 1,286 results.
_COMMAND_KEYS: Tuple[str, ...] = ("command", "cmd")

#: The working directory a shell tool was told to run in. Codex sends it on
#: 1,223 of its 1,225 ``exec_command`` invocations; Claude's ``Bash`` sends
#: none. The same argv in two different directories is not the same operand —
#: ``.venv/bin/python -m pytest tests/x.py`` in two checkouts is two runs of two
#: different files — so it is part of the key, and an invocation that reports no
#: workdir keys as the empty string rather than joining every other one.
_WORKDIR_KEYS: Tuple[str, ...] = ("workdir", "cwd")

#: Keys that hold the file a tool ACTED ON.
#:
#: ``path`` is deliberately absent. For the tools that send it here it is a
#: SCOPE, not an operand: Claude's ``Grep`` and ``Glob`` take ``pattern`` as
#: what was asked for and ``path`` as the directory to look in, so keying on it
#: would let ``Grep(pattern=A, path=src)`` failing and ``Grep(pattern=B,
#: path=src)`` succeeding mint "recovers" — R2's collision in the file
#: dimension. The one tool where ``path`` really is the operand, Codex's
#: ``view_image``, produced no pairs on the measured corpus, so admitting it
#: buys nothing and costs the collision.
_FILE_KEYS: Tuple[str, ...] = ("file_path", "notebook_path", "filePath")

#: Programs whose non-zero exit is an ANSWER, not a failure. ``rg``/``grep``
#: exit 1 on "no match" and 2 on "one of the path arguments does not exist"
#: while still doing the job they were asked to do — 10 of the corpus's 94 raw
#: failures are exactly this, and every false anchor manufactures a false
#: ``recovers`` downstream. ``diff``/``cmp``/``test`` are the same shape.
#:
#: The set is consulted on two paths, not one. With an exit code, only 1 and 2
#: are answers and anything else (``rg`` exiting 127) is a real failure. With NO
#: exit code — which is every Claude result — "it errored" cannot distinguish
#: the two, so no result from these programs anchors at all. That second path
#: refuses 4 of Claude's 20 anchored failures on the corpus; before it existed
#: the guard could not fire on Claude even once.
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

#: Program tokens that are the SAME program launched differently. The set has
#: exactly one member because the corpus exhibits exactly one such difference:
#: every observed recovery is ``python`` re-launched as ``python3`` or as
#: ``.venv/bin/python``. Anything else keys on its own basename, so a family is
#: something added on evidence, never on the assumption that two names probably
#: mean the same thing.
_PYTHON_PROGRAM = re.compile(r"^python[0-9]*(?:\.[0-9]+)?$")


class _Anchor(NamedTuple):
    """What two calls must agree on before one can be said to recover the other.

    ``operand`` is WHAT was asked for; ``program_family`` and ``workdir`` are
    the part of HOW that still has to agree. Dropping the program entirely — the
    shipped-then-corrected form of this key — made a *read* of a file recover a
    *run* of it, because ``cat foo.py`` and ``python foo.py`` reduce to the same
    argv.
    """

    tool: str
    kind: str  # "command" | "file"
    program_family: str  # "" for the file dimension
    workdir: str  # "" when the harness reported none
    operand: str


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
    # anchor -> the unrecovered failures on it, oldest first
    pending: Dict[_Anchor, List[Tuple[int, int, Mapping[str, object]]]] = {}
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

        anchor = _anchor_key(turn, invocations)
        if anchor is None:
            continue

        if status == OUTCOME_OK:
            # A reported success clears the anchor whether or not it mints an
            # edge. Once one is observed, no later edge on this anchor may say
            # "no success in between", so nothing older may stay pending.
            failures = pending.pop(anchor, None)
            if not failures:
                continue
            issued_at = _issued_turn_id(turn, invocations)
            # The recovering call must have been ISSUED AFTER the failure
            # LANDED. Codex emits a whole batch of calls and then the whole
            # batch of outputs, up to 5 deep, and 58% of its results were issued
            # while another call was outstanding — so without this a call that
            # was already in flight when the failure came back reads as the
            # response to it. Among the failures it could have seen, the LAST
            # one is the attempt it followed.
            informed = [f for f in failures if f[1] < issued_at]
            if not informed:
                continue
            fail_ordinal, fail_turn_id, fail_outcome = informed[-1]
            gap = result_ordinal - fail_ordinal
            if gap > _MAX_RECOVERY_GAP:
                continue
            edge = _recovery_edge(
                events_by_turn=events_by_turn,
                session_id=session_id,
                anchor=anchor,
                fail_turn_id=fail_turn_id,
                fail_outcome=fail_outcome,
                recovery_turn_id=turn_id,
                issued_turn_id=issued_at,
                gap=gap,
            )
            if edge is not None:
                edges.append(edge)
            continue

        if _is_failure_of_intent(anchor, outcome):
            pending.setdefault(anchor, []).append((result_ordinal, turn_id, dict(outcome)))

    return edges


# ---------------------------------------------------------------------------
# The anchor
# ---------------------------------------------------------------------------


def _anchor_key(
    result_turn: Mapping[str, object],
    invocations: Mapping[str, Tuple[int, Mapping[str, object]]],
) -> Optional[_Anchor]:
    """The :class:`_Anchor` for a tool result, or ``None``.

    ``None`` is returned for every result whose operand cannot be established,
    and that includes the truncated ones. 130 of 1,044 Claude invocations are
    cut mid-JSON at the 1,200-char cap; "unparseable" MUST mean no key rather
    than an empty key, because an empty key is shared by every other
    unparseable call and would silently join unrelated work into one anchor.
    """
    invocation = _invocation_of(result_turn, invocations)
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
        program, operand = _command_program_and_operand(command)
        if not operand:
            return None
        return _Anchor(
            tool=tool,
            kind="command",
            program_family=program,
            workdir=_workdir_of(arguments),
            operand=operand,
        )

    path = _first_string(arguments, _FILE_KEYS)
    if path:
        return _Anchor(
            tool=tool,
            kind="file",
            program_family="",
            workdir=_workdir_of(arguments),
            operand=redact_home_paths(" ".join(path.split())),
        )
    return None


def _invocation_of(
    result_turn: Mapping[str, object],
    invocations: Mapping[str, Tuple[int, Mapping[str, object]]],
) -> Optional[Mapping[str, object]]:
    call_id = result_turn.get("call_id")
    if not isinstance(call_id, str) or not call_id:
        # Sessions imported before the producer carried the harness's own id
        # anchor nothing. The alternative — pairing by position — is wrong for
        # 58% of Codex results, which arrive in batches.
        return None
    found = invocations.get(call_id)
    return None if found is None else found[1]


def _issued_turn_id(
    result_turn: Mapping[str, object],
    invocations: Mapping[str, Tuple[int, Mapping[str, object]]],
) -> int:
    """The turn index at which the call this result answers was ISSUED.

    ``-1`` when it cannot be established, which orders before every failure and
    so refuses the pair rather than assuming the call came later.
    """
    call_id = result_turn.get("call_id")
    if not isinstance(call_id, str) or not call_id:
        return -1
    found = invocations.get(call_id)
    return -1 if found is None else found[0]


def _workdir_of(arguments: Mapping[str, object]) -> str:
    value = _first_string(arguments, _WORKDIR_KEYS)
    if not value:
        return ""
    return redact_home_paths(" ".join(value.split()))


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


def _command_program_and_operand(command: str) -> Tuple[str, str]:
    """``(program family, operand)`` — HOW it was launched, and WHAT it asked for.

    Leading ``VAR=VAL`` assignments are dropped. The program token is reduced to
    its family (basename, then the one measured equivalence in
    :data:`_PYTHON_PROGRAM`) rather than deleted: deleting it is what let a
    *read* of a file recover a *run* of it, because ``cat foo.py`` and ``python
    foo.py`` have the same argv. Everything after the program must match
    exactly. A command with nothing after the program (``make``, ``pytest``)
    yields no operand — a bare program name is the program-name key that
    measured ~30% precision.
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
    if index >= len(tokens):
        return ("", "")
    rest = tokens[index + 1 :]
    if not rest:
        return ("", "")
    return (_program_family(tokens[index]), redact_home_paths(" ".join(rest)))


def _program_family(token: str) -> str:
    """The program, with the launcher stripped and one measured alias applied.

    ``.venv/bin/python``, ``/usr/bin/python3`` and ``python3.12`` are one
    program reached three ways; ``cat`` and ``python`` are not.
    """
    name = os.path.basename(token)
    if _PYTHON_PROGRAM.match(name):
        return "python"
    return name


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


def _is_failure_of_intent(anchor: _Anchor, outcome: Mapping[str, object]) -> bool:
    """``status == "error"`` is not, on its own, a failure.

    Roughly 14% of the corpus's raw failure signals are not failures of intent:
    search tools answering "no match", and processes the harness killed. Those
    are excluded HERE, before pairing, because a false anchor does not stay a
    false anchor — it becomes a false causal edge.

    The search-tool test deliberately sits OUTSIDE the exit-code branch. Claude
    reports no exit code on any result, ever — it sends a bare ``is_error`` — so
    a guard nested under ``isinstance(exit_code, int)`` is structurally unable to
    fire on a Claude transcript, and ``grep`` finding nothing and then finding
    something would mint "recovers" on the harness that runs the most greps. When
    the harness reports only "this errored", ``rg`` exiting 1 and ``rg`` failing
    to start are indistinguishable, so neither anchors.
    """
    exit_code = outcome.get("exit_code")
    if isinstance(exit_code, int) and not isinstance(exit_code, bool):
        if exit_code in _SIGNAL_EXIT_CODES:
            return False
        if exit_code in _ANSWER_EXIT_CODES and anchor.program_family in _ANSWER_BY_EXIT_PROGRAMS:
            return False
        return True
    return anchor.program_family not in _ANSWER_BY_EXIT_PROGRAMS


# ---------------------------------------------------------------------------
# Edge construction
# ---------------------------------------------------------------------------


def _recovery_edge(
    *,
    events_by_turn: Mapping[int, str],
    session_id: str,
    anchor: _Anchor,
    fail_turn_id: int,
    fail_outcome: Mapping[str, object],
    recovery_turn_id: int,
    issued_turn_id: int,
    gap: int,
) -> Optional[ResearchEdge]:
    source = events_by_turn.get(recovery_turn_id)
    target = events_by_turn.get(fail_turn_id)
    if not source or not target or source == target:
        return None

    # PUBLICATION BOUNDARY. Everything below becomes bytes in ``graph.json``,
    # the vault and any exported site, so it passes the same credential rules
    # ingest applies — and it passes them HERE rather than in the key, because
    # redacting before keying would give ``deploy --token=A`` and ``deploy
    # --token=B`` one shared ``[REDACTED]`` anchor: the truncation collapse,
    # rebuilt. Redaction runs before truncation for the same reason ingest runs
    # it before its cap: a secret straddling the cut must not survive as a
    # fragment.
    shown = truncate(redact_published_text(anchor.operand), _ANCHOR_DISPLAY_LIMIT)
    workdir = truncate(redact_published_text(anchor.workdir), _ANCHOR_DISPLAY_LIMIT)
    # ``program_family`` and ``tool`` are published too — into metadata AND into
    # the evidence sentence — so they go through the same boundary. They are
    # derived from transcript text, not from a fixed vocabulary: a program
    # basename is whatever the first token happened to be, and a URL invoked
    # directly gives a basename like ``?token=…``. Leaving two of the four
    # published strings unredacted because the other two are is exactly the hole
    # this round opened with.
    tool = redact_published_text(anchor.tool)
    program_family = redact_published_text(anchor.program_family)
    kind = anchor.kind
    exit_code = fail_outcome.get("exit_code")
    if isinstance(exit_code, int) and not isinstance(exit_code, bool):
        failed_as = f"exited {exit_code}"
    else:
        failed_as = "reported an error"

    if kind == "command":
        basis = "observed-error-then-observed-ok/same-tool+program-family+workdir+argv-after-program"
    else:
        basis = "observed-error-then-observed-ok/same-tool+file-path"

    metadata: Dict[str, object] = {
        "session_id": session_id,
        "tool": tool,
        "anchor": shown,
        "anchor_kind": kind,
        "program_family": program_family,
        "workdir": workdir,
        # What this edge was derived FROM, per EDGE — not per edge type, and
        # naming the derivation rather than restating the conclusion. A reader
        # who distrusts one basis can discount one edge.
        "basis": basis,
        "gap": gap,
        "failure_turn_id": fail_turn_id,
        "recovery_turn_id": recovery_turn_id,
        "recovery_issued_turn_id": issued_turn_id,
        "failure_status": fail_outcome.get("status"),
        "extractor": "session-recovery",
    }
    if isinstance(exit_code, int) and not isinstance(exit_code, bool):
        metadata["failure_exit_code"] = exit_code

    return ResearchEdge(
        source=source,
        target=target,
        type=RECOVERS_EDGE,
        evidence=_evidence(
            tool=tool,
            kind=kind,
            failed_as=failed_as,
            shown=shown,
            workdir=workdir,
            program_family=program_family,
            fail_turn_id=fail_turn_id,
            recovery_turn_id=recovery_turn_id,
            gap=gap,
        ),
        metadata=metadata,
    )


def _evidence(
    *,
    tool: str,
    kind: str,
    failed_as: str,
    shown: str,
    workdir: str,
    program_family: str,
    fail_turn_id: int,
    recovery_turn_id: int,
    gap: int,
) -> str:
    """Exactly what was observed, and nothing that was not.

    The shipped sentence said "succeeded on the same command" when the commands
    demonstrably differed — that difference is the whole point of the key — and
    "with no success on it in between" when the rule had only ever looked at
    results whose operand it could read and whose outcome the harness reported.
    An unreported success can sit between the two and that sentence would still
    have claimed there was none. This is the one field a human reads, so it says
    what the rule saw: which two calls agreed on what, what was NOT compared, and
    what was not examined.
    """
    if kind == "command":
        agreed = (
            f"Both calls ran a {program_family!r}-family program"
            + (f" in workdir {workdir!r}" if workdir else " (no working directory was reported)")
            + f" and passed the same arguments after the program name: {shown!r}."
            " The full command texts were not compared."
        )
        # Name the WHOLE key, not the argv alone. The in-between search is over
        # the anchor — tool, program family, workdir and argv — so a readable,
        # status-bearing success that shares the arguments but differs in family
        # or workdir sits between the pair without disqualifying it. Saying "on
        # those arguments" claims the rule looked at something it demonstrably
        # did not, which is the same over-claim this sentence was rewritten to
        # remove, one dimension narrower.
        subject = "that same program family, working directory and arguments"
    else:
        agreed = f"Both calls named the same file {shown!r}. The other arguments were not compared."
        subject = "that file, from the same tool"
    return (
        f"turn {fail_turn_id}: {tool} {failed_as}. "
        f"turn {recovery_turn_id}: {tool} reported success {gap} result(s) later, "
        f"from a call issued after that failure landed. "
        f"{agreed} "
        f"In between, no result this rule could read reported success on {subject}; "
        f"results with no reported outcome, and results whose operand could not be "
        f"derived, were not examined."
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


def _invocations_by_call_id(
    turns: Sequence[object],
) -> Dict[str, Tuple[int, Mapping[str, object]]]:
    """``call_id -> (turn index, the tool turn that issued it)``.

    The harness's own identifier, stored by ``harness_sessions._tool_call_turn``.
    First occurrence wins; a re-used id is a producer bug, not something to
    guess about. The index is kept because WHEN a call was issued is the only
    thing that can tell a fix apart from a call that was already in flight.
    """
    out: Dict[str, Tuple[int, Mapping[str, object]]] = {}
    for turn_id, turn in enumerate(turns):
        if not isinstance(turn, Mapping):
            continue
        if str(turn.get("role") or "").lower() != "tool":
            continue
        call_id = turn.get("call_id")
        if isinstance(call_id, str) and call_id:
            out.setdefault(call_id, (turn_id, turn))
    return out
