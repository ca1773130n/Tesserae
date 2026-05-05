"""Static-site renderers for inbound harness session history."""

from __future__ import annotations

import html
import json
import re
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


_RAW_COMMAND_FIELD_RE = re.compile(r"<(command-(?:name|message|args))>(.*?)</\1>", re.DOTALL)
_RAW_COMMAND_TAG_RE = re.compile(
    r"(?:<command-(?:name|message|args)>.*?</command-(?:name|message|args)>\s*){2,3}",
    re.DOTALL,
)
_COMMAND_FIELD_RE = re.compile(r"&lt;(command-(?:name|message|args))&gt;(.*?)&lt;/\1&gt;", re.DOTALL)
_COMMAND_TAG_RE = re.compile(
    r"(?:&lt;command-(?:name|message|args)&gt;.*?&lt;/command-(?:name|message|args)&gt;\s*){2,3}",
    re.DOTALL,
)
_TAG_PAIR_RE = re.compile(r"&lt;([a-z][A-Za-z0-9_-]{1,50})&gt;(.*?)&lt;/\1&gt;", re.DOTALL)
_RAW_TAG_PAIR_RE = re.compile(r"<([a-z][A-Za-z0-9_-]{1,50})>(.*?)</\1>", re.DOTALL)
_PATH_TOKEN_RE = re.compile(
    r"(?<![\w>&])((?:~?/?[A-Za-z0-9_.@%+=:,~-]+/)+[A-Za-z0-9_.@%+=:,~-]+|"
    r"[A-Za-z0-9_.-]+\.(?:py|js|ts|tsx|jsx|md|json|yaml|yml|toml|html|css|sh|txt|sql|rs|go|java|kt|swift|cpp|c|h|hpp|ipynb|lock))"
)
_TAG_TOKEN_RE = re.compile(r"(?<![\w&])#([A-Za-z][A-Za-z0-9_-]{1,40})\b")
_SKIP_DECORATION_TAGS = {"a", "code", "pre", "kbd", "samp", "script", "style"}


def _decorate_text_segment(segment: str) -> str:
    placeholders: List[str] = []

    def stash(piece: str) -> str:
        placeholders.append(piece)
        return f"\ue000{len(placeholders) - 1}\ue001"

    def command_repl(match: re.Match[str]) -> str:
        fields = {name: value.strip() for name, value in _COMMAND_FIELD_RE.findall(match.group(0))}
        name = fields.get("command-name", "")
        message = fields.get("command-message", "")
        args = fields.get("command-args", "")
        args_html = f"<span class='session-command-args'>{args}</span>" if args else ""
        return stash(
            "<span class='session-command-chip'>"
            f"<span class='session-command-name'>{name}</span>"
            f"<span class='session-command-message'>{message}</span>"
            f"{args_html}"
            "</span>"
        )

    def tag_pair_repl(match: re.Match[str]) -> str:
        tag = match.group(1).strip()
        body = " ".join(match.group(2).split())
        return stash(
            "<span class='session-tag-block'>"
            f"<span class='session-tag-name'>{tag}</span>"
            f"<span class='session-tag-content'>{body}</span>"
            "</span>"
        )

    def path_repl(match: re.Match[str]) -> str:
        token = match.group(1)
        return stash(f"<span class='session-token session-token--path'>{token}</span>")

    def tag_repl(match: re.Match[str]) -> str:
        token = f"#{match.group(1)}"
        return stash(f"<span class='session-token session-token--tag'>{token}</span>")

    segment = _COMMAND_TAG_RE.sub(command_repl, segment)
    segment = _TAG_PAIR_RE.sub(tag_pair_repl, segment)
    segment = _PATH_TOKEN_RE.sub(path_repl, segment)
    segment = _TAG_TOKEN_RE.sub(tag_repl, segment)
    for idx, piece in enumerate(placeholders):
        segment = segment.replace(f"\ue000{idx}\ue001", piece)
    return segment


_CODE_BLOCK_RE = re.compile(r"<pre><code(?: class=\"language-([^\"]+)\")?>(.*?)</code></pre>", re.DOTALL)
_CODE_KEYWORD_RE = re.compile(
    r"\b(def|class|return|if|else|elif|for|while|try|except|finally|with|import|from|as|"
    r"const|let|var|function|async|await|new|throw|catch|interface|type|export|"
    r"true|false|null|True|False|None|"
    r"SELECT|FROM|WHERE|INSERT|UPDATE|DELETE|CREATE|TABLE|JOIN|GROUP|ORDER|BY)\b"
)
_CODE_NUMBER_RE = re.compile(r"\b(\d+(?:\.\d+)?)\b")
_CODE_COMMENT_RE = re.compile(r"(^|\s)(#.*?$|//.*?$)", re.MULTILINE)
_CODE_STRING_RE = re.compile(r"(&quot;.*?&quot;|&#x27;.*?&#x27;)")
_CODE_SHELL_COMMAND_RE = re.compile(r"(^|\n)(\s*)([A-Za-z0-9_.@/+:-]+)(?=\s|$)")
_CODE_SHELL_FLAG_RE = re.compile(r"(?<![\w-])(--?[A-Za-z0-9][A-Za-z0-9_-]*)\b")


