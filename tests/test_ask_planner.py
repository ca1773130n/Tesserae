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
        "SENTINEL-ALPHA-BODY. Zygomorphic flange calibration settles the rig. "
        "It does work once the plate warms.\n",
        encoding="utf-8",
    )
    (beta / "paper.md").write_text(
        "SENTINEL-BETA-BODY. A plate warms and it does work. Nothing settles "
        "the rig here.\n",
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


def test_a_block_sharing_no_rare_term_with_the_question_is_not_admitted(tmp_path):
    """The over-fetch lane's job is coverage, not relevance, so it brings in
    near-miss prose that scored well and is about something else. A block that
    carries none of the question's rare vocabulary cannot answer it."""
    from tesserae.ask_planner import _anchor_terms, _build_synthesis_message

    project, alpha, beta = _make_source_project(tmp_path)
    question = "how does zygomorphic calibration work"

    assert _anchor_terms(project, question) == {"zygomorphic", "calibration"}

    message = _build_synthesis_message(
        question, [], _hits_for(alpha, beta), source_root=project
    )

    assert 'title="Alpha"' in message      # carries both anchors
    assert 'title="Beta"' not in message   # carries neither


def test_the_gate_fails_open_when_the_question_has_no_rare_term(tmp_path):
    """query.py records what a prompt-side constraint that fires
    indiscriminately costs: 59.9% refusals on the ANSWERABLE stratum against a
    6.3% baseline. A gate with nothing to discriminate on removes nothing."""
    from tesserae.ask_planner import _anchor_terms, _build_synthesis_message

    project, alpha, beta = _make_source_project(tmp_path)
    question = "how does it work"

    assert _anchor_terms(project, question) == set()

    message = _build_synthesis_message(
        question, [], _hits_for(alpha, beta), source_root=project
    )

    assert 'title="Alpha"' in message
    assert 'title="Beta"' in message


def test_the_gate_leaves_the_fusion_lane_and_the_graph_evidence_alone(tmp_path):
    """Conservative scope, and it is the whole safety argument: the fusion
    top-10 is byte-for-byte what the hybrid baseline answers from, and the kg:
    blocks are the graph's own dated evidence. Gating either trades measured
    gold coverage for an unmeasured fabrication effect."""
    from tesserae.ask_planner import _build_synthesis_message

    project, alpha, beta = _make_source_project(tmp_path)
    evidence = [{"action": "timeline", "args": {},
                 "content": "2026-01-01 an entirely unrelated dated row"}]

    message = _build_synthesis_message(
        "how does zygomorphic calibration work", evidence,
        _hits_for(alpha, beta), source_root=project,
    )

    assert 'kind="kg:timeline"' in message
    assert "an entirely unrelated dated row" in message
    # Beta lost its over-fetch block but its fusion-ranked document survives.
    assert message.count('kind="document"') == 2
    assert "SENTINEL-BETA-BODY" in message


def test_no_source_root_leaves_the_prompt_exactly_as_it_was(tmp_path):
    """Both lanes this change touches need a project root to read. Without one
    the prompt is what it always was, byte for byte — the callers that pass
    None are unaffected by either the dedupe key or the gate."""
    from tesserae.ask_planner import _build_synthesis_message

    _project, alpha, beta = _make_source_project(tmp_path)

    message = _build_synthesis_message(
        "how does zygomorphic calibration work", [], _hits_for(alpha, beta)
    )

    assert 'kind="document"' not in message
    assert 'title="Alpha"' in message
    assert 'title="Beta"' in message
# --- document provenance: which document is behind a non-wiki step ----------


def _make_provenance_project(tmp_path: Path):
    """A project whose graph nodes name real corpus documents — plus two that
    must never be reported: one outside the project root, one that is gone.

    ``source_path`` is untrusted frontmatter, so the fixture carries the two
    shapes that abuse it: an absolute path pointing out of the tree, and a
    path inside the tree that names no file. Both nodes are dated so they are
    genuinely RETURNED by the primitives below — a guard tested on a node the
    step never reaches is a guard tested on a branch that never executes.
    """
    from tesserae.project import ProjectWiki

    project = tmp_path / "prov-demo"
    (project / ".tesserae" / "wiki" / "concepts").mkdir(parents=True)
    (project / ".tesserae" / "site").mkdir(parents=True)
    (project / ".tesserae" / "config.json").write_text("{}", encoding="utf-8")
    (project / ".tesserae" / "site" / "search-index.json").write_text("[]", encoding="utf-8")
    corpus = project / "corpus"
    corpus.mkdir()
    (corpus / "session-a.md").write_text("# A\nMelanie repainted the kitchen.\n", encoding="utf-8")
    (corpus / "session-b.md").write_text("# B\nThe cache halved compile time.\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("ssh-rsa AAAA...\n", encoding="utf-8")

    nodes = [
        ResearchNode(
            id="Session:in", name="Kitchen session", type=ResearchNodeType.SESSION,
            description="repainting", source_path=str(corpus / "session-a.md"),
            metadata={"started_at": "2026-07-05T10:00:00Z"},
        ),
        ResearchNode(
            id="Session:escapes", name="Escaping session", type=ResearchNodeType.SESSION,
            description="hostile frontmatter", source_path=str(outside / "secret.md"),
            metadata={"started_at": "2026-07-04T10:00:00Z"},
        ),
        ResearchNode(
            id="Session:missing", name="Vanished session", type=ResearchNodeType.SESSION,
            description="document deleted since compile",
            source_path=str(corpus / "deleted-since-compile.md"),
            metadata={"started_at": "2026-07-03T10:00:00Z"},
        ),
        ResearchNode(
            id="SessionInsight:i1", name="Cache halves compile time",
            type=ResearchNodeType.SESSION_INSIGHT, description="cache cut compile 90s to 40s",
            source_path=str(corpus / "session-b.md"),
            metadata={"first_seen_at": "2026-07-05T11:00:00Z", "created_at": "2026-07-05T11:00:00Z"},
        ),
    ]
    edges = [ResearchEdge(source="SessionInsight:i1", target="Session:in",
                          type="derived_from_session", evidence="session finding")]
    (project / ".tesserae" / "graph.json").write_text(
        ResearchGraph(nodes=nodes, edges=edges).to_json(indent=2), encoding="utf-8"
    )
    return ProjectWiki.load(project), corpus, outside


def _doc(directory: Path, name: str) -> str:
    """The canonical form `_confined_doc` emits — `resolve()` is what makes
    `..` and symlinks unfoolable, and on macOS it also expands /var to
    /private/var, so comparing against a raw tmp_path would be platform luck."""
    return (directory / name).resolve().as_posix()


_PROV_STEPS = [
    {"action": "recent_sessions", "args": {"limit": 10}},
    {"action": "session_findings", "args": {"limit": 10}},
    {"action": "timeline", "args": {"query": "cache", "limit": 50}},
    {"action": "search_facts", "args": {"query": "cache", "limit": 10}},
    {"action": "activity_summary", "args": {}},
]


def _prov_envelope(wiki, steps=None, **kw):
    client = FakeClient(
        {"reasoning": "r", "steps": list(steps if steps is not None else _PROV_STEPS)},
        "Answer [kg-step-1-recent_sessions].",
    )
    envelope = plan_and_answer(wiki, "q", client=client, **kw)
    assert envelope is not None
    return envelope, client


def test_provenance_is_off_by_default_and_adds_no_key(tmp_path):
    """The product's ask path must be byte-identical to not having this
    feature: `ask_project` never passes the flag, so no `sources` key may
    appear in the envelope it publishes."""
    wiki, _corpus, _outside = _make_provenance_project(tmp_path)

    default, _ = _prov_envelope(wiki)
    explicit, _ = _prov_envelope(wiki, provenance=False)

    for envelope in (default, explicit):
        for entry in envelope["plan"]["executed"]:
            assert "sources" not in entry, entry


def test_provenance_carries_the_source_document_of_every_graph_backed_step(tmp_path):
    """Five of the seven non-wiki primitives read graph.json, where 99.7% of
    conv-26's nodes carry a resolving `source_path`. Losing it was a plumbing
    failure at the point of string formatting, not a data-model gap."""
    wiki, corpus, _outside = _make_provenance_project(tmp_path)

    envelope, _ = _prov_envelope(wiki, provenance=True)

    by_action = {e["action"]: e["sources"] for e in envelope["plan"]["executed"]}
    assert by_action["recent_sessions"] == [_doc(corpus, "session-a.md")]
    assert by_action["session_findings"] == [_doc(corpus, "session-b.md")]
    # A projected fact resolves through BOTH endpoints: the insight is the
    # subject, the session it derives from is the object.
    for action in ("timeline", "search_facts"):
        assert by_action[action] == [_doc(corpus, "session-b.md"), _doc(corpus, "session-a.md")], action


def test_provenance_is_recorded_per_step_never_as_a_plan_wide_union(tmp_path):
    """Measured on conv-26: concatenating a real plan's steps scored
    recall@10 0.333 where its own best single step scored 0.583, because the
    good step's rows landed after another step's. Per-step lists let a
    consumer fuse; a union forces plan-order concatenation, which is the
    position-dependence this repo has reverted for before."""
    wiki, corpus, _outside = _make_provenance_project(tmp_path)

    envelope, _ = _prov_envelope(wiki, provenance=True)

    executed = envelope["plan"]["executed"]
    assert [e["action"] for e in executed] == [s["action"] for s in _PROV_STEPS]
    # Step 1 saw only the session document, step 2 only the insight's — no
    # accumulator leaked one step's provenance into the next.
    assert executed[0]["sources"] == [_doc(corpus, "session-a.md")]
    assert executed[1]["sources"] == [_doc(corpus, "session-b.md")]
    assert "sources" not in envelope
    assert "sources" not in envelope["plan"]


def test_provenance_never_names_a_document_outside_the_project_root(tmp_path):
    """`source_path` arrives from document frontmatter and is UNTRUSTED. A
    node declaring an absolute path out of the tree must not get that path
    echoed back as "the document behind this evidence"."""
    wiki, _corpus, outside = _make_provenance_project(tmp_path)

    envelope, client = _prov_envelope(wiki, provenance=True)

    every = [s for e in envelope["plan"]["executed"] for s in e["sources"]]
    assert every, "no provenance at all — the guard would pass vacuously"
    assert not [s for s in every if "secret" in s or str(outside) in s], every
    # And the node itself still reached the model — the guard drops the PATH,
    # not the evidence row, so this is not passing by returning nothing.
    assert "Escaping session" in client.text_calls[0]["user"]


def test_provenance_never_names_a_document_that_does_not_exist(tmp_path):
    """Provenance naming a document that is not on disk is INVENTED
    provenance, which is worse than none: `_confined_doc` requires
    `is_file()`, so "we never fabricate a source" is a property of the code."""
    wiki, corpus, _outside = _make_provenance_project(tmp_path)
    ghost = _doc(corpus, "deleted-since-compile.md")
    assert not Path(ghost).exists()

    envelope, client = _prov_envelope(wiki, provenance=True)

    every = [s for e in envelope["plan"]["executed"] for s in e["sources"]]
    assert ghost not in every, every
    assert "Vanished session" in client.text_calls[0]["user"]


def test_provenance_leaves_the_evidence_and_the_synthesis_prompt_untouched(tmp_path):
    """This buys SCOREABILITY, not answer quality, and the code has to say so:
    provenance travels beside the answer in `plan.executed`, never into the
    evidence text, the prompt or `hits`. The same question must answer
    identically with the flag on and off."""
    wiki, _corpus, _outside = _make_provenance_project(tmp_path)

    off, off_client = _prov_envelope(wiki)
    on, on_client = _prov_envelope(wiki, provenance=True)

    assert on_client.text_calls[0]["user"] == off_client.text_calls[0]["user"]
    assert on_client.json_calls[0]["user"] == off_client.json_calls[0]["user"]
    assert on["hits"] == off["hits"]
    assert on["answer"] == off["answer"]
    assert on["plan"]["steps"] == off["plan"]["steps"]
    assert [{k: v for k, v in e.items() if k != "sources"} for e in on["plan"]["executed"]] \
        == off["plan"]["executed"]


def test_transcript_backed_primitives_report_no_document_provenance(tmp_path):
    """`activity_summary` and `decisions` read Claude Code transcripts and git,
    never graph.json — there is no corpus document behind their rows. They
    report an empty list rather than a missing key, so "nothing to carry" is
    distinguishable from "nobody wired this up"."""
    wiki, _corpus, _outside = _make_provenance_project(tmp_path)

    envelope, _ = _prov_envelope(wiki, provenance=True)

    entry = [e for e in envelope["plan"]["executed"] if e["action"] == "activity_summary"][0]
    assert entry["sources"] == []


def test_node_ts_reads_the_timestamp_keys_a_corpus_actually_carries():
    """`started_at`/`created_at`/`ts` alone silently zeroed the dated primitives.

    All 8 Session nodes in the compiled LoCoMo conv-26 graph carry `date` /
    `chat_time` / `timestamp`, so `recent_sessions` returned nothing for ANY
    `since` — and an empty result reads as "nothing happened in that window"
    rather than "this node's clock is written somewhere I do not look".
    """
    from tesserae.ask_planner import _node_ts
    from tesserae.research_graph import ResearchNode, ResearchNodeType

    def node(**md):
        return ResearchNode(id="n", name="n", type=ResearchNodeType.CONCEPT,
                            description="d", metadata=md)

    for key in ("started_at", "created_at", "ts", "timestamp", "date", "chat_time"):
        assert _node_ts(node(**{key: "2023-05-07"})) == "2023-05-07", key
    assert _node_ts(node()) == ""
    # Precedence: the explicit session clocks win over the generic ones.
    assert _node_ts(node(started_at="A", date="B")) == "A"


def test_a_step_that_raised_reports_no_sources():
    """A failed step must not carry evidence of success.

    `entry["sources"]` was written unconditionally under the flag, so a
    primitive that recorded two documents and then threw came back `ok=False`
    with a populated `sources` list — the same shape as the report that printed
    "every one of the 199 queries returned the full evidence budget" while all
    199 searches had raised.
    """
    import tesserae.ask_planner as AP

    entry = {"action": "timeline", "ok": False}
    sources = ["/a.md", "/b.md"]
    # The shipped expression, exercised directly: ok=False must yield [].
    assert (list(sources) if entry.get("ok", True) else []) == []
    entry_ok = {"action": "timeline", "ok": True}
    assert (list(sources) if entry_ok.get("ok", True) else []) == sources
    # And the real code path carries the same guard.
    import inspect
    src = inspect.getsource(AP._plan_and_answer)
    assert 'if entry.get("ok", True) else []' in src


# ------------------------------- per-sentence review flags on the envelope


def test_the_envelope_carries_unsupported_sentences_and_a_rate():
    """`grounding` is one number for a whole answer — it says something is wrong
    but not WHERE. These name the sentences and the words that were missing."""
    from tesserae.verify_answer import check_against_evidence

    evidence = ("ResNet-50 was evaluated on the ImageNet benchmark and reached "
                "76.1 top-1 accuracy under a single-crop protocol.")
    answer = ("ResNet-50 reached 76.1 top-1 accuracy on ImageNet. "
              "It also outperformed DenseNet on CIFAR-100 segmentation tasks.")
    report = check_against_evidence(answer, evidence)
    flagged = report.flagged()
    assert len(flagged) == 1
    assert "densenet" in flagged[0].missing
    assert report.supported_rate == 0.5


def test_ask_planner_computes_the_flags_from_its_own_source_blocks():
    """The flags must come from the SAME blocks the grounding score used. A
    recomputation from hit excerpts would judge the answer against text the
    model was never shown."""
    import inspect

    from tesserae import ask_planner

    src = inspect.getsource(ask_planner)
    assert "check_against_evidence" in src, "the evidence check is not wired into ask"
    assert 'envelope: Dict[str, Any] = {' in src
    assert '"unsupported": _unsupported' in src
    # bound before the try that can fail on import — otherwise the second
    # consumer swallows a NameError and reports "no flags" for an unchecked query
    assert "_sources: List[str] = []" in src


# ------------------------------------------------- band adjudication ---
# The cascade is opt-in on purpose: `check_against_evidence` is documented as
# costing no tokens and no network, and a cascade that switched itself on would
# break that for every existing caller.

def test_band_adjudication_is_on_unless_switched_off(monkeypatch):
    """The free check alone is less accurate than a model (0.872 vs 0.928 on
    750 held-out pairs) and no model-free variant closes that; the cascade
    does (0.935 on 40% of the calls). Inside `ask` a client is already in hand,
    so the accurate behaviour is the default and `off` is the opt-out."""
    from tesserae.ask_planner import _verify_band
    from tesserae.verify_answer import UNCERTAIN_HIGH, UNCERTAIN_LOW

    monkeypatch.delenv("TESSERAE_VERIFY_BAND", raising=False)
    assert _verify_band() == (UNCERTAIN_LOW, UNCERTAIN_HIGH)
    for off in ("0", "off", "false", "no", "OFF"):
        monkeypatch.setenv("TESSERAE_VERIFY_BAND", off)
        assert _verify_band() is None, f"{off!r} must disable the cascade"


def test_band_adjudication_reads_on_and_explicit_bands(monkeypatch):
    from tesserae.ask_planner import _verify_band
    from tesserae.verify_answer import UNCERTAIN_HIGH, UNCERTAIN_LOW

    for on in ("", "1", "on", "true", "yes", "default", "ON"):
        monkeypatch.setenv("TESSERAE_VERIFY_BAND", on)
        assert _verify_band() == (UNCERTAIN_LOW, UNCERTAIN_HIGH)
    monkeypatch.setenv("TESSERAE_VERIFY_BAND", "0.25-0.80")
    assert _verify_band() == (0.25, 0.80)


def test_an_unreadable_band_is_reported_not_swallowed(monkeypatch, capsys):
    """A silently dropped setting looks exactly like a cascade that ran and
    found nothing, which is the failure this file has been bitten by before."""
    from tesserae.ask_planner import _verify_band

    for bad in ("garbage", "0.9-0.2", "1.5-2.0", "a-b"):
        monkeypatch.setenv("TESSERAE_VERIFY_BAND", bad)
        assert _verify_band() is None
        assert bad in capsys.readouterr().err


def test_the_judge_reads_only_a_recognised_verdict():
    """Anything else must return None so the deterministic verdict stands."""
    from tesserae.ask_planner import _model_judge

    class Client:
        def __init__(self, reply):
            self.reply = reply

        def complete_json(self, **_kw):
            return self.reply

    assert _model_judge(Client({"verdict": "supported"}))("s", "e") == "SUPPORTED"
    assert _model_judge(Client({"verdict": "UNSUPPORTED"}))("s", "e") == "UNSUPPORTED"
    for junk in (None, {}, {"verdict": "MAYBE"}, [], "SUPPORTED"):
        assert _model_judge(Client(junk))("s", "e") is None


def test_the_cascade_is_wired_into_ask():
    import inspect

    from tesserae import ask_planner

    src = inspect.getsource(ask_planner)
    assert "adjudicate_uncertain" in src, "the cascade is not wired into ask"
    assert '"adjudicated": _adjudicated' in src, "the envelope must report the count"
    # None when the cascade never ran, an int when it did — a consumer pricing
    # calls has to tell "off" from "on and nothing was in the band".
    assert "_adjudicated: Optional[int] = None" in src


class VerdictClient(FakeClient):
    """A client that answers the band adjudicator as well as the planner.

    ``FakeClient`` returns the plan for every ``complete_json`` call, so the
    judge would read no verdict from it and fall back — which is correct
    behaviour but proves nothing about the cascade running.
    """

    def __init__(self, plan, answer, verdict="SUPPORTED"):
        super().__init__(plan, answer)
        self._verdict = verdict
        self.verdict_calls = []

    def complete_json(self, *, system, user, schema_name, **kw):
        if schema_name == "answer_sentence_verdict":
            self.verdict_calls.append(user)
            return {"verdict": self._verdict}
        return super().complete_json(system=system, user=user,
                                     schema_name=schema_name, **kw)


def test_envelope_reports_no_cascade_when_it_is_off(tmp_path, monkeypatch):
    monkeypatch.setenv("TESSERAE_VERIFY_BAND", "off")
    wiki = _make_project(tmp_path)
    client = VerdictClient(PLAN, "Recently the extraction cache shipped [kg-step-1-recent_sessions].")

    envelope = plan_and_answer(wiki, "what happened recently?", client=client)

    assert envelope is not None
    # None, not 0: "the cascade did not run" is a different fact from "it ran
    # and re-decided nothing", and a consumer pricing calls needs both.
    assert envelope["adjudicated"] is None
    assert client.verdict_calls == [], "nothing may be paid for while it is off"


def test_the_cascade_actually_re_decides_sentences_when_enabled(tmp_path, monkeypatch):
    # Full-width band: every checkable sentence is uncertain, so the count is
    # deterministic rather than a hostage to the fixture's vocabulary overlap.
    monkeypatch.setenv("TESSERAE_VERIFY_BAND", "0.0-1.0")
    wiki = _make_project(tmp_path)
    client = VerdictClient(PLAN, "Recently the extraction cache shipped [kg-step-1-recent_sessions].")

    envelope = plan_and_answer(wiki, "what happened recently?", client=client)

    assert envelope is not None
    assert isinstance(envelope["adjudicated"], int)
    assert envelope["adjudicated"] >= 1, "the band was total; something must have been judged"
    assert len(client.verdict_calls) == envelope["adjudicated"]
    # The judge is asked about a sentence against the evidence, not the question.
    assert "SENTENCE:" in client.verdict_calls[0]
    assert "EVIDENCE:" in client.verdict_calls[0]
    # A judge saying SUPPORTED cannot leave anything flagged.
    assert envelope["unsupported"] == []
    assert envelope["supported_rate"] == 1.0


def test_a_judge_saying_unsupported_flags_through_the_envelope(tmp_path, monkeypatch):
    monkeypatch.setenv("TESSERAE_VERIFY_BAND", "0.0-1.0")
    wiki = _make_project(tmp_path)
    client = VerdictClient(PLAN, "Recently the extraction cache shipped [kg-step-1-recent_sessions].",
                           verdict="UNSUPPORTED")

    envelope = plan_and_answer(wiki, "what happened recently?", client=client)

    assert envelope is not None
    assert envelope["adjudicated"] >= 1
    assert envelope["unsupported"], "the judge's verdict must reach the envelope"
    assert envelope["supported_rate"] == 0.0



def test_the_cascade_runs_by_default_when_a_client_is_in_hand(tmp_path, monkeypatch):
    monkeypatch.delenv("TESSERAE_VERIFY_BAND", raising=False)
    wiki = _make_project(tmp_path)
    client = VerdictClient(PLAN, "Recently the extraction cache shipped [kg-step-1-recent_sessions].")

    envelope = plan_and_answer(wiki, "what happened recently?", client=client)

    assert envelope is not None
    # ran: an int, never None — even when nothing fell in the band
    assert isinstance(envelope["adjudicated"], int)
    assert len(client.verdict_calls) == envelope["adjudicated"]
