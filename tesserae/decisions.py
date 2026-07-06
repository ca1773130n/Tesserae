"""Retrieve decisions across projects + time.

Two sources: **deterministic human decisions** parsed from Claude Code's
``AskUserQuestion`` tool (the explicit question + the option the user chose), and
**agent decisions** mined from the in-window conversation by the LLM. Reuses the
activity-summary window resolution, all-accounts transcript discovery, excerpt
rendering, and LLM client so both features stay consistent.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Mapping, Optional, Sequence

from tesserae.activity_summary import (
    Window,
    in_window,
    iter_project_transcripts,
    parse_ts,
    render_session_excerpts,
    resolve_windows,
    scan_messages,
    _resolve_projects,
    _summary_llm_client,
)
from tesserae.harness_sessions import _parse_jsonl

logger = logging.getLogger(__name__)

# The `"<question>"="<answer>"` pairs inside an AskUserQuestion tool_result string
# ("Your questions have been answered: \"Q\"=\"A\", ...").
_ANSWER_RE = re.compile(r'"([^"]+)"="([^"]+)"')


@dataclass
class Decision:
    """One decision — an explicit human choice or an agent decision."""

    ts: datetime
    source: str  # "human" | "agent"
    project: str
    session_id: str
    question: str
    answer: str
    options: List[str] = field(default_factory=list)
    header: str = ""


def _tool_result_text(content: object) -> str:
    """Flatten an AskUserQuestion tool_result's content (string OR list of blocks)."""
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


def parse_human_decisions(
    rows: Sequence[Mapping[str, object]],
    project: str,
    session_id: str,
    window: Window,
) -> List[Decision]:
    """Deterministic human decisions from a transcript's raw rows.

    Matches each ``AskUserQuestion`` ``tool_use`` to its ``tool_result`` (by
    ``tool_use_id``), parses the ``"Q"="A"`` answer pairs, and emits one
    :class:`Decision` (``source="human"``) per answered question — dated by the
    tool_result row's timestamp and kept only when that ts ∈ ``window``.
    """
    tool_uses: dict = {}  # tool_use_id -> [question dicts]
    for row in rows:
        content = (row.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for item in content:
            if (
                isinstance(item, dict)
                and item.get("type") == "tool_use"
                and item.get("name") == "AskUserQuestion"
            ):
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
            questions = tool_uses.get(item.get("tool_use_id"))
            if questions is None or not ts or not in_window(ts, window):
                continue
            answers = dict(_ANSWER_RE.findall(_tool_result_text(item.get("content"))))
            for q in questions:
                qtext = str(q.get("question") or "")
                ans = answers.get(qtext)
                if not ans:
                    continue
                out.append(
                    Decision(
                        ts=ts,
                        source="human",
                        project=project,
                        session_id=session_id,
                        question=qtext,
                        answer=ans,
                        options=[str(o.get("label") or "") for o in (q.get("options") or [])],
                        header=str(q.get("header") or ""),
                    )
                )
    return out


_AGENT_SYSTEM = (
    "You extract EXPLICIT decisions from a developer's agent-session excerpts: "
    "choices made, trade-offs settled, direction changes — the kind a teammate "
    "would want recorded. Output ONE decision per line as `<decision> :: <one-line "
    "rationale>`. No headers, no numbering, no prose. If a session made no real "
    "decision, output nothing. NEVER invent a decision not supported by the excerpts."
)


def extract_agent_decisions(
    excerpts: str, client: object, project: str, ts: datetime
) -> List[Decision]:
    """LLM-mined agent decisions from conversation ``excerpts``.

    ``client`` exposes ``complete_text(system, user) -> str``; each returned line
    ``<decision> :: <rationale>`` becomes a :class:`Decision` (``source="agent"``).
    All share ``ts`` (the session's earliest in-window turn) since the LLM does not
    date individual decisions. An empty reply yields ``[]``.
    """
    reply = (client.complete_text(system=_AGENT_SYSTEM, user=excerpts) or "").strip()
    out: List[Decision] = []
    for line in reply.splitlines():
        line = line.strip().lstrip("-*").strip()
        if "::" not in line:
            continue
        decision, _, rationale = line.partition("::")
        decision = decision.strip()
        if not decision:
            continue
        out.append(
            Decision(
                ts=ts,
                source="agent",
                project=project,
                session_id="",
                question=decision,
                answer=rationale.strip(),
            )
        )
    return out


def gather_decisions(
    windows: Sequence[Window],
    project_names: Optional[Sequence[str]] = None,
    *,
    include_agent: bool = True,
    turn_limit: int = 100_000,
) -> List[Decision]:
    """All decisions for the resolved projects within ``windows``.

    Human decisions are parsed deterministically from every in-window Claude
    transcript (``AskUserQuestion``); when ``include_agent`` and an LLM client is
    available, agent decisions are mined once per project from that project's
    in-window conversation excerpts. Sorted by ``(ts, project, source)``.
    """
    projects = _resolve_projects(list(project_names) if project_names else None)
    out: List[Decision] = []

    # Human (deterministic) — AskUserQuestion is Claude-Code-only.
    for name, harness, path, key in iter_project_transcripts(projects, windows):
        if harness != "claude-code":
            continue
        rows = _parse_jsonl(path)
        for window in windows:
            out.extend(parse_human_decisions(rows, name, key, window))

    # Agent (LLM) — best-effort; missing client / any error -> human-only.
    if include_agent and projects:
        client = None
        try:
            client = _summary_llm_client(str(projects[0][1]))
        except Exception as exc:  # noqa: BLE001 - best-effort narration
            logger.warning("decisions: no LLM client for agent decisions: %s", exc)
        if client is not None:
            messages_by = scan_messages(projects, windows, turn_limit=turn_limit)
            for name, _root in projects:
                msgs = [m for bucket in messages_by.get(name, {}).values() for m in bucket]
                if not msgs:
                    continue
                excerpts = render_session_excerpts(msgs)
                if not excerpts.strip():
                    continue
                try:
                    out.extend(
                        extract_agent_decisions(excerpts, client, name, min(m.ts for m in msgs))
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("decisions: agent extraction failed for %s: %s", name, exc)

    out.sort(key=lambda d: (d.ts, d.project, d.source))
    return out


def render_decisions(decisions: Sequence[Decision]) -> str:
    """Markdown grouped by ``## <project>`` then Human / Agent decision lists."""
    by_project: dict = {}
    for d in decisions:
        by_project.setdefault(d.project, {"human": [], "agent": []})[d.source].append(d)

    lines: List[str] = []
    for project in sorted(by_project):
        groups = by_project[project]
        lines.append(f"## {project}")
        lines.append("")
        lines.append("### Human decisions")
        human = sorted(groups["human"], key=lambda d: d.ts)
        if human:
            for d in human:
                opts = f"  _(options: {' · '.join(d.options)})_" if d.options else ""
                lines.append(
                    f"- **{d.header or 'decision'}**: {d.question} → **{d.answer}**"
                    f"{opts}  · {d.ts.strftime('%Y-%m-%d %H:%M')}"
                )
        else:
            lines.append("_none_")
        lines.append("")
        lines.append("### Agent decisions")
        agent = sorted(groups["agent"], key=lambda d: d.ts)
        if agent:
            for d in agent:
                why = f" — {d.answer}" if d.answer else ""
                lines.append(f"- {d.question}{why}  · {d.ts.strftime('%Y-%m-%d')}")
        else:
            lines.append("_none_")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
