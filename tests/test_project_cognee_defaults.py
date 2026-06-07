import json

import tesserae.cli as cli
from tesserae.cli import main
from tesserae.project import ProjectWiki, cognify_options_from_config
from tesserae.project_setup import build_setup_plan, apply_setup_plan


def test_setup_enables_cognee_backend_by_default(tmp_path):
    project = tmp_path / "demo"
    project.mkdir()
    (project / "README.md").write_text("# Demo\n", encoding="utf-8")

    plan = build_setup_plan(project)
    result = apply_setup_plan(plan)

    cfg = json.loads(result.config_path.read_text(encoding="utf-8"))
    cognee = cfg["memory_backends"]["cognee"]
    assert cognee["enabled"] is True
    assert cognee["mode"] == "codex_cognify"
    assert cognee["auto_cognify"] is False
    assert cognee["dataset"] == "demo_memory"
    assert cognee["system_root"] == ".tesserae/cognee_system"
    assert cognee["data_root"] == ".tesserae/cognee_data"
    assert cognee["fail_fast"] is False
    assert cognee["install"]["enabled"] is True
    assert cognee["install"]["auto_install"] is False
    assert "pip" in cognee["install"]["command"]


def test_setup_installs_cognee_when_requested(tmp_path, monkeypatch, capsys):
    project = tmp_path / "demo"
    project.mkdir()
    (project / "README.md").write_text("# Demo\n", encoding="utf-8")

    import subprocess

    seen: list[str] = []
    original_run = subprocess.run

    def fake_run(cmd, *rest, **kwargs):
        if isinstance(cmd, str) and "pip install cognee" in cmd:
            seen.append(cmd)
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok", stderr="")
        return original_run(cmd, *rest, **kwargs)

    monkeypatch.setattr(subprocess, "run", fake_run)

    # TODO(redesign-task-8): migrate when flag→config key lands. `--install-cognee`
    # / `--no-color` live ONLY on the legacy setup wizard; `tesserae init --yes`
    # hard-codes cognee install OFF. We drive the unchanged `_handle_setup` with the
    # namespace those flags produced (cognee enabled + install requested).
    args = cli._build_init_parser().parse_args(["--project", str(project), "--yes"])
    cli._backfill_setup_defaults(args)
    args.no_cognee = False
    args.install_cognee = True
    args.skip_install_cognee = False
    args.no_color = True
    assert cli._handle_setup(args) == 0

    assert seen, "cognee installer should have been invoked"
    out = capsys.readouterr().out
    assert "[installed] cognee" in out


def test_compile_uses_configured_cognee_when_auto_cognify_enabled(tmp_path, monkeypatch):
    project = tmp_path / "demo"
    project.mkdir()
    (project / "README.md").write_text("# Demo\nGaussian Splatting supports novel view synthesis.\n", encoding="utf-8")
    wiki = ProjectWiki.init(project, name="demo", source_kind="Repository", sources=["README.md"])
    cfg = wiki.config()
    cfg["memory_backends"] = {
        "cognee": {
            "enabled": True,
            "mode": "add",
            "auto_cognify": True,
            "dataset": "demo_memory",
            "system_root": ".tesserae/cognee_system",
            "data_root": ".tesserae/cognee_data",
        }
    }
    wiki.paths.config.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    calls = []
    monkeypatch.setattr(ProjectWiki, "_run_cognify", lambda self, options: calls.append(options))

    assert main(["compile", "--project", str(project), "--limit", "1"]) == 0

    assert calls
    assert calls[0].mode == "add"
    assert calls[0].dataset == "demo_memory"
    assert calls[0].system_root == ".tesserae/cognee_system"
    assert calls[0].data_root == ".tesserae/cognee_data"


def test_compile_cli_cognee_flags_override_config(tmp_path, monkeypatch):
    """Task 8 flag diet: the cognee knobs moved from `compile --cognee-*`
    flags to `compile_options.cognee_*` config keys. Setting them in
    config.json must still override the memory_backends config block."""
    project = tmp_path / "demo"
    project.mkdir()
    (project / "README.md").write_text("# Demo\nGaussian Splatting supports novel view synthesis.\n", encoding="utf-8")
    wiki = ProjectWiki.init(project, name="demo", source_kind="Repository", sources=["README.md"])
    cfg = wiki.config()
    cfg["memory_backends"] = {"cognee": {"enabled": True, "mode": "add", "auto_cognify": False, "dataset": "configured"}}
    cfg["compile_options"] = {"cognee_codex_cognify": True, "cognee_dataset": "override_memory"}
    wiki.paths.config.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    calls = []
    monkeypatch.setattr(ProjectWiki, "_run_cognify", lambda self, options: calls.append(options))

    assert main([
        "compile", "--project", str(project), "--limit", "1",
    ]) == 0

    assert calls
    assert calls[0].mode == "codex_cognify"
    assert calls[0].dataset == "override_memory"


