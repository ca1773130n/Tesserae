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
import json
import shutil
from pathlib import Path
from typing import Any, Iterator, List, Optional, Union

from tesserae.memory.store import bump_access, read_memory
from tesserae.project import ProjectWiki, SessionExtractionOptions
from tesserae.ports import Source

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


# ---------------------------------------------------------------------------
# Sessions-present byte-idempotence guard (the prior blind spot).
#
# The 05/06 byte-idempotence guard compiled a corpus with NO harness sessions,
# so it never exercised the session extraction path — where session finding /
# decision nodes used to stamp ``access_count`` / ``last_accessed_at`` into
# node.metadata, which ``ResearchNode.model_dump`` serialized into graph.json.
# Because the Phase-2 daemon drives compiles with LIVE sessions, that leaked
# wall-clock memory state into graph.json and broke byte-idempotence on the
# default path. These tests seed a session and prove graph.json is BYTE-IDENTICAL
# across two compiles AND carries no memory fields.
# ---------------------------------------------------------------------------


def _seed_session(wiki: ProjectWiki) -> None:
    """Seed one harness session via the live SQLite store (the daemon's path)."""
    from tesserae.harness_sessions import HarnessSession
    from tesserae.harness_sessions_db import HarnessSessionsDB

    session = HarnessSession(
        id="phase5-byte-session-001",
        slug="phase5-byte-session",
        harness="claude-code",
        agent_label="Claude Code",
        project_name="phase5_idempotence",
        project_root=str(wiki.project_root.resolve()),
        started_at="2026-05-19T10:00:00Z",
        ended_at="2026-05-19T11:00:00Z",
        title="byte-idempotence session",
        decisions=[
            "Use atomic writes with a PID plus random tmp suffix",
            "Use atomic writes need PID plus a random suffix for tmp",
        ],
    )
    db_path = wiki.project_root / ".tesserae" / "harness_sessions.db"
    HarnessSessionsDB(db_path).upsert(session)


def test_sessions_present_compile_is_byte_identical(tmp_path: Path) -> None:
    """BLOCKER guard: a compile WITH a live session present must be byte-stable.

    Fails before the session-node fix (which stamped access_count /
    last_accessed_at into node.metadata, serialized into graph.json) and passes
    after. Pin ``llm_enabled="false"`` so the STRUCTURAL session pass runs
    regardless of any signed-in CLI on the dev box.
    """
    project_root = tmp_path / "project"
    wiki = _seed_project(project_root)
    _seed_session(wiki)
    opts = SessionExtractionOptions(enabled=True, llm_enabled="false")

    wiki.compile(session_options=opts, vault_pull=False)
    graph_path = _graph_path(wiki)
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    session_nodes = [n for n in graph["nodes"] if n["type"] == "Session"]
    assert session_nodes, "the seeded session must produce a Session node"
    decision_nodes = [n for n in graph["nodes"] if n["type"] == "SessionDecision"]
    assert decision_nodes, "the seeded decisions must produce SessionDecision nodes"

    first_bytes = graph_path.read_bytes()
    first_hash = hashlib.sha256(first_bytes).hexdigest()

    # Simulate the daemon scenario: an MCP read bumps the session nodes'
    # access state in the sidecar between compiles. If the session pass stamps
    # access_count / last_accessed_at onto node.metadata (the BLOCKER bug), the
    # next compile would re-derive different bytes. Use a fixed future ts so any
    # leak is glaring (no now()).
    sqlite_path = wiki.paths.sqlite
    prior = read_memory(sqlite_path)
    assert prior, "first compile must stage node_memory rows for the session"
    future_ts = "2999-12-31T23:59:59+00:00"
    for node_id in prior:
        bump_access(sqlite_path, node_id, future_ts)
        bump_access(sqlite_path, node_id, future_ts)

    wiki.compile(session_options=opts, vault_pull=False)
    second_bytes = graph_path.read_bytes()
    second_hash = hashlib.sha256(second_bytes).hexdigest()

    assert second_hash == first_hash, (
        "graph.json must be byte-identical across two compiles WITH a session "
        "present — session-node memory state is leaking into the graph artifact"
    )
    assert second_bytes == first_bytes
    # And the leaked bumped timestamp must never appear in the graph artifact.
    assert "2999-12-31T23:59:59" not in graph_path.read_text(encoding="utf-8")


