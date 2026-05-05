"""Static-site renderers for inbound harness session history."""

from __future__ import annotations

import html
import json
from collections import Counter
from typing import Dict, Iterable, List

from ..harness_sessions import HarnessSession
from .components import breadcrumbs, page_shell, toc
from .markdown import render_markdown
from .search import token_set


def _esc(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _subagents(session: HarnessSession) -> List[Dict[str, object]]:
    items = session.metadata.get("subagents") if isinstance(session.metadata, dict) else []
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def _subagent_count_label(session: HarnessSession) -> str:
    count = len(_subagents(session))
    if count == 0:
        return "—"
    return f"{count} subagent" + ("" if count == 1 else "s")


def _turns(session: HarnessSession) -> List[Dict[str, object]]:
    items = session.metadata.get("turns") if isinstance(session.metadata, dict) else []
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict) and str(item.get("text") or "").strip()]


def _role_label(turn: Dict[str, object]) -> str:
    role = str(turn.get("role") or "message").replace("_", " ").strip().title()
    if role.lower() == "tool" and turn.get("name"):
        return f"Tool · {turn.get('name')}"
    return role or "Message"


def _turn_anchor(index: int) -> str:
    return f"turn-{index}"


def _is_tool_turn(turn: Dict[str, object]) -> bool:
    return str(turn.get("role") or "").strip().lower() == "tool"


def _is_conversation_turn(turn: Dict[str, object]) -> bool:
    role = str(turn.get("role") or "").strip().lower()
    return role in {"user", "assistant"}


def _conversation_groups(session: HarnessSession) -> List[Dict[str, object]]:
    groups: List[Dict[str, object]] = []
    last_assistant: Dict[str, object] | None = None
    for turn in _turns(session):
        if _is_tool_turn(turn):
            if last_assistant is not None:
                tools = last_assistant.setdefault("tools", [])
                if isinstance(tools, list):
                    tools.append(turn)
            continue
        if not _is_conversation_turn(turn):
            continue
        group = {"turn": turn, "tools": []}
        groups.append(group)
        role = str(turn.get("role") or "").strip().lower()
        last_assistant = group if role == "assistant" else None
    return groups


def _render_turn_markdown(text: str) -> str:
    rendered, _ = render_markdown(text or "")
    return rendered or "<p></p>"


def _turn_summary(turn: Dict[str, object], limit: int = 110) -> str:
    text = " ".join(str(turn.get("text") or "").split())
    if not text and turn.get("name"):
        text = str(turn.get("name"))
    if len(text) <= limit:
        return text or "Turn"
    return text[:limit].rstrip() + "…"


def session_search_entries(sessions: Iterable[HarnessSession]) -> List[Dict[str, object]]:
    entries: List[Dict[str, object]] = []
    for session in sessions:
        text = " ".join([
            session.title,
            session.summary,
            session.project_name,
            session.harness,
            session.agent_label,
            session.model,
            " ".join(session.tools_used),
            " ".join(session.files_touched),
            " ".join(session.decisions),
        ])
        tokens = token_set(text)
        entries.append({
            "id": session.id,
            "title": session.title or session.slug,
            "kind": "session",
            "type": "session",
            "href": session.href,
            "summary": session.summary or session.redacted_preview,
            "source_path": session.raw_transcript_path,
            "tokens": tokens,
            "len": len(tokens),
            "created_ts": None,
            "project": session.project_name,
            "model": session.model,
            "harness": session.harness,
            "date": session.date,
            "tools": list(session.tools_used),
        })
    return sorted(entries, key=lambda e: (str(e["date"]), str(e["title"])), reverse=True)


def _session_counts(sessions: List[HarnessSession]) -> Dict[str, int]:
    return {"sessions": len(sessions)}


def _format_number(value: int) -> str:
    return f"{int(value):,}"


def _shorten(text: str, limit: int = 700) -> str:
    clean = " ".join((text or "").split())
    if len(clean) <= limit:
        return clean
    return clean[:limit].rstrip() + "…"


