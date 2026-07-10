"""LLM-planned retrieval for ``ask --llm`` — plan → execute → synthesize.

The FakeClient stands in for the rotating CLI client: ``complete_json``
returns a canned plan, ``complete_text`` a canned cited answer. The graph
fixture carries Session nodes plus an edge so timeline/recent_sessions have
dated evidence to surface.
"""

from __future__ import annotations

import json
from pathlib import Path

from tesserae.ask_planner import _validated_steps, plan_and_answer
from tesserae.research_graph import ResearchEdge, ResearchGraph, ResearchNode, ResearchNodeType


class FakeClient:
    def __init__(self, plan, answer):
        self._plan = plan
        self._answer = answer
        self.json_calls = []
        self.text_calls = []

    def complete_json(self, *, system, user, schema_name, **kw):
        self.json_calls.append({"system": system, "user": user})
        return self._plan

    def complete_text(self, *, system, user, **kw):
        self.text_calls.append({"system": system, "user": user})
        return self._answer


def _make_project(tmp_path: Path):
    """Tiny project: one wiki concept page + a graph with dated sessions."""
    from tesserae.project import ProjectWiki

    project = tmp_path / "demo"
    wiki_dir = project / ".tesserae" / "wiki" / "concepts"
    site = project / ".tesserae" / "site"
    wiki_dir.mkdir(parents=True)
    site.mkdir(parents=True)
    (project / ".tesserae" / "config.json").write_text("{}", encoding="utf-8")
    (wiki_dir / "hybrid-retriever.md").write_text(
        "---\ntitle: Hybrid retriever\n---\n# Hybrid retriever\nBM25 + embeddings.\n",
        encoding="utf-8",
    )
    (site / "search-index.json").write_text(
        json.dumps(
            [
                {
                    "id": "Concept:hybrid-retriever",
                    "kind": "concepts",
                    "title": "Hybrid retriever",
                    "summary": "BM25 + embeddings retriever.",
                    "tokens": ["hybrid", "retriever", "bm25", "embeddings"],
                    "len": 4,
                    "href": "concepts/hybrid-retriever.html",
                    "source_path": "",
                    "created_ts": 1_700_000_000,
                }
            ]
        ),
        encoding="utf-8",
    )

    session = ResearchNode(
        id="Session:s1",
        name="Ship extraction cache",
        type=ResearchNodeType.SESSION,
        description="Added code-graph extraction cache",
        metadata={"started_at": "2026-07-05T10:00:00Z"},
    )
    insight = ResearchNode(
        id="SessionInsight:i1",
        name="Cache halves compile time",
        type=ResearchNodeType.SESSION_INSIGHT,
        description="Extraction cache cut compile from 90s to 40s",
        metadata={"created_at": "2026-07-05T11:00:00Z"},
    )
    graph = ResearchGraph(
        nodes=[session, insight],
        edges=[ResearchEdge(source=insight.id, target=session.id, type="derived_from_session", evidence="session finding")],
    )
    graph_json = project / ".tesserae" / "graph.json"
    graph_json.write_text(graph.to_json(indent=2), encoding="utf-8")
    return ProjectWiki.load(project)


PLAN = {
    "reasoning": "Temporal question — needs dated evidence plus wiki context.",
    "steps": [
        {"action": "recent_sessions", "args": {"since": "2026-07-01", "limit": 5}},
        {"action": "session_findings", "args": {"limit": 5}},
        {"action": "wiki_search", "args": {"query": "hybrid retriever"}},
        {"action": "made_up_action", "args": {}},
    ],
}


def test_plan_and_answer_executes_plan_and_synthesizes(tmp_path):
    wiki = _make_project(tmp_path)
    client = FakeClient(PLAN, "Recently the extraction cache shipped [kg-step-1-recent_sessions].")

    envelope = plan_and_answer(wiki, "what happened recently?", client=client)

    assert envelope is not None
    assert envelope["used_llm"] is True
    # Unknown action dropped; the three known steps executed in order.
    actions = [s["action"] for s in envelope["plan"]["steps"]]
    assert actions == ["recent_sessions", "session_findings", "wiki_search"]
    # Citation rewritten to a readable name.
    assert "[recent sessions]" in envelope["answer"]
    # wiki_search hits surface for display.
    assert envelope["hits"] and envelope["hits"][0]["title"] == "Hybrid retriever"
    # Dated KG evidence reached the synthesis prompt.
    synth_user = client.text_calls[0]["user"]
    assert "2026-07-05" in synth_user and "Ship extraction cache" in synth_user
    assert "Cache halves compile time" in synth_user


def test_plan_and_answer_returns_none_without_citations(tmp_path):
    wiki = _make_project(tmp_path)
    client = FakeClient(PLAN, "Ungrounded prose with no citations.")
    assert plan_and_answer(wiki, "what happened recently?", client=client) is None


def test_plan_and_answer_returns_none_on_empty_plan(tmp_path):
    wiki = _make_project(tmp_path)
    client = FakeClient({"reasoning": "?", "steps": []}, "unused")
    assert plan_and_answer(wiki, "q?", client=client) is None
    assert client.text_calls == []


def test_validated_steps_tolerates_malformed_payloads():
    assert _validated_steps(None) == []
    assert _validated_steps([1, 2]) == []
    assert _validated_steps({"steps": "nope"}) == []
    assert _validated_steps({"steps": [{"action": "timeline", "args": None}]}) == [
        {"action": "timeline", "args": {}}
    ]


def test_ask_project_llm_path_uses_planner(tmp_path, monkeypatch):
    """ask_project routes through the planner BY DEFAULT (use_llm defaults
    True — spec §1) when a compiled graph exists."""
    from tesserae.query import ask_project

    wiki = _make_project(tmp_path)
    client = FakeClient(PLAN, "Shipped the cache [kg-step-1-recent_sessions].")
    monkeypatch.delenv("TESSERAE_QUERY_DRY_RUN", raising=False)
    monkeypatch.setattr("tesserae.llm_json.build_rotating_client", lambda *a, **k: client)

    envelope = ask_project(wiki, "what happened recently?")

    assert envelope["backend"] == "wiki"
    assert envelope["used_llm"] is True
    assert envelope["plan"]["steps"]
    assert "[recent sessions]" in envelope["answer"]


def test_ask_project_no_llm_never_invokes_planner(tmp_path, monkeypatch):
    """no_llm=True skips the planner even with a compiled graph, use_llm=True,
    and TESSERAE_QUERY_LLM=1 — the force-off beats every enable knob."""
    from tesserae.query import ask_project

    wiki = _make_project(tmp_path)
    monkeypatch.delenv("TESSERAE_QUERY_DRY_RUN", raising=False)
    monkeypatch.setenv("TESSERAE_QUERY_LLM", "1")

    def _boom(*a, **k):
        raise AssertionError("planner must not run when no_llm=True")

    monkeypatch.setattr("tesserae.ask_planner.plan_and_answer", _boom)

    envelope = ask_project(wiki, "what happened recently?", use_llm=True, no_llm=True)

    assert envelope["backend"] == "wiki"
    assert envelope["used_llm"] is False
    assert envelope["answer"] is None
