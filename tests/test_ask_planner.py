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


def test_a_malformed_proposal_container_never_sinks_the_answer(tmp_path):
    """`_validated_proposal` must be TOTAL: a truthy SCALAR where a list was
    expected ("nodes": 2 — a plausible JSON-mode slip) must not raise, or a
    fully synthesized cited answer is discarded and the caller silently drops
    to BM25 over a malformed OPTIONAL key."""
    wiki = _make_view_project(tmp_path)
    for bad in (2, True, 1.5, "abc", {"a": 1}):
        client = FakeClient(
            _plan({"query": "retrieval budgeting"}, proposed_write={"nodes": bad, "edges": bad}),
            "Answer [kg-step-1-compile_context].",
        )
        envelope = plan_and_answer(wiki, "q", client=client)
        assert envelope is not None, f"a {type(bad).__name__} proposal sank the whole ask"
        assert "proposed_write" not in envelope


# --- timeline / search_facts honesty: the window is a real temporal filter ---


def _make_fact_project(tmp_path: Path, findings, supersedes=(), slug="facts-demo"):
    """A project whose graph projects one dated TemporalFact per finding.

    ``findings`` is a sequence of ``(name, valid_from | None)``; each becomes
    ``<name> discussed_in Shared Doc``, dated from the finding's own
    ``first_seen_at`` or left undated. ``supersedes`` pairs
    ``(newer, older)`` close the older fact's interval at the newer's date —
    the only way a projected fact gets a ``valid_to``, and the only shape that
    can tell an ``as_of`` pivot apart from a ``since`` window.
    """
    from tesserae.project import ProjectWiki

    project = tmp_path / slug
    (project / ".tesserae" / "wiki" / "concepts").mkdir(parents=True)
    (project / ".tesserae" / "site").mkdir(parents=True)
    (project / ".tesserae" / "config.json").write_text("{}", encoding="utf-8")
    (project / ".tesserae" / "site" / "search-index.json").write_text("[]", encoding="utf-8")

    doc = ResearchNode(id="Paper:doc", name="Shared Doc", type=ResearchNodeType.PAPER)
    nodes = [doc]
    edges = []
    ids = {}
    for name, valid_from in findings:
        node_id = f"SessionInsight:{name.replace(' ', '-')}"
        ids[name] = node_id
        nodes.append(
            ResearchNode(
                id=node_id,
                name=name,
                type=ResearchNodeType.SESSION_INSIGHT,
                description=f"finding {name}",
                metadata={"first_seen_at": valid_from} if valid_from else {},
            )
        )
        edges.append(ResearchEdge(source=node_id, target=doc.id, type="discussed_in"))
    for newer, older in supersedes:
        edges.append(ResearchEdge(source=ids[newer], target=ids[older], type="supersedes"))
    graph = ResearchGraph(nodes=nodes, edges=edges)
    (project / ".tesserae" / "graph.json").write_text(graph.to_json(indent=2), encoding="utf-8")
    return ProjectWiki.load(project)


def _run_step(wiki, action, **args):
    """Execute a one-step plan; return (executed entry, synthesis prompt)."""
    client = FakeClient(
        {"reasoning": "r", "steps": [{"action": action, "args": args}]},
        f"Answer [kg-step-1-{action}].",
    )
    envelope = plan_and_answer(wiki, "q", client=client)
    assert len(envelope["plan"]["executed"]) == len(envelope["plan"]["steps"])
    return envelope["plan"]["executed"][0], client.text_calls[0]["user"]


def test_a_since_window_drops_the_undated_row_a_string_compare_kept(tmp_path):
    """ANTI-MUTANT: the projector writes the literal "undated", which sorts
    AFTER every ISO date, so `valid_from >= since` kept exactly the rows the
    window exists to remove — and rendered them as if they carried a date."""
    wiki = _make_fact_project(
        tmp_path, [("august finding", "2026-08-01"), ("unstamped finding", None)]
    )
    assert "undated" >= "2026-07-01"  # what the string compare believed

    executed, prompt = _run_step(wiki, "timeline", since="2026-07-01")

    assert "august finding" in prompt
    assert "unstamped finding" not in prompt
    assert executed["rows"] == 1
    assert executed["undated_excluded"] == 1
    assert executed["undated_included"] == 0
    assert executed["since"] == "2026-07-01"


