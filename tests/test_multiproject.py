"""`compile --all` / `refresh --all` — one operation over every registered project.

The three properties that make a batch worth running, and each one is a defect
this file exists to prevent regressing:

* one project failing does not stop the others,
* a project whose compile lock is held is not reported as a failure,
* the exit code distinguishes "something broke" from "come back later".
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tesserae.locking import CompileLockHeldError
from tesserae.multiproject import (
    FAILED,
    LOCKED,
    OK,
    ProjectOutcome,
    exit_code_for,
    render_outcomes,
    resolve_projects,
    run_across_projects,
)


def _registry(tmp_path: Path, monkeypatch, names) -> dict:
    """Register `names`, each as a real project root with a compiled graph."""
    import tesserae.mcp_server as mcp

    registry_path = tmp_path / "registry.json"
    projects = {}
    for name in names:
        root = tmp_path / name
        (root / ".tesserae").mkdir(parents=True)
        (root / ".tesserae" / "graph.json").write_text(
            json.dumps({"nodes": [], "edges": []}), encoding="utf-8"
        )
        projects[name] = {
            "root": str(root),
            "graph_path": str(root / ".tesserae" / "graph.json"),
        }
    registry_path.write_text(
        json.dumps({"version": 1, "projects": projects}), encoding="utf-8"
    )
    monkeypatch.setattr(mcp, "DEFAULT_REGISTRY_PATH", registry_path)
    return projects


# ---------------------------------------------------------------------------
# Target resolution
# ---------------------------------------------------------------------------


def test_resolve_defaults_to_every_registered_project(tmp_path, monkeypatch):
    _registry(tmp_path, monkeypatch, ["alpha", "beta", "gamma"])
    assert [n for n, _ in resolve_projects()] == ["alpha", "beta", "gamma"]


def test_resolve_limits_to_named_subset(tmp_path, monkeypatch):
    _registry(tmp_path, monkeypatch, ["alpha", "beta", "gamma"])
    assert [n for n, _ in resolve_projects(["gamma", "alpha"])] == ["alpha", "gamma"]


def test_an_unknown_name_errors_rather_than_meaning_nothing(tmp_path, monkeypatch):
    """A typo silently selecting zero projects would report a clean, green run
    that did no work at all — the worst possible outcome for a batch."""
    _registry(tmp_path, monkeypatch, ["alpha"])
    with pytest.raises(ValueError, match="unknown project name"):
        resolve_projects(["alhpa"])


# ---------------------------------------------------------------------------
# Isolation
# ---------------------------------------------------------------------------


def test_one_project_failing_does_not_stop_the_others(tmp_path, monkeypatch):
    _registry(tmp_path, monkeypatch, ["alpha", "beta", "gamma"])
    ran = []

    def work(name, root):
        ran.append(name)
        if name == "beta":
            raise RuntimeError("beta is broken")
        return {"name": name}

    outcomes = run_across_projects(resolve_projects(), work)

    assert ran == ["alpha", "beta", "gamma"], "a failure aborted the batch"
    assert [o.status for o in outcomes] == [OK, FAILED, OK]
    assert "beta is broken" in outcomes[1].detail
    assert exit_code_for(outcomes) == 2


def test_a_held_compile_lock_is_not_a_failure(tmp_path, monkeypatch):
    """A background engine holding the lock means the batch did not get to that
    project — reporting it as an error trains people to ignore the exit code."""
    _registry(tmp_path, monkeypatch, ["alpha", "beta"])

    def work(name, root):
        if name == "beta":
            raise CompileLockHeldError("another compile is running (pid 1 on srv-b)")
        return {}

    outcomes = run_across_projects(resolve_projects(), work)

    assert [o.status for o in outcomes] == [OK, LOCKED]
    assert exit_code_for(outcomes) == 1
    assert "srv-b" in outcomes[1].detail


def test_all_green_exits_zero(tmp_path, monkeypatch):
    _registry(tmp_path, monkeypatch, ["alpha", "beta"])
    outcomes = run_across_projects(resolve_projects(), lambda name, root: {})
    assert exit_code_for(outcomes) == 0


def test_concurrent_jobs_still_run_every_project_and_isolate(tmp_path, monkeypatch):
    _registry(tmp_path, monkeypatch, ["a", "b", "c", "d"])
    ran = []

    def work(name, root):
        ran.append(name)
        if name == "c":
            raise RuntimeError("boom")
        print(f"output from {name}")
        return {}

    outcomes = run_across_projects(resolve_projects(), work, jobs=3)

    assert sorted(ran) == ["a", "b", "c", "d"]
    assert {o.name: o.status for o in outcomes} == {
        "a": OK, "b": OK, "c": FAILED, "d": OK,
    }
    # Above one job each project's stdout is buffered whole, so the report can
    # replay it un-interleaved rather than shredding four progress streams.
    assert outcomes[0].output.strip() == "output from a"


def test_render_names_every_project_and_counts_them():
    outcomes = [
        ProjectOutcome("alpha", Path("/a"), OK),
        ProjectOutcome("beta", Path("/b"), FAILED, "RuntimeError: boom"),
        ProjectOutcome("gamma", Path("/g"), LOCKED, "held"),
    ]
    text = render_outcomes(outcomes)
    for name in ("alpha", "beta", "gamma"):
        assert name in text
    assert "1 ok, 1 locked, 1 failed" in text


def test_empty_registry_renders_an_actionable_message():
    assert "projects register" in render_outcomes([])


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("verb", ["compile", "refresh"])
def test_cli_all_runs_every_project_and_reports(tmp_path, monkeypatch, capsys, verb):
    import tesserae.cli as cli

    _registry(tmp_path, monkeypatch, ["alpha", "beta"])
    seen = []

    def fake_one(args):
        seen.append(Path(args.project).name)
        return 0

    monkeypatch.setattr(
        cli, "_handle_compile_legacy" if verb == "compile" else "_handle_refresh_one", fake_one
    )

    rc = cli.main([verb, "--all"])

    assert rc == 0
    assert sorted(seen) == ["alpha", "beta"]
    out = capsys.readouterr().out
    assert "2 ok, 0 locked, 0 failed" in out


@pytest.mark.parametrize("verb", ["compile", "refresh"])
def test_cli_all_surfaces_a_nonzero_handler_code_as_a_failed_project(
    tmp_path, monkeypatch, capsys, verb
):
    """A handler that exits non-zero must not be swallowed into a green batch."""
    import tesserae.cli as cli

    _registry(tmp_path, monkeypatch, ["alpha", "beta"])

    def fake_one(args):
        return 0 if Path(args.project).name == "alpha" else 2

    monkeypatch.setattr(
        cli, "_handle_compile_legacy" if verb == "compile" else "_handle_refresh_one", fake_one
    )

    rc = cli.main([verb, "--all"])

    assert rc == 2
    assert "1 ok, 0 locked, 1 failed" in capsys.readouterr().out


def test_cli_all_rejects_unknown_name(tmp_path, monkeypatch, capsys):
    import tesserae.cli as cli

    _registry(tmp_path, monkeypatch, ["alpha"])
    assert cli.main(["compile", "--all", "--name", "nope"]) == 2
    assert "unknown project name" in capsys.readouterr().err


def test_cli_all_rejects_adhoc_paths(tmp_path, monkeypatch, capsys):
    """Ad-hoc paths belong to one project; ingesting them into every registered
    project would be surprising and tedious to undo."""
    import tesserae.cli as cli

    _registry(tmp_path, monkeypatch, ["alpha"])
    assert cli.main(["compile", "--all", "notes.md"]) == 2
    assert "cannot be combined with explicit paths" in capsys.readouterr().err


def test_cli_all_with_no_registered_projects_says_so(tmp_path, monkeypatch, capsys):
    import tesserae.cli as cli

    _registry(tmp_path, monkeypatch, [])
    assert cli.main(["refresh", "--all"]) == 1
    assert "no projects registered" in capsys.readouterr().err
