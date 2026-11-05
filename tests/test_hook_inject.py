"""Prompt-time context injection (roadmap R8): ``tesserae.hook_inject`` and
``hooks/prompt-inject.sh``.

The contract under test: lexical-only, read-only on the SQLite mirror,
floor-filtered, budgeted, deduped per session, never reads transcripts,
fail-open (rc 0, empty stdout) on ANY error with the error logged, and off
unless explicitly enabled.
"""

from __future__ import annotations

import builtins
import hashlib
import io
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tesserae import hook_inject
from tesserae.graph_stores.sqlite import SqliteGraphStore
from tesserae.research_graph import ResearchEdge, ResearchNode, ResearchNodeType

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOK = REPO_ROOT / "hooks" / "prompt-inject.sh"

D_LOCK = "SessionDecision:s1:lock"
D_OLD = "SessionTakeaway:s1:old-release"
D_NEW = "SessionTakeaway:s2:new-release"
F_HOOK = "SessionFailure:s1:hook"
PAPER = "Paper:compile-lock"


def _node(node_id, node_type, name):
    return ResearchNode(id=node_id, name=name, type=node_type, description="")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    tdir = root / ".tesserae"
    tdir.mkdir(parents=True)
    # graph.json first, mirror second — the order a compile publishes them in.
    (tdir / "graph.json").write_text('{"nodes": [], "edges": []}\n', encoding="utf-8")
    store = SqliteGraphStore(tdir / "sqlite.db")
    store.upsert_many_nodes([
        _node(D_LOCK, ResearchNodeType.SESSION_DECISION,
              "Compile takes an exclusive per-project compile lock so concurrent refreshes cannot stack."),
        _node(D_OLD, ResearchNodeType.SESSION_TAKEAWAY,
              "Release v0.25 published to PyPI and npm after green CI."),
        _node(D_NEW, ResearchNodeType.SESSION_TAKEAWAY,
              "Release v0.28 published to PyPI and npm after artifact verification."),
        _node(F_HOOK, ResearchNodeType.SESSION_FAILURE,
              "Editing session-end.sh broke the detach ladder on macOS."),
        # Matches the lock prompt lexically but is not an injectable type.
        _node(PAPER, ResearchNodeType.PAPER, "Compile lock contention in exclusive concurrent refreshes"),
        # Unrelated findings, so no term above is in half the candidate pool.
        *[_node(f"SessionDecision:s9:filler{i}", ResearchNodeType.SESSION_DECISION, text) for i, text in enumerate([
            "Vault sync writes Obsidian pages atomically.",
            "The site build vendors three.js locally.",
            "Charter routing names nothing when unsure.",
            "Graphiti export emits one episode per document.",
        ])],
    ])
    store.upsert_many_edges([ResearchEdge(source=D_NEW, target=D_OLD, type="supersedes")])
    later = time.time() + 5
    os.utime(tdir / "sqlite.db", (later, later))
    return root


def _prompt(text, session_id=None, **extra):
    event = {"hook_event_name": "UserPromptSubmit", "prompt": text, **extra}
    if session_id:
        event["session_id"] = session_id
    return event


_REAL_SPAWN = {"test_cold_pool_starts_one_detached_build"}


@pytest.fixture(autouse=True)
def inline_pool_build(request, monkeypatch):
    """Cold pools build synchronously in tests, not in a detached process.

    The cold event itself still injects nothing — that is the behaviour — but
    the next one finds the cache. Tests in ``_REAL_SPAWN`` opt out.
    """
    if request.node.name in _REAL_SPAWN:
        return

    def build(project_root, cache_dir):
        hook_inject.load_pool(project_root, cache_dir, build=True)
        return 0

    monkeypatch.setattr(hook_inject, "spawn_pool_build", build)


def _warm(project: Path, state_dir: Path) -> None:
    hook_inject.load_pool(project, state_dir, build=True)