def test_a_since_window_keeps_an_offset_timestamp_inside_it(tmp_path):
    """ANTI-MUTANT in the other direction: 2026-02-28T23:00-05:00 IS
    2026-03-01T04:00Z, inside the window by instant and before it by string."""
    wiki = _make_fact_project(tmp_path, [("offset finding", "2026-02-28T23:00:00-05:00")])
    assert not "2026-02-28T23:00:00-05:00" >= "2026-03-01"  # what it believed

    executed, prompt = _run_step(wiki, "timeline", since="2026-03-01")

    assert "offset finding" in prompt
    assert executed["rows"] == 1
    assert executed["undated_excluded"] == 0


def test_an_as_of_pivot_excludes_a_fact_superseded_before_it(tmp_path):
    """ANTI-MUTANT on semantics rather than parsing: valid_to is invisible to
    any comparison on valid_from, so no string compare can produce this answer
    at any date whatsoever."""
    wiki = _make_fact_project(
        tmp_path,
        [("old finding", "2026-01-01"), ("new finding", "2026-02-01")],
        supersedes=[("new finding", "old finding")],
    )

    executed, prompt = _run_step(wiki, "timeline", as_of="2026-03-01")

    assert "new finding" in prompt
    assert "old finding --discussed_in-->" not in prompt
    assert executed["as_of"] == "2026-03-01"
    assert "since" not in executed and "undated_excluded" not in executed


def test_since_and_as_of_answer_the_same_date_differently(tmp_path):
    """ANTI-MUTANT against collapsing the two knobs into one: at 2026-03-01
    the pivot keeps an open fact and an undated one, and the window keeps
    neither. A single predicate cannot produce both answers."""
    wiki = _make_fact_project(
        tmp_path,
        [
            ("open finding", "2026-01-01"),
            ("closed finding", "2026-01-01"),
            ("closer finding", "2026-02-01"),
            ("unstamped finding", None),
        ],
        supersedes=[("closer finding", "closed finding")],
    )

    pivoted, pivot_prompt = _run_step(wiki, "timeline", as_of="2026-03-01")
    windowed, window_prompt = _run_step(wiki, "timeline", since="2026-03-01")

    assert "open finding --discussed_in-->" in pivot_prompt
    assert "(undated) unstamped finding" in pivot_prompt
    assert "closed finding --discussed_in-->" not in pivot_prompt
    assert pivoted["undated_included"] == 1

    assert "(no timeline events in range)" in window_prompt
    assert windowed["rows"] == 0
    assert windowed["undated_excluded"] == 1
    assert pivot_prompt != window_prompt


def test_a_since_window_survives_the_row_limit(tmp_path):
    """The window must run BEFORE timeline's ascending sort and limit slice.
    Filtered afterwards, a July question over a corpus full of July events was
    handed the oldest 50 rows and then dropped every one of them."""
    findings = [
        (f"event {index:03d}", f"2026-{1 + index // 20:02d}-{1 + index % 20:02d}")
        for index in range(150)
    ]
    wiki = _make_fact_project(tmp_path, findings)

    executed, prompt = _run_step(wiki, "timeline", since="2026-07-01", limit=50)

    assert executed["rows"] == 30
    assert "(no timeline events in range)" not in prompt
    for month in ("2026-01-", "2026-02-", "2026-06-"):
        assert month not in prompt


def test_undated_included_counts_the_rows_shipped_not_the_corpus(tmp_path):
    """The counter describes the evidence in front of the synthesizer. The
    pivot-scoped number would have this three-row answer claim 41 undated
    rows — inverting the judgement the counter exists to support."""
    findings = [("splatting old", "2026-01-01"), ("splatting new", "2026-03-01"), ("splatting vague", None)]
    findings += [(f"kitten {index}", None) for index in range(40)]
    wiki = _make_fact_project(tmp_path, findings)

    executed, prompt = _run_step(wiki, "timeline", query="splatting")

    assert executed["rows"] == 3
    assert executed["undated_included"] == 1
    assert "kitten" not in prompt


