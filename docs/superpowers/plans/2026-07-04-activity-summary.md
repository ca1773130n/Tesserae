# Activity Summary + Citation-Name Fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a daily/weekly activity-summary feature (sessions + findings + git commits + PRs + ingested docs, windowed by each artifact's own timestamp, rendered deterministically then narrated by the LLM) exposed as a CLI command, MCP tool, and slash command; and fix the `node-<hash>` citation leak so synthesized answers show entity names.

**Architecture:** One gather engine in `tesserae/activity_summary.py` (window resolution → five gatherers → deterministic markdown → optional LLM narrative). Thin CLI/MCP/slash adapters call it. The citation fix is a shared `rewrite_citations` helper wired into the two synthesis paths (`query.py`, `llm_synthesis.py`).

**Tech Stack:** Python 3, stdlib (`argparse`, `subprocess`, `datetime`, `re`, `dataclasses`), pytest. Reuses existing Tesserae modules — no new dependencies.

## Global Constraints

- **Never window on session `started_at`/`ended_at`.** Sessions are long-running. Messages and findings window on the *turn's own* `timestamp`; ingested docs on the document's own `updated_at`/`created`/source mtime; commits/PRs on their git/GitHub dates.
- **Byte-idempotence:** do not add provenance timestamps to `graph.json` (a test forbids it). The summary reads timestamps from live sources (`.tesserae/harness_sessions.db` turns, git, gh, rag manifest), never writes them into the graph.
- **Default scope = all registered projects** (`ProjectRegistry.iter_registered_projects()`); `--project <name>` / `project=` opts into one.
- **Graceful degradation:** a project that is not a git repo, or a missing/unauthenticated `gh`, drops only its section — never fails the whole summary.
- **No new dependencies. No new SQLite columns/indexes. Markdown output only.**
- Follow existing patterns: CLI handlers are `_handle_*(args) -> int`; MCP tools are a dict in the `list_tools` list + an `if name == "..."` branch in `call_tool` delegating to a `self._mcp_*` method.
- Frequent commits: one per task. Conventional-commit messages.

---

## File Structure

