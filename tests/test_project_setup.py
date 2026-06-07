import json

import tesserae.cli as cli
from tesserae.cli import main
from tesserae.project_setup import build_setup_plan, render_setup_summary, expand_tool_command


def _run_setup(*, project, name=None, overrides=None):
    """Invoke the setup wizard exactly as `tesserae init --yes` does internally.

    Builds the canonical Namespace from the 8-flag init parser plus
    `_backfill_setup_defaults` (every dest `_handle_setup` reads, all optional
    integrations OFF), then applies the setup-wizard-only opt-ins the old
    `project setup` flags used to set. This replaces the removed `project_main`
    surface while exercising the identical `_handle_setup` behavior.
    """
    init_argv = ["--project", str(project), "--yes"]
    if name is not None:
        init_argv += ["--name", name]
    args = cli._build_init_parser().parse_args(init_argv)
    cli._backfill_setup_defaults(args)
    for key, value in (overrides or {}).items():
        setattr(args, key, value)
    return cli._handle_setup(args)


def test_setup_plan_detects_common_sources_and_understand_anything(tmp_path):
    project = tmp_path / "demo"
    project.mkdir()
    (project / "README.md").write_text("# Demo\n", encoding="utf-8")
    (project / "docs").mkdir()
    (project / "src").mkdir()
    ua = project / ".understand-anything"
    ua.mkdir()
    (ua / "knowledge-graph.json").write_text('{"nodes": [], "edges": []}\n', encoding="utf-8")

    plan = build_setup_plan(project, include_understand_anything=True)

    assert plan.sources == ["README.md", "docs", "src", ".tesserae/external/understand-anything.md"]
    assert plan.external_tools[0]["id"] == "understand-anything"
    assert plan.external_tools[0]["artifact"] == ".understand-anything/knowledge-graph.json"
    assert plan.external_tools[0]["source"] == ".tesserae/external/understand-anything.md"
    assert plan.external_tools[0]["auto_refresh"] is True
    assert plan.external_tools[0]["sync_mode"] == "native_graph"
    assert plan.external_tools[0]["preserve_markdown_projection"] is True
    assert plan.external_tools[0]["managed_refresh"] is True
    assert "tesserae.understand_anything_refresh" in plan.external_tools[0]["refresh_command"]


def test_managed_understand_anything_refresh_command_expands_to_current_python(tmp_path):
    project = tmp_path / "demo"
    project.mkdir()

    plan = build_setup_plan(project, include_understand_anything=True, understand_anything_platform="opencode")
    tool = plan.external_tools[0]
    command = expand_tool_command(tool["refresh_command"], project, tool)

    assert "tesserae.understand_anything_refresh" in command
    assert f"--project {project}" in command
    assert "--platform opencode" in command

def test_setup_command_yes_writes_config_with_external_tool_metadata(tmp_path, capsys):
    project = tmp_path / "demo"
    project.mkdir()
    (project / "README.md").write_text("# Demo\n", encoding="utf-8")
    ua = project / ".understand-anything"
    ua.mkdir()
    (ua / "knowledge-graph.json").write_text('{"nodes": [], "edges": []}\n', encoding="utf-8")

    # TODO(redesign-task-8): migrate when flag→config key lands. The setup-wizard-only
    # opt-ins applied below (with/install/run/skip-install-understand-anything, no-color,
    # name, understand-anything-command) are NOT surfaced on `tesserae init`; its
    # --yes hard-codes integrations OFF and color auto. We drive `_handle_setup` with
    # the same namespace those flags produced until Task 8 lands the flag→config keys.
    code = _run_setup(
        project=project,
        name="demo_wiki",
        overrides={"with_understand_anything": True, "no_color": True},
    )

    assert code == 0
    cfg = json.loads((project / ".tesserae" / "config.json").read_text(encoding="utf-8"))
    assert cfg["sources"] == ["README.md", ".tesserae/external/understand-anything.md"]
    assert cfg["setup"]["wizard"] == "tesserae project setup"
    assert cfg["external_tools"][0]["id"] == "understand-anything"
    assert cfg["external_tools"][0]["install"]["enabled"] is True
    assert cfg["external_tools"][0]["auto_refresh"] is True
    assert cfg["external_tools"][0]["sync_mode"] == "native_graph"
    assert cfg["external_tools"][0]["preserve_markdown_projection"] is True
    assert cfg["external_tools"][0]["managed_refresh"] is True
    assert "tesserae.understand_anything_refresh" in cfg["external_tools"][0]["refresh_command"]
    assert "install.sh" in cfg["external_tools"][0]["install"]["command"]
    assert (project / ".tesserae" / "external" / "understand-anything.md").exists()
    out = capsys.readouterr().out
    assert "Tesserae setup" in out
    assert "Understand Anything" in out


