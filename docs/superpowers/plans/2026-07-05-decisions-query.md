# Decisions Query Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Add `tesserae decisions` — retrieve decisions (deterministic human choices from `AskUserQuestion` + LLM-mined agent decisions) across all registered projects within an optional time range, as a CLI command, an `activity_summary`-style MCP tool, and a `/summary`-style slash command; and feed the deterministic human decisions into the summary's Decisions section.

**Architecture:** New `tesserae/decisions.py` reuses `activity_summary`'s window resolution, all-accounts transcript discovery (factored into a shared `iter_project_transcripts`), excerpt rendering, and LLM client. Human decisions are parsed deterministically from raw transcript rows (`AskUserQuestion` tool_use ↔ tool_result); agent decisions come from a codex LLM pass over the in-window excerpts.

**Tech Stack:** Python 3 stdlib (`re`, `json`, `dataclasses`, `datetime`), pytest. Reuses existing Tesserae modules; no new deps.

## Global Constraints

- **Never window on session `started_at`** — human decisions dated by the `AskUserQuestion` answer's turn timestamp; agent decisions by their session's in-window turn time.
- **Default scope = all registered projects**; `--project <name>` opts into one.
- **`AskUserQuestion` is Claude-Code-only** — deterministic human decisions come from `claude-code` transcripts; codex transcripts contribute only to the agent (LLM) pass.
- **Deterministic-first:** `--no-llm` yields only the human decisions (no LLM). MCP `include_agent` defaults `true`.
- Reuse, don't duplicate: `resolve_windows`, `render_session_excerpts`, `_summary_llm_client`, `parse_ts`, `in_window`, `_rows_match_project`, `_claude_project_dir`, `_parse_jsonl`.
- One commit per task; conventional messages.

---

## File Structure

- **Modify** `tesserae/activity_summary.py` — factor `iter_project_transcripts(projects, windows)` out of `scan_messages` (both use it); feed human decisions into `build_summary`'s narrator context.
- **Create** `tesserae/decisions.py` — `Decision`, `parse_human_decisions`, `extract_agent_decisions`, `gather_decisions`, `render_decisions`.
- **Modify** `tesserae/cli.py` — `_handle_decisions` + `decisions` subparser.
- **Modify** `tesserae/mcp_server.py` — `query_decisions` tool schema + dispatch + `_mcp_query_decisions`.
- **Create** the `/decisions` slash command file (mirror the `/summary` one).
- **Create** `tests/test_decisions.py`.

---

### Task 1: Factor `iter_project_transcripts` out of `scan_messages`

**Files:** Modify `tesserae/activity_summary.py`; Test `tests/test_decisions.py`

**Interfaces:**
- Produces: `iter_project_transcripts(projects: Sequence[Tuple[str, object]], windows: Sequence[Window]) -> Iterator[Tuple[str, str, Path, str]]` — yields `(project_name, harness, transcript_path, session_key)` for every transcript across all harness roots that (a) matches a project and (b) has `mtime >= min(window.start)`, deduped by real path. `harness` is `"claude-code"` or `"codex"`; `session_key` is `f"{account_dir_name}:{path.stem}"`.
- `scan_messages` is refactored to iterate `iter_project_transcripts` and parse turns per yielded transcript (behaviour unchanged).

- [ ] **Step 1: Write the failing test** (`tests/test_decisions.py`)

```python
import os
from datetime import datetime, timezone
from pathlib import Path
import tesserae.activity_summary as A
from tesserae.activity_summary import iter_project_transcripts, resolve_windows
from tesserae.harness_sessions import _claude_project_dir

def _claude_root(tmp_path, project_root, day, rows_writer):
    root = tmp_path / ".claude-acct"
    slug = _claude_project_dir(project_root)
    d = root / "projects" / slug
    d.mkdir(parents=True, exist_ok=True)
    tx = d / "sess.jsonl"
    rows_writer(tx)
    stamp = datetime.fromisoformat(f"{day}T10:00:00+00:00").timestamp()
    os.utime(tx, (stamp, stamp))
    return root, tx

def test_iter_project_transcripts_yields_matched_in_window(tmp_path, monkeypatch):
    proj = tmp_path / "proj"; proj.mkdir()
    root, tx = _claude_root(tmp_path, proj, "2026-07-04", lambda p: p.write_text("{}\n"))
    monkeypatch.setattr(A, "discover_harness_roots", lambda: [root])
    monkeypatch.setattr(A, "_root_supports_claude", lambda r: True)
    monkeypatch.setattr(A, "_root_supports_codex", lambda r: False)
    (w,) = resolve_windows(day="2026-07-04", tz=timezone.utc)
    got = list(iter_project_transcripts([("proj", proj)], [w]))
    assert len(got) == 1
    name, harness, path, key = got[0]
    assert name == "proj" and harness == "claude-code"
    assert Path(path).name == "sess.jsonl"
    assert key == ".claude-acct:sess"
```

