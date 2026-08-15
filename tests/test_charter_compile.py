# tests/test_charter_compile.py
"""The charter is DERIVED by a compile.

Before this, ``build_charter`` / ``write_charter`` / ``succeed`` had zero
production callers and ``.tesserae/charter/`` existed on no project — 886 lines
and 52 tests describing an institution nothing derived. These tests cover the
derivation itself: when a compile founds one, when it declines to, when it
succeeds a prior one, and when it must refuse to touch the file at all.

No knowledge graph is compiled here and no LLM is required: the pass under test
is a pure function of a ``ResearchGraph`` handed to it, so the graphs are built
in memory.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tesserae.charter import charter_path, read_charter
from tesserae.project import ProjectWiki
from tesserae.research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
)


def _fat(nid: str, filler: int = 5_000) -> ResearchNode:
    return ResearchNode(
        id=nid, name=nid, type=ResearchNodeType.CONCEPT, description="x" * filler
    )


def _clique(prefix: str, size: int = 4) -> tuple[list[ResearchNode], list[ResearchEdge]]:
    nodes = [_fat(f"Concept:{prefix}{i}") for i in range(size)]
    edges = [
        ResearchEdge(
            source=f"Concept:{prefix}{i}",
            target=f"Concept:{prefix}{j}",
            type="shares_concept_with",
        )
        for i in range(size)
        for j in range(i + 1, size)
    ]
    return nodes, edges


def _chartered_graph(cliques: int = 3) -> ResearchGraph:
    """Several heavy cliques, bridged in a chain — comfortably over the
    one-read bound and with real structure for detection to divide."""
    nodes: list[ResearchNode] = []
    edges: list[ResearchEdge] = []
    names = "abcdefgh"[:cliques]
    for name in names:
        clique_nodes, clique_edges = _clique(name)
        nodes += clique_nodes
        edges += clique_edges
    for left, right in zip(names, names[1:]):
        edges.append(
            ResearchEdge(
                source=f"Concept:{left}0", target=f"Concept:{right}0",
                type="shares_concept_with",
            )
        )
    return ResearchGraph(nodes=nodes, edges=edges)


def _wiki(tmp_path: Path) -> ProjectWiki:
    wiki = ProjectWiki(tmp_path / "project")
    wiki.root.mkdir(parents=True, exist_ok=True)
    return wiki


# ---------------------------------------------------------------------------
# founding
# ---------------------------------------------------------------------------


def test_a_compile_derives_the_charter(tmp_path: Path) -> None:
    wiki = _wiki(tmp_path)
    written = wiki._write_charter_sidecar(_chartered_graph())

    assert written is not None, "an above-the-bound graph must found a charter"
    assert written == charter_path(wiki.project_root), (
        "the charter must land at <project>/.tesserae/charter/charter.json — "
        "paths.root is ALREADY .tesserae, so passing it to charter_path nests "
        "a second .tesserae inside the first"
    )
    charter = read_charter(wiki.project_root)
    assert charter["reorg_seq"] == 0
    assert charter["domains"], "a founded charter with no domains is not a charter"


def test_the_charter_holds_every_node_exactly_once_on_disk(tmp_path: Path) -> None:
    """CH-01, read back off the file a compile wrote rather than off the value
    ``build_charter`` returned."""
    graph = _chartered_graph()
    wiki = _wiki(tmp_path)
    wiki._write_charter_sidecar(graph)

    charter = read_charter(wiki.project_root)
    held = [m for e in charter["domains"].values() for m in e["direct_member_ids"]]
    assert sorted(held) == sorted(n.id for n in graph.nodes)
    assert len(held) == len(set(held)), "a node held twice voids CH-01"
    assert charter["member_index"] == {
        m: slug
        for slug, e in charter["domains"].items()
        for m in e["direct_member_ids"]
    }


def test_a_project_below_the_one_read_bound_gets_no_charter_directory(tmp_path: Path) -> None:
    """Below the bound, ``.tesserae/charter/`` is never created — the directory
    itself is the signal every downstream reader branches on, so creating an
    empty one would flip them all."""
    wiki = _wiki(tmp_path)
    small = ResearchGraph(
        nodes=[ResearchNode(id="Concept:a", name="A", type=ResearchNodeType.CONCEPT)],
        edges=[],
    )
    assert wiki._write_charter_sidecar(small) is None
    assert not charter_path(wiki.project_root).parent.exists()
    assert read_charter(wiki.project_root) is None


def test_a_graph_with_nothing_to_divide_gets_no_charter(tmp_path: Path) -> None:
    """One division plus intake is a rename of the graph, not a structure an
    agent can route through."""
    wiki = _wiki(tmp_path)
    nodes, edges = _clique("solo", size=12)
    assert wiki._write_charter_sidecar(ResearchGraph(nodes=nodes, edges=edges)) is None
    assert not charter_path(wiki.project_root).exists()


# ---------------------------------------------------------------------------
# succession across compiles
# ---------------------------------------------------------------------------


def test_recompiling_an_unchanged_graph_leaves_the_charter_byte_identical(
    tmp_path: Path,
) -> None:
    """The house invariant applied to the one file everything else is keyed on.

    ``succeed`` bumps ``reorg_seq`` and re-stamps every domain ``stable`` on
    every call, so writing its result unconditionally would churn charter.json
    on every compile of an unchanged corpus and redefine reorg_seq as a compile
    counter.
    """
    graph = _chartered_graph()
    wiki = _wiki(tmp_path)
    wiki._write_charter_sidecar(graph)
    first = charter_path(wiki.project_root).read_bytes()

    for _ in range(3):
        assert wiki._write_charter_sidecar(graph) is None, "a no-op reorg must not write"
        assert charter_path(wiki.project_root).read_bytes() == first


def test_a_changed_graph_reorganises_and_keeps_the_surviving_slugs(
    tmp_path: Path,
) -> None:
    graph = _chartered_graph(cliques=3)
    wiki = _wiki(tmp_path)
    wiki._write_charter_sidecar(graph)
    before = read_charter(wiki.project_root)
    live_before = {s for s, e in before["domains"].items() if e["status"] == "live"}

    grown = _chartered_graph(cliques=5)
    assert wiki._write_charter_sidecar(grown) is not None, "new structure is a reorg"

    after = read_charter(wiki.project_root)
    assert after["reorg_seq"] == before["reorg_seq"] + 1
    assert live_before <= set(after["domains"]), (
        "a reorg must never make a prior slug unresolvable — it inherits or it "
        "tombstones, and either way the slug still resolves"
    )


def test_the_bound_decides_whether_to_found_not_whether_to_maintain(
    tmp_path: Path,
) -> None:
    """Once slugs are pinned attach paths, a corpus oscillating around the
    budget must not abandon them. A shrunk graph reorganises the existing
    institution instead of being ignored."""
    wiki = _wiki(tmp_path)
    wiki._write_charter_sidecar(_chartered_graph(cliques=4))
    before = read_charter(wiki.project_root)

    tiny = ResearchGraph(
        nodes=[ResearchNode(id="Concept:z", name="Z", type=ResearchNodeType.CONCEPT)],
        edges=[],
    )
    wiki._write_charter_sidecar(tiny)
    after = read_charter(wiki.project_root)

    assert after is not None, "an existing charter must not be abandoned"
    assert set(before["domains"]) <= set(after["domains"]), (
        "every prior slug must still resolve, as a tombstone if nothing else"
    )
    assert any(e["status"] == "retired" for e in after["domains"].values())


# ---------------------------------------------------------------------------
# a charter that cannot be read
# ---------------------------------------------------------------------------


def test_an_unreadable_charter_is_left_alone_and_the_compile_survives(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``read_charter`` distinguishes ABSENT from UNREADABLE precisely so this
    path cannot re-found. Founding over a mangled file would mint new slugs for
    every domain, break every pinned attach path and leave no tombstone saying
    where the old ones went — with the operator's only clue being a charter
    that silently changed."""
    wiki = _wiki(tmp_path)
    path = charter_path(wiki.project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"version": 1, "domains": {trunc', encoding="utf-8")
    corrupt = path.read_bytes()

    assert wiki._write_charter_sidecar(_chartered_graph()) is None
    assert path.read_bytes() == corrupt, "the damaged file must survive for restoring"
    assert "charter" in capsys.readouterr().out, "the operator must be told"


# ---------------------------------------------------------------------------
# wiring
# ---------------------------------------------------------------------------


def test_compile_calls_the_charter_pass_on_the_canonical_graph(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The defect this whole step exists to close was that nothing called it.
    A unit test of the pass cannot catch its call site going away, so assert
    the call site itself."""
    seen: list[ResearchGraph] = []

    def _spy(self: ProjectWiki, graph: ResearchGraph):
        seen.append(graph)
        return None

    monkeypatch.setattr(ProjectWiki, "_write_charter_sidecar", _spy)

    project_root = tmp_path / "project"
    (project_root / "docs").mkdir(parents=True)
    (project_root / "docs" / "note.md").write_text(
        "# Alpha\n\nAlpha relates to Beta.\n", encoding="utf-8"
    )
    wiki = ProjectWiki.init(project_root, name="charter_wiring")
    wiki.compile()

    assert len(seen) == 1, "compile must derive the charter exactly once"
    assert isinstance(seen[0], ResearchGraph)


def test_the_charter_directory_is_a_registered_sidecar() -> None:
    """A compile now writes it, so an unregistered entry would report as a
    stranger in ``.tesserae/`` — and ``safe_to_delete`` must stay False,
    because a rebuild does not reproduce the slugs it holds."""
    from tesserae.sidecars import KIND_DERIVED, classify, is_tesserae_sidecar

    assert is_tesserae_sidecar("charter")
    entry = classify("charter")
    assert entry.kind == KIND_DERIVED, "a compile republishes it"
    assert entry.safe_to_delete is False, (
        "deleting it re-founds every slug and breaks every pinned attach path"
    )


def test_charter_json_carries_no_timestamp(tmp_path: Path) -> None:
    """A wall clock in a declared determinism input is the byte-idempotence
    leak class this repo has taken four times."""
    wiki = _wiki(tmp_path)
    wiki._write_charter_sidecar(_chartered_graph())
    raw = charter_path(wiki.project_root).read_text(encoding="utf-8")

    payload = json.loads(raw)
    assert "generated_at" not in payload and "timestamp" not in payload
    for entry in payload["domains"].values():
        assert not [k for k in entry if k.endswith("_at") or k.endswith("_time")]
