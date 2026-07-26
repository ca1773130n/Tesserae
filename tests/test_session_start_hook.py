"""Tests for the SessionStart hook's live sync-code path.

The hook lives at hooks/session-start.sh and runs as a real bash
script under Claude Code. These tests invoke it directly with a
synthetic project_root + a stubbed ``tesserae`` binary on PATH, so
we can observe the four interesting branches:

1. CodeGraph DB is newer than code-graph.json → sync-code backgrounded.
2. code-graph.json is newer → no sync.
3. .codegraph/ missing → silent skip.
4. ``sync_code_on_start: false`` in tesserae.local.md → silent skip.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
HOOK_PATH = REPO_ROOT / "hooks" / "session-start.sh"


@pytest.fixture
def fake_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Build a fake project_root with .tesserae/ + a stubbed tesserae binary.

    The stub records every invocation to ``$invocation_log`` so tests
    can assert whether the hook fired sync-code or not.
    """
    project = tmp_path / "proj"
    project.mkdir()
    (project / ".tesserae").mkdir()
    # Minimal graph.json so the hook's pre-check doesn't short-circuit.
    (project / ".tesserae" / "graph.json").write_text(
        '{"nodes": [], "edges": []}\n', encoding="utf-8"
    )

    # Stubbed tesserae binary — records every invocation.
    invocation_log = tmp_path / "invocations.log"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "tesserae"
    stub.write_text(
        f"""#!/usr/bin/env bash
echo "stubbed tesserae called with: $*" >> {invocation_log}
exit 0
""",
        encoding="utf-8",
    )
    stub.chmod(0o755)

    # Run the hook from inside the project_root (the hook resolves
    # project_root from $PWD) and with bin_dir prepended to PATH so
    # ``find_tesserae`` picks up the stub.
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    env["PWD"] = str(project)
    # Disable session_start so the rest of the hook (graph summary)
    # doesn't print noise; keep sync_code_on_start on its default
    # (true) for the live-sync branch.
    # Wait — the sync-code block runs UNCONDITIONALLY at the end,
    # regardless of session_start. Leave session_start on so the
    # graph counts get computed.
    yield {
        "project": project,
        "invocation_log": invocation_log,
        "env": env,
    }


def _run_hook(env: dict, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(HOOK_PATH)],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(cwd),
        timeout=10,
    )


def _wait_for_invocation(log: Path, timeout: float = 15.0) -> bool:
    """Sync-code is backgrounded; poll for the stub to record.

    The poll returns as soon as the log lands, so the generous ceiling only
    slows genuine failures — 3.0s was missed under full-suite load (the
    backgrounded fork+exec of the stub can take seconds on a saturated box).
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if log.exists() and log.read_text(encoding="utf-8").strip():
            return True
        time.sleep(0.05)
    return False


def test_sync_code_triggered_when_db_is_newer(fake_project):
    proj = fake_project["project"]
    codegraph_dir = proj / ".codegraph"
    codegraph_dir.mkdir()
    db = codegraph_dir / "codegraph.db"
    code_graph_json = proj / ".tesserae" / "code-graph.json"

    code_graph_json.write_text("{}\n", encoding="utf-8")
    # Backdate the json by 60s so the db is unambiguously newer.
    old = time.time() - 60
    os.utime(code_graph_json, (old, old))
    db.write_text("fake sqlite\n", encoding="utf-8")

    result = _run_hook(fake_project["env"], proj)
    assert result.returncode == 0
    assert "syncing code-graph" in result.stdout, result.stdout

    assert _wait_for_invocation(fake_project["invocation_log"]), (
        "stubbed tesserae was never called within the timeout"
    )
    log_content = fake_project["invocation_log"].read_text(encoding="utf-8")
    assert "code sync" in log_content, log_content


def test_sync_code_skipped_when_json_is_fresh(fake_project):
    proj = fake_project["project"]
    codegraph_dir = proj / ".codegraph"
    codegraph_dir.mkdir()
    db = codegraph_dir / "codegraph.db"
    code_graph_json = proj / ".tesserae" / "code-graph.json"

    db.write_text("fake sqlite\n", encoding="utf-8")
    # Backdate the db so the json is newer.
    old = time.time() - 60
    os.utime(db, (old, old))
    code_graph_json.write_text("{}\n", encoding="utf-8")

    result = _run_hook(fake_project["env"], proj)
    assert result.returncode == 0
    assert "syncing code-graph" not in result.stdout, result.stdout

    # Give backgrounded code time to (not) fire.
    time.sleep(0.5)
    assert not fake_project["invocation_log"].exists() or (
        not fake_project["invocation_log"].read_text(encoding="utf-8").strip()
    )


def test_sync_code_silent_when_codegraph_dir_missing(fake_project):
    proj = fake_project["project"]
    # No .codegraph/ at all.

    result = _run_hook(fake_project["env"], proj)
    assert result.returncode == 0
    assert "syncing code-graph" not in result.stdout, result.stdout
    time.sleep(0.3)
    assert not fake_project["invocation_log"].exists()


def test_sync_code_silent_when_opted_out(fake_project):
    proj = fake_project["project"]
    codegraph_dir = proj / ".codegraph"
    codegraph_dir.mkdir()
    db = codegraph_dir / "codegraph.db"
    code_graph_json = proj / ".tesserae" / "code-graph.json"

    code_graph_json.write_text("{}\n", encoding="utf-8")
    old = time.time() - 60
    os.utime(code_graph_json, (old, old))
    db.write_text("fake sqlite\n", encoding="utf-8")

    claude_dir = proj / ".claude"
    claude_dir.mkdir()
    (claude_dir / "tesserae.local.md").write_text(
        "---\nhooks:\n  sync_code_on_start: false\n---\n\nopt-out\n",
        encoding="utf-8",
    )

    result = _run_hook(fake_project["env"], proj)
    assert result.returncode == 0
    assert "syncing code-graph" not in result.stdout, result.stdout
    time.sleep(0.3)
    assert not fake_project["invocation_log"].exists()


def test_sync_code_triggered_when_json_missing(fake_project):
    """If code-graph.json doesn't exist yet, any DB triggers initial sync."""
    proj = fake_project["project"]
    codegraph_dir = proj / ".codegraph"
    codegraph_dir.mkdir()
    db = codegraph_dir / "codegraph.db"
    db.write_text("fake sqlite\n", encoding="utf-8")
    # No code-graph.json yet.

    result = _run_hook(fake_project["env"], proj)
    assert result.returncode == 0
    assert "syncing code-graph" in result.stdout, result.stdout
    assert _wait_for_invocation(fake_project["invocation_log"]), (
        "stubbed tesserae was never called for initial sync"
    )


