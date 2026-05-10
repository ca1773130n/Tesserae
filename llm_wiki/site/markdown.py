"""A small, dependency-free markdown -> HTML renderer.

Standard library only (no ``markdown`` / ``mistune`` / ``markdown-it`` import).
Designed for the body fields of :class:`llm_wiki.wiki_store.WikiPage` —
we control the markdown shapes those bodies produce, so this engine sticks
to the GitHub-flavoured subset that actually appears in the wiki:

* ATX headings ``#`` .. ``######`` with stable id slugs (anchorable).
* Paragraphs separated by blank lines.
* Ordered (``1.``) and unordered (``-``/``*``/``+``) lists, with simple
  one-level nesting on indent.
* Fenced code blocks (``\\`\\`\\``) — language tag preserved on the
  ``<code>`` element when present.
* Inline code (single backticks).
* Bold (``**...**``) and italic (``*...*`` / ``_..._``).
* Links ``[text](url)`` and images ``![alt](src)``.
* Blockquotes (``>``).
* Horizontal rules (``---``, ``***``, ``___``).
* GitHub-flavoured tables (pipe + header + ``---`` separator).

The renderer also exposes a hook for rewriting wiki-link targets like
``papers/foo.md`` → ``papers/foo.html`` so cross-page links in the
wiki bodies resolve to the rendered HTML neighbours.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Callable, List, Optional, Tuple


__all__ = ["render_markdown", "strip_frontmatter", "Heading"]


_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?", re.DOTALL)


def strip_frontmatter(body: str) -> Tuple[str, str]:
    """Return ``(frontmatter_text, body_without_frontmatter)``."""
    match = _FRONTMATTER_RE.match(body)
    if not match:
        return "", body
    return match.group(1), body[match.end():]


@dataclass(frozen=True)
class Heading:
    level: int
    text: str
    anchor: str


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_HRULE_RE = re.compile(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$")
_FENCE_RE = re.compile(r"^```\s*([A-Za-z0-9_+\-]*)\s*$")
_OL_RE = re.compile(r"^(\s*)(\d+)\.\s+(.*)$")
_UL_RE = re.compile(r"^(\s*)([-*+])\s+(.*)$")
_BLOCKQUOTE_RE = re.compile(r"^\s*>\s?(.*)$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?\s*$")
_ADMONITION_RE = re.compile(r"^\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]\s*(.*)$", re.IGNORECASE)
_HTML_TAG_START_RE = re.compile(r"^\s*<([A-Za-z][A-Za-z0-9:-]*)(?:\s|>|/>)")
_HTML_CLOSE_RE = re.compile(r"</([A-Za-z][A-Za-z0-9:-]*)\s*>")
_HTML_OPEN_RE = re.compile(r"<([A-Za-z][A-Za-z0-9:-]*)(?:\s[^<>]*)?>")
_HTML_SELF_CLOSE_RE = re.compile(r"<([A-Za-z][A-Za-z0-9:-]*)(?:\s[^<>]*)?/>")
_HTML_VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
_HTML_BLOCK_TAGS = {
    "address", "article", "aside", "blockquote", "details", "div", "dl", "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "main", "nav", "ol", "p", "picture", "pre", "section", "table", "tbody", "td", "tfoot", "th", "thead", "tr", "ul",
}
_ALLOWED_HTML_TAGS = _HTML_BLOCK_TAGS | {
    "a", "abbr", "b", "br", "caption", "cite", "code", "col", "colgroup", "dd", "del", "dfn", "dt", "em", "i", "img", "ins", "kbd", "li", "mark", "s", "small", "source", "span", "strong", "sub", "summary", "sup", "u",
}
_ALLOWED_HTML_ATTRS = {
    "abbr", "align", "alt", "aria-label", "class", "colspan", "height", "href", "loading", "media", "rel", "rowspan", "src", "srcset", "target", "title", "type", "valign", "width",
}
_URL_ATTRS = {"href", "src", "srcset"}
_SAFE_URL_RE = re.compile(r"^(?:https?:|mailto:|#|/|\.\.?/|[^:]+$)", re.IGNORECASE)


def _slug_anchor(text: str) -> str:
    out = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    out = re.sub(r"-+", "-", out)
    return out or "section"


# ---------------------------------------------------------------------------
# Raw HTML / GitHub admonition helpers
# ---------------------------------------------------------------------------

class _SafeHtmlRenderer(HTMLParser):
    """Allow a small GitHub-README-like HTML subset and escape everything else."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.parts: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        tag_l = tag.lower()
        if tag_l not in _ALLOWED_HTML_TAGS:
            self.parts.append(html.escape(self.get_starttag_text() or f"<{tag}>"))
            return
        clean_attrs = []
        for name, value in attrs:
            name_l = name.lower()
            if name_l not in _ALLOWED_HTML_ATTRS or value is None:
                continue
            value_s = str(value).strip()
            if name_l in _URL_ATTRS and not _SAFE_URL_RE.match(value_s):
                continue
            clean_attrs.append(f'{name_l}="{html.escape(value_s, quote=True)}"')
        attr_text = (" " + " ".join(clean_attrs)) if clean_attrs else ""
        suffix = " /" if tag_l in _HTML_VOID_TAGS and (self.get_starttag_text() or "").rstrip().endswith("/>") else ""
        self.parts.append(f"<{tag_l}{attr_text}{suffix}>")

    def handle_startendtag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        tag_l = tag.lower()
        if tag_l in _ALLOWED_HTML_TAGS and tag_l not in _HTML_VOID_TAGS:
            self.parts.append(f"</{tag_l}>")
        elif tag_l not in _ALLOWED_HTML_TAGS:
            self.parts.append(html.escape(f"</{tag}>"))

    def handle_data(self, data: str) -> None:
        self.parts.append(html.escape(data))

    def handle_entityref(self, name: str) -> None:
        self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self.parts.append(f"&#{name};")

    def handle_comment(self, data: str) -> None:
        return

    def handle_decl(self, decl: str) -> None:
        return

    def unknown_decl(self, data: str) -> None:
        return


