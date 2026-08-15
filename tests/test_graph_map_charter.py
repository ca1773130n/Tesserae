"""graph_map's charter entry point — ``scope=None`` stops being a size rank.

Re-scope step 2. Measured on this project before the change: the root built
one card per key of ``hierarchy.coarsest`` sorted ``(-len(members), cid)`` —
1,852 cards, median 2 members, 1,124 at 2 or fewer, 83 at 100 or more. The
first discriminator was a member count and the second a sha256 digest.

The fixture is built so the two orderings DISAGREE: ``atlas`` (3 members)
sorts before ``zephyr`` (5) by name and after it by size, so a test that
asserts the card order cannot pass by accident under the old rule. Direct
members inside a domain are ordered the same way, by degree rather than by id,
and ``zephyr``'s edges are chosen so those two orderings disagree too.

Nothing is removed by the charter taking over the root: ``communities:root``
still serves the dendrogram, every community id still resolves, and a project
with an absent or unreadable charter gets the pre-charter root unchanged.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tesserae.charter import CharterUnreadable
from tesserae.community_summaries import community_id
from tesserae.mcp_server import LLMWikiMCPServer
from tesserae.research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
)

X_MEMBERS = ["Concept:x1", "Concept:x2", "Concept:x3"]
Y_MEMBERS = ["Concept:y1", "Concept:y2", "Concept:y3", "Concept:y4", "Concept:y5"]

CID_X = community_id(X_MEMBERS)
CID_Y = community_id(Y_MEMBERS)


def _fixture_graph() -> ResearchGraph:
    nodes = [
        ResearchNode(
            id=nid,
            name=f"Node {nid.split(':')[1].upper()}",
            type=ResearchNodeType.CONCEPT,
            description=f"description of {nid}",
        )
        for nid in X_MEMBERS + Y_MEMBERS
    ]
    edges = [
        ResearchEdge(source="Concept:x1", target="Concept:x2", type="shares_concept_with"),
        ResearchEdge(source="Concept:x1", target="Concept:x3", type="shares_concept_with"),
        # y1 (degree 4) and y5 (degree 3) outrank y2/y3 (2) and y4 (1), so
        # (-degree, id) order is y1, y5, y2, y3, y4 — NOT id order.
        ResearchEdge(source="Concept:y1", target="Concept:y2", type="shares_concept_with"),
        ResearchEdge(source="Concept:y1", target="Concept:y3", type="shares_concept_with"),
        ResearchEdge(source="Concept:y1", target="Concept:y4", type="shares_concept_with"),
        ResearchEdge(source="Concept:y1", target="Concept:y5", type="shares_concept_with"),
        ResearchEdge(source="Concept:y5", target="Concept:y2", type="shares_concept_with"),
        ResearchEdge(source="Concept:y5", target="Concept:y3", type="shares_concept_with"),
    ]
    return ResearchGraph(nodes=nodes, edges=edges)


def _hierarchy_payload() -> dict:
    return {
        "schema_version": 1,
        "levels": [{CID_X: X_MEMBERS, CID_Y: Y_MEMBERS}],
        "hubs": ["Concept:y1"],
    }


def _domain(**overrides) -> dict:
    record = {
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
    record.update(overrides)
    return record


def _charter_payload() -> dict:
    return {
        "version": 1,
        "reorg_seq": 0,
        "domains": {
            # atlas is SMALLER than zephyr and sorts before it by name — the
            # whole point of the fixture.
            "atlas": _domain(
                anchor_id="Concept:x1",
                child_slugs=["atlas-core"],
                direct_member_ids=["Concept:x1"],
                member_count=3,
            ),
            "atlas-core": _domain(
                tier=2,
                own_altitude="department",
                parent_slug="atlas",
                anchor_id="Concept:x2",
                direct_member_ids=["Concept:x2", "Concept:x3"],
                member_count=2,
            ),
            # The anchor is deliberately NOT the top-degree member (y1 is),
            # reproducing the live case: step 1's selector demotes producer
            # types, so the node a slug is minted from routinely loses the
            # degree race that titles a structural card.
            "zephyr": _domain(
                anchor_id="Concept:y4",
                direct_member_ids=list(Y_MEMBERS),
                member_count=5,
            ),
            # A tombstone: excluded from the root, and an explicit descent
            # into it must explain itself rather than 404.
            "ghost": _domain(
                status="retired",
                transition="retired",
                superseded_by=None,
                anchor_id="Concept:x3",
                direct_member_ids=["Concept:x3"],
                member_count=1,
            ),
        },
        "member_index": {
            "Concept:x1": "atlas",
            "Concept:x2": "atlas-core",
            "Concept:x3": "atlas-core",
            **{mid: "zephyr" for mid in Y_MEMBERS},
        },
    }


class _StubBriefClient:
    """A ``json_client`` returning one fixed valid envelope. No LLM, no network.

    Handed to the REAL ``materialize_domain_brief`` rather than writing a cache
    file by hand: a fixture that spells the cache convention out for itself is
    exactly what let the writer's key (``CharterDomain:<slug>`` at the domain's
    tier) and the card's reader drift apart while both stayed green.
    """

    TITLE = "The Zephyr Programme"
    DESCRIPTION = "Everything the zephyr division holds, described rather than ranked."
    TAGS = ["zephyr", "fixture"]

    def complete_json(self, **_kwargs: object) -> dict:
        return {
            "title": self.TITLE,
            "description": self.DESCRIPTION,
            "tags": list(self.TAGS),
        }


def _make_project(tmp_path: Path, charter: object) -> dict:
    root = tmp_path / "proj"
    tess = root / ".tesserae"
    tess.mkdir(parents=True)
    # ProjectWiki.load requires it, and the CLI verb goes through ProjectWiki.
    (tess / "config.json").write_text("{}\n", encoding="utf-8")
    graph_path = tess / "graph.json"
    graph_path.write_text(_fixture_graph().to_json(indent=2), encoding="utf-8")
    (tess / "hierarchy.json").write_text(
        json.dumps(_hierarchy_payload(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if charter is not None:
        charter_dir = tess / "charter"
        charter_dir.mkdir(parents=True)
        text = (
            charter
            if isinstance(charter, str)
            else json.dumps(charter, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        )
        (charter_dir / "charter.json").write_text(text, encoding="utf-8")
    return {"root": root, "graph_path": graph_path, "server": LLMWikiMCPServer()}


@pytest.fixture()
def project(tmp_path: Path) -> dict:
    return _make_project(tmp_path, _charter_payload())


@pytest.fixture()
def uncharted(tmp_path: Path) -> dict:
    return _make_project(tmp_path, None)


def _call(project: dict, **kwargs) -> dict:
    return project["server"].call_tool(
        "graph_map", {"graph_path": str(project["graph_path"]), **kwargs}
    )


# ---------------------------------------------------------------------------
# The root: named divisions, not a size rank
# ---------------------------------------------------------------------------


def test_root_serves_divisions_in_name_order_not_size_order(project) -> None:
    """The regression this whole step exists to prevent."""
    cards = _call(project)["cards"]
    assert [c["scope_id"] for c in cards] == ["domain:atlas", "domain:zephyr"]
    # Size order would have inverted them, so this is not a coincidence.
    assert [c["size"] for c in cards] == [3, 5]
    for card in cards:
        assert card["kind"] == "domain"
        assert card["parent_scope"] is None


def test_root_header_reports_that_it_served_the_charter(project) -> None:
    header = _call(project)["header"]
    assert header["scope"] is None
    assert header["kind"] == "root"
    assert header["entry"] == "charter"
    assert header["charter"] == "present"
    assert header["division_count"] == 2  # the tombstone is not a division
    assert header["reorg_seq"] == 0
    assert header["dendrogram_scope"] == "communities:root"
    # Still true, and still reported: the dendrogram did not go away.
    assert header["community_count"] == 2
    assert header["levels"] == 1
    assert header["total_cards"] == 2


def test_division_card_keeps_the_community_card_shape(project) -> None:
    card = next(c for c in _call(project)["cards"] if c["scope_id"] == "domain:zephyr")
    assert set(card) == {
        "scope_id", "kind", "title", "summary", "size", "children_count",
        "leaf_member_count", "live_member_count", "parent_scope", "tags",
        "quality", "stale",
    }
    assert card["quality"] == "structural"  # nothing has briefed this domain
    assert card["leaf_member_count"] == 5
    assert card["live_member_count"] == 5
    assert card["children_count"] == 5  # 0 live child domains + 5 direct


def test_division_card_counts_subtree_members_not_direct_ones(project) -> None:
    card = next(c for c in _call(project)["cards"] if c["scope_id"] == "domain:atlas")
    assert card["size"] == 3  # 1 direct + 2 held by atlas-core
    assert card["children_count"] == 2  # 1 live child domain + 1 direct member
    assert card["title"] == "Node X1"


def test_a_structural_division_card_is_titled_by_its_anchor(project) -> None:
    """A card whose name disagrees with the scope that reaches it is not a name.

    On the live graph ``domain:psnr`` came back titled ``한 줄 요약`` — the
    top-degree member, and precisely the SourceDocument anchor step 1's
    selector demoted so the slug would be readable.
    """
    cards = {c["scope_id"]: c for c in _call(project)["cards"]}
    assert cards["domain:zephyr"]["title"] == "Node Y4"  # the anchor
    assert "Node Y1" in cards["domain:zephyr"]["summary"]  # top-degree, in prose
    assert cards["domain:atlas"]["title"] == "Node X1"


def test_an_anchorless_domain_is_titled_by_its_slug(tmp_path) -> None:
    """``intake`` is a census of what structure could not route, and on the
    live graph the structural floor titled it ``PEP 8 Style Guide:
    Essentials``. A domain with no anchor has no name but its slug."""
    payload = _charter_payload()
    payload["domains"]["zephyr"]["anchor_id"] = ""
    anchorless = _make_project(tmp_path, payload)
    card = next(
        c for c in _call(anchorless)["cards"] if c["scope_id"] == "domain:zephyr"
    )
    assert card["title"] == "zephyr"
    assert "5 members" in card["summary"]  # the count is still visible


def test_retired_domain_is_not_offered_at_the_root(project) -> None:
    assert "domain:ghost" not in {c["scope_id"] for c in _call(project)["cards"]}


# ---------------------------------------------------------------------------
# Nothing is removed: the dendrogram is still one call away
# ---------------------------------------------------------------------------


def test_communities_root_scope_serves_the_pre_charter_root(project) -> None:
    result = _call(project, scope="communities:root")
    header = result["header"]
    assert header["scope"] == "communities:root"
    assert header["kind"] == "root"
    assert header["entry"] == "communities"
    # A bypassed charter is reported as present with its divisions counted, so
    # "you opted out of 2 divisions" and "the charter is empty" stay apart.
    assert header["charter"] == "present"
    assert header["division_count"] == 2
    assert "dendrogram_scope" not in header
    # Largest community first, exactly as before the charter existed.
    assert [c["scope_id"] for c in result["cards"]] == [CID_Y, CID_X]
    assert all(c["kind"] == "community" for c in result["cards"])


def test_community_ids_still_resolve_under_a_charter(project) -> None:
    header = _call(project, scope=CID_X)["header"]
    assert header["kind"] == "community"
    assert header["leaf_member_count"] == 3


# ---------------------------------------------------------------------------
# Degrading: no charter, and a broken one
# ---------------------------------------------------------------------------


def test_no_charter_gets_the_community_root_and_says_so(uncharted) -> None:
    result = _call(uncharted)
    header = result["header"]
    assert header["entry"] == "communities"
    assert header["charter"] == "absent"
    assert "division_count" not in header
    assert "reorg_seq" not in header
    assert [c["scope_id"] for c in result["cards"]] == [CID_Y, CID_X]


def test_unreadable_charter_degrades_the_root_rather_than_failing_it(tmp_path) -> None:
    """The entry point must survive a truncated optional sidecar."""
    broken = _make_project(tmp_path, '{"version": 1, "domains": {')
    result = _call(broken)
    assert result["header"]["entry"] == "communities"
    assert result["header"]["charter"] == "unreadable"
    assert [c["scope_id"] for c in result["cards"]] == [CID_Y, CID_X]


def test_unreadable_charter_still_fails_loud_on_an_explicit_domain_scope(tmp_path) -> None:
    """ABSENT and UNREADABLE must not collapse for a caller who asked for the charter."""
    broken = _make_project(tmp_path, '{"version": 1, "domains": {')
    with pytest.raises(CharterUnreadable, match="not valid JSON"):
        _call(broken, scope="domain:atlas")


def test_domain_scope_without_a_charter_says_so(uncharted) -> None:
    """Not "unknown domain": the repair is a compile, not a corrected spelling."""
    with pytest.raises(ValueError, match="has no charter"):
        _call(uncharted, scope="domain:atlas")


def test_unknown_scope_error_names_both_new_scopes(project) -> None:
    with pytest.raises(ValueError) as excinfo:
        _call(project, scope="CommunitySummary:deadbeefdeadbeef")
    message = str(excinfo.value)
    assert "domain:<slug>" in message
    assert "communities:root" in message


# ---------------------------------------------------------------------------
# Descending a domain
# ---------------------------------------------------------------------------


def test_domain_scope_lists_child_domains_then_its_own_members(project) -> None:
    result = _call(project, scope="domain:atlas")
    header = result["header"]
    assert header["scope"] == "domain:atlas"
    assert header["kind"] == "domain"
    assert header["tier"] == 1
    assert header["altitude"] == "division"
    assert header["leaf_member_count"] == 3
    assert header["charter_member_count"] == 3
    assert header["parent_scope"] is None
    assert [c["scope_id"] for c in result["cards"]] == [
        "domain:atlas-core",  # child domains first
        "Concept:x1",         # then the members atlas holds itself
    ]
    assert result["cards"][1]["parent_scope"] == "domain:atlas"


def test_domain_direct_members_are_ranked_by_degree_not_by_id(project) -> None:
    """Alphabetical ids over a 7,581-member intake put an arbitrary page first."""
    cards = _call(project, scope="domain:zephyr")["cards"]
    assert [c["scope_id"] for c in cards] == [
        "Concept:y1",  # degree 4
        "Concept:y5",  # degree 3
        "Concept:y2",  # degree 2, id tiebreak
        "Concept:y3",  # degree 2
        "Concept:y4",  # degree 1
    ]


def test_child_domain_ascends_to_its_parent(project) -> None:
    result = _call(project, scope="domain:atlas-core")
    assert result["header"]["parent_scope"] == "domain:atlas"
    assert result["header"]["tier"] == 2
    assert [c["scope_id"] for c in result["cards"]] == ["Concept:x2", "Concept:x3"]


def test_retired_domain_scope_explains_itself(project) -> None:
    with pytest.raises(ValueError, match="is retired"):
        _call(project, scope="domain:ghost")


def test_unknown_domain_offers_the_live_divisions(project) -> None:
    with pytest.raises(ValueError) as excinfo:
        _call(project, scope="domain:nope")
    message = str(excinfo.value)
    assert "atlas" in message and "zephyr" in message


def test_domain_scope_paginates_like_every_other_scope(project) -> None:
    result = _call(project, scope="domain:zephyr", budget_chars=900)
    assert result["continuation"].startswith("+")
    assert result["header"]["total_cards"] == 5
    assert 0 < len(result["cards"]) < 5
    resumed = _call(
        project,
        scope="domain:zephyr",
        budget_chars=900,
        cursor=int(result["continuation"].split("cursor=")[1]),
    )
    assert resumed["cards"][0]["scope_id"] not in {
        c["scope_id"] for c in result["cards"]
    }


# ---------------------------------------------------------------------------
# The demand signal
# ---------------------------------------------------------------------------


def test_domain_scope_ids_are_never_written_to_node_memory(project, monkeypatch) -> None:
    """daemon._summarize_once ranks sidecar cids only — a domain: row is unreadable."""
    bumped: list[str] = []
    server = project["server"]
    original = server._bump_nodes_access

    def _record(root, node_ids):
        ids = list(node_ids)
        bumped.extend(ids)
        return original(root, iter(ids))

    monkeypatch.setattr(server, "_bump_nodes_access", _record)
    _call(project)
    assert bumped == []  # the root served nothing but domain cards
    bumped.clear()
    _call(project, scope="domain:zephyr")
    assert bumped == [  # real graph ids only, in the card order
        "Concept:y1", "Concept:y5", "Concept:y2", "Concept:y3", "Concept:y4",
    ]


# ---------------------------------------------------------------------------
# A damaged charter is a state case: a message, never a traceback
# ---------------------------------------------------------------------------


def test_charter_unreadable_is_the_exception_class_every_cli_verb_catches() -> None:
    """Every verb in this tree sorts exceptions the same way: ValueError and
    OSError are the actionable USER/STATE cases and get one clean ``error:``
    line, everything else is a programming error and is allowed to traceback.
    A truncated charter.json is a state case whose message is already a repair
    instruction, so its class has to be on the ValueError side of that split —
    otherwise every consumer has to remember to widen its own catch tuple, and
    the one that forgets prints a traceback at an operator."""
    assert issubclass(CharterUnreadable, ValueError)


def test_cli_domain_scope_on_a_truncated_charter_prints_one_line(tmp_path, capsys) -> None:
    """The reproduction: ``tesserae graph-map --scope domain:<slug>`` used to
    traceback here. Absent this test the catch tuple was never widened."""
    from tesserae.cli import main

    broken = _make_project(tmp_path, '{"version": 1, "domains": {')
    rc = main([
        "graph-map", "--project", str(broken["root"]), "--scope", "domain:atlas",
    ])
    captured = capsys.readouterr()
    assert rc == 1
    assert captured.err.startswith("error: graph_map failed: ")
    assert "is not valid JSON" in captured.err
    assert "Traceback" not in captured.err
    assert captured.out == ""  # no half-written JSON result on stdout


@pytest.mark.parametrize(
    "body,shape",
    [("[]", "list"), ('"hello"', "str"), ("42", "int"), ("null", "NoneType")],
)
def test_valid_json_that_is_not_an_object_is_unreadable_not_a_charter(
    tmp_path, body: str, shape: str
) -> None:
    """``[]``, ``"hello"``, ``42`` and ``null`` all parse. Returning any of them
    hands every reader a value with no ``.get``, which crashed the DEFAULT
    entry point with an AttributeError instead of degrading.

    ``null`` is included deliberately even though it did not crash: it returned
    None, which every caller reads as "this project has no charter yet". On the
    compile path that means RE-FOUNDING the institution over a damaged file —
    new slugs, no tombstones, every pinned attach path broken — which is the
    precise disaster ``read_charter`` refuses to allow for a truncated file.
    "Unreadable" is the same answer for the same reason.
    """
    broken = _make_project(tmp_path, body)

    result = _call(broken)
    assert result["header"]["entry"] == "communities"
    assert result["header"]["charter"] == "unreadable"
    assert "division_count" not in result["header"]
    assert [c["scope_id"] for c in result["cards"]] == [CID_Y, CID_X]

    # And a caller who explicitly asked for the charter still gets told.
    with pytest.raises(CharterUnreadable, match=f"parsed as {shape}"):
        _call(broken, scope="domain:atlas")


def test_a_non_numeric_reorg_seq_does_not_fail_the_root(tmp_path) -> None:
    """The header coercion reads a hand-editable sidecar, so it has to be
    total: ``int("abc")`` raising here failed the surface every agent starts
    from over one mangled scalar in an OPTIONAL file."""
    payload = _charter_payload()
    payload["reorg_seq"] = "abc"
    mangled = _make_project(tmp_path, payload)

    header = _call(mangled)["header"]
    assert header["charter"] == "present"  # the domains parsed fine
    assert header["entry"] == "charter"
    assert header["reorg_seq"] == 0  # the same answer a missing key gets
    assert header["division_count"] == 2


# ---------------------------------------------------------------------------
# The warm brief: written by #166's writer, read by the card
# ---------------------------------------------------------------------------


def _write_brief(project: dict, slug: str, client: object = None) -> object:
    """Write a warm brief for ``slug`` through the REAL writer."""
    from tesserae.charter import materialize_domain_brief, read_charter
    from tesserae.hierarchy import undirected_degrees

    graph = _fixture_graph()
    return materialize_domain_brief(
        read_charter(project["root"]),
        slug,
        {n.id: n for n in graph.nodes},
        undirected_degrees(graph),
        cache_dir=project["root"] / ".tesserae" / "community_summaries",
        json_client=client or _StubBriefClient(),
    )


def test_a_brief_written_by_the_real_writer_is_read_back_by_the_card(project) -> None:
    """The end-to-end pairing, through ``materialize_domain_brief`` rather than
    a hand-built cache file, so the writer's key and the card's reader cannot
    drift apart again.

    Before this, a domain card could NEVER be ``quality: "llm"``: it routed its
    warm lookup through ``community_card``, whose read is gated on
    ``hierarchy.find_scope(cid)`` — always None for a slug, which is not in the
    dendrogram manifest and never will be. A valid brief sat on disk and the
    card served the top-degree member's name over it.
    """
    assert _write_brief(project, "zephyr") is not None, "the writer must have written"

    cards = {c["scope_id"]: c for c in _call(project)["cards"]}
    card = cards["domain:zephyr"]
    assert card["quality"] == "llm"
    assert card["title"] == _StubBriefClient.TITLE
    assert card["tags"] == _StubBriefClient.TAGS
    assert card["summary"].startswith("Everything the zephyr division holds")
    # The overridden card keeps every other key it had, from the same builder.
    assert card["size"] == 5
    assert card["children_count"] == 5
    assert card["live_member_count"] == 5
    assert card["scope_id"] == "domain:zephyr"
    assert card["kind"] == "domain"

    # An unbriefed domain is untouched: still structural, still anchor-titled.
    assert cards["domain:atlas"]["quality"] == "structural"
    assert cards["domain:atlas"]["title"] == "Node X1"


def test_a_briefed_domain_scope_header_carries_the_brief(project) -> None:
    """The header's title/summary/quality come from the same card, so descending
    into a briefed domain must show the brief rather than the structural floor."""
    _write_brief(project, "zephyr")
    header = _call(project, scope="domain:zephyr")["header"]
    assert header["quality"] == "llm"
    assert header["title"] == _StubBriefClient.TITLE


def test_reading_a_brief_costs_the_map_no_llm_call(project, monkeypatch) -> None:
    """``graph_map`` is a read surface for domains: the brief is materialized by
    its writer, never by a visit. A client handed out here would make the entry
    point pay a ``complete_json`` per division."""
    _write_brief(project, "zephyr")

    def _forbidden(*_args, **_kwargs):
        raise AssertionError("graph_map asked for an LLM client on a domain path")

    monkeypatch.setattr(
        project["server"], "_community_summary_json_client", _forbidden
    )
    assert _call(project)["cards"][1]["quality"] == "llm"
    assert _call(project, scope="domain:zephyr")["header"]["quality"] == "llm"


def test_a_brief_whose_members_drifted_is_a_miss_not_a_stale_serve(project) -> None:
    """``read_warm_summary`` is strict about the member digest, and the card
    must inherit that: prose describing a membership the domain no longer has
    is not ``quality: "llm"``, it is wrong."""
    _write_brief(project, "zephyr")
    charter_file = project["root"] / ".tesserae" / "charter" / "charter.json"
    payload = json.loads(charter_file.read_text(encoding="utf-8"))
    payload["domains"]["zephyr"]["direct_member_ids"] = ["Concept:y1", "Concept:y2"]
    charter_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    card = next(c for c in _call(project)["cards"] if c["scope_id"] == "domain:zephyr")
    assert card["quality"] == "structural"
    assert card["title"] == "Node Y4"  # back to the anchor