def test_capped_is_derived_from_the_page_not_from_a_page_size_constant(tmp_path):
    """`capped` says "there is more than you were shown", and the only honest
    source for that is the page itself. It read
    ``total_events >= FACT_MATCH_CEILING`` back when ``total_events`` WAS that
    clamp, so it stayed silent on every truncated answer under 100 rows and
    fired on a 100-match answer that was complete."""
    findings = [(f"event {index:03d}", f"2026-01-{1 + index:02d}") for index in range(4)]
    wiki = _make_fact_project(tmp_path, findings)

    truncated, _ = _run_step(wiki, "timeline", limit=2)
    whole, _ = _run_step(wiki, "timeline", limit=50)

    assert truncated["rows"] == 2 and truncated["capped"] is True
    # Absent, not False: a complete answer asserts nothing about a cap.
    assert whole["rows"] == 4 and "capped" not in whole


def test_an_unparseable_date_fails_the_step_instead_of_answering_everything(tmp_path):
    """A whole-corpus answer wearing an "as of DATE" label is the precise lie
    this branch exists to remove, so the step fails loudly instead."""
    wiki = _make_fact_project(tmp_path, [("august finding", "2026-08-01")])

    executed, prompt = _run_step(wiki, "timeline", as_of="last tuesday")

    assert executed["ok"] is False
    assert "Unparseable as_of timestamp" in prompt
    assert "discussed_in" not in prompt  # no event rows at all
    # Nothing is reported for a filter that never ran.
    assert "rows" not in executed and "as_of" not in executed


def test_the_same_dated_plan_twice_yields_byte_identical_evidence(tmp_path):
    """No wall clock anywhere: an as_of/since defaulted from "today" would
    make this green at 10:00 and red at 23:59 UTC."""
    wiki = _make_fact_project(
        tmp_path, [("august finding", "2026-08-01"), ("unstamped finding", None)]
    )

    first, first_prompt = _run_step(wiki, "timeline", since="2026-07-01", as_of="2026-09-01")
    second, second_prompt = _run_step(wiki, "timeline", since="2026-07-01", as_of="2026-09-01")

    assert first_prompt == second_prompt
    assert first == second


def test_search_facts_pivots_on_as_of_and_reports_what_ran(tmp_path):
    """Advertising as_of on search_facts without reading it would re-create
    the silent no-op the catalog/branch pairing exists to prevent."""
    wiki = _make_fact_project(
        tmp_path,
        [("old finding", "2026-01-01"), ("new finding", "2026-03-01")],
        supersedes=[("new finding", "old finding")],
    )

    executed, prompt = _run_step(wiki, "search_facts", query="finding", as_of="2026-02-01")

    assert "old finding" in prompt
    assert '"new finding"' not in prompt
    assert executed["as_of"] == "2026-02-01"
    assert executed["rows"] >= 1
    assert executed["undated_included"] == 0


def test_every_catalog_argument_is_read_by_the_branch_that_serves_it(tmp_path):
    """An argument the catalog advertises and the branch ignores is a silent
    no-op — the planner is told to send a knob that does nothing. The catalog
    IS the planner prompt, so this pairing has to be mechanical."""
    import inspect
    import re

    from tesserae.ask_planner import _CATALOG, _execute_step

    branches = {}
    current = None
    for line in inspect.getsource(_execute_step).splitlines():
        match = re.match(r'\s*if action == "(\w+)":', line)
        if match:
            current = match.group(1)
            branches[current] = []
        elif current:
            branches[current].append(line)

    for name, signature, _desc in _CATALOG:
        body = "\n".join(branches.get(name, []))
        assert body, f"{name} is advertised but has no branch"
        for key in re.findall(r'"(\w+)":', signature):
            assert f'"{key}"' in body, f"{name} advertises {key!r} but never reads it"