def _sanitize_html(raw: str) -> str:
    parser = _SafeHtmlRenderer()
    parser.feed(raw)
    parser.close()
    return "".join(parser.parts)


def _html_block_tag(line: str) -> Optional[str]:
    match = _HTML_TAG_START_RE.match(line)
    if not match:
        return None
    tag = match.group(1).lower()
    return tag if tag in _HTML_BLOCK_TAGS else None


def _html_depth_delta(line: str) -> int:
    opens = 0
    for match in _HTML_OPEN_RE.finditer(line):
        tag = match.group(1).lower()
        token = match.group(0)
        if tag in _HTML_VOID_TAGS or token.rstrip().endswith("/>"):
            continue
        opens += 1
    closes = sum(1 for m in _HTML_CLOSE_RE.finditer(line) if m.group(1).lower() not in _HTML_VOID_TAGS)
    return opens - closes


def _consume_html_block(lines: List[str], start: int) -> Tuple[int, str]:
    tag = _html_block_tag(lines[start])
    if not tag:
        return 0, ""
    buf = [lines[start]]
    depth = max(0, _html_depth_delta(lines[start]))
    i = start + 1
    if tag in _HTML_VOID_TAGS or depth == 0:
        return 1, _sanitize_html("\n".join(buf))
    while i < len(lines):
        if not lines[i].strip() and depth <= 0:
            break
        buf.append(lines[i])
        depth += _html_depth_delta(lines[i])
        i += 1
        if depth <= 0:
            break
    return i - start, _sanitize_html("\n".join(buf))


def _render_admonition(kind: str, title_tail: str, body_lines: List[str], link_rewriter: Optional[Callable[[str], str]]) -> str:
    label = kind.upper()
    title = label.title() if label != "TIP" else "Tip"
    body = "\n".join(body_lines).strip()
    body_html, _ = render_markdown(body, link_rewriter=link_rewriter) if body else ("", [])
    tail_html = _render_inline(title_tail.strip(), link_rewriter=link_rewriter) if title_tail.strip() else ""
    title_html = f'<p class="admonition-title">{html.escape(title)}'
    if tail_html:
        title_html += f" <span>{tail_html}</span>"
    title_html += "</p>"
    return f'<div class="admonition admonition-{label.lower()}">{title_html}{body_html}</div>'


# ---------------------------------------------------------------------------
# Inline rendering
# ---------------------------------------------------------------------------