def _call(project: Path, event, tmp_path: Path, *extra_args, warm=True):
    """Run the module entry point in-process; return (rc, stdout, log text)."""
    if warm and (project / ".tesserae" / "sqlite.db").exists():
        try:
            _warm(project, tmp_path / "seen")
        except RuntimeError:  # a deliberately stale mirror: the call must refuse it
            pass
    log = tmp_path / "hook.log"
    args = ["--project", str(project), "--state-dir", str(tmp_path / "seen"),
            "--log-file", str(log), *extra_args]
    stdin = io.StringIO(event if isinstance(event, str) else json.dumps(event))
    out = io.StringIO()
    parser = hook_inject.add_arguments(__import__("argparse").ArgumentParser())
    real_stdout = sys.stdout
    sys.stdout = out
    try:
        rc = hook_inject.run_args(parser.parse_args(args), stdin=stdin)
    finally:
        sys.stdout = real_stdout
    return rc, out.getvalue(), log.read_text(encoding="utf-8") if log.exists() else ""


def _context(stdout: str) -> str:
    payload = json.loads(stdout)
    return payload["hookSpecificOutput"]["additionalContext"]


# ---------------------------------------------------------------- query ---


def test_query_from_prompt_and_tool_events():
    assert hook_inject.query_from_event(_prompt("why the compile lock")) == "why the compile lock"
    read = {"hook_event_name": "PreToolUse", "tool_name": "Read",
            "tool_input": {"file_path": "/r/hooks/session-end.sh"}}
    terms = hook_inject.query_from_event(read).split()
    assert "sessionend" in terms and "session-end.sh" in terms and "hooks" not in terms
    grep = {"hook_event_name": "PreToolUse", "tool_name": "Grep",
            "tool_input": {"pattern": "compile_lock", "path": "tesserae"}}
    assert "compile_lock" in hook_inject.query_from_event(grep)
    bash = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "ls"}}
    assert hook_inject.query_from_event(bash) == ""
    assert hook_inject.file_anchors(read) == ["session-end.sh"]
    # A directory is not a file name: anchoring on it would match everything.
    assert hook_inject.file_anchors(grep) == []
    # Only PreToolUse is a tool event here.
    post = dict(grep, hook_event_name="PostToolUse")
    assert hook_inject.query_from_event(post) == "" and hook_inject.file_anchors(post) == []


# ------------------------------------------------------------ retrieval ---


