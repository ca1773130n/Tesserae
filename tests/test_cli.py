import json
from pathlib import Path

from tesserae.cli import main
from tesserae.research_graph import ResearchEdge, ResearchGraph, ResearchNode, ResearchNodeType


def test_cli_can_emit_corpus_trends(tmp_path):
    day1 = tmp_path / "data" / "research" / "daily" / "2026-04-25" / "papers" / "2604.00538" / "paper.md"
    day2 = tmp_path / "data" / "research" / "daily" / "2026-04-26" / "papers" / "2601.17835" / "paper.md"
    day1.parent.mkdir(parents=True)
    day2.parent.mkdir(parents=True)
    day1.write_text(
        """# 논문 분석: 2604.00538
> - 분석일: 2026-04-25
TRiGS: Temporal Rigid-Body Motion for Scalable 4D Gaussian Splatting | Cool Papers
4D Gaussian Splatting and 4DGS improve dynamic reconstruction.
""",
        encoding="utf-8",
    )
    day2.write_text(
        """# 논문 분석: 2601.17835
> - 분석일: 2026-04-26
Geometry-Grounded Gaussian Splatting | Cool Papers
Gaussian Splatting supports novel view synthesis.
""",
        encoding="utf-8",
    )
    output = tmp_path / "graph.json"

    assert main(["extract", str(tmp_path / "data"), "--source-kind", "Paper", "--trends", "--min-trend-sources", "2", "-o", str(output)]) == 0

    payload = output.read_text(encoding="utf-8")
    assert '"type": "Trend"' in payload
    assert "Trend: Gaussian Splatting" in payload


def test_cli_can_select_llm_extractor(monkeypatch, tmp_path):
    """`--extractor llm` drives the provider-agnostic LLMJsonClient (codex/claude/
    api per config), not a hardcoded Claude subprocess."""
    import tesserae.llm_json as lj

    source = tmp_path / "paper.md"
    source.write_text("# LLM Wiki Paper\nExtract this.", encoding="utf-8")
    output = tmp_path / "graph.json"
    seen = {}

    class _FakeClient:
        def complete_json(self, *, system, user, schema_name, cache_key=None, max_retries=2):
            seen["schema"] = schema_name
            return {"nodes": [{"name": "LLM Wiki Paper", "type": "Paper"}], "edges": []}

    monkeypatch.setattr(lj, "build_default_json_client", lambda **k: _FakeClient())

    assert main([
        "extract", str(source), "--source-kind", "Paper",
        "--extractor", "llm", "--llm-provider", "codex", "--llm-model", "gpt-5.6-luna",
        "-o", str(output),
    ]) == 0

    data = json.loads(output.read_text(encoding="utf-8"))
    assert any(n["name"] == "LLM Wiki Paper" for n in data["nodes"])
    assert seen["schema"] == "research-graph-v1"  # went through the LLM client


def test_cli_can_use_selective_llm_extractor(monkeypatch, tmp_path):
    import tesserae.llm_json as lj

    selected = tmp_path / "important" / "paper.md"
    plain = tmp_path / "plain" / "paper.md"
    selected.parent.mkdir()
    plain.parent.mkdir()
    selected.write_text("# Selected", encoding="utf-8")
    plain.write_text("# Plain", encoding="utf-8")
    output = tmp_path / "graph.json"

    class _FakeClient:
        def complete_json(self, *, system, user, schema_name, cache_key=None, max_retries=2):
            return {"nodes": [{"name": "Claude Selected", "type": "Paper"}], "edges": []}

    monkeypatch.setattr(lj, "build_default_json_client", lambda **k: _FakeClient())

    assert main([
        "extract", str(tmp_path), "--source-kind", "Paper",
        "--extractor", "selective-llm", "--llm-include", "*/important/*", "--llm-limit", "1",
        "-o", str(output),
    ]) == 0

    names = [node["name"] for node in json.loads(output.read_text(encoding="utf-8"))["nodes"]]
    assert "Claude Selected" in names  # important/ routed to the LLM
    assert "Plain" in names            # plain/ stayed deterministic


