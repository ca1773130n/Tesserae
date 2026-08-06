"""Tesserae must not ingest its OWN compile-time LLM calls as user sessions.

The harness session monitor records every Claude Code / Codex CLI invocation —
including the codex/claude calls Tesserae itself runs during compile (extraction,
synthesis, community summaries, …). Ingesting those is a self-capture feedback
loop: the session DB fills with Tesserae's prompts, drowning real work, and the
next compile "extracts findings" from its own extraction calls.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tesserae.harness_sessions import (
    HarnessSession,
    _TESSERAE_PROMPT_SIGNATURES,
    is_tesserae_internal_session,
)
from tesserae.harness_sessions_db import HarnessSessionsDB


def _session(sid: str, title: str, project_root: str = "/tmp/proj") -> HarnessSession:
    return HarnessSession(
        id=sid, slug=sid, harness="claude-code", agent_label="Claude Code",
        project_name="proj", project_root=project_root,
        started_at="2026-06-14T10:00:00Z", title=title,
    )


# A real coding session never OPENS with one of Tesserae's verbatim system prompts.
_REAL = [
    "Fix the off-by-one in the pagination helper",
    "Review this change for security vulnerabilities.",  # ambiguous → kept (conservative)
    "why is the GPU at 100%",
]
_INTERNAL = [
    "You are an extractor that reads agent/user conversation transcripts and produces JSON",
    "You are summarizing a community of related typed research-graph nodes. Return JSON",
    "You are extracting a typed research intelligence graph for Tesserae.",
    "You are an Tesserae synthesis writer. Your job is to summarize",
    "Summarize the following in 2 sentences as a TL;DR. Return only the summary",
    # JSON-client compile/retrieval prompts added after the codex review:
    "You distill a cluster of related coding/agent session findings into a single note",
    "You write ONE terse extraction-guidance bullet (<= 30 words) from a cluster",
    "You decide whether one research-session finding obsoletes another. Both findings",
    "You arbitrate a contradiction between two research performance claims. One claim",
    "You split a single retrieval question into a short list of focused sub-questions",
]


def test_predicate_flags_internal_keeps_real():
    for t in _INTERNAL:
        assert is_tesserae_internal_session(_session("i", t)) is True, t
    for t in _REAL:
        assert is_tesserae_internal_session(_session("r", t)) is False, t


def test_predicate_checks_first_turns_too():
    s = HarnessSession(
        id="x", slug="x", harness="claude-code", agent_label="Claude Code",
        project_name="proj", project_root="/tmp/proj",
        started_at="2026-06-14T10:00:00Z", title="(untitled)",
        metadata={"turns": [{"role": "user",
            "text": "You are an extractor that reads agent/user conversation transcripts ..."}]},
    )
    assert is_tesserae_internal_session(s) is True


def test_user_quoting_a_prompt_midmessage_is_kept():
    """Conservative anchoring: a REAL session that quotes/reviews one of Tesserae's
    prompts mid-message must NOT be flagged. The signature only matches at the
    START of a blob (after trimming), so embedded quotes are retained."""
    for body in (
        'Please review this constant:\n_PROMPT_SYSTEM = "You are an extractor that '
        'reads agent/user conversation transcripts and produces JSON"',
        "Why does our distill prompt start with 'You distill a cluster of related "
        "coding/agent session findings'? Is that wording too narrow?",
        "Fix the bug where the title is set to 'You are summarizing a community of "
        "related typed research-graph nodes'.",
    ):
        s = HarnessSession(
            id="real", slug="real", harness="claude-code", agent_label="Claude Code",
            project_name="proj", project_root="/tmp/proj",
            started_at="2026-06-14T10:00:00Z", title="Investigate prompt wording",
            metadata={"turns": [{"role": "user", "text": body}]},
        )
        assert is_tesserae_internal_session(s) is False, body[:60]


def _declared_system_prompts() -> list[tuple[str, str]]:
    """Every ``system=`` / ``system_prompt=`` string literal in the package.

    Parsed from the AST rather than matched with a regex. The regex this
    replaced looked for openings of the ``You are|distill|write|decide|
    arbitrate|split`` family — a hand-maintained allowlist of VERBS — and the
    single highest-volume prompt Tesserae issues opens with "You extract".
    So the one guard whose job was to prevent the self-capture loop could not
    see the prompt that re-opened it, and 98.4% of the harvested session store
    turned out to be Tesserae's own extraction calls.

    A prompt is identified by how it is PASSED, not by how it is worded. New
    prompts cannot dodge this by picking a different verb.
    """
    pkg = Path(__file__).resolve().parent.parent / "tesserae"
    sig_file = pkg / "harness_sessions.py"  # excluded: it DEFINES the signatures
    found: list[tuple[str, str]] = []
    for path in sorted(pkg.rglob("*.py")):
        if path == sig_file:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover — a broken file fails elsewhere
            continue

        # Module-level string constants, so `system=_SUMMARY_SYSTEM` resolves.
        # Catching only inline literals missed the most common shape in this
        # codebase by far — a named constant above the call — and three more
        # self-capturing prompts survived the first pass of this fix because of
        # it (activity_summary._SUMMARY_SYSTEM, ask_router's scope prompt, and
        # a second activity-summary variant).
        consts: dict[str, str] = {}
        for node in tree.body:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            value = node.value
            if not (isinstance(value, ast.Constant) and isinstance(value.value, str)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    consts[target.id] = value.value

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for kw in node.keywords or []:
                if kw.arg not in ("system", "system_prompt"):
                    continue
                loc = f"{path.relative_to(pkg)}:{kw.value.lineno}"
                if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                    found.append((loc, kw.value.value))
                elif isinstance(kw.value, ast.Name) and kw.value.id in consts:
                    found.append((loc, consts[kw.value.id]))
    return found


def test_every_system_prompt_is_covered():
    """Anti-drift guard: every Tesserae-owned LLM system prompt must be covered
    by a signature, so a newly added prompt cannot silently re-open the
    self-capture loop."""
    declared = _declared_system_prompts()
    assert declared, "found no system= prompts at all — the scanner is broken, not the code"

    uncovered = [
        (loc, text[:80])
        for loc, text in declared
        if not text.lstrip().startswith(_TESSERAE_PROMPT_SIGNATURES)
    ]
    assert not uncovered, (
        "Tesserae system prompt(s) not covered by _TESSERAE_PROMPT_SIGNATURES "
        "(the self-capture loop would re-open — every compile would file these "
        "calls as if they were the user's own coding sessions): "
        + "; ".join(f"{loc}: {t}" for loc, t in uncovered)
    )


def test_the_extraction_prompt_is_recognised_as_internal():
    """The specific regression: one call per document per compile, and every one
    of them was landing in the session store as a user session."""
    title = "You extract a typed research-intelligence graph as ONE JSON object (nodes + edges)."
    assert is_tesserae_internal_session(_session("x", title)) is True


def test_prune_internal_sessions_removes_only_internal(tmp_path: Path):
    db = HarnessSessionsDB(tmp_path / "sessions.db")
    sessions = (
        [_session(f"int{i}", t) for i, t in enumerate(_INTERNAL)]
        + [_session(f"real{i}", t) for i, t in enumerate(_REAL)]
    )
    for s in sessions:
        db.upsert(s, jsonl_path=f"/tmp/{s.id}.jsonl", last_offset=0)
    assert db.count_sessions() == len(sessions)

    removed = db.prune_internal_sessions()
    assert removed == len(_INTERNAL)
    remaining = db.list_for_project("/tmp/proj")
    assert {s.title for s in remaining} == set(_REAL)
    # idempotent: a second prune removes nothing
    assert db.prune_internal_sessions() == 0


def test_file_store_prune_removes_only_internal_records(tmp_path: Path):
    """The DB prune was not enough: `compile` reads the FILE store, so a store
    cleaned only in sqlite kept feeding the graph Tesserae's own extraction
    calls. Measured on the dogfood repo before this landed: 14,377 of 14,605
    stored records (98.4%) were self-captured."""
    from tesserae.harness_sessions import HarnessSessionStore

    store = HarnessSessionStore(tmp_path / "harness_sessions")
    internal = [_session(f"int{i}", t) for i, t in enumerate(_INTERNAL)]
    extraction = _session(
        "extract",
        "You extract a typed research-intelligence graph as ONE JSON object (nodes + edges).",
    )
    real = [_session(f"real{i}", t) for i, t in enumerate(_REAL)]
    store.write_sessions(internal + [extraction] + real)
    assert len(store.list_sessions()) == len(internal) + 1 + len(real)

    preview = store.prune_internal(dry_run=True)
    assert preview["removed"] == len(internal) + 1
    assert preview["kept"] == len(real)
    assert len(store.list_sessions()) == len(internal) + 1 + len(real), "dry run deleted"

    result = store.prune_internal()
    assert result["removed"] == len(internal) + 1
    assert {s.title for s in store.list_sessions()} == set(_REAL)
    # The markdown page goes with its record — an orphan page would keep the
    # session visible on the site after the record behind it was removed.
    assert not list((tmp_path / "harness_sessions").glob("*/*extract*.md"))
    assert store.prune_internal()["removed"] == 0  # idempotent


def test_file_store_prune_keeps_unreadable_records(tmp_path: Path):
    """Unparseable is not provably internal. Same conservatism as every other
    gate here: what cannot be shown to be ours is not ours to delete."""
    from tesserae.harness_sessions import HarnessSessionStore

    store = HarnessSessionStore(tmp_path / "harness_sessions")
    store.write_sessions([_session("real0", _REAL[0])])
    corrupt = store.root / "claude-code" / "corrupt.json"
    corrupt.write_text("{not json", encoding="utf-8")

    result = store.prune_internal()
    assert result["unreadable"] == 1
    assert result["removed"] == 0
    assert corrupt.exists()