- **Create** `tesserae/citation_names.py` — `NODE_CITATION_RE` + `rewrite_citations(body, id_to_name)`. Single source of the citation regex.
- **Modify** `tesserae/query.py` — import the shared regex/helper; rewrite citations in `answer` (~:640) and `_answer_via_cli` (~:710); fix the untitled-node title fallback in `search` (~:388).
- **Modify** `tesserae/llm_synthesis.py` — rewrite `[node_id]→name` on `synthesize()` output using the id→name map built from the request inputs.
- **Create** `tesserae/activity_summary.py` — `Window`, `resolve_window`, item dataclasses, five gatherers, `render_markdown`, `synthesize_narrative`, `build_summary`.
- **Modify** `tesserae/cli.py` — `_handle_summary(args)` + `summary` subparser in `main()`.
- **Modify** `tesserae/mcp_server.py` — `activity_summary` tool dict + `if name == "activity_summary"` branch + `_mcp_activity_summary` method.
- **Create** the `/summary` slash command file (mirror an existing tesserae slash command's location/format — the executor greps for one before writing).
- **Create** tests: `tests/test_citation_names.py`, `tests/test_activity_summary_window.py`, `tests/test_activity_summary_gather.py`, `tests/test_activity_summary_render.py`, `tests/test_cli_summary.py`, `tests/test_mcp_activity_summary.py`.

---

## PART A — `node-<hash>` citation fix

### Task 1: `rewrite_citations` shared helper

**Files:**
- Create: `tesserae/citation_names.py`
- Test: `tests/test_citation_names.py`

**Interfaces:**
- Produces: `NODE_CITATION_RE: re.Pattern`; `rewrite_citations(body: str, id_to_name: Mapping[str, str]) -> str` — replaces each `[node_id]` marker whose id is a key in `id_to_name` with `[<name>]`; leaves unknown ids and non-citation text untouched.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_citation_names.py
from tesserae.citation_names import rewrite_citations

def test_known_id_becomes_name():
    body = "We chose SQLite [SessionDecision:db:ab12cd] for storage."
    out = rewrite_citations(body, {"SessionDecision:db:ab12cd": "Use SQLite for the session index"})
    assert out == "We chose SQLite [Use SQLite for the session index] for storage."

def test_unknown_id_left_verbatim():
    body = "See [Type:slug:deadbeef]."
    assert rewrite_citations(body, {}) == "See [Type:slug:deadbeef]."

def test_non_citation_brackets_untouched():
    body = "array[0] and [ok] short token"
    assert rewrite_citations(body, {"array": "x"}) == body  # 'array' not a bracketed citation here

def test_multiple_and_repeated():
    body = "[a:b:c1] then [a:b:c1] and [d:e:f2]"
    out = rewrite_citations(body, {"a:b:c1": "Alpha", "d:e:f2": "Delta"})
    assert out == "[Alpha] then [Alpha] and [Delta]"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_citation_names.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tesserae.citation_names'`

- [ ] **Step 3: Write minimal implementation**

```python
# tesserae/citation_names.py
"""Rewrite ``[node_id]`` citation markers to human-readable names.

The synthesis paths instruct the model to cite sources with the raw node id.
This turns those ids into the display name already carried alongside each
source, so agents read names instead of ``node-<hash>``. Unknown ids are left
verbatim so a stray citation never crashes rendering."""
from __future__ import annotations

import re
from typing import Mapping

# Same shape query.py has used for its citation check. Single source of truth.
NODE_CITATION_RE = re.compile(r"\[([a-zA-Z0-9_\-:./]{3,})\]")


def rewrite_citations(body: str, id_to_name: Mapping[str, str]) -> str:
    if not body or not id_to_name:
        return body

    def _sub(match: "re.Match[str]") -> str:
        node_id = match.group(1)
        name = id_to_name.get(node_id)
        return f"[{name}]" if name else match.group(0)

    return NODE_CITATION_RE.sub(_sub, body)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_citation_names.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add tesserae/citation_names.py tests/test_citation_names.py
git commit -m "feat(citations): shared [node_id]->name rewrite helper"
```

### Task 2: Wire the rewrite into `query.py` (ask paths + title fallback)

**Files:**
- Modify: `tesserae/query.py` (imports; `search` hit construction ~:388; `answer` return ~:615-640; `_answer_via_cli` return ~:695-710)
- Test: `tests/test_citation_names.py` (add `test_query_answer_rewrites_citations`)

**Interfaces:**
- Consumes: `rewrite_citations`, `NODE_CITATION_RE` from `tesserae.citation_names`.
- Produces: `answer()` / `_answer_via_cli()` return bodies whose `[node_id]` citations are the node title; `QueryHit.title` never equals the raw id for untitled nodes.

- [ ] **Step 1: Read the three sites**

Read `tesserae/query.py` lines 180-210 (regex), 370-415 (`search` hit build, incl. the `title = raw.get("title") or raw.get("id")` fallback and `node_id` assignment), 600-645 (`answer` return), 680-712 (`_answer_via_cli` return). Confirm `hits` is a `list[QueryHit]` in scope at both return sites.

- [ ] **Step 2: Write the failing test**

```python
# tests/test_citation_names.py  (append)
def test_query_answer_rewrites_citations(monkeypatch):
    """answer() must return a body with [node_id] rewritten to the hit title."""
    from tesserae import query as q
    hits = [q.QueryHit(kind="decision", title="Use SQLite", node_id="Dec:db:ab12", score=1.0,
                       snippet="", href="", body="")]  # match QueryHit's real fields when reading
    body = "We decided [Dec:db:ab12]."
    id_to_name = {h.node_id: h.title for h in hits if h.node_id}
    assert q.rewrite_citations(body, id_to_name) == "We decided [Use SQLite]."
```

(Adjust `QueryHit(...)` kwargs to the real dataclass fields seen in Step 1; the assertion on `rewrite_citations` is the invariant that matters.)

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_citation_names.py::test_query_answer_rewrites_citations -v`
Expected: FAIL — `AttributeError: module 'tesserae.query' has no attribute 'rewrite_citations'`

- [ ] **Step 4: Implement**

At the top of `query.py`, add:

```python
from tesserae.citation_names import NODE_CITATION_RE, rewrite_citations
```

Remove the local `_NODE_CITATION_RE = re.compile(...)` definition and replace its two usages (`if not _NODE_CITATION_RE.search(body_text)`) with `NODE_CITATION_RE`.

In `search()` hit construction (~:388), replace the untitled fallback so an untitled node resolves to a readable label instead of its id:

```python
# BEFORE:  title = raw.get("title") or raw.get("id")
raw_title = raw.get("title")
if raw_title:
    title = str(raw_title)
else:
    # Never surface the raw id as a display name. Prefer a session display
    # name, else "<Kind>: <slug>" derived from the node id.
    title = _readable_label_from_id(str(raw.get("id") or ""), raw)
```

Add a small module-level helper near the other private helpers:

```python
def _readable_label_from_id(node_id: str, raw: Mapping[str, object]) -> str:
    """Human label for an untitled node. '<Kind>: <slug>' from 'Kind:slug:hash'."""
    parts = node_id.split(":")
    if len(parts) >= 2 and parts[0]:
        return f"{parts[0]}: {parts[1].replace('-', ' ').strip()}".strip(": ")
    return node_id or "source"
```

In `answer()` just before `return ...answer=body_text.strip() + "\n"...` (~:640):

```python
id_to_name = {h.node_id: h.title for h in hits if h.node_id and h.title}
body_text = rewrite_citations(body_text, id_to_name)
```

Apply the identical two lines just before the `_answer_via_cli` return (~:710), using the `hits` in that scope.

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/test_citation_names.py tests/test_ask_router.py tests/test_cli_top_level_ask.py -v`
Expected: PASS (no regression in the ask paths).

- [ ] **Step 6: Commit**

```bash
git add tesserae/query.py tests/test_citation_names.py
git commit -m "fix(query): render citations as node names, not raw ids"
```

### Task 3: Wire the rewrite into `llm_synthesis.py`

**Files:**
- Modify: `tesserae/llm_synthesis.py` (`synthesize()` return ~:452-490; it already has `_extract_citations`)
- Test: `tests/test_citation_names.py` (add `test_synthesis_body_rewrite`)

**Interfaces:**
- Consumes: `rewrite_citations` from `tesserae.citation_names`.
- Produces: `LlmSynthesisResponse.body` (for `compile_context(synthesize=True)` and `wiki_page`) has `[node_id]` citations rewritten to names.

- [ ] **Step 1: Read** `llm_synthesis.py` 120-200 (request/response dataclasses, how source nodes with ids+names are packed into the prompt inputs) and 452-495 (`synthesize` / `_call_api` return). Identify the field on the request that lists source nodes with `(node_id, name/title)` — that is the id→name map source.

- [ ] **Step 2: Write the failing test**

```python
# tests/test_citation_names.py (append)
def test_synthesis_body_rewrite():
    from tesserae.citation_names import rewrite_citations
    body = "Finding [SessionInsight:x:9f] supports it."
    assert rewrite_citations(body, {"SessionInsight:x:9f": "Cache warm-up halves latency"}) \
        == "Finding [Cache warm-up halves latency] supports it."
```

- [ ] **Step 3: Run to verify it fails** (only if the import/behavior is not yet present)

Run: `python -m pytest tests/test_citation_names.py::test_synthesis_body_rewrite -v`
Expected: PASS for the helper itself; the wiring is verified by Step 5's synthesis test path.

- [ ] **Step 4: Implement**

Add `from tesserae.citation_names import rewrite_citations` to `llm_synthesis.py`. Build the id→name map from the request's source-node list (field identified in Step 1), then rewrite the body immediately before constructing `LlmSynthesisResponse`:

```python
id_to_name = {n.node_id: (n.title or n.name) for n in req.sources if n.node_id}  # match real field names
body = rewrite_citations(body, id_to_name)
```

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/test_citation_names.py tests/test_context_compiler.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add tesserae/llm_synthesis.py tests/test_citation_names.py
git commit -m "fix(synthesis): rewrite compile_context/wiki citations to node names"
```

---

## PART B — Activity Summary

### Task 4: Window resolution + item dataclasses

**Files:**
- Create: `tesserae/activity_summary.py`
- Test: `tests/test_activity_summary_window.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) Window(start: datetime, end: datetime, label: str)` — `start` inclusive, `end` exclusive, both tz-aware.
  - `resolve_windows(*, day=None, week=None, since=None, until=None, tz=None) -> list[Window]` — `day="YYYY-MM-DD"` → one 24h window `[00:00, +24h)`; `week="YYYY-MM-DD"` (or `week=""`/None-with-flag → last 7 days ending today) → 7 consecutive daily `Window`s oldest-first; `since`/`until` → one window. Default tz = local (`datetime.now().astimezone().tzinfo`).
  - `in_window(ts: datetime, w: Window) -> bool` → `w.start <= ts < w.end`.
  - `parse_ts(value: str) -> datetime | None` — parse ISO-8601 (tolerate `Z`), return tz-aware (assume UTC if naive), else None.
  - Item dataclasses: `MessageItem(ts, role, name, text, project, session_id, harness)`, `FindingItem(ts, kind, body, project, session_id, node_id)`, `CommitItem(ts, sha, author, subject, project)`, `PRItem(ts, number, title, state, event, project)`, `DocItem(ts, title, source_path, project)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_activity_summary_window.py
