import json
from pathlib import Path

import pytest

from tesserae.raganything_adapter import RagAnythingGraphAdapter, merge_raganything_graph
from tesserae.research_graph import ResearchGraph, ResearchNode, ResearchNodeType


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
                "parsed_dir": ".tesserae/external/raganything/parsed/abc123",
                "content_list": [
                    {"type": "text", "page_idx": 0, "text": "Mermaid rendering is described here."},
                    {"type": "image", "page_idx": 1, "img_path": "p1.png", "img_caption": ["Mermaid pipeline"]},
                    {"type": "table", "page_idx": 2, "table_body": "| a | b |\n| - | - |\n| 1 | 2 |", "table_caption": ["Performance"]},
                    {"type": "equation", "page_idx": 3, "latex": "E = mc^2", "equation_caption": ["Energy"]},
                ],
            }
        ],
    }


def test_import_payload_creates_source_document_with_multimodal_blocks(tmp_path):
    """Raganything-projected docs are SourceDocument nodes (Bug B fix).

    They previously landed as SOURCE_FILE, which routed them into
    code_graph.json via partition_graph and made them invisible to the
    visual payload + public wiki.
    """
    adapter = RagAnythingGraphAdapter(tmp_path)
    graph, manifest = adapter.import_payload(
        _payload(),
        artifact_rel=".tesserae/external/raganything/manifest.json",
        artifact_sha256="deadbeef",
    )
    sources = [n for n in graph.nodes if n.type == ResearchNodeType.SOURCE_DOCUMENT]
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
    artifact = tmp_path / ".tesserae" / "external" / "raganything" / "manifest.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(json.dumps(_payload()), encoding="utf-8")

    result = RagAnythingGraphAdapter(tmp_path).import_artifact(artifact)
    assert result.manifest["artifact"].endswith("manifest.json")
    assert len(result.manifest["artifact_sha256"]) == 64  # sha256 hex
    assert result.graph.nodes  # at least one node


def test_merge_raganything_graph_appends_to_existing_graph_and_writes_manifest(tmp_path):
    artifact = tmp_path / ".tesserae" / "external" / "raganything" / "manifest.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(json.dumps(_payload()), encoding="utf-8")
    sync_path = tmp_path / ".tesserae" / "external" / "raganything-sync.json"

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
                "parsed_dir": ".tesserae/external/raganything/parsed/00",
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
    sources = [n for n in graph.nodes if n.type == ResearchNodeType.SOURCE_DOCUMENT]
    assert len(sources) == 1
    # Description must be a string (NOT None) so SQLite's NOT NULL constraint is satisfied.
    assert isinstance(sources[0].description, str)


# --- first-class Artifact evidence nodes (roadmap step 9) -------------------


def _payload_with_asset(root: Path) -> dict:
    """The standard payload plus the image asset ON DISK under parsed_dir,
    so the image block's content hash is resolvable."""
    payload = _payload()
    parsed_dir = root / ".tesserae" / "external" / "raganything" / "parsed" / "abc123"
    parsed_dir.mkdir(parents=True, exist_ok=True)
    (parsed_dir / "p1.png").write_bytes(b"\x89PNG\r\n\x1a\nfake-figure-bytes")
    return payload