- [ ] **Step 2: Run to verify it fails** — `.venv/bin/python -m pytest tests/test_decisions.py::test_iter_project_transcripts_yields_matched_in_window -v` → FAIL (import error).

- [ ] **Step 3: Implement** — add `iter_project_transcripts` to `activity_summary.py` (lift the root/glob/mtime/dedup logic out of `scan_messages`), then rewrite `scan_messages` to consume it:

```python
def iter_project_transcripts(projects, windows):
    roots = discover_harness_roots()
    floor = min(w.start for w in windows).timestamp()
    seen_files: set[str] = set()
    seen_roots: set[str] = set()

    def _fresh(path: str) -> bool:
        try:
            if os.stat(path).st_mtime < floor:
                return False
        except OSError:
            return False
        real = os.path.realpath(path)
        if real in seen_files:
            return False
        seen_files.add(real)
        return True

    for r in roots:
        rk = os.path.realpath(r)
        if rk in seen_roots:
            continue
        seen_roots.add(rk)
        acct = Path(r).name
        if _root_supports_claude(r):
            for name, root in projects:
                slug = _claude_project_dir(Path(root))
                for d in glob.glob(str(Path(r) / "projects" / (slug + "*"))):
                    dn = Path(d).name
                    if dn != slug and not dn.startswith(slug + "-"):
                        continue
                    for f in glob.glob(str(Path(d) / "*.jsonl")):
                        if _fresh(f):
                            yield name, "claude-code", Path(f), f"{acct}:{Path(f).stem}"
        if _root_supports_codex(r):
            for f in glob.glob(str(Path(r) / "sessions" / "**" / "*.jsonl"), recursive=True):
                if not _fresh(f):
                    continue
                rows = _parse_jsonl(Path(f))
                for name, root in projects:
                    if _rows_match_project(rows, Path(root)):
                        yield name, "codex", Path(f), f"{acct}:{Path(f).stem}"
                        break
```

Rewrite `scan_messages` to use it:

```python
def scan_messages(projects, windows, *, turn_limit=100_000):
    out = {name: {w.label: [] for w in windows} for name, _root in projects}
    for name, harness, path, key in iter_project_transcripts(projects, windows):
        rows = _parse_jsonl(path)
        turns = _codex_turns(rows, limit=turn_limit) if harness == "codex" else _claude_turns(rows, limit=turn_limit)
        for turn in turns:
            ts = parse_ts(str(turn.get("timestamp") or ""))
            if not ts:
                continue
            for w in windows:
                if in_window(ts, w):
                    nm = turn.get("name")
                    out[name][w.label].append(MessageItem(
                        ts=ts, role=str(turn.get("role") or ""),
                        name=str(nm) if nm else None, text=str(turn.get("text") or ""),
                        project=name, session_id=key, harness=harness))
                    break
    return out
```

- [ ] **Step 4: Run** — `.venv/bin/python -m pytest tests/test_decisions.py tests/test_activity_summary_gather.py tests/test_activity_summary_render.py -q` → PASS (scan_messages behaviour preserved; the gather/render suites still green).

- [ ] **Step 5: Commit** — `git add tesserae/activity_summary.py tests/test_decisions.py && git commit -m "refactor(summary): factor iter_project_transcripts shared by scan + decisions"`

### Task 2: `Decision` type + deterministic human-decision parser

**Files:** Create `tesserae/decisions.py`; Test `tests/test_decisions.py`

**Interfaces:**
- Produces: `@dataclass Decision(ts, source, project, session_id, question, answer, options, header)` where `ts: datetime`, `source: str` (`"human"|"agent"`), `options: list[str]`.
- `parse_human_decisions(rows: Sequence[Mapping], project: str, session_id: str, window: Window) -> list[Decision]` — for each `AskUserQuestion` `tool_use` matched to its `tool_result`, one `Decision(source="human")` per answered question; dated by the tool_result row's timestamp; window-filtered.

- [ ] **Step 1: Write the failing test**

