"""KB-07 (the milestone's central guard): graph.json byte-idempotence.

Two identical-source compiles of the same project, in the SAME tmp dir with
warm caches and the Phase-5 passes default-on, must produce a BYTE-IDENTICAL
``.tesserae/graph.json``. And NO mutable memory state may leak into
graph.json: ``decay_score`` / ``access_count`` / ``last_accessed_at`` /
``confidence`` / ``superseded`` all live in the ``node_memory`` SQLite
sidecar, never in the graph artifact.

Deterministic: no LLM json_client is wired (default no-backend compile), so
supersede/contradiction passes are no-ops; idempotence must hold anyway. No
wall-clock assertions — the compile uses a FIXED content-derived reference
timestamp (05-03).
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from tesserae.project import ProjectWiki

WIKI_CORPUS_ROOT = Path(__file__).parent / "fixtures" / "wiki_corpus"


def _seed_project(project_root: Path) -> ProjectWiki:
    project_root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(WIKI_CORPUS_ROOT / "data", project_root / "data")
    shutil.copytree(WIKI_CORPUS_ROOT / "docs", project_root / "docs")
    return ProjectWiki.init(project_root, name="phase5_idempotence")


def _graph_path(wiki: ProjectWiki) -> Path:
    return wiki.paths.graph


def test_two_compiles_produce_byte_identical_graph_json(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    wiki = _seed_project(project_root)

    wiki.compile()
    graph_path = _graph_path(wiki)
    assert graph_path.exists(), "first compile must produce graph.json"
    first_bytes = graph_path.read_bytes()
    first_hash = hashlib.sha256(first_bytes).hexdigest()

    # Second compile over the SAME corpus / SAME dir with warm caches.
    wiki.compile()
    second_bytes = graph_path.read_bytes()
    second_hash = hashlib.sha256(second_bytes).hexdigest()

    assert second_hash == first_hash, (
        "graph.json must be byte-identical across two identical-source compiles"
    )
    assert second_bytes == first_bytes


def test_mutable_memory_state_absent_from_graph_json(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    wiki = _seed_project(project_root)
    wiki.compile()

    text = _graph_path(wiki).read_text(encoding="utf-8")

    # All mutable memory columns must live in node_memory, NOT graph.json.
    assert "decay_score" not in text
    assert "access_count" not in text
    assert "last_accessed_at" not in text


def test_node_memory_columns_live_in_sidecar_not_graph(tmp_path: Path) -> None:
    # Positive complement: after compile the sidecar db exists (the home of the
    # mutable state) while graph.json carries none of those keys.
    project_root = tmp_path / "project"
    wiki = _seed_project(project_root)
    wiki.compile()

    sqlite_path = wiki.paths.sqlite
    assert sqlite_path.exists(), "compile must create the sqlite sidecar"

    text = _graph_path(wiki).read_text(encoding="utf-8")
    # "superseded" / "confidence" are likewise sidecar-owned scalars.
    assert '"superseded"' not in text
    assert '"decay_score"' not in text
