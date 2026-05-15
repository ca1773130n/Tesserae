"""Convert demo-corpus agent-session transcripts to harness-native format.

Reads:   examples/demo-corpus/.agent-sessions/<slug>/transcript.jsonl
         examples/demo-corpus/.agent-sessions/<slug>/metadata.json
Writes:  examples/demo-corpus/.harness-sessions/claude-code/<filename>.json

The output JSON files are consumable by
:class:`llm_wiki.harness_sessions.HarnessSession.from_dict` and discoverable by
:meth:`llm_wiki.harness_sessions.HarnessSessionStore.list_sessions`, which
globs ``<root>/*/*.json``. They are placed under a ``claude-code/`` subdir so
the store glob picks them up after CI copies the tree into
``.llm-wiki/harness_sessions/``.

The original JSONL transcripts under ``.agent-sessions/`` remain the
human-readable showcase content; the JSON files emitted here are derived
artifacts (re-runnable from this script).
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Dict, List

REPO_ROOT = Path(__file__).resolve().parents[3]
DEMO_SESSIONS_DIR = REPO_ROOT / "examples" / "demo-corpus" / ".agent-sessions"
OUT_DIR = REPO_ROOT / "examples" / "demo-corpus" / ".harness-sessions" / "claude-code"

HARNESS = "claude-code"
AGENT_LABEL = "Claude Code"
PROJECT_NAME = "LLM-Wiki"


def _safe_slug(value: str) -> str:
    text = "".join(ch.lower() if ch.isalnum() else "-" for ch in str(value)).strip("-")
    while "--" in text:
        text = text.replace("--", "-")
    return text or "session"


def _date_prefix(started_at: str, fallback_slug: str) -> str:
    match = re.match(r"\d{4}-\d{2}-\d{2}", started_at or "")
    if match:
        return match.group(0)
    match = re.match(r"\d{4}-\d{2}-\d{2}", fallback_slug or "")
    return match.group(0) if match else "undated"


def _filename(session_id: str, slug: str, date: str) -> str:
    stem = _safe_slug(slug or session_id)
    digest = hashlib.sha1(session_id.encode("utf-8")).hexdigest()[:8]
    return f"{date}-{stem}-{digest}"


def _truncate(text: str, limit: int = 1200) -> str:
    cleaned = text.strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


def _row_text(row: Dict[str, object]) -> str:
    content = row.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, (list, dict)):
        return json.dumps(content, ensure_ascii=False)
    return ""


def _tool_arguments_text(row: Dict[str, object]) -> str:
    args = row.get("arguments")
    if args is None:
        return ""
    if isinstance(args, str):
        return args
    return json.dumps(args, ensure_ascii=False, sort_keys=True)


def _build_turns(rows: List[Dict[str, object]]) -> List[Dict[str, object]]:
    """Map transcript rows to the {role, text, timestamp, name?} shape the
    site renderer expects (see llm_wiki.site.sessions._turns)."""

    turns: List[Dict[str, object]] = []
    for row in rows:
        role = str(row.get("role") or "").strip().lower()
        timestamp = str(row.get("timestamp") or "")
        if role in {"user", "assistant"}:
            text_parts: List[str] = []
            reasoning = row.get("reasoning")
            if isinstance(reasoning, str) and reasoning.strip():
                text_parts.append(f"_thinking_: {reasoning.strip()}")
            body = _row_text(row).strip()
            if body:
                text_parts.append(body)
            text = _truncate("\n\n".join(text_parts), limit=4000)
            if text:
                turns.append({"role": role, "timestamp": timestamp, "text": text})
        elif role == "tool_call":
            name = str(row.get("name") or "tool")
            text = _truncate(_tool_arguments_text(row), limit=1200)
            if not text:
                text = f"call to {name}"
            turns.append({"role": "tool", "timestamp": timestamp, "name": name, "text": text})
        elif role == "tool_result":
            # The renderer collapses tool results under their tool_call; skip
            # explicit result rows to avoid duplicate ``tool`` turns.
            continue
    return turns


def _summary_text(meta: Dict[str, object], rows: List[Dict[str, object]]) -> str:
    summary = meta.get("summary")
    if isinstance(summary, str) and summary.strip():
        return _truncate(summary, limit=1200)
    for row in rows:
        if str(row.get("role") or "").lower() == "user":
            return _truncate(_row_text(row), limit=1200)
    return ""


def _collect_activity(rows: List[Dict[str, object]]) -> tuple[List[str], int]:
    tools: set[str] = set()
    tool_call_count = 0
    for row in rows:
        if str(row.get("role") or "").lower() == "tool_call":
            tool_call_count += 1
            name = row.get("name")
            if isinstance(name, str) and name:
                tools.add(name)
    return sorted(tools), tool_call_count


def convert_one(session_dir: Path) -> Dict[str, object]:
    """Produce a HarnessSession-compatible dict for one demo session."""
    meta = json.loads((session_dir / "metadata.json").read_text(encoding="utf-8"))
    transcript_lines = (session_dir / "transcript.jsonl").read_text(encoding="utf-8").splitlines()
    rows: List[Dict[str, object]] = [json.loads(line) for line in transcript_lines if line.strip()]

    session_id_raw = str(meta.get("session_id") or session_dir.name)
    namespaced_id = f"claude-code:{session_id_raw}:{session_dir.name}"
    started_at = str(meta.get("started_at") or "")
    ended_at = str(meta.get("ended_at") or started_at)
    title = str(meta.get("title") or session_dir.name)
    slug = _safe_slug(title or session_id_raw)
    date = _date_prefix(started_at, session_dir.name)

    tools_detected, tool_call_count = _collect_activity(rows)
    declared_tools = meta.get("tools_used")
    if isinstance(declared_tools, list):
        merged = sorted(set(tools_detected) | {str(t) for t in declared_tools if isinstance(t, str)})
    else:
        merged = tools_detected

    files_touched: List[str] = []
    artifacts = meta.get("artifacts_created")
    if isinstance(artifacts, list):
        files_touched = sorted({str(p) for p in artifacts if isinstance(p, str)})

    message_count = sum(1 for r in rows if str(r.get("role") or "").lower() in {"user", "assistant"})
    summary = _summary_text(meta, rows)

    turns = _build_turns(rows)

    metadata: Dict[str, object] = {
        "config_root": "examples/demo-corpus/.agent-sessions",
        "transcript": str(session_dir / "transcript.jsonl"),
        "demo": True,
        "turns": turns,
    }
    for key in ("papers_cited", "questions_referenced", "stumbles", "turn_count", "user", "agent"):
        if key in meta:
            metadata[key] = meta[key]

    return {
        "id": namespaced_id,
        "slug": slug,
        "harness": HARNESS,
        "agent_label": AGENT_LABEL,
        "project_name": PROJECT_NAME,
        # Left as a non-resolvable demo marker so the renderer accepts it
        # regardless of where CI checks the repo out (Pages runner, local).
        # The store glob ``*/*.json`` is what surfaces the file; no
        # project_root matcher is applied at render time.
        "project_root": "",
        "started_at": started_at,
        "ended_at": ended_at,
        "model": str(meta.get("agent") or meta.get("model") or "claude-opus-4-7"),
        "title": title,
        "summary": summary,
        "message_count": message_count,
        "tool_call_count": tool_call_count,
        "tools_used": merged,
        "files_touched": files_touched,
        "commands_run": [],
        "raw_transcript_path": str((session_dir / "transcript.jsonl").relative_to(REPO_ROOT)),
        "redacted_preview": summary,
        "metadata": metadata,
        # Help the store choose a stable filename. HarnessSession.filename is
        # computed from id+slug+date so the output path here is informational
        # only; the actual on-disk name is recomputed at write time.
        "_filename_hint": _filename(namespaced_id, slug, date),
    }


def main(argv: List[str]) -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for stale in OUT_DIR.glob("*.json"):
        stale.unlink()
    session_dirs = sorted(p for p in DEMO_SESSIONS_DIR.iterdir() if p.is_dir())
    written = 0
    for session_dir in session_dirs:
        payload = convert_one(session_dir)
        filename = payload.pop("_filename_hint")
        out_path = OUT_DIR / f"{filename}.json"
        out_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        rel = out_path.relative_to(REPO_ROOT)
        print(
            f"wrote {rel} ({len(payload['metadata']['turns'])} turns, "
            f"{payload['tool_call_count']} tool calls)"
        )
        written += 1
    print(f"\nconverted {written} sessions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
