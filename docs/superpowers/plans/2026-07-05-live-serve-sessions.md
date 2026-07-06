# Live Serve Sessions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Make the `tesserae serve` page's session data current by reading the harness roots live — a new `/api/sessions` endpoint and a live `/api/transcript-search` (merged with the index), both backed by a bounded recent-window root scan — plus a `--max-turns` cap on `summary`/`decisions` and `max_turns` on the live endpoints.

**Architecture:** A shared `tesserae/live_sessions.py` reuses `activity_summary.iter_project_transcripts` (with a recent-window `mtime` floor, so scans stay fast) to list current sessions and search recent turns live. `serve.py` exposes these as origin-gated JSON endpoints; the Sessions page fetches `/api/sessions` and renders live, falling back to the compiled content on static hosting. The typed knowledge graph is unchanged.

**Tech Stack:** Python 3 stdlib, `http.server` (existing serve), pytest; JS `fetch` in the generated site. Reuses existing modules; no new deps.

## Global Constraints

- **Live scans are recent-window bounded** (`days`, default 30) — the `mtime >= window.start` prune keeps a full-root scan fast; old immutable sessions stay served from the index/compiled graph.
- **Origin-gated:** `/api/sessions` and `/api/transcript-search` reuse the existing clip-origin gate (`self._clip_origin()`), never emit `Access-Control-Allow-Origin`, and reject cross-site browsers — they expose local history.
- **Graceful fallback:** the Sessions page must render its compiled content when `/api/sessions` is absent (static gh-pages, no server).
- **`--max-turns` maps to `scan_messages(turn_limit=...)`**; default is the current effectively-unbounded `100_000`.
- Reuse: `iter_project_transcripts`, `parse_ts`, `in_window`, `Window`, `_claude_turns`, `_codex_turns`, `_parse_jsonl`.
- One commit per task; conventional messages.

---

## File Structure

- **Modify** `tesserae/cli.py` — `--max-turns` on the `summary` + `decisions` parsers/handlers → `scan_messages(turn_limit=...)`.
- **Modify** `tesserae/activity_summary.py` — thread `turn_limit` from `build_summary` into `scan_messages`; `tesserae/decisions.py` — thread it into `gather_decisions`.
- **Create** `tesserae/live_sessions.py` — `live_session_list`, `live_transcript_search` (+ `_recent_window`).
- **Modify** `tesserae/serve.py` — `/api/sessions` in both handlers; repoint `_run_transcript_search` to merge live + index; parse `days`/`max_turns`.
- **Modify** `tesserae/site/pages.py` (+ `js.py` if needed) — Sessions page fetches `/api/sessions`, renders live with fallback.
- **Create** `tests/test_live_sessions.py`; extend `tests/test_decisions.py` / a CLI test for `--max-turns`.

---

### Task 1: `--max-turns` on `summary` + `decisions`

**Files:** Modify `tesserae/cli.py`, `tesserae/activity_summary.py`, `tesserae/decisions.py`; Test `tests/test_live_sessions.py`

**Interfaces:**
- `build_summary(windows, project_names=None, *, synthesize=True, write=True, turn_limit=100_000)` — passes `turn_limit` to its `scan_messages` call.
- `gather_decisions(windows, project_names=None, *, include_agent=True, turn_limit=100_000)` — passes `turn_limit` to its `scan_messages` call (human parse is unaffected — it reads raw rows).
- CLI: `summary`/`decisions` gain `--max-turns N` (int, default None → unbounded); `_handle_summary`/`_handle_decisions` map `turn_limit = args.max_turns or 100_000`.

- [ ] **Step 1: Write the failing test** (`tests/test_live_sessions.py`)

```python
def test_scan_messages_respects_turn_limit(tmp_path, monkeypatch):
    import os, json
    from datetime import datetime, timezone
    import tesserae.activity_summary as A
    from tesserae.activity_summary import scan_messages, resolve_windows
    from tesserae.harness_sessions import _claude_project_dir
    proj = tmp_path / "proj"; proj.mkdir()
    root = tmp_path / ".claude-acct"
    d = root / "projects" / _claude_project_dir(proj); d.mkdir(parents=True)
    rows = [{"type": "user", "timestamp": f"2026-07-04T10:00:0{i}Z",
             "message": {"role": "user", "content": [{"type": "text", "text": f"t{i}"}]}}
            for i in range(5)]
    tx = d / "s.jsonl"; tx.write_text("\n".join(json.dumps(r) for r in rows))
    stamp = datetime(2026, 7, 4, 10, tzinfo=timezone.utc).timestamp(); os.utime(tx, (stamp, stamp))
    monkeypatch.setattr(A, "discover_harness_roots", lambda: [root])
    monkeypatch.setattr(A, "_root_supports_claude", lambda r: True)
    monkeypatch.setattr(A, "_root_supports_codex", lambda r: False)
    (w,) = resolve_windows(day="2026-07-04", tz=timezone.utc)
    out = scan_messages([("proj", proj)], [w], turn_limit=2)
    assert len(out["proj"]["2026-07-04"]) == 2   # capped
```

