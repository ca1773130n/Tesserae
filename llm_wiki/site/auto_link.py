"""Deterministic auto-linker for rendered markdown bodies.

Walks rendered HTML and wraps the longest unlinked node-name (or alias) in
each occurrence with a ``<a class="auto-link">`` pointing at the wiki page.
The candidate set is built from a :class:`SiteContext` once per build, so the
cost per page is regex/state-machine over the body length only.

Design contract (mirrors §Issue 3 of the polish brief):

  * Longest match wins, ties broken alphabetically (deterministic).
  * Whole-word boundaries, Unicode-aware (CJK runs accepted as a unit).
  * Skip text inside <a>, <code>, <pre>, <h1>/<h2>/<h3>, <textarea>,
    <script>, <style>, and inside any attribute value.
  * Per-page cap: each ``node_id`` is auto-linked at most once per body so
    the prose never reads like keyword spam.
  * ``exclude_node_ids`` lets the page about node X opt out of self-linking.
  * Output href is depth-prefixed (``../`` per directory) so links resolve
    from wherever the page lives.

Standard library only — no third-party HTML parsers — so the build stays
hermetic. The walker is intentionally conservative: when in doubt we leave
the original text alone.
"""

from __future__ import annotations

import html as _html
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable, Mapping, Optional, Sequence, Tuple

if TYPE_CHECKING:  # pragma: no cover - import cycle avoidance
    from .pages import SiteContext


# Tags whose text content must never be touched.
_SKIP_TAGS = frozenset(
    {
        "a",
        "code",
        "pre",
        "h1",
        "h2",
        "h3",
        "textarea",
        "script",
        "style",
    }
)


def _is_word_char(ch: str) -> bool:
    """Word-boundary helper. Letters/digits/underscore *or* CJK glyphs.

    A regex ``\b`` is ASCII-biased; we treat any non-space, non-punctuation
    Unicode codepoint as a word character so a Korean word like ``적용`` is
    not accidentally split inside another run of Hangul.
    """
    if not ch:
        return False
    if ch == "_":
        return True
    return ch.isalnum()


def _bounded(text: str, start: int, end: int) -> bool:
    """True when ``text[start:end]`` sits at whole-word boundaries."""
    before = text[start - 1] if start > 0 else ""
    after = text[end] if end < len(text) else ""
    if before and _is_word_char(before) and _is_word_char(text[start]):
        return False
    if after and _is_word_char(after) and _is_word_char(text[end - 1]):
        return False
    return True


@dataclass(frozen=True)
class LinkTarget:
    """Where an auto-linked name should resolve to."""

    href: str          # e.g. "concepts/gaussian-splatting.html"
    kind: str          # the wiki kind (papers/concepts/...)
    node_id: str       # for debug + dedupe
    title: str         # display title (used as fallback / tooltip)