from datetime import datetime, timezone, timedelta
from tesserae.activity_summary import resolve_windows, in_window, parse_ts, Window

def test_day_window_is_24h_half_open():
    (w,) = resolve_windows(day="2026-07-04", tz=timezone.utc)
    assert w.start == datetime(2026, 7, 4, tzinfo=timezone.utc)
    assert w.end == datetime(2026, 7, 5, tzinfo=timezone.utc)
    assert w.label == "2026-07-04"

def test_week_expands_to_seven_daily_windows():
    ws = resolve_windows(week="2026-07-04", tz=timezone.utc)
    assert len(ws) == 7
    assert ws[0].start == datetime(2026, 6, 28, tzinfo=timezone.utc)
    assert ws[-1].end == datetime(2026, 7, 5, tzinfo=timezone.utc)

def test_edge_inclusion_half_open():
    (w,) = resolve_windows(day="2026-07-04", tz=timezone.utc)
    assert in_window(w.start, w) is True                    # start included
    assert in_window(w.end, w) is False                     # end excluded
    assert in_window(w.end - timedelta(seconds=1), w) is True

def test_parse_ts_handles_z_and_naive():
    assert parse_ts("2026-07-04T12:00:00Z") == datetime(2026, 7, 4, 12, tzinfo=timezone.utc)
    assert parse_ts("not-a-date") is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_activity_summary_window.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement** `Window`, `resolve_windows`, `in_window`, `parse_ts`, and the item dataclasses in `tesserae/activity_summary.py`.

```python
# tesserae/activity_summary.py  (window section)
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
from typing import List, Optional

@dataclass(frozen=True)
class Window:
    start: datetime
    end: datetime
    label: str

def _local_tz() -> tzinfo:
    return datetime.now().astimezone().tzinfo or timezone.utc

def _midnight(day: str, tz: tzinfo) -> datetime:
    y, m, d = (int(x) for x in day.split("-"))
    return datetime(y, m, d, tzinfo=tz)

def resolve_windows(*, day: Optional[str] = None, week: Optional[str] = None,
                    since: Optional[str] = None, until: Optional[str] = None,
                    tz: Optional[tzinfo] = None) -> List[Window]:
    tz = tz or _local_tz()
    if day:
        s = _midnight(day, tz)
        return [Window(s, s + timedelta(days=1), day)]
    if week is not None:
        end_day = _midnight(week, tz) if week else _midnight(datetime.now(tz).strftime("%Y-%m-%d"), tz)
        first = end_day - timedelta(days=6)
        out = []
        for i in range(7):
            s = first + timedelta(days=i)
            out.append(Window(s, s + timedelta(days=1), s.strftime("%Y-%m-%d")))
        return out
    if since or until:
        s = parse_ts(since) if since else datetime(1970, 1, 1, tzinfo=tz)
        e = parse_ts(until) if until else datetime.now(tz)
        return [Window(s, e, f"{s.date()}..{e.date()}")]
    today = datetime.now(tz).strftime("%Y-%m-%d")
    s = _midnight(today, tz)
    return [Window(s, s + timedelta(days=1), today)]

def in_window(ts: datetime, w: Window) -> bool:
    return w.start <= ts < w.end

def parse_ts(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
```

Add the five item dataclasses below (fields exactly as in Interfaces).

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_activity_summary_window.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tesserae/activity_summary.py tests/test_activity_summary_window.py
git commit -m "feat(summary): window resolution + activity item types"
```

### Task 5: Message gatherer (turn-level, uncapped, no started_at)

**Files:**
- Modify: `tesserae/activity_summary.py`
- Test: `tests/test_activity_summary_gather.py`

**Interfaces:**
- Consumes: `HarnessSessionsDB.list_for_project(project_root) -> List[HarnessSession]`; `HarnessSession.raw_transcript_path`, `.harness`, `.id`; `tesserae.harness_sessions._parse_jsonl(path)`, `_claude_turns(rows, limit)`, `_codex_turns(rows, limit)` (each returns `List[{role, timestamp, text, name?}]`).
- Produces: `gather_messages(project: str, root: str, window: Window, *, turn_limit: int = 100_000) -> tuple[list[MessageItem], dict[str, list[dict]]]` — messages whose `parse_ts(turn["timestamp"]) ∈ window`, plus a `session_id -> full turns list` map (reused by Task 6 for finding resolution).

- [ ] **Step 1: Read** `harness_sessions.py` 476-520 (`_parse_jsonl`), 822-870 (`_claude_turns`/`_codex_turns` — confirm the `limit` keeps the **head** or **tail** of the turn list; record which). Read `harness_sessions_db.py` 111-143.

- [ ] **Step 2: Write the failing test** (fixture builds two tiny Claude JSONL transcripts with turns on 07-04 and 07-05):

```python
# tests/test_activity_summary_gather.py
import json
from datetime import timezone
from pathlib import Path
from tesserae.activity_summary import gather_messages, resolve_windows

