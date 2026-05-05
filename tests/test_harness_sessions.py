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
        metadata={
            "turns": [
                {"role": "user", "timestamp": "2026-05-05T10:00:00Z", "text": "Please ingest Claude Code and Codex sessions from llm_wiki/project.py for #project-memory.\n\n<command-name>/effort</command-name> <command-message>effort</command-message> <command-args></command-args>"},
                {"role": "assistant", "timestamp": "2026-05-05T10:01:00Z", "text": "I will add **normalized** `project-memory` session pages.\n\n- Render sessions\n- Index turns\n\n```python\ndef build_session():\n    return 42\n```\n\n```sh\nllm-wiki project build-site --project .\n```"},
                {"role": "tool", "timestamp": "2026-05-05T10:02:00Z", "name": "Read", "text": "{\"ok\": true, \"count\": 2}"},
                {"role": "assistant", "timestamp": "2026-05-05T10:42:00Z", "text": "Implemented session import and static pages. <status>ready</status>"},
            ]
        },
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
    expected_href = session.href
    assert manifest["sessions"][0]["href"] == expected_href
    payload = json.loads((store.root / "claude-code" / f"{session.filename}.json").read_text(encoding="utf-8"))
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
    home = (wiki.paths.site / "index.html").read_text(encoding="utf-8")
    assert 'href="sessions/index.html"' in home
    assert "Sessions" in home
    sessions_index = wiki.paths.site / "sessions" / "index.html"
    detail = wiki.paths.site / "sessions" / "demo-project" / f"{sample_session(project).filename}.html"
    assert sessions_index.exists()
    assert detail.exists()
    index_html = sessions_index.read_text(encoding="utf-8")
    assert 'href="../sessions/index.html"' in index_html
    assert "All sessions" in index_html
    assert "Project memory ingestion" in index_html
    detail_html = detail.read_text(encoding="utf-8")
    assert "session-hero" in detail_html
    assert "Session Summary" in detail_html
    assert "High-Level Summary" in detail_html
    assert "Main outcome" in detail_html
    assert "Timeline &amp; size" in detail_html
    assert "Treat harness sessions as first-class project memory." in detail_html
    assert "llm_wiki/project.py" in detail_html
    assert "Turn-by-turn conversation" in detail_html
    assert "session-turn-list" in detail_html
    assert "id='turn-1'" in detail_html
    assert "id='turn-3'" in detail_html
    assert "session-turn-nav" in detail_html
    assert "Conversation turns" in detail_html
    assert "href=\"#turn-1\"" in detail_html
    assert "All sessions" in detail_html
    assert "session-rail-back" in detail_html
    assert "href=\"../index.html\"" in detail_html
    assert "data-session-turn-target=\"turn-1\"" in detail_html
    assert "session-reference-card" not in detail_html
    assert "Reference project" not in detail_html
    assert "main main--session" in detail_html
    assert "shell shell--session" in detail_html
    assert "href=\"#turn-3\"" in detail_html
    assert "Please ingest" in detail_html
    assert "session-token session-token--path'>llm_wiki/project.py</span>" in detail_html
    assert "session-token session-token--tag'>#project-memory</span>" in detail_html
    assert "session-token--noun" not in detail_html
    assert "session-turn-nav--user" in detail_html
    assert "session-turn-nav--assistant" in detail_html
    assert "session-command-chip" in detail_html
    assert "session-command-name'>/effort</span>" in detail_html
    assert "session-command-message'>effort</span>" in detail_html
    assert "&lt;command-name&gt;" not in detail_html
    assert "session-tag-block" in detail_html
    assert "session-tag-name'>status</span>" in detail_html
    assert "session-tag-content'>ready</span>" in detail_html
    assert "&lt;status&gt;" not in detail_html
    assert "I will add <strong>normalized</strong> <code>project-memory</code> session pages." in detail_html
    assert "<li>Render sessions</li>" in detail_html
    assert "session-code-block" in detail_html
    assert "session-code-lang'>python</span>" in detail_html
    assert "session-code-keyword'>def</span> build_session" in detail_html
    assert "session-code-keyword'>return</span> <span class='session-code-number'>42</span>" in detail_html
    assert "session-code-command'>llm-wiki</span>" in detail_html
    assert "session-code-flag'>--project</span>" in detail_html
    assert "session-tool-details" in detail_html
    assert "Tool use (1)" in detail_html
    assert "session-tool-use-text" in detail_html
    assert "data-lang='json'" in detail_html
    assert "{\n  <span class='session-code-string'>&quot;ok&quot;</span>" in detail_html
    assert "session-code-string'>&quot;ok&quot;</span>" in detail_html
    assert "session-code-keyword'>true</span>" in detail_html
    assert "session-code-number'>2</span>" in detail_html
    rail_html = detail_html.split("<nav class='session-turn-nav'", 1)[1].split("</nav>", 1)[0]
    assert "Tool · Read" not in rail_html
    assert "&quot;ok&quot;" not in rail_html
    assert "Source explorer" not in detail_html

    search = json.loads((wiki.paths.site / "search-index.json").read_text(encoding="utf-8"))
    session_entries = [entry for entry in search if entry["kind"] == "session"]
    assert len(session_entries) == 1
    assert session_entries[0]["type"] == "session"
    assert session_entries[0]["project"] == "demo-project"
    assert session_entries[0]["model"] == "claude-sonnet-4-6"
    assert session_entries[0]["href"] == sample_session(project).href


