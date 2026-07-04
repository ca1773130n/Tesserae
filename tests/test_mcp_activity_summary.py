"""Task 12 — the ``activity_summary`` MCP tool: registration + dispatch.

The tool is a thin adapter over :func:`tesserae.activity_summary.build_summary`.
We patch ``build_summary`` at the ``mcp_server`` module scope so the dispatch
path is exercised without seeding a real project, and assert the return shape
``{"markdown": str, "paths": [str]}`` plus that the args are forwarded.
"""
from pathlib import Path

import tesserae.mcp_server as m
from tesserae.activity_summary import SummaryResult


def test_activity_summary_tool_registered():
    tools = {t["name"] for t in m.LLMWikiMCPServer().list_tools()}
    assert "activity_summary" in tools


def test_mcp_activity_summary_dispatch(monkeypatch):
    captured = {}

    def fake_build(windows, project_names=None, *, synthesize=True, write=True):
        captured.update(
            windows=windows,
            project_names=project_names,
            synthesize=synthesize,
            write=write,
        )
        return SummaryResult(markdown="# d", paths=[Path("/tmp/x.md")])

    monkeypatch.setattr(m, "build_summary", fake_build, raising=False)
    server = m.LLMWikiMCPServer()
    out = server.call_tool(
        "activity_summary",
        {"day": "2026-07-04", "project": "proj", "synthesize": False},
    )

    # Return shape: markdown + string-coerced paths.
    assert out["markdown"] == "# d"
    assert out["paths"] == ["/tmp/x.md"]
    assert "# d" in str(out)

    # Args forwarded: single-day window, named project as a one-element list,
    # synthesize honoured, write always on (summaries persisted).
    assert len(captured["windows"]) == 1
    assert captured["project_names"] == ["proj"]
    assert captured["synthesize"] is False
    assert captured["write"] is True


def test_mcp_activity_summary_defaults(monkeypatch):
    """No project => all registered (None); synthesize defaults to True."""
    captured = {}

    def fake_build(windows, project_names=None, *, synthesize=True, write=True):
        captured.update(project_names=project_names, synthesize=synthesize)
        return SummaryResult(markdown="# all", paths=[])

    monkeypatch.setattr(m, "build_summary", fake_build, raising=False)
    server = m.LLMWikiMCPServer()
    out = server.call_tool("activity_summary", {"day": "2026-07-04"})

    assert out == {"markdown": "# all", "paths": []}
    assert captured["project_names"] is None
    assert captured["synthesize"] is True
