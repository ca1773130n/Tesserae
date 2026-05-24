"""Tests for the PostToolUse(Edit|Write|MultiEdit) sync-code hook.

The hook lives at hooks/posttooluse-sync-code.sh and runs as a real
bash script under Claude Code. These tests invoke it directly with a
synthetic project_root + a stubbed ``tesserae`` binary on PATH so we
can observe the interesting branches:

1. CodeGraph DB present, no prior sync touch-file → triggered.
2. CodeGraph DB present, touch-file <30s old → debounced.
3. CodeGraph DB present, touch-file >30s old → triggered.
4. .codegraph/ missing → silent skip, no touch-file.
5. ``sync_code_on_edit: false`` in tesserae.local.md → silent skip.
6. Another ``tesserae project sync-code <project_root>`` running →
   skipped by the pgrep re-entry guard.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
HOOK_PATH = REPO_ROOT / "hooks" / "posttooluse-sync-code.sh"


@pytest.fixture
def fake_project(tmp_path: Path):
    """Build a fake project_root with .tesserae/, .codegraph/, stub binary."""
    project = tmp_path / "proj"
    project.mkdir()
    (project / ".tesserae").mkdir()

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

    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    env["PWD"] = str(project)
    yield {
        "project": project,
        "invocation_log": invocation_log,
        "env": env,
        "bin_dir": bin_dir,
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


def _make_codegraph_db(project: Path) -> Path:
    codegraph_dir = project / ".codegraph"
    codegraph_dir.mkdir(exist_ok=True)
    db = codegraph_dir / "codegraph.db"
    db.write_text("fake sqlite\n", encoding="utf-8")
    return db


def test_triggered_when_no_prior_sync(fake_project):
    """First edit on a CodeGraph project → sync-code fires + touch-file written."""
    proj = fake_project["project"]
    _make_codegraph_db(proj)
    touch = proj / ".tesserae" / ".last-sync-code"
    assert not touch.exists()

    result = _run_hook(fake_project["env"], proj)
    assert result.returncode == 0, result.stderr
    # Silent hook — no stdout.
    assert result.stdout == "", result.stdout

    assert _wait_for_invocation(fake_project["invocation_log"]), (
        "stubbed tesserae was never called within timeout"
    )
    log_content = fake_project["invocation_log"].read_text(encoding="utf-8")
    assert "project sync-code" in log_content, log_content
    assert "--project" in log_content, log_content
    assert str(proj) in log_content, log_content

    # Touch-file must be written so the next edit gets debounced.
    assert touch.exists(), "debounce touch-file was not written"


def test_debounced_when_recent_sync(fake_project):
    """Touch-file <30s old → skip the sync."""
    proj = fake_project["project"]
    _make_codegraph_db(proj)
    touch = proj / ".tesserae" / ".last-sync-code"
    touch.touch()
    # Backdate by 5s — well within the 30s debounce window.
    recent = time.time() - 5
    os.utime(touch, (recent, recent))

    result = _run_hook(fake_project["env"], proj)
    assert result.returncode == 0
    assert result.stdout == ""

    # Give backgrounded code time to (not) fire.
    time.sleep(0.5)
    assert not fake_project["invocation_log"].exists() or (
        not fake_project["invocation_log"].read_text(encoding="utf-8").strip()
    )


def test_triggered_when_debounce_window_elapsed(fake_project):
    """Touch-file >30s old → sync re-fires."""
    proj = fake_project["project"]
    _make_codegraph_db(proj)
    touch = proj / ".tesserae" / ".last-sync-code"
    touch.touch()
    # Backdate by 120s — well past the 30s window.
    stale = time.time() - 120
    os.utime(touch, (stale, stale))

    result = _run_hook(fake_project["env"], proj)
    assert result.returncode == 0
    assert result.stdout == ""

    assert _wait_for_invocation(fake_project["invocation_log"]), (
        "stubbed tesserae was never called after debounce expired"
    )
    log_content = fake_project["invocation_log"].read_text(encoding="utf-8")
    assert "project sync-code" in log_content

    # Touch-file should be refreshed.
    new_mtime = touch.stat().st_mtime
    assert new_mtime > stale + 30, "touch-file mtime was not refreshed"


def test_silent_when_codegraph_dir_missing(fake_project):
    """No .codegraph/ → silent skip, no touch-file created."""
    proj = fake_project["project"]
    touch = proj / ".tesserae" / ".last-sync-code"

    result = _run_hook(fake_project["env"], proj)
    assert result.returncode == 0
    assert result.stdout == ""

    time.sleep(0.3)
    assert not fake_project["invocation_log"].exists()
    assert not touch.exists(), (
        "touch-file must not be created for non-CodeGraph projects"
    )


def test_silent_when_opted_out(fake_project):
    """sync_code_on_edit: false → silent skip even with fresh DB."""
    proj = fake_project["project"]
    _make_codegraph_db(proj)
    touch = proj / ".tesserae" / ".last-sync-code"

    claude_dir = proj / ".claude"
    claude_dir.mkdir()
    (claude_dir / "tesserae.local.md").write_text(
        "---\nhooks:\n  sync_code_on_edit: false\n---\n\nopt-out\n",
        encoding="utf-8",
    )

    result = _run_hook(fake_project["env"], proj)
    assert result.returncode == 0
    assert result.stdout == ""

    time.sleep(0.3)
    assert not fake_project["invocation_log"].exists()
    assert not touch.exists(), (
        "opt-out must not produce a touch-file"
    )


def test_silent_when_opted_in_explicitly(fake_project):
    """sync_code_on_edit: true (explicit) → sync fires, mirrors default."""
    proj = fake_project["project"]
    _make_codegraph_db(proj)

    claude_dir = proj / ".claude"
    claude_dir.mkdir()
    (claude_dir / "tesserae.local.md").write_text(
        "---\nhooks:\n  sync_code_on_edit: true\n---\n\nopt-in\n",
        encoding="utf-8",
    )

    result = _run_hook(fake_project["env"], proj)
    assert result.returncode == 0
    assert _wait_for_invocation(fake_project["invocation_log"])


def test_skipped_when_concurrent_sync_running(fake_project, tmp_path):
    """pgrep re-entry guard skips when another sync-code is already running."""
    proj = fake_project["project"]
    _make_codegraph_db(proj)
    touch = proj / ".tesserae" / ".last-sync-code"

    # Spawn a long-sleep process whose argv contains the magic string
    # the hook's pgrep is looking for:
    #   tesserae project sync-code .* ${project_root}
    # We use ``sh -c`` with a fake argv0 + long sleep so pgrep -f finds
    # the full command line.
    fake_cmd = f"tesserae project sync-code --project {proj} && sleep 30"
    bg = subprocess.Popen(
        ["sh", "-c", f"exec -a 'tesserae project sync-code --project {proj}' sleep 30"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        # Give the kernel a moment to register the new argv in ps.
        time.sleep(0.2)

        # Sanity-check that pgrep -f can actually see our fake.
        probe = subprocess.run(
            ["pgrep", "-f", f"tesserae project sync-code.*{proj}"],
            capture_output=True,
            text=True,
        )
        if probe.returncode != 0:
            pytest.skip(
                "pgrep -f cannot match exec -a argv on this platform; "
                "skipping concurrent-guard test"
            )

        result = _run_hook(fake_project["env"], proj)
        assert result.returncode == 0
        assert result.stdout == ""

        time.sleep(0.4)
        # No invocation should have been recorded.
        if fake_project["invocation_log"].exists():
            assert fake_project["invocation_log"].read_text(encoding="utf-8").strip() == "", (
                "sync-code should have been skipped by the pgrep guard"
            )
        # Touch-file must NOT be written when we skipped.
        assert not touch.exists(), (
            "touch-file must not be written when concurrent sync skipped"
        )
    finally:
        try:
            bg.send_signal(signal.SIGTERM)
            bg.wait(timeout=2)
        except Exception:
            bg.kill()