def test_static_site_renders_subagent_history_collapsed_under_parent(tmp_path):
    project = tmp_path / "demo-project"
    project.mkdir()
    wiki = ProjectWiki.init(project, name="demo_project", source_kind="Repository")
    parent = HarnessSession.from_dict({
        **sample_session(project).to_dict(),
        "metadata": {
            "subagents": [
                {
                    "id": "claude-code:parent:agent-child",
                    "title": "Child subagent session",
                    "started_at": "2026-05-05T10:05:00Z",
                    "message_count": 2,
                    "tool_call_count": 3,
                    "summary": "Subagent investigated frontend links.",
                    "files_touched": ["llm_wiki/site/sessions.py"],
                    "commands_run": ["pytest tests/test_harness_sessions.py -q"],
                    "raw_transcript_path": "/tmp/parent/subagents/agent-child.jsonl",
                }
            ]
        },
    })
    HarnessSessionStore(project / ".llm-wiki" / "harness_sessions").write_sessions([parent])

    StaticSiteBuilder(site_title="LLM-Wiki").write_site(
        ResearchGraph(), wiki.paths.wiki, wiki.paths.site
    )

    index_html = (wiki.paths.site / "sessions" / "index.html").read_text(encoding="utf-8")
    detail_html = (wiki.paths.site / "sessions" / "demo-project" / f"{parent.filename}.html").read_text(encoding="utf-8")
    assert "Subagents" in index_html
    assert "1 subagent" in index_html
    assert "<details" in detail_html
    assert "Subagent sessions (1)" in detail_html
    assert "Child subagent session" in detail_html
    assert "Subagent investigated frontend links." in detail_html
    assert "llm_wiki/site/sessions.py" in detail_html


def test_harness_sessions_with_same_date_and_title_get_distinct_pages(tmp_path):
    project = tmp_path / "demo-project"
    project.mkdir()
    wiki = ProjectWiki.init(project, name="demo_project", source_kind="Repository")
    base = sample_session(project)
    other = HarnessSession.from_dict({**base.to_dict(), "id": "claude-code:other", "raw_transcript_path": "/tmp/other.jsonl"})
    HarnessSessionStore(project / ".llm-wiki" / "harness_sessions").write_sessions([base, other])

    StaticSiteBuilder(site_title="LLM-Wiki").write_site(
        ResearchGraph(), wiki.paths.wiki, wiki.paths.site
    )

    pages = list((wiki.paths.site / "sessions" / "demo-project").glob("*.html"))
    assert len(pages) == 2
    search = json.loads((wiki.paths.site / "search-index.json").read_text(encoding="utf-8"))
    assert len([entry for entry in search if entry["kind"] == "session"]) == 2


def test_cli_project_sessions_import_filters_other_project_sessions(tmp_path, capsys):
    project = tmp_path / "demo-project"
    other_project = tmp_path / "other-project"
    project.mkdir()
    other_project.mkdir()
    ProjectWiki.init(project, name="demo_project", source_kind="Repository")
    focused = sample_session(project)
    foreign = HarnessSession.from_dict({
        **sample_session(other_project).to_dict(),
        "id": "claude-code:foreign",
        "title": "Foreign project session",
        "project_root": str(other_project),
    })
    session_file = tmp_path / "sessions.json"
    session_file.write_text(json.dumps([focused.to_dict(), foreign.to_dict()]), encoding="utf-8")

    assert main(["project", "sessions", "import", "--project", str(project), str(session_file)]) == 0
    assert main(["project", "sessions", "list", "--project", str(project)]) == 0

    captured = capsys.readouterr().out
    assert "Imported harness sessions: 1" in captured
    assert "Skipped non-project harness sessions: 1" in captured
    assert "Project memory ingestion" in captured
    assert "Foreign project session" not in captured