```python
import json
from datetime import timezone
from tesserae.decisions import Decision, parse_human_decisions
from tesserae.activity_summary import resolve_windows

def _auq_rows(day):
    tuid = "toolu_x"
    return [
        {"type": "assistant", "timestamp": f"{day}T10:00:00Z", "message": {"content": [
            {"type": "tool_use", "id": tuid, "name": "AskUserQuestion", "input": {"questions": [
                {"question": "Which backend?", "header": "Backend",
                 "options": [{"label": "SQLite"}, {"label": "Postgres"}]}]}}]}},
        {"type": "user", "timestamp": f"{day}T10:01:00Z", "message": {"content": [
            {"type": "tool_result", "tool_use_id": tuid,
             "content": 'Your questions have been answered: "Which backend?"="Postgres". Continue.'}]}},
    ]

def test_parse_human_decision(tmp_path):
    (w,) = resolve_windows(day="2026-07-04", tz=timezone.utc)
    got = parse_human_decisions(_auq_rows("2026-07-04"), "proj", "acct:sess", w)
    assert len(got) == 1
    d = got[0]
    assert d.source == "human" and d.question == "Which backend?"
    assert d.answer == "Postgres" and d.options == ["SQLite", "Postgres"]
    assert d.header == "Backend" and d.project == "proj"
    # Out-of-window is excluded.
    (w5,) = resolve_windows(day="2026-07-05", tz=timezone.utc)
    assert parse_human_decisions(_auq_rows("2026-07-04"), "proj", "acct:sess", w5) == []
```

- [ ] **Step 2: Run to verify it fails** — module not found.

- [ ] **Step 3: Implement** `tesserae/decisions.py`:

```python
"""Retrieve decisions across projects + time: deterministic human choices from
Claude Code's AskUserQuestion tool, plus LLM-mined agent decisions."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Mapping, Optional, Sequence, Tuple

from tesserae.activity_summary import (
    Window, in_window, iter_project_transcripts, parse_ts, render_session_excerpts,
    resolve_windows, scan_messages, _resolve_projects, _summary_llm_client,
)
from tesserae.harness_sessions import _parse_jsonl, _claude_turns, _codex_turns

logger = logging.getLogger(__name__)

# Matches the `"<question>"="<answer>"` pairs in an AskUserQuestion tool_result.
_ANSWER_RE = re.compile(r'"([^"]+)"="([^"]+)"')


@dataclass
class Decision:
    ts: datetime
    source: str            # "human" | "agent"
    project: str
    session_id: str
    question: str
    answer: str
    options: List[str] = field(default_factory=list)
    header: str = ""


def _tool_result_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for it in content:
            if isinstance(it, dict) and isinstance(it.get("text"), str):
                parts.append(it["text"])
            elif isinstance(it, str):
                parts.append(it)
        return " ".join(parts)
    return ""


def parse_human_decisions(rows, project, session_id, window):
    tool_uses = {}   # tool_use_id -> [question dicts]
    for row in rows:
        content = (row.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for item in content:
            if (isinstance(item, dict) and item.get("type") == "tool_use"
                    and item.get("name") == "AskUserQuestion"):
                tool_uses[item.get("id")] = ((item.get("input") or {}).get("questions")) or []
    out: List[Decision] = []
    for row in rows:
        content = (row.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        ts = parse_ts(str(row.get("timestamp") or ""))
        for item in content:
            if not isinstance(item, dict) or item.get("type") != "tool_result":
                continue
            qs = tool_uses.get(item.get("tool_use_id"))
            if qs is None or not ts or not in_window(ts, window):
                continue
            answers = dict(_ANSWER_RE.findall(_tool_result_text(item.get("content"))))
            for q in qs:
                qtext = str(q.get("question") or "")
                ans = answers.get(qtext)
                if not ans:
                    continue
                out.append(Decision(
                    ts=ts, source="human", project=project, session_id=session_id,
                    question=qtext, answer=ans,
                    options=[str(o.get("label") or "") for o in (q.get("options") or [])],
                    header=str(q.get("header") or "")))
    return out
```

- [ ] **Step 4: Run** — `.venv/bin/python -m pytest tests/test_decisions.py::test_parse_human_decision -v` → PASS.

- [ ] **Step 5: Commit** — `git add tesserae/decisions.py tests/test_decisions.py && git commit -m "feat(decisions): Decision type + deterministic AskUserQuestion parser"`

### Task 3: Agent-decision LLM extractor

**Files:** Modify `tesserae/decisions.py`; Test `tests/test_decisions.py`

**Interfaces:**
- Produces: `extract_agent_decisions(excerpts: str, client, project: str, ts: datetime) -> list[Decision]` — one `Decision(source="agent")` per line the model returns in the form `<decision> :: <rationale>`; empty on empty reply. `client` exposes `complete_text(system, user) -> str`.

- [ ] **Step 1: Write the failing test**

