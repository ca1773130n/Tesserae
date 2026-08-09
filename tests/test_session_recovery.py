"""Roadmap step 6 — the ``recovers`` edge.

Every test here is BEHAVIOURAL: it names a thing the graph must or must not
say, not a function it must call. The load-bearing ones are the refusals —
``test_unrelated_command_on_the_same_program_is_not_a_recovery`` and its
neighbours — because a wrong ``recovers`` edge reads as evidence in a way a
wrong node does not, and every anchor key loose enough to produce pairs at all
produces mostly wrong ones (measured: program-name keying yields 16 pairs on
the 103-transcript corpus at roughly 30% hand-verified precision).
"""

from __future__ import annotations

import json
import re

import pytest

from tesserae.research_graph import (
    ALLOWED_EDGE_TYPES,
    CAUSAL_EDGE_TYPES,
    EXTRACTABLE_EDGE_TYPES,
    ResearchEdge,
)
from tesserae.session_event import extract_events
from tesserae.session_recovery import RECOVERS_EDGE, detect_recoveries


# ---------------------------------------------------------------------------
# Fixtures — sessions shaped like the real transcripts
# ---------------------------------------------------------------------------


class _Session:
    def __init__(self, turns, session_id="codex:s1"):
        self.id = session_id
        self.started_at = "2026-01-01T00:00:00Z"
        self.metadata = {"turns": list(turns)}


def _call(name, args, call_id, ts="2026-01-01T00:00:00Z"):
    return {
        "role": "tool",
        "timestamp": ts,
        "name": name,
        "text": json.dumps(args, ensure_ascii=False, sort_keys=True),
        "call_id": call_id,
    }


def _result(name, call_id, *, exit_code=None, is_error=None, text="", ts="2026-01-01T00:00:00Z"):
    turn = {"role": "tool_result", "timestamp": ts, "name": name, "text": text, "call_id": call_id}
    if exit_code is not None:
        turn["exit_code"] = exit_code
    if is_error is not None:
        turn["is_error"] = is_error
    return turn


def _edges(session):
    nodes, _ = extract_events(session)
    return nodes, detect_recoveries(session, nodes)


def _recovers(session):
    _, edges = _edges(session)
    return [e for e in edges if e.type == RECOVERS_EDGE]


EXPR = 'from tesserae.engine import pidlock; print(pidlock.serialize())'


def _interpreter_recovery_session():
    """The real shape, quoted from ``codex:019edcdc-2f5f-7353-a791-6cf6ed460610``:
    ``python -c EXPR`` exits 127 (command not found), then ``python3 -c EXPR``
    exits 0. One token changed, and the change IS the lesson."""
    return _Session(
        [
            _call("exec_command", {"cmd": f'python -c "{EXPR}"'}, "c1"),
            _result("exec_command", "c1", exit_code=127, text="zsh:1: command not found: python"),
            _call("exec_command", {"cmd": f'python3 -c "{EXPR}"'}, "c2"),
            _result("exec_command", "c2", exit_code=0, text="ok"),
        ]
    )


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------


def test_a_leading_env_assignment_is_not_the_program():
    """The ``VAR=VAL`` strip, quoted from the two corpus sessions that need it.

    ``codex:019f0936`` and ``codex:019f0bff`` both prefix the command with
    ``PYTHONDONTWRITEBYTECODE=1``. Without the strip that assignment becomes the
    "program", identically on both sides, and ``python`` vs ``.venv/bin/python``
    falls into the OPERAND instead — where it must match exactly, so the pair is
    refused.

    Nothing pinned this. Disabling ``_is_env_assignment`` left the whole module
    green at 47 passed while the corpus yield dropped from 9 edges across 7
    sessions to 7 across 5 — a silent loss of 22% of everything this step
    produces. The module docstring uses this exact shape as its worked example,
    and no test used one.
    """
    session = _Session(
        [
            _call("exec_command", {"cmd": f'PYTHONDONTWRITEBYTECODE=1 python -c "{EXPR}"'}, "c1"),
            _result("exec_command", "c1", exit_code=127, text="command not found: python"),
            _call(
                "exec_command",
                {"cmd": f'PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -c "{EXPR}"'},
                "c2",
            ),
            _result("exec_command", "c2", exit_code=0, text="ok"),
        ]
    )

    edges = _recovers(session)

    assert len(edges) == 1, (
        "the env assignment must be stripped so the program family is compared; "
        f"got {[e.metadata.get('anchor') for e in edges]}"
    )
    assert edges[0].metadata["program_family"] == "python"
    assert "PYTHONDONTWRITEBYTECODE" not in str(edges[0].metadata["anchor"])


def test_recovers_is_in_the_ontology_and_is_causal():
    assert RECOVERS_EDGE == "recovers"
    assert RECOVERS_EDGE in ALLOWED_EDGE_TYPES
    assert RECOVERS_EDGE in CAUSAL_EDGE_TYPES
    ResearchEdge(source="a", target="b", type=RECOVERS_EDGE)


def test_a_causal_edge_is_producer_owned_and_no_llm_may_mint_one():
    """The Event pathology, refused up front: 226 of 226 live Event nodes were
    minted by document extraction. A causal edge asserted by a paper-reading
    LLM would be indistinguishable in the graph from a detector-derived one."""
    from tesserae.agent_write import DENIED_EDGE_TYPES

    assert CAUSAL_EDGE_TYPES <= DENIED_EDGE_TYPES
    assert not (CAUSAL_EDGE_TYPES & EXTRACTABLE_EDGE_TYPES)
    assert EXTRACTABLE_EDGE_TYPES < ALLOWED_EDGE_TYPES


