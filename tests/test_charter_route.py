"""Tests for ``charter_route`` — the one-call route over the charter.

Every test here builds its charter and graph by hand. Nothing compiles a
knowledge graph and nothing reaches an LLM: routing is an LLM-free read over
``charter.json`` plus a cache it may or may not find warm, and a test that
needed either would be testing the wrong thing.

The embedding backend is pinned to ``HashEmbeddingBackend`` throughout. The
default backend is whatever ``active_embedding_backend`` resolves on the host
— model2vec here, possibly nothing on CI — and a route's ranking is allowed to
vary with it. Pinning the stub is what makes these assertions about the
router's own guarantees rather than about the machine they ran on.
"""

from __future__ import annotations

import json

import pytest

from tesserae.charter_route import ROUTE_ALTITUDES, charter_route
from tesserae.research_graph import ResearchGraph, ResearchNode, ResearchNodeType
from tesserae.retrieval.hybrid import HashEmbeddingBackend


def _node(node_id: str, name: str, node_type: ResearchNodeType) -> ResearchNode:
    return ResearchNode(id=node_id, name=name, type=node_type)


@pytest.fixture()
def graph() -> ResearchGraph:
    return ResearchGraph(
        nodes=[
            _node("n:splat", "Gaussian Splatting", ResearchNodeType.RESEARCH_TOPIC),
            _node("n:raster", "Differentiable Rasterization", ResearchNodeType.CONCEPT),
            _node("n:sh", "Spherical Harmonics", ResearchNodeType.CONCEPT),
            _node("n:sessions", "Session Monitoring", ResearchNodeType.PROJECT),
            _node("n:daemon", "Watcher Daemon", ResearchNodeType.CONCEPT),
            _node("n:old", "Retired Subject", ResearchNodeType.CONCEPT),
        ],
        edges=[],
    )


def _domain(**overrides) -> dict:
    entry = {
        "tier": 1,
        "own_altitude": "division",
        "parent_slug": None,
        "child_slugs": [],
        "anchor_id": "",
        "direct_member_ids": [],
        "member_count": 0,
        "reorg_seq": 0,
        "status": "live",
        "transition": "founded",
        "unsplittable": False,
    }
    entry.update(overrides)
    return entry


@pytest.fixture()
def charter() -> dict:
    return {
        "version": 1,
        "reorg_seq": 0,
        "domains": {
            "gaussian-splatting": _domain(
                tier=1,
                own_altitude="division",
                child_slugs=["differentiable-rasterization"],
                anchor_id="n:splat",
                direct_member_ids=["n:splat"],
                member_count=3,
            ),
            "differentiable-rasterization": _domain(
                tier=2,
                own_altitude="department",
                parent_slug="gaussian-splatting",
                anchor_id="n:raster",
                direct_member_ids=["n:raster", "n:sh"],
                member_count=2,
            ),
            "session-monitoring": _domain(
                tier=1,
                own_altitude="division",
                anchor_id="n:sessions",
                direct_member_ids=["n:sessions", "n:daemon"],
                member_count=2,
            ),
            "retired-subject": _domain(
                tier=1,
                own_altitude="division",
                anchor_id="n:old",
                direct_member_ids=["n:old"],
                member_count=1,
                status="retired",
                transition="retired",
            ),
        },
        "member_index": {
            "n:splat": "gaussian-splatting",
            "n:raster": "differentiable-rasterization",
            "n:sh": "differentiable-rasterization",
            "n:sessions": "session-monitoring",
            "n:daemon": "session-monitoring",
        },
    }


def _route(graph, charter, task, **kwargs):
    return charter_route(
        graph, charter, task, backend=HashEmbeddingBackend(), **kwargs
    )


def _slugs(payload) -> list:
    return [card["slug"] for card in payload["path"]]


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


def test_routes_to_the_division_whose_subject_the_task_names(graph, charter):
    payload = _route(graph, charter, "how is session monitoring wired up")
    assert payload["routed"] is True
    assert _slugs(payload) == ["session-monitoring"]
    assert payload["brief"]["slug"] == "session-monitoring"