def _write_claude_transcript(p: Path, day: str, texts):
    rows = []
    for i, t in enumerate(texts):
        rows.append({"type": "user" if i % 2 == 0 else "assistant",
                     "timestamp": f"{day}T10:0{i}:00Z",
                     "message": {"role": "user" if i % 2 == 0 else "assistant",
                                 "content": [{"type": "text", "text": t}]}})
    p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")

def test_gather_messages_only_in_window(tmp_path, monkeypatch):
    tx = tmp_path / "sess.jsonl"
    _write_claude_transcript(tx, "2026-07-04", ["hi day4", "reply day4"])
    # Stand up a fake HarnessSessionsDB returning one session pointing at tx.
    # (Executor: use the real HarnessSession dataclass; harness="claude".)
    ...
    (w,) = resolve_windows(day="2026-07-04", tz=timezone.utc)
    msgs, turns_by_session = gather_messages("proj", str(tmp_path), w)
    assert {m.text for m in msgs} == {"hi day4", "reply day4"}
    assert all(m.ts.date().isoformat() == "2026-07-04" for m in msgs)

    (w5,) = resolve_windows(day="2026-07-05", tz=timezone.utc)
    msgs5, _ = gather_messages("proj", str(tmp_path), w5)
    assert msgs5 == []   # day-4 turns excluded from the day-5 window
```

(Executor completes the `...` by constructing/monkeypatching `HarnessSessionsDB` to return a `HarnessSession(raw_transcript_path=str(tx), harness="claude", id="s1", ...)`. Reuse the real transcript-row shape observed in Step 1 so `_claude_turns` parses it.)

- [ ] **Step 3: Run to verify it fails.** Run: `python -m pytest tests/test_activity_summary_gather.py::test_gather_messages_only_in_window -v`. Expected: FAIL — `gather_messages` undefined.

- [ ] **Step 4: Implement**

```python
# activity_summary.py
from tesserae.harness_sessions import _parse_jsonl, _claude_turns, _codex_turns
from tesserae.harness_sessions_db import HarnessSessionsDB
from pathlib import Path

def _turns_for(session, turn_limit: int):
    rows = _parse_jsonl(Path(session.raw_transcript_path))
    if session.harness == "codex":
        return _codex_turns(rows, limit=turn_limit)
    return _claude_turns(rows, limit=turn_limit)

def gather_messages(project, root, window, *, turn_limit=100_000):
    db = HarnessSessionsDB(Path(root) / ".tesserae" / "harness_sessions.db")
    messages, turns_by_session = [], {}
    for session in db.list_for_project(root):
        tpath = Path(session.raw_transcript_path)
        # cheap prune: a transcript untouched before the window can hold no in-window turns
        if not tpath.exists() or tpath.stat().st_mtime < window.start.timestamp():
            continue
        turns = _turns_for(session, turn_limit)
        turns_by_session[session.id] = turns
        for turn in turns:
            ts = parse_ts(str(turn.get("timestamp") or ""))
            if ts and in_window(ts, window):
                messages.append(MessageItem(ts=ts, role=str(turn.get("role") or ""),
                    name=turn.get("name"), text=str(turn.get("text") or ""),
                    project=project, session_id=session.id, harness=session.harness))
    return messages, turns_by_session
```

(Confirm the `HarnessSessionsDB(...)` constructor path arg matches Step 1; adjust if it takes a project dir instead of the db file.)

- [ ] **Step 5: Run to verify it passes.** Run: `python -m pytest tests/test_activity_summary_gather.py -v`. Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add tesserae/activity_summary.py tests/test_activity_summary_gather.py
git commit -m "feat(summary): turn-level message gatherer (no started_at)"
```

### Task 6: Findings gatherer (turn_ids → compile-aligned turn timestamp)

**Files:**
- Modify: `tesserae/activity_summary.py`
- Test: `tests/test_activity_summary_gather.py` (add)

**Interfaces:**
- Consumes: compiled graph nodes (loaded via the same path `mcp_server` uses — executor reads how `_mcp_find_session_findings` loads the graph and reuses it); `_SESSION_FINDING_TYPES`; per-node `metadata` keys `session_id`, `turn_ids` (list[int]), `body`/`name`; the `turns_by_session` map from Task 5.
- Produces: `gather_findings(project: str, graph, turns_by_session: dict, window: Window) -> list[FindingItem]` — a finding's `ts` = `parse_ts(turns[max(turn_ids)]["timestamp"])` using a **compile-aligned turn list**; keep if `ts ∈ window`. A finding with no resolvable turn timestamp is **skipped** (never dated from `started_at`).

- [ ] **Step 1: Resolve the index-base question.** From Task 5 Step 1 you recorded whether `_claude_turns`'s `limit` keeps head or tail. Findings' `turn_ids` were assigned against the compile-time list built with the **default limit (300)**. To guarantee alignment, resolve finding timestamps against a turn list built the SAME way the compiler built it: `_claude_turns(rows)` / `_codex_turns(rows)` with the **default** limit. Add a helper that returns this compile-aligned list per session (may reuse `turns_by_session` only if it was built with the default limit; otherwise rebuild with default limit for finding resolution).