def test_graph_write_refuses_a_causal_edge():
    from tesserae.agent_write import validate_write
    from tesserae.llm_extractor import GraphJSONValidationError

    payload = {
        "provenance": {"agent": "agent-x", "session_id": "s1"},
        "nodes": [
            {"key": "a", "name": "A", "type": "Concept", "description": "a thing"},
            {"key": "b", "name": "B", "type": "Concept", "description": "another"},
        ],
        "edges": [{"source": "a", "target": "b", "type": RECOVERS_EDGE,
                   "evidence": "because"}],
    }
    with pytest.raises(GraphJSONValidationError) as exc:
        validate_write(payload, "agent-x")
    assert RECOVERS_EDGE in str(exc.value)
    assert "producer-owned" in str(exc.value)


def test_the_document_extraction_prompt_never_offers_a_causal_edge():
    from tesserae.llm_extractor import build_research_extraction_prompt

    prompt = build_research_extraction_prompt("some text", None, "paper")
    assert '"recovers"' not in prompt


def test_document_extraction_drops_an_llm_asserted_causal_edge():
    from tesserae.llm_extractor import graph_from_llm_payload

    payload = {
        "nodes": [
            {"key": "a", "name": "Alpha", "type": "Concept"},
            {"key": "b", "name": "Beta", "type": "Concept"},
        ],
        "edges": [
            {"source": "a", "target": "b", "type": RECOVERS_EDGE, "evidence": "e"},
            {"source": "a", "target": "b", "type": "uses", "evidence": "e"},
        ],
    }
    graph = graph_from_llm_payload(payload, source_path=None, source_kind="paper")
    assert [e.type for e in graph.edges] == ["uses"]


def _semantic_edge_types_literal() -> str:
    """The body of the ``SEMANTIC_EDGE_TYPES`` object, and nothing else.

    Scoped rather than searched for across the whole blob: ``recovers: 1``
    appearing ANYWHERE in 1,000 lines of JS — in a weights table, a comment, an
    unrelated map — would satisfy a whole-blob substring while ``edgeClassOf()``
    still rendered the only causal edge in the graph as a faint structural line.
    """
    from tesserae.site import js as site_js

    start = site_js.JS_GRAPH.index("var SEMANTIC_EDGE_TYPES = {")
    end = site_js.JS_GRAPH.index("};", start)
    return site_js.JS_GRAPH[start:end]


def test_every_hand_maintained_edge_list_knows_the_causal_types():
    """One source of truth, enforced where the code cannot derive it."""
    from tesserae.lint import _REASONING_EDGE_TYPES
    from tesserae.retrieval.ppr import DEFAULT_EDGE_TYPE_WEIGHTS

    assert CAUSAL_EDGE_TYPES <= _REASONING_EDGE_TYPES
    assert CAUSAL_EDGE_TYPES <= set(DEFAULT_EDGE_TYPE_WEIGHTS)
    literal = _semantic_edge_types_literal()
    for edge_type in CAUSAL_EDGE_TYPES:
        assert re.search(rf"\b{re.escape(edge_type)}\s*:\s*1\b", literal), (
            f"{edge_type} missing from SEMANTIC_EDGE_TYPES — edgeClassOf() would "
            "render the only causal edge in the graph as a faint structural line"
        )


# ---------------------------------------------------------------------------
# What a recovery IS
# ---------------------------------------------------------------------------


def test_a_failed_command_rerun_with_a_fixed_interpreter_is_a_recovery():
    nodes, edges = _edges(_interpreter_recovery_session())
    recovers = [e for e in edges if e.type == RECOVERS_EDGE]
    assert len(recovers) == 1
    edge = recovers[0]
    by_id = {n.id: n for n in nodes}
    # The edge runs success -> failure: "this succeeded, and it recovers that".
    assert by_id[edge.source].metadata["turn_id"] == 3
    assert by_id[edge.target].metadata["turn_id"] == 1
    assert by_id[edge.source].metadata["status"] == "ok"
    assert by_id[edge.target].metadata["status"] == "error"


def test_the_edge_names_both_turns_and_how_it_was_derived():
    edge = _recovers(_interpreter_recovery_session())[0]
    assert edge.metadata["failure_turn_id"] == 1
    assert edge.metadata["recovery_turn_id"] == 3
    assert "1" in (edge.evidence or "") and "3" in (edge.evidence or "")
    basis = edge.metadata["basis"]
    assert isinstance(basis, str) and basis
    # "how was this derived" must be answerable per EDGE, not per edge type,
    # and it must name the DERIVATION rather than restate the conclusion:
    # every dimension the two calls had to agree on, and what was observed at
    # each end. "failure-then-success" alone implies more than was seen.
    assert "argv-after-program" in basis
    assert "program-family" in basis
    assert "workdir" in basis
    assert "observed-error-then-observed-ok" in basis
    assert edge.metadata["tool"] == "exec_command"
    assert edge.metadata["program_family"] == "python"
    assert edge.metadata["gap"] == 1
    assert edge.metadata["failure_exit_code"] == 127