def test_import_payload_mints_an_artifact_node_per_multimodal_block(tmp_path):
    adapter = RagAnythingGraphAdapter(tmp_path)
    graph, manifest = adapter.import_payload(
        _payload_with_asset(tmp_path), artifact_rel="manifest.json"
    )

    artifacts = {
        n.metadata["kind"]: n
        for n in graph.nodes
        if n.type == ResearchNodeType.ARTIFACT
    }
    assert sorted(artifacts) == ["equation", "image", "table"]

    image = artifacts["image"]
    assert image.name == "Figure: Mermaid pipeline"
    assert image.metadata["caption"] == ["Mermaid pipeline"]
    assert image.metadata["page"] == 1
    assert len(image.metadata["content_hash"]) == 64
    # The asset lives under the project root, so its project-relative path
    # is stored; the MinerU img_path string never lands on the node.
    assert image.metadata["asset_path"] == (
        ".tesserae/external/raganything/parsed/abc123/p1.png"
    )
    assert "img_path" not in image.metadata

    table = artifacts["table"]
    assert table.description == "| a | b |\n| - | - |\n| 1 | 2 |"
    equation = artifacts["equation"]
    assert equation.description == "E = mc^2"
    assert equation.name == "Equation: Energy"

    # One part_of edge per artifact, into the owning document.
    doc = next(n for n in graph.nodes if n.type == ResearchNodeType.SOURCE_DOCUMENT)
    part_of = [
        e for e in graph.edges
        if e.type == "part_of" and e.target == doc.id
    ]
    assert sorted(e.source for e in part_of) == sorted(n.id for n in artifacts.values())

    # The flattened record stays (back-compat), gaining the join key.
    for block in doc.metadata["multimodal_blocks"]:
        assert len(block["content_hash"]) == 64
    # And the doc itself now carries its content hash (federation identity).
    assert doc.metadata["content_hash"] == "abc123"
    assert "skipped_blocks" not in manifest


def test_artifact_ids_are_content_seeded_not_path_seeded(tmp_path):
    """THE non-negotiable: identical CONTENT yields byte-identical artifact
    node ids — across different roots, different parsed_dirs, different
    img_path filenames AND different captions — and neither root leaks into
    the serialized graph. Varying everything except the content is what
    makes this a real proof: a path-, caption- or page-seeded id would
    differ somewhere below."""
    import hashlib

    from tesserae.research_graph import stable_id

    root_a = tmp_path / "plain" / "proj"
    root_a.mkdir(parents=True)
    payload_a = _payload_with_asset(root_a)

    root_b = tmp_path / "2026-08-13-dated" / "proj"
    root_b.mkdir(parents=True)
    payload_b = json.loads(json.dumps(_payload()))
    doc_b = payload_b["documents"][0]
    doc_b["parsed_dir"] = "elsewhere/parsed/zzz"
    for block in doc_b["content_list"]:
        if block["type"] == "image":
            block["img_path"] = "renamed-by-a-new-parser.png"
            block["img_caption"] = ["A different caption entirely"]
            block["page_idx"] = 7
    parsed_b = root_b / "elsewhere" / "parsed" / "zzz"
    parsed_b.mkdir(parents=True)
    (parsed_b / "renamed-by-a-new-parser.png").write_bytes(
        b"\x89PNG\r\n\x1a\nfake-figure-bytes"  # SAME bytes as root_a's p1.png
    )

    graph_a, _ = RagAnythingGraphAdapter(root_a).import_payload(
        payload_a, artifact_rel="manifest.json"
    )
    graph_b, _ = RagAnythingGraphAdapter(root_b).import_payload(
        payload_b, artifact_rel="manifest.json"
    )

    ids_a = sorted(n.id for n in graph_a.nodes if n.type == ResearchNodeType.ARTIFACT)
    ids_b = sorted(n.id for n in graph_b.nodes if n.type == ResearchNodeType.ARTIFACT)
    assert ids_a == ids_b
    assert len(ids_a) == 3

    # And the seed derivation is EXACTLY the documented one — pinning the
    # formula makes any future path/caption/page ingredient impossible, not
    # merely undetected.
    image_hash = hashlib.sha256(b"\x89PNG\r\n\x1a\nfake-figure-bytes").hexdigest()
    expected = stable_id("Artifact", f"raganything:artifact:image:{image_hash}")
    images_a = [
        n for n in graph_a.nodes
        if n.type == ResearchNodeType.ARTIFACT and n.metadata["kind"] == "image"
    ]
    assert [n.id for n in images_a] == [expected]

    for root, graph in ((root_a, graph_a), (root_b, graph_b)):
        text = graph.to_json(indent=2)
        assert str(root) not in text
        assert str(tmp_path) not in text