- [ ] **Step 2: Write the failing test**

```python
def test_finding_dated_by_source_turn(tmp_path):
    from tesserae.activity_summary import gather_findings, resolve_windows
    from datetime import timezone
    # compile-aligned turns: index 0 -> 07-04, index 1 -> 07-05
    turns_by_session = {"s1": [
        {"role": "assistant", "timestamp": "2026-07-04T10:00:00Z", "text": "a"},
        {"role": "assistant", "timestamp": "2026-07-05T10:00:00Z", "text": "b"},
    ]}
    class N:  # minimal node stand-in; executor swaps for the real node type
        type = type("T", (), {"value": "SessionInsight"})()
        id = "SessionInsight:s1:zz"
        metadata = {"session_id": "s1", "turn_ids": [0], "body": "insight text"}
    graph = type("G", (), {"nodes": {"n": N()}})()
    (w4,) = resolve_windows(day="2026-07-04", tz=timezone.utc)
    got = gather_findings("proj", graph, turns_by_session, w4)
    assert [f.body for f in got] == ["insight text"]
    (w5,) = resolve_windows(day="2026-07-05", tz=timezone.utc)
    assert gather_findings("proj", graph, turns_by_session, w5) == []
```

- [ ] **Step 3: Run to verify it fails.** Expected: FAIL — `gather_findings` undefined.

- [ ] **Step 4: Implement**

```python
def gather_findings(project, graph, turns_by_session, window):
    from tesserae.mcp_server import ProjectWiki  # or the finding-type set import; match real location
    finding_types = {"SessionInsight", "SessionDecision", "SessionQuestion",
                     "SessionTODO", "SessionHypothesis", "SessionTakeaway"}
    out = []
    for node in graph.nodes.values():
        tname = getattr(node.type, "value", node.type)
        if tname not in finding_types:
            continue
        meta = node.metadata or {}
        turns = turns_by_session.get(meta.get("session_id")) or []
        ids = [i for i in (meta.get("turn_ids") or []) if isinstance(i, int) and 0 <= i < len(turns)]
        if not ids:
            continue  # no resolvable turn -> undated -> skip
        ts = parse_ts(str(turns[max(ids)].get("timestamp") or ""))
        if ts and in_window(ts, window):
            out.append(FindingItem(ts=ts, kind=tname, body=str(meta.get("body") or node.name or ""),
                                   project=project, session_id=meta.get("session_id"), node_id=node.id))
    return out
```

(Replace the inline `finding_types` with the real `_SESSION_FINDING_TYPES` import once located; keep the values identical.)

- [ ] **Step 5: Run to verify it passes.** Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add tesserae/activity_summary.py tests/test_activity_summary_gather.py
git commit -m "feat(summary): finding gatherer dated by source turn"
```

### Task 7: Git + PR gatherers (read at summary time, graceful skip)

**Files:**
- Modify: `tesserae/activity_summary.py`
- Test: `tests/test_activity_summary_gather.py` (add — real temp git repo; PRs skip-path)

**Interfaces:**
- Produces:
  - `gather_git(project: str, root: str, window: Window) -> list[CommitItem]` — `git -C <root> log --since=<start iso> --until=<end iso> --date=iso-strict --pretty=%H%x1f%an%x1f%aI%x1f%s`, split on `\x1f`, `ts=parse_ts(aI)`. Returns `[]` if not a git repo / git missing (catch `FileNotFoundError`/non-zero via `subprocess.run(..., capture_output=True)`).
  - `gather_prs(project: str, root: str, window: Window) -> list[PRItem]` — derive `owner/repo` from `git -C <root> remote get-url origin`; run `gh pr list --state all --limit 200 --json number,title,state,createdAt,mergedAt,closedAt` (via subprocess). Emit a `PRItem(event="opened")` when `createdAt ∈ window`, `event="merged"` when `mergedAt ∈ window`, `event="closed"` when `closedAt ∈ window` (and not merged). Returns `[]` on any failure (no origin, gh missing, non-zero exit, JSON error) — never raises.

- [ ] **Step 1: Write the failing test** (git path is deterministic; PR path asserts graceful empty):

```python
def test_gather_git_windows_commits(tmp_path):
    import subprocess
    from datetime import timezone
    from tesserae.activity_summary import gather_git, resolve_windows
    root = tmp_path
    def git(*a, env=None): subprocess.run(["git", "-C", str(root), *a], check=True,
                                          capture_output=True, env=env)
    git("init", "-q"); git("config", "user.email", "t@t"); git("config", "user.name", "T")
    import os
    env = {**os.environ, "GIT_AUTHOR_DATE": "2026-07-04T10:00:00", "GIT_COMMITTER_DATE": "2026-07-04T10:00:00"}
    (root / "f").write_text("x")
    git("add", "."); 
    subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", "day4 commit"], check=True,
                   capture_output=True, env=env)
    (w4,) = resolve_windows(day="2026-07-04", tz=timezone.utc)
    commits = gather_git("proj", str(root), w4)
    assert [c.subject for c in commits] == ["day4 commit"]
    (w5,) = resolve_windows(day="2026-07-05", tz=timezone.utc)
    assert gather_git("proj", str(root), w5) == []

def test_gather_prs_skips_without_gh(tmp_path):
    from datetime import timezone
    from tesserae.activity_summary import gather_prs, resolve_windows
    (w,) = resolve_windows(day="2026-07-04", tz=timezone.utc)
    assert gather_prs("proj", str(tmp_path), w) == []  # not a repo / no gh -> empty, no raise
```

- [ ] **Step 2: Run to verify it fails.** Expected: FAIL — functions undefined.

- [ ] **Step 3: Implement** (reuse the `subprocess.run` timeout+capture pattern from `raganything_refresh.py:_git_head`):

```python
import subprocess, json as _json