def test_the_evidence_describes_only_what_was_observed():
    """The honesty defect this whole step exists to avoid, in the one field a
    human reads. The two commands DIFFERED — that difference is the entire point
    of the key — so the sentence may not say "the same command"; the anchor is
    argv after the program, not "the command"; and the rule only ever looked at
    results whose operand it could read and whose outcome the harness reported,
    so it may not claim there was no success in between full stop."""
    edge = _recovers(_interpreter_recovery_session())[0]
    ev = edge.evidence or ""
    assert "the same command" not in ev
    assert "on command" not in ev
    # It says which dimension actually agreed...
    assert "the same arguments after the program name" in ev
    assert "'python'-family program" in ev
    # ...and admits what it did NOT compare and did NOT look at.
    assert "The full command texts were not compared." in ev
    assert "results with no reported outcome" in ev
    assert "whose operand could not be derived, were not examined" in ev


def test_the_evidence_says_which_program_family_and_working_directory_agreed():
    session = _Session(
        [
            _call("exec_command", {"cmd": f'python -c "{EXPR}"', "workdir": "/tmp/proj"}, "c1"),
            _result("exec_command", "c1", exit_code=127),
            _call("exec_command", {"cmd": f'python3 -c "{EXPR}"', "workdir": "/tmp/proj"}, "c2"),
            _result("exec_command", "c2", exit_code=0),
        ]
    )
    edge = _recovers(session)[0]
    assert "in workdir '/tmp/proj'" in (edge.evidence or "")
    assert edge.metadata["workdir"] == "/tmp/proj"


# ---------------------------------------------------------------------------
# A published string is a published secret — R1
# ---------------------------------------------------------------------------


#: A credential SHAPE, assembled at import time so no finished literal is
#: committed. It must look real to the redactor and to nothing else.
#:
#: The first version hard-coded the token and named it ``LIVE_KEY`` — which is
#: what a genuinely leaked one would be called. GitGuardian failed the pull
#: request, correctly: a repository cannot tell a fixture from a leak by
#: looking, and that is the whole premise of secret scanning.
#:
#: THE BODY IS WHAT GETS DETECTED, not the vendor prefix. That took four
#: attempts to learn, so it is written down:
#:
#:   1. hard-coded ``sk-ant-api03-<40 random-looking chars>``  -> flagged
#:   2. same body, prefix assembled from fragments             -> flagged
#:   3. every vendor shape deleted from the tree               -> STILL flagged,
#:      because an earlier commit in the same pull request still carried it and
#:      the scan covers all nine
#:   4. body replaced with a low-entropy, self-describing string -> this
#:
#: Attempts 1-3 all chased the ``sk-ant`` prefix. The detector was never looking
#: at the prefix; it was looking at 40 characters of mixed-case alphanumeric
#: entropy, which is what a real key body looks like and what a fixture has no
#: reason to contain. Splitting the prefix defeated a match that was not
#: happening.
#:
#: So the body now spells out what it is. It still matches the ``sk[-_]ant[-_]``
#: rule in ``redaction.SECRET_PATTERNS`` — verified, and the tests below still
#: fail when redaction is disabled — while carrying no entropy for a scanner to
#: object to. Deleting the fixture was the alternative, and it cost the only
#: test of the rule that exists because this shape is the most likely credential
#: on a machine running this codebase; the coverage was worth keeping.
#:
#: An ignore rule was never the answer: it would teach the scanner to skip the
#: one family this module redacts.
_KEY_PREFIX = "sk-" + "ant-" + "api03-"
CREDENTIAL_SHAPED = _KEY_PREFIX + "NOT" + "A" * 8 + "REALKEY" + "N" * 8 + "FIXTUREONLY"
CREDENTIAL_SHAPED_ALT = _KEY_PREFIX + "Z" * 16


def _credentialed_session():
    return _Session(
        [
            _call("exec_command", {"cmd": f"python publish.py --api-key {CREDENTIAL_SHAPED} --dry-run"}, "c1"),
            _result("exec_command", "c1", exit_code=127, text="command not found: python"),
            _call("exec_command", {"cmd": f"python3 publish.py --api-key {CREDENTIAL_SHAPED} --dry-run"}, "c2"),
            _result("exec_command", "c2", exit_code=0, text="published"),
        ]
    )


def test_a_credential_in_the_command_never_reaches_the_edge():
    """The edge copies a shell command into ``metadata["anchor"]`` and into the
    human-readable evidence. Those become bytes in ``graph.json``, the markdown
    projection and any exported site, so they pass the same credential rules
    ingest applies to a turn — this producer is not exempt from them because it
    is downstream of one that applies them."""
    edges = _recovers(_credentialed_session())
    assert len(edges) == 1
    blob = json.dumps(edges[0].model_dump(), ensure_ascii=False)
    assert CREDENTIAL_SHAPED not in blob
    assert "sk-ant" not in blob
    assert "[REDACTED]" in blob
    # and the rest of the operand survives, so the edge still says something
    assert "publish.py" in blob


def test_the_credential_rules_are_shared_rather_than_private_to_ingest():
    """A private copy in the ingest layer is unreachable from the graph layer,
    which is exactly how this producer came to publish a raw command."""
    from tesserae.harness_sessions import _turn_text

    # An Anthropic key is ``sk-ant-<version>-<blob>``; the hyphens break the
    # ``sk-[A-Za-z0-9]{12,}`` rule after three characters, and ``--api-key``
    # separated by a space is not the ``key=value`` the generic rule wants. The
    # single most likely credential on a machine running this codebase passed
    # ingest untouched, which is what made the edge's omission reachable.
    assert CREDENTIAL_SHAPED not in _turn_text(f'{{"cmd": "deploy --api-key {CREDENTIAL_SHAPED}"}}')

    from tesserae.redaction import redact_secrets

    assert CREDENTIAL_SHAPED not in redact_secrets(f"deploy --api-key {CREDENTIAL_SHAPED}")
    # Each half of that carries its own weight: a vendor-prefixed key with no
    # flag in front of it...
    assert CREDENTIAL_SHAPED not in redact_secrets(f"deploy {CREDENTIAL_SHAPED}")
    # ...and a flag value that is not vendor-prefixed at all. The value is
    # deliberately self-describing rather than hex: an 18-character hex string
    # here is what GitGuardian's "Generic CLI Option Secret" detector fires on,
    # and it did — this exact line failed PR #128 while four rewrites of the
    # sk-ant fixture above chased the wrong string entirely. The rule under test
    # is `--(api-key|token|secret|password)[=\s]+\S+`, which matches any
    # non-space value, so nothing is lost by making the value say what it is.
    _flag_value = "not-a-real-token-fixture-only"
    assert _flag_value not in redact_secrets(f"deploy --token {_flag_value}")


