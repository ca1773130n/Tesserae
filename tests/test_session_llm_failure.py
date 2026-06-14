"""Session extraction must not silently cache a FAILED LLM call.

When the LLM backend is reachable-but-failing (rate limit / auth / wrong model)
``complete_json`` returns ``None``. Previously the extractor cached that as
"0 findings", so an outage got baked in and every later compile reused zeros.
Now: a failed chunk is NOT cached (so it re-extracts once the backend works),
the failure is counted, and a loud warning is printed. A genuinely *empty but
successful* response IS still cached.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from tesserae.harness_sessions import HarnessSession
from tesserae.research_graph import ResearchGraph, ResearchNode, ResearchNodeType
from tesserae.session_graph import SessionGraphExtractor
from tesserae.session_graph_llm import extract_with_llm


class _NoneClient:
    """A reachable client whose calls always fail (return None)."""

    def __init__(self):
        self.calls = 0

    def complete_json(self, **kwargs):
        self.calls += 1
        return None


class _EmptyOkClient:
    """A working client that legitimately returns zero findings."""

    def complete_json(self, **kwargs):
        return {"findings": []}


def _session(sid: str = "s1", turns: int = 3) -> HarnessSession:
    return HarnessSession(
        id=sid, slug=sid, harness="claude-code", agent_label="Claude Code",
        project_name="test", project_root="/tmp/test",
        started_at="2026-06-14T10:00:00Z", title="T",
        metadata={"turns": [{"role": "user", "text": f"q{i}"} for i in range(turns)]},
    )


def _doc_graph() -> ResearchGraph:
    return ResearchGraph(nodes=[], edges=[])


def _make_extractor(tmp_path: Path, client) -> SessionGraphExtractor:
    root = tmp_path / "project"
    root.mkdir(parents=True, exist_ok=True)
    sess = HarnessSession.from_dict({**_session().to_dict(), "project_root": str(root.resolve())})
    cache_dir = root / ".tesserae" / "session_findings"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return SessionGraphExtractor(
        project_root=root.resolve(), cache_dir=cache_dir,
        doc_graph=_doc_graph(), sessions=[sess], json_client=client,
    )


def test_extract_with_llm_records_failure_in_stats():
    stats: dict = {}
    out = extract_with_llm(
        _session(), [{"role": "user", "text": "hi"}], [], _NoneClient(), stats=stats,
    )
    assert out == []
    assert stats == {"calls": 1, "failed": 1}


def test_successful_empty_response_is_not_a_failure():
    stats: dict = {}
    extract_with_llm(
        _session(), [{"role": "user", "text": "hi"}], [], _EmptyOkClient(), stats=stats,
    )
    assert stats.get("failed", 0) == 0
    assert stats.get("calls") == 1


def test_failed_chunk_is_not_cached_and_warns(tmp_path: Path, capsys):
    ex = _make_extractor(tmp_path, _NoneClient())
    ex.extract()
    chunk_files = list((tmp_path / "project" / ".tesserae" / "session_findings").rglob("chunk-*.json"))
    assert chunk_files == [], "a FAILED LLM call must NOT write a chunk cache"
    assert ex._llm_failed > 0 and ex._llm_calls > 0
    out = capsys.readouterr().out
    assert "session extraction" in out and "FAILED" in out


def test_successful_empty_chunk_is_cached(tmp_path: Path, capsys):
    ex = _make_extractor(tmp_path, _EmptyOkClient())
    ex.extract()
    chunk_files = list((tmp_path / "project" / ".tesserae" / "session_findings").rglob("chunk-*.json"))
    assert chunk_files, "a successful (empty) response SHOULD be cached"
    assert ex._llm_failed == 0
    assert "FAILED" not in capsys.readouterr().out