_IMG_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"([^\"]*)\")?\)")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)(?:\s+\"([^\"]*)\")?\)")
_CODE_INLINE_RE = re.compile(r"`([^`]+)`")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_ITALIC_STAR_RE = re.compile(r"(?<![\*\w])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![\*\w])")
_ITALIC_UNDER_RE = re.compile(r"(?<![\w_])_(?!\s)([^_\n]+?)(?<!\s)_(?![\w_])")
# Bare URL autolinker — runs *after* explicit ``[label](url)`` parsing has
# already stashed real link tags as placeholders, so we never double-wrap a
# URL that the author already linked. Trailing punctuation like ``.``, ``,``,
# ``)`` and ``;`` is excluded so prose like "see https://x.org/abs/2604.20329."
# does not glue the period into the URL.
_AUTOLINK_URL_RE = re.compile(r"(?<![\w/\"'>])(https?://[^\s<>\"'`]+?)(?=[)\].,;:!?]*(?:\s|$))")
# arXiv:2604.20329 (or 2604.20329v2 etc.) — links to https://arxiv.org/abs/...
_AUTOLINK_ARXIV_RE = re.compile(r"\barXiv:(\d{4}\.\d{4,6}(?:v\d+)?)\b", re.IGNORECASE)
_HTML_INLINE_TAG_RE = re.compile(r"</?[A-Za-z][^<>]*?/?>")


def _render_inline(text: str, link_rewriter: Optional[Callable[[str], str]] = None) -> str:
    """Render a single line / span of inline markdown to HTML."""
    placeholders: List[str] = []

    def _stash(piece: str) -> str:
        placeholders.append(piece)
        return f"\x00MD{len(placeholders) - 1}\x00"

    # 1. Inline code first — its content must not be re-interpreted.
    def _code_repl(m: re.Match[str]) -> str:
        return _stash(f"<code>{html.escape(m.group(1))}</code>")

    text = _CODE_INLINE_RE.sub(_code_repl, text)

    # 2. Images (must run before plain links).
    def _img_repl(m: re.Match[str]) -> str:
        alt, src, title = m.group(1), m.group(2), m.group(3)
        if link_rewriter:
            src = link_rewriter(src)
        title_attr = f' title="{html.escape(title, quote=True)}"' if title else ""
        return _stash(
            f'<img src="{html.escape(src, quote=True)}" '
            f'alt="{html.escape(alt, quote=True)}"{title_attr}>'
        )

    text = _IMG_RE.sub(_img_repl, text)

    # 3. Links.
    def _link_repl(m: re.Match[str]) -> str:
        label, target, title = m.group(1), m.group(2), m.group(3)
        if link_rewriter:
            target = link_rewriter(target)
        title_attr = f' title="{html.escape(title, quote=True)}"' if title else ""
        return _stash(
            f'<a href="{html.escape(target, quote=True)}"{title_attr}>'
            f"{html.escape(label)}</a>"
        )

    text = _LINK_RE.sub(_link_repl, text)

    # 3.5. Autolink bare URLs and arXiv-id tokens that the author didn't wrap
    #      in ``[label](url)``. We stash the rendered ``<a>`` tags as
    #      placeholders so the subsequent ``html.escape`` doesn't break them.
    def _autolink_url(m: re.Match[str]) -> str:
        url = m.group(1)
        # Strip trailing punctuation that the regex couldn't reasonably exclude
        # without breaking on legitimate URL characters.
        trailing = ""
        while url and url[-1] in ".,;:!?)]":
            trailing = url[-1] + trailing
            url = url[:-1]
        if not url:
            return m.group(0)
        return _stash(
            f'<a href="{html.escape(url, quote=True)}" rel="nofollow noopener">'
            f"{html.escape(url)}</a>"
        ) + trailing

    text = _AUTOLINK_URL_RE.sub(_autolink_url, text)

    def _autolink_arxiv(m: re.Match[str]) -> str:
        arxiv_id = m.group(1)
        url = f"https://arxiv.org/abs/{arxiv_id}"
        return _stash(
            f'<a href="{html.escape(url, quote=True)}" rel="nofollow noopener">'
            f"arXiv:{html.escape(arxiv_id)}</a>"
        )

    text = _AUTOLINK_ARXIV_RE.sub(_autolink_arxiv, text)

    # 3.6. Preserve a safe subset of inline raw HTML used by README-style
    #      markdown. Unsafe tags/attributes are escaped or dropped by the
    #      sanitizer before the main text escape runs.
    text = _HTML_INLINE_TAG_RE.sub(lambda m: _stash(_sanitize_html(m.group(0))), text)

    # 4. Now escape the remaining text.
    text = html.escape(text)

    # 5. Emphasis runs (on already-escaped text, but our placeholders are
    #    intact because they use \x00 sentinels).
    text = _BOLD_RE.sub(lambda m: f"<strong>{m.group(1)}</strong>", text)
    text = _ITALIC_STAR_RE.sub(lambda m: f"<em>{m.group(1)}</em>", text)
    text = _ITALIC_UNDER_RE.sub(lambda m: f"<em>{m.group(1)}</em>", text)

    # 6. Restore placeholders.
    for idx, piece in enumerate(placeholders):
        text = text.replace(f"\x00MD{idx}\x00", piece)
    return text


