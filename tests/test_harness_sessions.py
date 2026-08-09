import json
import threading

from tesserae.cli import main
from tesserae.harness_sessions import (
    HarnessSession,
    HarnessSessionStore,
    PRODUCER_DISCOVERY,
    PRODUCER_IMPORT,
    _is_boilerplate_preamble,
    _title_and_preview,
)
from tesserae.project import ProjectWiki
from tesserae.research_graph import ResearchGraph
from tesserae.site import StaticSiteBuilder


def test_title_skips_harness_injected_preamble():
    # Codex prepends "# AGENTS.md instructions for <path>" to every session;
    # without skipping it, all sessions collapse to one title and keyword
    # search returns indistinguishable boilerplate (the bug this guards).
    texts = [
        "# AGENTS.md instructions for /Users/x/proj\n## CodeGraph\nThis project...",
        "You are grd-hypothesizer. Generate ONE ranked hypothesis about 3DGS compression.",
    ]
    title, preview = _title_and_preview(texts)
    assert title.startswith("You are grd-hypothesizer")
    assert "AGENTS.md instructions" not in title
    assert preview.startswith("You are grd-hypothesizer")


def test_title_preamble_detection_and_fallbacks():
    assert _is_boilerplate_preamble("# AGENTS.md instructions for /x")
    assert _is_boilerplate_preamble("# CLAUDE.md instructions for /y")
    assert _is_boilerplate_preamble("<system-reminder>do X</system-reminder>")
    assert not _is_boilerplate_preamble("Fix the scheduler rate-limit bug")
    # all-boilerplate input still yields a non-empty title (graceful fallback)
    title, _ = _title_and_preview(["# CLAUDE.md instructions for /z"])
    assert title
    # a normal first message is preserved unchanged
    title2, _ = _title_and_preview(["Refactor the retrieval pipeline"])
    assert title2.startswith("Refactor the retrieval")


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
        summary="Discussed ingesting agent harness session history into Tesserae.",
        message_count=4,
        tool_call_count=7,
        token_total=12345,
        tools_used=["Read", "Write", "Bash"],
        files_touched=["tesserae/project.py", "tesserae/site/__init__.py"],
        commands_run=["pytest tests/test_harness_sessions.py -q"],
        decisions=["Treat harness sessions as first-class project memory."],
        redacted_preview="User asked to add harness session history pages.",
        metadata={
            "turns": [
                {"role": "user", "timestamp": "2026-05-05T10:00:00Z", "text": "Please ingest Claude Code and Codex sessions from tesserae/project.py for #project-memory.\n\n<command-name>/effort</command-name> <command-message>effort</command-message> <command-args></command-args>"},
                {"role": "assistant", "timestamp": "2026-05-05T10:01:00Z", "text": "I will add **normalized** `project-memory` session pages.\n\n- Render sessions\n- Index turns\n\n```python\ndef build_session():\n    return 42\n```\n\n```sh\ntesserae export site --project .\n```"},
                {"role": "tool", "timestamp": "2026-05-05T10:02:00Z", "name": "Read", "text": "{\"ok\": true, \"count\": 2}"},
                {"role": "assistant", "timestamp": "2026-05-05T10:42:00Z", "text": "Implemented session import and static pages. <status>ready</status>"},
            ]
        },
    )