def render_sessions_index(site_title: str, sessions: List[HarnessSession]) -> str:
    rows = []
    for session in sorted(sessions, key=lambda s: (s.started_at or "", s.title), reverse=True):
        rows.append(
            "<tr>"
            f"<td><a class='session-link' href='{_esc(session.safe_project)}/{_esc(session.filename)}.html'>{_esc(session.title or session.slug)}</a>"
            f"<div class='muted small'>{_esc(session.summary or session.redacted_preview)[:180]}</div></td>"
            f"<td>{_esc(session.agent_label or session.harness)}</td>"
            f"<td>{_esc(session.project_name)}</td>"
            f"<td>{_esc(session.date)}</td>"
            f"<td><code>{_esc(session.model or 'unknown')}</code></td>"
            f"<td>{_format_number(session.message_count)}</td>"
            f"<td>{_format_number(session.tool_call_count)}</td>"
            f"<td>{_esc(_subagent_count_label(session))}</td>"
            "</tr>"
        )
    table_body = "".join(rows) or "<tr><td colspan='8'>No harness sessions ingested yet.</td></tr>"
    tool_counts = Counter(tool for session in sessions for tool in session.tools_used)
    harness_counts = Counter(session.harness for session in sessions)
    subagent_total = sum(len(_subagents(session)) for session in sessions)
    body = f"""
<div class="session-page session-index-page">
<section class="hero session-hero" aria-label="Agent session memory">
  <p class="eyebrow">Project memory · agent history</p>
  <h1>All sessions</h1>
  <p class="lead">Browse top-level agent sessions attached to this project. Subagent trees stay collapsed under their parent runs until you choose to inspect them.</p>
</section>
<section class="stats" id="stats" aria-label="Session stats">
  <div class="stat"><b>{_format_number(len(sessions))}</b><span>Main sessions</span></div>
  <div class="stat"><b>{_format_number(subagent_total)}</b><span>Sub-agent runs</span></div>
  <div class="stat"><b>{_format_number(len(harness_counts))}</b><span>Harnesses</span></div>
  <div class="stat"><b>{_format_number(sum(session.tool_call_count for session in sessions))}</b><span>Tool calls</span></div>
</section>
<section class="panel">
  <h2>Harness mix</h2>
  <p class="muted">{_esc(' · '.join(f'{k}: {v}' for k, v in sorted(harness_counts.items())) or 'No harnesses yet.')}</p>
  <p class="muted">Top tools: {_esc(', '.join(f'{k} {v}' for k, v in tool_counts.most_common(8)) or 'None recorded.')}</p>
</section>
<section class="panel" id="sessions">
  <div class="table-scroll"><table class="node-table session-table">
    <thead><tr><th>Session</th><th>Agent</th><th>Project</th><th>Date</th><th>Model</th><th>Msgs</th><th>Tools</th><th>Subagents</th></tr></thead>
    <tbody>{table_body}</tbody>
  </table></div>
</section>
</div>
"""
    return page_shell(
        title="Sessions",
        head="",
        body=body,
        depth=1,
        active="sessions",
        site_title=site_title,
        counts=_session_counts(sessions),
        main_variant="wide",
        breadcrumbs_html=breadcrumbs([("Home", "../index.html"), ("Sessions", "")]),
        toc_html=toc([(2, "Stats", "stats"), (2, "Sessions", "sessions")]),
    )


def _render_tool_details(tools: List[Dict[str, object]]) -> str:
    if not tools:
        return ""
    items: List[str] = []
    for idx, tool in enumerate(tools, start=1):
        name = str(tool.get("name") or "tool")
        timestamp = str(tool.get("timestamp") or "")
        text = str(tool.get("text") or "")
        items.append(
            "<article class='session-tool-use'>"
            "<header class='session-tool-use-header'>"
            f"<span>#{idx}</span>"
            f"<span>{_esc(name)}</span>"
            f"<time>{_esc(timestamp)}</time>"
            "</header>"
            f"<pre class='session-tool-use-text'>{_esc(text)}</pre>"
            "</article>"
        )
    return (
        f"<details class='session-tool-details'><summary>Tool use ({len(tools)})</summary>"
        f"<div class='session-tool-use-list'>{''.join(items)}</div>"
        "</details>"
    )