def test_descends_to_the_child_that_holds_the_evidence(graph, charter):
    """A task naming a department's subject must reach the department.

    The division's own row is its slug and anchor name only, so a task about
    rasterization matches NOTHING in it. Ranking divisions on their own text
    would refuse this route outright while the domain that holds the subject
    sat one level down, scored and unreachable — which is why the descent
    scores a subtree rather than a row.
    """
    payload = _route(graph, charter, "differentiable rasterization backward pass")
    assert _slugs(payload) == ["gaussian-splatting", "differentiable-rasterization"]
    assert payload["path"][0]["rrf_score"] == 0.0
    assert payload["path"][1]["rrf_score"] > 0.0


def test_parent_and_siblings_are_the_routed_domain_s_neighbours(graph, charter):
    payload = _route(graph, charter, "differentiable rasterization backward pass")
    assert payload["parent"]["slug"] == "gaussian-splatting"
    assert payload["siblings"] == []

    top = _route(graph, charter, "session monitoring")
    assert top["parent"] is None
    # Live roots only — the tombstoned division is not a destination and is
    # not offered as an alternative one either.
    assert [card["slug"] for card in top["siblings"]] == ["gaussian-splatting"]


def test_a_retired_domain_is_never_routed_into(graph, charter):
    payload = _route(graph, charter, "retired subject")
    assert payload["routed"] is False
    assert "retired-subject" not in json.dumps(payload)


def test_two_identical_calls_return_identical_payloads(graph, charter):
    task = "differentiable rasterization backward pass"
    assert _route(graph, charter, task) == _route(graph, charter, task)


# ---------------------------------------------------------------------------
# Refusal — the load-bearing half
# ---------------------------------------------------------------------------


def _assert_names_nothing(payload, charter):
    """A refusal must be indistinguishable from nothing, not from a weak guess."""
    assert payload["routed"] is False
    assert payload["path"] == []
    assert payload["brief"] is None
    assert payload["parent"] is None
    assert payload["siblings"] == []
    assert payload["note"]
    blob = json.dumps(payload)
    for slug in charter["domains"]:
        assert slug not in blob


def test_an_unplaceable_task_names_no_domain_at_all(graph, charter):
    payload = _route(graph, charter, "quarterly payroll withholding deadlines")
    _assert_names_nothing(payload, charter)


def test_a_term_every_domain_shares_is_not_evidence(graph, charter):
    """The seventh overstatement, refused.

    Every corpus row carries its synthetic node type as text, so a task
    containing that word scores one identical lexical hit on every row:
    ``hybrid_search`` admits the whole corpus, the fused scores are
    indistinguishable, and a slug tiebreak would pick a "winner" with no
    evidence behind it whatsoever. A term shared by every domain discriminates
    nothing and must not route.
    """
    payload = _route(graph, charter, "concept")
    _assert_names_nothing(payload, charter)
    assert payload["route_quality"]["evidenced_rows"] == 0


def test_no_charter_is_a_refusal_not_an_error(graph):
    payload = charter_route(graph, None, "anything", backend=HashEmbeddingBackend())
    assert payload["routed"] is False
    assert "charter" in payload["note"]


def test_a_charter_with_no_live_domain_refuses(graph, charter):
    for entry in charter["domains"].values():
        entry["status"] = "retired"
    payload = _route(graph, charter, "session monitoring")
    assert payload["routed"] is False
    assert payload["route_quality"]["corpus_rows"] == 0


def test_an_empty_task_refuses_rather_than_taking_the_first_row(graph, charter):
    """``hybrid_search`` short-circuits an empty query to input order at score
    0, which would hand back whichever domain sorted first as though it had
    been chosen."""
    payload = _route(graph, charter, "   ")
    _assert_names_nothing(payload, charter)



class _StubSemanticBackend:
    """A deterministic stand-in for a real embedding model.

    Not the hash stub, so ``backend_is_semantic`` is True and the router
    credits embedding-only evidence — which is the behaviour under test.
    Deterministic by construction so this asserts the router's rule rather
    than a model's opinion, and so it means the same thing on CI, where
    whichever model the host happens to have installed is not present.
    """

    name = "stub-semantic"
    dim = 2
    _NEAR = ("session", "monitor", "watch", "daemon", "telemetry")

    def embed(self, texts):
        return [
            [1.0, 0.0] if any(w in t.casefold() for w in self._NEAR) else [0.0, 1.0]
            for t in texts
        ]