def test_sessions_present_graph_json_has_no_memory_fields(tmp_path: Path) -> None:
    """With a session present, graph.json must carry NONE of the memory fields."""
    project_root = tmp_path / "project"
    wiki = _seed_project(project_root)
    _seed_session(wiki)
    wiki.compile(
        session_options=SessionExtractionOptions(enabled=True, llm_enabled="false"),
        vault_pull=False,
    )

    text = _graph_path(wiki).read_text(encoding="utf-8")
    for field_name in _MEMORY_FIELDS:
        assert field_name not in text, (
            f"{field_name} leaked into graph.json from the session pass"
        )
    # ``first_seen_at`` (deterministic, derived from the session's own
    # started_at) IS allowed — it is byte-stable and drives decay.
    assert "first_seen_at" in text, (
        "the deterministic decay anchor should still be present on session nodes"
    )


# ---------------------------------------------------------------------------
# LLM-pass gating consistency (KB-03 / KB-04): supersede and contradiction must
# run TOGETHER (gate on) or NOT AT ALL (gate off, the default) — never one
# without the other. The two graph-mutating LLM passes share ONE compile-level
# client.
# ---------------------------------------------------------------------------


class _RecordingClient:
    """LLMJsonClient stub: routes scripted responses by ``cache_key`` and
    records which passes invoked it.

    supersede uses ``cache_key="supersede-v1"``; contradiction uses
    ``cache_key="contradiction-v1"``. Recording both lets a test assert BOTH
    passes ran on the same compile with the SAME client.
    """

    def __init__(self) -> None:
        self.cache_keys: List[str] = []

    def complete_json(self, **kwargs: Any) -> Optional[Union[dict, list]]:
        cache_key = str(kwargs.get("cache_key") or "")
        self.cache_keys.append(cache_key)
        if cache_key == "supersede-v1":
            return {"verdict": "a_obsoletes_b", "rationale": "sharper wording."}
        if cache_key == "contradiction-v1":
            return {
                "winner_id": "PerformanceClaim:a",
                "loser_id": "PerformanceClaim:b",
                "rationale": "A used the standard split.",
            }
        return None


def _claim_graph():
    """A graph slice with a single contradicting PerformanceClaim pair."""
    from tesserae.research_graph import (
        ResearchGraph,
        ResearchNode,
        ResearchNodeType,
    )

    a = ResearchNode(
        id="PerformanceClaim:a",
        name="Model X beats Y on GLUE",
        type=ResearchNodeType.PERFORMANCE_CLAIM,
        description="Model X outperforms Model Y on the GLUE benchmark.",
        source_path="docs/paper-a.md",
    )
    b = ResearchNode(
        id="PerformanceClaim:b",
        name="Model X loses to Y on GLUE",
        type=ResearchNodeType.PERFORMANCE_CLAIM,
        description="Model X is outperformed by Model Y on the GLUE benchmark.",
        source_path="docs/paper-b.md",
    )
    return ResearchGraph(nodes=[a, b], edges=[])


class _ContradictionDocExtractor:
    """``doc_extractor`` stub: emits a contradicting PerformanceClaim pair once.

    Returns the claim slice for the first source it sees and an empty graph
    thereafter, so the final compiled graph carries exactly one contradiction
    candidate pair for the contradiction pass to arbitrate.
    """

    def __init__(self) -> None:
        self._emitted = False

    def extract_text(self, content: str, source_path: str = "", source_kind: str = "") -> Any:  # noqa: D401
        from tesserae.research_graph import ResearchGraph

        if self._emitted:
            return ResearchGraph(nodes=[], edges=[])
        self._emitted = True
        return _claim_graph()