def _render_conversation(session: HarnessSession) -> str:
    groups = _conversation_groups(session)
    if not groups:
        return (
            "<section class='panel session-conversation' id='conversation'>"
            "<h2>Turn-by-turn conversation</h2>"
            "<p class='muted'>No normalized user/assistant transcript is attached yet. Re-run session discovery/import to populate redacted turns.</p>"
            "</section>"
        )
    rows: List[str] = []
    for idx, group in enumerate(groups, start=1):
        turn = group["turn"]
        if not isinstance(turn, dict):
            continue
        tools = group.get("tools") if isinstance(group.get("tools"), list) else []
        role = str(turn.get("role") or "message").strip().lower() or "message"
        timestamp = str(turn.get("timestamp") or "")
        text = str(turn.get("text") or "")
        anchor = _turn_anchor(idx)
        rows.append(
            f"<article class='session-turn session-turn--{_esc(role)}' id='{_esc(anchor)}'>"
            "<header class='session-turn-header'>"
            f"<span class='session-turn-index'>#{idx}</span>"
            f"<span class='session-turn-role'>{_esc(_role_label(turn))}</span>"
            f"<time>{_esc(timestamp)}</time>"
            "</header>"
            f"<div class='session-turn-text markdown-body'>{_render_turn_markdown(text)}</div>"
            f"{_render_tool_details(tools)}"
            "</article>"
        )
    return (
        "<section class='panel session-conversation' id='conversation'>"
        "<h2>Turn-by-turn conversation</h2>"
        "<p class='muted'>Redacted user/assistant transcript turns, with assistant tool use collapsed under its response.</p>"
        f"<div class='session-turn-list'>{''.join(rows)}</div>"
        "</section>"
    )


def _render_turn_rail(session: HarnessSession) -> str:
    groups = _conversation_groups(session)
    items: List[str] = []
    for idx, group in enumerate(groups[:160], start=1):
        turn = group.get("turn") if isinstance(group, dict) else None
        if not isinstance(turn, dict):
            continue
        anchor = _turn_anchor(idx)
        role = _role_label(turn)
        summary = _turn_summary(turn)
        items.append(
            f"<li data-session-turn-target=\"{_esc(anchor)}\">"
            f"<a href=\"#{_esc(anchor)}\">"
            f"<span class='session-turn-nav-index'>#{idx}</span>"
            f"<span class='session-turn-nav-role'>{_esc(role)}</span>"
            f"<span class='session-turn-nav-summary'>{_esc(summary)}</span>"
            "</a></li>"
        )
    if len(groups) > 160:
        items.append(f"<li class='session-turn-nav-more'>+{len(groups) - 160} more turns in the page</li>")
    body = "".join(items) or "<li class='muted'>No normalized user/assistant turns attached.</li>"
    return (
        "<aside class='rail session-detail-rail' id='rail' aria-label='Conversation turns'>"
        "<div class='rail-title-row'>"
        "<div class='rail-section-label'>Conversation turns</div>"
        "<a class='session-rail-back' href='../index.html'>All sessions</a>"
        "</div>"
        "<nav class='session-turn-nav' aria-label='Conversation turns'>"
        f"<ol>{body}</ol>"
        "</nav></aside>"
    )


def _render_subagent_tree(session: HarnessSession) -> str:
    children = _subagents(session)
    if not children:
        return "<section class='panel'><h2>Subagent sessions</h2><p class='muted'>No subagent transcripts attached.</p></section>"
    rows = []
    for child in children:
        files = child.get("files_touched") if isinstance(child.get("files_touched"), list) else []
        commands = child.get("commands_run") if isinstance(child.get("commands_run"), list) else []
        files_html = "".join(f"<li><code>{_esc(item)}</code></li>" for item in files[:12]) or "<li class='muted'>No files recorded.</li>"
        commands_html = "".join(f"<li><code>{_esc(item)}</code></li>" for item in commands[:8]) or "<li class='muted'>No commands recorded.</li>"
        rows.append(
            "<li class='subagent-node'>"
            f"<h3>{_esc(child.get('title') or child.get('id') or 'Subagent session')}</h3>"
            f"<p class='muted'>{_esc(child.get('started_at') or 'unknown time')} · "
            f"{_esc(child.get('message_count') or 0)} msgs · {_esc(child.get('tool_call_count') or 0)} tools</p>"
            f"<p>{_esc(child.get('summary') or 'No summary yet.')}</p>"
            f"<details><summary>Files and commands</summary><h4>Files touched</h4><ul>{files_html}</ul><h4>Commands run</h4><ul>{commands_html}</ul></details>"
            "</li>"
        )
    return (
        "<section class='panel subagent-tree' id='subagents'>"
        f"<details><summary>Subagent sessions ({len(children)})</summary>"
        "<p class='muted'>Child agent transcripts are hidden by default so the top-level session list stays focused.</p>"
        f"<ul>{''.join(rows)}</ul>"
        "</details></section>"
    )


