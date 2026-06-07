from pathlib import Path
from types import SimpleNamespace


def test_cli_setup_passes_raganything_flags_to_plan(tmp_path, monkeypatch):
    from tesserae import cli
    import tesserae.setup as tess_setup

    captured = {}
    real_build = tess_setup.build_plan
    real_apply = tess_setup.apply_plan

    def spying_build(detection, *, overrides=None):
        captured.update(overrides or {})
        return real_build(detection, overrides=overrides)

    def noop_apply(plan, **kwargs):
        return SimpleNamespace(
            wiki_root=plan.project_root,
            config_path=plan.project_root / ".tesserae" / "config.json",
            actions_taken=[],
            warnings=[],
            drift={},
        )

    monkeypatch.setattr(tess_setup, "build_plan", spying_build)
    monkeypatch.setattr(tess_setup, "apply_plan", noop_apply)

    # TODO(redesign-task-8): migrate when flag→config key lands. These
    # `--with-raganything`/`--install-raganything`/`--raganything-*`/`--run-raganything`
    # flags live ONLY on the legacy setup wizard; `tesserae init` does not surface
    # them (its `--yes` hard-codes raganything OFF), so the legacy `project_main`
    # setup path keeps serving this flag→override-plumbing assertion until Task 8.
    # TODO(redesign-task-7): setup-wizard-only flags — rewrite against _handle_setup namespace when project_main is deleted
    rc = cli.project_main([
        "setup", "--yes",
        "--project", str(tmp_path),
        "--with-raganything", "--install-raganything",
        "--raganything-parser", "docling",
        "--raganything-extras", "all",
        "--run-raganything",
    ])
    assert rc == 0
    assert captured["include_raganything"] is True
    assert captured["install_raganything"] is True
    assert captured["raganything_parser"] == "docling"
    assert captured["raganything_extras"] == "all"


def test_cli_with_raganything_alone_passes_none_for_install(tmp_path, monkeypatch):
    from tesserae import cli
    import tesserae.setup as tess_setup

    captured = {}
    real_build = tess_setup.build_plan

    def spying_build(detection, *, overrides=None):
        captured["overrides"] = dict(overrides or {})
        return real_build(detection, overrides=overrides)

    def noop_apply(plan, **kwargs):
        return SimpleNamespace(
            wiki_root=plan.project_root,
            config_path=plan.project_root / ".tesserae" / "config.json",
            actions_taken=[],
            warnings=[],
            drift={},
        )

    monkeypatch.setattr(tess_setup, "build_plan", spying_build)
    monkeypatch.setattr(tess_setup, "apply_plan", noop_apply)

    # TODO(redesign-task-8): migrate when flag→config key lands. `--with-raganything`
    # is a legacy setup-wizard-only flag absent from `tesserae init`; the legacy
    # `project_main` setup path keeps serving this override-filtering assertion.
    # TODO(redesign-task-7): setup-wizard-only flags — rewrite against _handle_setup namespace when project_main is deleted
    rc = cli.project_main([
        "setup", "--yes",
        "--with-raganything",
        "--project", str(tmp_path),
    ])
    assert rc == 0
    # When neither --install-raganything nor --skip-install-raganything is passed,
    # CLI should not include the key (None overrides are filtered out).
    assert "install_raganything" not in captured["overrides"]
    assert captured["overrides"].get("include_raganything") is True


def test_cli_ask_routes_raganything_when_backend_explicit(tmp_path, monkeypatch, capsys):
    """--backend raganything calls raganything_query.query directly."""
    from tesserae import cli
    import json as _json

    # Set up a minimal project on disk
    cfg_dir = tmp_path / ".tesserae"
    cfg_dir.mkdir()
    cfg = {
        "name": "demo",
        "sources": ["README.md"],
        "external_tools": [],
        "memory_backends": {
            "raganything": {
                "enabled": True,
                "working_dir": ".tesserae/external/raganything/working_dir",
                "parser": "docling",
                "query_mode": "hybrid",
            }
        },
    }
    (cfg_dir / "config.json").write_text(_json.dumps(cfg), encoding="utf-8")
    (tmp_path / "README.md").write_text("# demo", encoding="utf-8")

    captured = {}

    def fake_query(question, *, backend_config):
        captured["question"] = question
        captured["backend_config"] = backend_config
        return "raganything-answer"

    import tesserae.raganything_query as rq
    monkeypatch.setattr(rq, "query", fake_query)
    # The CLI imports `query` symbolically; patch the cli reference too if it's bound at call time.
    monkeypatch.setattr(cli, "_raganything_refresh_main", lambda argv: 0, raising=False)

    rc = cli.main([
        "ask", "What does the demo say?",
        "--backend", "raganything",
        "--project", str(tmp_path),
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "RAG-Anything answer:" in out
    assert "raganything-answer" in out
    assert captured["question"] == "What does the demo say?"
    # working_dir should be resolved relative to the project root
    assert str(tmp_path) in captured["backend_config"]["working_dir"]


def test_cli_ask_falls_through_when_raganything_returns_none(tmp_path, monkeypatch, capsys):
    """auto mode: raganything returning None falls through to cognee/wiki."""
    from tesserae import cli
    import json as _json

    cfg_dir = tmp_path / ".tesserae"
    cfg_dir.mkdir()
    cfg = {
        "name": "demo",
        "sources": ["README.md"],
        "external_tools": [],
        "memory_backends": {
            "raganything": {"enabled": True, "working_dir": "wd", "parser": "docling"},
            "cognee": {"enabled": False},  # force fallback to wiki
        },
    }
    (cfg_dir / "config.json").write_text(_json.dumps(cfg), encoding="utf-8")
    (tmp_path / "README.md").write_text("# demo\n\nSome content.", encoding="utf-8")

    import tesserae.raganything_query as rq
    monkeypatch.setattr(rq, "query", lambda q, *, backend_config: None)

    # The wiki fallback must run; it should not crash even with minimal corpus.
    rc = cli.main([
        "ask", "anything",
        "--backend", "auto",
        "--project", str(tmp_path),
    ])
    # rc may be 0 regardless of whether the wiki path returns hits — accept 0 or 2.
    assert rc in (0, 2)
    err = capsys.readouterr().err
    # No "RAG-Anything ask failed" since raganything just returned None silently.
    assert "RAG-Anything ask failed" not in err


def test_cli_refresh_raganything_invokes_refresh_main(monkeypatch):
    from tesserae import cli
    captured = {}

    def fake_refresh_main(argv):
        captured["argv"] = list(argv)
        return 0

    monkeypatch.setattr(cli, "_raganything_refresh_main", fake_refresh_main)
    rc = cli.main(["integrations", "refresh", "raganything", "--parser", "mineru", "--full"])
    assert rc == 0
    assert "--parser" in captured["argv"]
    assert "mineru" in captured["argv"]
    assert "--full" in captured["argv"]