def test_a_semantic_only_route_is_labelled_as_one(graph, charter):
    """A route the embedding lane alone produced must say so.

    Measured on the live 780-row charter: the one-word task "concept" is
    refused on the hash stub and routes on model2vec, where 238 rows clear the
    cosine floor. Refusing that outright would need a diffuseness threshold no
    design specifies; reporting which lane carried it costs nothing and lets a
    caller see that this answer moves with the machine. The task below shares
    no token with any row, so lexical evidence is structurally impossible.
    """
    task = "telemetry"
    lexical = _route(graph, charter, task)
    assert lexical["routed"] is False

    semantic = charter_route(graph, charter, task, backend=_StubSemanticBackend())
    assert semantic["routed"] is True
    assert _slugs(semantic) == ["session-monitoring"]
    assert semantic["path"][0]["evidence"] == "semantic"
    assert semantic["route_quality"]["evidence"] == "semantic"
    assert semantic["route_quality"]["semantic"] is True


def test_a_lexical_route_is_labelled_as_one_and_a_pass_through_is_not(graph, charter):
    payload = _route(graph, charter, "differentiable rasterization backward pass")
    # The division was walked through, not matched: its own row scored nothing.
    assert payload["path"][0]["evidence"] == "none"
    assert payload["path"][1]["evidence"] == "lexical"
    assert payload["route_quality"]["evidence"] == "lexical"


def test_a_refusal_characterises_no_evidence(graph, charter):
    payload = _route(graph, charter, "quarterly payroll withholding deadlines")
    assert payload["route_quality"]["evidence"] is None

# ---------------------------------------------------------------------------
# Altitude
# ---------------------------------------------------------------------------


def test_a_pinned_altitude_stops_the_descent_there(graph, charter):
    payload = _route(
        graph, charter, "differentiable rasterization backward pass", altitude="division"
    )
    assert _slugs(payload) == ["gaussian-splatting"]
    assert payload["route_quality"]["altitude_reached"] == "division"


def test_an_unreached_altitude_reports_the_one_actually_reached(graph, charter):
    payload = _route(graph, charter, "session monitoring", altitude="team")
    assert _slugs(payload) == ["session-monitoring"]
    assert payload["route_quality"]["altitude"] == "team"
    assert payload["route_quality"]["altitude_reached"] == "division"


def _deep_branch(graph, charter) -> None:
    """A three-level branch carrying NO ``department``.

    Legal by construction: ``charter._altitude_for`` labels a tier-2 domain
    ``department`` only at 100+ members, so a small one is a ``team`` and the
    branch skips the middle label entirely. This is the shape a label-equality
    stop cannot cap.
    """
    graph.nodes.append(
        _node("n:restart", "Daemon Restart Backoff", ResearchNodeType.CONCEPT)
    )
    charter["domains"]["session-monitoring"]["child_slugs"] = ["capture-pipeline"]
    charter["domains"]["capture-pipeline"] = _domain(
        tier=2,
        own_altitude="team",
        parent_slug="session-monitoring",
        child_slugs=["daemon-restart-backoff"],
        anchor_id="",
        direct_member_ids=[],
        member_count=1,
    )
    charter["domains"]["daemon-restart-backoff"] = _domain(
        tier=3,
        own_altitude="team",
        parent_slug="capture-pipeline",
        anchor_id="n:restart",
        direct_member_ids=["n:restart"],
        member_count=1,
    )
    charter["member_index"]["n:restart"] = "daemon-restart-backoff"


