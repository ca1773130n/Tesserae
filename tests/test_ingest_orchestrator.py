from pathlib import Path

from tesserae.project import ProjectWiki
from tesserae.ingest.orchestrator import ingest_sources


def _seed_project(root: Path) -> ProjectWiki:
    (root / "data").mkdir(parents=True, exist_ok=True)
    (root / "data" / "seed.md").write_text(
        "---\ntype: paper\n---\n# Seed\n\nGraph neural networks for retrieval.\n",
        encoding="utf-8",
    )
    return ProjectWiki.init(root, name="ingest_test")


def test_ingest_local_file_merges_and_reports(tmp_path):
    wiki = _seed_project(tmp_path)
    wiki.compile(changed_only=False)  # establish baseline graph

    # NOTE: the deterministic extractor surfaces the heading (node name) into
    # graph.json, not body prose. Put the distinguishing token in the heading so
    # the assertion below tests real merge behavior, not a false fixture assumption.
    new = tmp_path / "data" / "new.md"
    new.write_text(
        "---\ntype: paper\n---\n# Diffusion Models for Planning\n\nDiffusion models for planning.\n",
        encoding="utf-8",
    )

    result = ingest_sources(wiki, [str(new)], exact=True)

    assert result["path_taken"] == "full-recompile"
    assert result["node_count"] > 0
    assert "graph_path" in result
    graph_text = Path(result["graph_path"]).read_text(encoding="utf-8")
    assert "Diffusion" in graph_text or "diffusion" in graph_text.lower()
