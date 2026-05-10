import json

from llm_wiki.cli import main
from llm_wiki.project_setup import build_setup_plan, render_setup_summary, expand_tool_command


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

    assert plan.sources == ["README.md", "docs", "src", ".llm-wiki/external/understand-anything.md"]
    assert plan.external_tools[0]["id"] == "understand-anything"
    assert plan.external_tools[0]["artifact"] == ".understand-anything/knowledge-graph.json"
    assert plan.external_tools[0]["source"] == ".llm-wiki/external/understand-anything.md"
    assert plan.external_tools[0]["auto_refresh"] is True
    assert plan.external_tools[0]["sync_mode"] == "native_graph"
    assert plan.external_tools[0]["preserve_markdown_projection"] is True
    assert plan.external_tools[0]["managed_refresh"] is True
    assert "llm_wiki.understand_anything_refresh" in plan.external_tools[0]["refresh_command"]


def test_managed_understand_anything_refresh_command_expands_to_current_python(tmp_path):
    project = tmp_path / "demo"
    project.mkdir()

    plan = build_setup_plan(project, include_understand_anything=True, understand_anything_platform="opencode")
    tool = plan.external_tools[0]
    command = expand_tool_command(tool["refresh_command"], project, tool)

    assert "llm_wiki.understand_anything_refresh" in command
    assert f"--project {project}" in command
    assert "--platform opencode" in command

def test_setup_command_yes_writes_config_with_external_tool_metadata(tmp_path, capsys):
    project = tmp_path / "demo"
    project.mkdir()
    (project / "README.md").write_text("# Demo\n", encoding="utf-8")
    ua = project / ".understand-anything"
    ua.mkdir()
    (ua / "knowledge-graph.json").write_text('{"nodes": [], "edges": []}\n', encoding="utf-8")

    code = main([
        "project",
        "setup",
        "--project",
        str(project),
        "--yes",
        "--with-understand-anything",
        "--name",
        "demo_wiki",
    ])

    assert code == 0
    cfg = json.loads((project / ".llm-wiki" / "config.json").read_text(encoding="utf-8"))
    assert cfg["sources"] == ["README.md", ".llm-wiki/external/understand-anything.md"]
    assert cfg["setup"]["wizard"] == "llm_wiki project setup"
    assert cfg["external_tools"][0]["id"] == "understand-anything"
    assert cfg["external_tools"][0]["install"]["enabled"] is True
    assert cfg["external_tools"][0]["auto_refresh"] is True
    assert cfg["external_tools"][0]["sync_mode"] == "native_graph"
    assert cfg["external_tools"][0]["preserve_markdown_projection"] is True
    assert cfg["external_tools"][0]["managed_refresh"] is True
    assert "llm_wiki.understand_anything_refresh" in cfg["external_tools"][0]["refresh_command"]
    assert "install.sh" in cfg["external_tools"][0]["install"]["command"]
    assert (project / ".llm-wiki" / "external" / "understand-anything.md").exists()
    out = capsys.readouterr().out
    assert "LLM-Wiki setup" in out
    assert "Understand Anything" in out


def test_setup_installs_understand_anything_when_requested(tmp_path, monkeypatch, capsys):
    project = tmp_path / "demo"
    project.mkdir()
    (project / "README.md").write_text("# Demo\n", encoding="utf-8")
    calls = []

    def fake_run_tool_configs(project_root, tools, *, only_auto=True, fail_fast=True, run_installers=False):
        calls.append((project_root, tools, only_auto, fail_fast, run_installers))
        return [{"id": "understand-anything", "status": "installed", "command": tools[0]["install"]["command"]}]

    monkeypatch.setattr("llm_wiki.project_setup.run_tool_configs", fake_run_tool_configs)

    assert main([
        "project",
        "setup",
        "--project",
        str(project),
        "--yes",
        "--with-understand-anything",
        "--install-understand-anything",
        "--no-color",
    ]) == 0

    assert calls
    tools = calls[0][1]
    assert tools[0]["install"]["enabled"] is True
    assert "install.sh" in tools[0]["install"]["command"]
    assert "Understand Anything installed/updated" in capsys.readouterr().out


def test_setup_persists_config_even_when_initial_external_refresh_fails(tmp_path, capsys):
    project = tmp_path / "demo"
    project.mkdir()
    (project / "README.md").write_text("# Demo\n", encoding="utf-8")

    assert main([
        "project",
        "setup",
        "--project",
        str(project),
        "--yes",
        "--with-understand-anything",
        "--understand-anything-command",
        "definitely_missing_understand_command",
        "--run-understand-anything",
        "--skip-install-understand-anything",
        "--no-color",
    ]) == 0

    cfg = json.loads((project / ".llm-wiki" / "config.json").read_text(encoding="utf-8"))
    assert cfg["external_tools"][0]["refresh_command"] == "definitely_missing_understand_command"
    assert cfg["external_tools"][0]["auto_refresh"] is True
    assert (project / ".llm-wiki" / "external" / "understand-anything.md").exists()
    out = capsys.readouterr().out
    assert "External tool" in out and "warnings" in out
    assert "definitely_missing_understand_command" in out


def test_compile_auto_refreshes_configured_external_tools(tmp_path, capsys):
    project = tmp_path / "demo"
    project.mkdir()
    (project / "README.md").write_text("# Demo\nGaussian Splatting supports novel view synthesis.\n", encoding="utf-8")
    command = "python3 -c \"from pathlib import Path; p=Path('.understand-anything'); p.mkdir(exist_ok=True); (p/'knowledge-graph.json').write_text('{\\\"nodes\\\": [], \\\"edges\\\": []}\\n')\""

    assert main([
        "project",
        "setup",
        "--project",
        str(project),
        "--yes",
        "--with-understand-anything",
        "--understand-anything-command",
        command,
        "--run-understand-anything",
        "--skip-install-understand-anything",
        "--no-color",
    ]) == 0
    (project / ".understand-anything" / "knowledge-graph.json").unlink()

    assert main(["project", "compile", "--project", str(project), "--limit", "1"]) == 0

    assert (project / ".understand-anything" / "knowledge-graph.json").exists()
    assert "Refreshed external tools" in capsys.readouterr().out


def test_compile_warns_and_continues_when_auto_refresh_command_is_missing(tmp_path, capsys):
    project = tmp_path / "demo"
    project.mkdir()
    (project / "README.md").write_text("# Demo\nGaussian Splatting supports novel view synthesis.\n", encoding="utf-8")

    assert main([
        "project",
        "setup",
        "--project",
        str(project),
        "--yes",
        "--with-understand-anything",
        "--understand-anything-command",
        "definitely_missing_understand_command",
        "--run-understand-anything",
        "--skip-install-understand-anything",
        "--no-color",
    ]) == 0

    assert main(["project", "compile", "--project", str(project), "--limit", "1"]) == 0

    out = capsys.readouterr().out
    assert "External tool" in out and "warnings" in out
    assert "definitely_missing_understand_command" in out
    assert "Compiled project wiki" in out
    assert (project / ".llm-wiki" / "graph.json").exists()


def test_render_setup_summary_contains_ansi_when_color_enabled(tmp_path):
    project = tmp_path / "demo"
    project.mkdir()
    plan = build_setup_plan(project, sources=["README.md"], include_understand_anything=False)

    rendered = render_setup_summary(plan, color=True)

    assert "\x1b[" in rendered
    assert "README.md" in rendered