def test_cli_can_canonicalize_and_write_review_queue(tmp_path):
    source = tmp_path / "paper.md"
    source.write_text(
        """# Alias Paper
Gaussian Splatting, 3DGS, and 3D Gaussian Splatting are discussed for novel view synthesis.
""",
        encoding="utf-8",
    )
    graph_output = tmp_path / "graph.json"
    review_output = tmp_path / "review.json"

    assert main([
        "extract",
        str(source),
        "--source-kind",
        "Paper",
        "--canonicalize",
        "--review-output",
        str(review_output),
        "--pretty",
        "-o",
        str(graph_output),
    ]) == 0

    graph = json.loads(graph_output.read_text(encoding="utf-8"))
    review = json.loads(review_output.read_text(encoding="utf-8"))
    names = [node["name"] for node in graph["nodes"]]
    assert "Gaussian Splatting" in names
    assert "3DGS" not in names
    assert "items" in review


def test_cli_can_apply_review_decision_file(monkeypatch, tmp_path):
    source = tmp_path / "paper.md"
    source.write_text("# Review Paper\nignored by fake extractor", encoding="utf-8")
    graph_output = tmp_path / "merged.json"
    decisions_path = tmp_path / "decisions.json"
    review_item_id = "review:similar_name:test"
    decisions_path.write_text(
        json.dumps({"decisions": [{"item_id": review_item_id, "action": "merge", "canonical_node_id": "MethodologicalConcept:gs:test"}]}),
        encoding="utf-8",
    )

    class FakeCanonicalizer:
        def canonicalize(self, graph):
            from tesserae.canonicalization import CanonicalizationResult, ReviewItem

            return CanonicalizationResult(
                graph=graph,
                review_items=[
                    ReviewItem(
                        id=review_item_id,
                        left_node_id="MethodologicalConcept:gs:test",
                        right_node_id="MethodologicalConcept:4dgs:test",
                        left_name="Gaussian Splatting",
                        right_name="4D Gaussian Splatting",
                        node_type="MethodologicalConcept",
                        reason="similar_name",
                        score=0.9,
                    )
                ],
            )

    class FakeExtractor:
        def extract_file(self, path, source_kind="SourceDocument"):
            return ResearchGraph(
                nodes=[
                    ResearchNode(id="MethodologicalConcept:gs:test", name="Gaussian Splatting", type=ResearchNodeType.METHODOLOGICAL_CONCEPT),
                    ResearchNode(id="MethodologicalConcept:4dgs:test", name="4D Gaussian Splatting", type=ResearchNodeType.METHODOLOGICAL_CONCEPT),
                ],
                edges=[ResearchEdge(source="MethodologicalConcept:4dgs:test", target="MethodologicalConcept:gs:test", type="shares_concept_with")],
            )

    import tesserae.cli as cli

    monkeypatch.setattr(cli, "ResearchGraphExtractor", lambda: FakeExtractor())
    monkeypatch.setattr(cli, "GraphCanonicalizer", lambda **kwargs: FakeCanonicalizer())

    assert main([
        "extract",
        str(source),
        "--source-kind",
        "Paper",
        "--canonicalize",
        "--apply-review-decisions",
        str(decisions_path),
        "-o",
        str(graph_output),
    ]) == 0

    graph = json.loads(graph_output.read_text(encoding="utf-8"))
    assert [node["name"] for node in graph["nodes"]] == ["Gaussian Splatting"]
    assert graph["edges"] == []


def test_cli_can_write_markdown_projection(tmp_path):
    source = tmp_path / "paper.md"
    source.write_text("# Projection Paper\nGaussian Splatting supports novel view synthesis.", encoding="utf-8")
    graph_output = tmp_path / "graph.json"
    projection_dir = tmp_path / "projection"

    assert main([
        "extract",
        str(source),
        "--source-kind",
        "Paper",
        "--project-markdown",
        str(projection_dir),
        "-o",
        str(graph_output),
    ]) == 0

    assert (projection_dir / "index.md").exists()
    assert (projection_dir / "concepts" / "gaussian-splatting.md").exists()
    assert "[[gaussian-splatting]]" in (projection_dir / "index.md").read_text(encoding="utf-8")