def test_harness_session_store_writes_manifest_and_json(tmp_path):
    project = tmp_path / "demo-project"
    project.mkdir()
    store = HarnessSessionStore(project / ".tesserae" / "harness_sessions")
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
    HarnessSessionStore(project / ".tesserae" / "harness_sessions").write_sessions([sample_session(project)])

    result = StaticSiteBuilder(site_title="Tesserae").write_site(
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
    assert "tesserae/project.py" in detail_html
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
    assert "session-token session-token--path'>tesserae/project.py</span>" in detail_html
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
    assert "session-code-command'>tesserae</span>" in detail_html
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
                    "files_touched": ["tesserae/site/sessions.py"],
                    "commands_run": ["pytest tests/test_harness_sessions.py -q"],
                    "raw_transcript_path": "/tmp/parent/subagents/agent-child.jsonl",
                }
            ]
        },
    })
    HarnessSessionStore(project / ".tesserae" / "harness_sessions").write_sessions([parent])

    StaticSiteBuilder(site_title="Tesserae").write_site(
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
    assert "tesserae/site/sessions.py" in detail_html


def test_harness_sessions_with_same_date_and_title_get_distinct_pages(tmp_path):
    project = tmp_path / "demo-project"
    project.mkdir()
    wiki = ProjectWiki.init(project, name="demo_project", source_kind="Repository")
    base = sample_session(project)
    other = HarnessSession.from_dict({**base.to_dict(), "id": "claude-code:other", "raw_transcript_path": "/tmp/other.jsonl"})
    HarnessSessionStore(project / ".tesserae" / "harness_sessions").write_sessions([base, other])

    StaticSiteBuilder(site_title="Tesserae").write_site(
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

    assert main(["sessions", "import", "--project", str(project), str(session_file)]) == 0
    assert main(["sessions", "list", "--project", str(project)]) == 0

    captured = capsys.readouterr().out
    assert "Imported harness sessions: 1" in captured
    assert "Skipped non-project harness sessions: 1" in captured
    assert "Project memory ingestion" in captured
    assert "Foreign project session" not in captured


# ---------------------------------------------------------------------------
# write_sessions merge-by-default (item-6 rider): an empty import / empty
# discover must never wipe the store; replace=True keeps the authoritative
# prune-stale semantics.
# ---------------------------------------------------------------------------


def test_write_sessions_merges_by_default(tmp_path):
    project = tmp_path / "demo-project"
    project.mkdir()
    store = HarnessSessionStore(project / ".tesserae" / "harness_sessions")
    first = sample_session(project)
    store.write_sessions([first])

    second = HarnessSession.from_dict({
        **sample_session(project).to_dict(),
        "id": "claude-code:second",
        "slug": "second-session",
        "title": "Second session",
        "started_at": "2026-05-06T10:00:00Z",
    })
    result = store.write_sessions([second])

    # Both records survive: merge added the new one without deleting the old.
    assert result["sessions"] == 1
    assert result["total"] == 2
    listed = store.list_sessions()
    assert {s.title for s in listed} == {"Project memory ingestion", "Second session"}
    manifest = json.loads((store.root / "manifest.json").read_text(encoding="utf-8"))
    assert len(manifest["sessions"]) == 2


def test_write_sessions_empty_write_is_a_noop_not_a_wipe(tmp_path):
    project = tmp_path / "demo-project"
    project.mkdir()
    store = HarnessSessionStore(project / ".tesserae" / "harness_sessions")
    store.write_sessions([sample_session(project)])

    result = store.write_sessions([])  # empty import/discover

    assert result["sessions"] == 0
    assert result["total"] == 1
    assert len(store.list_sessions()) == 1


def test_write_sessions_replace_prunes_stale_records(tmp_path):
    project = tmp_path / "demo-project"
    project.mkdir()
    store = HarnessSessionStore(project / ".tesserae" / "harness_sessions")
    store.write_sessions([sample_session(project)])

    replacement = HarnessSession.from_dict({
        **sample_session(project).to_dict(),
        "id": "claude-code:replacement",
        "slug": "replacement-session",
        "title": "Replacement session",
    })
    result = store.write_sessions([replacement], replace=True)

    assert result["sessions"] == 1
    listed = store.list_sessions()
    assert [s.title for s in listed] == ["Replacement session"]


def test_write_sessions_replace_spares_records_outside_prune_roots(tmp_path):
    """Regression (#104): a local discovery must not prune another producer's records.

    `sessions discover --import` replaces with the scanned harness roots as its
    prune scope. A session imported through `sessions import <path>` carries no
    transcript under those roots, so it is out of scope and survives.
    """
    project = tmp_path / "demo-project"
    project.mkdir()
    harness_root = tmp_path / "home" / ".claude"
    (harness_root / "projects").mkdir(parents=True)
    store = HarnessSessionStore(project / ".tesserae" / "harness_sessions")

    external = HarnessSession.from_dict({
        **sample_session(project).to_dict(),
        "id": "external:orchestrator-run",
        "slug": "orchestrator-run",
        "title": "Externally imported session",
        "raw_transcript_path": "",
    })
    store.write_sessions([external])  # `tesserae sessions import <path>` semantics

    stale = HarnessSession.from_dict({
        **sample_session(project).to_dict(),
        "id": "claude-code:stale",
        "slug": "stale-session",
        "title": "Stale local session",
        "raw_transcript_path": str(harness_root / "projects" / "demo" / "stale.jsonl"),
    })
    store.write_sessions([stale], replace=True, prune_roots=[harness_root])

    discovered = HarnessSession.from_dict({
        **sample_session(project).to_dict(),
        "id": "claude-code:fresh",
        "slug": "fresh-session",
        "title": "Fresh local session",
        "raw_transcript_path": str(harness_root / "projects" / "demo" / "fresh.jsonl"),
    })
    result = store.write_sessions([discovered], replace=True, prune_roots=[harness_root])

    titles = {s.title for s in store.list_sessions()}
    assert titles == {"Externally imported session", "Fresh local session"}
    assert result["removed"] == 1  # the stale local record, and only it
    assert result["total"] == 2
    manifest = json.loads((store.root / "manifest.json").read_text(encoding="utf-8"))
    assert len(manifest["sessions"]) == 2


def _record(project, harness, slug, title, transcript=""):
    return HarnessSession.from_dict({
        **sample_session(project).to_dict(),
        "id": f"{harness}:{slug}", "slug": slug, "harness": harness, "title": title,
        "raw_transcript_path": transcript,
    })


def test_replace_spares_a_harness_the_scan_filtered_out(tmp_path):
    """`discover --harness codex --import` must not delete claude-code records.

    --harness narrows what is scanned but roots are discovered before the filter
    applies, so a codex-only run still carries ~/.claude in its prune scope. Root
    alone is not the scope; (root AND harness) is.
    """
    project = tmp_path / "demo-project"
    project.mkdir()
    claude_root, codex_root = tmp_path / "home" / ".claude", tmp_path / "home" / ".codex"
    (claude_root / "projects").mkdir(parents=True)
    (codex_root / "sessions").mkdir(parents=True)
    store = HarnessSessionStore(project / ".tesserae" / "harness_sessions")

    store.write_sessions([_record(project, "claude-code", "claude-one", "Claude record",
                                  str(claude_root / "projects" / "p" / "a.jsonl"))])

    codex = _record(project, "codex", "codex-one", "Codex record",
                    str(codex_root / "sessions" / "b.jsonl"))
    result = store.write_sessions([codex], replace=True,
                                  prune_roots=[claude_root, codex_root],
                                  prune_harnesses=["codex"])

    assert {s.title for s in store.list_sessions()} == {"Claude record", "Codex record"}
    assert result["removed"] == 0


def test_replace_spares_a_record_it_cannot_read(tmp_path):
    """A record mid-write by another producer parses as corrupt. Unknown owner
    means not ours to delete — deleting here would recreate #104 by a narrower
    door. An orphaned page with no record behind it is still swept."""
    project = tmp_path / "demo-project"
    project.mkdir()
    root = tmp_path / "home" / ".claude"
    (root / "projects").mkdir(parents=True)
    store = HarnessSessionStore(project / ".tesserae" / "harness_sessions")
    store.write_sessions([_record(project, "claude-code", "corrupt-one", "Corrupt record",
                                  str(root / "projects" / "p" / "c.jsonl"))])

    corrupt = next(store.root.glob("*/*.json"))
    corrupt.write_text("{ this is not json", encoding="utf-8")
    orphan = corrupt.parent / "2026-05-05-orphan-page.md"
    orphan.write_text("# a page whose record is gone\n", encoding="utf-8")

    fresh = _record(project, "claude-code", "fresh-one", "Fresh record",
                    str(root / "projects" / "p" / "f.jsonl"))
    store.write_sessions([fresh], replace=True, prune_roots=[root])

    assert corrupt.exists(), "unparseable record was deleted; owner was unknown"
    assert not orphan.exists(), "page with no record behind it should be swept"


def test_replace_survives_a_transcript_path_that_cannot_resolve(tmp_path):
    """An embedded NUL raises ValueError out of Path.resolve(), a symlink loop
    raises RuntimeError. Either would abort write_sessions after the new files
    are written but before the manifest is rebuilt, leaving the two disagreeing."""
    project = tmp_path / "demo-project"
    project.mkdir()
    root = tmp_path / "home" / ".claude"
    (root / "projects").mkdir(parents=True)
    store = HarnessSessionStore(project / ".tesserae" / "harness_sessions")
    store.write_sessions([_record(project, "claude-code", "bad-path", "Unresolvable record",
                                  "/tmp/a\x00b.jsonl")])

    fresh = _record(project, "claude-code", "fresh-one", "Fresh record",
                    str(root / "projects" / "p" / "f.jsonl"))
    result = store.write_sessions([fresh], replace=True, prune_roots=[root])

    assert {s.title for s in store.list_sessions()} == {"Unresolvable record", "Fresh record"}
    manifest = json.loads((store.root / "manifest.json").read_text(encoding="utf-8"))
    assert len(manifest["sessions"]) == result["total"] == 2


def test_cli_sessions_import_with_no_paths_leaves_existing_sessions_intact(tmp_path, capsys):
    """Regression: `tesserae sessions import` (no paths) used to WIPE the store."""
    project = tmp_path / "demo-project"
    project.mkdir()
    ProjectWiki.init(project, name="demo_project", source_kind="Repository")
    store = HarnessSessionStore(project / ".tesserae" / "harness_sessions")
    store.write_sessions([sample_session(project)])

    assert main(["sessions", "import", "--project", str(project)]) == 0
    capsys.readouterr()

    assert len(store.list_sessions()) == 1
    manifest = json.loads((store.root / "manifest.json").read_text(encoding="utf-8"))
    assert len(manifest["sessions"]) == 1


def test_long_sessions_are_not_truncated_at_300_turns():
    # Regression: the old default limit=300 silently dropped everything past
    # turn 300, so the chunked LLM extractor never saw the rest of the session.
    from tesserae.harness_sessions import _claude_turns, _codex_turns

    claude_rows = [
        {
            "type": "user" if i % 2 == 0 else "assistant",
            "timestamp": f"2026-07-01T00:00:{i % 60:02d}Z",
            "message": {"content": f"turn {i}"},
        }
        for i in range(500)
    ]
    assert len(_claude_turns(claude_rows)) == 500

    codex_rows = [
        {
            "timestamp": f"2026-07-01T00:00:{i % 60:02d}Z",
            "payload": {
                "type": "message",
                "role": "user" if i % 2 == 0 else "assistant",
                "content": f"turn {i}",
            },
        }
        for i in range(500)
    ]
    assert len(_codex_turns(codex_rows)) == 500


# ---------------------------------------------------------------------------
# Provenance (#104, reopened). Root- and harness-scoping could only separate
# producers that read different directories. Two importers routinely describe
# the SAME transcript — the local scan mints a plain record from ~/.claude, an
# orchestrator exports that session with the agent identity only it knows — so
# the external record sat inside the scope by construction. Both failure modes
# below were measured against a real 375-record store on 0.28.6.
# ---------------------------------------------------------------------------

CLAUDE_ID = "claude-code:abc123"


def _same_transcript_record(project, transcript, **kw):
    payload = {
        **sample_session(project).to_dict(),
        "id": CLAUDE_ID, "slug": "a-session", "harness": "claude-code",
        "agent_label": "Claude Code", "raw_transcript_path": transcript,
    }
    payload.update(kw)
    return HarnessSession.from_dict(payload)


def _external_store(tmp_path):
    project = tmp_path / "demo-project"
    project.mkdir()
    root = tmp_path / "home" / ".claude"
    (root / "projects").mkdir(parents=True)
    store = HarnessSessionStore(project / ".tesserae" / "harness_sessions")
    transcript = str(root / "projects" / "p" / "run.jsonl")
    enriched = _same_transcript_record(
        project, transcript, agent_label="sa-apoc",
        metadata={"super_agent": "apoc", "role": "reviewer"})
    store.write_sessions([enriched], producer=PRODUCER_IMPORT)
    return project, root, store, transcript


def test_discovery_cannot_delete_another_producers_record_for_a_scanned_transcript(tmp_path):
    """Mode 1: the record's transcript is inside the scanned root — that is the
    point, both tools describe the same session — and the scan no longer finds
    it. Scope cannot save it; provenance must."""
    project, root, store, transcript = _external_store(tmp_path)

    elsewhere = _same_transcript_record(
        project, str(root / "projects" / "p" / "other.jsonl"),
        id="claude-code:zzz", slug="other")
    result = store.write_sessions([elsewhere], replace=True, prune_roots=[root],
                                  prune_harnesses=["claude-code"],
                                  producer=PRODUCER_DISCOVERY)

    assert any(s.agent_label == "sa-apoc" for s in store.list_sessions())
    assert result["removed"] == 0


def test_discovery_cannot_overwrite_another_producers_record(tmp_path):
    """Mode 2: not a pruning problem at all. Both records key to the same
    filename, so the scan's plain version silently replaced the enriched one and
    reported success — nothing was deleted, so `removed` stayed 0."""
    project, root, store, transcript = _external_store(tmp_path)

    result = store.write_sessions([_same_transcript_record(project, transcript)],
                                  replace=True, prune_roots=[root],
                                  prune_harnesses=["claude-code"],
                                  producer=PRODUCER_DISCOVERY)

    kept = next(s for s in store.list_sessions() if s.id == CLAUDE_ID)
    assert kept.agent_label == "sa-apoc"
    assert kept.metadata == {"super_agent": "apoc", "role": "reviewer"}
    assert result["preserved"] == 1


def test_a_producer_still_manages_its_own_records(tmp_path):
    """The protection must not freeze the store: discovery refreshes and prunes
    what discovery wrote."""
    project = tmp_path / "demo-project"
    project.mkdir()
    root = tmp_path / "home" / ".claude"
    (root / "projects").mkdir(parents=True)
    store = HarnessSessionStore(project / ".tesserae" / "harness_sessions")
    mine = _same_transcript_record(project, str(root / "projects" / "p" / "run.jsonl"),
                                   title="First pass")
    store.write_sessions([mine], producer=PRODUCER_DISCOVERY)

    updated = _same_transcript_record(project, str(root / "projects" / "p" / "run.jsonl"),
                                      title="Second pass, more turns")
    result = store.write_sessions([updated], replace=True, prune_roots=[root],
                                  prune_harnesses=["claude-code"],
                                  producer=PRODUCER_DISCOVERY)

    assert [s.title for s in store.list_sessions()] == ["Second pass, more turns"]
    assert result["preserved"] == 0


def test_records_predating_provenance_are_nobodys_until_adopted(tmp_path):
    """An unstamped record is unowned: no producer may prune or overwrite it,
    because there is no way to tell whose it is. `adopt_unowned` is the one-time
    migration, and it is wrong to pass when another tool shares the store."""
    project = tmp_path / "demo-project"
    project.mkdir()
    root = tmp_path / "home" / ".claude"
    (root / "projects").mkdir(parents=True)
    store = HarnessSessionStore(project / ".tesserae" / "harness_sessions")
    legacy = _same_transcript_record(project, str(root / "projects" / "p" / "run.jsonl"),
                                     title="Written before provenance existed")
    store.write_sessions([legacy])                      # no producer: unstamped
    assert store.list_sessions()[0].producer == ""

    fresh = _same_transcript_record(project, str(root / "projects" / "p" / "run.jsonl"),
                                    title="Rediscovered")
    guarded = store.write_sessions([fresh], replace=True, prune_roots=[root],
                                   producer=PRODUCER_DISCOVERY)
    assert store.list_sessions()[0].title == "Written before provenance existed"
    assert guarded["preserved"] == 1

    adopted = store.write_sessions([fresh], replace=True, prune_roots=[root],
                                   producer=PRODUCER_DISCOVERY, adopt_unowned=True)
    kept = store.list_sessions()[0]
    assert kept.title == "Rediscovered" and kept.producer == PRODUCER_DISCOVERY
    assert adopted["preserved"] == 0


# ---------------------------------------------------------------------------
# Host provenance: several machines sharing one project directory
# ---------------------------------------------------------------------------


def test_a_host_may_not_prune_a_record_it_never_harvested(tmp_path):
    """Mode 3: the host axis. N servers run Claude Code, each with its OWN
    local transcripts, all harvesting into one project directory on shared
    disk. Both stamp PRODUCER_DISCOVERY and both `~/.claude` roots resolve to
    the same string, so the producer gate and the scope gate BOTH pass — and
    host B deletes host A's session while reporting success."""
    project = tmp_path / "demo-project"
    project.mkdir()
    root = tmp_path / "home" / ".claude"
    (root / "projects").mkdir(parents=True)
    store = HarnessSessionStore(project / ".tesserae" / "harness_sessions")

    # Host A harvests a session from its own local transcript.
    from_a = _same_transcript_record(project, str(root / "projects" / "p" / "a.jsonl"),
                                     id="claude-code:aaa", slug="from-a", title="Host A work")
    store.write_sessions([from_a], producer=PRODUCER_DISCOVERY, host="host-a")

    # Host B harvests its own, different session, and prunes as it always does.
    from_b = _same_transcript_record(project, str(root / "projects" / "p" / "b.jsonl"),
                                     id="claude-code:bbb", slug="from-b", title="Host B work")
    result = store.write_sessions([from_b], replace=True, prune_roots=[root],
                                  prune_harnesses=["claude-code"],
                                  producer=PRODUCER_DISCOVERY, host="host-b")

    titles = sorted(s.title for s in store.list_sessions())
    assert titles == ["Host A work", "Host B work"], "host B deleted host A's record"
    assert result["removed"] == 0


def test_a_host_still_prunes_its_own_stale_records(tmp_path):
    """The host gate must not freeze the store either: a host reclaims what it
    harvested when the transcript is gone."""
    project = tmp_path / "demo-project"
    project.mkdir()
    root = tmp_path / "home" / ".claude"
    (root / "projects").mkdir(parents=True)
    store = HarnessSessionStore(project / ".tesserae" / "harness_sessions")

    old = _same_transcript_record(project, str(root / "projects" / "p" / "old.jsonl"),
                                  id="claude-code:old", slug="old", title="Superseded")
    store.write_sessions([old], producer=PRODUCER_DISCOVERY, host="host-a")

    new = _same_transcript_record(project, str(root / "projects" / "p" / "new.jsonl"),
                                  id="claude-code:new", slug="new", title="Current")
    result = store.write_sessions([new], replace=True, prune_roots=[root],
                                  prune_harnesses=["claude-code"],
                                  producer=PRODUCER_DISCOVERY, host="host-a")

    assert [s.title for s in store.list_sessions()] == ["Current"]
    assert result["removed"] == 1


def test_unstamped_records_survive_a_foreign_host_until_adopted(tmp_path):
    """The migration case: every record written before the host field carries
    PRODUCER_DISCOVERY and an empty host, so the producer gate abstains. It has
    to be the host gate that refuses, or nothing protects them."""
    project = tmp_path / "demo-project"
    project.mkdir()
    root = tmp_path / "home" / ".claude"
    (root / "projects").mkdir(parents=True)
    store = HarnessSessionStore(project / ".tesserae" / "harness_sessions")

    legacy = _same_transcript_record(project, str(root / "projects" / "p" / "legacy.jsonl"),
                                     id="claude-code:leg", slug="legacy", title="Pre-host")
    store.write_sessions([legacy], producer=PRODUCER_DISCOVERY)  # producer, no host
    assert store.list_sessions()[0].host == ""

    other = _same_transcript_record(project, str(root / "projects" / "p" / "other.jsonl"),
                                    id="claude-code:oth", slug="other", title="Host B work")
    guarded = store.write_sessions([other], replace=True, prune_roots=[root],
                                   prune_harnesses=["claude-code"],
                                   producer=PRODUCER_DISCOVERY, host="host-b")
    assert "Pre-host" in {s.title for s in store.list_sessions()}
    assert guarded["removed"] == 0

    adopted = store.write_sessions([other], replace=True, prune_roots=[root],
                                   prune_harnesses=["claude-code"],
                                   producer=PRODUCER_DISCOVERY, host="host-b",
                                   adopt_unowned=True)
    assert [s.title for s in store.list_sessions()] == ["Host B work"]
    assert adopted["removed"] == 1


def test_host_blind_callers_keep_todays_behaviour(tmp_path):
    """A single-machine deployment passes no host and must be unchanged."""
    project = tmp_path / "demo-project"
    project.mkdir()
    root = tmp_path / "home" / ".claude"
    (root / "projects").mkdir(parents=True)
    store = HarnessSessionStore(project / ".tesserae" / "harness_sessions")

    old = _same_transcript_record(project, str(root / "projects" / "p" / "old.jsonl"),
                                  id="claude-code:old", slug="old", title="Gone")
    store.write_sessions([old], producer=PRODUCER_DISCOVERY)
    new = _same_transcript_record(project, str(root / "projects" / "p" / "new.jsonl"),
                                  id="claude-code:new", slug="new", title="Kept")
    result = store.write_sessions([new], replace=True, prune_roots=[root],
                                  prune_harnesses=["claude-code"],
                                  producer=PRODUCER_DISCOVERY)

    assert [s.title for s in store.list_sessions()] == ["Kept"]
    assert result["removed"] == 1


def test_local_host_id_is_stable_and_overridable(tmp_path, monkeypatch):
    """A persisted id, not a bare hostname: a renamed or re-imaged host must
    not inherit another machine's records."""
    import tesserae.harness_sessions as hs

    monkeypatch.setenv("TESSERAE_HOST_ID", "srv-a")
    assert hs.local_host_id() == "srv-a"

    monkeypatch.delenv("TESSERAE_HOST_ID", raising=False)
    monkeypatch.setattr(hs, "_HOST_ID_CACHE", None, raising=False)
    monkeypatch.setattr(hs, "HOST_ID_PATH", tmp_path / "host_id")
    first = hs.local_host_id()
    assert first and (tmp_path / "host_id").read_text(encoding="utf-8").strip() == first

    monkeypatch.setattr(hs, "_HOST_ID_CACHE", None, raising=False)
    assert hs.local_host_id() == first  # survives a fresh process


# ---------------------------------------------------------------------------
# Non-text content blocks: counted, and SAID OUT LOUD. Images attached to a
# session used to vanish inside _content_to_text with no trace, so the size of
# the multimodal gap was unknowable. Two things make it knowable: the tally has
# to reach the blocks (harness images sit NESTED inside a tool_result, never at
# the top level), and an operator has to be able to read the number from the
# command they actually ran.
#
# Every test below drives a public entry point — `sessions discover` or
# discover_harness_sessions() — so it fails on the old code because the
# BEHAVIOUR is absent, not because a helper it imports is.
# ---------------------------------------------------------------------------


def _transcript(root, project, blocks, name="s1.jsonl"):
    """Write a one-row Claude transcript whose message carries ``blocks``."""
    from tesserae import harness_sessions as hs

    directory = root / "projects" / hs._claude_project_dir(project.resolve())
    directory.mkdir(parents=True, exist_ok=True)
    row = {
        "type": "user",
        "cwd": str(project.resolve()),
        "timestamp": "2026-05-01T10:00:00Z",
        "sessionId": name.split(".")[0],
        "message": {"role": "user", "content": blocks},
    }
    path = directory / name
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    return path


def _discover_output(tmp_path, blocks, capsys):
    """Run `tesserae sessions discover` over a transcript and return stdout."""
    project = tmp_path / "proj"
    project.mkdir()
    (project / "data").mkdir()
    (project / "data" / "seed.md").write_text(
        "---\ntype: paper\n---\n# Seed\n\nx.\n", encoding="utf-8"
    )
    ProjectWiki.init(project, name="dropped_blocks")
    root = tmp_path / "claude"
    _transcript(root, project, blocks)
    main(["sessions", "discover", "--project", str(project), "--root", str(root)])
    return capsys.readouterr().out


def test_sessions_discover_says_what_it_dropped(tmp_path, capsys):
    """The count is only a measurement if the operator can read it.

    `sessions discover` is not `tesserae engine`, so it never calls
    logging.basicConfig — an INFO record from a library logger goes nowhere.
    stdout, next to the counts this command already prints, is the channel.
    """
    out = _discover_output(
        tmp_path,
        [
            {"type": "text", "text": "what is in this screenshot?"},
            {"type": "image", "source": {"type": "base64", "media_type": "image/png"}},
        ],
        capsys,
    )

    assert "image=1" in out, out
    assert "dropped" in out.lower(), out


def test_sessions_discover_counts_images_nested_inside_a_tool_result(tmp_path, capsys):
    """Harness images live INSIDE tool_result["content"], never at the top level.

    A scan of 150 recent transcripts under ~/.claude/projects and
    ~/.claude-personal2/projects found nine image blocks and ZERO of them at the
    top level. A tally that stops at the tool_result therefore reports the
    multimodal gap as zero on a machine that has one.
    """
    out = _discover_output(
        tmp_path,
        [
            {"type": "text", "text": "run the tool"},
            {
                "type": "tool_result",
                "content": [
                    {"type": "text", "text": "here is the render"},
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png"}},
                ],
            },
        ],
        capsys,
    )

    assert "image=1" in out, out
    assert "tool_result=1" in out, out


def test_dropped_blocks_are_counted_once_per_discovery_not_once_per_pass(
    tmp_path, capsys
):
    """_content_to_text runs over the same transcript several times (activity,
    turns, title/preview). Tallying inside it multiplied every block by the
    number of passes, so the histogram measured passes, not content."""
    out = _discover_output(
        tmp_path,
        [
            {"type": "text", "text": "one image, counted once"},
            {"type": "image", "source": {"type": "base64", "media_type": "image/png"}},
        ],
        capsys,
    )

    assert "image=1" in out, out
    assert "image=2" not in out, out


def test_deeply_nested_content_terminates_and_says_it_was_truncated(tmp_path, capsys):
    """A malformed transcript must not be able to hang the importer.

    Real content nests one level (image inside tool_result). The walk is capped
    well above that, and reaching the cap is itself reported rather than
    silently dropping the tail — the same posture as the count itself.
    """
    block = {"type": "image", "source": {}}
    for _ in range(400):
        block = {"type": "tool_result", "content": [block]}

    out = _discover_output(tmp_path, [{"type": "text", "text": "deep"}, block], capsys)

    assert "<truncated>" in out, out


def test_discover_reports_nothing_when_every_block_is_text(tmp_path, capsys):
    """No drops, no line: the summary must not become constant noise."""
    out = _discover_output(
        tmp_path, [{"type": "text", "text": "plain prose only"}], capsys
    )

    assert "dropped" not in out.lower(), out


def test_dropped_block_tally_does_not_accumulate_across_discoveries(tmp_path, capsys):
    """The number describes ONE discovery. A tally that survived between runs
    would grow without bound in the engine daemon, which discovers on a loop."""
    project = tmp_path / "proj"
    project.mkdir()
    (project / "data").mkdir()
    (project / "data" / "seed.md").write_text(
        "---\ntype: paper\n---\n# Seed\n\nx.\n", encoding="utf-8"
    )
    ProjectWiki.init(project, name="accumulate")
    root = tmp_path / "claude"
    _transcript(
        root,
        project,
        [{"type": "text", "text": "hi"}, {"type": "image", "source": {"type": "base64"}}],
    )
    argv = ["sessions", "discover", "--project", str(project), "--root", str(root)]

    main(argv)
    first = capsys.readouterr().out
    main(argv)
    second = capsys.readouterr().out

    assert "image=1" in first, first
    assert "image=1" in second, second


def test_dropped_content_block_counts_returns_a_snapshot(tmp_path):
    """Callers reading the tally must not be able to edit the result's copy."""
    from tesserae import harness_sessions as hs

    project = tmp_path / "proj"
    project.mkdir()
    root = tmp_path / "claude"
    _transcript(root, project, [{"type": "image", "source": {}}])

    sessions = hs.discover_harness_sessions(project, roots=[root])
    snapshot = hs.dropped_content_blocks(sessions)
    snapshot["image"] = 999

    assert hs.dropped_content_blocks(sessions) == {"image": 1}


def test_content_to_text_has_no_side_effects(tmp_path):
    """Flattening is a pure function. It is called an unpredictable number of
    times per transcript, so it is the wrong place to count anything."""
    from tesserae import harness_sessions as hs

    project = tmp_path / "proj"
    project.mkdir()
    root = tmp_path / "claude"
    _transcript(root, project, [{"type": "image", "source": {}}])
    sessions = hs.discover_harness_sessions(project, roots=[root])
    before = hs.dropped_content_blocks(sessions)

    hs._content_to_text([{"type": "image"}, {"type": "document"}])

    assert hs.dropped_content_blocks(sessions) == before


# ---------------------------------------------------------------------------
# The tally travels WITH the discovery it describes. It used to be a module
# global, reset at the start of a run and read at the end of it — which is only
# correct while exactly one discovery is in flight. `tesserae refresh --jobs N`
# dispatches projects into a ThreadPoolExecutor in ONE process, and the engine
# daemon's tailer thread parses transcripts on a poll loop without ever
# resetting or reading it.
# ---------------------------------------------------------------------------


def _project_with_images(tmp_path, name, images):
    """A project, its own harness root, and one transcript carrying ``images``."""
    project = tmp_path / name
    project.mkdir()
    root = tmp_path / f"{name}-claude"
    blocks = [{"type": "text", "text": "hello"}]
    blocks += [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png"}}
        for _ in range(images)
    ]
    _transcript(root, project, blocks, name=f"{name}.jsonl")
    return project, root


def test_two_projects_discovered_concurrently_keep_separate_tallies(tmp_path):
    """The failure the feature exists to eliminate, in the shape that produces
    it: alpha has three images, beta has none, and both scans run at once.

    A barrier forces both threads to finish tallying before either reads, so
    the interleaving is deterministic rather than a scheduling accident. On the
    module-global tally this fails in BOTH directions depending on which thread
    resets last: beta reports alpha's three images, or alpha reports none.
    """
    from concurrent.futures import ThreadPoolExecutor

    from tesserae import harness_sessions as hs

    alpha = _project_with_images(tmp_path, "alpha", images=3)
    beta = _project_with_images(tmp_path, "beta", images=0)
    barrier = threading.Barrier(2, timeout=30)
    real_parse = hs._parse_claude_session

    def staged(*args, **kwargs):
        result = real_parse(*args, **kwargs)
        barrier.wait()
        return result

    hs._parse_claude_session = staged
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(hs.discover_harness_sessions, project, roots=[root])
                for project, root in (alpha, beta)
            ]
            alpha_sessions, beta_sessions = [f.result() for f in futures]
    finally:
        hs._parse_claude_session = real_parse

    assert hs.dropped_content_blocks(alpha_sessions) == {"image": 3}
    assert hs.dropped_content_blocks(beta_sessions) == {}


def test_a_discovery_reports_its_own_counts_whatever_ran_after_it(tmp_path):
    """Reading the tally after a LATER discovery must not re-read that one.

    `multiproject.run_across_projects` collects every project's output and
    prints it afterwards, so a read-at-the-end global was already answering
    with whichever scan finished last even without true concurrency.
    """
    from tesserae import harness_sessions as hs

    alpha = _project_with_images(tmp_path, "alpha", images=3)
    beta = _project_with_images(tmp_path, "beta", images=0)

    alpha_sessions = hs.discover_harness_sessions(alpha[0], roots=[alpha[1]])
    beta_sessions = hs.discover_harness_sessions(beta[0], roots=[beta[1]])

    assert "image=3" in (hs.format_dropped_content_blocks(alpha_sessions) or "")
    assert hs.format_dropped_content_blocks(beta_sessions) is None


def test_parsing_one_transcript_repeatedly_accumulates_nothing_module_wide(tmp_path):
    """engine/session_tail.py calls _parse_claude_session on every poll cycle
    and never resets or reads a tally. Anything module-global therefore grew
    without bound for the lifetime of `tesserae engine`."""
    from tesserae import harness_sessions as hs

    project, root = _project_with_images(tmp_path, "engine", images=2)
    path = (
        root / "projects" / hs._claude_project_dir(project.resolve()) / "engine.jsonl"
    )

    for _ in range(3):
        hs._parse_claude_session(project.resolve(), root, path)

    # A fresh discovery must still see exactly this project's two images, not
    # six plus whatever the tailer added.
    sessions = hs.discover_harness_sessions(project, roots=[root])
    assert hs.dropped_content_blocks(sessions) == {"image": 2}


def test_the_tally_is_not_reachable_as_module_state(tmp_path):
    """A guarded global is still a global. Removing it is what makes the
    concurrency question unanswerable-by-construction, so pin its absence."""
    from tesserae import harness_sessions as hs

    assert not hasattr(hs, "_DROPPED_CONTENT_BLOCKS")
    assert not hasattr(hs, "reset_dropped_content_blocks")
    assert not hasattr(hs, "dropped_content_block_counts")


# ---------------------------------------------------------------------------
# Roadmap step 5 — tool results survive ingest.
#
# verify.py says of this codebase, in source: "tool_result is parsed solely to
# map subagent ids and never becomes a turn, so no exit code survives ingest
# ... this tool can say a document says so, never this ran and passed."
#
# Every test below asserts on the CONTENT of the minted turn list — the
# behaviour verify.py names as missing — not on the existence of a helper.
# ---------------------------------------------------------------------------


def _claude_tool_rows(result_block, *, tool="Bash", tool_input=None):
    """An assistant tool_use row followed by the user row carrying its result."""
    return [
        {
            "type": "assistant",
            "timestamp": "2026-08-09T10:00:00Z",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": tool,
                        "input": tool_input or {"command": "pytest"},
                    }
                ]
            },
        },
        {
            "type": "user",
            "timestamp": "2026-08-09T10:00:05Z",
            "message": {"content": [{**result_block, "tool_use_id": "toolu_1"}]},
        },
    ]