def render_session_detail(site_title: str, session: HarnessSession, session_count: int = 0) -> str:
    def list_items(items: List[str], code: bool = False, limit: int = 24) -> str:
        if not items:
            return "<p class='muted'>None recorded.</p>"
        visible = items[:limit]
        if code:
            base = "<ul>" + "".join(f"<li><code>{_esc(item)}</code></li>" for item in visible) + "</ul>"
        else:
            base = "<ul>" + "".join(f"<li>{_esc(item)}</li>" for item in visible) + "</ul>"
        if len(items) <= limit:
            return base
        hidden = items[limit:]
        if code:
            rest = "<ul>" + "".join(f"<li><code>{_esc(item)}</code></li>" for item in hidden) + "</ul>"
        else:
            rest = "<ul>" + "".join(f"<li>{_esc(item)}</li>" for item in hidden) + "</ul>"
        return base + f"<details><summary>Show {len(hidden)} more</summary>{rest}</details>"

    metadata = {
        "id": session.id,
        "kind": "session",
        "project": session.project_name,
        "harness": session.harness,
        "model": session.model,
        "date": session.date,
    }
    outcome = _shorten(session.summary or session.redacted_preview or "No outcome summary recorded yet.", 720)
    body = f"""
<div class="session-page session-detail-page">
<script type="application/json" id="llmwiki-metadata">{_esc(json.dumps(metadata, ensure_ascii=False, sort_keys=True))}</script>
<section class="hero session-hero" aria-label="Session Summary">
  <p class="eyebrow">{_esc(session.agent_label or session.harness)} · { _esc(session.date) } · { _esc(session.branch or 'unknown branch') }</p>
  <h1>Session Summary: {_esc(session.title or session.slug)}</h1>
  <p class="lead"><strong>Main outcome:</strong> {_esc(outcome)}</p>
</section>
<section class="stats" id="timeline-size" aria-label="Timeline and size">
  <div class="stat"><b>{_format_number(session.message_count)}</b><span>Messages</span></div>
  <div class="stat"><b>{_format_number(session.tool_call_count)}</b><span>Tool calls</span></div>
  <div class="stat"><b>{_format_number(session.token_total)}</b><span>Tokens</span></div>
  <div class="stat"><b>{_format_number(len(_subagents(session)))}</b><span>Subagents</span></div>
</section>
<section class="panel" id="summary">
  <h2>High-Level Summary</h2>
  <p>{_esc(outcome)}</p>
</section>
<section class="panel" id="metadata">
  <h2>Timeline &amp; size</h2>
  <dl class="meta-grid">
    <dt>Project</dt><dd>{_esc(session.project_name)}</dd>
    <dt>Started</dt><dd>{_esc(session.started_at or 'unknown')}</dd>
    <dt>Ended</dt><dd>{_esc(session.ended_at or 'unknown')}</dd>
    <dt>Model</dt><dd><code>{_esc(session.model or 'unknown')}</code></dd>
    <dt>Harness</dt><dd>{_esc(session.harness)}</dd>
    <dt>Raw transcript</dt><dd><code>{_esc(session.raw_transcript_path)}</code></dd>
  </dl>
</section>
<section class="panel" id="decisions"><h2>Key decisions</h2>{list_items(session.decisions)}</section>
<section class="panel" id="files"><h2>Files touched</h2>{list_items(session.files_touched, code=True, limit=18)}</section>
<section class="panel" id="commands"><h2>Commands run</h2>{list_items(session.commands_run, code=True, limit=12)}</section>
<section class="panel" id="tools"><h2>Tools used</h2>{list_items(session.tools_used, limit=24)}</section>
{_render_conversation(session)}
{_render_subagent_tree(session)}
<section class="panel" id="preview"><h2>Redacted preview</h2><pre>{_esc(session.redacted_preview)}</pre></section>
</div>
"""
    return page_shell(
        title=f"Session: {session.title or session.slug}",
        head="",
        body=body,
        depth=2,
        active="sessions",
        site_title=site_title,
        main_variant="session",
        counts={"sessions": session_count or 1},
        breadcrumbs_html=breadcrumbs([("Home", "../../index.html"), ("Sessions", "../index.html"), (session.title or session.slug, "")]),
        rail_html=_render_turn_rail(session),
        toc_html=toc([
            (2, "High-Level Summary", "summary"),
            (2, "Timeline & size", "metadata"),
            (2, "Files touched", "files"),
            (2, "Commands run", "commands"),
            (2, "Turn-by-turn conversation", "conversation"),
            (2, "Subagent sessions", "subagents"),
        ]),
    )


def _page(title: str, body: str, depth: int = 0) -> str:
    prefix = "../" * max(depth, 0)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_esc(title)}</title>
  <link rel="stylesheet" href="{_esc(prefix)}assets/style.css">
</head>
<body>
{body}
</body>
</html>
"""