def test_a_credential_straddling_the_display_cap_does_not_survive_as_a_fragment():
    """Redaction runs BEFORE truncation, exactly as it does at ingest. Cut
    first and the tail of a key becomes a fragment short enough to slip under
    every pattern's length floor — and a fragment of a live key is a live
    key's prefix, published."""
    from tesserae.session_recovery import _ANCHOR_DISPLAY_LIMIT

    # Positioned so the cut lands ten characters into the key: enough of it to
    # publish, too little for any pattern's length floor to recognise.
    padding = "x" * (_ANCHOR_DISPLAY_LIMIT - 25)
    command = f"python run.py --pad {padding} {CREDENTIAL_SHAPED}"
    session = _Session(
        [
            _call("exec_command", {"cmd": command}, "c1"),
            _result("exec_command", "c1", exit_code=127),
            _call("exec_command", {"cmd": command.replace("python", "python3", 1)}, "c2"),
            _result("exec_command", "c2", exit_code=0),
        ]
    )
    edges = _recovers(session)
    assert len(edges) == 1
    blob = json.dumps(edges[0].model_dump(), ensure_ascii=False)
    assert "sk-ant" not in blob


def test_two_calls_differing_only_in_their_credential_are_not_one_anchor():
    """...and the redaction happens at PUBLICATION, not in the key. Redacting
    before keying would give every credentialed call one shared ``[REDACTED]``
    anchor — the truncation collapse, rebuilt: unrelated work silently joined
    into a single operand."""
    session = _Session(
        [
            _call("exec_command", {"cmd": f"python deploy.py --api-key {CREDENTIAL_SHAPED}"}, "c1"),
            _result("exec_command", "c1", exit_code=1, text="denied"),
            _call("exec_command", {"cmd": f"python deploy.py --api-key {CREDENTIAL_SHAPED_ALT}"}, "c2"),
            _result("exec_command", "c2", exit_code=0, text="ok"),
        ]
    )
    assert _recovers(session) == []


def test_a_recovery_may_be_separated_by_unrelated_work():
    """The genuine gap-4 pair from ``codex:019f0936``: the fix arrives after a
    couple of unrelated calls, and it is still the FIRST success on that
    operand."""
    session = _Session(
        [
            _call("exec_command", {"cmd": f'python -c "{EXPR}"'}, "c1"),
            _result("exec_command", "c1", exit_code=127, text="command not found"),
            _call("exec_command", {"cmd": "ls -la"}, "c2"),
            _result("exec_command", "c2", exit_code=0),
            _call("exec_command", {"cmd": f'.venv/bin/python -c "{EXPR}"'}, "c3"),
            _result("exec_command", "c3", exit_code=0),
        ]
    )
    edges = _recovers(session)
    assert len(edges) == 1
    assert edges[0].metadata["gap"] == 2


def test_a_file_tool_that_errors_then_reports_success_on_the_same_file():
    session = _Session(
        [
            _call("Edit", {"file_path": "/tmp/x.py", "old_string": "a", "new_string": "b"}, "t1"),
            _result("Edit", "t1", is_error=True, text="String to replace not found"),
            _call("Edit", {"file_path": "/tmp/x.py", "old_string": "a  ", "new_string": "b"}, "t2"),
            _result("Edit", "t2", is_error=False, text="edited"),
        ],
        session_id="claude-code:s2",
    )
    edges = _recovers(session)
    assert len(edges) == 1
    assert "file" in edges[0].metadata["basis"]


# ---------------------------------------------------------------------------
# What a recovery IS NOT — the load-bearing refusals
# ---------------------------------------------------------------------------


def test_unrelated_command_on_the_same_program_is_not_a_recovery():
    """THE false positive, quoted from ``codex:019f0936-533d-7001-9155-48fc089d7741``
    results #33 -> #36. A pytest run failed; it never passed in that session.
    Three results later an unrelated one-liner run by the same interpreter
    exited 0. Under a program-name key that is a strict, gap-2, no-intervening
    -success pair, and the graph would publish 'the failing test run was
    recovered'. It was not."""
    session = _Session(
        [
            _call(
                "exec_command",
                {"cmd": ".venv/bin/python -m pytest -p no:cacheprovider tests/test_federation_eval.py"},
                "c1",
            ),
            _result("exec_command", "c1", exit_code=1, text="1 failed"),
            _call("exec_command", {"cmd": f'.venv/bin/python -c "{EXPR}"'}, "c2"),
            _result("exec_command", "c2", exit_code=0, text="ok"),
        ]
    )
    assert _recovers(session) == []