def test_setup_installs_understand_anything_when_requested(tmp_path, monkeypatch, capsys):
    project = tmp_path / "demo"
    project.mkdir()
    (project / "README.md").write_text("# Demo\n", encoding="utf-8")

    # Patch the new pipeline's subprocess.run so the curl|bash installer
    # doesn't actually execute.
    import subprocess

    seen: list[str] = []
    original_run = subprocess.run

    def fake_run(cmd, *rest, **kwargs):
        if isinstance(cmd, str) and "install.sh" in cmd:
            seen.append(cmd)
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok", stderr="")
        return original_run(cmd, *rest, **kwargs)

    monkeypatch.setattr(subprocess, "run", fake_run)

    # TODO(redesign-task-8): migrate when flag→config key lands. The setup-wizard-only
    # opt-ins applied below (with/install/run/skip-install-understand-anything, no-color,
    # name, understand-anything-command) are NOT surfaced on `tesserae init`; its
    # --yes hard-codes integrations OFF and color auto. We drive `_handle_setup` with
    # the same namespace those flags produced until Task 8 lands the flag→config keys.
    assert _run_setup(
        project=project,
        overrides={
            "with_understand_anything": True,
            "install_understand_anything": True,
            "skip_install_understand_anything": False,
            "no_color": True,
        },
    ) == 0

    assert seen, "install.sh installer should have been invoked"
    out = capsys.readouterr().out
    assert "[installed] understand-anything" in out


def test_setup_persists_config_even_when_initial_external_refresh_fails(tmp_path, capsys):
    project = tmp_path / "demo"
    project.mkdir()
    (project / "README.md").write_text("# Demo\n", encoding="utf-8")

    # TODO(redesign-task-8): migrate when flag→config key lands. The setup-wizard-only
    # opt-ins applied below (with/install/run/skip-install-understand-anything, no-color,
    # name, understand-anything-command) are NOT surfaced on `tesserae init`; its
    # --yes hard-codes integrations OFF and color auto. We drive `_handle_setup` with
    # the same namespace those flags produced until Task 8 lands the flag→config keys.
    assert _run_setup(
        project=project,
        overrides={
            "with_understand_anything": True,
            "understand_anything_command": "definitely_missing_understand_command",
            "run_understand_anything": True,
            "skip_install_understand_anything": True,
            "no_color": True,
        },
    ) == 0

    cfg = json.loads((project / ".tesserae" / "config.json").read_text(encoding="utf-8"))
    assert cfg["external_tools"][0]["refresh_command"] == "definitely_missing_understand_command"
    assert cfg["external_tools"][0]["auto_refresh"] is True
    assert (project / ".tesserae" / "external" / "understand-anything.md").exists()
    out = capsys.readouterr().out
    assert "[failed] understand-anything" in out
    assert "definitely_missing_understand_command" in out


def test_compile_auto_refreshes_configured_external_tools(tmp_path, capsys):
    project = tmp_path / "demo"
    project.mkdir()
    (project / "README.md").write_text("# Demo\nGaussian Splatting supports novel view synthesis.\n", encoding="utf-8")
    command = "python3 -c \"from pathlib import Path; p=Path('.understand-anything'); p.mkdir(exist_ok=True); (p/'knowledge-graph.json').write_text('{\\\"nodes\\\": [], \\\"edges\\\": []}\\n')\""

    # TODO(redesign-task-8): migrate when flag→config key lands. The setup-wizard-only
    # opt-ins applied below (with/install/run/skip-install-understand-anything, no-color,
    # name, understand-anything-command) are NOT surfaced on `tesserae init`; its
    # --yes hard-codes integrations OFF and color auto. We drive `_handle_setup` with
    # the same namespace those flags produced until Task 8 lands the flag→config keys.
    assert _run_setup(
        project=project,
        overrides={
            "with_understand_anything": True,
            "understand_anything_command": command,
            "run_understand_anything": True,
            "skip_install_understand_anything": True,
            "no_color": True,
        },
    ) == 0
    (project / ".understand-anything" / "knowledge-graph.json").unlink()

    assert main(["compile", "--project", str(project), "--limit", "1"]) == 0

    assert (project / ".understand-anything" / "knowledge-graph.json").exists()
    assert "Refreshed external tools" in capsys.readouterr().out


def test_compile_warns_and_continues_when_auto_refresh_command_is_missing(tmp_path, capsys):
    project = tmp_path / "demo"
    project.mkdir()
    (project / "README.md").write_text("# Demo\nGaussian Splatting supports novel view synthesis.\n", encoding="utf-8")

    # TODO(redesign-task-8): migrate when flag→config key lands. The setup-wizard-only
    # opt-ins applied below (with/install/run/skip-install-understand-anything, no-color,
    # name, understand-anything-command) are NOT surfaced on `tesserae init`; its
    # --yes hard-codes integrations OFF and color auto. We drive `_handle_setup` with
    # the same namespace those flags produced until Task 8 lands the flag→config keys.
    assert _run_setup(
        project=project,
        overrides={
            "with_understand_anything": True,
            "understand_anything_command": "definitely_missing_understand_command",
            "run_understand_anything": True,
            "skip_install_understand_anything": True,
            "no_color": True,
        },
    ) == 0

    assert main(["compile", "--project", str(project), "--limit", "1"]) == 0

    out = capsys.readouterr().out
    assert "External tool" in out and "warnings" in out
    assert "definitely_missing_understand_command" in out
    assert "Compiled project wiki" in out
    assert (project / ".tesserae" / "graph.json").exists()


def test_render_setup_summary_contains_ansi_when_color_enabled(tmp_path):
    project = tmp_path / "demo"
    project.mkdir()
    plan = build_setup_plan(project, sources=["README.md"], include_understand_anything=False)

    rendered = render_setup_summary(plan, color=True)

    assert "\x1b[" in rendered
    assert "README.md" in rendered