# ---------------------------------------------------------------------------
# Block parsing
# ---------------------------------------------------------------------------


def render_markdown(
    body: str,
    *,
    link_rewriter: Optional[Callable[[str], str]] = None,
) -> Tuple[str, List[Heading]]:
    """Render markdown ``body`` to HTML.

    Returns ``(html, headings)``. ``headings`` collects every ATX heading in
    document order so callers can build a TOC. ``link_rewriter`` is applied
    to every link / image target before HTML escaping.
    """
    # Frontmatter is the caller's responsibility, but we strip it
    # defensively so a stray wiki body never bleeds ``---`` through.
    _, body = strip_frontmatter(body)

    lines = body.splitlines()
    out: List[str] = []
    headings: List[Heading] = []
    i = 0
    n = len(lines)

    def _is_blank(s: str) -> bool:
        return not s.strip()

    def _inline(text: str) -> str:
        return _render_inline(text, link_rewriter=link_rewriter)

    while i < n:
        line = lines[i]

        # blank → consume
        if _is_blank(line):
            i += 1
            continue

        # fenced code block
        m = _FENCE_RE.match(line)
        if m:
            lang = m.group(1)
            i += 1
            buf: List[str] = []
            while i < n and not _FENCE_RE.match(lines[i]):
                buf.append(lines[i])
                i += 1
            if i < n:
                i += 1  # consume closing fence
            code_text = "\n".join(buf)
            code_html = html.escape(code_text)
            if lang.lower() == "mermaid":
                out.append(f'<div class="mermaid" data-mermaid-source="fence">{code_html}</div>')
            else:
                cls = f' class="language-{html.escape(lang, quote=True)}"' if lang else ""
                out.append(f"<pre><code{cls}>{code_html}</code></pre>")
            continue

        # raw HTML block (GitHub README subset)
        html_consumed, html_block = _consume_html_block(lines, i)
        if html_consumed:
            out.append(html_block)
            i += html_consumed
            continue

        # horizontal rule
        if _HRULE_RE.match(line) and not (line.lstrip().startswith("---") and _is_table_following(lines, i)):
            out.append("<hr>")
            i += 1
            continue

        # heading
        h = _HEADING_RE.match(line)
        if h:
            level = len(h.group(1))
            text = h.group(2).strip()
            anchor = _slug_anchor(text)
            headings.append(Heading(level=level, text=text, anchor=anchor))
            out.append(f'<h{level} id="{anchor}">{_inline(text)}</h{level}>')
            i += 1
            continue

        # blockquote / GitHub-style admonition
        if _BLOCKQUOTE_RE.match(line):
            buf = []
            while i < n and _BLOCKQUOTE_RE.match(lines[i]):
                buf.append(_BLOCKQUOTE_RE.match(lines[i]).group(1))
                i += 1
            first = buf[0].strip() if buf else ""
            admonition = _ADMONITION_RE.match(first)
            if admonition:
                body_lines = buf[1:]
                out.append(
                    _render_admonition(
                        admonition.group(1),
                        admonition.group(2),
                        body_lines,
                        link_rewriter,
                    )
                )
            else:
                inner_html, _ = render_markdown("\n".join(buf), link_rewriter=link_rewriter)
                out.append(f"<blockquote>{inner_html}</blockquote>")
            continue

        # table (header line followed by separator line)
        if "|" in line and i + 1 < n and _TABLE_SEP_RE.match(lines[i + 1]):
            consumed, table_html = _consume_table(lines, i, _inline)
            if consumed > 0:
                out.append(table_html)
                i += consumed
                continue

        # ordered/unordered list
        if _OL_RE.match(line) or _UL_RE.match(line):
            consumed, list_html = _consume_list(lines, i, _inline)
            out.append(list_html)
            i += consumed
            continue

        # otherwise, paragraph: join consecutive non-blank, non-special lines.
        para: List[str] = [line]
        i += 1
        while i < n:
            nxt = lines[i]
            if _is_blank(nxt):
                break
            if (
                _HEADING_RE.match(nxt)
                or _FENCE_RE.match(nxt)
                or _HRULE_RE.match(nxt)
                or _BLOCKQUOTE_RE.match(nxt)
                or _html_block_tag(nxt)
                or _OL_RE.match(nxt)
                or _UL_RE.match(nxt)
            ):
                break
            para.append(nxt)
            i += 1
        joined = " ".join(s.strip() for s in para)
        out.append(f"<p>{_inline(joined)}</p>")

    return "\n".join(out), headings