def test_prompt_injects_matching_decision_behind_cost_header(project, tmp_path):
    rc, out, log = _call(project, _prompt("why does compile take an exclusive lock?"), tmp_path)
    assert rc == 0 and log == ""
    payload = json.loads(out)
    assert payload["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    text = payload["hookSpecificOutput"]["additionalContext"]
    header, *rest = text.splitlines()
    assert header.startswith("[tesserae inject · ") and "no LLM" in header
    assert D_LOCK in text
    assert PAPER not in text  # not an injectable type, however well it matches


def test_superseded_hit_is_a_warning_pointing_at_its_replacement(project, tmp_path):
    rc, out, _ = _call(project, _prompt("release published to pypi npm"), tmp_path)
    text = _context(out)
    warning = next(line for line in text.splitlines() if D_OLD in line)
    assert warning.startswith("- ⚠ superseded takeaway:")
    assert "→ now: Release v0.28" in warning
    # Warnings render before active items.
    lines = [line for line in text.splitlines() if line.startswith("- ")]
    assert lines[0] == warning


def test_one_shared_word_is_below_the_relevance_floor(project, tmp_path):
    rc, out, _ = _call(project, _prompt("please explain the weather and compile"), tmp_path)
    assert rc == 0 and out == ""


def test_tool_event_needs_two_terms_or_an_exact_file_mention(project, tmp_path):
    named = {"hook_event_name": "PreToolUse", "tool_name": "Edit",
             "tool_input": {"file_path": str(project / "hooks" / "session-end.sh")}}
    rc, out, _ = _call(project, named, tmp_path)
    text = _context(out)
    assert F_HOOK in text
    assert json.loads(out)["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    # One-word stem, no node names the file: strict floor refuses "compile" alone.
    bare = {"hook_event_name": "PreToolUse", "tool_name": "Read",
            "tool_input": {"file_path": str(project / "compile.py")}}
    rc, out, _ = _call(project, bare, tmp_path)
    assert rc == 0 and out == ""


def test_budget_drops_whole_items_never_cuts_one(project, tmp_path):
    rc, out, _ = _call(project, _prompt("release published to pypi npm"), tmp_path,
                       "--budget-tokens", "40")
    text = _context(out)
    body = [line for line in text.splitlines() if line.startswith("- ")]
    assert len(body) == 1 and body[0].endswith("]")
    rc, out, _ = _call(project, _prompt("release published to pypi npm"), tmp_path,
                       "--budget-tokens", "1")
    assert rc == 0 and out == ""


def test_per_session_dedupe(project, tmp_path):
    prompt = "why does compile take an exclusive lock?"
    _, first, _ = _call(project, _prompt(prompt, session_id="A"), tmp_path)
    _, again, _ = _call(project, _prompt(prompt, session_id="A"), tmp_path)
    _, other, _ = _call(project, _prompt(prompt, session_id="B"), tmp_path)
    assert D_LOCK in _context(first)
    assert again == ""
    assert D_LOCK in _context(other)


def test_pool_cache_is_rebuilt_when_a_compile_republishes_the_graph(project, tmp_path):
    tdir = project / ".tesserae"
    prompt = _prompt("vacuum the session chunks database")
    rc, out, _ = _call(project, prompt, tmp_path, warm=False)
    assert out == ""  # cold: nothing injected, build requested
    rc, out, _ = _call(project, prompt, tmp_path, warm=False)
    assert out == "" and list((tmp_path / "seen").glob("*.pool.json"))
    # A "compile": new finding in the mirror, graph.json rewritten, mirror after it.
    SqliteGraphStore(tdir / "sqlite.db").upsert_node(_node(
        "SessionDecision:s3:vacuum", ResearchNodeType.SESSION_DECISION,
        "Vacuum the session chunks database after pruning; sqlite does not shrink on delete."))
    (tdir / "graph.json").write_text('{"nodes": [], "edges": [] }\n', encoding="utf-8")
    later = time.time() + 10
    os.utime(tdir / "sqlite.db", (later, later))
    rc, out, log = _call(project, prompt, tmp_path, warm=False)
    assert out == "" and "pool cache cold" in log  # the old pool is not served
    rc, out, _ = _call(project, prompt, tmp_path, warm=False)
    assert "SessionDecision:s3:vacuum" in _context(out)


def test_cold_pool_starts_one_detached_build(project, tmp_path):
    state = tmp_path / "seen"
    first = hook_inject.spawn_pool_build(project, state)
    second = hook_inject.spawn_pool_build(project, state)
    assert isinstance(first, int) and second is None  # the lock holds a burst to one builder
    lock = next(state.glob("*.building"))
    deadline = time.time() + 20
    while lock.exists() and time.time() < deadline:
        time.sleep(0.05)
    assert not lock.exists(), "builder never released its lock"
    rc, out, _ = _call(project, _prompt("compile exclusive lock"), tmp_path, warm=False)
    assert D_LOCK in _context(out)


def test_never_writes_the_mirror_and_never_opens_the_transcript(project, tmp_path, monkeypatch):
    db = project / ".tesserae" / "sqlite.db"
    before = hashlib.sha256(db.read_bytes()).hexdigest(), db.stat().st_mtime
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text('{"type":"user"}\n', encoding="utf-8")
    opened = []
    real_open = builtins.open

    def spy(file, *args, **kwargs):
        opened.append(str(file))
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", spy)
    monkeypatch.setattr(io, "open", spy)
    rc, out, _ = _call(project, _prompt("compile exclusive lock", transcript_path=str(transcript)), tmp_path)
    monkeypatch.undo()
    assert D_LOCK in _context(out)
    assert str(transcript) not in opened
    assert (hashlib.sha256(db.read_bytes()).hexdigest(), db.stat().st_mtime) == before


def test_open_readonly_refuses_writes_and_missing_files(project, tmp_path):
    store = SqliteGraphStore.open_readonly(project / ".tesserae" / "sqlite.db")
    with pytest.raises(sqlite3.OperationalError):
        store.upsert_node(_node("SessionDecision:x", ResearchNodeType.SESSION_DECISION, "x"))
    with pytest.raises(FileNotFoundError):
        SqliteGraphStore.open_readonly(tmp_path / "nope.db")
    assert not (tmp_path / "nope.db").exists()


# ------------------------------------------------------------ fail-open ---


@pytest.mark.parametrize("breakage, expect", [
    ("stale", "newer than"),
    ("missing", "no SQLite mirror"),
    ("garbage", "JSONDecodeError"),
])
def test_errors_fail_open_and_are_logged(project, tmp_path, breakage, expect):
    tdir = project / ".tesserae"
    event = _prompt("compile exclusive lock")
    if breakage == "stale":
        later = time.time() + 60
        os.utime(tdir / "graph.json", (later, later))
    elif breakage == "missing":
        (tdir / "sqlite.db").unlink()
    else:
        event = "{not json"
    rc, out, log = _call(project, event, tmp_path)
    assert rc == 0 and out == ""
    assert expect in log and "injected nothing" in log


def test_deadline_fails_open_and_logs_timeout(project, tmp_path, monkeypatch):
    def slow(*args, **kwargs):
        time.sleep(2)
        return []

    monkeypatch.setattr(hook_inject, "retrieve", slow)
    started = time.perf_counter()
    rc, out, log = _call(project, _prompt("compile exclusive lock"), tmp_path, "--timeout-ms", "50")
    assert time.perf_counter() - started < 1.0
    assert rc == 0 and out == "" and "timeout after 50 ms" in log


def test_cli_route_matches_module(project, tmp_path, monkeypatch, capsys):
    from tesserae.cli import main

    _warm(project, tmp_path / "s")
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(_prompt("compile exclusive lock"))))
    rc = main(["hook", "inject", "--project", str(project), "--state-dir", str(tmp_path / "s"),
               "--log-file", str(tmp_path / "l.log")])
    assert rc == 0 and D_LOCK in _context(capsys.readouterr().out)