def test_a_claude_tool_result_becomes_its_own_turn_naming_the_tool_that_ran():
    from tesserae.harness_sessions import _claude_turns

    rows = _claude_tool_rows(
        {"type": "tool_result", "content": "1 failed, 4 passed", "is_error": True}
    )
    turns = _claude_turns(rows)

    assert [t["role"] for t in turns] == ["tool", "tool_result"]
    result = turns[1]
    # The tool_result block carries only tool_use_id; the NAME has to be
    # resolved back through the invocation or the outcome is unattributable.
    assert result["name"] == "Bash"
    assert "1 failed, 4 passed" in str(result["text"])
    assert result["is_error"] is True


def test_a_claude_tool_result_without_is_error_is_not_recorded_as_a_success():
    """`is_error` is omitted for every non-Bash tool, so its ABSENCE is not
    evidence of success. Stamping ok here would manufacture the exact claim
    verify.py refuses to make."""
    from tesserae.harness_sessions import _claude_turns

    turns = _claude_turns(
        _claude_tool_rows({"type": "tool_result", "content": "file contents"}, tool="Read")
    )
    result = next(t for t in turns if t["role"] == "tool_result")
    assert "is_error" not in result
    assert "exit_code" not in result


def test_a_claude_tool_result_text_is_capped_like_a_tool_turn():
    """Results are an order of magnitude larger than inputs (measured max: a
    2 MB single block). Uncapped they would be stored verbatim."""
    from tesserae.harness_sessions import _claude_turns

    turns = _claude_turns(
        _claude_tool_rows({"type": "tool_result", "content": "x" * 50_000})
    )
    result = next(t for t in turns if t["role"] == "tool_result")
    assert len(str(result["text"])) <= 1201  # 1200 + the ellipsis


