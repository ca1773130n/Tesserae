"""Normalized inbound agent-harness session history.

This module is intentionally separate from :mod:`tesserae.agent_harness`:
that module writes outbound harness config/instructions for agents, while this
one stores inbound historical sessions discovered from Claude Code, Codex, and
future local coding harnesses.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


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
        stem = safe_slug(self.slug or self.title or self.id)
        digest = hashlib.sha1(self.id.encode("utf-8")).hexdigest()[:8]
        return f"{self.date}-{stem}-{digest}"

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
    text = re.sub(r"-{2,}", "-", text)
    return text or "session"


def session_matches_project(session: HarnessSession, project_root: str | Path) -> bool:
    """Return true when a normalized session belongs to ``project_root``."""

    return _path_value_matches_project(session.project_root, Path(project_root).resolve())


class HarnessSessionStore:
    """Read/write normalized sessions under ``.tesserae/harness_sessions``."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.manifest_path = self.root / "manifest.json"

    def write_sessions(self, sessions: Iterable[HarnessSession]) -> Dict[str, object]:
        ordered = sorted(list(sessions), key=lambda s: (s.started_at or "", s.harness, s.slug))
        self.root.mkdir(parents=True, exist_ok=True)
        # Treat writes as an authoritative normalized import. Remove stale
        # session records first so changed filename schemes or deduped imports
        # cannot leave orphan pages/search entries behind on the next build.
        for stale in list(self.root.glob("*/*.json")) + list(self.root.glob("*/*.md")):
            try:
                stale.unlink()
            except OSError:
                pass
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


DEFAULT_HARNESS_ROOT_NAMES: Tuple[str, ...] = (".claude", ".codex")


def discover_harness_roots(home: str | Path | None = None) -> List[Path]:
    """Find local Claude Code and Codex config roots under ``home``.

    The default accounts live at ``~/.claude`` and ``~/.codex``, but users can
    keep multiple accounts in arbitrarily named sibling directories. Detect
    hidden home-directory candidates by harness-specific marker files/directories
    rather than by a fixed list of account names or suffixes.
    """

    base = Path(home).expanduser() if home is not None else Path.home()
    candidates: List[Path] = []
    for name in DEFAULT_HARNESS_ROOT_NAMES:
        candidates.append(base / name)
    try:
        candidates.extend(
            p for p in base.iterdir()
            if p.is_dir() and p.name.startswith(".")
        )
    except OSError:
        pass

    roots: List[Path] = []
    seen: set[Path] = set()
    for candidate in sorted(set(candidates)):
        if candidate in seen or not candidate.exists():
            continue
        if _root_supports_claude(candidate) or _root_supports_codex(candidate):
            seen.add(candidate)
            roots.append(candidate)
    return roots


def _root_supports_claude(root: Path) -> bool:
    return any((root / marker).exists() for marker in ("projects", "history.jsonl", "settings.json", "settings.local.json"))


def _root_supports_codex(root: Path) -> bool:
    return any((root / marker).exists() for marker in ("sessions", "history.jsonl", "config.toml", "auth.json"))


def discover_harness_sessions(
    project_root: str | Path,
    roots: Optional[Sequence[str | Path]] = None,
    harnesses: Optional[Sequence[str]] = None,
) -> List[HarnessSession]:
    """Discover local Claude Code / Codex JSONL sessions for ``project_root``.

    Discovery is intentionally project-scoped: a transcript must carry a strong
    cwd/workdir signal equal to the project root, or live in Claude Code's
    project-encoded directory for that root. Raw transcript text is not copied
    into the generated pages; the path is stored as provenance only.
    """

    project = Path(project_root).resolve()
    selected = {h.lower() for h in (harnesses or ("claude-code", "codex"))}
    scan_roots = [Path(r).expanduser() for r in roots] if roots is not None else discover_harness_roots()
    sessions: List[HarnessSession] = []
    seen: set[str] = set()
    for root in scan_roots:
        if not root.exists():
            continue
        if _root_supports_claude(root) and "claude-code" in selected:
            for session in _discover_claude_sessions(project, root):
                if session.id not in seen:
                    seen.add(session.id)
                    sessions.append(session)
        if _root_supports_codex(root) and "codex" in selected:
            for session in _discover_codex_sessions(project, root):
                if session.id not in seen:
                    seen.add(session.id)
                    sessions.append(session)
    sessions.sort(key=lambda s: (s.started_at or "", s.harness, s.slug), reverse=True)
    return sessions


def _is_claude_subagent_transcript(path: Path) -> bool:
    return "subagents" in path.parts