class _OneSourceLoader:
    """Minimal :class:`SourceLoader` yielding a single in-memory source."""

    def __init__(self, content: str) -> None:
        self._source = Source(id="src-1", path="docs/paper-a.md", content=content)

    def discover(self) -> Iterator[Source]:
        yield self._source

    def fetch(self, source_id: str) -> Source:
        return self._source


def _seed_supersede_session(wiki: ProjectWiki) -> None:
    """Seed a session whose two near-duplicate decisions form a supersede pair."""
    from tesserae.harness_sessions import HarnessSession
    from tesserae.harness_sessions_db import HarnessSessionsDB

    session = HarnessSession(
        id="phase5-gate-session-001",
        slug="phase5-gate-session",
        harness="claude-code",
        agent_label="Claude Code",
        project_name="phase5_idempotence",
        project_root=str(wiki.project_root.resolve()),
        started_at="2026-05-19T10:00:00Z",
        ended_at="2026-05-19T11:00:00Z",
        decisions=[
            "Atomic writes need a PID plus random tmp suffix",
            "Atomic writes need PID plus random suffix for tmp",
        ],
    )
    db_path = wiki.project_root / ".tesserae" / "harness_sessions.db"
    HarnessSessionsDB(db_path).upsert(session)


def _compile_with_claims(
    wiki: ProjectWiki, *, llm_passes_client=None
) -> dict:
    """Compile with the contradiction doc-extractor + supersede session wired in."""
    _seed_supersede_session(wiki)
    wiki.ingest(
        ["docs"],
        loader=_OneSourceLoader("# Paper A\n\nModel X outperforms Model Y on GLUE.\n"),
        doc_extractor=_ContradictionDocExtractor(),
        session_options=SessionExtractionOptions(enabled=True, llm_enabled="false"),
        vault_pull=False,
        llm_passes_client=llm_passes_client,
    )
    return json.loads(_graph_path(wiki).read_text(encoding="utf-8"))


def test_default_compile_runs_supersede_but_not_contradiction(tmp_path: Path) -> None:
    """Default path (Phase 5.1): supersede runs default-on, contradiction stays gated.

    As of Plan 05.1-01/02 the supersede pass is DEFAULT-ON with a deterministic,
    credential-free verdict, so a default compile (no client, gate unset) DOES
    mint ``supersedes`` edges. The contradiction pass remains gated on an LLM
    client, so ``resolved_by`` must still be absent. (The contradiction candidate
    pair IS present in the graph — proven by the gate-ON test below.)
    """
    project_root = tmp_path / "project"
    wiki = _seed_project(project_root)
    graph = _compile_with_claims(wiki)

    edge_types = {e["type"] for e in graph["edges"]}
    assert "supersedes" in edge_types, "supersede should run default-on (Phase 5.1)"
    assert "resolved_by" not in edge_types, "contradiction ran without an LLM client"

    # The contradiction candidate pair must be present (so the gate, not a
    # missing pair, is what suppressed the resolved_by edge).
    claim_ids = {n["id"] for n in graph["nodes"] if n["type"] == "PerformanceClaim"}
    assert {"PerformanceClaim:a", "PerformanceClaim:b"} <= claim_ids


def test_llm_passes_gate_runs_both_supersede_and_contradiction(
    tmp_path: Path,
) -> None:
    """Gate ON (explicit client): BOTH supersede AND contradiction run together.

    One ``_RecordingClient`` is threaded into the SINGLE compile-level gate, so
    the same client drives both passes. We assert the client was invoked for
    BOTH cache keys and that both edge types landed in graph.json.
    """
    project_root = tmp_path / "project"
    wiki = _seed_project(project_root)
    client = _RecordingClient()
    graph = _compile_with_claims(wiki, llm_passes_client=client)

    assert "supersede-v1" in client.cache_keys, "supersede pass did not run"
    assert "contradiction-v1" in client.cache_keys, "contradiction pass did not run"

    edge_types = {e["type"] for e in graph["edges"]}
    assert "supersedes" in edge_types, "supersede edge missing with gate ON"
    assert "resolved_by" in edge_types, "contradiction edge missing with gate ON"