def _codex_tool_rows(output):
    return [
        {
            "timestamp": "2026-08-09T10:00:00Z",
            "payload": {
                "type": "function_call",
                "name": "shell",
                "call_id": "call_1",
                "arguments": '{"command":["pytest"]}',
            },
        },
        {
            "timestamp": "2026-08-09T10:00:05Z",
            "payload": {
                "type": "function_call_output",
                "call_id": "call_1",
                "output": output,
            },
        },
    ]


def test_a_codex_function_call_output_turn_carries_the_process_exit_code():
    """Codex is the only harness that reports a real exit status, and it does
    so as a header line inside a plain output string."""
    from tesserae.harness_sessions import _codex_turns

    turns = _codex_turns(
        _codex_tool_rows(
            "Chunk ID: 861f8c\nWall time: 0.4 seconds\nProcess exited with code 2\n"
            "Output:\n---\nE   assert 1 == 2\n"
        )
    )
    assert [t["role"] for t in turns] == ["tool", "tool_result"]
    result = turns[1]
    assert result["name"] == "shell"
    assert result["exit_code"] == 2
    assert "assert 1 == 2" in str(result["text"])


def test_a_codex_output_with_no_exit_line_carries_no_exit_code():
    """apply_patch and MCP tools emit no exit line (measured: 54 of 1,286).
    Defaulting those to 0 would silently rot coverage into a false pass."""
    from tesserae.harness_sessions import _codex_turns

    turns = _codex_turns(_codex_tool_rows("Success. Updated the following files:\nM foo.py"))
    result = next(t for t in turns if t["role"] == "tool_result")
    assert "exit_code" not in result