# --------------------------------------------------------- self-capture ---


def test_injected_context_never_becomes_session_turn_text():
    """Claude Code records hook additionalContext as an ``attachment`` row.

    Session import must not turn it into turn text, or every injected
    decision would be re-extracted from the next session as a new one.
    """
    from tesserae.harness_sessions import _claude_turns

    injected = "[tesserae inject · 1 item]\n<tesserae-context>\n- decision: X [SessionDecision:a]\n</tesserae-context>"
    rows = [
        {"type": "user", "timestamp": "2026-11-05T00:00:00Z",
         "message": {"role": "user", "content": "why is compile locked"}},
        {"type": "attachment", "timestamp": "2026-11-05T00:00:01Z",
         "attachment": {"type": "hook_additional_context", "hookEvent": "UserPromptSubmit",
                        "hookName": "UserPromptSubmit", "content": [injected]}},
    ]
    turns = _claude_turns(rows)
    assert any("why is compile locked" in t["text"] for t in turns)
    assert not any("tesserae-context" in str(t.get("text")) for t in turns)


def test_tesserae_own_claude_calls_carry_the_internal_marker(monkeypatch):
    """Both ``claude -p`` spawners mark the child env, so an opted-in hook
    firing inside a compile's extraction call can tell it is not the user's."""
    import tesserae.llm_json as lj
    from tesserae import llm_extractor

    assert hook_inject.INTERNAL_LLM_ENV == lj.INTERNAL_LLM_ENV
    monkeypatch.delenv(lj.INTERNAL_LLM_ENV, raising=False)
    seen = []

    def fake_cli(cmd, prompt=None, env=None, timeout=None):
        seen.append(env)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok")

    monkeypatch.setattr(lj, "_run_cli", fake_cli)
    assert lj.ClaudeCLIJsonClient(config_dirs=["/tmp/fake-claude"]).complete_text(system="s", user="u") == "ok"

    def fake_run(cmd, **kwargs):
        seen.append(kwargs["env"])
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="{}")

    monkeypatch.setattr(subprocess, "run", fake_run)
    llm_extractor.run_claude_cli("p", "/tmp/fake-claude", "sonnet", 5)
    assert [env.get(lj.INTERNAL_LLM_ENV) for env in seen] == ["1", "1"]