def test_bundle_anchors_are_rewritten_to_resolvable_node_ids(tmp_path):
    """The bundle's own [node-N] anchors are the nearest-looking citation
    syntax in the evidence, so the synthesizer copies them — they satisfy the
    grounding gate while resolving to nothing. They must reach the model as
    real node ids instead."""
    wiki = _make_view_project(tmp_path)
    client = FakeClient(
        _plan({"query": "retrieval budgeting"}),
        "Budgeting uses fusion [Concept:fusion].",
    )

    envelope = plan_and_answer(wiki, "q", client=client)

    evidence = client.text_calls[0]["user"]
    assert "[node-1]" not in evidence
    assert "[Concept:retrieval]" in evidence or "[Concept:fusion]" in evidence
    # And a citation the model copied from the evidence resolves to a name.
    assert "[Rank fusion]" in envelope["answer"]


# ---------------------------------------------------------------------------
# Synthesis prompt: what is admitted into it, and once
# ---------------------------------------------------------------------------


class _Hit:
    """The shape `_build_synthesis_message` reads off a wiki search hit."""

    def __init__(self, kind, title, node_id, page_text, excerpt=""):
        self.kind = kind
        self.title = title
        self.node_id = node_id
        self.page_text = page_text
        self.excerpt = excerpt


def _make_source_project(tmp_path: Path):
    """A project whose wiki pages point at real source documents.

    Two units, because `_document_corpus` indexes a DIRECTORY as one document
    and a term's document frequency is what the admission gate reads. `alpha`
    carries a rare vocabulary; `beta` carries none of it.
    """
    project = tmp_path / "demo"
    wiki_dir = project / ".tesserae" / "wiki" / "concepts"
    wiki_dir.mkdir(parents=True)

    alpha = project / "corpus" / "alpha"
    beta = project / "corpus" / "beta"
    alpha.mkdir(parents=True)
    beta.mkdir(parents=True)
    (alpha / "paper.md").write_text(
        "SENTINEL-ALPHA-BODY. Zygomorphic flange calibration is how the rig "
        "settles. It does work when the plate is warm.\n",
        encoding="utf-8",
    )
    (beta / "paper.md").write_text(
        "SENTINEL-BETA-BODY. A plate warms and it does work. Nothing here "
        "settles anything else.\n",
        encoding="utf-8",
    )
    (wiki_dir / "alpha.md").write_text(
        "---\ntitle: Alpha\nsource_path: %s\n---\n"
        "# Alpha\nZygomorphic calibration, summarised.\n" % (alpha / "paper.md"),
        encoding="utf-8",
    )
    (wiki_dir / "beta.md").write_text(
        "---\ntitle: Beta\nsource_path: %s\n---\n"
        "# Beta\nThe plate warms.\n" % (beta / "paper.md"),
        encoding="utf-8",
    )
    return project, alpha, beta


def _hits_for(alpha: Path, beta: Path):
    return [
        _Hit("concepts", "Alpha", "Concept:alpha",
             "---\ntitle: Alpha\nsource_path: %s\n---\n"
             "# Alpha\nZygomorphic calibration, summarised.\n" % (alpha / "paper.md")),
        _Hit("concepts", "Beta", "Concept:beta",
             "---\ntitle: Beta\nsource_path: %s\n---\n"
             "# Beta\nThe plate warms.\n" % (beta / "paper.md")),
    ]


def test_a_document_the_fusion_lane_already_pasted_is_not_pasted_again(tmp_path):
    """The two lanes keyed their dedupe set differently — the document lane by
    unit DIRECTORY, the hit lane by FILE path — so the guard could never match
    and every ranked document was emitted twice, verbatim."""
    from tesserae.ask_planner import _build_synthesis_message

    project, alpha, beta = _make_source_project(tmp_path)

    message = _build_synthesis_message(
        "how does it work", [], _hits_for(alpha, beta), source_root=project
    )

    assert message.count('kind="document"') == 2  # the fusion lane ran
    assert message.count("SENTINEL-ALPHA-BODY") == 1
    assert message.count("SENTINEL-BETA-BODY") == 1
