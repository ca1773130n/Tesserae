import json
import subprocess
import sys

from llm_wiki.cli import main
from llm_wiki.project import ProjectWiki


def test_project_init_creates_llm_wiki_workspace(tmp_path):
    project = tmp_path / "demo-project"
    project.mkdir()

    wiki = ProjectWiki.init(project, name="demo_wiki", source_kind="Repository")

    assert wiki.root == project / ".llm-wiki"
    assert (wiki.root / "config.json").exists()
    assert (wiki.root / "graph.json").exists()
    assert (wiki.root / "manifest.json").exists()
    assert (wiki.root / "markdown_projection").is_dir()
    assert (wiki.root / "cognee_bundle").is_dir()
    config = json.loads((wiki.root / "config.json").read_text(encoding="utf-8"))
    assert config["name"] == "demo_wiki"
    assert config["project_root"] == str(project.resolve())
    assert config["source_kind"] == "Repository"
    assert config["graph_path"] == ".llm-wiki/graph.json"


def test_project_mcp_config_renders_absolute_hermes_snippet(tmp_path):
    project = tmp_path / "demo-project"
    project.mkdir()
    wiki = ProjectWiki.init(project, name="demo_wiki")

    snippet = wiki.render_mcp_config(server_name="demo_project_wiki", pythonpath="/opt/llm-wiki")

    assert "mcp_servers:" in snippet
    assert "demo_project_wiki:" in snippet
    assert 'command: "python3"' in snippet
    assert "llm_wiki.mcp_server" in snippet
    assert str((project / ".llm-wiki" / "graph.json").resolve()) in snippet
    assert 'PYTHONPATH: "/opt/llm-wiki"' in snippet


def test_project_ingest_updates_standard_artifacts(tmp_path):
    project = tmp_path / "demo-project"
    project.mkdir()
    docs = project / "docs"
    docs.mkdir()
    (docs / "paper.md").write_text("# Demo Paper\nGaussian Splatting supports novel view synthesis.", encoding="utf-8")
    wiki = ProjectWiki.init(project, source_kind="Paper")

    result = wiki.ingest([docs], trends=False)

    assert result["node_count"] > 0
    assert result["edge_count"] > 0
    graph = json.loads((project / ".llm-wiki" / "graph.json").read_text(encoding="utf-8"))
    assert any(node["name"] == "Demo Paper" for node in graph["nodes"])
    assert (project / ".llm-wiki" / "sqlite.db").exists()
    assert (project / ".llm-wiki" / "markdown_projection" / "index.md").exists()
    assert (project / ".llm-wiki" / "cognee_bundle" / "nodes.jsonl").exists()
    assert (project / ".llm-wiki" / "temporal_facts.jsonl").exists()
    assert (project / ".llm-wiki" / "competitive_report.md").exists()


def test_project_temporal_artifacts_include_provenance(tmp_path):
    project = tmp_path / "temporal-project"
    project.mkdir()
    (project / "note.md").write_text("# Temporal Note\nGaussian Splatting supports novel view synthesis.", encoding="utf-8")
    wiki = ProjectWiki.init(project, source_kind="Paper", sources=["note.md"])

    wiki.compile()

    facts = [json.loads(line) for line in (project / ".llm-wiki" / "temporal_facts.jsonl").read_text(encoding="utf-8").splitlines()]
    assert facts
    assert all("provenance" in fact for fact in facts)
    assert "MegaMem" in (project / ".llm-wiki" / "competitive_report.md").read_text(encoding="utf-8")


def test_project_compile_writes_graphiti_episode_export(tmp_path):
    project = tmp_path / "graphiti-project"
    project.mkdir()
    (project / "note.md").write_text("# Graphiti Note\nGaussian Splatting supports novel view synthesis.", encoding="utf-8")
    wiki = ProjectWiki.init(project, name="graphiti_demo", source_kind="Paper", sources=["note.md"])

    result = wiki.compile()

    episodes_path = project / ".llm-wiki" / "graphiti_episodes.jsonl"
    episodes = [json.loads(line) for line in episodes_path.read_text(encoding="utf-8").splitlines()]
    assert result["graphiti_episodes_path"] == str(episodes_path)
    assert episodes
    assert all(row["group_id"] == "graphiti_demo" for row in episodes)
    assert any("Graphiti Note" in row["content"] for row in episodes)


def test_cli_project_export_graphiti_writes_episode_jsonl(tmp_path, capsys):
    project = tmp_path / "graphiti-cli-project"
    project.mkdir()
    (project / "note.md").write_text("# CLI Graphiti Note\nGaussian Splatting supports novel view synthesis.", encoding="utf-8")

    assert main(["project", "init", "--project", str(project), "--name", "graphiti_cli", "--source-kind", "Paper", "--source", "note.md"]) == 0
    assert main(["project", "compile", "--project", str(project)]) == 0
    assert main(["project", "export-graphiti", "--project", str(project)]) == 0

    captured = capsys.readouterr().out
    assert "Exported Graphiti episodes" in captured
    assert (project / ".llm-wiki" / "graphiti_episodes.jsonl").exists()