def test_a_different_query_to_the_same_search_tool_is_not_a_recovery():
    """``grep -rn "max_turn" src`` exits 1, then a DIFFERENT grep exits 0.
    Same tool, same program, unrelated question."""
    session = _Session(
        [
            _call("Bash", {"command": 'grep -rn "max_turn" src --include="*.py" -l'}, "t1"),
            _result("Bash", "t1", is_error=True, text="Error"),
            _call("Bash", {"command": 'grep -n "text" tesserae/harness_sessions.py'}, "t2"),
            _result("Bash", "t2", is_error=False, text="hits"),
        ],
        session_id="claude-code:s3",
    )
    assert _recovers(session) == []


def test_a_read_of_a_file_does_not_recover_a_run_of_it():
    """THE collision that comes from deleting the program token. ``cat foo.py``
    and ``python foo.py`` reduce to the same argv, so an operand-only key makes
    a *read* of a file "recover" a *run* of it — and the edge reads as evidence
    that the script was fixed. The program is reduced to its family, never
    dropped."""
    session = _Session(
        [
            _call("exec_command", {"cmd": "python scripts/build.py --release"}, "c1"),
            _result("exec_command", "c1", exit_code=1, text="Traceback"),
            _call("exec_command", {"cmd": "cat scripts/build.py --release"}, "c2"),
            _result("exec_command", "c2", exit_code=0, text="<the source>"),
        ]
    )
    assert _recovers(session) == []


def test_the_same_program_reached_by_a_different_path_is_still_one_program():
    """The RECALL guard on the fix above, and the reason the program becomes a
    family rather than a literal: ``python``, ``python3``, ``python3.12`` and
    ``.venv/bin/python`` are one program launched four ways, and that difference
    is 9 of the 9 edges the corpus yields. This one passes before the fix too —
    it is here to fail if the key is ever tightened to the exact program token,
    which is the plausible over-correction."""
    for fixed in ("python3", "python3.12", ".venv/bin/python", "/usr/bin/python3"):
        session = _Session(
            [
                _call("exec_command", {"cmd": f'python -c "{EXPR}"'}, "c1"),
                _result("exec_command", "c1", exit_code=127),
                _call("exec_command", {"cmd": f'{fixed} -c "{EXPR}"'}, "c2"),
                _result("exec_command", "c2", exit_code=0),
            ]
        )
        assert len(_recovers(session)) == 1, fixed


def test_the_same_argv_in_a_different_working_directory_is_not_a_recovery():
    """``workdir`` is on 1,223 of Codex's 1,225 shell invocations and was
    ignored. ``-m pytest tests/x.py`` failing in one checkout and passing in
    another is two different files, and the edge would say the test was fixed."""
    session = _Session(
        [
            _call("exec_command", {"cmd": "python -m pytest tests/x.py", "workdir": "/a"}, "c1"),
            _result("exec_command", "c1", exit_code=1, text="1 failed"),
            _call("exec_command", {"cmd": "python -m pytest tests/x.py", "workdir": "/b"}, "c2"),
            _result("exec_command", "c2", exit_code=0, text="1 passed"),
        ]
    )
    assert _recovers(session) == []


def test_a_reported_working_directory_does_not_match_an_unreported_one():
    """"Nowhere reported" is not a directory. Claude's ``Bash`` sends no
    workdir at all, so treating a missing one as a wildcard would let every
    Claude call match every Codex call on the same argv."""
    session = _Session(
        [
            _call("exec_command", {"cmd": "python -m pytest tests/x.py"}, "c1"),
            _result("exec_command", "c1", exit_code=1, text="1 failed"),
            _call("exec_command", {"cmd": "python -m pytest tests/x.py", "workdir": "/a"}, "c2"),
            _result("exec_command", "c2", exit_code=0, text="1 passed"),
        ]
    )
    assert _recovers(session) == []


def test_a_search_scope_is_not_the_operand():
    """The file dimension's twin of the program collision. ``Grep`` takes
    ``pattern`` as what was asked for and ``path`` as the directory to look in;
    keying on ``path`` makes one failed search "recovered" by an unrelated
    successful search of the same directory. 34 of the corpus's 35 ``path``
    invocations are exactly this."""
    session = _Session(
        [
            _call("Grep", {"pattern": "max_turns", "path": "tesserae"}, "t1"),
            _result("Grep", "t1", is_error=True, text="Error"),
            _call("Grep", {"pattern": "call_id", "path": "tesserae"}, "t2"),
            _result("Grep", "t2", is_error=False, text="hits"),
        ],
        session_id="claude-code:s8",
    )
    assert _recovers(session) == []


def test_a_search_that_found_nothing_is_not_a_failure_even_with_no_exit_code():
    """The guard that could not fire. Claude reports NO exit code on any result,
    ever — so a search-tool exemption nested inside ``isinstance(exit_code, int)``
    is structurally unable to fire on a Claude transcript, on the harness that
    runs the most greps. It fires 4 times on the measured corpus once it can."""
    session = _Session(
        [
            _call("Bash", {"command": 'grep -rn "call_id" tesserae/session_event.py'}, "t1"),
            _result("Bash", "t1", is_error=True, text=""),
            _call("Bash", {"command": 'grep -rn "call_id" tesserae/session_event.py'}, "t2"),
            _result("Bash", "t2", is_error=False, text="12: call_id"),
        ],
        session_id="claude-code:s9",
    )
    assert _recovers(session) == []