def test_an_altitude_absent_from_the_branch_still_caps_the_walk(graph, charter):
    """``altitude`` is a CAP, not a label to match.

    Stopping only on ``own_altitude == altitude`` capped nothing when the
    requested label was missing from the chosen branch: the walk ran to the
    leaf, so ``--altitude department`` could land DEEPER than
    ``--altitude team``. For a parameter whose whole purpose is to bound
    depth that is incoherent, and it is invisible to a caller — both answers
    look like routes.

    What must hold is the ORDER: division <= department <= team <= auto,
    whatever labels the branch happens to carry.
    """
    _deep_branch(graph, charter)
    task = "daemon restart backoff"

    depths = {
        pin: len(_slugs(_route(graph, charter, task, altitude=pin)))
        for pin in ("division", "department", "team", "auto")
    }
    assert depths["division"] <= depths["department"] <= depths["team"] <= depths["auto"]
    # The branch has no department, so the cap lands on the first level past
    # it rather than running to the leaf the way equality did.
    assert _slugs(_route(graph, charter, task, altitude="department")) == [
        "session-monitoring",
        "capture-pipeline",
    ]
    assert _slugs(_route(graph, charter, task, altitude="team")) == [
        "session-monitoring",
        "capture-pipeline",
    ]
    assert _slugs(_route(graph, charter, task, altitude="division")) == [
        "session-monitoring"
    ]
    # ...and "auto" still reaches the domain that actually holds the evidence.
    assert _slugs(_route(graph, charter, task)) == [
        "session-monitoring",
        "capture-pipeline",
        "daemon-restart-backoff",
    ]


def test_a_capped_walk_reports_the_altitude_it_stopped_at(graph, charter):
    """Not the one that was asked for: the branch carries no ``department``,
    and inventing the requested label would be the payload lying about where
    the caller landed."""
    _deep_branch(graph, charter)
    quality = _route(
        graph, charter, "daemon restart backoff", altitude="department"
    )["route_quality"]
    assert quality["altitude"] == "department"
    assert quality["altitude_reached"] == "team"


def test_an_unknown_altitude_raises_rather_than_silently_meaning_auto(graph, charter):
    with pytest.raises(ValueError, match="unknown altitude"):
        _route(graph, charter, "session monitoring", altitude="continent")
    assert "auto" in ROUTE_ALTITUDES


# ---------------------------------------------------------------------------
# Honesty reporting
# ---------------------------------------------------------------------------


def test_route_quality_reports_the_machinery_that_answered(graph, charter):
    payload = _route(graph, charter, "session monitoring")
    quality = payload["route_quality"]
    assert quality["best_effort"] is True
    # The hash stub is not a semantic backend, and a caller must be able to
    # tell that from the payload rather than from the host's installed extras.
    assert quality["semantic"] is False
    assert quality["corpus_rows"] == 3
    assert quality["warm_rows"] == 0
    assert quality["evidenced_rows"] >= 1


def test_a_cold_summary_cache_degrades_the_brief_and_says_so(graph, charter, tmp_path):
    cache_dir = tmp_path / "community_summaries"
    cache_dir.mkdir()
    payload = _route(
        graph, charter, "session monitoring", summary_cache_dir=cache_dir
    )
    assert payload["route_quality"]["warm_rows"] == 0
    assert payload["brief"]["quality"] == "structural"
    assert payload["brief"]["title"]


class _StubBriefClient:
    """A ``json_client`` returning one fixed valid envelope. No LLM, no network.

    Handed to the REAL writer rather than used to hand-build a cache file,
    because a fixture that spells the cache convention out for itself is
    exactly what let this router and #166's writer disagree while the suite
    stayed green: the router read ``<tier>/<slug>.json`` digested over the 25
    lowest-sorting member ids, the writer wrote
    ``<tier>/CharterDomain_<slug>.json`` digested over the top 25 by
    ``(-degree, id)``. Two independent mismatches, so ``warm_rows`` could
    never be anything but 0 — and the test that was here asserted 1, because
    it wrote the file the same wrong way the reader read it.
    """

    TITLE = "Live session capture"
    DESCRIPTION = "Watches agent transcripts and ingests turns as they land."
    TAGS = ["telemetry", "transcripts"]

    def complete_json(self, **_kwargs: object) -> dict:
        return {
            "title": self.TITLE,
            "description": self.DESCRIPTION,
            "tags": list(self.TAGS),
        }


def _write_brief(charter, graph, slug, cache_dir, client=None):
    """Write ``slug``'s brief through #166's writer — the only one there is."""
    from tesserae.charter import materialize_domain_brief
    from tesserae.hierarchy import undirected_degrees

    return materialize_domain_brief(
        charter,
        slug,
        {node.id: node for node in graph.nodes},
        undirected_degrees(graph),
        cache_dir=cache_dir,
        json_client=client or _StubBriefClient(),
    )


