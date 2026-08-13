import json
import sqlite3

from tesserae.persistence import SQLiteResearchGraphStore
from tesserae.research_graph import ResearchEdge, ResearchGraph, ResearchNode, ResearchNodeType


def sample_graph():
    paper = ResearchNode(id="Paper:p:test", name="Paper A", type=ResearchNodeType.PAPER, metadata={"arxiv_id": "2601.00001"})
    method = ResearchNode(id="MethodologicalConcept:gs:test", name="Gaussian Splatting", type=ResearchNodeType.METHODOLOGICAL_CONCEPT, aliases=["3DGS"])
    return ResearchGraph(
        nodes=[paper, method],
        edges=[ResearchEdge(source=paper.id, target=method.id, type="uses", evidence="uses Gaussian Splatting")],
    )


def test_sqlite_store_writes_nodes_edges_and_metadata(tmp_path):
    db_path = tmp_path / "research_graph.sqlite"
    store = SQLiteResearchGraphStore(db_path)
    store.write_graph(sample_graph(), replace=True)

    con = sqlite3.connect(db_path)
    node_count = con.execute("select count(*) from nodes").fetchone()[0]
    edge_count = con.execute("select count(*) from edges").fetchone()[0]
    gaussian = con.execute("select name, type, aliases_json from nodes where id=?", ("MethodologicalConcept:gs:test",)).fetchone()
    edge = con.execute("select source, target, type, evidence from edges").fetchone()

    assert node_count == 2
    assert edge_count == 1
    assert gaussian[0] == "Gaussian Splatting"
    assert gaussian[1] == "MethodologicalConcept"
    assert json.loads(gaussian[2]) == ["3DGS"]
    assert edge == ("Paper:p:test", "MethodologicalConcept:gs:test", "uses", "uses Gaussian Splatting")


def test_sqlite_store_can_roundtrip_graph(tmp_path):
    db_path = tmp_path / "research_graph.sqlite"
    store = SQLiteResearchGraphStore(db_path)
    store.write_graph(sample_graph(), replace=True)

    loaded = store.read_graph()

    assert [node.name for node in loaded.nodes] == ["Paper A", "Gaussian Splatting"]
    assert loaded.edges[0].type == "uses"