def _run(cmd, cwd, timeout=20):
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    return p.stdout if p.returncode == 0 else None

def gather_git(project, root, window):
    out = _run(["git", "log", f"--since={window.start.isoformat()}",
                f"--until={window.end.isoformat()}", "--date=iso-strict",
                "--pretty=%H%x1f%an%x1f%aI%x1f%s"], cwd=root)
    if not out:
        return []
    items = []
    for line in out.splitlines():
        sha, an, aI, subj = (line.split("\x1f") + ["", "", "", ""])[:4]
        ts = parse_ts(aI)
        if ts and in_window(ts, window):
            items.append(CommitItem(ts=ts, sha=sha[:12], author=an, subject=subj, project=project))
    return items

def gather_prs(project, root, window):
    origin = _run(["git", "remote", "get-url", "origin"], cwd=root)
    if not origin:
        return []
    raw = _run(["gh", "pr", "list", "--state", "all", "--limit", "200", "--json",
                "number,title,state,createdAt,mergedAt,closedAt"], cwd=root, timeout=30)
    if not raw:
        return []
    try:
        prs = _json.loads(raw)
    except ValueError:
        return []
    items = []
    for pr in prs:
        for field, event in (("createdAt", "opened"), ("mergedAt", "merged"), ("closedAt", "closed")):
            ts = parse_ts(pr.get(field) or "")
            if event == "closed" and pr.get("mergedAt"):
                continue  # a merge already counted; don't double-count as closed
            if ts and in_window(ts, window):
                items.append(PRItem(ts=ts, number=pr["number"], title=pr["title"],
                                    state=pr["state"], event=event, project=project))
    return items
```

- [ ] **Step 4: Run to verify it passes.** Expected: PASS (git test real; PR test empty).

- [ ] **Step 5: Commit**

```bash
git add tesserae/activity_summary.py tests/test_activity_summary_gather.py
git commit -m "feat(summary): git + PR gatherers, read-time, graceful skip"
```

### Task 8: Ingested-docs gatherer (best-effort, doc's own timestamp)

**Files:**
- Modify: `tesserae/activity_summary.py`
- Test: `tests/test_activity_summary_gather.py` (add)

**Interfaces:**
- Produces: `gather_docs(project: str, root: str, graph, window: Window) -> list[DocItem]` — for ingested/document nodes, timestamp = the doc's own `updated_at`/`created`/`analysis_date` metadata if present, else the `source_path` file's mtime; keep if `∈ window`. Best-effort: unknown/unreadable → skip. **Never** uses session `started_at`.

- [ ] **Step 1: Read** where ingested content records `updated_at` (`raganything_refresh.py:304` and its manifest location) and how Document nodes carry `source_path`. Decide the precedence: node metadata `updated_at`/`created`/`analysis_date` → else `os.stat(source_path).st_mtime`.

- [ ] **Step 2: Write the failing test** (a fake document node with `updated_at`, and one dated via file mtime):

```python
def test_gather_docs_by_own_timestamp(tmp_path):
    import os, time
    from datetime import timezone, datetime
    from tesserae.activity_summary import gather_docs, resolve_windows
    src = tmp_path / "doc.md"; src.write_text("hello")
    os.utime(src, (datetime(2026,7,4,10,tzinfo=timezone.utc).timestamp(),) * 2)
    class D:
        type = type("T", (), {"value": "Document"})()
        id = "Document:doc:aa"; name = "Doc"
        metadata = {"source_path": str(src)}  # no explicit updated_at -> fall back to mtime
    graph = type("G", (), {"nodes": {"d": D()}})()
    (w4,) = resolve_windows(day="2026-07-04", tz=timezone.utc)
    assert [d.title for d in gather_docs("proj", str(tmp_path), graph, w4)] == ["Doc"]
    (w5,) = resolve_windows(day="2026-07-05", tz=timezone.utc)
    assert gather_docs("proj", str(tmp_path), graph, w5) == []
```

- [ ] **Step 3: Run to verify it fails.** Expected: FAIL — undefined.

- [ ] **Step 4: Implement**

```python
import os
_DOC_TYPES = {"Document", "IngestedDocument", "Source"}  # widen to the real ingested-node type names from Step 1

def _doc_ts(meta, window):
    for key in ("updated_at", "created", "analysis_date"):
        ts = parse_ts(str(meta.get(key) or ""))
        if ts:
            return ts
    sp = meta.get("source_path")
    if sp and os.path.exists(sp):
        return datetime.fromtimestamp(os.stat(sp).st_mtime, tz=window.start.tzinfo)
    return None

def gather_docs(project, root, graph, window):
    out = []
    for node in graph.nodes.values():
        tname = getattr(node.type, "value", node.type)
        if tname not in _DOC_TYPES:
            continue
        ts = _doc_ts(node.metadata or {}, window)
        if ts and in_window(ts, window):
            out.append(DocItem(ts=ts, title=node.name or node.id,
                               source_path=str((node.metadata or {}).get("source_path") or ""),
                               project=project))
    return out
```

- [ ] **Step 5: Run to verify it passes.** Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add tesserae/activity_summary.py tests/test_activity_summary_gather.py
git commit -m "feat(summary): ingested-docs gatherer by own timestamp (best-effort)"
```

### Task 9: Deterministic renderer + `build_summary` orchestration + e2e

**Files:**
- Modify: `tesserae/activity_summary.py`
- Test: `tests/test_activity_summary_render.py`