def _discover_claude_sessions(project: Path, root: Path) -> List[HarnessSession]:
    project_dir = root / "projects" / _claude_project_dir(project)
    candidates: List[Path] = []
    if project_dir.exists():
        candidates.extend(p for p in project_dir.rglob("*.jsonl") if not _is_claude_subagent_transcript(p))
    projects_root = root / "projects"
    if projects_root.exists():
        # Some account directories may encode paths differently, and history can
        # move between accounts. Scan all project transcripts but keep the
        # parser's strong cwd/path match before importing anything.
        candidates.extend(p for p in projects_root.rglob("*.jsonl") if not _is_claude_subagent_transcript(p))
    history = root / "history.jsonl"
    if history.exists():
        candidates.append(history)
    return [s for p in sorted(set(candidates)) if (s := _parse_claude_session(project, root, p))]


def _discover_codex_sessions(project: Path, root: Path) -> List[HarnessSession]:
    sessions_dir = root / "sessions"
    if not sessions_dir.exists():
        return []
    return [s for p in sorted(sessions_dir.rglob("*.jsonl")) if (s := _parse_codex_session(project, root, p))]


def _parse_jsonl(path: Path) -> List[Mapping[str, object]]:
    rows: List[Mapping[str, object]] = []
    try:
        with path.open(encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    rows.append(obj)
    except OSError:
        return []
    return rows


@dataclass
class _ClaudeRowsResult:
    project_match: bool
    session_id: str
    timestamps: List[str]
    title: str
    preview: str
    tools: List[str]
    commands: List[str]
    files: List[str]
    message_count: int
    branch: str
    model: str


def _parse_claude_rows(rows: Sequence[Mapping[str, object]], project: Path) -> _ClaudeRowsResult:
    """Single pass over a Claude JSONL transcript accumulating all session fields."""
    project = project.resolve()
    project_match = False
    session_id = ""
    timestamps: List[str] = []
    message_texts: List[str] = []
    tools: List[str] = []
    commands: List[str] = []
    files: List[str] = []
    message_count = 0
    branch = ""
    model = ""

    for row in rows:
        if not project_match:
            if _path_value_matches_project(row.get("cwd"), project):
                project_match = True
            else:
                payload_v = row.get("payload")
                if isinstance(payload_v, dict):
                    if _jsonish_contains_project_context(payload_v, project):
                        project_match = True
                    elif payload_v.get("type") == "function_call" and _jsonish_contains_project_context(payload_v.get("arguments"), project):
                        project_match = True
                if not project_match:
                    att_v = row.get("attachment")
                    if isinstance(att_v, dict) and _jsonish_contains_project_context(att_v, project):
                        project_match = True

        if not session_id:
            v = row.get("sessionId")
            if isinstance(v, str) and v:
                session_id = v

        ts = row.get("timestamp")
        if isinstance(ts, str):
            timestamps.append(ts)

        if not branch:
            v = row.get("gitBranch")
            if isinstance(v, str) and v:
                branch = v

        row_type = row.get("type")
        if row_type in {"user", "assistant"}:
            message_count += 1

        msg = row.get("message")
        if isinstance(msg, dict):
            if not model:
                v = msg.get("model") or msg.get("model_slug")
                if isinstance(v, str):
                    model = v
            content = msg.get("content")
            if row_type in {"user", "assistant"}:
                text = _content_to_text(content)
                if text:
                    message_texts.append(text)
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "tool_use":
                        tools.append(str(item.get("name") or "tool"))
                        _collect_activity_from_value(item.get("input"), project, commands, files)

        attachment = row.get("attachment")
        if isinstance(attachment, dict):
            command = attachment.get("command")
            if isinstance(command, str) and command.strip():
                commands.append(command.strip())
            atype = attachment.get("type")
            if isinstance(atype, str) and atype and atype not in {"hook_success", "hook_additional_context"}:
                tools.append(atype)
            _collect_activity_from_value(attachment, project, commands, files)
        _collect_activity_from_value(row, project, commands, files)

    title, preview = _title_and_preview(message_texts)
    return _ClaudeRowsResult(
        project_match=project_match,
        session_id=session_id,
        timestamps=timestamps,
        title=title,
        preview=preview,
        tools=tools,
        commands=commands,
        files=files,
        message_count=message_count,
        branch=branch,
        model=model,
    )


def _parse_claude_session(project: Path, root: Path, path: Path) -> Optional[HarnessSession]:
    rows = _parse_jsonl(path)
    if not rows:
        return None
    parsed = _parse_claude_rows(rows, project)
    if not parsed.project_match:
        return None
    session_id = parsed.session_id or path.stem
    timestamps = parsed.timestamps
    started_at = min(timestamps) if timestamps else ""
    ended_at = max(timestamps) if timestamps else ""
    title, preview = parsed.title, parsed.preview
    tools, commands, files = parsed.tools, parsed.commands, parsed.files
    message_count = parsed.message_count
    branch = parsed.branch
    model = parsed.model
    slug = safe_slug(title or session_id)
    subagents = _claude_subagent_summaries(project, root, path, session_id)
    metadata: Dict[str, object] = {"config_root": str(root), "transcript": str(path), "turns": _claude_turns(rows)}
    if subagents:
        metadata["subagents"] = subagents
    return HarnessSession(
        id=f"claude-code:{session_id}:{path.stem}",
        slug=slug,
        harness="claude-code",
        agent_label="Claude Code",
        project_name=project.name,
        project_root=str(project),
        started_at=started_at,
        ended_at=ended_at,
        branch=branch,
        model=model,
        title=title or f"Claude Code session {path.stem}",
        summary=preview,
        message_count=message_count,
        tool_call_count=len(set(tools)) + len(_dedupe(commands)),
        tools_used=sorted(set(tools)),
        files_touched=sorted(set(files)),
        commands_run=_dedupe(commands)[:50],
        raw_transcript_path=str(path),
        redacted_preview=preview,
        metadata=metadata,
    )


def _claude_subagent_summaries(project: Path, root: Path, parent_path: Path, parent_session_id: str) -> List[Dict[str, object]]:
    subagents_dir = parent_path.with_suffix("") / "subagents"
    if not subagents_dir.exists():
        return []
    summaries: List[Dict[str, object]] = []
    for path in sorted(subagents_dir.glob("*.jsonl")):
        rows = _parse_jsonl(path)
        if not rows or not _rows_match_project(rows, project):
            continue
        timestamps = [v for row in rows if isinstance((v := row.get("timestamp")), str)]
        title, preview = _title_and_preview_from_claude(rows)
        tools, commands, files = _claude_activity(rows, project)
        message_count = sum(1 for row in rows if row.get("type") in {"user", "assistant"})
        summaries.append({
            "id": f"claude-code:{parent_session_id}:{path.stem}",
            "title": title or f"Claude Code subagent {path.stem}",
            "started_at": min(timestamps) if timestamps else "",
            "ended_at": max(timestamps) if timestamps else "",
            "summary": preview,
            "message_count": message_count,
            "tool_call_count": len(set(tools)) + len(_dedupe(commands)),
            "tools_used": sorted(set(tools)),
            "files_touched": sorted(set(files)),
            "commands_run": _dedupe(commands)[:50],
            "raw_transcript_path": str(path),
        })
    return sorted(summaries, key=lambda item: str(item.get("started_at") or ""))


def _parse_codex_session(project: Path, root: Path, path: Path) -> Optional[HarnessSession]:
    rows = _parse_jsonl(path)
    if not rows or not _rows_match_project(rows, project):
        return None
    session_meta = next((r.get("payload") for r in rows if r.get("type") == "session_meta" and isinstance(r.get("payload"), dict)), {})
    session_id = str(session_meta.get("id") or path.stem) if isinstance(session_meta, dict) else path.stem
    timestamps = [v for row in rows if isinstance((v := row.get("timestamp")), str)]
    started_at = min(timestamps) if timestamps else (str(session_meta.get("timestamp", "")) if isinstance(session_meta, dict) else "")
    ended_at = max(timestamps) if timestamps else started_at
    title, preview = _title_and_preview_from_codex(rows)
    tools, commands, files = _codex_activity(rows, project)
    message_count = 0
    for row in rows:
        payload = row.get("payload")
        if isinstance(payload, dict) and payload.get("type") == "message" and payload.get("role") in {"user", "assistant"}:
            message_count += 1
    slug = safe_slug(title or session_id)
    model = ""
    if isinstance(session_meta, dict):
        model = str(session_meta.get("model") or session_meta.get("model_slug") or session_meta.get("model_provider") or "")
    return HarnessSession(
        id=f"codex:{session_id}",
        slug=slug,
        harness="codex",
        agent_label="Codex",
        project_name=project.name,
        project_root=str(project),
        started_at=started_at,
        ended_at=ended_at,
        model=model,
        title=title or f"Codex session {path.stem}",
        summary=preview,
        message_count=message_count,
        tool_call_count=len(set(tools)),
        tools_used=sorted(set(tools)),
        files_touched=sorted(set(files)),
        commands_run=_dedupe(commands)[:50],
        raw_transcript_path=str(path),
        redacted_preview=preview,
        metadata={"config_root": str(root), "transcript": str(path), "turns": _codex_turns(rows)},
    )


def _claude_project_dir(project: Path) -> str:
    return str(project).replace("/", "-")


def _claude_path_matches_project(path: Path, project: Path) -> bool:
    return _claude_project_dir(project) in path.parts


def _rows_match_project(rows: Sequence[Mapping[str, object]], project: Path) -> bool:
    project = project.resolve()
    for row in rows:
        if _path_value_matches_project(row.get("cwd"), project):
            return True
        payload = row.get("payload")
        if isinstance(payload, dict):
            if _jsonish_contains_project_context(payload, project):
                return True
            if payload.get("type") == "function_call":
                args = payload.get("arguments")
                if _jsonish_contains_project_context(args, project):
                    return True
        attachment = row.get("attachment")
        if isinstance(attachment, dict) and _jsonish_contains_project_context(attachment, project):
            return True
    return False


def _path_value_matches_project(value: object, project: Path) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        return Path(value).expanduser().resolve() == project
    except OSError:
        return value == str(project)


def _jsonish_contains_project_context(value: object, project: Path) -> bool:
    """Return true only for explicit cwd/workdir-style project context.

    Plain transcript text or shell commands that merely mention the focused
    project path are not enough to import a session. A discovered transcript must
    declare that the harness was running in the plugged-in project root.
    """

    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return False
        return _jsonish_contains_project_context(decoded, project)
    if isinstance(value, dict):
        for key in ("cwd", "workdir", "project_root", "root"):
            if _path_value_matches_project(value.get(key), project):
                return True
        return any(_jsonish_contains_project_context(v, project) for v in value.values())
    if isinstance(value, list):
        return any(_jsonish_contains_project_context(v, project) for v in value)
    return False


def _first_str(rows: Sequence[Mapping[str, object]], key: str) -> str:
    for row in rows:
        value = row.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _first_message_model(rows: Sequence[Mapping[str, object]]) -> str:
    for row in rows:
        msg = row.get("message")
        if isinstance(msg, dict):
            value = msg.get("model") or msg.get("model_slug")
            if isinstance(value, str):
                return value
    return ""


_REDACT_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|token|secret|password|passwd|authorization|bearer)\s*[:=]\s*[^\s,;]+"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+\-/=]+"),
    re.compile(r"sk-[A-Za-z0-9]{12,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]+"),
)