def _highlight_code_html(code_html: str, lang: str = "") -> str:
    placeholders: List[str] = []

    def stash(piece: str) -> str:
        placeholders.append(piece)
        idx = len(placeholders) - 1
        key = ""
        n = idx
        while True:
            key = chr(65 + (n % 26)) + key
            n = n // 26 - 1
            if n < 0:
                break
        return f"\ue100{key}\ue101"

    code_html = _CODE_STRING_RE.sub(lambda m: stash(f"<span class='session-code-string'>{m.group(1)}</span>"), code_html)
    code_html = _CODE_COMMENT_RE.sub(lambda m: f"{m.group(1)}" + stash(f"<span class='session-code-comment'>{m.group(2)}</span>"), code_html)
    if lang.lower() in {"sh", "bash", "shell", "zsh"}:
        code_html = _CODE_SHELL_COMMAND_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}" + stash(f"<span class='session-code-command'>{m.group(3)}</span>"), code_html)
        code_html = _CODE_SHELL_FLAG_RE.sub(lambda m: stash(f"<span class='session-code-flag'>{m.group(1)}</span>"), code_html)
    code_html = _CODE_KEYWORD_RE.sub(lambda m: f"<span class='session-code-keyword'>{m.group(1)}</span>", code_html)
    code_html = _CODE_NUMBER_RE.sub(lambda m: f"<span class='session-code-number'>{m.group(1)}</span>", code_html)
    for idx, piece in enumerate(placeholders):
        key = ""
        n = idx
        while True:
            key = chr(65 + (n % 26)) + key
            n = n // 26 - 1
            if n < 0:
                break
        code_html = code_html.replace(f"\ue100{key}\ue101", piece)
    return code_html


def _highlight_code_blocks(rendered: str) -> str:
    def repl(match: re.Match[str]) -> str:
        lang = match.group(1) or ""
        code = _highlight_code_html(match.group(2), lang)
        lang_attr = f' class="language-{html.escape(lang, quote=True)}"' if lang else ""
        label = f"<span class='session-code-lang'>{html.escape(lang)}</span>" if lang else ""
        return f"<pre class='session-code-block'>{label}<code{lang_attr}>{code}</code></pre>"

    return _CODE_BLOCK_RE.sub(repl, rendered)


def _guess_code_lang(text: str, tool_name: str = "") -> str:
    clean = (text or "").strip()
    lower_name = tool_name.lower()
    if lower_name in {"bash", "shell", "sh"}:
        return "sh"
    if clean.startswith(("{", "[")) and clean.endswith(("}", "]")):
        return "json"
    if clean.startswith(("$ ", "llm-wiki", "llm_wiki", "python ", "pytest ", "git ", "npm ", "uv ", "curl ")):
        return "sh"
    return "text"


def _render_highlighted_pre(text: str, *, lang: str, class_name: str) -> str:
    escaped = html.escape(text or "")
    highlighted = _highlight_code_html(escaped, lang)
    lang_label = f"<span class='session-code-lang'>{html.escape(lang)}</span>" if lang and lang != "text" else ""
    lang_attr = f" data-lang='{html.escape(lang, quote=True)}'" if lang else ""
    return f"<pre class='{class_name}'{lang_attr}>{lang_label}<code>{highlighted}</code></pre>"


def _decorate_conversation_html(rendered: str) -> str:
    rendered = _highlight_code_blocks(rendered)
    parts = re.split(r"(<[^>]+>)", rendered)
    skip_stack: List[str] = []
    out: List[str] = []
    for part in parts:
        if not part:
            continue
        if part.startswith("<") and part.endswith(">"):
            tag_match = re.match(r"</?\s*([A-Za-z0-9:-]+)", part)
            if tag_match:
                tag = tag_match.group(1).lower()
                if tag in _SKIP_DECORATION_TAGS:
                    if part.startswith("</"):
                        for i in range(len(skip_stack) - 1, -1, -1):
                            if skip_stack[i] == tag:
                                del skip_stack[i]
                                break
                    elif not part.endswith("/>"):
                        skip_stack.append(tag)
            out.append(part)
            continue
        out.append(part if skip_stack else _decorate_text_segment(part))
    return "".join(out)


def _render_turn_markdown(text: str) -> str:
    rendered, _ = render_markdown(text or "")
    return _decorate_conversation_html(rendered or "<p></p>")


def _turn_summary(turn: Dict[str, object], limit: int = 110) -> str:
    raw_text = str(turn.get("text") or "")
    def _raw_command_summary(match: re.Match[str]) -> str:
        fields = {name: value.strip() for name, value in _RAW_COMMAND_FIELD_RE.findall(match.group(0))}
        return " ".join(p for p in [fields.get("command-name", ""), fields.get("command-message", ""), fields.get("command-args", "")] if p)
    raw_text = _RAW_COMMAND_TAG_RE.sub(_raw_command_summary, raw_text)
    raw_text = _RAW_TAG_PAIR_RE.sub(lambda m: f"{m.group(1)} {' '.join(m.group(2).split())}", raw_text)
    text = " ".join(raw_text.split())
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
        lang = _guess_code_lang(text, name)
        highlighted_text = _render_highlighted_pre(text, lang=lang, class_name="session-tool-use-text")
        items.append(
            "<article class='session-tool-use'>"
            "<header class='session-tool-use-header'>"
            f"<span>#{idx}</span>"
            f"<span>{_esc(name)}</span>"
            f"<time>{_esc(timestamp)}</time>"
            "</header>"
            f"{highlighted_text}"
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
        role_key = str(turn.get("role") or "message").strip().lower() or "message"
        summary = _turn_summary(turn)
        items.append(
            f"<li class=\"session-turn-nav--{_esc(role_key)}\" data-session-turn-target=\"{_esc(anchor)}\">"
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