def test_internal_llm_call_never_injects(project, tmp_path, monkeypatch):
    monkeypatch.setenv(hook_inject.INTERNAL_LLM_ENV, "1")
    rc, out, log = _call(project, _prompt("why does compile take an exclusive lock?"), tmp_path)
    assert rc == 0 and out == "" and log == ""


# ----------------------------------------------------------- shell hook ---


def _hook_env(tmp_path: Path, stub: str, **env):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    launcher = bin_dir / "tesserae"
    launcher.write_text(stub, encoding="utf-8")
    launcher.chmod(0o755)
    base = dict(os.environ)
    for key in ("TESSERAE_HOOK_INJECT", "TESSERAE_HOOK_INJECT_TIMEOUT_MS"):
        base.pop(key, None)
    base["PATH"] = f"{bin_dir}:{base.get('PATH', '')}"
    base["PYTHONPATH"] = str(REPO_ROOT)
    base["HOME"] = str(tmp_path / "home")
    base["TMPDIR"] = str(tmp_path)
    base.update(env)
    return base


def _run_hook(project: Path, env, event, warm=True):
    if warm:
        _warm(project, Path(env["TMPDIR"]) / "tesserae-hook-inject")
    return subprocess.run(["bash", str(HOOK)], input=json.dumps(event), capture_output=True,
                          text=True, cwd=project, env=env, timeout=10)


PY_STUB = f"#!{sys.executable}\nraise SystemExit('fast path not taken')\n"


def test_shell_hook_is_off_by_default(project, tmp_path):
    proc = _run_hook(project, _hook_env(tmp_path, PY_STUB), _prompt("compile exclusive lock"))
    assert proc.returncode == 0 and proc.stdout == ""
    assert not (project / ".tesserae" / hook_inject.LOG_NAME).exists()


@pytest.mark.parametrize("switch", ["env", "settings"])
def test_shell_hook_injects_when_opted_in(project, tmp_path, switch):
    env = _hook_env(tmp_path, PY_STUB)
    if switch == "env":
        env["TESSERAE_HOOK_INJECT"] = "1"
    else:
        (project / ".claude").mkdir()
        (project / ".claude" / "tesserae.local.md").write_text(
            "---\nhooks:\n  prompt_inject: true\n---\n", encoding="utf-8")
    # A generous watchdog, so a hook that waits on it (e.g. a sleep holding
    # stdout open) shows up as ~5 s instead of passing.
    env["TESSERAE_HOOK_INJECT_TIMEOUT_MS"] = "5000"
    started = time.perf_counter()
    proc = _run_hook(project, env, _prompt("compile exclusive lock"))
    assert time.perf_counter() - started < 3.0, "hook waited on its watchdog"
    assert proc.returncode == 0, proc.stderr
    assert D_LOCK in _context(proc.stdout)


def test_project_files_cannot_shadow_the_injector_modules(project, tmp_path):
    """The hook runs with the user's project as cwd; a ``json.py`` there must
    not be imported in place of the stdlib, by the injector or its builder."""
    marker = tmp_path / "shadowed"
    for name in ("json.py", "hashlib.py"):
        (project / name).write_text(f"open({str(marker)!r}, 'a').write({name!r})\n", encoding="utf-8")
    env = _hook_env(tmp_path, PY_STUB, TESSERAE_HOOK_INJECT="1", TESSERAE_HOOK_INJECT_TIMEOUT_MS="5000")
    proc = _run_hook(project, env, _prompt("compile exclusive lock"))
    assert proc.returncode == 0, proc.stderr
    assert D_LOCK in _context(proc.stdout)
    assert not marker.exists(), marker.read_text()
    # The detached cold-pool builder, started from a process whose cwd is the project.
    state = tmp_path / "builder-state"
    script = ("import sys; from pathlib import Path; from tesserae import hook_inject; "
              f"print(hook_inject.spawn_pool_build(Path('.'), Path({str(state)!r})))")
    subprocess.run([sys.executable, "-I", "-c", script], cwd=project, check=True, timeout=10)
    deadline = time.time() + 20
    while list(state.glob("*.building")) and time.time() < deadline:
        time.sleep(0.05)
    assert list(state.glob("*.pool.json")), "builder wrote no pool"
    assert not marker.exists(), marker.read_text()


