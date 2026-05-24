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


def _wait_for_invocation(log: Path, timeout: float = 3.0) -> bool:
    """Sync-code is backgrounded; poll briefly for the stub to record."""
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
    assert "project sync-code" in log_content, log_content


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