def test_the_tool_name_alone_is_never_an_anchor():
    """1,233 of Codex's 1,286 results are one tool name, ``exec_command``. If
    the tool name were the anchor, 'a shell command failed and later some shell
    command succeeded' would mint a causal edge."""
    session = _Session(
        [
            _call("exec_command", {"cmd": "cargo build"}, "c1"),
            _result("exec_command", "c1", exit_code=101, text="error"),
            _call("exec_command", {"cmd": "git status --short"}, "c2"),
            _result("exec_command", "c2", exit_code=0),
        ]
    )
    assert _recovers(session) == []


def test_an_unreported_result_is_not_a_success():
    """Step 5's rule, carried into the causal layer: the absence of a failure
    signal is not success. 90 of Claude's 95 Edit results are ``unreported``."""
    session = _Session(
        [
            _call("Read", {"file_path": "/tmp/x.py"}, "t1"),
            _result("Read", "t1", is_error=True, text="File does not exist"),
            _call("Read", {"file_path": "/tmp/x.py"}, "t2"),
            _result("Read", "t2", text="contents"),  # no is_error at all
        ],
        session_id="claude-code:s4",
    )
    assert _recovers(session) == []


def test_an_unreported_result_does_not_swallow_the_pending_failure():
    """...and it must not clear the anchor either: a later REAL success on the
    same operand is still the recovery."""
    session = _Session(
        [
            _call("Read", {"file_path": "/tmp/x.py"}, "t1"),
            _result("Read", "t1", is_error=True, text="File does not exist"),
            _call("Read", {"file_path": "/tmp/x.py"}, "t2"),
            _result("Read", "t2", text="contents"),
            _call("Read", {"file_path": "/tmp/x.py"}, "t3"),
            _result("Read", "t3", is_error=False, text="contents"),
        ],
        session_id="claude-code:s5",
    )
    edges = _recovers(session)
    assert len(edges) == 1
    assert edges[0].metadata["failure_turn_id"] == 1


def test_only_the_first_success_recovers_a_failure():
    """No intervening success — measured to discard 90.4% of naive candidates."""
    session = _Session(
        [
            _call("exec_command", {"cmd": f'python -c "{EXPR}"'}, "c1"),
            _result("exec_command", "c1", exit_code=127),
            _call("exec_command", {"cmd": f'python3 -c "{EXPR}"'}, "c2"),
            _result("exec_command", "c2", exit_code=0),
            _call("exec_command", {"cmd": f'python3 -c "{EXPR}"'}, "c3"),
            _result("exec_command", "c3", exit_code=0),
        ]
    )
    edges = _recovers(session)
    assert len(edges) == 1
    assert edges[0].metadata["recovery_turn_id"] == 3


def test_a_search_exit_code_is_not_a_failure_of_intent():
    """``rg`` exits 1 on 'no match' and 2 on 'a path argument did not exist'
    while still doing its job — 10 of the corpus's 94 raw failures. A false
    anchor manufactures a false recovery downstream."""
    session = _Session(
        [
            _call("exec_command", {"cmd": 'rg -n "pytest" pyproject.toml setup.cfg'}, "c1"),
            _result("exec_command", "c1", exit_code=2, text="rg: setup.cfg: No such file or directory"),
            _call("exec_command", {"cmd": 'rg -n "pytest" pyproject.toml setup.cfg'}, "c2"),
            _result("exec_command", "c2", exit_code=0, text="hit"),
        ]
    )
    assert _recovers(session) == []


def test_a_killed_process_is_not_a_failed_command():
    """Exit 143 is SIGTERM — the harness's 10-minute timeout, not a command
    that failed. 128+signal is 'it was killed', and killing is not evidence
    that the next thing fixed anything."""
    session = _Session(
        [
            _call("exec_command", {"cmd": "sleep 900 && echo done"}, "c1"),
            _result("exec_command", "c1", exit_code=143, text=""),
            _call("exec_command", {"cmd": "sleep 900 && echo done"}, "c2"),
            _result("exec_command", "c2", exit_code=0, text="done"),
        ]
    )
    assert _recovers(session) == []


def test_a_bare_program_with_no_operand_is_not_an_anchor():
    session = _Session(
        [
            _call("exec_command", {"cmd": "make"}, "c1"),
            _result("exec_command", "c1", exit_code=2),
            _call("exec_command", {"cmd": "make"}, "c2"),
            _result("exec_command", "c2", exit_code=0),
        ]
    )
    assert _recovers(session) == []


def test_the_same_operand_under_a_different_tool_is_not_a_recovery():
    """The anchor is tool-scoped. Two tools that happen to take the same
    argument shape share no operand namespace — 'Bash failed on X, some other
    tool succeeded on X' is co-occurrence with extra steps."""
    session = _Session(
        [
            _call("Bash", {"command": "pytest tests/x.py"}, "t1"),
            _result("Bash", "t1", is_error=True, text="1 failed"),
            _call("ctx_execute", {"command": "pytest tests/x.py"}, "t2"),
            _result("ctx_execute", "t2", is_error=False, text="ok"),
        ],
        session_id="claude-code:s7",
    )
    assert _recovers(session) == []


def test_turns_that_disagree_about_which_tool_ran_anchor_nothing():
    """The operand comes from the invocation and the outcome from the result.
    If the two disagree about what was run, the join is unsound and nothing is
    claimed — a consumer of somebody else's records does not get to pick which
    half to believe."""
    session = _Session(
        [
            _call("exec_command", {"cmd": f'python -c "{EXPR}"'}, "c1"),
            _result("shell", "c1", exit_code=127),
            _call("exec_command", {"cmd": f'python3 -c "{EXPR}"'}, "c2"),
            _result("shell", "c2", exit_code=0),
        ]
    )
    assert _recovers(session) == []