def test_cognify_options_from_config_ignores_disabled_or_manual_cognee(tmp_path):
    cfg = {"memory_backends": {"cognee": {"enabled": True, "auto_cognify": False, "mode": "codex_cognify"}}}
    assert cognify_options_from_config(cfg) is None
    cfg["memory_backends"]["cognee"]["auto_cognify"] = True
    cfg["memory_backends"]["cognee"]["enabled"] = False
    assert cognify_options_from_config(cfg) is None


def test_legacy_project_config_gets_default_cognee_backend():
    from tesserae.project import cognee_backend_config

    cognee = cognee_backend_config({"name": "legacy_demo"})

    assert cognee["enabled"] is True
    assert cognee["dataset"] == "legacy_demo_memory"
    assert cognee["auto_cognify"] is False
    assert cognee["install"]["enabled"] is True


def test_legacy_auto_cognify_config_auto_installs_cognee_if_missing():
    options = cognify_options_from_config({
        "name": "legacy_demo",
        "memory_backends": {"cognee": {"enabled": True, "mode": "codex_cognify", "auto_cognify": True}},
    })

    assert options is not None
    assert options.auto_install is True
    assert "pip install cognee" in options.install_command


def test_configured_cognee_failure_warns_and_compile_continues(tmp_path, monkeypatch, capsys):
    project = tmp_path / "demo"
    project.mkdir()
    (project / "README.md").write_text("# Demo\nGaussian Splatting supports novel view synthesis.\n", encoding="utf-8")
    wiki = ProjectWiki.init(project, name="demo", source_kind="Repository", sources=["README.md"])
    cfg = wiki.config()
    cfg["memory_backends"]["cognee"]["auto_cognify"] = True
    cfg["memory_backends"]["cognee"]["fail_fast"] = False
    wiki.paths.config.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")

    def fail_cognee(self, options):
        raise RuntimeError("cognee missing")

    monkeypatch.setattr(ProjectWiki, "_run_cognify", fail_cognee)

    assert main(["compile", "--project", str(project), "--limit", "1"]) == 0

    out = capsys.readouterr().out
    assert "Cognee cognify warning" in out
    assert "Compiled project wiki" in out


def test_configured_cognee_missing_module_installs_then_retries(tmp_path, monkeypatch, capsys):
    project = tmp_path / "demo"
    project.mkdir()
    (project / "README.md").write_text("# Demo\nGaussian Splatting supports novel view synthesis.\n", encoding="utf-8")
    wiki = ProjectWiki.init(project, name="demo", source_kind="Repository", sources=["README.md"])
    cfg = wiki.config()
    cfg["memory_backends"]["cognee"]["auto_cognify"] = True
    cfg["memory_backends"]["cognee"]["install"]["auto_install"] = True
    wiki.paths.config.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    calls = {"cognify": 0, "install": 0}

    def flaky_cognee(self, options):
        calls["cognify"] += 1
        if calls["cognify"] == 1:
            raise ModuleNotFoundError("No module named 'cognee'")

    def fake_install(self, options):
        calls["install"] += 1
        return {"status": "installed", "command": options.install_command}

    monkeypatch.setattr(ProjectWiki, "_run_cognify", flaky_cognee)
    monkeypatch.setattr(ProjectWiki, "_install_cognee", fake_install)

    assert main(["compile", "--project", str(project), "--limit", "1"]) == 0

    assert calls == {"cognify": 2, "install": 1}
    out = capsys.readouterr().out
    assert "Cognee missing; installing" in out
    assert "Cognee installed; retrying cognify" in out


def test_project_ask_uses_configured_cognee_backend(tmp_path, monkeypatch, capsys):
    project = tmp_path / "demo"
    project.mkdir()
    wiki = ProjectWiki.init(project, name="demo", sources=[])
    cfg = wiki.config()
    cfg["memory_backends"] = {"cognee": {"enabled": True, "dataset": "demo_memory"}}
    wiki.paths.config.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")

    monkeypatch.setattr(
        "tesserae.cognee_query.search_cognee",
        lambda question, dataset=None, search_type="INSIGHTS", top_k=8: [f"answer for {question} in {dataset}"],
    )

    assert main(["ask", "What renders Mermaid?", "--project", str(project)]) == 0
    out = capsys.readouterr().out
    assert "Cognee answer" in out
    assert "answer for What renders Mermaid? in demo_memory" in out