def test_cli_can_write_sqlite_graph_store(tmp_path):
    source = tmp_path / "paper.md"
    source.write_text("# SQLite Paper\nGaussian Splatting supports novel view synthesis.", encoding="utf-8")
    graph_output = tmp_path / "graph.json"
    sqlite_output = tmp_path / "graph.sqlite"

    assert main([
        "extract",
        str(source),
        "--source-kind",
        "Paper",
        "--sqlite-output",
        str(sqlite_output),
        "-o",
        str(graph_output),
    ]) == 0

    import sqlite3
    con = sqlite3.connect(sqlite_output)
    assert con.execute("select count(*) from nodes").fetchone()[0] > 0
    assert con.execute("select count(*) from edges").fetchone()[0] > 0


def test_cli_can_write_graph_report(tmp_path):
    source = tmp_path / "paper.md"
    source.write_text("# Report Paper\nGaussian Splatting supports novel view synthesis.", encoding="utf-8")
    graph_output = tmp_path / "graph.json"
    report_output = tmp_path / "report.md"

    assert main([
        "extract",
        str(source),
        "--source-kind",
        "Paper",
        "--report-output",
        str(report_output),
        "-o",
        str(graph_output),
    ]) == 0

    report = report_output.read_text(encoding="utf-8")
    assert "# Research Graph Report" in report
    assert "node_count:" in report
    assert "## Claim Evidence Coverage" in report


def test_cli_can_write_review_human_workflow_files(tmp_path):
    source = tmp_path / "paper.md"
    source.write_text("# Review UX Paper\nGaussian Splatting and 4D Gaussian Splatting are related.", encoding="utf-8")
    graph_output = tmp_path / "graph.json"
    review_json = tmp_path / "review.json"
    review_md = tmp_path / "review.md"
    review_jsonl = tmp_path / "review.jsonl"
    template = tmp_path / "decisions.template.json"

    assert main([
        "extract",
        str(source),
        "--source-kind",
        "Paper",
        "--canonicalize",
        "--review-output",
        str(review_json),
        "--review-markdown-output",
        str(review_md),
        "--review-jsonl-output",
        str(review_jsonl),
        "--review-decisions-template",
        str(template),
        "-o",
        str(graph_output),
    ]) == 0

    assert "# Research Graph Review Queue" in review_md.read_text(encoding="utf-8")
    assert review_jsonl.exists()
    assert json.loads(template.read_text(encoding="utf-8"))["decisions"] is not None


def test_cli_extract_cognee_flags_removed_with_stub(tmp_path, capsys):
    """Backend EOL stage 2 (0.19): every extract --cognee-* flag prints a
    one-line removal stub and exits 2 (clean break, never an alias)."""
    import pytest

    source = tmp_path / "paper.md"
    source.write_text("# Cognee Paper\nGaussian Splatting supports novel view synthesis.", encoding="utf-8")

    for flag in (
        ["--cognee-output", str(tmp_path / "cognee")],
        ["--cognee-add"],
        ["--cognee-cognify"],
        ["--cognee-dataset", "tesserae_test"],
        ["--cognee-codex-cognify"],
        ["--cognee-codex-model", "gpt-5.6-luna"],
        ["--cognee-codex-timeout", "11"],
        ["--cognee-embedding-provider", "ollama"],
        ["--cognee-local-embedding-dimensions", "1024"],
        ["--cognee-system-root", "/tmp/sys"],
        ["--cognee-data-root", "/tmp/data"],
    ):
        with pytest.raises(SystemExit) as exc_info:
            main(["extract", str(source), *flag])
        assert exc_info.value.code == 2
        err = capsys.readouterr().err
        assert f"extract: {flag[0]} was removed in 0.19" in err


