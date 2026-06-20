"""OKF v0.1 import/export: lossless round-trip + tolerant foreign import."""

from __future__ import annotations

from pathlib import Path

from tesserae.okf import read_okf_bundle, write_okf_bundle
from tesserae.research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
)


def _graph() -> ResearchGraph:
    nodes = [
        ResearchNode(id="n1", name="Attention", type=ResearchNodeType.CONCEPT,
                     description="A mechanism.", aliases=["attn"],
                     metadata={"weight": 3, "tags": ["nlp"]}),
        ResearchNode(id="n2", name="Transformer", type=ResearchNodeType.MODEL,
                     description="Uses attention.", source_path="papers/x.md"),
        ResearchNode(id="n3", name="ghost", type=ResearchNodeType.STUB),  # excluded
    ]
    edges = [ResearchEdge(source="n2", target="n1", type="uses", evidence="sec 3")]
    return ResearchGraph(nodes=nodes, edges=edges)


def test_round_trip_is_lossless(tmp_path: Path):
    g = _graph()
    write_okf_bundle(g, tmp_path)
    back = read_okf_bundle(tmp_path)

    # Stub excluded; the two real nodes survive with identity intact.
    by_id = {n.id: n for n in back.nodes}
    assert set(by_id) == {"n1", "n2"}
    assert by_id["n1"].type == ResearchNodeType.CONCEPT
    assert by_id["n1"].aliases == ["attn"]
    assert by_id["n1"].metadata == {"weight": 3, "tags": ["nlp"]}
    assert by_id["n2"].description == "Uses attention."
    assert by_id["n2"].source_path == "papers/x.md"
    # Typed edge survives with evidence; targets the same node.
    assert [(e.source, e.type, e.target, e.evidence) for e in back.edges] == [
        ("n2", "uses", "n1", "sec 3")
    ]


def test_export_is_deterministic(tmp_path: Path):
    g = _graph()
    write_okf_bundle(g, tmp_path / "a")
    write_okf_bundle(g, tmp_path / "b")
    a = sorted(p.relative_to(tmp_path / "a").as_posix() for p in (tmp_path / "a").rglob("*.md"))
    b = sorted(p.relative_to(tmp_path / "b").as_posix() for p in (tmp_path / "b").rglob("*.md"))
    assert a == b
    for rel in a:
        assert (tmp_path / "a" / rel).read_text() == (tmp_path / "b" / rel).read_text()
    assert "index.md" in a and "log.md" in a  # reserved files emitted


def test_same_name_nodes_round_trip_without_collision(tmp_path: Path):
    # Two distinct nodes with the same name+type would collide on one concept
    # file; both must survive (codex BLOCKER).
    g = ResearchGraph(
        nodes=[
            ResearchNode(id="a1", name="Cache", type=ResearchNodeType.CONCEPT, description="one"),
            ResearchNode(id="a2", name="Cache", type=ResearchNodeType.CONCEPT, description="two"),
        ],
        edges=[],
    )
    write_okf_bundle(g, tmp_path)
    back = read_okf_bundle(tmp_path)
    assert {n.id for n in back.nodes} == {"a1", "a2"}
    assert {n.description for n in back.nodes} == {"one", "two"}


def test_edge_metadata_round_trips(tmp_path: Path):
    g = ResearchGraph(
        nodes=[
            ResearchNode(id="n1", name="A", type=ResearchNodeType.CONCEPT),
            ResearchNode(id="n2", name="B", type=ResearchNodeType.MODEL),
        ],
        edges=[ResearchEdge(source="n2", target="n1", type="uses",
                            metadata={"weight": 2, "note": "x"})],
    )
    write_okf_bundle(g, tmp_path)
    back = read_okf_bundle(tmp_path)
    assert back.edges[0].metadata == {"weight": 2, "note": "x"}


def test_reexport_drops_deleted_nodes(tmp_path: Path):
    write_okf_bundle(_graph(), tmp_path)
    smaller = ResearchGraph(
        nodes=[ResearchNode(id="n1", name="Attention", type=ResearchNodeType.CONCEPT)],
        edges=[],
    )
    write_okf_bundle(smaller, tmp_path)  # re-export into the SAME dir
    back = read_okf_bundle(tmp_path)
    assert {n.id for n in back.nodes} == {"n1"}  # n2 (deleted) must not linger


def test_symlink_outside_root_is_skipped(tmp_path: Path):
    import os as _os
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "real.md").write_text("---\ntype: Concept\nname: Real\n---\n\nhi\n", encoding="utf-8")
    secret = tmp_path / "secret.md"
    secret.write_text("---\ntype: Concept\nname: Secret\n---\n\nleak\n", encoding="utf-8")
    try:
        _os.symlink(secret, bundle / "link.md")
    except (OSError, NotImplementedError):
        import pytest
        pytest.skip("symlinks unavailable")
    g = read_okf_bundle(bundle)
    assert {n.name for n in g.nodes} == {"Real"}  # symlinked file outside root not read


def test_foreign_bundle_best_effort(tmp_path: Path):
    # A hand-authored OKF bundle: no x_tesserae, an unknown type, a body link,
    # a broken link, and a file with no type (must be skipped).
    (tmp_path / "topics").mkdir()
    (tmp_path / "topics" / "graphs.md").write_text(
        "---\ntype: WeirdCustomType\nname: Graphs\n---\n\n"
        "See [Search](../search.md) and [missing](./nope.md).\n",
        encoding="utf-8",
    )
    (tmp_path / "search.md").write_text(
        "---\ntype: Concept\nname: Search\n---\n\nLexical search.\n", encoding="utf-8"
    )
    (tmp_path / "junk.md").write_text("no frontmatter here\n", encoding="utf-8")

    g = read_okf_bundle(tmp_path)
    by_id = {n.id: n for n in g.nodes}
    # junk.md (no type) skipped; the other two imported.
    assert set(by_id) == {"topics/graphs", "search"}
    # Unknown type degrades to Concept, original preserved.
    assert by_id["topics/graphs"].type == ResearchNodeType.CONCEPT
    assert by_id["topics/graphs"].metadata["okf_type"] == "WeirdCustomType"
    # Valid body link -> references edge; broken link dropped.
    assert [(e.source, e.type, e.target) for e in g.edges] == [
        ("topics/graphs", "references", "search")
    ]
