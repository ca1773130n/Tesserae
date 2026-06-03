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

from tesserae.memory.store import bump_access, read_memory
from tesserae.project import ProjectWiki

WIKI_CORPUS_ROOT = Path(__file__).parent / "fixtures" / "wiki_corpus"

# Memory/sidecar scalars that must NEVER appear in graph.json.
_MEMORY_FIELDS = (
    "decay_score",
    "access_count",
    "last_accessed_at",
    "confidence",
    "superseded",
)


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


def test_compile_after_mcp_read_is_byte_identical(tmp_path: Path) -> None:
    """THE blocker gate: a simulated MCP node read must not leak into graph.json.

    1. Full compile -> capture graph.json bytes + sha256.
    2. Simulate an MCP read by bumping access_count / last_accessed_at in the
       node_memory sidecar DIRECTLY (no MCP server, no now() — a fixed
       wall-clock-shaped timestamp that, if it leaked into node.metadata and
       got serialized, WOULD change graph.json bytes).
    3. Compile AGAIN -> graph.json must be BYTE-IDENTICAL to the first compile.
    4. graph.json must contain NONE of the memory fields.

    Before the fix (which stamped sidecar fields onto node.metadata, where
    ResearchNode.model_dump serializes the whole metadata dict into graph.json)
    step 3 produced different bytes -> this test FAILED. After the fix the
    access state is fed to decay via a copied view only, so bytes are stable.
    """
    project_root = tmp_path / "project"
    wiki = _seed_project(project_root)

    wiki.compile()
    graph_path = _graph_path(wiki)
    first_bytes = graph_path.read_bytes()
    first_hash = hashlib.sha256(first_bytes).hexdigest()

    # Simulate MCP reads bumping access state via the SAME atomic write path
    # the MCP server uses (bump_access). Use a fixed timestamp far in the
    # future so any leak would be glaringly non-idempotent (no now()).
    sqlite_path = wiki.paths.sqlite
    prior = read_memory(sqlite_path)
    assert prior, "first compile must stage node_memory rows"
    future_ts = "2999-12-31T23:59:59+00:00"
    for node_id in prior:
        bump_access(sqlite_path, node_id, future_ts)
        bump_access(sqlite_path, node_id, future_ts)
        bump_access(sqlite_path, node_id, future_ts)

    # Confirm the bump actually landed in the sidecar.
    after_bump = read_memory(sqlite_path)
    assert any(r.access_count >= 3 for r in after_bump.values())
    assert any(r.last_accessed_at == future_ts for r in after_bump.values())

    # Compile AGAIN — the read bump must not change graph.json by a single byte.
    wiki.compile()
    second_bytes = graph_path.read_bytes()
    second_hash = hashlib.sha256(second_bytes).hexdigest()

    assert second_hash == first_hash, (
        "graph.json changed after an MCP read bumped node_memory — sidecar "
        "state is leaking into the graph artifact (byte-idempotence broken)"
    )
    assert second_bytes == first_bytes

    # And no memory field (incl. the leaked future timestamp) is in graph.json.
    text = graph_path.read_text(encoding="utf-8")
    for field_name in _MEMORY_FIELDS:
        assert field_name not in text, f"{field_name} leaked into graph.json"
    assert "2999-12-31T23:59:59" not in text, "leaked bumped last_accessed_at into graph.json"
