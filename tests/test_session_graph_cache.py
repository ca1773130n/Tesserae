"""Tests for the SessionGraphExtractor orchestrator's caching layer.

Caching guarantees from the design:
1. Same content_hash + same project_root_hash → no LLM call.
2. Different content_hash → cache miss → new LLM call.
3. Different project_root_hash (cache file copied between projects) →
   cache rejected; re-extraction.
4. Stale cache files (sessions removed from disk) are pruned.
5. Schema-version mismatch → cache rejected.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, List, Optional, Union

import pytest

from tesserae.harness_sessions import HarnessSession
from tesserae.research_graph import ResearchGraph, ResearchNode, ResearchNodeType
from tesserae.session_graph import (
    CACHE_SCHEMA_VERSION,
    SessionGraphExtractor,
    _project_root_hash,
    _session_content_hash,
)


class _ScriptedClient:
    """Tracks how many times complete_json was called."""

    def __init__(self, responses: List[Optional[Union[dict, list]]]):
        self._responses = list(responses)
        self.calls: int = 0

    def complete_json(self, **kwargs):
        self.calls += 1
        if not self._responses:
            return None
        return self._responses.pop(0)


def _session(*, id: str = "sess-1", title: str = "T", turns: int = 3) -> HarnessSession:
    return HarnessSession(
        id=id,
        slug=id,
        harness="claude-code",
        agent_label="Claude Code",
        project_name="test",
        project_root="/tmp/test",
        started_at="2026-05-19T10:00:00Z",
        title=title,
        metadata={
            "turns": [
                {"role": "user", "text": f"q{i}"} for i in range(turns)
            ],
        },
    )


def _doc_graph() -> ResearchGraph:
    return ResearchGraph(
        nodes=[
            ResearchNode(
                id="Paper:foo",
                name="Foo Paper",
                type=ResearchNodeType.PAPER,
                source_path="docs/foo.md",
            ),
        ],
        edges=[],
    )


def _scripted_finding_response():
    return {
        "findings": [
            {
                "kind": "insight",
                "body": "Cache hits skip the LLM call",
                "turn_ids": [1],
                "references": ["Paper:foo"],
            }
        ]
    }


def _make_extractor(
    tmp_path: Path,
    sessions: List[HarnessSession],
    *,
    client: Optional[_ScriptedClient] = None,
    project_root: Optional[Path] = None,
) -> SessionGraphExtractor:
    root = project_root or (tmp_path / "project")
    root.mkdir(parents=True, exist_ok=True)
    # Make each test session's project_root match `root` so the
    # session_matches_project filter accepts it.
    fixed_sessions = [
        HarnessSession.from_dict({**s.to_dict(), "project_root": str(root.resolve())})
        for s in sessions
    ]
    cache_dir = root / ".tesserae" / "session_findings"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return SessionGraphExtractor(
        project_root=root.resolve(),
        cache_dir=cache_dir,
        doc_graph=_doc_graph(),
        sessions=fixed_sessions,
        json_client=client,
    )


def _mutate_turns(session: HarnessSession, turns: List[dict]) -> HarnessSession:
    d = session.to_dict()
    d["metadata"] = {**(d.get("metadata") or {}), "turns": turns}
    return HarnessSession.from_dict(d)


def test_cache_hit_skips_llm_call(tmp_path: Path):
    # Per-turn cache (v2): a cold 3-turn extract calls the LLM once per turn.
    session = _session(turns=3)
    client = _ScriptedClient([_scripted_finding_response() for _ in range(3)])
    extractor = _make_extractor(tmp_path, [session], client=client)

    # First call → all 3 turns miss → 3 LLM calls.
    extractor.extract()
    assert client.calls == 3

    # Second call (fresh extractor instance, same content) → all turns hit.
    client2 = _ScriptedClient([_scripted_finding_response() for _ in range(3)])
    extractor2 = _make_extractor(
        tmp_path, [session], client=client2,
        project_root=extractor.project_root,
    )
    extractor2.extract()
    assert client2.calls == 0, "second extract should hit the per-turn cache"


def test_content_hash_change_invalidates_cache(tmp_path: Path):
    # Changing a TURN's text (not just title) invalidates that turn's cache.
    session = _session(turns=3)
    client = _ScriptedClient([_scripted_finding_response() for _ in range(3)])
    extractor = _make_extractor(tmp_path, [session], client=client)
    extractor.extract()
    assert client.calls == 3

    # Mutate one turn's text → exactly one turn re-extracts (content-keyed).
    new_turns = [{"role": "user", "text": f"q{i}"} for i in range(3)]
    new_turns[1] = {"role": "user", "text": "CHANGED"}
    changed = _mutate_turns(session, new_turns)
    client2 = _ScriptedClient([_scripted_finding_response() for _ in range(3)])
    extractor2 = _make_extractor(
        tmp_path, [changed], client=client2,
        project_root=extractor.project_root,
    )
    extractor2.extract()
    assert client2.calls == 1, "only the mutated turn must re-extract"


def test_project_root_hash_mismatch_rejects_cache(tmp_path: Path):
    """Cache file copied from another project must not be replayed."""
    session = _session()
    project_a = tmp_path / "project-a"
    project_b = tmp_path / "project-b"

    client_a = _ScriptedClient([_scripted_finding_response() for _ in range(3)])
    extractor_a = _make_extractor(
        tmp_path, [session], client=client_a, project_root=project_a
    )
    extractor_a.extract()
    assert client_a.calls == 3

    # Simulate someone copying project-a's per-session cache DIR into
    # project-b's cache dir (e.g. by `cp -R` of the .tesserae/ dir).
    import shutil

    src_dir = next((project_a / ".tesserae/session_findings").iterdir())
    target_cache = project_b / ".tesserae/session_findings"
    target_cache.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src_dir, target_cache / src_dir.name)

    # Now run extractor in project-b. The cached project_root_hash points
    # at project-a, so every turn must reject the cache and re-extract.
    client_b = _ScriptedClient([_scripted_finding_response() for _ in range(3)])
    extractor_b = _make_extractor(
        tmp_path, [session], client=client_b, project_root=project_b
    )
    extractor_b.extract()
    assert client_b.calls == 3, (
        "cache from a different project_root must be rejected (no replay)"
    )


def test_stale_cache_pruned_on_extract(tmp_path: Path):
    """Cache files for sessions that no longer exist on disk are removed."""
    session = _session(id="sess-current")
    extractor = _make_extractor(tmp_path, [session], client=_ScriptedClient([]))

    # Plant a stale per-session cache DIR from a long-gone session id.
    stale_dir = extractor.cache_dir / "sess-old"
    stale_dir.mkdir(parents=True, exist_ok=True)
    stale_path = stale_dir / "turn-0.json"
    stale_path.write_text(json.dumps({"schema_version": 2}), encoding="utf-8")
    assert stale_path.exists()

    extractor.extract()
    assert not stale_dir.exists(), "stale cache dir should be pruned"


def test_no_client_returns_structural_only(tmp_path: Path):
    """With json_client=None, only the structural slice survives."""
    session = _session()
    extractor = _make_extractor(tmp_path, [session], client=None)
    graph = extractor.extract()
    # Structural pass mints one Session node (no decisions in this fixture).
    session_nodes = [n for n in graph.nodes if n.type == ResearchNodeType.SESSION]
    finding_nodes = [
        n for n in graph.nodes
        if n.type.value.startswith("Session") and n.type != ResearchNodeType.SESSION
    ]
    assert len(session_nodes) == 1
    assert finding_nodes == [], "no LLM client → no finding nodes"


def test_session_content_hash_includes_all_fields(tmp_path: Path):
    """Two sessions identical except for `title` must produce different hashes."""
    a = _session(title="T1")
    b = _session(title="T2")
    assert _session_content_hash(a) != _session_content_hash(b)


def test_project_root_hash_is_resolve_stable(tmp_path: Path):
    """Project root hashes are normalized through Path.resolve()."""
    project = tmp_path / "p"
    project.mkdir()
    # `project` and `project/.` resolve to the same path.
    assert _project_root_hash(project) == _project_root_hash(project / ".")
