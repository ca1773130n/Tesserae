"""Tests for the colbymchenry/codegraph SQLite adapter (Option C).

Covers the kind/edge mapping table, the line-shift-stable ``id_seed``
contract that keeps feature H's ``discusses`` edges sticking through
refactors, unknown-kind fallback, the missing-DB CLI helper message,
and the atomic-write tmp-suffix invariant. Also exercises a
round-trip into feature H's ``run_insight_symbol_link_pass`` so we
know the adapter's output is shaped the way the resolver expects.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable, Tuple

import pytest

from tesserae.code_graph_adapter import (
    CodeGraphAdapter,
    _default_codegraph_db,
    write_code_graph_from_codegraph,
)
from tesserae.memory.insight_symbol_link import run_insight_symbol_link_pass
from tesserae.research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
)


# ---------------------------------------------------------------------------
# Fixture: build a tiny CodeGraph-shaped SQLite database in tmp_path.
# Mirrors the canonical schema from /tmp/codegraph-research/schema.md
# §1 — every NOT NULL column is populated so we exercise the same
# constraints CodeGraph itself enforces.
# ---------------------------------------------------------------------------


_NODES_SQL = """
CREATE TABLE IF NOT EXISTS nodes (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    qualified_name TEXT NOT NULL,
    file_path TEXT NOT NULL,
    language TEXT NOT NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    start_column INTEGER NOT NULL,
    end_column INTEGER NOT NULL,
    docstring TEXT,
    signature TEXT,
    visibility TEXT,
    is_exported INTEGER DEFAULT 0,
    is_async INTEGER DEFAULT 0,
    is_static INTEGER DEFAULT 0,
    is_abstract INTEGER DEFAULT 0,
    decorators TEXT,
    type_parameters TEXT,
    updated_at INTEGER NOT NULL
);
"""

_EDGES_SQL = """
CREATE TABLE IF NOT EXISTS edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    target TEXT NOT NULL,
    kind TEXT NOT NULL,
    metadata TEXT,
    line INTEGER,
    col INTEGER,
    provenance TEXT DEFAULT NULL,
    FOREIGN KEY (source) REFERENCES nodes(id) ON DELETE CASCADE,
    FOREIGN KEY (target) REFERENCES nodes(id) ON DELETE CASCADE
);
"""

_FILES_SQL = """
CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL,
    language TEXT NOT NULL,
    size INTEGER NOT NULL,
    modified_at INTEGER NOT NULL,
    indexed_at INTEGER NOT NULL,
    node_count INTEGER DEFAULT 0,
    errors TEXT
);
"""


def _make_db(path: Path, *, extra_kind_row: bool = False) -> None:
    """Create a small CodeGraph DB.

    Layout:
      * ``foo.py`` (python) — class ``A`` with method ``A.foo``
      * ``bar.ts`` (typescript) — function ``bar`` that calls ``foo``
      * One import: ``foo.py`` imports ``json`` (mapped as DEPENDENCY)
      * Edges: contains (file→class, class→method, file→function),
        calls (bar→foo), imports (foo.py→json).
    """
    conn = sqlite3.connect(path)
    try:
        conn.executescript(_NODES_SQL + _EDGES_SQL + _FILES_SQL)
        now = 1_700_000_000_000

        node_rows = [
            # (id, kind, name, qn, file, lang, sline, eline)
            ("file:hash_foo", "file", "foo.py", "foo.py", "foo.py", "python", 1, 20),
            ("class:hash_A", "class", "A", "A", "foo.py", "python", 3, 10),
            ("method:hash_A_foo", "method", "foo", "A.foo", "foo.py", "python", 5, 8),
            ("import:hash_json", "import", "json", "json", "foo.py", "python", 1, 1),
            ("file:hash_bar", "file", "bar.ts", "bar.ts", "bar.ts", "typescript", 1, 5),
            ("function:hash_bar", "function", "bar", "bar", "bar.ts", "typescript", 2, 4),
        ]
        if extra_kind_row:
            node_rows.append(
                ("weird:hash_zzz", "weird_kind", "Zzz", "Zzz", "bar.ts", "typescript", 1, 1)
            )

        conn.executemany(
            """
            INSERT INTO nodes (
                id, kind, name, qualified_name, file_path, language,
                start_line, end_line, start_column, end_column,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?)
            """,
            [(*row, now) for row in node_rows],
        )

        conn.executemany(
            "INSERT INTO edges (source, target, kind) VALUES (?, ?, ?)",
            [
                ("file:hash_foo", "class:hash_A", "contains"),
                ("class:hash_A", "method:hash_A_foo", "contains"),
                ("file:hash_foo", "import:hash_json", "imports"),
                ("file:hash_bar", "function:hash_bar", "contains"),
                ("function:hash_bar", "method:hash_A_foo", "calls"),
            ],
        )

        conn.executemany(
            """
            INSERT INTO files (path, content_hash, language, size,
                               modified_at, indexed_at, node_count, errors)
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            [
                ("foo.py", "h1", "python", 100, now, now, 3),
                ("bar.ts", "h2", "typescript", 50, now, now, 2),
            ],
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_read_emits_typed_research_graph(tmp_path: Path) -> None:
    db = tmp_path / "codegraph.db"
    _make_db(db)

    result = CodeGraphAdapter(db, project_root=tmp_path).read()
    graph = result.graph

    by_name: dict[str, ResearchNode] = {n.name: n for n in graph.nodes}

    # CODE_FILE node minted for foo.py.
    assert "foo.py" in by_name
    assert by_name["foo.py"].type == ResearchNodeType.CODE_FILE
    # bar.ts also present (different language, same kind).
    assert by_name["bar.ts"].type == ResearchNodeType.CODE_FILE

    # The class is keyed by its qualified name (A).
    assert by_name["A"].type == ResearchNodeType.CODE_CLASS

    # Methods are keyed by ``Class.method`` — feature H's symbol index
    # leans on this shape for dotted-path resolution.
    assert "A.foo" in by_name
    a_foo = by_name["A.foo"]
    assert a_foo.type == ResearchNodeType.CODE_METHOD
    assert a_foo.metadata["codegraph_kind"] == "method"
    assert a_foo.metadata["language"] == "python"

    # The top-level function from bar.ts.
    bar = by_name["bar"]
    assert bar.type == ResearchNodeType.CODE_FUNCTION

    # Import row collapses onto a DEPENDENCY node, per the mapping.
    assert by_name["json"].type == ResearchNodeType.DEPENDENCY

    # Edges: contains, calls, imports plus the synthesized declared_in.
    edge_types = {e.type for e in graph.edges}
    assert {"contains", "calls", "imports", "declared_in"} <= edge_types

    # Top-line counts reflect the file/language stats.
    assert result.processed_files == 2
    assert result.languages == 2
    assert result.skipped_edges == 0


