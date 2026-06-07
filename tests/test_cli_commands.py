"""tests/test_cli_commands.py — new-tree dispatch reaches the OLD handlers."""
from __future__ import annotations

import os

import pytest


@pytest.mark.parametrize(
    "argv, handler",
    [
        (["compile"], "_handle_compile"),
        (["context", "q"], "_handle_context"),
        (["serve"], "_handle_serve"),
        (["status"], "_handle_status"),
        (["refresh"], "_handle_refresh"),
        (["engine", "--once"], "_handle_engine"),
    ],
)
def test_verb_dispatches_to_handler(argv, handler, monkeypatch):
    import tesserae.cli as cli

    called = {}

    def _stub(args):
        called["args"] = args
        return 0

    monkeypatch.setattr(cli, handler, _stub)
    rc = cli.main(argv)
    assert rc == 0
    assert "args" in called


def test_compile_accepts_paths_as_adhoc_ingest(monkeypatch):
    import tesserae.cli as cli

    seen = {}

    def _stub(args):
        seen["paths"] = args.paths
        return 0

    monkeypatch.setattr(cli, "_handle_compile", _stub)
    assert cli.main(["compile", "notes/a.md", "notes/b.md"]) == 0
    assert seen["paths"] == ["notes/a.md", "notes/b.md"]


def test_compile_paths_route_ingest_only(monkeypatch):
    """Non-empty paths must hit the ingest-only path, NOT a full compile."""
    import tesserae.cli as cli

    seen = {}

    def _ingest(args):
        seen["paths"] = args.paths
        return 0

    def _legacy(args):
        seen["legacy"] = True
        return 0

    monkeypatch.setattr(cli, "_handle_compile_paths_ingest", _ingest)
    monkeypatch.setattr(cli, "_handle_compile_legacy", _legacy)
    assert cli.main(["compile", "notes/a.md"]) == 0
    assert seen.get("paths") == ["notes/a.md"]
    assert "legacy" not in seen


def test_compile_no_paths_runs_full_compile(monkeypatch):
    import tesserae.cli as cli

    seen = {}

    def _legacy(args):
        seen["legacy"] = True
        return 0

    def _ingest(args):
        seen["ingest"] = True
        return 0

    monkeypatch.setattr(cli, "_handle_compile_legacy", _legacy)
    monkeypatch.setattr(cli, "_handle_compile_paths_ingest", _ingest)
    assert cli.main(["compile"]) == 0
    assert seen.get("legacy") is True
    assert "ingest" not in seen


def test_status_uninitialized_project_exits_2(tmp_path, capsys):
    import tesserae.cli as cli

    rc = cli.main(["status", "--project", str(tmp_path)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "init" in err and "Traceback" not in err


def test_status_initialized_project_reports_counts(tmp_path, capsys):
    import tesserae.cli as cli

    assert cli.project_main(["init", "--project", str(tmp_path)]) == 0
    rc = cli.main(["status", "--project", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "nodes:" in out
    assert "edges:" in out
    assert "last compile:" in out
    assert "vault:" in out


def test_serve_autobuilds_when_missing(tmp_path, monkeypatch, capsys):
    import tesserae.cli as cli

    assert cli.project_main(["init", "--project", str(tmp_path)]) == 0
    built = {}

    def _build(args):
        built["built"] = True
        return 0

    monkeypatch.setattr(cli, "_serve_build_site", _build)
    monkeypatch.setattr(cli, "_handle_serve_legacy", lambda args: 0)
    rc = cli.main(["serve", "--project", str(tmp_path), "--dry-run"])
    assert rc == 0
    assert built.get("built") is True
    out = capsys.readouterr().out
    assert "building site first (missing)" in out


def test_serve_autobuilds_when_stale(tmp_path, monkeypatch, capsys):
    import tesserae.cli as cli

    assert cli.project_main(["init", "--project", str(tmp_path)]) == 0
    from tesserae.project import ProjectWiki

    wiki = ProjectWiki.load(str(tmp_path))
    # Fake an existing-but-stale site index, then bump graph.json mtime past it.
    wiki.paths.site.mkdir(parents=True, exist_ok=True)
    index = wiki.paths.site / "index.html"
    index.write_text("<html></html>")
    wiki.paths.graph.parent.mkdir(parents=True, exist_ok=True)
    if not wiki.paths.graph.exists():
        wiki.paths.graph.write_text("{}")
    old = index.stat().st_mtime
    os.utime(wiki.paths.graph, (old + 100, old + 100))

    built = {}

    def _build(args):
        built["built"] = True
        return 0

    monkeypatch.setattr(cli, "_serve_build_site", _build)
    monkeypatch.setattr(cli, "_handle_serve_legacy", lambda args: 0)
    rc = cli.main(["serve", "--project", str(tmp_path), "--dry-run"])
    assert rc == 0
    assert built.get("built") is True
    assert "building site first (stale)" in capsys.readouterr().out


def test_serve_no_build_flag_skips_autobuild(tmp_path, monkeypatch, capsys):
    import tesserae.cli as cli

    assert cli.project_main(["init", "--project", str(tmp_path)]) == 0
    built = {}

    def _build(args):
        built["built"] = True
        return 0

    monkeypatch.setattr(cli, "_serve_build_site", _build)
    monkeypatch.setattr(cli, "_handle_serve_legacy", lambda args: 0)
    rc = cli.main(["serve", "--project", str(tmp_path), "--no-build", "--dry-run"])
    assert rc == 0
    assert "built" not in built


def test_status_survives_corrupt_graph_json(tmp_path, capsys):
    import tesserae.cli as cli

    assert cli.project_main(["init", "--project", str(tmp_path)]) == 0
    (tmp_path / ".tesserae" / "graph.json").write_text("{truncated", encoding="utf-8")
    rc = cli.main(["status", "--project", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "corrupt" in out and "Traceback" not in out