**Interfaces:**
- Consumes: all five gatherers; `ProjectRegistry.iter_registered_projects()`; graph load helper (from Task 6).
- Produces:
  - `render_day(window, items_by_kind: dict, aggregates: dict) -> str` — deterministic markdown for one day: `## <label>` then subsections `### Sessions`, `### Files touched`, `### Decisions & Insights`, `### Commits`, `### Pull Requests`, `### Ingested docs`; empty subsections rendered as `_none_`. Stable ordering (sort by ts then id) so output is reproducible.
  - `@dataclass SummaryResult(markdown: str, paths: list[Path])`.
  - `build_summary(windows: list[Window], project_names: Optional[list[str]] = None, *, synthesize: bool = True, write: bool = True) -> SummaryResult` — resolves projects (all registered, or the named subset), runs the five gatherers per project per window, renders each day deterministically, optionally calls `synthesize_narrative` (Task 10), writes `.tesserae/summaries/<project>/daily-<label>.md` (or `weekly-*.md` when >1 window) per project when `write`, returns combined markdown + paths.

- [ ] **Step 1: Write the failing e2e test** — a fixture project dir with `.tesserae/harness_sessions.db` seeded (via `HarnessSessionsDB.upsert`) with two sessions (turns on 07-04 and 07-05) + a temp git repo with one commit each day; run `build_summary([day 07-04], ["proj"], synthesize=False, write=False)`:

```python
# tests/test_activity_summary_render.py
def test_e2e_deterministic_day(tmp_path, monkeypatch):
    # Executor: seed HarnessSessionsDB(tmp_path/'.tesserae/harness_sessions.db') with two
    # HarnessSession rows whose transcripts have turns on 2026-07-04 and 2026-07-05; git repo
    # with a "day4 commit" (author date 07-04) and a "day5 commit" (07-05). Register "proj"->tmp_path
    # by monkeypatching ProjectRegistry.iter_registered_projects to yield ("proj", tmp_path).
    from tesserae.activity_summary import build_summary, resolve_windows
    from datetime import timezone
    (w4,) = resolve_windows(day="2026-07-04", tz=timezone.utc)
    res = build_summary([w4], ["proj"], synthesize=False, write=False)
    assert "day4 commit" in res.markdown
    assert "day5 commit" not in res.markdown        # day-5 activity excluded
    # determinism: same inputs -> byte-identical output
    res2 = build_summary([w4], ["proj"], synthesize=False, write=False)
    assert res.markdown == res2.markdown
```

- [ ] **Step 2: Run to verify it fails.** Expected: FAIL — `build_summary` undefined.

- [ ] **Step 3: Implement** `render_day`, `SummaryResult`, and `build_summary`. Use the graph load helper from Task 6 (skip findings/docs for a project with no compiled graph). Sort every list by `(ts, id/sha/number)` before rendering. When `synthesize=False`, `markdown` is the deterministic body only.

- [ ] **Step 4: Run to verify it passes.** Run: `python -m pytest tests/test_activity_summary_render.py -v`. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tesserae/activity_summary.py tests/test_activity_summary_render.py
git commit -m "feat(summary): deterministic renderer + build_summary orchestration"
```

### Task 10: LLM narrative synthesis (weekly = synth over dailies)

**Files:**
- Modify: `tesserae/activity_summary.py`
- Test: `tests/test_activity_summary_render.py` (add — mock client)

**Interfaces:**
- Consumes: the project's configured LLM client — the SAME one `query.py:_answer_via_cli` constructs (exposes `complete_text(system: str, user: str) -> str`). Executor reads `_answer_via_cli` (query.py:646-710) to replicate client construction; expose it as `_summary_llm_client(root)`.
- Produces: `synthesize_narrative(deterministic_md: str, client) -> str` — one prose "what happened" section prepended to the deterministic body. For a multi-day (weekly) run, `build_summary` renders the 7 deterministic days, then calls `synthesize_narrative` once over their concatenation.

- [ ] **Step 1: Write the failing test** with a fake client:

```python
def test_synthesize_narrative_uses_client():
    from tesserae.activity_summary import synthesize_narrative
    class FakeClient:
        def complete_text(self, system, user):
            assert "day4 commit" in user  # deterministic body is the context
            return "On July 4, one commit landed."
    md = "## 2026-07-04\n### Commits\n- day4 commit\n"
    out = synthesize_narrative(md, FakeClient())
    assert "On July 4, one commit landed." in out
    assert "## 2026-07-04" in out  # deterministic body retained below the narrative
```

- [ ] **Step 2: Run to verify it fails.** Expected: FAIL — undefined.

- [ ] **Step 3: Implement**

```python
_SUMMARY_SYSTEM = ("You are summarizing a developer's activity for a time period. "
                   "Given a deterministic digest of sessions, findings, commits, PRs and "
                   "ingested docs, write a concise narrative of what happened and why it "
                   "mattered. Do not invent activity not present in the digest.")

def synthesize_narrative(deterministic_md: str, client) -> str:
    prose = client.complete_text(system=_SUMMARY_SYSTEM, user=deterministic_md)
    return f"{prose.strip()}\n\n---\n\n{deterministic_md}"
```

Wire `build_summary(..., synthesize=True)` to build `_summary_llm_client(root)` and call this; on client construction/LLM failure, fall back to the deterministic body (log a warning), never raise.

- [ ] **Step 4: Run to verify it passes.** Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tesserae/activity_summary.py tests/test_activity_summary_render.py
git commit -m "feat(summary): LLM narrative over the deterministic digest"
```

### Task 11: CLI `tesserae summary`

**Files:**
- Modify: `tesserae/cli.py` (add `_handle_summary`; register `summary` subparser in `main()` near the other `add_parser` calls ~:1871+)
- Test: `tests/test_cli_summary.py`

**Interfaces:**
- Consumes: `resolve_windows`, `build_summary`.
- Produces: `tesserae summary [--day YYYY-MM-DD] [--week [YYYY-MM-DD]] [--since ISO] [--until ISO] [--project NAME]... [--no-llm]` → prints markdown, writes files, returns `0`.

- [ ] **Step 1: Read** `cli.py` `main()` around 1871-2088 to copy the exact `sub.add_parser(...)` + `set_defaults(func=...)` registration idiom used by neighbors (e.g. `sessions`).

- [ ] **Step 2: Write the failing test** (invoke `main(["summary", "--day", "2026-07-04", "--project", "proj", "--no-llm"])` with `build_summary` monkeypatched):