def test_sync_code_passes_project_when_run_from_subdir(fake_project):
    """codex PR #11 P2 fix: backgrounded CLI must receive --project so it
    doesn't fall back to CWD when Claude opens a session in a subdir.
    """
    proj = fake_project["project"]
    codegraph_dir = proj / ".codegraph"
    codegraph_dir.mkdir()
    db = codegraph_dir / "codegraph.db"
    code_graph_json = proj / ".tesserae" / "code-graph.json"

    code_graph_json.write_text("{}\n", encoding="utf-8")
    old = time.time() - 60
    os.utime(code_graph_json, (old, old))
    db.write_text("fake sqlite\n", encoding="utf-8")

    # Invoke the hook from a SUBDIRECTORY of the project.
    subdir = proj / "tesserae" / "memory"
    subdir.mkdir(parents=True)
    env = dict(fake_project["env"])
    env["PWD"] = str(subdir)

    result = _run_hook(env, subdir)
    assert result.returncode == 0
    assert "syncing code-graph" in result.stdout, result.stdout
    assert _wait_for_invocation(fake_project["invocation_log"])
    log_content = fake_project["invocation_log"].read_text(encoding="utf-8")
    # Must contain --project pointing at the actual project root, not the subdir.
    assert "--project" in log_content, log_content
    assert str(proj) in log_content, (
        f"expected --project {proj} in invocation; got: {log_content}"
    )


# ---------------------------------------------------------------------------
# The $HOME trap. A knowledge base at ~/.tesserae made $HOME look like a project
# root, so a session started anywhere outside a registered project resolved
# there and the PostToolUse hook backgrounded a compile over the whole home
# directory — 15k files, ~10h of LLM spend, from a detached process that
# outlived its session.
# ---------------------------------------------------------------------------

import subprocess
from pathlib import Path

HOOKS = Path(__file__).resolve().parents[1] / "hooks"


def _resolve(cwd, home):
    r = subprocess.run(
        ["zsh", "-c", f'source "{HOOKS}/_lib.sh"; resolve_project_root'],
        cwd=str(cwd), capture_output=True, text=True, env={"HOME": str(home), "PATH": "/usr/bin:/bin"},
    )
    return r.stdout.strip(), r.returncode


def test_resolve_project_root_refuses_home(tmp_path):
    home = tmp_path / "home"
    (home / ".tesserae").mkdir(parents=True)
    out, rc = _resolve(home, home)
    assert out == "", f"$HOME resolved as a project root: {out!r}"
    assert rc == 1


def test_resolve_project_root_refuses_home_from_a_subdirectory(tmp_path):
    """The walk-up is the actual attack path: cwd is under $HOME, not $HOME."""
    home = tmp_path / "home"
    (home / ".tesserae").mkdir(parents=True)
    sub = home / "Documents" / "scratch"
    sub.mkdir(parents=True)
    out, rc = _resolve(sub, home)
    assert out == "", f"a subdirectory of $HOME resolved to it: {out!r}"
    assert rc == 1


def test_resolve_project_root_still_finds_a_real_project(tmp_path):
    home = tmp_path / "home"
    (home / ".tesserae").mkdir(parents=True)
    proj = home / "code" / "myproj"
    (proj / ".tesserae").mkdir(parents=True)
    src = proj / "src"
    src.mkdir()
    out, rc = _resolve(src, home)
    assert out == str(proj) and rc == 0


def test_no_project_returns_empty_not_cwd(tmp_path):
    """The old fallback echoed $PWD, so callers' `-d $root/.tesserae` test
    passed for any cwd under $HOME. Empty makes them no-op instead of guess."""
    home = tmp_path / "home"
    home.mkdir()
    loose = home / "elsewhere"
    loose.mkdir()
    out, rc = _resolve(loose, home)
    assert out == "" and rc == 1


def test_money_spending_hooks_are_off_by_default(tmp_path):
    """A hook that backgrounds LLM work must be switched ON deliberately."""
    for name in ("posttooluse-edit.sh", "session-end.sh"):
        src = (HOOKS / name).read_text(encoding="utf-8")
        assert "hook_autocompile_enabled || exit 0" in src, name
    r = subprocess.run(
        ["zsh", "-c", f'source "{HOOKS}/_lib.sh"; hook_autocompile_enabled && echo on || echo off'],
        capture_output=True, text=True, env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
    )
    assert r.stdout.strip() == "off"
