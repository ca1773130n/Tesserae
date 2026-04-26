import json

from llm_wiki.obsidian_adapter import ObsidianVaultAdapter
from llm_wiki.research_graph import ResearchEdge, ResearchGraph, ResearchNode, ResearchNodeType


def obsidian_sample_graph():
    paper = ResearchNode(id="Paper:obsidian", name="Obsidian Paper", type=ResearchNodeType.PAPER, source_path="notes/obsidian.md")
    method = ResearchNode(id="Method:gs", name="Gaussian Splatting", type=ResearchNodeType.METHODOLOGICAL_CONCEPT)
    return ResearchGraph(
        nodes=[paper, method],
        edges=[ResearchEdge(source=paper.id, target=method.id, type="uses", evidence="Obsidian Paper uses Gaussian Splatting.")],
    )


def test_obsidian_adapter_writes_vault_projection_and_config(tmp_path):
    vault = tmp_path / "vault"

    result = ObsidianVaultAdapter(vault_name="Demo Wiki").write_vault(obsidian_sample_graph(), vault)

    assert result["vault_path"] == str(vault)
    assert result["notes"] >= 3
    assert (vault / ".obsidian" / "app.json").exists()
    assert (vault / ".obsidian" / "graph.json").exists()
    assert (vault / "index.md").exists()
    assert (vault / "README.md").exists()
    assert (vault / "concepts" / "gaussian-splatting.md").exists()
    app = json.loads((vault / ".obsidian" / "app.json").read_text(encoding="utf-8"))
    assert app["attachmentFolderPath"] == "raw/assets"
    assert "Dataview" in (vault / "README.md").read_text(encoding="utf-8")


def test_obsidian_adapter_can_write_dataview_dashboard(tmp_path):
    vault = tmp_path / "vault"

    ObsidianVaultAdapter(vault_name="Demo Wiki").write_vault(obsidian_sample_graph(), vault)

    dashboard = (vault / "_meta" / "dashboard.md").read_text(encoding="utf-8")
    assert "# Demo Wiki Dashboard" in dashboard
    assert "TABLE type, source_path" in dashboard
    assert 'FROM "papers" OR "concepts" OR "claims"' in dashboard