def test_a_recovery_far_away_in_the_session_is_not_a_recovery():
    """'No intervening success' is necessary and not sufficient. Under a looser
    key the corpus contains a pair 201 results apart, in a different release
    cycle. The bound excluded nothing at the shipped key (max observed gap 5),
    and it is what stops a session-spanning coincidence."""
    from tesserae.session_recovery import _MAX_RECOVERY_GAP

    filler = []
    for i in range(_MAX_RECOVERY_GAP + 2):
        filler.append(_call("exec_command", {"cmd": f"echo {i}"}, f"f{i}"))
        filler.append(_result("exec_command", f"f{i}", exit_code=0))
    session = _Session(
        [
            _call("exec_command", {"cmd": f'python -c "{EXPR}"'}, "c1"),
            _result("exec_command", "c1", exit_code=127),
            *filler,
            _call("exec_command", {"cmd": f'python3 -c "{EXPR}"'}, "c2"),
            _result("exec_command", "c2", exit_code=0),
        ]
    )
    edges = _recovers(session)
    assert edges == []
    # ...and one result closer, it is.
    near = _Session(
        [
            _call("exec_command", {"cmd": f'python -c "{EXPR}"'}, "c1"),
            _result("exec_command", "c1", exit_code=127),
            *filler[: 2 * (_MAX_RECOVERY_GAP - 1)],
            _call("exec_command", {"cmd": f'python3 -c "{EXPR}"'}, "c2"),
            _result("exec_command", "c2", exit_code=0),
        ]
    )
    assert len(_recovers(near)) == 1


def test_two_unparseable_invocations_do_not_collide_into_one_anchor():
    """130 of 1,044 Claude invocations are truncated mid-JSON at the 1,200-char
    cap. 'Unparseable' must mean NO key — never an empty key shared by every
    other unparseable call, which would silently join unrelated work."""
    truncated_a = {"role": "tool", "timestamp": "t", "name": "Bash", "call_id": "t1",
                   "text": '{"command": "ruff check --fix --unsafe-fixes tesse'}
    truncated_b = {"role": "tool", "timestamp": "t", "name": "Bash", "call_id": "t2",
                   "text": '{"command": "git commit -m \\"a very long messa'}
    session = _Session(
        [
            truncated_a,
            _result("Bash", "t1", is_error=True, text="failed"),
            truncated_b,
            _result("Bash", "t2", is_error=False, text="ok"),
        ],
        session_id="claude-code:s6",
    )
    assert _recovers(session) == []


def test_a_recovery_never_crosses_a_session():
    a = _Session(
        [
            _call("exec_command", {"cmd": f'python -c "{EXPR}"'}, "c1"),
            _result("exec_command", "c1", exit_code=127),
        ],
        session_id="codex:a",
    )
    b = _Session(
        [
            _call("exec_command", {"cmd": f'python3 -c "{EXPR}"'}, "c1"),
            _result("exec_command", "c1", exit_code=0),
        ],
        session_id="codex:b",
    )
    assert _recovers(a) == []
    assert _recovers(b) == []


# ---------------------------------------------------------------------------
# The batching trap — Codex issues a whole batch of calls, then the outputs
# ---------------------------------------------------------------------------


def test_batched_calls_are_matched_by_call_id_not_by_adjacency():
    """746 of 1,286 Codex results (58%) were issued while another call was
    outstanding. 'The result after the invocation' is wrong for the majority of
    them; only the harness's own call id is right. Here the failure is the FIRST
    result of a batch and its fix is issued in the NEXT batch, so a positional
    pairing would read the operand of ``git status`` off the python failure."""
    session = _Session(
        [
            _call("exec_command", {"cmd": f'python -c "{EXPR}"'}, "cA"),
            _call("exec_command", {"cmd": "git status --short"}, "cB"),
            _result("exec_command", "cA", exit_code=127, text="command not found"),
            _result("exec_command", "cB", exit_code=0),
            _call("exec_command", {"cmd": f'python3 -c "{EXPR}"'}, "cC"),
            _call("exec_command", {"cmd": "git diff --stat"}, "cD"),
            _result("exec_command", "cD", exit_code=0),
            _result("exec_command", "cC", exit_code=0),
        ]
    )
    edges = _recovers(session)
    assert len(edges) == 1
    assert edges[0].metadata["failure_turn_id"] == 2
    assert edges[0].metadata["recovery_turn_id"] == 7


def test_a_call_already_in_flight_when_the_failure_landed_is_not_a_recovery():
    """Codex emits a whole batch of calls and then the whole batch of outputs,
    up to 5 deep, and 58% of its results were issued while another call was
    outstanding. A call issued BEFORE the failure came back cannot be a response
    to it — the agent had not seen the failure yet — however neatly the two
    results line up afterwards. The call id says when each was issued; nothing
    else in the record does."""
    session = _Session(
        [
            _call("exec_command", {"cmd": f'python -c "{EXPR}"'}, "cA"),
            _call("exec_command", {"cmd": f'python3 -c "{EXPR}"'}, "cB"),
            _result("exec_command", "cA", exit_code=127, text="command not found"),
            _result("exec_command", "cB", exit_code=0),
        ]
    )
    assert _recovers(session) == []