def _redact_text(text: str) -> str:
    if not text:
        return ""
    redacted = text
    for pattern in _REDACT_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


def _turn_text(text: str, limit: int = 2400) -> str:
    clean = _redact_text(text.strip())
    if len(clean) <= limit:
        return clean
    return clean[:limit].rstrip() + "…"


def _claude_turns(rows: Sequence[Mapping[str, object]], limit: int = 300) -> List[Dict[str, object]]:
    turns: List[Dict[str, object]] = []
    for row in rows:
        role = row.get("type")
        if role not in {"user", "assistant"}:
            continue
        timestamp = row.get("timestamp") if isinstance(row.get("timestamp"), str) else ""
        msg = row.get("message")
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        text = _content_to_text(content)
        if text and not text.startswith("<environment_context>"):
            turns.append({"role": str(role), "timestamp": timestamp, "text": _turn_text(text)})
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "tool_use":
                    name = str(item.get("name") or "tool")
                    tool_text = _turn_text(json.dumps(item.get("input", {}), ensure_ascii=False, sort_keys=True), limit=1200)
                    turns.append({"role": "tool", "timestamp": timestamp, "name": name, "text": tool_text})
        if len(turns) >= limit:
            break
    return turns


def _codex_turns(rows: Sequence[Mapping[str, object]], limit: int = 300) -> List[Dict[str, object]]:
    turns: List[Dict[str, object]] = []
    for row in rows:
        timestamp = row.get("timestamp") if isinstance(row.get("timestamp"), str) else ""
        payload = row.get("payload")
        if not isinstance(payload, dict):
            continue
        if payload.get("type") == "message" and payload.get("role") in {"user", "assistant"}:
            text = _content_to_text(payload.get("content"))
            if text and not text.startswith("<environment_context>") and not text.startswith("<permissions instructions>"):
                turns.append({"role": str(payload.get("role")), "timestamp": timestamp, "text": _turn_text(text)})
        elif payload.get("type") == "function_call":
            name = str(payload.get("name") or "function_call")
            tool_text = _turn_text(str(payload.get("arguments") or ""), limit=1200)
            if tool_text:
                turns.append({"role": "tool", "timestamp": timestamp, "name": name, "text": tool_text})
        if len(turns) >= limit:
            break
    return turns


