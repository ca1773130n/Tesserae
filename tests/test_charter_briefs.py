# tests/test_charter_briefs.py
"""A brief is a community summary keyed on a slug, not a second renderer.

Every test here defends one of the two claims that make that true: the cache
KEY stops moving when membership moves, and nothing else about
``materialize_community_summary``'s contract changes — one cold call, digest
invalidation, the citation lint, never raising.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from tesserae.charter import (
    brief_cid,
    build_charter,
    domain_member_ids,
    materialize_domain_brief,
    read_domain_brief,
)
from tesserae.community_summaries import (
    community_id,
    level_cache_path,
    prune_stale_summary_caches,
)
from tesserae.hierarchy import undirected_degrees
from tesserae.research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _fat_node(nid: str, filler: int = 5_000) -> ResearchNode:
    return ResearchNode(
        id=nid, name=nid, type=ResearchNodeType.CONCEPT, description="x" * filler
    )


def _two_fat_triangles_plus_orphan() -> ResearchGraph:
    """The charter fixture from ``tests/test_charter.py``: a tier-1 router that
    holds six members and NONE of them directly, two tier-2 leaves, and a
    non-empty intake."""
    nodes = [_fat_node(f"Concept:a{i}") for i in range(3)]
    nodes += [_fat_node(f"Concept:b{i}") for i in range(3)]
    edges = []
    for i in range(3):
        for j in range(i + 1, 3):
            edges.append(
                ResearchEdge(
                    source=f"Concept:a{i}",
                    target=f"Concept:a{j}",
                    type="shares_concept_with",
                )
            )
            edges.append(
                ResearchEdge(
                    source=f"Concept:b{i}",
                    target=f"Concept:b{j}",
                    type="shares_concept_with",
                )
            )
    edges.append(
        ResearchEdge(
            source="Concept:a0", target="Concept:b0", type="shares_concept_with"
        )
    )
    nodes.append(
        ResearchNode(id="Concept:lonely", name="Lonely", type=ResearchNodeType.CONCEPT)
    )
    return ResearchGraph(nodes=nodes, edges=edges)


def _root_slug(charter: Dict[str, Any]) -> str:
    """The one tier-1 router with children — the domain a brief matters for."""
    roots = [
        slug
        for slug, entry in charter["domains"].items()
        if entry["tier"] == 1 and entry["child_slugs"]
    ]
    assert len(roots) == 1, f"fixture must produce exactly one router, got {roots}"
    return roots[0]


class _ScriptedClient:
    """LLMJsonClient stub: counts calls, returns a scripted or generated payload."""

    def __init__(self, scripted: Optional[List[Optional[dict]]] = None) -> None:
        self.scripted = scripted
        self.calls: List[dict] = []

    def complete_json(self, **kwargs: Any) -> Optional[dict]:
        self.calls.append(kwargs)
        if self.scripted is not None:
            return self.scripted[min(len(self.calls) - 1, len(self.scripted) - 1)]
        return {
            "title": "Scripted domain",
            "description": "A description of the domain.",
            "tags": ["alpha", "beta", "gamma", "delta", "epsilon"],
        }


def _citing_client(charter: Dict[str, Any], slug: str) -> _ScriptedClient:
    """A client whose prose cites a child brief, so a ROUTER's summary passes
    ``_cites_child_communities``."""
    child = charter["domains"][slug]["child_slugs"][0]
    return _ScriptedClient(
        scripted=[
            {
                "title": "Concepts",
                "description": f"Routes to {brief_cid(child)} and its siblings.",
                "tags": ["alpha", "beta", "gamma", "delta", "epsilon"],
            }
        ]
    )


def _fixture() -> tuple[ResearchGraph, Dict[str, Any], Dict[str, ResearchNode], Dict[str, int]]:
    graph = _two_fat_triangles_plus_orphan()
    charter = build_charter(graph)
    return (
        graph,
        charter,
        {n.id: n for n in graph.nodes},
        undirected_degrees(graph),
    )


# ---------------------------------------------------------------------------
# the member set a brief summarizes
# ---------------------------------------------------------------------------


def test_domain_member_ids_covers_the_subtree_a_router_gave_away() -> None:
    """A router's direct block is routinely EMPTY — every member went to a
    child — so briefing from ``direct_member_ids`` alone would leave the top of
    the institution, the domains an agent routes from first, with nothing to
    summarize."""
    _graph, charter, _by_id, _degrees = _fixture()
    root = _root_slug(charter)
    assert charter["domains"][root]["direct_member_ids"] == []

    members = domain_member_ids(charter, root)
    assert len(members) == charter["domains"][root]["member_count"] == 6
    assert members == sorted(members)
    # Leaves and intake still answer for themselves.
    for slug, entry in charter["domains"].items():
        assert len(domain_member_ids(charter, slug)) == entry["member_count"]


def test_domain_member_ids_raises_on_a_charter_whose_children_cycle() -> None:
    """A cycle or a shared child voids the tree CH-01 rests on, and walking it
    would not terminate. Silence here would hang a compile."""
    charter = {
        "domains": {
            "a": {"tier": 1, "direct_member_ids": [], "child_slugs": ["b"]},
            "b": {"tier": 2, "direct_member_ids": ["Concept:x"], "child_slugs": ["a"]},
        }
    }
    with pytest.raises(ValueError, match="reachable twice"):
        domain_member_ids(charter, "a")


def test_domain_member_ids_skips_a_child_the_charter_does_not_define() -> None:
    """``succeed`` deliberately preserves an unmapped child verbatim so an
    operator can see the corruption; refusing to brief the institution over one
    dangling name would be the worse failure."""
    charter = {
        "domains": {
            "a": {
                "tier": 1,
                "direct_member_ids": ["Concept:x"],
                "child_slugs": ["gone", "b"],
            },
            "b": {"tier": 2, "direct_member_ids": ["Concept:y"], "child_slugs": []},
        }
    }
    assert domain_member_ids(charter, "a") == ["Concept:x", "Concept:y"]


def test_domain_member_ids_rejects_an_unknown_slug() -> None:
    _graph, charter, _by_id, _degrees = _fixture()
    with pytest.raises(KeyError):
        domain_member_ids(charter, "no-such-domain")


def test_brief_prompt_members_are_ranked_by_degree_not_by_id(tmp_path: Path) -> None:
    """Ids are ``<Type>:<...>``, so the first 25 by id are 25 nodes of whichever
    type sorts first — a sample of one node type, not of the domain. The prompt
    and the digest both read the FIRST members, so this ordering decides both
    what a brief describes and whether a warm read can ever hit."""
    graph = _two_fat_triangles_plus_orphan()
    # Give one member a decisively higher degree than its id-sorted neighbours.
    graph.nodes.append(
        ResearchNode(id="Zeta:hub", name="Hub", type=ResearchNodeType.CONCEPT)
    )
    for i in range(3):
        graph.edges.append(
            ResearchEdge(
                source="Zeta:hub", target=f"Concept:a{i}", type="shares_concept_with"
            )
        )
        graph.edges.append(
            ResearchEdge(
                source="Zeta:hub", target=f"Concept:b{i}", type="shares_concept_with"
            )
        )
    charter = build_charter(graph)
    by_id = {n.id: n for n in graph.nodes}
    degrees = undirected_degrees(graph)
    slug = charter["member_index"]["Zeta:hub"]

    client = _ScriptedClient()
    materialize_domain_brief(
        charter, slug, by_id, degrees, cache_dir=tmp_path, json_client=client
    )
    assert len(client.calls) == 1
    user = client.calls[0]["user"]
    hub_line = next(line for line in user.splitlines() if "Hub" in line)
    first_member_line = next(
        line for line in user.splitlines() if line.startswith("- ")
    )
    assert hub_line == first_member_line, (
        "the highest-degree member must lead the prompt; sorted by id 'Zeta:hub' "
        "would come last"
    )


# ---------------------------------------------------------------------------
# the key that does not move — the whole point of the step
# ---------------------------------------------------------------------------


def test_the_brief_cache_path_survives_a_membership_change_a_community_id_does_not() -> None:
    """The thesis, in one test. Add a node to the graph: every affected
    community's ``community_id`` moves, so its cache file is not stale but
    UNREACHABLE. The slug does not move, so the brief's path is the same file
    and a re-summarization overwrites it."""
    graph = _two_fat_triangles_plus_orphan()
    charter_before = build_charter(graph)
    root = _root_slug(charter_before)
    members_before = domain_member_ids(charter_before, root)

    graph.nodes.append(_fat_node("Concept:a3"))
    graph.edges.append(
        ResearchEdge(
            source="Concept:a3", target="Concept:a0", type="shares_concept_with"
        )
    )
    charter_after = build_charter(graph)
    members_after = domain_member_ids(charter_after, root)

    assert members_before != members_after, "fixture must actually move membership"
    assert community_id(members_before) != community_id(members_after)
    assert brief_cid(root) == brief_cid(root)
    assert level_cache_path(Path("c"), 1, brief_cid(root)) == level_cache_path(
        Path("c"), 1, brief_cid(root)
    )
    # And the two keys are in different namespaces, so they cannot collide.
    assert not brief_cid(root).startswith("CommunitySummary:")


def test_prune_stale_summary_caches_never_deletes_a_domain_brief(tmp_path: Path) -> None:
    """The regression this namespace exists to prevent. Pruning deletes every
    ``CommunitySummary_*.json`` whose cid is absent from the hierarchy's live
    manifest, and a charter slug is never in that manifest — so a brief written
    under that prefix would be deleted on the next compile, reinstating the
    per-ingest cache wipe the stable key removes."""
    _graph, charter, by_id, degrees = _fixture()
    root = _root_slug(charter)
    assert materialize_domain_brief(
        charter,
        root,
        by_id,
        degrees,
        cache_dir=tmp_path,
        json_client=_citing_client(charter, root),
    ) is not None
    brief_path = level_cache_path(tmp_path, 1, brief_cid(root))
    assert brief_path.is_file()

    # A community summary at the same level, with a cid no longer live.
    dead = community_id(["Concept:a0", "Concept:a1"])
    dead_path = level_cache_path(tmp_path, 1, dead)
    dead_path.parent.mkdir(parents=True, exist_ok=True)
    dead_path.write_text("{}", encoding="utf-8")

    deleted = prune_stale_summary_caches(tmp_path, live_cids=[])
    assert deleted == [str(dead_path.relative_to(tmp_path))]
    assert brief_path.is_file(), "a domain brief must survive community pruning"


def test_brief_and_community_summary_share_a_level_dir_without_colliding(
    tmp_path: Path,
) -> None:
    _graph, charter, by_id, degrees = _fixture()
    root = _root_slug(charter)
    materialize_domain_brief(
        charter,
        root,
        by_id,
        degrees,
        cache_dir=tmp_path,
        json_client=_citing_client(charter, root),
    )
    brief_path = level_cache_path(tmp_path, 1, brief_cid(root))
    community_path = level_cache_path(
        tmp_path, 1, community_id(domain_member_ids(charter, root))
    )
    assert brief_path.is_file()
    assert brief_path != community_path
    assert brief_path.parent == community_path.parent
    assert brief_path.name == f"CharterDomain_{root}.json"


# ---------------------------------------------------------------------------
# everything else is materialize_community_summary's contract, unchanged
# ---------------------------------------------------------------------------


def test_a_cold_brief_costs_exactly_one_call_and_a_warm_one_costs_none(
    tmp_path: Path,
) -> None:
    _graph, charter, by_id, degrees = _fixture()
    root = _root_slug(charter)
    client = _ScriptedClient(
        scripted=[
            {
                "title": "Alpha and beta",
                "description": f"Covers {brief_cid('concept-a1')} and its siblings.",
                "tags": ["a", "b", "c", "d", "e"],
            }
        ]
    )
    first = materialize_domain_brief(
        charter, root, by_id, degrees, cache_dir=tmp_path, json_client=client
    )
    assert first is not None
    assert len(client.calls) == 1

    second_client = _ScriptedClient()
    again = materialize_domain_brief(
        charter, root, by_id, degrees, cache_dir=tmp_path, json_client=second_client
    )
    assert again == first
    assert second_client.calls == [], "a warm cache must not pay an LLM call"

    # And the offline reader serves the same thing with no client at all.
    assert read_domain_brief(charter, root, by_id, degrees, cache_dir=tmp_path) == first

    payload = json.loads(
        level_cache_path(tmp_path, 1, brief_cid(root)).read_text(encoding="utf-8")
    )
    assert payload["community_id"] == brief_cid(root)
    assert payload["member_ids"] == domain_member_ids(charter, root)
    assert payload["members_digest"]


def test_a_router_brief_citing_no_child_is_rejected_and_not_cached(
    tmp_path: Path,
) -> None:
    """A router's brief is a summary of summaries, the exact shape
    ``_cites_child_communities`` exists for: prose naming no child leaves a
    reader with nothing to descend into."""
    _graph, charter, by_id, degrees = _fixture()
    root = _root_slug(charter)
    child = charter["domains"][root]["child_slugs"][0]

    uncited = _ScriptedClient(
        scripted=[
            {
                "title": "Vague",
                "description": "Some concepts about some things.",
                "tags": ["a", "b", "c", "d", "e"],
            }
        ]
    )
    assert (
        materialize_domain_brief(
            charter, root, by_id, degrees, cache_dir=tmp_path, json_client=uncited
        )
        is None
    )
    assert not level_cache_path(tmp_path, 1, brief_cid(root)).exists(), (
        "a citation-rejected brief must not be cached, so a later attempt can "
        "still produce one"
    )

    cited = _ScriptedClient(
        scripted=[
            {
                "title": "Concepts",
                "description": f"Routes to {brief_cid(child)} and its sibling.",
                "tags": ["a", "b", "c", "d", "e"],
            }
        ]
    )
    assert materialize_domain_brief(
        charter, root, by_id, degrees, cache_dir=tmp_path, json_client=cited
    ) is not None
    assert level_cache_path(tmp_path, 1, brief_cid(root)).is_file()


def test_a_leaf_brief_has_no_citation_requirement(tmp_path: Path) -> None:
    """Vacuously true at a leaf: its members are nodes, not summaries."""
    _graph, charter, by_id, degrees = _fixture()
    leaf = next(
        slug
        for slug, entry in charter["domains"].items()
        if not entry["child_slugs"] and entry["direct_member_ids"]
    )
    client = _ScriptedClient(
        scripted=[
            {
                "title": "Leaf",
                "description": "Cites nothing at all.",
                "tags": ["a", "b", "c", "d", "e"],
            }
        ]
    )
    assert materialize_domain_brief(
        charter, leaf, by_id, degrees, cache_dir=tmp_path, json_client=client
    ) is not None


def test_a_brief_without_a_client_is_none_and_writes_nothing(tmp_path: Path) -> None:
    _graph, charter, by_id, degrees = _fixture()
    root = _root_slug(charter)
    assert (
        materialize_domain_brief(
            charter, root, by_id, degrees, cache_dir=tmp_path, json_client=None
        )
        is None
    )
    assert not (tmp_path / "1").exists()
    assert read_domain_brief(charter, root, by_id, degrees, cache_dir=tmp_path) is None


def test_materialize_domain_brief_never_raises(tmp_path: Path) -> None:
    """The never-blocking posture is inherited, and it has to cover the charter
    walk this module adds above it as well as the LLM call below it."""

    class _Exploding:
        def complete_json(self, **kwargs: Any) -> None:
            raise RuntimeError("boom")

    _graph, charter, by_id, degrees = _fixture()
    root = _root_slug(charter)
    assert (
        materialize_domain_brief(
            charter, root, by_id, degrees, cache_dir=tmp_path, json_client=_Exploding()
        )
        is None
    )

    cyclic = {
        "domains": {
            "a": {"tier": 1, "direct_member_ids": [], "child_slugs": ["b"]},
            "b": {"tier": 2, "direct_member_ids": ["Concept:a0"], "child_slugs": ["a"]},
        }
    }
    assert (
        materialize_domain_brief(
            cyclic, "a", by_id, degrees, cache_dir=tmp_path, json_client=_ScriptedClient()
        )
        is None
    )
    assert read_domain_brief(cyclic, "a", by_id, degrees, cache_dir=tmp_path) is None


def test_a_slug_that_could_escape_the_cache_dir_is_refused(tmp_path: Path) -> None:
    """``slug_for`` cannot mint such a name, but ``charter.json`` is a file a
    bad hand-merge can mangle — and a slug is used here as a FILENAME."""
    _graph, charter, by_id, degrees = _fixture()
    root = _root_slug(charter)
    mangled = {
        "domains": {
            "../../escape": dict(charter["domains"][root]),
            **{s: e for s, e in charter["domains"].items() if s != root},
        }
    }
    client = _ScriptedClient()
    assert (
        materialize_domain_brief(
            mangled,
            "../../escape",
            by_id,
            degrees,
            cache_dir=tmp_path,
            json_client=client,
        )
        is None
    )
    assert client.calls == []
    assert list(tmp_path.rglob("*")) == []
    assert (
        read_domain_brief(mangled, "../../escape", by_id, degrees, cache_dir=tmp_path)
        is None
    )


def test_a_brief_over_members_absent_from_the_graph_degrades_to_none(
    tmp_path: Path,
) -> None:
    """The ordering window ``_write_charter_sidecar`` documents: a member named
    in the charter can be gone from ``graph.json``. Counting that is lint's job;
    here it must not fabricate a member or raise."""
    _graph, charter, _by_id, degrees = _fixture()
    root = _root_slug(charter)
    client = _ScriptedClient()
    assert (
        materialize_domain_brief(
            charter, root, {}, degrees, cache_dir=tmp_path, json_client=client
        )
        is None
    )
    assert client.calls == []
