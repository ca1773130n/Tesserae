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

    assert cli.main(["init", "--bare", "--project", str(tmp_path)]) == 0
    rc = cli.main(["status", "--project", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "nodes:" in out
    assert "edges:" in out
    assert "last compile:" in out
    assert "vault:" in out


def test_serve_autobuilds_when_missing(tmp_path, monkeypatch, capsys):
    import tesserae.cli as cli

    assert cli.main(["init", "--bare", "--project", str(tmp_path)]) == 0
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

    assert cli.main(["init", "--bare", "--project", str(tmp_path)]) == 0
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

    assert cli.main(["init", "--bare", "--project", str(tmp_path)]) == 0
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

    assert cli.main(["init", "--bare", "--project", str(tmp_path)]) == 0
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


# ---------------------------------------------------------------------------
# Task 8 — compile flag diet
# ---------------------------------------------------------------------------
#
# `tesserae compile` historically carried ~26 flags. The spec caps the
# everyday surface at 8; every removed flag becomes a
# ``compile_options.<dest>`` config-key read at the SAME handler behavior
# point with the old argparse default as fallback.


def _bare_project(tmp_path):
    """Init a --bare project and return its config.json path."""
    import json

    import tesserae.cli as cli

    assert cli.main(["init", "--bare", "--project", str(tmp_path), "--name", "diet"]) == 0
    return tmp_path / ".tesserae" / "config.json"


def _set_compile_options(config_path, **opts):
    """Merge ``opts`` into ``compile_options`` in an existing config.json."""
    import json

    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    cfg.setdefault("compile_options", {}).update(opts)
    config_path.write_text(json.dumps(cfg), encoding="utf-8")


def test_compile_flag_surface_is_small():
    import tesserae.cli as cli

    parser = cli._build_compile_parser()
    flags = [a for a in parser._actions if a.option_strings and "-h" not in a.option_strings]
    # 10 core dests (llm_provider/claude_config_dir/codex_home + project +
    # changed_only + limit + refresh_integrations + sessions_enabled +
    # distill_enabled + strict) + the provider-agnostic extractor surface:
    # extractor + llm_model/llm_include/llm_limit (LLM is the default), with the
    # deprecated claude_include/limit/timeout/model hidden aliases = 18 dests max.
    assert len({a.dest for a in flags}) <= 18, sorted({a.dest for a in flags})


def test_compile_keeps_exactly_the_dieted_dests():
    import tesserae.cli as cli

    parser = cli._build_compile_parser()
    flags = [a for a in parser._actions if a.option_strings and "-h" not in a.option_strings]
    assert sorted({a.dest for a in flags}) == sorted([
        "project",
        "changed_only",
        "limit",
        "refresh_integrations",
        "sessions_enabled",
        # ``distill_enabled`` is a sanctioned feature toggle (peer of
        # ``--sessions``): ``--distill`` / ``--no-distill`` flips the opt-in
        # AgentRunbook distillation passes for this run, overriding the
        # config.json ``distillation.enabled`` key — so it stays a CLI flag
        # rather than a pure ``compile_options.*`` config key.
        "distill_enabled",
        # ``--strict`` gates the exit code on the post-compile lint (errors→2,
        # warnings→1); default stays report-only. A per-run CI/publish knob,
        # so a CLI flag rather than a ``compile_options.*`` key.
        "strict",
        "llm_provider",
        "claude_config_dir",
        "codex_home",
        # Provider-agnostic extractor surface: `compile --extractor llm` (the
        # DEFAULT) builds the concept/claim layer via the configured provider;
        # --llm-model/--llm-include/--llm-limit tune it. The claude_* dests are
        # deprecated hidden aliases (no default timeout) kept for back-compat.
        "extractor",
        "llm_model",
        "llm_include",
        "llm_limit",
        "claude_include",
        "claude_limit",
        "claude_timeout",
        "claude_model",
    ])


def test_refresh_integrations_renamed_from_external_tools():
    """The kept flag is `--refresh-integrations` (dest refresh_integrations);
    the old `--refresh-external-tools` name is gone."""
    import tesserae.cli as cli

    parser = cli._build_compile_parser()
    options = {opt for a in parser._actions for opt in a.option_strings}
    assert "--refresh-integrations" in options
    assert "--refresh-external-tools" not in options


def _patch_compile(monkeypatch, sink):
    """Patch ProjectWiki.compile to capture kwargs and return a stub result."""
    import tesserae.cli as cli

    def _fake_compile(self, **kwargs):
        sink.update(kwargs)
        return {
            "processed_files": 0,
            "skipped_files": 0,
            "node_count": 0,
            "edge_count": 0,
            "graph_path": str(self.paths.graph),
        }

    monkeypatch.setattr(cli.ProjectWiki, "compile", _fake_compile)


def test_compile_options_flow_into_wiki_compile(tmp_path, monkeypatch):
    """source_kind, trends, min_trend_sources, exclude_data, no_vault_pull,
    use_extraction_feedback removed flags → wiki.compile() kwargs."""
    config_path = _bare_project(tmp_path)
    _set_compile_options(
        config_path,
        source_kind="papers",
        trends=True,
        min_trend_sources=5,
        exclude_data=True,
        no_vault_pull=True,
        use_extraction_feedback=True,
    )
    seen = {}
    _patch_compile(monkeypatch, seen)
    import tesserae.cli as cli

    assert cli.main(["compile", "--project", str(tmp_path)]) == 0
    assert seen["source_kind"] == "papers"
    assert seen["trends"] is True
    assert seen["min_trend_sources"] == 5
    assert seen["exclude_data"] is True
    assert seen["vault_pull"] is False  # no_vault_pull=True ⇒ vault_pull=False
    assert seen["use_extraction_feedback"] is True


def test_compile_options_defaults_when_unset(tmp_path, monkeypatch):
    """No compile_options ⇒ old argparse defaults flow through."""
    _bare_project(tmp_path)
    seen = {}
    _patch_compile(monkeypatch, seen)
    import tesserae.cli as cli

    assert cli.main(["compile", "--project", str(tmp_path)]) == 0
    assert seen["source_kind"] is None
    assert seen["trends"] is False
    assert seen["min_trend_sources"] == 2
    assert seen["exclude_data"] is False
    assert seen["vault_pull"] is True
    assert seen["use_extraction_feedback"] is False


def test_session_compile_options_flow_into_session_options(tmp_path, monkeypatch):
    """sessions_llm + sessions_model removed flags → SessionExtractionOptions."""
    config_path = _bare_project(tmp_path)
    _set_compile_options(config_path, sessions_llm="true", sessions_model="claude-x")
    seen = {}
    _patch_compile(monkeypatch, seen)
    import tesserae.cli as cli

    assert cli.main(["compile", "--project", str(tmp_path)]) == 0
    opts = seen["session_options"]
    assert opts is not None
    assert opts.llm_enabled == "true"
    assert opts.model == "claude-x"


def test_cognee_compile_options_flow_into_cognify(tmp_path, monkeypatch):
    """All cognee_* removed flags → CognifyOptions at the handler point."""
    config_path = _bare_project(tmp_path)
    _set_compile_options(
        config_path,
        cognee_cognify=True,
        cognee_dataset="custom_ds",
        cognee_codex_model="gpt-x",
        cognee_codex_timeout=999,
        cognee_local_embedding_dimensions=1024,
        cognee_embedding_provider="ollama",
        cognee_ollama_embedding_model="my-embed",
        cognee_ollama_embedding_endpoint="http://host:1234/api/embed",
        cognee_ollama_embedding_timeout=42,
        cognee_system_root="/tmp/sys",
        cognee_data_root="/tmp/data",
    )
    seen = {}
    _patch_compile(monkeypatch, seen)
    import tesserae.cli as cli

    assert cli.main(["compile", "--project", str(tmp_path)]) == 0
    cognify = seen["cognify"]
    assert cognify is not None
    assert cognify.mode == "cognify"
    assert cognify.dataset == "custom_ds"
    assert cognify.codex_model == "gpt-x"
    assert cognify.codex_timeout == 999
    assert cognify.local_embedding_dimensions == 1024
    assert cognify.embedding_provider == "ollama"
    assert cognify.ollama_embedding_model == "my-embed"
    assert cognify.ollama_embedding_endpoint == "http://host:1234/api/embed"
    assert cognify.ollama_embedding_timeout == 42
    assert cognify.system_root == "/tmp/sys"
    assert cognify.data_root == "/tmp/data"


def test_cognee_codex_cognify_compile_option(tmp_path, monkeypatch):
    """cognee_codex_cognify + cognee_add removed flags select cognify mode."""
    config_path = _bare_project(tmp_path)
    _set_compile_options(config_path, cognee_codex_cognify=True)
    seen = {}
    _patch_compile(monkeypatch, seen)
    import tesserae.cli as cli

    assert cli.main(["compile", "--project", str(tmp_path)]) == 0
    assert seen["cognify"] is not None
    assert seen["cognify"].mode == "codex_cognify"


def test_refresh_integrations_reaches_external_tool_refresh(tmp_path, monkeypatch):
    """The kept (renamed) --refresh-integrations flag still drives
    refresh_configured_external_tools(only_auto=...)."""
    config_path = _bare_project(tmp_path)
    seen = {}
    _patch_compile(monkeypatch, seen)
    import tesserae.cli as cli

    def _fake_refresh(project, only_auto=True, fail_fast=False):
        seen["only_auto"] = only_auto
        return []

    monkeypatch.setattr(cli, "refresh_configured_external_tools", _fake_refresh)

    assert cli.main(["compile", "--project", str(tmp_path)]) == 0
    assert seen["only_auto"] is True  # flag absent ⇒ only auto-refresh tools

    assert cli.main(["compile", "--project", str(tmp_path), "--refresh-integrations"]) == 0
    assert seen["only_auto"] is False  # flag present ⇒ refresh all


# --------------------------------------------------------------------------- #
# `tesserae projects register` auto-inits an uninitialized project dir
# --------------------------------------------------------------------------- #


def test_projects_register_auto_inits_uninitialized_dir(tmp_path, capsys):
    """Registering an existing dir with no .tesserae/ initializes it first,
    so register succeeds instead of failing with 'No .tesserae/graph.json'."""
    import tesserae.cli as cli

    proj = tmp_path / "mpgs"
    proj.mkdir()  # exists, but NOT a Tesserae project

    rc = cli.main(["projects", "register", str(proj), "--name", "mpgs"])
    assert rc == 0, capsys.readouterr().err
    # bare workspace was created
    assert (proj / ".tesserae" / "config.json").is_file()
    assert (proj / ".tesserae" / "graph.json").is_file()
    out = capsys.readouterr().out
    assert "Registered 'mpgs'" in out


def test_projects_register_does_not_create_missing_path(tmp_path, capsys):
    """A non-existent (typo'd) path must NOT be auto-created — still an error."""
    import tesserae.cli as cli

    missing = tmp_path / "does-not-exist"
    rc = cli.main(["projects", "register", str(missing), "--name", "x"])
    assert rc == 2
    assert not missing.exists()
    assert "register failed" in capsys.readouterr().err


def test_projects_register_does_not_reinit_existing_project(tmp_path, capsys):
    """An already-initialized project is registered as-is — config.json is
    NOT overwritten by a re-init."""
    import json as _json

    import tesserae.cli as cli
    from tesserae.project import ProjectWiki

    proj = tmp_path / "already"
    wiki = ProjectWiki.init(proj, name="already")
    cfg = _json.loads(wiki.paths.config.read_text())
    cfg["sentinel"] = "preserve-me"
    wiki.paths.config.write_text(_json.dumps(cfg))

    rc = cli.main(["projects", "register", str(proj), "--name", "already"])
    assert rc == 0, capsys.readouterr().err
    after = _json.loads(wiki.paths.config.read_text())
    assert after.get("sentinel") == "preserve-me", "register must not re-init/overwrite config"