def test_the_edge_points_at_the_last_failure_the_recovering_call_could_have_seen():
    """Three attempts, the third works. The edge must name the attempt the fix
    followed, not the first one in the session — 'this succeeded after that
    failed' is a claim about the attempt immediately before it, and the gap is
    read as how long the agent was stuck."""
    session = _Session(
        [
            _call("exec_command", {"cmd": f'python -c "{EXPR}"'}, "c1"),
            _result("exec_command", "c1", exit_code=127),
            _call("exec_command", {"cmd": f'python2 -c "{EXPR}"'}, "c2"),
            _result("exec_command", "c2", exit_code=127),
            _call("exec_command", {"cmd": f'python3 -c "{EXPR}"'}, "c3"),
            _result("exec_command", "c3", exit_code=0),
        ]
    )
    edges = _recovers(session)
    assert len(edges) == 1
    assert edges[0].metadata["failure_turn_id"] == 3
    assert edges[0].metadata["gap"] == 1


def test_a_long_retry_loop_is_not_lost_to_the_gap_bound():
    """Measuring the gap from the FIRST failure throws away the case the bound
    was never meant to catch: an agent that retried the same thing more times
    than the bound allows and then fixed it. The success is one result after the
    attempt it followed; only an accounting choice made it look 50 apart."""
    from tesserae.session_recovery import _MAX_RECOVERY_GAP

    turns = []
    for i in range(_MAX_RECOVERY_GAP + 2):
        turns.append(_call("exec_command", {"cmd": f'python -c "{EXPR}"'}, f"f{i}"))
        turns.append(_result("exec_command", f"f{i}", exit_code=127))
    turns.append(_call("exec_command", {"cmd": f'python3 -c "{EXPR}"'}, "ok"))
    turns.append(_result("exec_command", "ok", exit_code=0))
    edges = _recovers(_Session(turns))
    assert len(edges) == 1
    assert edges[0].metadata["gap"] == 1
    assert edges[0].metadata["failure_turn_id"] == len(turns) - 3


def test_a_batch_whose_ids_would_mislead_a_positional_pairing():
    """Same batch, reversed output order: a positional pairing would attribute
    ``git status`` to the python failure and mint an edge from nothing."""
    session = _Session(
        [
            _call("exec_command", {"cmd": "git status --short"}, "cB"),
            _call("exec_command", {"cmd": f'python -c "{EXPR}"'}, "cA"),
            _result("exec_command", "cA", exit_code=127, text="command not found"),
            _result("exec_command", "cB", exit_code=0),
        ]
    )
    assert _recovers(session) == []


def test_a_result_with_no_call_id_yields_no_anchor():
    """Sessions imported before the call id was carried get no causal edges,
    rather than edges derived from a guessed pairing."""
    session = _Session(
        [
            {"role": "tool", "timestamp": "t", "name": "exec_command",
             "text": json.dumps({"cmd": f'python -c "{EXPR}"'})},
            {"role": "tool_result", "timestamp": "t", "name": "exec_command",
             "text": "command not found", "exit_code": 127},
            {"role": "tool", "timestamp": "t", "name": "exec_command",
             "text": json.dumps({"cmd": f'python3 -c "{EXPR}"'})},
            {"role": "tool_result", "timestamp": "t", "name": "exec_command",
             "text": "ok", "exit_code": 0},
        ]
    )
    assert _recovers(session) == []


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_detection_is_byte_idempotent_and_takes_no_client():
    session = _interpreter_recovery_session()
    first = [e.model_dump() for e in _recovers(session)]
    second = [e.model_dump() for e in _recovers(session)]
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert first  # and it actually found something


def test_no_home_directory_survives_into_a_recovery_edge():
    session = _Session(
        [
            _call("exec_command", {"cmd": "python /Users/neo/Developer/x.py --flag"}, "c1"),
            _result("exec_command", "c1", exit_code=127),
            _call("exec_command", {"cmd": "python3 /Users/neo/Developer/x.py --flag"}, "c2"),
            _result("exec_command", "c2", exit_code=0),
        ]
    )
    edges = _recovers(session)
    assert len(edges) == 1
    blob = json.dumps(edges[0].model_dump(), ensure_ascii=False)
    assert "/Users/neo" not in blob
    assert "~/Developer/x.py" in blob


def test_an_empty_or_eventless_session_yields_nothing():
    assert detect_recoveries(None, []) == []
    assert detect_recoveries(_Session([]), []) == []


# ---------------------------------------------------------------------------
# Producer: the harness's own call id survives ingest
# ---------------------------------------------------------------------------


def test_a_claude_tool_result_turn_carries_the_tool_use_id():
    from tesserae.harness_sessions import _claude_turns

    rows = [
        {
            "type": "assistant",
            "timestamp": "2026-01-01T00:00:00Z",
            "message": {"content": [{"type": "tool_use", "id": "toolu_1", "name": "Bash",
                                     "input": {"command": "ls"}}]},
        },
        {
            "type": "user",
            "timestamp": "2026-01-01T00:00:01Z",
            "message": {"content": [{"type": "tool_result", "tool_use_id": "toolu_1",
                                     "content": "out", "is_error": False}]},
        },
    ]
    turns = _claude_turns(rows)
    assert [t["call_id"] for t in turns] == ["toolu_1", "toolu_1"]


def test_a_codex_tool_result_turn_carries_the_call_id():
    from tesserae.harness_sessions import _codex_turns

    rows = [
        {"timestamp": "t", "payload": {"type": "function_call", "call_id": "call_1",
                                       "name": "exec_command", "arguments": '{"cmd": "ls"}'}},
        {"timestamp": "t", "payload": {"type": "function_call_output", "call_id": "call_1",
                                       "output": "Process exited with code 0\n\nok"}},
    ]
    turns = _codex_turns(rows)
    assert [t["call_id"] for t in turns] == ["call_1", "call_1"]
