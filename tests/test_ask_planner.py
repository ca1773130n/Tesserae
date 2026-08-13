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


# --- compile_context in the catalog: views + propose-never-execute (step 11) ---


def _make_view_project(tmp_path: Path):
    """A graph whose edges span several view partitions, so a view-restricted
    walk demonstrably reaches different nodes than an unrestricted one."""
    from tesserae.project import ProjectWiki

    project = tmp_path / "views-demo"
    (project / ".tesserae" / "wiki" / "concepts").mkdir(parents=True)
    (project / ".tesserae" / "site").mkdir(parents=True)
    (project / ".tesserae" / "config.json").write_text("{}", encoding="utf-8")
    (project / ".tesserae" / "site" / "search-index.json").write_text("[]", encoding="utf-8")

    def _node(nid, name, ntype, desc):
        return ResearchNode(id=nid, name=name, type=ntype, description=desc)

    seed = _node(
        "Concept:retrieval", "Retrieval budgeting",
        ResearchNodeType.CONCEPT, "How much context to spend per answer. " * 6,
    )
    idea = _node(
        "Concept:fusion", "Rank fusion",
        ResearchNodeType.CONCEPT, "Reciprocal rank fusion across lanes. " * 6,
    )
    fixer = _node(
        "Person:alex", "Alex Rivera",
        ResearchNodeType.PERSON, "Maintainer who fixed the regression. " * 6,
    )
    graph = ResearchGraph(
        nodes=[seed, idea, fixer],
        edges=[
            # semantic view
            ResearchEdge(source=seed.id, target=idea.id, type="uses", evidence="budget uses fusion"),
            # entity view
            ResearchEdge(source=seed.id, target=fixer.id, type="authored_by", evidence="alex owns it"),
        ],
    )
    (project / ".tesserae" / "graph.json").write_text(graph.to_json(indent=2), encoding="utf-8")
    return ProjectWiki.load(project)


def _plan(step_args, **extra):
    return {"reasoning": "r", "steps": [{"action": "compile_context", "args": step_args}], **extra}


def test_compile_context_is_planned_and_its_bundle_reaches_synthesis(tmp_path):
    wiki = _make_view_project(tmp_path)
    client = FakeClient(
        _plan({"query": "retrieval budgeting"}),
        "Budgeting relates to fusion [kg-step-1-compile_context].",
    )

    envelope = plan_and_answer(wiki, "how does budgeting relate to fusion?", client=client)

    assert envelope is not None
    assert [s["action"] for s in envelope["plan"]["steps"]] == ["compile_context"]
    # The walked bundle — not page text — is what the synthesizer saw.
    assert "Retrieval budgeting" in client.text_calls[0]["user"]
    assert 'kind="kg:compile_context"' in client.text_calls[0]["user"]


def test_the_catalog_advertises_every_registered_view(tmp_path):
    """A view missing from the signature is a view the planner is instructed
    never to select — the same wiring rule the finding kinds follow."""
    from tesserae.ask_planner import _CATALOG, _PLANNER_SYSTEM
    from tesserae.retrieval.views import VIEWS

    sig = next(s for name, s, _d in _CATALOG if name == "compile_context")
    for view_name in VIEWS:
        assert view_name in sig
        assert view_name in _PLANNER_SYSTEM


def test_an_invented_view_degrades_to_the_full_graph_and_is_reported(tmp_path):
    """One hallucinated name must not cost the step its evidence: unknown
    views are dropped and reported, never raised."""
    wiki = _make_view_project(tmp_path)
    client = FakeClient(
        _plan({"query": "retrieval budgeting", "views": ["causal", "codepath"]}),
        "Answer [kg-step-1-compile_context].",
    )

    envelope = plan_and_answer(wiki, "why did retrieval regress?", client=client)

    executed = envelope["plan"]["executed"][0]
    assert executed["ok"] is True
    assert executed["views"] == ["causal"]
    assert executed["views_dropped"] == ["codepath"]
    assert "(step failed" not in client.text_calls[0]["user"]


def test_all_unknown_views_still_walk_the_whole_graph(tmp_path):
    wiki = _make_view_project(tmp_path)
    client = FakeClient(
        _plan({"query": "retrieval budgeting", "views": ["nonsense"]}),
        "Answer [kg-step-1-compile_context].",
    )

    envelope = plan_and_answer(wiki, "how does budgeting work?", client=client)

    executed = envelope["plan"]["executed"][0]
    assert executed["views"] == []
    assert executed["views_dropped"] == ["nonsense"]
    assert executed["ok"] is True
    assert executed["citations"] >= 1


