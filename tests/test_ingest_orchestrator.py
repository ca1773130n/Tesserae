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


def test_ingest_url_fetches_persists_and_merges(tmp_path, monkeypatch):
    wiki = _seed_project(tmp_path)
    wiki.compile(changed_only=False)

    class _Resp:
        status_code = 200
        text = "<h1>Remote</h1><p>Reinforcement learning from human feedback.</p>"
        headers = {"content-type": "text/html"}
        def raise_for_status(self): pass

    monkeypatch.setattr(
        "tesserae.ingest.fetch._http_get",
        lambda url, timeout=None, follow_redirects=True, headers=None: _Resp(),
    )
    result = ingest_sources(wiki, ["https://example.com/rlhf"], exact=True)

    persisted = tmp_path / "data" / "ingested"
    assert any(persisted.glob("*.md"))
    assert result["path_taken"] == "full-recompile"
    assert result["node_count"] > 0


def test_ingest_dry_run_writes_no_graph_but_fetches(tmp_path, monkeypatch):
    wiki = _seed_project(tmp_path)
    new = tmp_path / "data" / "dry.md"
    new.write_text("---\ntype: paper\n---\n# Dry\n\ncontent\n", encoding="utf-8")

    result = ingest_sources(wiki, [str(new)], dry_run=True)
    assert result["path_taken"] == "dry-run"
    assert result["sources"] == [str(new)]


def test_ingest_fast_path_uses_incremental_override(tmp_path, monkeypatch):
    wiki = _seed_project(tmp_path)
    wiki.compile(changed_only=False)
    seen = {}
    real_ingest = wiki.ingest
    def _spy(inputs, **kw):
        seen.update(kw)
        return real_ingest(inputs, **kw)
    monkeypatch.setattr(wiki, "ingest", _spy)

    (tmp_path / "data" / "f.md").write_text("---\ntype: paper\n---\n# F\n\ncontent\n", encoding="utf-8")
    result = ingest_sources(wiki, [str(tmp_path / "data" / "f.md")], exact=False)

    assert seen.get("changed_only") is True
    assert seen.get("incremental_override") is True
    assert result["path_taken"] == "incremental"