@dataclass(frozen=True)
class AutoLinker:
    """Pre-computed name→target table that can linkify any HTML body."""

    # Sorted longest-name first (so we greedy-match the longest wrapper),
    # ties broken alphabetically for determinism.
    targets: Tuple[Tuple[str, LinkTarget], ...]
    # Lower-cased name → original-cased keys, so case-insensitive lookups
    # can recover the canonical entry. Many → many: an alias may shadow a
    # canonical entry of the same lowercase form; we keep the FIRST sorted
    # entry (longest, then alphabetic) which matches our greedy strategy.
    _by_lower: Mapping[str, LinkTarget]

    @classmethod
    def from_context(cls, ctx: "SiteContext") -> "AutoLinker":
        """Build an :class:`AutoLinker` from a :class:`SiteContext`.

        Walks every node in the graph, asks ``kind_for_node()`` whether it
        gets a public route, and registers the canonical name plus every
        alias against that route's URL. Private/code-graph types are
        silently skipped — they have no wiki page to link at.
        """
        # Local import to avoid the circular dependency at module load.
        from .pages import kind_for_node, node_href  # noqa: WPS433

        registered: dict[str, LinkTarget] = {}
        for node in ctx.graph.nodes:
            kind = kind_for_node(node)
            if not kind:
                continue
            href = node_href(node, ctx)
            if not href:
                continue
            target = LinkTarget(
                href=href,
                kind=kind,
                node_id=node.id,
                title=node.name,
            )
            for raw_name in (node.name, *node.aliases):
                name = (raw_name or "").strip()
                # Skip empty / one-char / two-char keys: too many false hits.
                # Three characters is the floor; anything shorter is noise
                # (``GS`` matches every plural ``Gs`` etc.).
                if len(name) < 3:
                    continue
                # First registration wins on exact-case collisions; the
                # sort below guarantees deterministic precedence.
                registered.setdefault(name, target)

        sorted_keys = sorted(
            registered.keys(),
            key=lambda k: (-len(k), k.casefold()),
        )
        targets = tuple((k, registered[k]) for k in sorted_keys)
        # Lower-cased index for the case-insensitive scanner. We want the
        # FIRST target that maps to each lowercased key, so iterate the
        # already-sorted ``targets`` and only insert when missing.
        by_lower: dict[str, LinkTarget] = {}
        for k, target in targets:
            lk = k.casefold()
            by_lower.setdefault(lk, target)
        return cls(targets=targets, _by_lower=by_lower)

    # -- public API ---------------------------------------------------------

    def linkify(
        self,
        html_in: str,
        *,
        depth: int = 0,
        exclude_node_ids: Optional[Iterable[str]] = None,
    ) -> str:
        """Return ``html_in`` with auto-link wrappers applied.

        ``depth`` controls how many ``../`` segments are prepended to each
        href so the link resolves from the page's own directory.
        ``exclude_node_ids`` is the set of node ids that must NOT be linked
        (typically ``{current_page_node.id}`` so a page never links to
        itself).
        """
        if not html_in or not self.targets:
            return html_in

        excluded = frozenset(exclude_node_ids or ())
        prefix = "../" * max(depth, 0)
        used_node_ids: set[str] = set()
        return _walk_and_linkify(
            html_in,
            scan_fn=lambda text: self._scan_text(text, prefix, excluded, used_node_ids),
        )

    # -- internals ----------------------------------------------------------

    def _scan_text(
        self,
        text: str,
        prefix: str,
        excluded: frozenset,
        used_node_ids: set,
    ) -> str:
        """Linkify a single text-node's worth of content.

        Walks left-to-right. At each position attempts the longest candidate
        first; on a match wraps it in <a> and skips past. If no candidate
        matches, advances by one codepoint.

        ``text`` is already-escaped HTML text-content (it came out of the
        markdown renderer), so we pass non-matched characters through
        verbatim — re-escaping would double-encode ``&amp;`` etc. The
        wrapped match itself is also already-escaped: we copy the matched
        slice byte-for-byte into the new ``<a>`` wrapper.
        """
        if not text or not self.targets:
            return text

        out: list[str] = []
        i = 0
        n = len(text)
        # Build a dispatch index keyed on the lowercase first character so
        # we don't scan every entry at every position. ``lower_keys`` is the
        # cached, sorted candidate list per first-char.
        first_char_index = self._first_char_index()

        while i < n:
            ch = text[i]
            candidates = first_char_index.get(ch.casefold(), ())
            matched = False
            for key, target in candidates:
                klen = len(key)
                if i + klen > n:
                    continue
                segment = text[i : i + klen]
                if segment.casefold() != key.casefold():
                    continue
                if not _bounded(text, i, i + klen):
                    continue
                if target.node_id in excluded:
                    continue
                if target.node_id in used_node_ids:
                    # Per-page cap: one wrapper per node_id only.
                    continue
                # Wrap and advance. ``segment`` is already part of the
                # rendered HTML's text content — emit verbatim, no escape.
                href = prefix + target.href
                out.append(
                    f'<a class="auto-link" href="{_html.escape(href, quote=True)}"'
                    f' title="{_html.escape(target.kind + ": " + target.title, quote=True)}">'
                    f"{segment}"
                    "</a>"
                )
                used_node_ids.add(target.node_id)
                i += klen
                matched = True
                break
            if not matched:
                out.append(ch)
                i += 1
        return "".join(out)

    def _first_char_index(self):
        """Cache: lowercase first-char → sorted (key, target) candidates."""
        cache = getattr(self, "_first_char_cache", None)
        if cache is not None:
            return cache
        index: dict[str, list[Tuple[str, LinkTarget]]] = {}
        for key, target in self.targets:
            if not key:
                continue
            head = key[0].casefold()
            index.setdefault(head, []).append((key, target))
        # Already sorted via ``targets``; preserve order.
        # Stash on the instance via object.__setattr__ since the dataclass
        # is frozen.
        object.__setattr__(self, "_first_char_cache", index)
        return index


