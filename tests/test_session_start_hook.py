"""Tests for the SessionStart hook.

The hook lives at hooks/session-start.sh and runs as a real bash
script under Claude Code. These tests invoke it directly with a
synthetic project_root + a stubbed ``tesserae`` binary on PATH.

The hook used to background ``tesserae code sync`` when a CodeGraph
SQLite outran the derived code-graph.json; source code left Tesserae's
scope, so what remains to test is that the hook prints its summary and
shells out to nothing at all.
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
    can assert whether the hook shelled out to the CLI or not.
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


def test_session_start_reports_the_graph_and_spawns_nothing(fake_project):
    """SessionStart is a pure read, and a CodeGraph store must not change that.

    The sync-code branch that used to live here backgrounded a CLI on every
    session start; with source code out of scope there is nothing to sync, so
    a project carrying a .codegraph/ store must still produce a summary line
    and zero subprocesses.
    """
    proj = fake_project["project"]
    (proj / ".codegraph").mkdir()
    (proj / ".codegraph" / "codegraph.db").write_text("fake sqlite\n", encoding="utf-8")

    result = _run_hook(fake_project["env"], proj)
    assert result.returncode == 0
    # Counts come from jq when it is installed and "?" when it is not, so
    # assert the shape of the line rather than the numbers.
    assert result.stdout.startswith("tesserae: "), result.stdout
    assert "nodes" in result.stdout and "edges" in result.stdout, result.stdout
    assert "code-graph" not in result.stdout, result.stdout

    # The old spawn was backgrounded, so give it the time it would have needed
    # to record itself before concluding it never happened.
    time.sleep(0.5)
    assert not fake_project["invocation_log"].exists(), (
        fake_project["invocation_log"].read_text(encoding="utf-8")
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
    # ``bash``, not ``zsh``: _lib.sh documents itself as POSIX-friendly so hooks
    # run "under either bash or the user's shell of choice", and the rest of this
    # suite already drives it with bash/sh. Only these two call sites asked for
    # zsh, which is absent on the CI runners — so five tests here passed on macOS
    # and failed everywhere else, testing the developer's shell rather than the
    # library. bash is present wherever the hooks are.
    r = subprocess.run(
        ["bash", "-c", f'source "{HOOKS}/_lib.sh"; resolve_project_root'],
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
        ["bash", "-c", f'source "{HOOKS}/_lib.sh"; hook_autocompile_enabled && echo on || echo off'],
        capture_output=True, text=True, env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
    )
    assert r.stdout.strip() == "off"