def test_a_zero_exit_code_is_recorded_as_zero_not_dropped_as_falsey():
    from tesserae.harness_sessions import _codex_turns

    turns = _codex_turns(_codex_tool_rows("Process exited with code 0\nOutput:\nok\n"))
    result = next(t for t in turns if t["role"] == "tool_result")
    assert result["exit_code"] == 0


def test_a_session_records_its_failures_in_the_errors_field(tmp_path):
    """`HarnessSession.errors` has been declared and round-tripped since it was
    written and populated by nothing — 0 of the 211 live records carry one."""
    from tesserae import harness_sessions as hs

    project = tmp_path / "proj"
    project.mkdir()
    root = tmp_path / "claude"
    directory = root / "projects" / hs._claude_project_dir(project.resolve())
    directory.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "type": "user",
            "cwd": str(project.resolve()),
            "timestamp": "2026-08-09T09:59:00Z",
            "sessionId": "s1",
            "message": {"role": "user", "content": "run the suite"},
        }
    ] + [
        {**row, "cwd": str(project.resolve()), "sessionId": "s1"}
        for row in _claude_tool_rows(
            {"type": "tool_result", "content": "ERROR: 1 failed", "is_error": True}
        )
    ]
    path = directory / "s1.jsonl"
    path.write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
    )

    session = hs._parse_claude_session(project.resolve(), root, path)
    assert session is not None
    assert session.errors, "a failing tool result must land in session.errors"
    assert any("Bash" in e for e in session.errors)
    # and it must survive the store round-trip it was already wired for
    assert hs.HarnessSession.from_dict(session.to_dict()).errors == session.errors


