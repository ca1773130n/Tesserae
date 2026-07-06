"""Live session views for ``tesserae serve`` — read the harness roots directly
(recent-window bounded) so the served page never lags the way the compiled
projection can. Reuses the activity-summary discovery + parsing.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Sequence, Tuple

from tesserae.activity_summary import (
    KST,
    Window,
    in_window,
    iter_project_transcripts,
    parse_ts,
)
from tesserae.harness_sessions import _claude_turns, _codex_turns, _parse_jsonl


def _recent_window(days: int) -> Window:
    """A ``[now - days, now)`` window in KST. Only files touched inside it are
    scanned (the ``mtime`` prune in :func:`iter_project_transcripts`), which keeps
    a full-root scan fast — old, immutable sessions stay in the index/graph."""
    now = datetime.now(KST)
    return Window(now - timedelta(days=max(1, days)), now, "recent")


def _turns_for(harness: str, rows, max_turns: int):
    if harness == "codex":
        return _codex_turns(rows, limit=max_turns)
    return _claude_turns(rows, limit=max_turns)


def live_session_list(
    projects: Sequence[Tuple[str, object]], *, days: int = 30, max_turns: int = 100_000
) -> List[dict]:
    """Current sessions touched in the last ``days``, scanned live from the roots.

    One dict per session: ``session_id, project, harness, account, turns,
    first_ts, last_ts, preview`` (preview = the first user turn, ≤160 chars).
    Newest-active first.
    """
    window = _recent_window(days)
    out: List[dict] = []
    for name, harness, path, key in iter_project_transcripts(projects, [window]):
        rows = _parse_jsonl(path)
        stamped = []
        for turn in _turns_for(harness, rows, max_turns):
            ts = parse_ts(str(turn.get("timestamp") or ""))
            if ts and in_window(ts, window):
                stamped.append((ts, turn))
        if not stamped:
            continue
        stamped.sort(key=lambda pair: pair[0])
        preview = next(
            (str(t.get("text") or "")[:160] for _ts, t in stamped if t.get("role") == "user"),
            "",
        )
        out.append(
            {
                "session_id": key,
                "project": name,
                "harness": harness,
                "account": key.split(":", 1)[0],
                "turns": len(stamped),
                "first_ts": stamped[0][0].isoformat(),
                "last_ts": stamped[-1][0].isoformat(),
                "preview": preview,
            }
        )
    out.sort(key=lambda s: s["last_ts"], reverse=True)
    return out


def live_transcript_search(
    query: str,
    projects: Sequence[Tuple[str, object]],
    *,
    days: int = 30,
    max_turns: int = 100_000,
    limit: int = 20,
) -> List[dict]:
    """Turns in the last ``days`` whose text contains ``query`` (casefold).

    Each hit: ``session_id, project, harness, ts, role, text`` (text ≤240 chars),
    newest first, capped to ``limit``. Empty query → ``[]``.
    """
    q = (query or "").casefold()
    if not q:
        return []
    window = _recent_window(days)
    hits: List[Tuple[datetime, dict]] = []
    for name, harness, path, key in iter_project_transcripts(projects, [window]):
        rows = _parse_jsonl(path)
        for turn in _turns_for(harness, rows, max_turns):
            text = str(turn.get("text") or "")
            ts = parse_ts(str(turn.get("timestamp") or ""))
            if ts and in_window(ts, window) and q in text.casefold():
                hits.append(
                    (
                        ts,
                        {
                            "session_id": key,
                            "project": name,
                            "harness": harness,
                            "ts": ts.isoformat(),
                            "role": str(turn.get("role") or ""),
                            "text": text[:240],
                        },
                    )
                )
    hits.sort(key=lambda pair: pair[0], reverse=True)
    return [hit for _ts, hit in hits[: max(1, int(limit))]]
