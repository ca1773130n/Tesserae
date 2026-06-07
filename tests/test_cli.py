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


def test_cli_can_select_claude_cli_extractor(monkeypatch, tmp_path):
    source = tmp_path / "paper.md"
    source.write_text("# LLM Wiki Paper\nClaude should extract this.", encoding="utf-8")
    output = tmp_path / "graph.json"
    calls = []

    class FakeClaudeExtractor:
        def __init__(self, config_dirs, model, timeout):
            calls.append({"config_dirs": config_dirs, "model": model, "timeout": timeout})

        def extract_file(self, path, source_kind="SourceDocument"):
            calls.append({"path": str(path), "source_kind": source_kind})
            return ResearchGraph(
                nodes=[ResearchNode(id="Paper:tesserae-paper:test", name="LLM Wiki Paper", type=ResearchNodeType.PAPER)],
                edges=[],
            )

    import tesserae.cli as cli

    monkeypatch.setattr(cli, "ClaudeCLIResearchExtractor", FakeClaudeExtractor)

    assert main([
        "extract",
        str(source),
        "--source-kind",
        "Paper",
        "--extractor",
        "claude-cli",
        "--claude-config-dir",
        "/Users/neo/.claude-personal1",
        "--claude-model",
        "sonnet",
        "--claude-timeout",
        "9",
        "-o",
        str(output),
    ]) == 0

    data = json.loads(output.read_text(encoding="utf-8"))
    assert data["nodes"][0]["name"] == "LLM Wiki Paper"
    assert calls[0] == {"config_dirs": ["/Users/neo/.claude-personal1"], "model": "sonnet", "timeout": 9}
    assert calls[1]["source_kind"] == "Paper"


def test_cli_can_use_selective_claude_extractor(monkeypatch, tmp_path):
    selected = tmp_path / "important" / "paper.md"
    plain = tmp_path / "plain" / "paper.md"
    selected.parent.mkdir()
    plain.parent.mkdir()
    selected.write_text("# Selected", encoding="utf-8")
    plain.write_text("# Plain", encoding="utf-8")
    output = tmp_path / "graph.json"

    class FakeClaudeExtractor:
        def __init__(self, config_dirs, model, timeout):
            pass

        def extract_file(self, path, source_kind="SourceDocument"):
            return ResearchGraph(nodes=[ResearchNode(id="Paper:claude:test", name="Claude Selected", type=ResearchNodeType.PAPER)], edges=[])

    import tesserae.cli as cli

    monkeypatch.setattr(cli, "ClaudeCLIResearchExtractor", FakeClaudeExtractor)

    assert main([
        "extract",
        str(tmp_path),
        "--source-kind",
        "Paper",
        "--extractor",
        "selective-claude",
        "--claude-include",
        "*/important/*",
        "--claude-limit",
        "1",
        "-o",
        str(output),
    ]) == 0

    names = [node["name"] for node in json.loads(output.read_text(encoding="utf-8"))["nodes"]]
    assert "Claude Selected" in names
    assert "Plain" in names


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
    monkeypatch.setattr(cli, "GraphCanonicalizer", lambda: FakeCanonicalizer())

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


def test_cli_can_write_kuzu_graph_store(tmp_path):
    source = tmp_path / "paper.md"
    source.write_text("# Kuzu Paper\nGaussian Splatting supports novel view synthesis.", encoding="utf-8")
    graph_output = tmp_path / "graph.json"
    kuzu_output = tmp_path / "graph.kuzu"

    assert main([
        "extract",
        str(source),
        "--source-kind",
        "Paper",
        "--kuzu-output",
        str(kuzu_output),
        "-o",
        str(graph_output),
    ]) == 0

    from tesserae.persistence import KuzuResearchGraphStore

    counts = KuzuResearchGraphStore(kuzu_output).counts()
    assert counts["nodes"] > 0
    assert counts["edges"] > 0


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


def test_cli_can_write_cognee_bundle(tmp_path):
    source = tmp_path / "paper.md"
    source.write_text("# Cognee Paper\nGaussian Splatting supports novel view synthesis.", encoding="utf-8")
    graph_output = tmp_path / "graph.json"
    cognee_output = tmp_path / "cognee"

    assert main([
        "extract",
        str(source),
        "--source-kind",
        "Paper",
        "--cognee-output",
        str(cognee_output),
        "-o",
        str(graph_output),
    ]) == 0

    assert (cognee_output / "nodes.jsonl").exists()
    assert (cognee_output / "edges.jsonl").exists()
    assert (cognee_output / "manifest.json").exists()