def test_cli_project_sync_graphiti_dry_run_reports_episode_count(tmp_path, capsys):
    project = tmp_path / "graphiti-sync-project"
    project.mkdir()
    (project / "note.md").write_text("# Sync Graphiti Note\nGaussian Splatting supports novel view synthesis.", encoding="utf-8")

    assert main(["project", "init", "--project", str(project), "--name", "graphiti_sync", "--source-kind", "Paper", "--source", "note.md"]) == 0
    assert main(["project", "compile", "--project", str(project)]) == 0
    assert main(["project", "sync-graphiti", "--project", str(project), "--dry-run"]) == 0

    captured = capsys.readouterr().out
    assert "Graphiti dry-run" in captured
    assert "episodes=" in captured


def test_project_compile_writes_agent_harness_and_obsidian_vault(tmp_path):
    project = tmp_path / "harness-project"
    project.mkdir()
    (project / "note.md").write_text("# Harness Note\nGaussian Splatting supports novel view synthesis.", encoding="utf-8")
    wiki = ProjectWiki.init(project, name="harness_wiki", source_kind="Paper", sources=["note.md"])

    result = wiki.compile()

    assert result["agent_harness_path"] == str(project / ".llm-wiki" / "agent_harness")
    assert result["obsidian_vault_path"] == str(project / ".llm-wiki" / "obsidian_vault")
    assert (project / ".llm-wiki" / "agent_harness" / "manifest.json").exists()
    assert (project / ".llm-wiki" / "agent_harness" / "cursor" / ".cursor" / "rules" / "llm-wiki.mdc").exists()
    assert (project / ".llm-wiki" / "obsidian_vault" / ".obsidian" / "app.json").exists()
    assert (project / ".llm-wiki" / "obsidian_vault" / "index.md").exists()


def test_cli_project_export_agent_harness_and_obsidian(tmp_path, capsys):
    project = tmp_path / "harness-cli-project"
    project.mkdir()
    (project / "note.md").write_text("# CLI Harness Note\nGaussian Splatting supports novel view synthesis.", encoding="utf-8")

    assert main(["project", "init", "--project", str(project), "--name", "harness_cli", "--source-kind", "Paper", "--source", "note.md"]) == 0
    assert main(["project", "compile", "--project", str(project)]) == 0
    assert main(["project", "export-agent-harness", "--project", str(project), "--target", "claude-code", "--target", "cursor"]) == 0
    assert main(["project", "export-obsidian", "--project", str(project)]) == 0

    captured = capsys.readouterr().out
    assert "Exported agent harness" in captured
    assert "Exported Obsidian vault" in captured
    assert (project / ".llm-wiki" / "agent_harness" / "claude" / "CLAUDE.md").exists()
    assert (project / ".llm-wiki" / "obsidian_vault" / "_meta" / "dashboard.md").exists()


def test_project_compile_includes_code_graph_and_frontend_site_for_repository(tmp_path):
    project = tmp_path / "code-project"
    project.mkdir()
    src = project / "src"
    src.mkdir()
    (src / "app.py").write_text("import os\n\ndef main():\n    return os.getcwd()\n", encoding="utf-8")
    wiki = ProjectWiki.init(project, name="code_wiki", source_kind="CodeProject", sources=["src"])

    result = wiki.compile()

    assert result["site_path"] == str(project / ".llm-wiki" / "site")
    # Codex review F-11: code-graph nodes live in their own artifact, not in
    # ``graph.json``. ``graph.json`` is research-only; ``code-graph.json``
    # carries ``CodeProject`` / ``SourceFile`` / ``CodeFunction`` / etc.
    code_graph = json.loads(
        (project / ".llm-wiki" / "code-graph.json").read_text(encoding="utf-8")
    )
    code_types = {node["type"] for node in code_graph["nodes"]}
    assert {"CodeProject", "SourceFile"}.issubset(code_types)
    research_graph = json.loads(
        (project / ".llm-wiki" / "graph.json").read_text(encoding="utf-8")
    )
    research_types = {node["type"] for node in research_graph["nodes"]}
    assert research_types.isdisjoint(
        {"CodeProject", "SourceFile", "CodeClass", "CodeFunction", "CodeModule", "Dependency"}
    )
    assert (project / ".llm-wiki" / "site" / "index.html").exists()
    assert (project / ".llm-wiki" / "site" / "search-index.json").exists()


