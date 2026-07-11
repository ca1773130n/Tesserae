import json

import tesserae.cli as cli
from tesserae.cli import main
from tesserae.project_setup import (
    build_setup_plan,
    expand_tool_command,
    refresh_configured_external_tools,
    render_setup_summary,
    run_tool_configs,
)


def _run_setup(*, project, name=None, overrides=None):
    """Invoke the setup wizard exactly as `tesserae init --yes` does internally.

    Builds the canonical Namespace from the 8-flag init parser plus
    `_backfill_setup_defaults` (every dest `_handle_setup` reads, all optional
    integrations OFF), then applies the setup-wizard-only opt-ins the old
    `init` flags used to set. This replaces the removed `project_main`
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


def _write_external_tool(project, tool: dict) -> None:
    """Inject an external_tools entry into an already-initialized config.json."""
    cfg_path = project / ".tesserae" / "config.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    cfg.setdefault("external_tools", []).append(tool)
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def test_setup_plan_detects_common_sources(tmp_path):
    project = tmp_path / "demo"
    project.mkdir()
    (project / "README.md").write_text("# Demo\n", encoding="utf-8")
    (project / "docs").mkdir()
    (project / "src").mkdir()

    plan = build_setup_plan(project)

    assert plan.sources == ["README.md", "docs", "src"]
    assert plan.external_tools == []


def test_setup_plan_ignores_understand_anything_artifact(tmp_path):
    """Removed backend: a .understand-anything artifact on disk never pulls
    anything into the plan (the integration itself was removed)."""
    project = tmp_path / "demo"
    project.mkdir()
    (project / "README.md").write_text("# Demo\n", encoding="utf-8")
    ua = project / ".understand-anything"
    ua.mkdir()
    (ua / "knowledge-graph.json").write_text('{"nodes": [], "edges": []}\n', encoding="utf-8")

    plan = build_setup_plan(project)

    assert plan.external_tools == []
    assert ".tesserae/external/understand-anything.md" not in plan.sources


def test_expand_tool_command_substitutes_python_project_platform(tmp_path):
    project = tmp_path / "demo"
    project.mkdir()
    tool = {"install": {"platform": "opencode"}}
    command = expand_tool_command(
        "{python} -m some.module --project {project} --platform {platform}", project, tool
    )

    assert "some.module" in command
    assert f"--project {project}" in command
    assert "--platform opencode" in command


def test_setup_command_yes_writes_config_without_external_tools(tmp_path):
    project = tmp_path / "demo"
    project.mkdir()
    (project / "README.md").write_text("# Demo\n", encoding="utf-8")

    code = _run_setup(project=project, name="demo_wiki", overrides={"no_color": True})

    assert code == 0
    cfg = json.loads((project / ".tesserae" / "config.json").read_text(encoding="utf-8"))
    assert cfg["sources"] == ["README.md"]
    assert cfg.get("external_tools", []) == []


def test_legacy_understand_anything_config_entry_is_ignored_with_one_note(tmp_path, capsys):
    """Regression (backend EOL stage 1): loading an OLD config that still
    carries an understand-anything external_tools entry prints ONE stderr
    note and continues — never an error."""
    project = tmp_path / "demo"
    project.mkdir()
    (project / "README.md").write_text(
        "# Demo\nGaussian Splatting supports novel view synthesis.\n", encoding="utf-8"
    )
    assert _run_setup(project=project, overrides={"no_color": True}) == 0
    capsys.readouterr()
    _write_external_tool(
        project,
        {
            "id": "understand-anything",
            "name": "Understand Anything",
            "artifact": ".understand-anything/knowledge-graph.json",
            "refresh_command": "definitely_missing_understand_command",
            "auto_refresh": True,
            "enabled": True,
        },
    )

    results = refresh_configured_external_tools(project, only_auto=True, fail_fast=False)

    err = capsys.readouterr().err
    assert err.count("understand-anything external tool was removed") == 1
    assert [r for r in results if r.get("id") == "understand-anything"] == [
        {"id": "understand-anything", "status": "skipped", "reason": "backend removed"}
    ]

    # And a full compile over that legacy config still succeeds.
    assert main(["compile", "--project", str(project), "--limit", "1"]) == 0
    assert (project / ".tesserae" / "graph.json").exists()


def test_run_tool_configs_notes_once_for_multiple_legacy_entries(tmp_path, capsys):
    project = tmp_path / "demo"
    project.mkdir()
    tools = [
        {"id": "understand-anything", "enabled": True},
        {"id": "understand-anything", "enabled": True},
    ]

    results = run_tool_configs(project, tools, only_auto=False, fail_fast=False)

    err = capsys.readouterr().err
    assert err.count("understand-anything external tool was removed") == 1
    assert all(r["status"] == "skipped" for r in results)


def test_run_tool_configs_reports_failure_without_raising_when_not_fail_fast(tmp_path):
    project = tmp_path / "demo"
    project.mkdir()
    tools = [
        {
            "id": "custom-tool",
            "name": "Custom Tool",
            "refresh_command": "definitely_missing_custom_command",
            "auto_refresh": True,
            "enabled": True,
        }
    ]

    results = run_tool_configs(project, tools, only_auto=True, fail_fast=False)

    assert results[0]["id"] == "custom-tool"
    assert results[0]["status"] == "failed"
    assert results[0]["returncode"] != 0


def test_compile_auto_refreshes_configured_external_tools(tmp_path, capsys):
    project = tmp_path / "demo"
    project.mkdir()
    (project / "README.md").write_text(
        "# Demo\nGaussian Splatting supports novel view synthesis.\n", encoding="utf-8"
    )
    assert _run_setup(project=project, overrides={"no_color": True}) == 0
    command = (
        "python3 -c \"from pathlib import Path; p=Path('.external-marker');"
        " p.mkdir(exist_ok=True); (p/'artifact.json').write_text('{}')\""
    )
    _write_external_tool(
        project,
        {
            "id": "custom-tool",
            "name": "Custom Tool",
            "refresh_command": command,
            "auto_refresh": True,
            "enabled": True,
        },
    )

    assert main(["compile", "--project", str(project), "--limit", "1"]) == 0

    assert (project / ".external-marker" / "artifact.json").exists()
    assert "Refreshed external tools" in capsys.readouterr().out


def test_compile_warns_and_continues_when_auto_refresh_command_is_missing(tmp_path, capsys):
    project = tmp_path / "demo"
    project.mkdir()
    (project / "README.md").write_text(
        "# Demo\nGaussian Splatting supports novel view synthesis.\n", encoding="utf-8"
    )
    assert _run_setup(project=project, overrides={"no_color": True}) == 0
    _write_external_tool(
        project,
        {
            "id": "custom-tool",
            "name": "Custom Tool",
            "refresh_command": "definitely_missing_custom_command",
            "auto_refresh": True,
            "enabled": True,
        },
    )

    assert main(["compile", "--project", str(project), "--limit", "1"]) == 0

    out = capsys.readouterr().out
    assert "External tool" in out and "warnings" in out
    assert "definitely_missing_custom_command" in out
    assert "Compiled project wiki" in out
    assert (project / ".tesserae" / "graph.json").exists()


def test_render_setup_summary_contains_ansi_when_color_enabled(tmp_path):
    project = tmp_path / "demo"
    project.mkdir()
    plan = build_setup_plan(project, sources=["README.md"])

    rendered = render_setup_summary(plan, color=True)

    assert "\x1b[" in rendered
    assert "README.md" in rendered