def test_a_result_that_reported_nothing_is_not_recorded_as_a_failure(tmp_path):
    """The interesting case is SILENCE, not `is_error: false`. Most results —
    every non-Bash tool — carry no outcome field at all, and the whole point of
    the change is that neither absence nor presence may be invented. A session
    of ordinary Read calls must record no errors, or `errors` becomes noise
    instead of the failure list a recovery edge can anchor on."""
    from tesserae import harness_sessions as hs

    project = tmp_path / "proj"
    project.mkdir()
    root = tmp_path / "claude"
    directory = root / "projects" / hs._claude_project_dir(project.resolve())
    directory.mkdir(parents=True, exist_ok=True)
    rows = [
        {**row, "cwd": str(project.resolve()), "sessionId": "s2"}
        for row in (
            # no is_error key at all — the 58.7% case
            _claude_tool_rows({"type": "tool_result", "content": "file contents"}, tool="Read")
            # explicitly not an error — the 37.8% case
            + _claude_tool_rows({"type": "tool_result", "content": "ok", "is_error": False})
        )
    ]
    path = directory / "s2.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    session = hs._parse_claude_session(project.resolve(), root, path)
    assert session is not None
    assert session.errors == []


def test_a_non_zero_exit_code_lands_in_errors_even_without_is_error(tmp_path):
    """Codex reports no is_error at all — its only failure signal is the exit
    code, so `errors` has to read that too or half the corpus never reports."""
    from tesserae import harness_sessions as hs

    project = tmp_path / "proj"
    project.mkdir()
    root = tmp_path / "codex"
    directory = root / "sessions"
    directory.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "timestamp": "2026-08-09T09:59:00Z",
            "payload": {"type": "session_meta", "cwd": str(project.resolve())},
        },
        {
            "timestamp": "2026-08-09T09:59:30Z",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "run the suite"}],
            },
        },
    ] + _codex_tool_rows(
        "Process exited with code 2\nOutput:\nE   assert 1 == 2\n"
    )
    path = directory / "c1.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    session = hs._parse_codex_session(project.resolve(), root, path)
    assert session is not None
    assert session.errors, "a non-zero exit code is a failure"
    assert any("exited 2" in e for e in session.errors)