- [ ] **Step 2: Run to verify it fails** — `scan_messages` already takes `turn_limit`, but the cap is applied AFTER window filtering today. Verify current behaviour: it may already pass — if so, this test just locks it in; the real change is threading the CLI flag. Run: `.venv/bin/python -m pytest tests/test_live_sessions.py::test_scan_messages_respects_turn_limit -v`.

- [ ] **Step 3: Implement** — `scan_messages` already takes `turn_limit` and passes it to `_claude_turns/_codex_turns` (the parser cap), which bounds the parsed turns before windowing — so `turn_limit=2` yields ≤2 turns. Confirm this holds; if the cap is only on the parser (pre-window) the test asserts the pre-window slice. Then thread the flag: add `turn_limit` param to `build_summary` and `gather_decisions`, forwarding to their `scan_messages(...)` calls; add `--max-turns` (`type=int, default=None`) to `_build_summary_parser` and `_build_decisions_parser`; in `_handle_summary`/`_handle_decisions` compute `turn_limit = args.max_turns or 100_000` and pass it.

- [ ] **Step 4: Run** — `.venv/bin/python -m pytest tests/test_live_sessions.py tests/test_cli_summary.py tests/test_decisions.py -q` → PASS.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(cli): --max-turns on summary + decisions"`

### Task 2: `tesserae/live_sessions.py` — live session list + transcript search

**Files:** Create `tesserae/live_sessions.py`; Test `tests/test_live_sessions.py`