# ---------------------------------------------------------------------------
# HTML walker
# ---------------------------------------------------------------------------


# Match an HTML tag opener: ``<name`` (with optional namespace prefix). We
# only need the name to decide whether to enter a skip-region.
_TAG_RE = re.compile(r"<\s*(/?)\s*([a-zA-Z][a-zA-Z0-9:_-]*)")


def _walk_and_linkify(html_in: str, *, scan_fn) -> str:
    """Walk ``html_in`` token by token, applying ``scan_fn`` to text nodes.

    The walker is a tiny state machine — *not* a full HTML parser. It
    tracks:

      * Whether we're inside a tag (``<...>``), so attribute text never
        gets touched.
      * A stack of currently-open "skip" tags so we don't linkify text
        inside <a>, <code>, <pre>, <h1>/<h2>/<h3>, <textarea>, <script>,
        <style>.
      * HTML comments (``<!-- ... -->``) are passed through verbatim.

    The walker emits original substrings byte-for-byte except for text
    nodes that ``scan_fn`` rewrites. Self-closing forms (``<br/>``) and
    void elements never enter the skip stack.
    """
    out: list[str] = []
    skip_stack: list[str] = []
    i = 0
    n = len(html_in)

    while i < n:
        # Comment passthrough.
        if html_in.startswith("<!--", i):
            close = html_in.find("-->", i + 4)
            if close == -1:
                out.append(html_in[i:])
                break
            out.append(html_in[i : close + 3])
            i = close + 3
            continue

        # CDATA passthrough (rare in Markdown output but cheap to guard).
        if html_in.startswith("<![CDATA[", i):
            close = html_in.find("]]>", i + 9)
            if close == -1:
                out.append(html_in[i:])
                break
            out.append(html_in[i : close + 3])
            i = close + 3
            continue

        # DOCTYPE / processing instructions — pass through.
        if html_in.startswith("<!", i) or html_in.startswith("<?", i):
            close = html_in.find(">", i + 2)
            if close == -1:
                out.append(html_in[i:])
                break
            out.append(html_in[i : close + 1])
            i = close + 1
            continue

        # Look for the next tag opener.
        next_lt = html_in.find("<", i)
        if next_lt == -1:
            chunk = html_in[i:]
            if not skip_stack:
                chunk = scan_fn(chunk)
            out.append(chunk)
            break

        # Emit any text before the next tag.
        if next_lt > i:
            chunk = html_in[i:next_lt]
            if not skip_stack:
                chunk = scan_fn(chunk)
            out.append(chunk)
            i = next_lt

        # Parse the tag name + closing flag.
        m = _TAG_RE.match(html_in, i)
        if not m:
            # Stray ``<`` — emit literally and advance.
            out.append(html_in[i])
            i += 1
            continue

        is_close = m.group(1) == "/"
        tag_name = m.group(2).lower()
        # Find the tag closing ``>``. We have to skip over any quoted
        # attribute values so a ``>`` inside ``alt=">"`` doesn't trick us.
        j = m.end()
        in_quote: Optional[str] = None
        while j < n:
            c = html_in[j]
            if in_quote:
                if c == in_quote:
                    in_quote = None
                j += 1
                continue
            if c in ('"', "'"):
                in_quote = c
                j += 1
                continue
            if c == ">":
                break
            j += 1
        if j >= n:
            # Unterminated tag — emit the rest verbatim.
            out.append(html_in[i:])
            break

        tag_html = html_in[i : j + 1]
        out.append(tag_html)
        # Self-closing? ``<br/>`` or ``<br />``.
        is_self_closing = tag_html.rstrip(">").rstrip().endswith("/")
        i = j + 1

        if tag_name in _SKIP_TAGS:
            if is_close:
                # Pop the matching skip frame if any.
                for k in range(len(skip_stack) - 1, -1, -1):
                    if skip_stack[k] == tag_name:
                        del skip_stack[k]
                        break
            elif not is_self_closing:
                skip_stack.append(tag_name)

    return "".join(out)


__all__ = ["AutoLinker", "LinkTarget"]