def test_different_views_reach_different_nodes(tmp_path):
    """The anti-mutant case: if `view` were dropped on the floor, the semantic
    and entity walks would return identical evidence."""
    wiki = _make_view_project(tmp_path)

    def _run(view_name):
        client = FakeClient(
            _plan({"query": "retrieval budgeting", "views": [view_name]}),
            "Answer [kg-step-1-compile_context].",
        )
        env = plan_and_answer(wiki, "q", client=client)
        return env["plan"]["executed"][0], client.text_calls[0]["user"]

    semantic, semantic_prompt = _run("semantic")
    entity, entity_prompt = _run("entity")

    assert semantic["views_reached"] == ["semantic"]
    assert entity["views_reached"] == ["entity"]
    assert "Rank fusion" in semantic_prompt and "Alex Rivera" not in semantic_prompt
    assert "Alex Rivera" in entity_prompt and "Rank fusion" not in entity_prompt


def test_executed_reports_the_outcome_without_touching_the_request(tmp_path):
    """`steps` is what the model ASKED for; `executed` is what ran. Merging
    them is the defect the knobs/pool_reservations split exists to avoid."""
    wiki = _make_view_project(tmp_path)
    client = FakeClient(
        _plan({"query": "retrieval budgeting", "views": ["semantic", "bogus"]}),
        "Answer [kg-step-1-compile_context].",
    )

    envelope = plan_and_answer(wiki, "q", client=client)

    assert envelope["plan"]["steps"][0]["args"]["views"] == ["semantic", "bogus"]
    assert envelope["plan"]["executed"][0]["views"] == ["semantic"]
    assert len(envelope["plan"]["executed"]) == len(envelope["plan"]["steps"])


def test_the_same_question_twice_yields_byte_identical_evidence(tmp_path):
    """No wall clock, no nested LLM call: compile_context is a pure function
    and the planner passes it nothing that would break that."""
    wiki = _make_view_project(tmp_path)

    def _evidence():
        client = FakeClient(
            _plan({"query": "retrieval budgeting", "views": ["semantic"]}),
            "Answer [kg-step-1-compile_context].",
        )
        plan_and_answer(wiki, "q", client=client)
        return client.text_calls[0]["user"]

    assert _evidence() == _evidence()


def test_the_planner_proposes_a_write_it_can_never_perform(tmp_path, monkeypatch):
    """Propose, never execute — pinned by three independent locks."""
    import tesserae.agent_write as agent_write

    def _explode(*a, **kw):  # pragma: no cover - must never run
        raise AssertionError("the planner wrote to the graph")

    monkeypatch.setattr(agent_write, "record_agent_write", _explode)

    wiki = _make_view_project(tmp_path)
    plan = _plan(
        {"query": "retrieval budgeting"},
        proposed_write={
            "nodes": [
                {
                    "name": "Budget defaults to 2200 chars in ask",
                    "type": "Claim",
                    "description": "Stated in the question.",
                }
            ],
            "edges": [
                {
                    "source": "Budget defaults to 2200 chars in ask",
                    "target": "Concept:retrieval",
                    "type": "references",
                    "evidence": "the question asserts it",
                }
            ],
            "rationale": "durable fact asserted by the question",
        },
    )
    # Lock 2: a writer named as a STEP is dropped before execution.
    plan["steps"].append({"action": "graph_write", "args": {"nodes": []}})
    client = FakeClient(plan, "Answer [kg-step-1-compile_context].")

    envelope = plan_and_answer(wiki, "record that the budget is 2200", client=client)

    assert [s["action"] for s in envelope["plan"]["steps"]] == ["compile_context"]
    proposal = envelope["proposed_write"]
    assert proposal["tool"] == "graph_write"
    assert proposal["status"] == "unsubmitted"
    # Lock 1 + the strongest guarantee: unsubmittable until a caller with an
    # agent key and an outside anchor fills provenance in.
    assert proposal["provenance"] is None
    assert len(proposal["nodes"]) == 1 and len(proposal["edges"]) == 1
    # Nothing was written.
    assert not (wiki.project_root / ".tesserae" / "agent-writes.jsonl").exists()


def test_a_proposal_that_cannot_be_verified_is_dropped_not_repaired(tmp_path):
    wiki = _make_view_project(tmp_path)
    plan = _plan(
        {"query": "retrieval budgeting"},
        proposed_write={
            "nodes": [{"name": "X", "type": "NotAType", "description": ""}],
            "edges": [
                {"source": "X", "target": "Y", "type": "invented_edge", "evidence": "e"},
                {"source": "X", "target": "Y", "type": "references", "evidence": ""},
            ],
            "rationale": "nope",
        },
    )
    client = FakeClient(plan, "Answer [kg-step-1-compile_context].")

    envelope = plan_and_answer(wiki, "q", client=client)

    # Nothing survived, so the key is absent entirely rather than empty.
    assert "proposed_write" not in envelope


def test_no_proposal_means_no_key(tmp_path):
    wiki = _make_view_project(tmp_path)
    client = FakeClient(_plan({"query": "retrieval budgeting"}), "A [kg-step-1-compile_context].")

    envelope = plan_and_answer(wiki, "q", client=client)

    assert "proposed_write" not in envelope