**Interfaces:**
- `_recent_window(days: int) -> Window` — `Window(now-days, now, "recent")` in KST (reuse `activity_summary.KST` / `_local_tz`).
- `live_session_list(projects: Sequence[Tuple[str, object]], *, days: int = 30, max_turns: int = 100_000) -> list[dict]` — one dict per session touched in the last `days`: `{"session_id","project","harness","account","turns","first_ts","last_ts","preview"}` (preview = first user turn's text, ≤160 chars). Sorted by `last_ts` desc.
- `live_transcript_search(query: str, projects, *, days: int = 30, max_turns: int = 100_000, limit: int = 20) -> list[dict]` — turns whose text contains `query` (casefold), each `{"session_id","project","harness","ts","role","text"}` (text ≤240 chars), newest first, capped to `limit`.

- [ ] **Step 1: Write the failing test**

```python
def _seed(tmp_path, monkeypatch, texts, day="2026-07-04"):
    import os, json
    from datetime import datetime, timezone
    import tesserae.activity_summary as A
    from tesserae.harness_sessions import _claude_project_dir
    proj = tmp_path / "proj"; proj.mkdir()
    root = tmp_path / ".claude-acct"
    d = root / "projects" / _claude_project_dir(proj); d.mkdir(parents=True)
    rows = [{"type": "user" if i % 2 == 0 else "assistant",
             "timestamp": f"{day}T10:0{i}:00Z",
             "message": {"role": "user", "content": [{"type": "text", "text": t}]}}
            for i, t in enumerate(texts)]
    tx = d / "s.jsonl"; tx.write_text("\n".join(json.dumps(r) for r in rows))
    stamp = datetime(2026, 7, 4, 10, tzinfo=timezone.utc).timestamp(); os.utime(tx, (stamp, stamp))
    monkeypatch.setattr(A, "discover_harness_roots", lambda: [root])
    monkeypatch.setattr(A, "_root_supports_claude", lambda r: True)
    monkeypatch.setattr(A, "_root_supports_codex", lambda r: False)
    return proj

def test_live_session_list_and_search(tmp_path, monkeypatch):
    from tesserae.live_sessions import live_session_list, live_transcript_search
    proj = _seed(tmp_path, monkeypatch, ["design the parser", "ok done", "ship it"])
    # A huge window so 'recent' catches the fixture regardless of wall clock:
    sessions = live_session_list([("proj", proj)], days=100000)
    assert len(sessions) == 1
    s = sessions[0]
    assert s["project"] == "proj" and s["turns"] == 3
    assert s["account"] == ".claude-acct" and "parser" in s["preview"]
    hits = live_transcript_search("parser", [("proj", proj)], days=100000)
    assert len(hits) == 1 and "parser" in hits[0]["text"]
    assert live_transcript_search("nonexistent", [("proj", proj)], days=100000) == []
```

- [ ] **Step 2: Run to verify it fails** — module not found.

- [ ] **Step 3: Implement** `tesserae/live_sessions.py`:

```python
"""Live session views for `tesserae serve` — read the harness roots directly
(recent-window bounded) so the served page never lags the way the compiled
projection can. Reuses the activity-summary discovery + parsing."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Sequence, Tuple

from tesserae.activity_summary import (
    KST, Window, in_window, iter_project_transcripts, parse_ts,
)
from tesserae.harness_sessions import _parse_jsonl, _claude_turns, _codex_turns


def _recent_window(days: int) -> Window:
    now = datetime.now(KST)
    return Window(now - timedelta(days=max(1, days)), now, "recent")


def _turns_for(harness: str, rows, max_turns: int):
    if harness == "codex":
        return _codex_turns(rows, limit=max_turns)
    return _claude_turns(rows, limit=max_turns)


def live_session_list(projects, *, days: int = 30, max_turns: int = 100_000):
    window = _recent_window(days)
    out: List[dict] = []
    for name, harness, path, key in iter_project_transcripts(projects, [window]):
        rows = _parse_jsonl(path)
        stamped = []
        for t in _turns_for(harness, rows, max_turns):
            ts = parse_ts(str(t.get("timestamp") or ""))
            if ts and in_window(ts, window):
                stamped.append((ts, t))
        if not stamped:
            continue
        stamped.sort(key=lambda p: p[0])
        preview = next(
            (str(t.get("text") or "")[:160] for _ts, t in stamped if t.get("role") == "user"),
            "",
        )
        out.append({
            "session_id": key, "project": name, "harness": harness,
            "account": key.split(":", 1)[0], "turns": len(stamped),
            "first_ts": stamped[0][0].isoformat(), "last_ts": stamped[-1][0].isoformat(),
            "preview": preview,
        })
    out.sort(key=lambda s: s["last_ts"], reverse=True)
    return out


def live_transcript_search(query, projects, *, days: int = 30, max_turns: int = 100_000, limit: int = 20):
    q = (query or "").casefold()
    if not q:
        return []
    window = _recent_window(days)
    hits: List[Tuple[datetime, dict]] = []
    for name, harness, path, key in iter_project_transcripts(projects, [window]):
        rows = _parse_jsonl(path)
        for t in _turns_for(harness, rows, max_turns):
            text = str(t.get("text") or "")
            ts = parse_ts(str(t.get("timestamp") or ""))
            if ts and in_window(ts, window) and q in text.casefold():
                hits.append((ts, {
                    "session_id": key, "project": name, "harness": harness,
                    "ts": ts.isoformat(), "role": str(t.get("role") or ""),
                    "text": text[:240],
                }))
    hits.sort(key=lambda p: p[0], reverse=True)
    return [h for _ts, h in hits[: max(1, int(limit))]]
```

- [ ] **Step 4: Run** — `.venv/bin/python -m pytest tests/test_live_sessions.py -q` → PASS.

- [ ] **Step 5: Commit** — `git add tesserae/live_sessions.py tests/test_live_sessions.py && git commit -m "feat(serve): live_sessions core — list + search harness roots directly"`

### Task 3: serve `/api/sessions` + live-merged `/api/transcript-search`

**Files:** Modify `tesserae/serve.py`; Test `tests/test_live_sessions.py`

**Interfaces:**
- New module helper `_run_sessions(handler, query_string, *, project_root, project_name)` → `handler._send_json(200, {"sessions": live_session_list([(project_name, project_root)], days=?, max_turns=?)})`; `days`/`max_turns` parsed from the query (defaults 30 / 100_000).
- `_run_transcript_search` gains `project_root`/`project_name`: it now calls `live_transcript_search(...)` and merges the hits ahead of the index `search_transcripts(...)["results"]`, deduped by `(session_id, ts)` where derivable, capped to `limit`; returns `{"available": True, "results": merged, "total": len(merged), "live": len(live_hits)}`.

- [ ] **Step 1: Read** `serve.py` 147-170 (`_run_transcript_search`), 289-331 (`_AskAwareHandler.do_GET`, incl. `self.project_root`, `self._clip_origin`, `self._send_json`), and 700-730 (the fleet handler's transcript-search call, which passes a per-alias `root`). Note the exact index-result item shape returned by `search_transcripts` (run `memex search` once or inspect `.results[0]`) so the merged live hits carry compatible keys — if the memex item has `text`/`snippet`+`session`+`ts`, map the live hit's `text`→that field.

- [ ] **Step 2: Write the failing test** (endpoint-level, via the module helpers with a fake handler):

```python
def test_run_sessions_and_search_merge(tmp_path, monkeypatch):
    import tesserae.serve as S
    proj = _seed(tmp_path, monkeypatch, ["design the parser", "reply"])
    monkeypatch.setattr(S, "search_transcripts",
                        lambda *a, **k: {"available": True, "results": [{"text": "old indexed hit"}], "total": 1},
                        raising=False)
    sent = {}
    class H:  # minimal fake handler
        project_root = proj
        def _send_json(self, status, body): sent["status"] = status; sent["body"] = body
    S._run_sessions(H(), "days=100000", project_root=proj, project_name="proj")
    assert sent["status"] == 200 and sent["body"]["sessions"][0]["project"] == "proj"
    S._run_transcript_search(H(), "q=parser", project=None, project_root=proj, project_name="proj")
    results = sent["body"]["results"]
    assert any("parser" in (r.get("text") or "") for r in results)   # live hit present
    assert any(r.get("text") == "old indexed hit" for r in results)  # index hit merged
```

- [ ] **Step 3: Implement** — add `_run_sessions`; extend `_run_transcript_search` to accept `project_root=None, project_name=None`, run the live search when a root is given, and merge. Wire `/api/sessions` into `_AskAwareHandler.do_GET` (origin-gated, same as transcript-search) passing `self.project_root` and its name; pass `project_root`/`project_name` into the existing transcript-search call. Do the same in the fleet handler (`_run_sessions`/`_run_transcript_search` with the per-alias `root`).

- [ ] **Step 4: Run** — `.venv/bin/python -m pytest tests/test_live_sessions.py -q` → PASS.

- [ ] **Step 5: Commit** — `git add tesserae/serve.py tests/test_live_sessions.py && git commit -m "feat(serve): /api/sessions live endpoint + live-merged transcript-search"`

### Task 4: Sessions page renders live with fallback

**Files:** Modify `tesserae/site/pages.py` (Sessions index generation); Test: Playwright screenshot

**Interfaces:**
- The generated `sessions/index.html` includes a small inline script: on load, `fetch('/api/sessions')`; on a 200, replace/prepend a "Live sessions" list rendered from the JSON (session_id/account/turns/last_ts/preview); on any error or non-200 (static hosting), leave the compiled content untouched.

- [ ] **Step 1:** Read how `pages.py` generates the sessions index (around the `card(title="Sessions", href="sessions/index.html"…)` producer and the sessions page renderer).
- [ ] **Step 2:** Add the inline fetch+render script to the sessions index page (guarded, best-effort, fallback-safe).
- [ ] **Step 3:** `.venv/bin/python -m tesserae export site`; `tesserae serve --port 8480` (background); Playwright `browser_navigate` to `/tesserae/sessions/` and screenshot — confirm the live list renders under serve. Then confirm the page still renders its compiled content when `/api/sessions` 404s (open the built file directly / static).
- [ ] **Step 4:** Commit — `git add tesserae/site/pages.py && git commit -m "feat(site): sessions page renders live /api/sessions with compiled fallback"`

### Task 5: Final verification
- [ ] `.venv/bin/python -m pytest tests/test_live_sessions.py tests/test_decisions.py tests/test_activity_summary_gather.py tests/test_activity_summary_render.py tests/test_cli_summary.py tests/test_cli_command_table.py -q` — green.
- [ ] Real drive: `tesserae serve` running → `GET /api/sessions` returns current sessions; `GET /api/transcript-search?q=<recent term>` returns fresh live hits ahead of index hits. Screenshot the live Sessions page.

## Self-review notes
- **Coverage:** live session list (T2), live transcript search + merge (T2/T3), `/api/sessions` endpoint (T3), page live+fallback (T4), `--max-turns` all surfaces (T1 + `max_turns` params in T2/T3), recent-window bound (T2 `_recent_window`), origin gate (T3), graceful static fallback (T4). Mapped.
- **Type consistency:** `live_session_list(projects, *, days, max_turns) -> list[dict]`, `live_transcript_search(query, projects, *, days, max_turns, limit) -> list[dict]`, `_recent_window(days) -> Window`, `build_summary(..., turn_limit=…)`, `gather_decisions(..., turn_limit=…)` — consistent.
- **Confirm during execution:** the memex index result-item shape (T3 Step 1) so merged live hits carry compatible keys; the exact `pages.py` sessions-index producer (T4 Step 1).
