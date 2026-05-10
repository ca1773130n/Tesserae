from pathlib import Path
from types import SimpleNamespace


def test_cli_setup_passes_raganything_flags_to_plan(tmp_path, monkeypatch):
    from llm_wiki import cli

    captured = {}

    def fake_build(root, **kwargs):
        captured["root"] = root
        captured.update(kwargs)
        from llm_wiki.project_setup import SetupPlan
        return SetupPlan(project_root=Path(root), name="demo", sources=["README.md"])

    def fake_apply(plan):
        return SimpleNamespace(
            wiki=SimpleNamespace(root=plan.project_root),
            config_path=plan.project_root / ".llm-wiki" / "config.json",
            ran_tools=[],
        )

    monkeypatch.setattr(cli, "build_setup_plan", fake_build)
    monkeypatch.setattr(cli, "apply_setup_plan", fake_apply)

    rc = cli.main([
        "project", "setup", "--yes",
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
    assert captured["run_raganything"] is True


def test_cli_with_raganything_alone_passes_none_for_install(tmp_path, monkeypatch):
    from llm_wiki import cli

    captured = {}

    def fake_build(root, **kwargs):
        captured.update(kwargs)
        from llm_wiki.project_setup import SetupPlan
        from pathlib import Path
        return SetupPlan(project_root=Path(root), name="demo", sources=["README.md"])

    monkeypatch.setattr(cli, "build_setup_plan", fake_build)
    monkeypatch.setattr(cli, "apply_setup_plan", lambda *a, **kw: SimpleNamespace(
        wiki=SimpleNamespace(root=Path(str(tmp_path))),
        config_path=Path(str(tmp_path)) / ".llm-wiki" / "config.json",
        ran_tools=[],
    ))

    rc = cli.main([
        "project", "setup", "--yes",
        "--with-raganything",
        "--project", str(tmp_path),
    ])
    assert rc == 0
    # When neither --install-raganything nor --skip-install-raganything is passed,
    # CLI should forward None so build_setup_plan can decide.
    assert captured["install_raganything"] is None


def test_cli_refresh_raganything_invokes_refresh_main(monkeypatch):
    from llm_wiki import cli
    captured = {}

    def fake_refresh_main(argv):
        captured["argv"] = list(argv)
        return 0

    monkeypatch.setattr(cli, "_raganything_refresh_main", fake_refresh_main)
    rc = cli.main(["project", "refresh-raganything", "--parser", "mineru", "--full"])
    assert rc == 0
    assert "--parser" in captured["argv"]
    assert "mineru" in captured["argv"]
    assert "--full" in captured["argv"]