def test_a_brief_written_by_the_real_writer_feeds_the_corpus_and_the_brief(
    graph, charter, tmp_path
):
    """The end-to-end pairing, through ``materialize_domain_brief``.

    Two things are proved at once, and the first is the one that was broken:
    the brief's PROSE enters the row's corpus text — the task names only words
    the brief carries, so a cold corpus refuses it outright — and the routed
    payload serves the same brief at ``quality: "llm"``.
    """
    cache_dir = tmp_path / "community_summaries"
    cache_dir.mkdir()
    task = "ingesting agent transcripts"

    # Cold: nothing in the charter carries these words, so the router refuses.
    cold = _route(graph, charter, task, summary_cache_dir=cache_dir)
    assert cold["routed"] is False
    assert cold["route_quality"]["warm_rows"] == 0

    assert _write_brief(charter, graph, "session-monitoring", cache_dir) is not None

    payload = _route(graph, charter, task, summary_cache_dir=cache_dir)
    assert payload["route_quality"]["warm_rows"] == 1
    assert payload["routed"] is True
    assert _slugs(payload) == ["session-monitoring"]
    assert payload["brief"]["quality"] == "llm"
    assert payload["brief"]["title"] == _StubBriefClient.TITLE
    assert payload["brief"]["tags"] == _StubBriefClient.TAGS
    assert payload["brief"]["summary"] == _StubBriefClient.DESCRIPTION


def test_the_router_reads_the_path_the_writer_actually_wrote(
    graph, charter, tmp_path
):
    """A regression pin on the cid convention itself.

    The suite above would go green again on any pair of readers and writers
    that agree with EACH OTHER, including a second private convention. This
    asserts the file on disk is the one ``charter.brief_cid`` names, so the
    router cannot quietly re-acquire a key of its own.
    """
    from tesserae.charter import brief_cid
    from tesserae.community_summaries import level_cache_path

    cache_dir = tmp_path / "community_summaries"
    cache_dir.mkdir()
    _write_brief(charter, graph, "session-monitoring", cache_dir)

    written = level_cache_path(cache_dir, 1, brief_cid("session-monitoring"))
    assert written.is_file()
    assert written.name == "CharterDomain_session-monitoring.json"
    # The bare slug is NOT the key, and nothing may fall back to it.
    assert not level_cache_path(cache_dir, 1, "session-monitoring").exists()


def test_routing_a_cold_charter_never_materializes_a_brief(
    graph, charter, tmp_path, monkeypatch
):
    """Routing is a READ. Delegating the warm lookup to ``read_domain_brief``
    must not have handed the router its writer as well: a route that
    summarized a cold domain would put one ``complete_json`` per unbriefed
    domain behind a call an agent makes to decide where to look."""
    import tesserae.community_summaries as cs

    def _explode(*_args, **_kwargs):
        raise AssertionError("routing must not summarize anything")

    monkeypatch.setattr(cs, "summarize_community", _explode)

    cache_dir = tmp_path / "community_summaries"
    cache_dir.mkdir()
    payload = _route(
        graph, charter, "session monitoring", summary_cache_dir=cache_dir
    )
    assert payload["brief"]["quality"] == "structural"
    assert payload["route_quality"]["warm_rows"] == 0


def test_a_domain_orphaned_by_a_retired_parent_is_reachable(graph, charter):
    """The router's root set must be the entry point's root set.

    ``graph_map()`` deliberately SURFACES a live domain whose parent is a
    tombstone (``test_charter.py::
    test_an_orphaned_domain_surfaces_at_the_root_instead_of_vanishing``) and
    ``compile_context(scope='domain:<slug>')`` resolves it. Building roots from
    "has no parent_slug" instead of ``live_divisions`` excluded exactly that
    domain, so an agent was offered a scope the router refused and the whole
    subtree beneath it was invisible to routing.
    """
    from tesserae.charter import live_divisions

    charter["domains"]["gaussian-splatting"]["status"] = "retired"
    charter["domains"]["gaussian-splatting"]["transition"] = "retired"
    # Still live, still naming the tombstone as its parent.
    assert "differentiable-rasterization" in live_divisions(charter)

    payload = _route(graph, charter, "differentiable rasterization backward pass")
    assert payload["routed"] is True
    assert _slugs(payload) == ["differentiable-rasterization"]
    # A tombstone is never presented as a parent...
    assert payload["parent"] is None
    # ...and at the root the alternatives are the other live divisions, not
    # merely the domains that happen to share the same dead parent_slug.
    assert [card["slug"] for card in payload["siblings"]] == ["session-monitoring"]