def _title_and_preview_from_claude(rows: Sequence[Mapping[str, object]]) -> Tuple[str, str]:
    texts: List[str] = []
    for row in rows:
        if row.get("type") not in {"user", "assistant"}:
            continue
        msg = row.get("message")
        if isinstance(msg, dict):
            text = _content_to_text(msg.get("content"))
            if text:
                texts.append(text)
    return _title_and_preview(texts)


def _title_and_preview_from_codex(rows: Sequence[Mapping[str, object]]) -> Tuple[str, str]:
    texts: List[str] = []
    for row in rows:
        payload = row.get("payload")
        if isinstance(payload, dict) and payload.get("type") == "message" and payload.get("role") in {"user", "assistant"}:
            text = _content_to_text(payload.get("content"))
            if text and not text.startswith("<environment_context>") and not text.startswith("<permissions instructions>"):
                texts.append(text)
    return _title_and_preview(texts)


def _title_and_preview(texts: Sequence[str]) -> Tuple[str, str]:
    if not texts:
        return "", ""
    first_raw = texts[0].strip()
    title = _clean_text(first_raw.splitlines()[0]).strip("# ")[:96]
    preview = _clean_text("\n\n".join(texts[:4]))[:1200]
    return title, preview


def _content_to_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text") or item.get("input_text") or item.get("output_text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return ""