def test_id_seed_stable_across_line_shifts(tmp_path: Path) -> None:
    db = tmp_path / "codegraph.db"
    _make_db(db)
    before = {n.name: n.id for n in CodeGraphAdapter(db).read().graph.nodes}

    # Bump every node's line numbers — simulates the user pressing Enter
    # at the top of every file. CodeGraph rewrites its row ids (line is
    # in the hash) but Tesserae's id_seed (file/kind/qualified_name)
    # should stay constant.
    conn = sqlite3.connect(db)
    try:
        conn.execute("UPDATE nodes SET start_line = start_line + 42, end_line = end_line + 42")
        conn.commit()
    finally:
        conn.close()

    after = {n.name: n.id for n in CodeGraphAdapter(db).read().graph.nodes}

    # Every name we had before is still present and pinned to the same id.
    assert before.keys() == after.keys()
    for name, node_id in before.items():
        assert after[name] == node_id, f"id_seed for {name!r} drifted after line shift"


def test_unknown_kind_falls_back_to_code_symbol(tmp_path: Path) -> None:
    db = tmp_path / "codegraph.db"
    _make_db(db, extra_kind_row=True)

    result = CodeGraphAdapter(db).read()
    by_name = {n.name: n for n in result.graph.nodes}

    assert "Zzz" in by_name
    zzz = by_name["Zzz"]
    assert zzz.type == ResearchNodeType.CODE_SYMBOL
    # The raw kind survives in metadata so downstream surfaces can
    # still tell what the foreign kind was.
    assert zzz.metadata["codegraph_kind"] == "weird_kind"


def test_missing_db_returns_helpful_error(tmp_path: Path, capsys) -> None:
    db = tmp_path / "nope" / "codegraph.db"
    adapter = CodeGraphAdapter(db, project_root=tmp_path)
    assert adapter.available() is False
    with pytest.raises(FileNotFoundError) as exc:
        adapter.read()
    assert "npx @colbymchenry/codegraph init -i" in str(exc.value)

    # CLI seam: `tesserae project sync-code` must exit nonzero with the
    # same install instructions on the stderr stream.
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "tesserae.cli",
            "project",
            "sync-code",
            "--project",
            str(tmp_path),
            "--db",
            str(db),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 2
    assert "codegraph init -i" in proc.stderr


def test_feature_h_resolves_against_codegraph_output(tmp_path: Path) -> None:
    db = tmp_path / "codegraph.db"
    _make_db(db)
    code_graph_json = tmp_path / ".tesserae" / "code-graph.json"
    write_code_graph_from_codegraph(db, code_graph_json, project_root=tmp_path)

    # Build a tiny session graph with one insight mentioning ``bar``.
    insight = ResearchNode(
        id="SessionInsight:test1",
        name="we should rewrite `bar` to be async",
        type=ResearchNodeType.SESSION_INSIGHT,
        metadata={"session_id": "sess-1"},
    )
    graph = ResearchGraph(nodes=[insight], edges=[])
    run_insight_symbol_link_pass(graph, code_graph_path=code_graph_json)

    discusses = [e for e in graph.edges if e.type == "discusses" and e.source == insight.id]
    assert len(discusses) == 1, f"expected one discusses edge, got: {discusses}"
    # Target must be the CodeFunction:bar node minted by the adapter.
    target_id = discusses[0].target
    # Sanity: that id resolves back to a CodeFunction named "bar".
    payload = json.loads(code_graph_json.read_text(encoding="utf-8"))
    node_index = {n["id"]: n for n in payload["nodes"]}
    target_node = node_index[target_id]
    assert target_node["name"] == "bar"
    assert target_node["type"] == "CodeFunction"


def test_atomic_write_uses_pid_random_suffix(tmp_path: Path) -> None:
    db = tmp_path / "codegraph.db"
    _make_db(db)
    target = tmp_path / ".tesserae" / "code-graph.json"

    write_code_graph_from_codegraph(db, target, project_root=tmp_path)
    write_code_graph_from_codegraph(db, target, project_root=tmp_path)

    # No leftover .tmp files from the atomic-write dance.
    leftovers = list(target.parent.glob("code-graph.json.tmp.*"))
    assert leftovers == []
    assert target.exists()


def test_default_db_path_is_codegraph_subdir(tmp_path: Path) -> None:
    # Sanity-check the helper used by the CLI so tests pin the schema.md §6
    # convention (``.codegraph/codegraph.db``).
    assert _default_codegraph_db(tmp_path) == tmp_path / ".codegraph" / "codegraph.db"
