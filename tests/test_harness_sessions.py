import json

from llm_wiki.cli import main
from llm_wiki.harness_sessions import HarnessSession, HarnessSessionStore
from llm_wiki.project import ProjectWiki
from llm_wiki.research_graph import ResearchGraph
from llm_wiki.site import StaticSiteBuilder


def sample_session(project_root):
    return HarnessSession(
        id="claude-code:2026-05-05-project-memory",
        slug="project-memory",
        harness="claude-code",
        agent_label="Claude Code",
        project_name="demo-project",
        project_root=str(project_root),
        started_at="2026-05-05T10:00:00Z",
        ended_at="2026-05-05T10:42:00Z",
        branch="main",
        model="claude-sonnet-4-6",
        title="Project memory ingestion",
        summary="Discussed ingesting agent harness session history into LLM-Wiki.",
        message_count=4,
        tool_call_count=7,
        token_total=12345,
        tools_used=["Read", "Write", "Bash"],
        files_touched=["llm_wiki/project.py", "llm_wiki/site/__init__.py"],
        commands_run=["pytest tests/test_harness_sessions.py -q"],
        decisions=["Treat harness sessions as first-class project memory."],
        redacted_preview="User asked to add harness session history pages.",
    )


def test_harness_session_store_writes_manifest_and_json(tmp_path):
    project = tmp_path / "demo-project"
    project.mkdir()
    store = HarnessSessionStore(project / ".llm-wiki" / "harness_sessions")
    session = sample_session(project)

    written = store.write_sessions([session])

    assert written["sessions"] == 1
    manifest = json.loads((store.root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["sessions"][0]["title"] == "Project memory ingestion"
    assert manifest["sessions"][0]["href"] == "sessions/demo-project/2026-05-05-project-memory.html"
    payload = json.loads((store.root / "claude-code" / "2026-05-05-project-memory.json").read_text(encoding="utf-8"))
    assert payload["harness"] == "claude-code"
    assert payload["tools_used"] == ["Read", "Write", "Bash"]


def test_static_site_renders_harness_sessions_and_search_entries(tmp_path):
    project = tmp_path / "demo-project"
    project.mkdir()
    wiki = ProjectWiki.init(project, name="demo_project", source_kind="Repository")
    HarnessSessionStore(project / ".llm-wiki" / "harness_sessions").write_sessions([sample_session(project)])

    result = StaticSiteBuilder(site_title="LLM-Wiki").write_site(
        ResearchGraph(), wiki.paths.wiki, wiki.paths.site
    )

    assert result["sessions"] == 1
    sessions_index = wiki.paths.site / "sessions" / "index.html"
    detail = wiki.paths.site / "sessions" / "demo-project" / "2026-05-05-project-memory.html"
    assert sessions_index.exists()
    assert detail.exists()
    assert "Project memory ingestion" in sessions_index.read_text(encoding="utf-8")
    detail_html = detail.read_text(encoding="utf-8")
    assert "Treat harness sessions as first-class project memory." in detail_html
    assert "llm_wiki/project.py" in detail_html

    search = json.loads((wiki.paths.site / "search-index.json").read_text(encoding="utf-8"))
    session_entries = [entry for entry in search if entry["kind"] == "session"]
    assert len(session_entries) == 1
    assert session_entries[0]["type"] == "session"
    assert session_entries[0]["project"] == "demo-project"
    assert session_entries[0]["model"] == "claude-sonnet-4-6"
    assert session_entries[0]["href"] == "sessions/demo-project/2026-05-05-project-memory.html"


def test_cli_project_sessions_import_and_list(tmp_path, capsys):
    project = tmp_path / "demo-project"
    project.mkdir()
    ProjectWiki.init(project, name="demo_project", source_kind="Repository")
    session_file = tmp_path / "session.json"
    session_file.write_text(json.dumps(sample_session(project).to_dict()), encoding="utf-8")

    assert main(["project", "sessions", "import", "--project", str(project), str(session_file)]) == 0
    assert main(["project", "sessions", "list", "--project", str(project)]) == 0

    captured = capsys.readouterr().out
    assert "Imported harness sessions: 1" in captured
    assert "Project memory ingestion" in captured
    assert "claude-code" in captured