def test_cli_project_build_site_and_serve_smoke(tmp_path, capsys):
    project = tmp_path / "site-project"
    project.mkdir()
    (project / "note.md").write_text("# Site Note\nGaussian Splatting supports novel view synthesis.", encoding="utf-8")

    assert main(["project", "init", "--project", str(project), "--name", "site_wiki", "--source-kind", "Paper", "--source", "note.md"]) == 0
    assert main(["project", "compile", "--project", str(project)]) == 0
    assert main(["project", "build-site", "--project", str(project)]) == 0
    assert main(["project", "serve", "--project", str(project), "--dry-run"]) == 0

    captured = capsys.readouterr().out
    assert "Built frontend site" in captured
    assert "Frontend site ready" in captured


def test_cli_project_serve_reports_bind_errors_before_claiming_ready(tmp_path, capsys):
    project = tmp_path / "busy-port-project"
    project.mkdir()
    (project / "note.md").write_text("# Busy Port Note\nGaussian Splatting supports novel view synthesis.", encoding="utf-8")

    assert main(["project", "init", "--project", str(project), "--name", "busy_port", "--source-kind", "Paper", "--source", "note.md"]) == 0
    assert main(["project", "compile", "--project", str(project)]) == 0

    import socket

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    port = sock.getsockname()[1]
    try:
        assert main(["project", "serve", "--project", str(project), "--host", "127.0.0.1", "--port", str(port)]) == 2
    finally:
        sock.close()

    captured = capsys.readouterr()
    assert "Could not serve frontend site" in captured.err
    assert "Serving frontend site" not in captured.out


def test_cli_project_init_ingest_and_mcp_config_from_working_directory(tmp_path, capsys):
    project = tmp_path / "demo-project"
    project.mkdir()
    note = project / "note.md"
    note.write_text("# Project Note\nGaussian Splatting supports novel view synthesis.", encoding="utf-8")

    assert main(["project", "init", "--project", str(project), "--name", "demo_wiki", "--source-kind", "Paper"]) == 0
    assert main(["project", "ingest", "--project", str(project), "note.md"]) == 0
    assert main(["project", "mcp-config", "--project", str(project), "--server-name", "demo_wiki"]) == 0

    captured = capsys.readouterr().out
    assert "Initialized project wiki" in captured
    assert "Ingested project wiki" in captured
    assert "mcp_servers:" in captured
    assert "demo_wiki:" in captured
    assert (project / ".llm-wiki" / "graph.json").exists()


def test_project_compile_scans_configured_sources_and_changed_only(tmp_path):
    project = tmp_path / "compile-project"
    project.mkdir()
    docs = project / "docs"
    docs.mkdir()
    (docs / "paper.md").write_text("# Compile Paper\nGaussian Splatting supports novel view synthesis.", encoding="utf-8")
    wiki = ProjectWiki.init(project, source_kind="Paper", sources=["docs"])

    first = wiki.compile(changed_only=True)
    second = wiki.compile(changed_only=True)

    assert first["processed_files"] == 1
    assert first["node_count"] > 0
    assert second["processed_files"] == 0
    assert second["skipped_files"] == 1
    graph = json.loads((project / ".llm-wiki" / "graph.json").read_text(encoding="utf-8"))
    assert any(node["name"] == "Compile Paper" for node in graph["nodes"])


def test_cli_project_compile_uses_configured_sources(tmp_path, capsys):
    project = tmp_path / "compile-cli-project"
    project.mkdir()
    docs = project / "docs"
    docs.mkdir()
    (docs / "paper.md").write_text("# CLI Compile Paper\nGaussian Splatting supports novel view synthesis.", encoding="utf-8")

    assert main(["project", "init", "--project", str(project), "--name", "compile_wiki", "--source-kind", "Paper", "--source", "docs"]) == 0
    assert main(["project", "compile", "--project", str(project), "--changed-only"]) == 0

    captured = capsys.readouterr().out
    assert "Compiled project wiki" in captured
    graph = json.loads((project / ".llm-wiki" / "graph.json").read_text(encoding="utf-8"))
    assert any(node["name"] == "CLI Compile Paper" for node in graph["nodes"])


def test_cli_module_can_init_from_current_working_directory(tmp_path):
    project = tmp_path / "cwd-project"
    project.mkdir()
    result = subprocess.run(
        [sys.executable, "-m", "llm_wiki.cli", "project", "init", "--name", "cwd_wiki"],
        cwd=project,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={"PYTHONPATH": "/Users/neo/Developer/Projects/LLM-Wiki"},
        timeout=20,
    )

    assert result.returncode == 0, result.stderr
    assert (project / ".llm-wiki" / "config.json").exists()
    assert "Initialized project wiki" in result.stdout
