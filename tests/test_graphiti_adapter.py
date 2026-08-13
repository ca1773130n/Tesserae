import json

import pytest

from tesserae.graphiti_adapter import GraphitiResearchGraphAdapter, GraphitiSyncUnavailableError
from tesserae.research_graph import ResearchEdge, ResearchGraph, ResearchNode, ResearchNodeType


def graphiti_sample_graph():
    paper = ResearchNode(
        id="Paper:demo",
        name="Demo Paper",
        type=ResearchNodeType.PAPER,
        source_path="papers/demo.md",
        metadata={"analysis_date": "2026-04-27"},
    )
    method = ResearchNode(
        id="Method:gs",
        name="Gaussian Splatting",
        type=ResearchNodeType.METHODOLOGICAL_CONCEPT,
        description="A point-based rendering method.",
    )
    return ResearchGraph(
        nodes=[paper, method],
        edges=[ResearchEdge(source=paper.id, target=method.id, type="uses", evidence="Demo Paper uses Gaussian Splatting.")],
    )


def test_graphiti_adapter_exports_temporal_facts_as_episode_jsonl(tmp_path):
    output = tmp_path / "graphiti_episodes.jsonl"

    episodes = GraphitiResearchGraphAdapter(group_id="demo_project").write_episodes(graphiti_sample_graph(), output)

    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == len(episodes) == 1
    assert rows[0]["group_id"] == "demo_project"
    assert rows[0]["source"] == "tesserae"
    assert rows[0]["source_description"] == "Tesserae controlled research graph temporal fact"
    assert rows[0]["reference_time"] == "2026-04-27"
    assert "Demo Paper --uses--> Gaussian Splatting" in rows[0]["content"]
    assert "Demo Paper uses Gaussian Splatting." in rows[0]["content"]
    assert rows[0]["metadata"]["subject_type"] == "Paper"
    assert rows[0]["metadata"]["object_type"] == "MethodologicalConcept"


def test_graphiti_episodes_carry_the_reinforced_confidence_not_the_heuristic(tmp_path):
    """The exported number must be the one Tesserae itself uses.

    ``episodes()`` used to call the projector with no ``memory_by_id``, so
    every episode carried ``infer_confidence``'s heuristic label while
    ``temporal_facts.jsonl`` — same projector, same compile — carried the
    reinforced value from the ``node_memory`` sidecar. An external consumer
    was reading a number no Tesserae surface agrees with.
    """
    from tesserae.memory.store import NodeMemoryRow

    graph = graphiti_sample_graph()
    adapter = GraphitiResearchGraphAdapter(group_id="demo_project")

    heuristic = adapter.episodes(graph)[0]
    reinforced = adapter.episodes(
        graph, memory_by_id={"Paper:demo": NodeMemoryRow(node_id="Paper:demo", confidence="0.91")}
    )[0]

    assert reinforced.metadata["confidence"] == "0.91"
    assert heuristic.metadata["confidence"] != "0.91"
    assert "Confidence: 0.91" in reinforced.content

    # The JSONL export and the live sync path both have to carry it too — the
    # defect was a missing argument, and it was missing on every entry point.
    output = tmp_path / "episodes.jsonl"
    adapter.write_episodes(
        graph, output, memory_by_id={"Paper:demo": NodeMemoryRow(node_id="Paper:demo", confidence="0.91")}
    )
    assert json.loads(output.read_text(encoding="utf-8"))["metadata"]["confidence"] == "0.91"


def test_project_export_graphiti_reads_the_memory_sidecar(tmp_path):
    """`tesserae export graphiti` outside a compile must still be honest.

    Compile hands its in-hand rows to ``export_graphiti``; the standalone CLI
    verb has none, so it reads them back from ``sqlite.db``. Without that the
    fix would only hold for the artifact compile writes, not the one a user
    regenerates afterwards.
    """
    from tesserae.memory.store import NodeMemoryRow, write_memory
    from tesserae.project import ProjectWiki

    project = tmp_path / "proj"
    wiki = ProjectWiki.init(project, name="demo_project")
    wiki.paths.graph.write_text(graphiti_sample_graph().to_json(), encoding="utf-8")
    write_memory(wiki.paths.sqlite, [NodeMemoryRow(node_id="Paper:demo", confidence="0.77")])

    wiki.export_graphiti()

    row = json.loads(wiki.paths.graphiti_episodes.read_text(encoding="utf-8"))
    assert row["metadata"]["confidence"] == "0.77"


def test_graphiti_sync_fails_helpfully_when_optional_dependency_missing(monkeypatch):
    monkeypatch.setattr("tesserae.graphiti_adapter.find_spec", lambda name: None if name == "graphiti_core" else object())

    with pytest.raises(GraphitiSyncUnavailableError) as exc:
        GraphitiResearchGraphAdapter().sync(graphiti_sample_graph(), neo4j_uri="bolt://localhost:7687", neo4j_user="neo4j", neo4j_password="password")

    assert "graphiti_core" in str(exc.value)
    assert "pip install" in str(exc.value)
