"""Normalized inbound agent-harness session history.

This module is intentionally separate from :mod:`llm_wiki.agent_harness`:
that module writes outbound harness config/instructions for agents, while this
one stores inbound historical sessions discovered from Claude Code, Codex, and
future local coding harnesses.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional


@dataclass(frozen=True)
class HarnessSession:
    """Harness-independent record for one local agent session."""

    id: str
    slug: str
    harness: str
    agent_label: str
    project_name: str
    project_root: str
    started_at: str
    ended_at: str = ""
    branch: str = ""
    commit_before: str = ""
    commit_after: str = ""
    model: str = ""
    title: str = ""
    summary: str = ""
    message_count: int = 0
    tool_call_count: int = 0
    token_input: int = 0
    token_output: int = 0
    token_total: int = 0
    cache_hit_ratio: Optional[float] = None
    tools_used: List[str] = field(default_factory=list)
    files_touched: List[str] = field(default_factory=list)
    commands_run: List[str] = field(default_factory=list)
    decisions: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    raw_transcript_path: str = ""
    redacted_preview: str = ""
    metadata: Dict[str, object] = field(default_factory=dict)

    @property
    def date(self) -> str:
        match = re.match(r"\d{4}-\d{2}-\d{2}", self.started_at or "")
        return match.group(0) if match else "undated"

    @property
    def safe_project(self) -> str:
        return safe_slug(self.project_name or Path(self.project_root).name or "project")

    @property
    def filename(self) -> str:
        return f"{self.date}-{safe_slug(self.slug or self.title or self.id)}"

    @property
    def href(self) -> str:
        return f"sessions/{self.safe_project}/{self.filename}.html"

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        payload["date"] = self.date
        payload["href"] = self.href
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "HarnessSession":
        allowed = set(cls.__dataclass_fields__.keys())
        clean: Dict[str, object] = {k: payload[k] for k in allowed if k in payload}
        for key in ("tools_used", "files_touched", "commands_run", "decisions", "errors"):
            value = clean.get(key)
            if value is None:
                clean[key] = []
            elif not isinstance(value, list):
                clean[key] = [str(value)]
        meta = clean.get("metadata")
        if meta is None or not isinstance(meta, dict):
            clean["metadata"] = {}
        return cls(**clean)  # type: ignore[arg-type]


def safe_slug(value: str) -> str:
    text = "".join(ch.lower() if ch.isalnum() else "-" for ch in str(value)).strip("-")
    while "--" in text:
        text = text.replace("--", "-")
    return text or "session"


class HarnessSessionStore:
    """Read/write normalized sessions under ``.llm-wiki/harness_sessions``."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.manifest_path = self.root / "manifest.json"

    def write_sessions(self, sessions: Iterable[HarnessSession]) -> Dict[str, object]:
        ordered = sorted(list(sessions), key=lambda s: (s.started_at or "", s.harness, s.slug))
        self.root.mkdir(parents=True, exist_ok=True)
        manifest_sessions: List[Dict[str, object]] = []
        for session in ordered:
            harness_dir = self.root / safe_slug(session.harness)
            harness_dir.mkdir(parents=True, exist_ok=True)
            json_path = harness_dir / f"{session.filename}.json"
            payload = session.to_dict()
            json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            md_path = harness_dir / f"{session.filename}.md"
            md_path.write_text(render_session_markdown(session), encoding="utf-8")
            manifest_sessions.append(_manifest_entry(session))
        manifest = {"version": "1", "sessions": manifest_sessions}
        self.manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return {"path": str(self.root), "sessions": len(ordered)}

    def list_sessions(self) -> List[HarnessSession]:
        if not self.root.exists():
            return []
        sessions: List[HarnessSession] = []
        for path in sorted(self.root.glob("*/*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                sessions.append(HarnessSession.from_dict(payload))
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                continue
        sessions.sort(key=lambda s: (s.started_at or "", s.harness, s.slug), reverse=True)
        return sessions


def _manifest_entry(session: HarnessSession) -> Dict[str, object]:
    return {
        "id": session.id,
        "title": session.title or session.slug,
        "harness": session.harness,
        "agent_label": session.agent_label,
        "project_name": session.project_name,
        "date": session.date,
        "model": session.model,
        "message_count": session.message_count,
        "tool_call_count": session.tool_call_count,
        "token_total": session.token_total,
        "href": session.href,
    }


def render_session_markdown(session: HarnessSession) -> str:
    lines = [
        f"# {session.title or session.slug}",
        "",
        f"- Harness: {session.harness}",
        f"- Agent: {session.agent_label}",
        f"- Project: {session.project_name}",
        f"- Date: {session.started_at}",
        f"- Model: {session.model or 'unknown'}",
        f"- Messages: {session.message_count}",
        f"- Tool calls: {session.tool_call_count}",
        "",
        "## Summary",
        "",
        session.summary or session.redacted_preview or "No summary yet.",
        "",
    ]
    if session.decisions:
        lines.extend(["## Decisions", ""])
        lines.extend(f"- {item}" for item in session.decisions)
        lines.append("")
    if session.files_touched:
        lines.extend(["## Files touched", ""])
        lines.extend(f"- `{item}`" for item in session.files_touched)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