```python
from datetime import datetime, timezone
from tesserae.decisions import extract_agent_decisions

class _FakeClient:
    def __init__(self, out): self.out = out; self.seen = None
    def complete_text(self, system, user): self.seen = user; return self.out

def test_extract_agent_decisions_parses_lines():
    c = _FakeClient("Use SQLite by default :: lighter, no server\nPin origin to the ext id :: security")
    ts = datetime(2026, 7, 4, tzinfo=timezone.utc)
    got = extract_agent_decisions("<excerpts>", c, "proj", ts)
    assert [d.question for d in got] == ["Use SQLite by default", "Pin origin to the ext id"]
    assert got[0].answer == "lighter, no server"
    assert all(d.source == "agent" and d.project == "proj" for d in got)
    assert "<excerpts>" in c.seen
```

- [ ] **Step 2: Run to verify it fails** — `extract_agent_decisions` undefined.

- [ ] **Step 3: Implement** (append to `tesserae/decisions.py`):

```python
_AGENT_SYSTEM = (
    "You extract EXPLICIT decisions from a developer's agent-session excerpts: "
    "choices made, trade-offs settled, direction changes — the kind a teammate "
    "would want recorded. Output ONE decision per line as `<decision> :: <one-line "
    "rationale>`. No headers, no numbering, no prose. If a session made no real "
    "decision, output nothing. NEVER invent a decision not supported by the excerpts."
)


def extract_agent_decisions(excerpts, client, project, ts):
    reply = (client.complete_text(system=_AGENT_SYSTEM, user=excerpts) or "").strip()
    out: List[Decision] = []
    for line in reply.splitlines():
        line = line.strip().lstrip("-* ").strip()
        if "::" not in line:
            continue
        decision, _, rationale = line.partition("::")
        decision = decision.strip()
        if not decision:
            continue
        out.append(Decision(ts=ts, source="agent", project=project, session_id="",
                            question=decision, answer=rationale.strip()))
    return out
```

- [ ] **Step 4: Run** — `.venv/bin/python -m pytest tests/test_decisions.py::test_extract_agent_decisions_parses_lines -v` → PASS.

- [ ] **Step 5: Commit** — `git add tesserae/decisions.py tests/test_decisions.py && git commit -m "feat(decisions): LLM agent-decision extractor"`

### Task 4: `gather_decisions` orchestration + `render_decisions`

**Files:** Modify `tesserae/decisions.py`; Test `tests/test_decisions.py`

**Interfaces:**
- Produces:
  - `gather_decisions(windows, project_names=None, *, include_agent=True) -> list[Decision]` — resolves projects (all registered or the named subset), iterates `iter_project_transcripts`; for claude transcripts runs `parse_human_decisions`; when `include_agent`, runs one `extract_agent_decisions` per project over that project's in-window excerpts (from `scan_messages`), dated by the project's earliest in-window turn. Sorted by `(ts, project, source)`.
  - `render_decisions(decisions) -> str` — markdown grouped by `## <project>` then `### Human decisions` / `### Agent decisions`; human line `- **<header>**: <question> → **<answer>**  _(options: a · b)_  · <YYYY-MM-DD HH:MM>`; agent line `- <question> — <answer>  · <date>`; `_none_` when a group is empty.

- [ ] **Step 1: Write the failing test** — deterministic (`include_agent=False`) e2e over a fake claude account root (reuse `_claude_root` + `_auq_rows` from earlier), monkeypatching `discover_harness_roots`/`_root_supports_*` and `ProjectRegistry.iter_registered_projects` to yield `("proj", root)`; assert the human decision appears and `render_decisions` groups it under `## proj`. (Full code: build the account root with `_auq_rows`, patch, call `gather_decisions([w], ["proj"], include_agent=False)`, assert `len==1` and `"Which backend?"`/`"Postgres"` in `render_decisions(...)`.)

- [ ] **Step 2: Run to verify it fails.**

- [ ] **Step 3: Implement** `gather_decisions` + `render_decisions`. `gather_decisions` computes each project's earliest in-window message ts from `scan_messages` output to date its agent decisions; builds the client via `_summary_llm_client` (best-effort — on failure, skip agent decisions, log). `render_decisions` as specified.

- [ ] **Step 4: Run** — `.venv/bin/python -m pytest tests/test_decisions.py -q` → PASS.

- [ ] **Step 5: Commit** — `git add tesserae/decisions.py tests/test_decisions.py && git commit -m "feat(decisions): gather + render decisions"`

### Task 5: CLI `tesserae decisions`

**Files:** Modify `tesserae/cli.py`; Test `tests/test_decisions.py`