def test_a_hand_edited_child_cycle_does_not_hang_the_router(graph, charter):
    """``charter.json`` is a file an operator can edit, and a patched
    ``child_slugs`` cycle must not turn a read into unbounded recursion."""
    charter["domains"]["differentiable-rasterization"]["child_slugs"] = [
        "gaussian-splatting"
    ]
    payload = _route(graph, charter, "differentiable rasterization backward pass")
    assert _slugs(payload) == ["gaussian-splatting", "differentiable-rasterization"]


# ---------------------------------------------------------------------------
# The MCP tool and the CLI verb
# ---------------------------------------------------------------------------


@pytest.fixture()
def project(tmp_path, graph, charter):
    """A project root carrying just the two files the route reads."""
    tess = tmp_path / ".tesserae"
    (tess / "charter").mkdir(parents=True)
    graph_path = tess / "graph.json"
    graph_path.write_text(graph.to_json(indent=2), encoding="utf-8")
    # The CLI verb loads a ProjectWiki, which requires an initialized project.
    (tess / "config.json").write_text("{}\n", encoding="utf-8")
    (tess / "charter" / "charter.json").write_text(
        json.dumps(charter), encoding="utf-8"
    )
    return {"root": tmp_path, "graph_path": graph_path}


def test_the_mcp_tool_is_declared_and_dispatches(project):
    from tesserae.mcp_server import LLMWikiMCPServer

    server = LLMWikiMCPServer()
    declared = {tool["name"]: tool for tool in server.list_tools()}
    assert "charter_route" in declared
    # The honesty split rides in the tool description, which is the only
    # thing most agents will ever read about this surface.
    assert "best-effort" in declared["charter_route"]["description"].lower()
    assert declared["charter_route"]["inputSchema"]["required"] == ["task"]

    payload = server.call_tool(
        "charter_route",
        {"graph_path": str(project["graph_path"]), "task": "session monitoring"},
    )
    # Asserted without pinning a winning slug: the embedding lane varies with
    # whichever backend the host resolved, which is exactly what this tool
    # reports rather than hides. What must hold on ANY backend is that lexical
    # evidence produces a route into a domain the charter actually declares.
    assert payload["routed"] is True
    assert payload["path"]
    assert payload["route_quality"]["corpus_rows"] == 3
    assert payload["route_quality"]["best_effort"] is True


def test_an_unreadable_charter_is_not_reported_as_no_charter(project):
    from tesserae.mcp_server import LLMWikiMCPServer

    (project["root"] / ".tesserae" / "charter" / "charter.json").write_text(
        "{truncated", encoding="utf-8"
    )
    server = LLMWikiMCPServer()
    with pytest.raises(ValueError, match="charter"):
        server.call_tool(
            "charter_route",
            {"graph_path": str(project["graph_path"]), "task": "session monitoring"},
        )


def test_the_cli_verb_emits_json_and_exits_zero_on_a_refusal(project, capsys):
    from tesserae.cli import main

    assert main(["charter-route", "quarterly payroll withholding", "--project", str(project["root"])]) == 0
    payload = json.loads(capsys.readouterr().out)
    # A refusal is an answer, not a failure — a non-zero exit here would make
    # "this task belongs to no domain" look like a broken install.
    assert payload["routed"] is False
    assert payload["path"] == []


def test_the_cli_verb_rejects_an_unknown_altitude(project):
    from tesserae.cli import main

    with pytest.raises(SystemExit):
        main(["charter-route", "anything", "--altitude", "continent", "--project", str(project["root"])])