def test_declared_parsed_dir_never_falls_back_to_an_unrelated_root_file(tmp_path):
    """A missing parsed asset must be a LOUD skip, never a silent wrong-file
    hit: with parsed_dir declared, an unrelated same-named file at the
    project root must not donate its bytes as the figure's identity."""
    (tmp_path / "p1.png").write_bytes(b"UNRELATED ROOT FILE")

    graph, manifest = RagAnythingGraphAdapter(tmp_path).import_payload(
        _payload(), artifact_rel="manifest.json"  # asset NOT under parsed_dir
    )

    kinds = sorted(
        n.metadata["kind"] for n in graph.nodes
        if n.type == ResearchNodeType.ARTIFACT
    )
    assert kinds == ["equation", "table"]  # no image artifact minted
    assert manifest["skipped_blocks"][0]["reason"] == "image asset not found"


def test_out_of_tree_document_path_never_lands_on_the_artifact(tmp_path):
    """raganything_refresh stores out-of-tree document paths ABSOLUTE; the
    document node keeps that pre-existing behaviour, but the Artifact node
    must never carry a machine-specific path."""
    payload = _payload_with_asset(tmp_path)
    payload["documents"][0]["path"] = "/somewhere/else/whitepaper.pdf"

    graph, _ = RagAnythingGraphAdapter(tmp_path).import_payload(
        payload, artifact_rel="manifest.json"
    )

    artifacts = [n for n in graph.nodes if n.type == ResearchNodeType.ARTIFACT]
    assert len(artifacts) == 3
    for node in artifacts:
        assert node.source_path is None
        assert "/somewhere/else" not in json.dumps(
            {"id": node.id, "name": node.name, "metadata": node.metadata}
        )


def test_missing_image_asset_skips_the_artifact_loudly(tmp_path):
    """No resolvable bytes -> no node (never a path- or caption-derived
    pseudo-identity), the block stays flattened, and the skip is recorded
    in the manifest rather than silently swallowed."""
    adapter = RagAnythingGraphAdapter(tmp_path)
    graph, manifest = adapter.import_payload(_payload(), artifact_rel="manifest.json")

    kinds = sorted(
        n.metadata["kind"] for n in graph.nodes
        if n.type == ResearchNodeType.ARTIFACT
    )
    assert kinds == ["equation", "table"]  # image skipped: p1.png not on disk

    doc = next(n for n in graph.nodes if n.type == ResearchNodeType.SOURCE_DOCUMENT)
    image_blocks = [b for b in doc.metadata["multimodal_blocks"] if b["type"] == "image"]
    assert len(image_blocks) == 1
    assert "content_hash" not in image_blocks[0]

    assert manifest["skipped_blocks"] == [
        {
            "doc_id": "doc-abc123",
            "type": "image",
            "img_path": "p1.png",
            "reason": "image asset not found",
        }
    ]


def test_identical_content_across_documents_is_one_artifact(tmp_path):
    """Byte-identical content in two documents collapses to ONE node —
    graph identity equals federation identity — with a part_of edge to
    EACH owning document."""
    payload = _payload()
    second = dict(payload["documents"][0])
    second = json.loads(json.dumps(second))
    second["id"] = "doc-def456"
    second["path"] = "docs/other.pdf"
    second["sha256"] = "def456"
    payload["documents"].append(second)

    graph, _ = RagAnythingGraphAdapter(tmp_path).import_payload(
        payload, artifact_rel="manifest.json"
    )

    tables = [
        n for n in graph.nodes
        if n.type == ResearchNodeType.ARTIFACT and n.metadata["kind"] == "table"
    ]
    assert len(tables) == 1
    owners = {
        e.target for e in graph.edges
        if e.type == "part_of" and e.source == tables[0].id
    }
    docs = {n.id for n in graph.nodes if n.type == ResearchNodeType.SOURCE_DOCUMENT}
    assert owners == docs and len(docs) == 2