def test_injector_imports_a_package_outside_the_interpreters_own_site(project, tmp_path):
    """A ``pip install --user`` tesserae lives in user site-packages, not next
    to its interpreter; ``python -I`` would drop it and every event would fail.
    Stand-in: a bare interpreter that reaches the package only via PYTHONPATH."""
    bare = Path(sys.base_prefix) / "bin" / "python3"
    if not bare.exists():
        pytest.skip("no base interpreter outside the venv")
    stub = f"#!{bare}\nraise SystemExit('fast path not taken')\n"
    env = _hook_env(tmp_path, stub, TESSERAE_HOOK_INJECT="1", TESSERAE_HOOK_INJECT_TIMEOUT_MS="5000")
    proc = _run_hook(project, env, _prompt("compile exclusive lock"))
    assert proc.returncode == 0, proc.stderr
    log = project / ".tesserae" / hook_inject.LOG_NAME
    assert D_LOCK in _context(proc.stdout), log.read_text() if log.exists() else "no log"


def test_launcher_found_through_a_relative_path_entry_still_runs(project, tmp_path):
    """`command -v` returns `bin/tesserae` for a relative PATH entry; the hook
    `cd`s to the launcher's dir before exec, so it must make it absolute first."""
    rel_bin = project / "bin"
    rel_bin.mkdir()
    launcher = rel_bin / "tesserae"
    # Not a python shebang, so the hook execs the launcher itself.
    launcher.write_text(f'#!/usr/bin/env bash\nshift 2\nexec {sys.executable} -m tesserae.hook_inject "$@"\n',
                        encoding="utf-8")
    launcher.chmod(0o755)
    env = _hook_env(tmp_path, PY_STUB, TESSERAE_HOOK_INJECT="1", TESSERAE_HOOK_INJECT_TIMEOUT_MS="5000")
    env["PATH"] = "bin:" + env["PATH"]
    proc = _run_hook(project, env, _prompt("compile exclusive lock"))
    log = project / ".tesserae" / hook_inject.LOG_NAME
    assert proc.stdout, log.read_text() if log.exists() else "no log"
    assert D_LOCK in _context(proc.stdout)


def test_shell_hook_is_a_noop_inside_tesserae_own_llm_call(project, tmp_path):
    env = _hook_env(tmp_path, PY_STUB, TESSERAE_HOOK_INJECT="1", TESSERAE_INTERNAL_LLM="1")
    proc = _run_hook(project, env, _prompt("compile exclusive lock"))
    assert proc.returncode == 0 and proc.stdout == ""
    assert not (project / ".tesserae" / hook_inject.LOG_NAME).exists()


def test_shell_hook_watchdog_kills_a_hung_injector(project, tmp_path):
    # Not a python shebang → the slow `tesserae hook inject` path, which hangs.
    stub = "#!/usr/bin/env bash\nexec sleep 5\n"
    env = _hook_env(tmp_path, stub, TESSERAE_HOOK_INJECT="1", TESSERAE_HOOK_INJECT_TIMEOUT_MS="200")
    started = time.perf_counter()
    proc = _run_hook(project, env, _prompt("compile exclusive lock"))
    assert time.perf_counter() - started < 2.0
    assert proc.returncode == 0 and proc.stdout == ""
    log = (project / ".tesserae" / hook_inject.LOG_NAME).read_text(encoding="utf-8")
    assert "rc=143" in log