def _claude_activity(rows: Sequence[Mapping[str, object]], project: Path) -> Tuple[List[str], List[str], List[str]]:
    tools: List[str] = []
    commands: List[str] = []
    files: List[str] = []
    for row in rows:
        msg = row.get("message")
        if isinstance(msg, dict):
            content = msg.get("content")
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "tool_use":
                        name = str(item.get("name") or "tool")
                        tools.append(name)
                        _collect_activity_from_value(item.get("input"), project, commands, files)
        attachment = row.get("attachment")
        if isinstance(attachment, dict):
            command = attachment.get("command")
            if isinstance(command, str) and command.strip():
                commands.append(command.strip())
            atype = attachment.get("type")
            if isinstance(atype, str) and atype and atype not in {"hook_success", "hook_additional_context"}:
                tools.append(atype)
            _collect_activity_from_value(attachment, project, commands, files)
        _collect_activity_from_value(row, project, commands, files)
    return tools, commands, files


def _codex_activity(rows: Sequence[Mapping[str, object]], project: Path) -> Tuple[List[str], List[str], List[str]]:
    tools: List[str] = []
    commands: List[str] = []
    files: List[str] = []
    for row in rows:
        payload = row.get("payload")
        if isinstance(payload, dict):
            if payload.get("type") == "function_call":
                name = str(payload.get("name") or "function_call")
                tools.append(name)
                _collect_activity_from_value(payload.get("arguments"), project, commands, files)
            elif payload.get("type") == "message":
                _collect_activity_from_value(payload.get("content"), project, commands, files)
            else:
                _collect_activity_from_value(payload, project, commands, files)
    return tools, commands, files


def _collect_activity_from_value(value: object, project: Path, commands: List[str], files: List[str]) -> None:
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            decoded = None
        if decoded is not None:
            _collect_activity_from_value(decoded, project, commands, files)
        text = value
        for key in ("cmd", "command"):
            # handled below for dicts; regex catches serialized snippets.
            pass
        files.extend(_extract_project_files(text, project))
        return
    if isinstance(value, dict):
        for key in ("cmd", "command"):
            item = value.get(key)
            if isinstance(item, str) and item.strip():
                commands.append(item.strip())
        for key in ("file_path", "path"):
            item = value.get(key)
            if isinstance(item, str):
                files.extend(_extract_project_files(item, project))
        for item in value.values():
            _collect_activity_from_value(item, project, commands, files)
    elif isinstance(value, list):
        for item in value:
            _collect_activity_from_value(item, project, commands, files)


def _extract_project_files(text: str, project: Path) -> List[str]:
    out: List[str] = []
    if not text:
        return out
    project_str = re.escape(str(project))
    for match in re.finditer(project_str + r"/([^\s\"'`<>),]+)", text):
        rel = match.group(1).strip()
        if rel and not rel.startswith(".tesserae/"):
            out.append(rel)
    for match in re.finditer(r"\b(?:tesserae|tests|docs|data)/[\w./-]+", text):
        out.append(match.group(0).rstrip(".,);:"))
    return _dedupe(out)[:100]


def _dedupe(items: Iterable[str]) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _clean_text(text: str) -> str:
    text = re.sub(r"<[^>]{1,80}>", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


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
