"""KB-01/KB-02: node_memory sidecar persistence roundtrip + atomic access bump.

Deterministic by construction — every timestamp is an injected fixed ISO
string, never ``datetime.now()``. All mutable memory state (decay_score,
access_count, last_accessed_at, confidence, superseded) lives in the
``node_memory`` table of ``.tesserae/sqlite.db`` and NEVER in graph.json.

Covers:
  * write_memory -> read_memory exact roundtrip (decay/access/confidence/superseded).
  * bump_access called N times -> read_memory shows access_count == N (atomic).
  * a compile-owned write preserves the MCP-accumulated access_count
    (write_node_memory_many must not clobber it on conflict).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tesserae.memory.store import (
    NodeMemoryRow,
    bump_access,
    read_memory,
    write_memory,
)

# Fixed reference instant reused across the suite — NO wall-clock.
_T0 = "2026-05-21T12:00:00+00:00"
_T1 = "2026-05-21T13:00:00+00:00"


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "sqlite.db"


def test_write_then_read_roundtrip_is_exact(db: Path) -> None:
    rows = [
        NodeMemoryRow(
            node_id="SessionInsight:n1",
            decay_score=0.5,
            access_count=3,
            last_accessed_at=_T0,
            confidence="high",
            superseded=True,
            updated_at=_T0,
        ),
        NodeMemoryRow(
            node_id="SessionInsight:n2",
            decay_score=0.125,
            access_count=0,
            last_accessed_at=None,
            confidence="low",
            superseded=False,
            updated_at=_T0,
        ),
    ]
    write_memory(db, rows)

    out = read_memory(db)
    assert set(out) == {"SessionInsight:n1", "SessionInsight:n2"}

    n1 = out["SessionInsight:n1"]
    assert n1.decay_score == pytest.approx(0.5)
    assert n1.access_count == 3
    assert n1.last_accessed_at == _T0
    assert n1.confidence == "high"
    assert n1.superseded is True

    n2 = out["SessionInsight:n2"]
    assert n2.decay_score == pytest.approx(0.125)
    assert n2.access_count == 0
    assert n2.last_accessed_at is None
    assert n2.confidence == "low"
    assert n2.superseded is False


def test_bump_access_is_atomic_increment(db: Path) -> None:
    # Three reads of the same node => access_count == 3, no read-modify-write.
    for _ in range(3):
        bump_access(db, "SessionInsight:n1", _T1)

    out = read_memory(db)
    assert out["SessionInsight:n1"].access_count == 3
    # The accessor stamps the supplied (fixed) timestamp.
    assert out["SessionInsight:n1"].last_accessed_at == _T1


def test_compile_write_preserves_mcp_access_count(db: Path) -> None:
    # 1. MCP reads bump access_count to 2 (last_accessed_at = _T1).
    bump_access(db, "SessionInsight:n1", _T1)
    bump_access(db, "SessionInsight:n1", _T1)
    assert read_memory(db)["SessionInsight:n1"].access_count == 2

    # 2. A later compile writes the COMPILE-owned columns for the same node.
    #    access_count on the row is the staging value (0), but the store must
    #    NOT clobber the MCP-accumulated count of 2 on conflict.
    write_memory(
        db,
        [
            NodeMemoryRow(
                node_id="SessionInsight:n1",
                decay_score=0.42,
                access_count=0,
                confidence="high",
                superseded=False,
                updated_at=_T0,
            )
        ],
    )

    row = read_memory(db)["SessionInsight:n1"]
    # Compile-owned columns updated...
    assert row.decay_score == pytest.approx(0.42)
    assert row.confidence == "high"
    # ...but MCP-owned access state PRESERVED (anti-clobber, KB-02).
    assert row.access_count == 2
    assert row.last_accessed_at == _T1