def test_same_named_artifacts_with_different_content_never_fuse(tmp_path):
    """Two figures captioned identically in different papers are distinct
    evidence: the aggressive same-name dedup must skip ARTIFACT (identity
    lives in the content hash)."""
    from tesserae.research_graph import merge_same_type_aliased_duplicates

    payload = _payload_with_asset(tmp_path)
    second = json.loads(json.dumps(payload["documents"][0]))
    second["id"] = "doc-def456"
    second["path"] = "docs/other.pdf"
    second["sha256"] = "def456"
    second["parsed_dir"] = ".tesserae/external/raganything/parsed/def456"
    parsed = tmp_path / ".tesserae" / "external" / "raganything" / "parsed" / "def456"
    parsed.mkdir(parents=True)
    (parsed / "p1.png").write_bytes(b"\x89PNG\r\n\x1a\nDIFFERENT-figure-bytes")
    payload["documents"].append(second)

    graph, _ = RagAnythingGraphAdapter(tmp_path).import_payload(
        payload, artifact_rel="manifest.json"
    )
    figures = [
        n for n in graph.nodes
        if n.type == ResearchNodeType.ARTIFACT and n.metadata["kind"] == "image"
    ]
    assert len(figures) == 2
    assert figures[0].name == figures[1].name  # same caption, same name

    nodes, edges = merge_same_type_aliased_duplicates(list(graph.nodes), list(graph.edges))
    survivors = [n for n in nodes if n.type == ResearchNodeType.ARTIFACT and n.metadata["kind"] == "image"]
    assert len(survivors) == 2  # dedup exemption held


def test_artifact_is_producer_owned_across_every_hand_maintained_list():
    """The step-6 precedent, node-type edition: one source of truth,
    enforced where the code cannot derive it."""
    from tesserae.agent_write import DENIED_NODE_TYPES
    from tesserae.canonicalization import CANONICALIZABLE_TYPES
    from tesserae.federation import identity_key
    from tesserae.research_graph import (
        CODE_GRAPH_TYPES,
        EXTRACTABLE_NODE_TYPES,
        PROCEDURAL_POOL_TYPES,
        _CROSS_TYPE_MERGE_PRIORITY,
        merge_cross_type_duplicates,
    )
    from tesserae.site.pages import _GRAPH_VIEW_TYPES
    from tesserae.site.search import WIKI_LAYER_TYPES
    from tesserae.wiki_projector import ASSERTION_LAYER_TYPES, kind_for_node

    # The LLM never gains the type; agents cannot mint it.
    assert "Artifact" not in EXTRACTABLE_NODE_TYPES
    assert "Artifact" in DENIED_NODE_TYPES
    # Evidence bucket, like EvidenceSpan: no wiki page, no public URL.
    assert ResearchNodeType.ARTIFACT in ASSERTION_LAYER_TYPES
    artifact = ResearchNode(
        id="Artifact:x", name="Figure: x", type=ResearchNodeType.ARTIFACT,
        description="", metadata={"content_hash": "cafe" * 16},
    )
    assert kind_for_node(artifact) is None
    # Federation identity is the content hash.
    assert identity_key(artifact) == ("Artifact", "cafe" * 16)
    # Deliberate absences, pinned so drift is loud.
    assert ResearchNodeType.ARTIFACT not in CODE_GRAPH_TYPES
    assert ResearchNodeType.ARTIFACT not in PROCEDURAL_POOL_TYPES
    assert ResearchNodeType.ARTIFACT not in CANONICALIZABLE_TYPES
    merge_cross_type_duplicates([], [])  # force late-bound init
    assert ResearchNodeType.ARTIFACT not in _CROSS_TYPE_MERGE_PRIORITY
    assert "Artifact" not in _GRAPH_VIEW_TYPES
    assert "Artifact" not in WIKI_LAYER_TYPES