def test_the_self_capture_guard_reads_past_tool_results_to_find_the_signature():
    """v0.29.0 exists because Tesserae harvested its own LLM calls — 98.4% of
    the store. The guard anchors on the first three turns; widening ingest adds
    turns that can DISPLACE the signature out of that window. The window must be
    defined over conversation turns, not over whatever ingest happens to mint."""
    from tesserae.harness_sessions import (
        HarnessSession,
        _TESSERAE_PROMPT_SIGNATURES,
        is_tesserae_internal_session,
    )

    signature = _TESSERAE_PROMPT_SIGNATURES[0]
    session = HarnessSession(
        id="x",
        slug="x",
        harness="claude-code",
        agent_label="Claude Code",
        project_name="p",
        project_root="/tmp/p",
        started_at="2026-08-09T10:00:00Z",
        title="unrelated",
        summary="unrelated",
        redacted_preview="unrelated",
        metadata={
            "turns": [
                {"role": "tool", "name": "Read", "text": "{}"},
                {"role": "tool_result", "name": "Read", "text": "some file"},
                {"role": "tool", "name": "Bash", "text": "{}"},
                {"role": "tool_result", "name": "Bash", "text": "output"},
                {"role": "user", "text": signature + " and then some"},
            ]
        },
    )
    assert is_tesserae_internal_session(session) is True


