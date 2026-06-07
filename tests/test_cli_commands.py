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


# ---------------------------------------------------------------------------
# Task 4: `tesserae init` — wizard default, --yes non-interactive, --bare.
# ---------------------------------------------------------------------------


def test_init_bare_creates_workspace(tmp_path):
    import tesserae.cli as cli

    rc = cli.main(["init", "--bare", "--project", str(tmp_path), "--name", "t"])
    assert rc == 0
    assert (tmp_path / ".tesserae" / "config.json").exists()


def test_init_yes_runs_setup_noninteractive(tmp_path, monkeypatch):
    import tesserae.cli as cli

    called = {}

    def _stub(args):
        called["yes"] = args.yes
        return 0

    monkeypatch.setattr(cli, "_handle_setup", _stub)
    rc = cli.main(["init", "--yes", "--project", str(tmp_path)])
    assert rc == 0
    assert called["yes"] is True


def test_init_keeps_llm_flags(tmp_path):
    import json

    import tesserae.cli as cli

    rc = cli.main([
        "init", "--bare", "--project", str(tmp_path),
        "--llm-provider", "codex", "--codex-home", "/h/.codex-personal1",
    ])
    assert rc == 0
    cfg = json.loads((tmp_path / ".tesserae" / "config.json").read_text())
    assert cfg["llm_provider"] == "codex"


def test_init_has_exactly_eight_flags():
    import tesserae.cli as cli

    parser = cli._build_init_parser()
    flags = [a for a in parser._actions if a.option_strings and "-h" not in a.option_strings]
    dests = sorted(a.dest for a in flags)
    assert dests == sorted([
        "project", "name", "source", "yes", "bare",
        "llm_provider", "claude_config_dir", "codex_home",
    ]), dests


def test_init_yes_defaults_disable_optional_integrations(tmp_path, monkeypatch):
    """--yes must encode what CI's --no-cognee/--skip-* flags meant."""
    import tesserae.cli as cli

    seen = {}

    def _stub(args):
        seen.update(vars(args))
        return 0

    monkeypatch.setattr(cli, "_handle_setup", _stub)
    assert cli.main(["init", "--yes", "--project", str(tmp_path)]) == 0
    # exact attr names come from the legacy setup parser dests — the
    # integration toggles must land OFF (these are the real dests).
    assert seen["no_cognee"] is True
    assert seen["skip_raganything"] is True
    assert seen["with_understand_anything"] is False


@pytest.mark.parametrize(
    "argv, handler",
    [
        (["sessions", "import"], "_handle_sessions_import"),
        (["sessions", "discover"], "_handle_sessions_discover"),
        (["sessions", "list"], "_handle_sessions_list"),
        (["vault", "sync"], "_handle_vault_sync"),
        (["vault", "export"], "_handle_vault_export"),
        (["export", "harness"], "_handle_export_harness"),
        (["export", "graphiti"], "_handle_export_graphiti_cmd"),
        (["export", "site"], "_handle_export_site"),
        (["projects", "list"], "_handle_projects_list"),
        (["projects", "mcp-config"], "_handle_projects_mcp_config"),
        (["integrations", "refresh", "raganything"], "_handle_integrations_refresh"),
        (["lab", "evolve"], "_handle_lab_evolve"),
        (["lab", "schema-drift"], "_handle_lab_schema_drift"),
        (["extract", "x.md"], "_handle_extract"),
        (["code", "ingest"], "_handle_code_ingest"),
        (["code", "sync"], "_handle_code_sync"),
        (["research", "some question"], "_handle_research"),
        (["lint"], "_handle_lint"),
        (["query", "some question"], "_handle_query"),
        (["vault", "set-root", "/tmp/v"], "_handle_vault_set_root"),
        (["vault", "sync-all"], "_handle_vault_sync_all"),
        (["projects", "register", "/tmp/p"], "_handle_projects_register"),
    ],
)
def test_group_dispatch(argv, handler, monkeypatch):
    import tesserae.cli as cli

    called = {}

    def _stub(args):
        called["ok"] = True
        return 0

    monkeypatch.setattr(cli, handler, _stub)
    assert cli.main(argv) == 0
    assert called.get("ok")


def test_config_llm_is_old_llm_defaults(monkeypatch, tmp_path):
    import json

    import tesserae.cli as cli
    import tesserae.llm_json as lj

    monkeypatch.setattr(lj, "GLOBAL_CONFIG_PATH", tmp_path / "config.json")
    assert cli.main(["config", "llm", "--llm-provider", "codex"]) == 0
    assert json.loads((tmp_path / "config.json").read_text())["llm_provider"] == "codex"
    assert cli.main(["config", "show"]) == 0


def test_export_site_deploy_flag(monkeypatch):
    import tesserae.cli as cli

    seen = {}

    def _stub(args):
        seen["deploy"] = args.deploy
        return 0

    monkeypatch.setattr(cli, "_handle_export_site", _stub)
    assert cli.main(["export", "site", "--deploy"]) == 0
    assert seen["deploy"] is True


def test_group_smokes_run_real_handlers(tmp_path):
    """Real handlers, real namespaces — catches missing parser attrs."""
    import tesserae.cli as cli

    assert cli.main(["init", "--bare", "--project", str(tmp_path), "--name", "smoke"]) == 0
    assert cli.main(["status", "--project", str(tmp_path)]) == 0
    assert cli.main(["sessions", "list", "--project", str(tmp_path)]) == 0
    # lint on a freshly-`--bare`-initialized project has an empty graph and no
    # findings ("wiki is clean"), so the default --severity warning floor
    # exits 0 — not 1. (Lint only exits non-zero when findings exist.)
    assert cli.main(["lint", "--project", str(tmp_path)]) == 0
    assert cli.main(["export", "harness", "--project", str(tmp_path)]) == 0
    assert cli.main(["vault", "export", "--project", str(tmp_path)]) == 0
