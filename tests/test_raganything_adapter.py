import json
from pathlib import Path

import pytest

from llm_wiki.raganything_adapter import RagAnythingGraphAdapter, merge_raganything_graph
from llm_wiki.research_graph import ResearchGraph, ResearchNode, ResearchNodeType


def _payload():
    return {
        "version": 1,
        "project": {"name": "demo"},
        "parser": "mineru",
        "documents": [
            {
                "id": "doc-abc123",
                "path": "docs/whitepaper.pdf",
                "sha256": "abc123",
                "parsed_dir": ".llm-wiki/external/raganything/parsed/abc123",
                "content_list": [
                    {"type": "text", "page_idx": 0, "text": "Mermaid rendering is described here."},
                    {"type": "image", "page_idx": 1, "img_path": "p1.png", "img_caption": ["Mermaid pipeline"]},
                    {"type": "table", "page_idx": 2, "table_body": "| a | b |\n| - | - |\n| 1 | 2 |", "table_caption": ["Performance"]},
                    {"type": "equation", "page_idx": 3, "latex": "E = mc^2", "equation_caption": ["Energy"]},
                ],
            }
        ],
    }


def test_import_payload_creates_source_file_with_multimodal_blocks(tmp_path):
    adapter = RagAnythingGraphAdapter(tmp_path)
    graph, manifest = adapter.import_payload(
        _payload(),
        artifact_rel=".llm-wiki/external/raganything/manifest.json",
        artifact_sha256="deadbeef",
    )
    sources = [n for n in graph.nodes if n.type == ResearchNodeType.SOURCE_FILE]
    assert len(sources) == 1
    src = sources[0]
    assert src.metadata["parser"] == "raganything"
    assert src.source_path == "docs/whitepaper.pdf"
    blocks = src.metadata["multimodal_blocks"]
    types = sorted({b["type"] for b in blocks})
    assert types == ["equation", "image", "table"]
    refs = src.metadata["external_refs"]
    assert refs[0]["system"] == "rag-anything"
    assert refs[0]["id"] == "doc-abc123"
    assert manifest["artifact_sha256"] == "deadbeef"
    assert manifest["imported_documents"]["doc-abc123"] == src.id


def test_import_artifact_reads_file_and_records_sha256(tmp_path):
    artifact = tmp_path / ".llm-wiki" / "external" / "raganything" / "manifest.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(json.dumps(_payload()), encoding="utf-8")

    result = RagAnythingGraphAdapter(tmp_path).import_artifact(artifact)
    assert result.manifest["artifact"].endswith("manifest.json")
    assert len(result.manifest["artifact_sha256"]) == 64  # sha256 hex
    assert result.graph.nodes  # at least one node


def test_merge_raganything_graph_appends_to_existing_graph_and_writes_manifest(tmp_path):
    artifact = tmp_path / ".llm-wiki" / "external" / "raganything" / "manifest.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(json.dumps(_payload()), encoding="utf-8")
    sync_path = tmp_path / ".llm-wiki" / "external" / "raganything-sync.json"

    base = ResearchGraph(nodes=[], edges=[])
    merged, manifest = merge_raganything_graph(
        base,
        project_root=tmp_path,
        artifact=artifact,
        sync_manifest_path=sync_path,
    )
    assert merged.nodes  # at least one source file node added
    assert sync_path.exists()
    written = json.loads(sync_path.read_text(encoding="utf-8"))
    assert written == manifest


def test_import_payload_emits_empty_string_description_when_no_text_blocks(tmp_path):
    payload = {
        "version": 1,
        "project": {"name": "demo"},
        "parser": "docling",
        "documents": [
            {
                "id": "doc-empty",
                "path": "data/empty.md",
                "sha256": "00",
                "parsed_dir": ".llm-wiki/external/raganything/parsed/00",
                "content_list": [
                    # No text block — only an image (caption empty), simulating
                    # a doc whose parsed body is non-textual.
                    {"type": "image", "page_idx": 0, "img_path": "x.png"}
                ],
            }
        ],
    }

    adapter = RagAnythingGraphAdapter(tmp_path)
    graph, _manifest = adapter.import_payload(payload, artifact_rel="manifest.json")
    sources = [n for n in graph.nodes if n.type == ResearchNodeType.SOURCE_FILE]
    assert len(sources) == 1
    # Description must be a string (NOT None) so SQLite's NOT NULL constraint is satisfied.
    assert isinstance(sources[0].description, str)