def test_cli_changed_only_uses_batch_manifest(tmp_path):
    source = tmp_path / "paper.md"
    source.write_text("# Batch Paper\nGaussian Splatting supports novel view synthesis.", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    first_output = tmp_path / "first.json"
    second_output = tmp_path / "second.json"

    assert main(["extract", str(source), "--source-kind", "Paper", "--batch-manifest", str(manifest), "--changed-only", "-o", str(first_output)]) == 0
    assert main(["extract", str(source), "--source-kind", "Paper", "--batch-manifest", str(manifest), "--changed-only", "-o", str(second_output)]) == 0

    first = json.loads(first_output.read_text(encoding="utf-8"))
    second = json.loads(second_output.read_text(encoding="utf-8"))
    manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert first["nodes"]
    assert second["nodes"] == []
    assert str(source) in manifest_payload["files"]


def test_cli_limit_caps_batch_processing(tmp_path):
    for idx in range(3):
        (tmp_path / f"paper{idx}.md").write_text(f"# Paper {idx}\nGaussian Splatting", encoding="utf-8")
    output = tmp_path / "limited.json"

    assert main(["extract", str(tmp_path), "--source-kind", "Paper", "--limit", "2", "-o", str(output)]) == 0

    data = json.loads(output.read_text(encoding="utf-8"))
    paper_nodes = [node for node in data["nodes"] if node["type"] == "Paper"]
    assert len(paper_nodes) == 2


def test_cli_extract_accepts_canonicalize_semantic(monkeypatch, tmp_path, capsys):
    """`--canonicalize-semantic` reaches GraphCanonicalizer and reports honestly."""
    source = tmp_path / "paper.md"
    source.write_text("# Semantic Paper\nGaussian Splatting supports novel view synthesis.", encoding="utf-8")
    graph_output = tmp_path / "graph.json"
    captured: dict = {}

    class FakeCanonicalizer:
        def canonicalize(self, graph):
            from tesserae.canonicalization import CanonicalizationResult

            return CanonicalizationResult(
                graph=graph,
                stats={"semantic_backend": "stub", "semantic_added": 3},
            )

    import tesserae.cli as cli

    def factory(**kwargs):
        captured.update(kwargs)
        return FakeCanonicalizer()

    monkeypatch.setattr(cli, "GraphCanonicalizer", factory)

    capsys.readouterr()
    assert main([
        "extract",
        str(source),
        "--source-kind",
        "Paper",
        "--canonicalize-semantic",
        "-o",
        str(graph_output),
    ]) == 0
    # ``-o`` here is an ad-hoc path, not ``<root>/.tesserae/graph.json``, so
    # there is no sidecar to cache vectors in and the pass runs uncached rather
    # than creating one as a side effect of an extract.
    assert captured == {"semantic": True, "vector_cache": None}
    err = capsys.readouterr().err
    assert "3 review candidates via stub" in err
    assert "candidates only, nothing merged" in err


def test_cli_extract_semantic_skip_says_why(monkeypatch, tmp_path, capsys):
    """A skip must never read as 'ran and found nothing'."""
    source = tmp_path / "paper.md"
    source.write_text("# Skip Paper\nGaussian Splatting.", encoding="utf-8")
    graph_output = tmp_path / "graph.json"

    class FakeCanonicalizer:
        def canonicalize(self, graph):
            from tesserae.canonicalization import CanonicalizationResult

            return CanonicalizationResult(
                graph=graph,
                stats={"semantic_backend": "hash-bucket", "semantic_added": 0,
                       "semantic_skipped": "no real embedding backend (install tesserae[semantic])"},
            )

    import tesserae.cli as cli

    monkeypatch.setattr(cli, "GraphCanonicalizer", lambda **kwargs: FakeCanonicalizer())
    capsys.readouterr()
    assert main([
        "extract", str(source), "--source-kind", "Paper",
        "--canonicalize-semantic", "-o", str(graph_output),
    ]) == 0
    err = capsys.readouterr().err
    assert "semantic canonicalization skipped" in err
    assert "tesserae[semantic]" in err