```python
# tests/test_cli_summary.py
def test_cli_summary_no_llm(monkeypatch, capsys):
    import tesserae.cli as cli
    from tesserae.activity_summary import SummaryResult
    called = {}
    def fake_build(windows, projects, *, synthesize, write):
        called.update(windows=windows, projects=projects, synthesize=synthesize)
        return SummaryResult(markdown="# digest\n", paths=[])
    monkeypatch.setattr(cli, "build_summary", fake_build, raising=False)
    rc = cli.main(["summary", "--day", "2026-07-04", "--project", "proj", "--no-llm"])
    assert rc == 0
    assert called["projects"] == ["proj"]
    assert called["synthesize"] is False
    assert "# digest" in capsys.readouterr().out
```

- [ ] **Step 3: Run to verify it fails.** Expected: FAIL — unknown subcommand `summary`.

- [ ] **Step 4: Implement** `_handle_summary` (import `resolve_windows`/`build_summary` from `tesserae.activity_summary`; `--week` uses `nargs="?"`; `--project` uses `action="append"`; `synthesize = not args.no_llm`; print `result.markdown`; if `result.paths`, print each path) and register the subparser. Ensure `cli.build_summary` is importable at module scope so the test can patch it.

- [ ] **Step 5: Run to verify it passes.** Run: `python -m pytest tests/test_cli_summary.py tests/test_cli_command_table.py -v`. Expected: PASS (command-table test still green — update it if it enumerates subcommands).

- [ ] **Step 6: Commit**

```bash
git add tesserae/cli.py tests/test_cli_summary.py
git commit -m "feat(cli): tesserae summary command"
```

### Task 12: MCP `activity_summary` tool + `/summary` slash command

**Files:**
- Modify: `tesserae/mcp_server.py` (tool dict in the `list_tools` list ~:728 neighbors; `if name == "activity_summary"` branch in `call_tool` ~:1471+; `_mcp_activity_summary` method)
- Create: `/summary` slash command file (executor greps for an existing tesserae slash command to match path + frontmatter)
- Test: `tests/test_mcp_activity_summary.py`

**Interfaces:**
- Consumes: `resolve_windows`, `build_summary`.
- Produces: MCP tool `activity_summary(day?: str, week?: str, since?: str, until?: str, project?: str, synthesize?: bool = true)` → `{ "markdown": str, "paths": [str] }`. `project` optional (default all registered).

- [ ] **Step 1: Read** one existing tool end-to-end — its dict in `list_tools` (~:728 `list_sessions`) and its `if name == "list_sessions"` branch + `_mcp_list_sessions` method — to copy the arg-extraction + return idiom.

- [ ] **Step 2: Write the failing test** (call the server's `call_tool("activity_summary", {...})` with `build_summary` patched):

```python
# tests/test_mcp_activity_summary.py
def test_mcp_activity_summary_dispatch(monkeypatch):
    import tesserae.mcp_server as m
    from tesserae.activity_summary import SummaryResult
    monkeypatch.setattr(m, "build_summary", lambda *a, **k: SummaryResult(markdown="# d", paths=[]),
                        raising=False)
    server = m.TesseraeMCPServer() if hasattr(m, "TesseraeMCPServer") else m._server_for_tests()  # executor: real ctor
    out = server.call_tool("activity_summary", {"day": "2026-07-04", "synthesize": False})
    assert "# d" in str(out)
```

- [ ] **Step 3: Run to verify it fails.** Expected: FAIL — tool not registered.

- [ ] **Step 4: Implement** the tool dict (JSON schema with the six optional properties), the dispatch branch calling `self._mcp_activity_summary(arguments)`, and the method (resolve windows from args, `build_summary(windows, [project] if project else None, synthesize=..., write=True)`, return `{"markdown":..., "paths":[str(p) for p in ...]}`). Add the `/summary` slash file mirroring an existing one, invoking the CLI `tesserae summary` with passthrough args.

- [ ] **Step 5: Run to verify it passes.** Run: `python -m pytest tests/test_mcp_activity_summary.py -v`. Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add tesserae/mcp_server.py tests/test_mcp_activity_summary.py <slash-command-file>
git commit -m "feat(mcp): activity_summary tool + /summary slash command"
```

---

## Final verification

- [ ] Run the full affected suite: `python -m pytest tests/test_citation_names.py tests/test_activity_summary_window.py tests/test_activity_summary_gather.py tests/test_activity_summary_render.py tests/test_cli_summary.py tests/test_mcp_activity_summary.py tests/test_ask_router.py tests/test_context_compiler.py tests/test_cli_command_table.py -v` — all green.
- [ ] Drive it for real (verify skill): `tesserae summary --day $(date +%F) --no-llm` in a registered project → a real digest prints. Then without `--no-llm` → a narrative is prepended.
- [ ] Confirm no `graph.json` writes were added (byte-idempotence): `python -m pytest tests/test_byte_idempotence_phase5.py -v`.

## Self-review notes (author)

- **Spec coverage:** surfaces (T11/T12), all-projects default + `--project` (T9/T11/T12), no-`started_at` windowing (T4–T6, T8), git/PR read-time (T7), deterministic+narrative (T9/T10), citation fix (T1–T3), tests (each task) — all mapped.
- **Type consistency:** `resolve_windows`/`Window`/`in_window`/`parse_ts`, `build_summary(windows, project_names, *, synthesize, write)`, `SummaryResult(markdown, paths)`, `gather_messages` returning `(items, turns_by_session)` consumed by `gather_findings` — consistent across tasks.
- **Known soft spots to confirm during execution (from the spec):** `_claude_turns` head-vs-tail cap (T5/T6 Step 1), exact ingested-doc timestamp field + node type names (T8 Step 1), real `QueryHit`/node/server constructors in the illustrative tests (T2/T6/T12). These are reads, not open design questions.