def test_a_tool_result_quoting_a_tesserae_prompt_does_not_drop_a_real_session():
    """The guard's own docstring: "False positives (dropping real work) are
    worse than false negatives here." A Read of a prompt constant echoes a
    signature verbatim at position 0 of the result text."""
    from tesserae.harness_sessions import (
        HarnessSession,
        _TESSERAE_PROMPT_SIGNATURES,
        is_tesserae_internal_session,
    )

    signature = _TESSERAE_PROMPT_SIGNATURES[0]
    session = HarnessSession(
        id="y",
        slug="y",
        harness="claude-code",
        agent_label="Claude Code",
        project_name="p",
        project_root="/tmp/p",
        started_at="2026-08-09T10:00:00Z",
        title="fix the extractor prompt",
        summary="fix the extractor prompt",
        redacted_preview="fix the extractor prompt",
        metadata={
            "turns": [
                {"role": "user", "text": "show me the extractor prompt"},
                {"role": "tool", "name": "Read", "text": '{"file_path": "prompts.py"}'},
                {"role": "tool_result", "name": "Read", "text": signature},
            ]
        },
    )
    assert is_tesserae_internal_session(session) is False


def test_a_successful_run_is_not_recorded_as_an_error(tmp_path):
    """The guard that separates a failure from a success is ``exit_code != 0``,
    and nothing tested it: dropping it made EVERY result carrying an exit code a
    reported failure, and the whole suite still passed (3,334 tests). Codex
    stamps an exit code on 1,232 of its 1,286 results, so the mutant turns a
    session of clean runs into a session that failed 1,232 times."""
    from tesserae.harness_sessions import _errors_from_turns

    turns = [
        {"role": "tool_result", "name": "shell", "text": "12 passed", "exit_code": 0},
        {"role": "tool_result", "name": "shell", "text": "boom", "exit_code": 1},
    ]
    errors = _errors_from_turns(turns)
    assert len(errors) == 1, f"a run that exited 0 is a success: {errors}"
    assert "exited 1" in errors[0]


def test_a_non_boolean_is_error_is_not_recorded_as_a_failure():
    """``is_error`` is compared with ``is True``, not for truthiness. A harness
    that sends the STRING "false" would otherwise report a failure that the
    harness explicitly denied."""
    from tesserae.harness_sessions import _errors_from_turns, _tool_result_turn

    assert _errors_from_turns(
        [{"role": "tool_result", "name": "Bash", "text": "fine", "is_error": "false"}]
    ) == []
    turn = _tool_result_turn(timestamp="", name="Bash", text="fine", is_error="false")
    assert "is_error" not in turn


def test_a_session_never_stores_more_than_the_error_cap(tmp_path):
    """``errors`` is serialized into every stored record, so it is bounded on
    both axes. Neither bound was asserted anywhere."""
    from tesserae.harness_sessions import (
        _ERROR_TEXT_LIMIT,
        _MAX_SESSION_ERRORS,
        _errors_from_turns,
    )

    turns = [
        {
            "role": "tool_result",
            "name": "shell",
            "text": "x" * 5000,
            "exit_code": 1,
        }
    ] * (_MAX_SESSION_ERRORS + 25)
    errors = _errors_from_turns(turns)
    assert len(errors) == _MAX_SESSION_ERRORS
    assert all(len(e) <= len("shell exited 1: ") + _ERROR_TEXT_LIMIT for e in errors)


def test_a_recorded_error_does_not_publish_the_operators_home_directory():
    """``errors`` was the one field this branch added that the home-path
    redaction invariant did not cover, and it is stored in every session
    record."""
    from tesserae.harness_sessions import _errors_from_turns

    (error,) = _errors_from_turns(
        [
            {
                "role": "tool_result",
                "name": "Bash",
                "text": "FAILED /Users/rivka/proj/tests/test_x.py::test_y",
                "exit_code": 1,
            }
        ]
    )
    assert "/Users/rivka" not in error
    assert "~/proj/tests/test_x.py" in error


# ---------------------------------------------------------------------------
# The Codex exit code is read from a header, never from the body
# ---------------------------------------------------------------------------


def test_an_exit_line_quoted_in_a_tool_result_body_is_not_an_exit_code():
    """A result that merely CONTAINS the sentence has not reported an exit
    status. ``cat`` of a transcript, a test asserting on the string, ``git
    show`` of the module that defines it — all of them put the line in the body,
    and the 54 results with no header are exactly where it would be believed."""
    from tesserae.harness_sessions import _codex_exit_code

    assert (
        _codex_exit_code(
            "Success. Updated the following files:\n"
            "M runner.py\n"
            "and the transcript it patched reads:\n"
            "Process exited with code 0\n"
        )
        is None
    )


def test_the_exit_code_is_read_from_the_header_not_from_the_body():
    """Both halves in one result: the header says 0, the body quotes 1."""
    from tesserae.harness_sessions import _codex_exit_code

    assert (
        _codex_exit_code(
            "Chunk ID: 861f8c\n"
            "Wall time: 0.4 seconds\n"
            "Process exited with code 0\n"
            "Original token count: 12\n"
            "Output:\n"
            "Process exited with code 1\n"
        )
        == 0
    )


def test_the_self_capture_window_does_not_reach_past_the_first_reply():
    """Counting SPOKEN turns with no stop condition walks arbitrarily deep, so
    a user who pastes one of these prompts to ask about it — routine in this
    repository — has the whole session dropped. The guard's own docstring says
    dropping real work is the worse failure, so the window ends at the model's
    first reply: after an answer exists, prompt-shaped text is someone
    discussing a prompt."""
    from tesserae.harness_sessions import (
        HarnessSession,
        _TESSERAE_PROMPT_SIGNATURES,
        is_tesserae_internal_session,
    )

    signature = _TESSERAE_PROMPT_SIGNATURES[0]
    turns = [
        {"role": "user", "text": "refactor the exporter"},
        {"role": "assistant", "text": "Starting on the exporter now."},
    ]
    for _ in range(20):
        turns.append({"role": "tool", "name": "Edit", "text": "{}"})
        turns.append({"role": "tool_result", "name": "Edit", "text": "ok"})
    turns.append({"role": "user", "text": signature + " — why does this drop TODOs?"})

    session = HarnessSession(
        id="z",
        slug="z",
        harness="claude-code",
        agent_label="Claude Code",
        project_name="p",
        project_root="/tmp/p",
        started_at="2026-08-09T10:00:00Z",
        title="refactor the exporter",
        summary="refactor the exporter",
        redacted_preview="refactor the exporter",
        metadata={"turns": turns},
    )
    assert is_tesserae_internal_session(session) is False