**Interfaces:**
- Consumes: `resolve_windows`, `gather_decisions`, `render_decisions`, `Decision`.
- Produces: `tesserae decisions [--day/--week/--since/--until] [--project N]... [--no-llm] [--json]` → markdown to stdout (or a JSON array of decision dicts when `--json`). Mirror `_handle_summary`'s window parsing + the `summary` subparser registration exactly (`_handle_summary` is the reference).

- [ ] **Step 1–5:** failing CLI test (monkeypatch `cli.gather_decisions` → one canned `Decision`, invoke `cli.main(["decisions","--day","2026-07-04","--no-llm"])`, assert rc 0 and output contains the decision); implement `_handle_decisions` (`include_agent = not args.no_llm`; `--json` → `print(json.dumps([asdict(d)|{"ts":d.ts.isoformat()} ...]))`) + register the subparser using the same mechanism `summary` uses (read `_handle_summary` and its registration first); run `tests/test_decisions.py tests/test_cli_command_table.py`; commit `feat(cli): tesserae decisions`.

### Task 6: MCP `query_decisions` tool + `/decisions` slash

**Files:** Modify `tesserae/mcp_server.py`; Create the `/decisions` slash file; Test `tests/test_decisions.py`

**Interfaces:**
- Produces: MCP `query_decisions(day?, week?, since?, until?, project?, include_agent?=true)` → `{"decisions": [ {ts, source, project, question, answer, options, header}, ... ]}` (ts ISO). Mirror `activity_summary`'s tool dict + `if name == "activity_summary"` dispatch + `_mcp_activity_summary` (read those first).

- [ ] **Step 1–5:** failing MCP dispatch test (monkeypatch `mcp_server.gather_decisions`; call the real server ctor `LLMWikiMCPServer().call_tool("query_decisions", {"day":"2026-07-04","include_agent":False})`; assert the decision dict is in the result); implement the tool dict + dispatch branch + `_mcp_query_decisions` (catch `ValueError` from `resolve_windows` → `{"error": ...}`); add the `/decisions` slash file mirroring the discovered `/summary` one; run `tests/test_decisions.py tests/test_mcp_activity_summary.py`; commit `feat(mcp): query_decisions tool + /decisions slash`.

### Task 7: Feed human decisions into the summary's Decisions section

**Files:** Modify `tesserae/activity_summary.py`; Test `tests/test_activity_summary_render.py`

**Interfaces:**
- Consumes: `parse_human_decisions` / a thin `gather_decisions(..., include_agent=False)` for the windows/projects being summarized.
- Produces: `build_summary`, when `synthesize`, prepends an explicit `=== HUMAN DECISIONS (AskUserQuestion) ===` block (one line per human decision: `<question> -> <answer>`) to the narrator's conversation context so the LLM's **Decisions & Insights** reliably includes them. Deterministic digest unchanged. Import inside the function to avoid a circular import (`decisions` imports `activity_summary`).

- [ ] **Step 1–5:** failing test asserting the fake narrator's `seen_user` contains a human decision line when a summarized session has an `AskUserQuestion`; implement (gather human decisions for the windows/projects, format the block, append to `conversation` before `_maybe_narrate`); run `tests/test_activity_summary_render.py tests/test_decisions.py`; commit `feat(summary): surface explicit AskUserQuestion decisions in Decisions & Insights`.

---

## Final verification
- [ ] `.venv/bin/python -m pytest tests/test_decisions.py tests/test_activity_summary_gather.py tests/test_activity_summary_render.py tests/test_cli_summary.py tests/test_cli_command_table.py tests/test_mcp_activity_summary.py -q` — all green.
- [ ] Real drive: `tesserae decisions --since 2026-06-30 --no-llm` → prints the explicit human `AskUserQuestion` decisions across registered projects since 2026-06-30.

## Self-review notes
- **Coverage:** human deterministic (T2), agent LLM (T3), gather+render structured list (T4), CLI +`--json` (T5), MCP `query_decisions` + slash (T6), time-range via `resolve_windows` (T4/T5/T6), all-projects default (T4), summary integration (T7), DRY `iter_project_transcripts` (T1). All spec points mapped.
- **Type consistency:** `Decision(ts, source, project, session_id, question, answer, options, header)`, `iter_project_transcripts -> (name, harness, Path, key)`, `gather_decisions(windows, project_names=None, *, include_agent=True)`, `render_decisions(list)` — consistent across tasks.
- **Confirm during execution:** the real `AskUserQuestion` tool_result content may be a string OR a list of blocks (`_tool_result_text` handles both); the summary/CLI/MCP registration idioms (read `_handle_summary` / the `activity_summary` tool before T5/T6).
