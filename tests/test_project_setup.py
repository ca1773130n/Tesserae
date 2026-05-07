import json

from llm_wiki.cli import main
from llm_wiki.project_setup import build_setup_plan, render_setup_summary


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
    assert (project / ".llm-wiki" / "external" / "understand-anything.md").exists()
    out = capsys.readouterr().out
    assert "LLM-Wiki setup" in out
    assert "Understand Anything" in out


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
        "--no-color",
    ]) == 0
    (project / ".understand-anything" / "knowledge-graph.json").unlink()

    assert main(["project", "compile", "--project", str(project), "--limit", "1"]) == 0

    assert (project / ".understand-anything" / "knowledge-graph.json").exists()
    assert "Refreshed external tools" in capsys.readouterr().out


def test_render_setup_summary_contains_ansi_when_color_enabled(tmp_path):
    project = tmp_path / "demo"
    project.mkdir()
    plan = build_setup_plan(project, sources=["README.md"], include_understand_anything=False)

    rendered = render_setup_summary(plan, color=True)

    assert "\x1b[" in rendered
    assert "README.md" in rendered