def _is_table_following(lines: List[str], idx: int) -> bool:
    # Heuristic: the candidate ``---`` line is the table separator, not a
    # horizontal rule, when the line above it contains a ``|``.
    return idx > 0 and "|" in lines[idx - 1]


def _split_row(line: str) -> List[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [cell.strip() for cell in s.split("|")]


def _consume_table(
    lines: List[str], start: int, inline: Callable[[str], str]
) -> Tuple[int, str]:
    header = _split_row(lines[start])
    sep = lines[start + 1]
    if not _TABLE_SEP_RE.match(sep):
        return 0, ""
    aligns: List[str] = []
    for cell in _split_row(sep):
        c = cell.strip()
        if c.startswith(":") and c.endswith(":"):
            aligns.append("center")
        elif c.endswith(":"):
            aligns.append("right")
        elif c.startswith(":"):
            aligns.append("left")
        else:
            aligns.append("")
    body_rows: List[List[str]] = []
    j = start + 2
    while j < len(lines) and lines[j].strip() and "|" in lines[j]:
        body_rows.append(_split_row(lines[j]))
        j += 1

    def _td(value: str, align: str, tag: str) -> str:
        style = f' style="text-align:{align}"' if align else ""
        return f"<{tag}{style}>{inline(value)}</{tag}>"

    head_html = "".join(
        _td(h, aligns[idx] if idx < len(aligns) else "", "th")
        for idx, h in enumerate(header)
    )
    body_html_parts: List[str] = []
    for row in body_rows:
        cells_html = "".join(
            _td(cell, aligns[idx] if idx < len(aligns) else "", "td")
            for idx, cell in enumerate(row)
        )
        body_html_parts.append(f"<tr>{cells_html}</tr>")
    table_html = (
        '<table class="md-table">'
        f"<thead><tr>{head_html}</tr></thead>"
        f"<tbody>{''.join(body_html_parts)}</tbody>"
        "</table>"
    )
    return j - start, table_html


def _consume_list(
    lines: List[str], start: int, inline: Callable[[str], str]
) -> Tuple[int, str]:
    """Consume a list block (single-level + simple nesting).

    Indented continuation lines are folded into the previous item. A change
    in indent that lands on another list marker opens a nested ``<ul>``/``<ol>``.
    """
    first_ol = _OL_RE.match(lines[start])
    ordered = first_ol is not None
    base_indent = len((first_ol or _UL_RE.match(lines[start])).group(1))
    items: List[Tuple[int, str, List[str]]] = []  # (indent, head, continuations)
    i = start
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            # Blank line — peek; if next line is a list item at >= base indent,
            # keep going, else stop.
            if i + 1 >= len(lines):
                break
            nxt = lines[i + 1]
            if (_OL_RE.match(nxt) or _UL_RE.match(nxt)) and len(
                (_OL_RE.match(nxt) or _UL_RE.match(nxt)).group(1)
            ) >= base_indent:
                i += 1
                continue
            break
        m_ol = _OL_RE.match(line)
        m_ul = _UL_RE.match(line)
        if m_ol or m_ul:
            indent = len((m_ol or m_ul).group(1))
            if indent < base_indent:
                break
            head = (m_ol or m_ul).group(3)
            items.append((indent, head, []))
            i += 1
            continue
        # Continuation of the previous item if indented.
        if items and (line.startswith(" " * (base_indent + 1)) or line.startswith("\t")):
            items[-1][2].append(line.strip())
            i += 1
            continue
        break

    parts: List[str] = []
    for indent, head, continuations in items:
        body = head
        if continuations:
            body = body + " " + " ".join(continuations)
        parts.append(f"<li>{inline(body)}</li>")

    tag = "ol" if ordered else "ul"
    return i - start, f"<{tag}>{''.join(parts)}</{tag}>"