def test_cli_can_add_cognee_bundle_directly(monkeypatch, tmp_path):
    source = tmp_path / "paper.md"
    source.write_text("# Cognee Direct Paper\nGaussian Splatting supports novel view synthesis.", encoding="utf-8")
    graph_output = tmp_path / "graph.json"
    cognee_output = tmp_path / "cognee"
    calls = []

    class FakeCogneeDirectImporter:
        async def add_bundle(self, bundle_dir, dataset_name="tesserae_research_graph", cognify=False, system_root=None, data_root=None):
            calls.append({"bundle_dir": str(bundle_dir), "dataset_name": dataset_name, "cognify": cognify, "system_root": system_root, "data_root": data_root})
            return {"dataset_name": dataset_name, "files_added": 2, "cognified": cognify}

    import tesserae.cli as cli

    monkeypatch.setattr(cli, "CogneeDirectImporter", FakeCogneeDirectImporter)

    assert main([
        "extract",
        str(source),
        "--source-kind",
        "Paper",
        "--cognee-output",
        str(cognee_output),
        "--cognee-add",
        "--cognee-dataset",
        "tesserae_test",
        "-o",
        str(graph_output),
    ]) == 0

    assert calls == [{"bundle_dir": str(cognee_output), "dataset_name": "tesserae_test", "cognify": False, "system_root": None, "data_root": None}]


def test_cli_can_cognify_cognee_bundle_with_codex_patch(monkeypatch, tmp_path):
    source = tmp_path / "paper.md"
    source.write_text("# Cognee Codex Paper\nGaussian Splatting supports novel view synthesis.", encoding="utf-8")
    graph_output = tmp_path / "graph.json"
    cognee_output = tmp_path / "cognee"
    calls = []
    patches = []

    class FakeCogneeDirectImporter:
        async def add_bundle(self, bundle_dir, dataset_name="tesserae_research_graph", cognify=False, system_root=None, data_root=None):
            calls.append({"bundle_dir": str(bundle_dir), "dataset_name": dataset_name, "cognify": cognify, "system_root": str(system_root) if system_root else None, "data_root": str(data_root) if data_root else None})
            return {"dataset_name": dataset_name, "files_added": 2, "cognified": cognify}

    class FakeCogneeCodexPatch:
        def __init__(self, model="gpt-5.4", timeout=300, deterministic_embeddings=False, ollama_embeddings=False, ollama_model="qwen3-embedding:0.6b", ollama_endpoint="http://127.0.0.1:11434/api/embed", ollama_timeout=120, embedding_dimensions=128):
            patches.append({"model": model, "timeout": timeout, "deterministic_embeddings": deterministic_embeddings, "ollama_embeddings": ollama_embeddings, "ollama_model": ollama_model, "ollama_endpoint": ollama_endpoint, "ollama_timeout": ollama_timeout, "embedding_dimensions": embedding_dimensions, "event": "init"})

        def __enter__(self):
            patches.append({"event": "enter"})
            return self

        def __exit__(self, exc_type, exc, tb):
            patches.append({"event": "exit"})
            return False

    import tesserae.cli as cli

    monkeypatch.setattr(cli, "CogneeDirectImporter", FakeCogneeDirectImporter)
    monkeypatch.setattr(cli, "CogneeCodexPatch", FakeCogneeCodexPatch)

    assert main([
        "extract",
        str(source),
        "--source-kind",
        "Paper",
        "--cognee-output",
        str(cognee_output),
        "--cognee-codex-cognify",
        "--cognee-dataset",
        "tesserae_codex_test",
        "--cognee-codex-model",
        "gpt-5.4",
        "--cognee-codex-timeout",
        "11",
        "--cognee-local-embedding-dimensions",
        "1024",
        "--cognee-embedding-provider",
        "ollama",
        "--cognee-ollama-embedding-model",
        "qwen3-embedding:0.6b",
        "--cognee-ollama-embedding-timeout",
        "44",
        "--cognee-system-root",
        str(tmp_path / "cognee_system"),
        "--cognee-data-root",
        str(tmp_path / "cognee_data"),
        "-o",
        str(graph_output),
    ]) == 0

    assert calls == [{"bundle_dir": str(cognee_output), "dataset_name": "tesserae_codex_test", "cognify": True, "system_root": str(tmp_path / "cognee_system"), "data_root": str(tmp_path / "cognee_data")}]
    assert patches == [{"model": "gpt-5.4", "timeout": 11, "deterministic_embeddings": False, "ollama_embeddings": True, "ollama_model": "qwen3-embedding:0.6b", "ollama_endpoint": "http://127.0.0.1:11434/api/embed", "ollama_timeout": 44, "embedding_dimensions": 1024, "event": "init"}, {"event": "enter"}, {"event": "exit"}]


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
