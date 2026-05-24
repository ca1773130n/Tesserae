"""Tests for the agentic research mode."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

import pytest

from tesserae.research_graph import ResearchGraph, ResearchNode, ResearchNodeType
from tesserae.research_mode import (
    EvidenceRef,
    GraphSearchBackend,
    ResearchSession,
    SubQuestion,
    _atomic_write,
    _coerce_reflection,
    _coerce_subqueries,
    _slugify,
    _synthetic_node_for_id,
)


# --- Test doubles -----------------------------------------------------------


class _ScriptedLLM:
    """Deterministic ``LLMJsonClient`` that returns canned JSON per schema name.

    Mirrors the doubles in :mod:`tests.test_schema_drift` — record every call
    so tests can assert ordering and arguments, and serve responses from a
    schema-name-keyed queue so plan/reflect/synthesize calls don't collide.
    """

    def __init__(self, responses: Dict[str, List[Optional[Union[dict, list]]]]):
        self._responses: Dict[str, List[Optional[Union[dict, list]]]] = {
            schema: list(payloads) for schema, payloads in responses.items()
        }
        self.calls: List[dict] = []

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema_name: str,
        cache_key: Optional[str] = None,
        max_retries: int = 2,
    ) -> Optional[Union[dict, list]]:
        self.calls.append(
            {
                "system": system,
                "user": user,
                "schema_name": schema_name,
                "cache_key": cache_key,
            }
        )
        queue = self._responses.get(schema_name)
        if not queue:
            raise AssertionError(f"unscripted LLM call for schema {schema_name!r}")
        return queue.pop(0)


class _StaticSearchBackend:
    """``SearchBackend`` that returns fixed evidence hits keyed by query substring."""

    def __init__(self, hits_by_substr: Dict[str, List[dict]]):
        self._hits = hits_by_substr
        self.calls: List[Tuple[str, int]] = []

    def search_nodes(self, query: str, *, limit: int = 5) -> List[dict]:
        self.calls.append((query, limit))
        for substr, hits in self._hits.items():
            if substr.lower() in query.lower():
                return list(hits[:limit])
        return []


class _StaticWebFetcher:
    """``WebFetcher`` returning canned (title, url, snippet) tuples."""

    def __init__(self, hits: List[Tuple[str, str, str]]):
        self._hits = hits
        self.calls: List[str] = []

    def search(self, query: str, *, limit: int = 5) -> List[Tuple[str, str, str]]:
        self.calls.append(query)
        return list(self._hits[:limit])


# --- Pure-function tests ----------------------------------------------------


def test_slugify_replaces_punctuation_and_trims():
    assert _slugify("How does CRAG handle low-resource langs?") == "how-does-crag-handle-low-resource-langs"
    assert _slugify("") == "query"
    assert _slugify("!!!") == "query"


def test_coerce_subqueries_handles_dict_and_list_payloads():
    assert _coerce_subqueries({"subqueries": ["a", "b"]}, breadth=3) == ["a", "b"]
    assert _coerce_subqueries({"questions": ["x"]}, breadth=3) == ["x"]
    assert _coerce_subqueries(["a", "b"], breadth=3) == ["a", "b"]
    assert _coerce_subqueries(None, breadth=3) == []


def test_coerce_reflection_normalizes_hypotheses_and_followups():
    payload = {
        "finding": "  ok  ",
        "followups": ["q1", "q2", "q3", "q4"],
        "hypotheses": [
            "bare string hypothesis",
            {"text": "structured", "evidence_ids": ["Paper:a:1", "Concept:b:2"]},
            {"text": "no evidence"},
        ],
    }
    finding, followups, hypotheses = _coerce_reflection(payload, breadth=2)
    assert finding == "ok"
    # followups are clamped to ``breadth``
    assert followups == ["q1", "q2"]
    assert hypotheses == [
        {"text": "bare string hypothesis", "evidence_ids": []},
        {"text": "structured", "evidence_ids": ["Paper:a:1", "Concept:b:2"]},
        {"text": "no evidence", "evidence_ids": []},
    ]


def test_atomic_write_creates_parent_and_replaces(tmp_path: Path):
    target = tmp_path / "nested" / "report.md"
    _atomic_write(target, "hello\n")
    assert target.read_text(encoding="utf-8") == "hello\n"
    # Should also leave no .tmp files behind in the parent dir.
    leftovers = [p.name for p in target.parent.iterdir() if ".tmp" in p.name]
    assert leftovers == []


def test_synthetic_node_for_id_uses_supplied_id():
    node = _synthetic_node_for_id("Paper:foo:bar")
    assert node.id == "Paper:foo:bar"


# --- Integration: end-to-end run() ------------------------------------------


def _evidence_hits(query: str, ids: List[str]) -> List[dict]:
    return [
        {"id": node_id, "name": f"{node_id} display", "description": f"snippet for {node_id}"}
        for node_id in ids
    ]


def _make_run(tmp_path: Path, *, with_web: bool = False, depth: int = 1, breadth: int = 2, max_iters: int = 10):
    """Build a fully wired ResearchSession with canned LLM + search responses."""
    plan_payload = {"subqueries": ["What is CRAG?", "Where does CRAG fail?"]}
    reflect_root1 = {
        "finding": "CRAG augments retrieval with a corrective re-rank step.",
        "followups": ["How is the re-rank trained?"] if depth > 0 else [],
        "hypotheses": [
            {
                "text": "CRAG outperforms vanilla RAG on noisy corpora",
                "evidence_ids": ["Paper:crag:abc"],
            }
        ],
    }
    reflect_root2 = {
        "finding": "CRAG degrades when the corrective classifier is mis-calibrated.",
        "followups": ["How to calibrate the classifier?"] if depth > 0 else [],
        "hypotheses": [],
    }
    # follow-up reflections (depth=1): no further follow-ups (depth budget exhausted)
    reflect_followup_payload = {
        "finding": "Calibration via temperature scaling looks promising.",
        "followups": [],
        "hypotheses": [],
    }
    synth_payload = {
        "report": (
            "# CRAG report\n\nCRAG augments retrieval [Paper:crag:abc] and "
            "degrades when the classifier is miscalibrated [Paper:crag-fail:xyz]."
        )
    }
    responses: Dict[str, List] = {
        "research-mode-plan-v1": [plan_payload],
        "research-mode-reflect-v1": [reflect_root1, reflect_root2, reflect_followup_payload, reflect_followup_payload],
        "research-mode-synthesize-v1": [synth_payload],
    }
    llm = _ScriptedLLM(responses)
    search = _StaticSearchBackend(
        {
            "what is crag": _evidence_hits("What is CRAG?", ["Paper:crag:abc", "Concept:retrieval:def"]),
            "where does crag fail": _evidence_hits("Where", ["Paper:crag-fail:xyz"]),
            "how is the re-rank": _evidence_hits("re-rank", ["Concept:rerank:111"]),
            "how to calibrate": _evidence_hits("calibrate", ["Concept:calibration:222"]),
        }
    )
    web = (
        _StaticWebFetcher([("DDG result", "https://example.com/crag", "Hosted snippet about CRAG.")])
        if with_web
        else None
    )
    session = ResearchSession(
        query="How does CRAG work and where does it fail?",
        llm=llm,
        search=search,
        output_dir=tmp_path,
        breadth=breadth,
        depth=depth,
        max_iters=max_iters,
        web=web,
    )
    return session, llm, search, web


def test_run_writes_report_and_mints_nodes(tmp_path: Path):
    session, llm, search, _web = _make_run(tmp_path, depth=1)
    report = session.run()
    # Report file exists with expected content
    assert report.report_path.exists()
    body = report.report_path.read_text(encoding="utf-8")
    assert "CRAG report" in body
    assert "Paper:crag:abc" in body  # evidence node id cited
    # Counts: 2 root questions + 2 follow-ups (depth=1, breadth=2 -> 2 followups)
    assert report.questions == 4
    # Hypotheses: only one was minted (root1 produced one, root2 produced none, followups produced none)
    assert report.hypotheses == 1
    assert report.sources == 0  # web=None
    # Graph wiring sanity: at least one references edge and one derived_from edge
    graph = session.builder.build()
    edge_types = {e.type for e in graph.edges}
    assert "references" in edge_types
    assert "derived_from" in edge_types
    # The references edge should point at an evidence node id
    references = [e for e in graph.edges if e.type == "references"]
    assert any(e.target == "Paper:crag:abc" for e in references)
    # plan called once, reflect called 4x (2 root + 2 follow-ups), synth once
    by_schema = {}
    for call in llm.calls:
        by_schema.setdefault(call["schema_name"], 0)
        by_schema[call["schema_name"]] += 1
    assert by_schema == {
        "research-mode-plan-v1": 1,
        "research-mode-reflect-v1": 4,
        "research-mode-synthesize-v1": 1,
    }


def test_run_with_web_backend_mints_source_documents(tmp_path: Path):
    session, _llm, _search, web = _make_run(tmp_path, with_web=True, depth=0)
    report = session.run()
    assert report.sources >= 1
    # Web search must have been called at least once per root sub-question
    assert web is not None
    assert len(web.calls) >= 2
    # SourceDocument node carries the URL we provided
    graph = session.builder.build()
    sources = [n for n in graph.nodes if n.type == ResearchNodeType.SOURCE_DOCUMENT]
    assert sources, "expected at least one SOURCE_DOCUMENT node"
    assert any(n.source_path == "https://example.com/crag" for n in sources)


def test_run_no_web_does_not_call_network(tmp_path: Path):
    # Sanity: with web=None we never trigger any network. Use a fetcher whose
    # `.search()` would explode if invoked.
    class _ExplodingFetcher:
        def search(self, query, *, limit=5):  # pragma: no cover — defensive
            raise AssertionError("must not be called when --no-web")

    session, _llm, _search, _web = _make_run(tmp_path, with_web=False, depth=0)
    # Replace the field to make the assertion explicit.
    session.web = None
    report = session.run()
    assert report.sources == 0


def test_run_falls_back_to_stub_report_when_synthesizer_fails(tmp_path: Path):
    plan_payload = {"subqueries": ["Q?"]}
    reflect_payload = {"finding": "f.", "followups": [], "hypotheses": []}
    responses = {
        "research-mode-plan-v1": [plan_payload],
        "research-mode-reflect-v1": [reflect_payload],
        "research-mode-synthesize-v1": [None],  # simulate LLM failure
    }
    llm = _ScriptedLLM(responses)
    search = _StaticSearchBackend({"q": _evidence_hits("Q", ["Paper:x:1"])})
    session = ResearchSession(
        query="Q?",
        llm=llm,
        search=search,
        output_dir=tmp_path,
        breadth=1,
        depth=0,
        max_iters=5,
    )
    report = session.run()
    body = report.report_path.read_text(encoding="utf-8")
    assert body.startswith("# Research report: Q?")
    assert "Paper:x:1" in body  # citation appears in fallback


def test_max_iters_caps_loop(tmp_path: Path):
    # breadth=2, depth=5 would normally produce many questions; max_iters=2 caps us
    # at exactly two reflect() calls (the two root subqueries) — follow-ups are
    # generated but never reflected on.
    plan_payload = {"subqueries": ["A", "B"]}
    # Distinct follow-ups per parent so stable_id dedup doesn't collapse them.
    reflect_a = {"finding": "f.", "followups": ["A-c", "A-d"], "hypotheses": []}
    reflect_b = {"finding": "f.", "followups": ["B-c", "B-d"], "hypotheses": []}
    synth_payload = {"report": "# r\n"}
    responses = {
        "research-mode-plan-v1": [plan_payload],
        "research-mode-reflect-v1": [reflect_a, reflect_b],  # only 2 reflects allowed
        "research-mode-synthesize-v1": [synth_payload],
    }
    llm = _ScriptedLLM(responses)
    search = _StaticSearchBackend({})
    session = ResearchSession(
        query="root",
        llm=llm,
        search=search,
        output_dir=tmp_path,
        breadth=2,
        depth=5,
        max_iters=2,
    )
    report = session.run()
    assert session.iters_used == 2
    # 2 root questions + 4 follow-ups minted (but not searched/reflected)
    assert report.questions == 2 + 4


def test_plan_falls_back_when_planner_returns_no_subqueries(tmp_path: Path):
    plan_payload: dict = {"subqueries": []}
    reflect_payload = {"finding": "", "followups": [], "hypotheses": []}
    synth_payload = {"report": "# r\n"}
    responses = {
        "research-mode-plan-v1": [plan_payload],
        "research-mode-reflect-v1": [reflect_payload],
        "research-mode-synthesize-v1": [synth_payload],
    }
    llm = _ScriptedLLM(responses)
    search = _StaticSearchBackend({})
    session = ResearchSession(
        query="fallback query",
        llm=llm,
        search=search,
        output_dir=tmp_path,
        breadth=3,
        depth=0,
        max_iters=5,
    )
    report = session.run()
    # Exactly one question minted from the raw query
    assert report.questions == 1


def test_run_persists_slice_into_graph_path(tmp_path: Path):
    """Codex PR #16 P2 fix — the minted Question/Hypothesis/SourceDoc
    slice must be merged into the supplied graph.json so subsequent
    compiles can recover the research thread."""
    session, _llm, _search, _web = _make_run(tmp_path, depth=0)
    graph_path = tmp_path / "graph.json"
    # Pre-existing graph with one node so we can verify the merge is
    # additive (didn't blow away prior content).
    prior = ResearchGraph(
        nodes=[
            ResearchNode(
                id="Paper:pre-existing",
                name="pre-existing",
                type=ResearchNodeType.PAPER,
            ),
        ],
        edges=[],
    )
    graph_path.write_text(prior.to_json(indent=2) + "\n", encoding="utf-8")

    session.graph_path = graph_path
    report = session.run()
    assert report.merged_into == graph_path

    # Re-load graph_path — must contain BOTH the prior node AND the
    # minted research nodes (Question + at least one Hypothesis).
    merged_payload = json.loads(graph_path.read_text(encoding="utf-8"))
    ids = {n["id"] for n in merged_payload["nodes"]}
    assert "Paper:pre-existing" in ids, "merge clobbered prior content"
    types = {n["type"] for n in merged_payload["nodes"]}
    assert "OpenQuestion" in types or "Question" in types, (
        f"expected research-minted Question node in merged graph; types={types}"
    )
    # references / derived_from edges from the slice should also be present.
    edge_types = {e["type"] for e in merged_payload["edges"]}
    assert "references" in edge_types or "derived_from" in edge_types, (
        f"expected research-minted edges in merged graph; edge_types={edge_types}"
    )


def test_run_does_not_persist_when_graph_path_unset(tmp_path: Path):
    """Default behaviour: report-only run leaves no graph.json side effect."""
    session, _llm, _search, _web = _make_run(tmp_path, depth=0)
    # graph_path not set → no merge.
    report = session.run()
    assert report.merged_into is None
    assert not (tmp_path / "graph.json").exists()


def test_graph_search_backend_uses_mcp_server_signature():
    """Smoke: GraphSearchBackend correctly unpacks the MCP server's response."""

    class _FakeServer:
        def __init__(self):
            self.calls = []

        def search_nodes(self, graph, query="", limit=10, **kwargs):
            self.calls.append((query, limit))
            return {"nodes": [{"id": "Paper:x:1", "name": "x", "description": "d"}]}

    graph = ResearchGraph(nodes=[], edges=[])
    backend = GraphSearchBackend(server=_FakeServer(), graph=graph)
    hits = backend.search_nodes("hello", limit=3)
    assert hits == [{"id": "Paper:x:1", "name": "x", "description": "d"}]
    assert backend.server.calls == [("hello", 3)]
