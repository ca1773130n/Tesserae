"""Raw-document viewer route for the LLM-Wiki site.

The user's product principle: "wiki is for agents, but humans should still be
able to click a Source link and see the original document rendered." This
module emits ``/raw/<safe>.html`` pages that render the on-disk file behind a
node's ``source_path`` field (or any other project-relative file we surface a
link to).

Public surface:

* :func:`relativize_source_path` — strip a project-root prefix off an absolute
  source path so the page chrome displays ``data/research/...`` rather than
  ``/Users/neo/.../data/research/...``. Used by ``pages.py`` for the eyebrow
  metadata, and by ``synthesis.py`` when serialising ``sources:`` frontmatter.
* :func:`safe_raw_slug` — turn a project-relative path into the URL-safe stem
  used by ``raw/<safe>.html`` and by the binary asset copies under
  ``raw-assets/<safe>.<ext>``.
* :func:`raw_href` — single source of truth for "what URL renders this raw
  document?". Returns ``None`` when the file is outside the project root or
  doesn't exist on disk.
* :func:`render_raw_view` — render the full HTML document for a raw file.

Stays standard-library only and timestamp-free in body output to honour the
byte-idempotence invariant.
"""

from __future__ import annotations

import html
import json
import os
import re
from pathlib import Path
from typing import Callable, Iterable, List, Mapping, Optional, Tuple

from .components import breadcrumbs, page_shell, toc
from .markdown import render_markdown


# Regex used by :func:`_unique_heading_anchors` to find ``<h2>`` / ``<h3>``
# blocks in already-rendered HTML. Groups: 1=level digit, 2=attrs, 3=inner.
# DOTALL so headings spanning a newline (rare but possible) still match.
_HEADING_TAG_RE = re.compile(r'<h([23])([^>]*)>(.*?)</h\1>', re.DOTALL)
_HEADING_ID_RE = re.compile(r'\bid="([^"]+)"')
_HEADING_CLASS_RE = re.compile(r'\bclass="([^"]+)"')
# Strip every HTML tag for the TOC label — ``<code>foo</code>`` inside an
# ``<h3>`` should display as just ``foo`` because the TOC builder calls
# ``_esc(text)`` on the label and we don't want literal angle brackets in
# the rail.
_HEADING_TAG_STRIP_RE = re.compile(r'<[^>]+>')


# A wiki-link resolver maps a ``(kind, key)`` lookup onto a page slug.
#  * ``kind="papers"`` + ``key=<arxiv-id>`` (e.g. ``"2509.23563"``) →
#    ``slug`` of the corresponding paper-detail page, or ``None``.
#  * ``kind="repos"`` + ``key="<owner>/<repo>"`` (case-insensitive) →
#    ``slug`` of the corresponding repo-detail page, or ``None``.
# Used by :func:`_render_markdown_body` to rewrite cross-page references like
# ``[GitHub 분석](papers/2509.23563/repo.md)`` and
# ``[분석](repos/OpenDriveLab_WorldEngine.md)`` onto canonical analysis URLs
# instead of leaving 404s under ``/raw/``.
WikiLinkResolver = Callable[[str, str], Optional[str]]


__all__ = [
    "RAW_ASSETS_DIR",
    "RAW_ROUTE_DIR",
    "relativize_source_path",
    "safe_raw_slug",
    "raw_href",
    "render_raw_view",
    "iter_raw_sources",
    "iter_markdown_binary_assets",
    "copy_raw_asset",
    "is_binary_extension",
    "is_markdown_source_path",
    "WikiLinkResolver",
    "build_wiki_link_resolver",
]


# Where binary assets (PDF, images) get copied under the site output. The
# ``raw/<safe>.html`` page references these via ``raw-assets/<safe>.<ext>``
# (one level up + into ``raw-assets``).
RAW_ASSETS_DIR = "raw-assets"
RAW_ROUTE_DIR = "raw"

# Per-extension caps on what we are willing to inline. Binary types over the
# cap fall back to a download link.
_TEXT_INLINE_LIMIT = 1 * 1024 * 1024     # 1 MB
_DATA_INLINE_LIMIT = 256 * 1024          # 256 KB

_MARKDOWN_EXTS = {".md", ".markdown", ".mdx"}
_TEXT_EXTS = {".txt", ".log", ".csv", ".tsv"}
_DATA_EXTS = {".json", ".yaml", ".yml"}
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}
_HTML_EXTS = {".html", ".htm"}
_PDF_EXTS = {".pdf"}

_BINARY_EXTS = _IMAGE_EXTS | _PDF_EXTS
_MD_IMAGE_TARGET_RE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_HTML_URL_ATTR_RE = re.compile(
    r"(?P<prefix>\b(?:src|href)=)(?P<quote>[\"'])(?P<url>[^\"']+)(?P=quote)",
    re.IGNORECASE,
)


def is_binary_extension(suffix: str) -> bool:
    """Return ``True`` for extensions we copy alongside as binary assets."""
    return suffix.lower() in _BINARY_EXTS


def is_markdown_source_path(path: object) -> bool:
    """Return ``True`` for Markdown files, including root ``README.md.<lang>``.

    GitHub renders localized README companions named like ``README.md.ko`` as
    Markdown when linked from the repository root. Treat those files as Markdown
    in the generated raw viewer too; otherwise their raw pages degrade into
    plain ``<pre>`` text because ``Path.suffix`` is just ``.ko``.
    """
    p = Path(str(path))
    name = p.name.lower()
    return p.suffix.lower() in _MARKDOWN_EXTS or name.startswith("readme.md.")


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def relativize_source_path(value: object, project_root: Optional[Path] = None) -> str:
    """Return a project-relative version of ``value``.

    ``value`` is expected to be a string (we accept any object, just stringify).
    Empty / missing values pass through unchanged. When ``project_root`` is
    provided and ``value`` is absolute under it, the prefix is stripped so the
    rendered page shows ``data/research/...`` rather than the full machine path.

    Already-relative paths are normalised (forward slashes, no leading ``./``)
    but otherwise left alone — this lets us run the helper unconditionally on
    any source path string without changing behaviour for paths that are
    already nice. Idempotent: calling twice in a row yields the same string.
    """
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""

    # Normalise to forward slashes and drop any leading ``./``.
    text = text.replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]

    if project_root is not None:
        try:
            root = Path(project_root).resolve()
        except (OSError, ValueError):
            root = Path(project_root)
        root_str = str(root).replace("\\", "/")
        if not root_str.endswith("/"):
            root_str += "/"
        # Match against both the resolved and the given form so paths that
        # were never resolve()'d (test fixtures often skip the syscall) still
        # collapse onto the same project-relative shape.
        candidates = {root_str}
        try:
            literal = str(Path(project_root)).replace("\\", "/")
            if not literal.endswith("/"):
                literal += "/"
            candidates.add(literal)
        except TypeError:
            pass
        for prefix in candidates:
            if text.startswith(prefix):
                return text[len(prefix):]

    return text


def derive_project_root(wiki_root: Optional[Path]) -> Optional[Path]:
    """Recover the project root from a wiki root.

    Convention: ``<project_root>/.llm-wiki/wiki/`` — see ``ProjectPaths``.
    Returns ``None`` for the legacy two-arg ``write_site(graph, output_dir)``
    call shape (no wiki root).
    """
    if wiki_root is None:
        return None
    p = Path(wiki_root)
    # parent[0] == .llm-wiki, parent[1] == project root.
    if p.name == "wiki" and p.parent.name == ".llm-wiki":
        return p.parent.parent
    # Defensive fallback: caller may have pointed wiki_root somewhere unusual.
    return p.parent if p.parent != p else None


def safe_raw_slug(project_relative_path: str) -> str:
    """Project-relative path → URL-safe slug used by ``raw/<safe>.html``.

    Example: ``data/research/daily/2026-04-25/papers/2603.24725/paper.md`` →
    ``data-research-daily-2026-04-25-papers-2603-24725-paper-md``. The result
    contains only ASCII alphanumerics and hyphens so it is safe in both the
    URL path and on case-insensitive filesystems.
    """
    text = (project_relative_path or "").strip().replace("\\", "/")
    # Drop any leading ``/`` (defensive) so we never accidentally encode an
    # absolute path with a double-leading hyphen.
    text = text.lstrip("/")
    out = []
    last_dash = False
    for ch in text.lower():
        if ch.isalnum():
            out.append(ch)
            last_dash = False
        else:
            if not last_dash:
                out.append("-")
                last_dash = True
    slug = "".join(out).strip("-")
    return slug


def raw_href(
    project_root: Optional[Path],
    source_path: object,
    *,
    depth: int = 1,
) -> Optional[str]:
    """Return the ``raw/<safe>.html`` href for a source path.

    Returns ``None`` when the path is empty, escapes the project root, or
    points at a file that doesn't exist on disk. ``depth`` is how many
    directory levels the rendered page lives below the site root (the
    standard detail page renders at depth 1).
    """
    rel = relativize_source_path(source_path, project_root=project_root)
    if not rel:
        return None
    # Reject paths that still look absolute after relativisation — a missing
    # project_root or a path outside it will otherwise mint a junk slug.
    if rel.startswith("/") or (len(rel) > 1 and rel[1] == ":"):
        return None
    # File-existence check. We deliberately only mint the link when the file
    # is on disk; otherwise the raw page would 404.
    if project_root is not None:
        target = Path(project_root) / rel
        try:
            if not target.is_file():
                return None
        except OSError:
            return None
    slug = safe_raw_slug(rel)
    if not slug:
        return None
    prefix = "../" * max(depth, 0)
    return f"{prefix}{RAW_ROUTE_DIR}/{slug}.html"


def iter_raw_sources(
    source_paths: Iterable[object], project_root: Optional[Path]
) -> List[Tuple[str, str, Path]]:
    """Return the deduplicated raw-source inventory used by the site builder.

    Each entry is ``(project_relative_path, slug, absolute_path)``. Paths that
    don't exist on disk are skipped silently. Order is sorted by the
    project-relative path so the emission order is deterministic.
    """
    seen: dict[str, Tuple[str, str, Path]] = {}
    for sp in source_paths:
        rel = relativize_source_path(sp, project_root=project_root)
        if not rel or rel.startswith("/") or rel in seen:
            continue
        if project_root is None:
            continue
        absolute = Path(project_root) / rel
        try:
            if not absolute.is_file():
                continue
        except OSError:
            continue
        slug = safe_raw_slug(rel)
        if not slug:
            continue
        seen[rel] = (rel, slug, absolute)
    return [seen[k] for k in sorted(seen)]


def copy_raw_asset(absolute: Path, slug: str, assets_dir: Path) -> Optional[str]:
    """Copy a binary file to ``raw-assets/<slug><suffix>``.

    Returns the relative filename written under ``assets_dir`` (e.g.
    ``foo.pdf``). Skips when the destination already exists with matching
    size + mtime so two consecutive compiles don't churn binaries. Assets
    use the file's lowercased suffix verbatim.
    """
    suffix = absolute.suffix.lower()
    if not suffix:
        return None
    dest_name = f"{slug}{suffix}"
    dest = assets_dir / dest_name
    try:
        src_stat = absolute.stat()
    except OSError:
        return None
    needs_copy = True
    if dest.exists():
        try:
            dst_stat = dest.stat()
            if (
                dst_stat.st_size == src_stat.st_size
                and int(dst_stat.st_mtime) == int(src_stat.st_mtime)
            ):
                needs_copy = False
        except OSError:
            needs_copy = True
    if needs_copy:
        try:
            assets_dir.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(absolute.read_bytes())
            # Mirror the source mtime so the next compile can short-circuit.
            os.utime(dest, (src_stat.st_atime, src_stat.st_mtime))
        except OSError:
            return None
    return dest_name


# ---------------------------------------------------------------------------
# Page renderer
# ---------------------------------------------------------------------------


def _split_url_suffix(target: str) -> Tuple[str, str, str]:
    """Return ``(path, query, fragment)`` for a URL-ish markdown/html target."""
    rest, fragment, query = target, "", ""
    if "#" in rest:
        rest, frag = rest.split("#", 1)
        fragment = "#" + frag
    if "?" in rest:
        rest, q = rest.split("?", 1)
        query = "?" + q
    return rest, query, fragment


def _is_external_or_root_url(target: str) -> bool:
    return not target or target.startswith(("http://", "https://", "mailto:", "#", "/", "data:"))


def _raw_asset_href_for_project_rel(project_rel: str, *, depth: int = 1) -> str:
    suffix = Path(project_rel).suffix.lower()
    prefix = "../" * max(depth, 0)
    return f"{prefix}{RAW_ASSETS_DIR}/{safe_raw_slug(project_rel)}{suffix}"


def iter_markdown_binary_assets(
    markdown_path: Path,
    project_root: Path,
) -> List[Tuple[str, str, Path]]:
    """Find local files referenced from a markdown source.

    README-style source documents often link sibling docs and embed screenshots
    with raw HTML such as ``<img src=\"docs/assets/demo.png\">``. Raw pages live
    under ``raw/``, so literal targets would resolve under ``raw/`` and break on
    the generated/deployed site unless the referenced files are emitted too.
    We collect local dependencies so markdown links can land on raw pages and
    binary assets can be copied to ``raw-assets/``.
    """
    try:
        text = markdown_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    targets: list[str] = []
    targets.extend(m.group(1) for m in _MD_IMAGE_TARGET_RE.finditer(text))
    targets.extend(m.group("url") for m in _HTML_URL_ATTR_RE.finditer(text))
    seen: dict[str, Tuple[str, str, Path]] = {}
    for target in targets:
        if _is_external_or_root_url(target):
            continue
        rest, _query, _fragment = _split_url_suffix(target)
        try:
            resolved = (markdown_path.parent / rest).resolve()
            project_rel_path = resolved.relative_to(project_root)
        except (ValueError, OSError):
            continue
        try:
            if not resolved.is_file():
                continue
        except OSError:
            continue
        rel = str(project_rel_path).replace("\\", "/")
        seen[rel] = (rel, safe_raw_slug(rel), resolved)
    return [seen[k] for k in sorted(seen)]


# ---------------------------------------------------------------------------
# Page renderer
# ---------------------------------------------------------------------------


def _esc(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _format_size(num_bytes: int) -> str:
    """Format a byte count as a short human-readable string."""
    units = ("B", "KB", "MB", "GB")
    size = float(num_bytes)
    for unit in units:
        if size < 1024.0 or unit == units[-1]:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{num_bytes} B"


def _format_mtime(ts: float) -> str:
    """Format a POSIX mtime as ``YYYY-MM-DD`` (UTC).

    Date only, never time-of-day, so the rendered page stays stable across
    machines in different time zones (the user's idempotence invariant).
    """
    from datetime import datetime, timezone

    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def _render_breadcrumb_long_path(rel_path: str) -> str:
    """Return a breadcrumbs nav whose final entry shows the long raw path.

    We split on ``/`` and render every segment as a ``<span>`` — non-clickable
    intermediate path segments — followed by the filename as the current
    crumb. Long names get a ``title`` attribute with the full text so the
    truncated label is still discoverable on hover.
    """
    parts = [p for p in rel_path.split("/") if p]
    items: list[tuple[str, str]] = [("Home", "../index.html"), ("Raw", "")]
    for idx, segment in enumerate(parts):
        is_last = idx == len(parts) - 1
        label = segment
        # Truncate long labels for the visible crumb; the ``title`` attribute
        # below preserves the full text. We do this through the breadcrumbs
        # component rather than directly so the look matches every other page.
        if len(label) > 32:
            label = label[:29] + "..."
        if is_last:
            items.append((label, ""))
    # Note: we hand the breadcrumbs component an extended trail; the filename
    # is the "current page" crumb and the rest stay as plain spans (no href).
    if not parts:
        return breadcrumbs(items)
    return breadcrumbs(items)


def build_wiki_link_resolver(
    graph: object,
    page_slug_for_node: Optional[Mapping[str, str]] = None,
    canonical_slug: Optional[Callable[[str], str]] = None,
) -> WikiLinkResolver:
    """Build a :data:`WikiLinkResolver` from the graph.

    The graph carries ``Paper`` nodes (with ``metadata.arxiv_id``) and
    ``Repository`` / ``CodeProject`` / ``Project`` nodes (with
    ``metadata.github_repo`` like ``"owner/repo"``). The returned resolver
    maps:

      * ``("papers", "<arxiv-id>")`` — looks up the *repo companion* of that
        paper. Curated digests use ``papers/<id>/repo.md`` to mean "the
        GitHub analysis page for the repo paired with this paper". We
        match by ``metadata.arxiv_id`` against ``Repository``-kind nodes.
      * ``("repos", "owner/repo")`` — looks up a repo node by its
        ``metadata.github_repo`` field (case-insensitive).

    Slug resolution prefers ``page_slug_for_node`` (the on-disk slug
    written by the projector) and falls back to
    :func:`canonical_slug(node.name)` so the link is still self-consistent
    when no page exists yet.
    """
    page_slug_for_node = page_slug_for_node or {}

    if canonical_slug is None:
        # Late import to dodge the pages → raw_view import cycle.
        from .pages import _canonical_slug as canonical_slug  # type: ignore

    # Pre-index the graph once. We index every plausibly-public node-kind so
    # the resolver works for both ``Paper`` and the ``Repository`` family.
    repo_by_github: dict[str, object] = {}
    repo_by_arxiv: dict[str, object] = {}
    paper_by_arxiv: dict[str, object] = {}

    # ``Paper`` nodes whose ``title_quality`` is one of ``arxiv_only`` /
    # ``invalid`` / ``needs_metadata`` are intentionally hidden by the wiki
    # projector (see ``research_graph.is_public_research_node``). Indexing
    # them here would let the resolver mint hrefs to pages that were never
    # written, producing the bulk of the ``DANGLING_HTML_LINK`` findings.
    _verified_qualities = {"paper_file", "verified", "reference_context"}

    nodes = getattr(graph, "nodes", None) or []
    for node in nodes:
        node_type = getattr(node, "type", None)
        type_name = getattr(node_type, "value", node_type)
        type_str = str(type_name)
        meta = getattr(node, "metadata", None) or {}
        if not isinstance(meta, dict):
            continue
        if type_str in {"Repository", "CodeProject", "Project"}:
            github_repo = meta.get("github_repo")
            if isinstance(github_repo, str) and github_repo:
                repo_by_github.setdefault(github_repo.lower(), node)
            arxiv_id = meta.get("arxiv_id")
            if isinstance(arxiv_id, str) and arxiv_id:
                repo_by_arxiv.setdefault(arxiv_id, node)
        elif type_str == "Paper":
            quality = str(meta.get("title_quality") or "")
            if quality and quality not in _verified_qualities:
                continue
            arxiv_id = meta.get("arxiv_id")
            if isinstance(arxiv_id, str) and arxiv_id:
                paper_by_arxiv.setdefault(arxiv_id, node)

    def _slug_for(node: object) -> Optional[str]:
        node_id = getattr(node, "id", None)
        if isinstance(node_id, str):
            slug = page_slug_for_node.get(node_id)
            if slug:
                return slug
        name = getattr(node, "name", None)
        if isinstance(name, str) and name:
            slug = canonical_slug(name)
            if slug:
                return slug
        return None

    def resolve(kind: str, key: str) -> Optional[str]:
        if not kind or not key:
            return None
        if kind == "papers":
            # ``papers/<arxiv>/paper.md`` → the LLM-Wiki paper detail page.
            # We prefer the paper node here so the rewriter at the call
            # site builds a real ``../papers/<paper-slug>.html`` href; the
            # previous implementation preferred the repo companion which
            # produced ``../papers/<repo-slug>.html`` dangling hrefs (the
            # repo page lives under ``/repos/``, not ``/papers/``).
            node = paper_by_arxiv.get(key)
            if node is not None:
                slug = _slug_for(node)
                if slug:
                    return slug
            # Fallback: surface the repo companion's slug so callers that
            # still want the GitHub-analysis page (``papers/<id>/repo.md``)
            # can find it. The rewriter routes the result under ``/repos/``.
            node = repo_by_arxiv.get(key)
            if node is not None:
                return _slug_for(node)
            return None
        if kind == "papers-repo":
            # Explicit lookup for the curated ``papers/<arxiv>/repo.md``
            # shorthand: always return the repo companion's slug.
            node = repo_by_arxiv.get(key)
            if node is not None:
                return _slug_for(node)
            return None
        if kind == "repos":
            node = repo_by_github.get(key.lower())
            if node is not None:
                return _slug_for(node)
            return None
        return None

    return resolve


def _wrap_tables_in_scroll(html_text: str) -> str:
    """Wrap every top-level ``<table>...</table>`` in a ``.table-scroll`` div.

    Markdown engines emit raw ``<table>`` blocks that, when rendered inside
    ``.markdown-body`` / ``.article-body``, sometimes lose their cell borders
    on certain rendering paths because the outer table rule used to set
    ``display: block; overflow-x: auto`` to handle horizontal overflow on
    narrow viewports. The wrapping div carries the scroll affordance instead
    so the table itself stays a normal ``display: table`` element with
    ``border-collapse: collapse`` working as intended.

    Idempotent — does not double-wrap a table that already sits inside a
    ``<div class="table-scroll">``. Tables already wrapped (for example by
    :func:`components.node_table`) keep their existing wrapper.
    """
    if not html_text or "<table" not in html_text:
        return html_text

    # Find every <table>...</table> block (non-greedy across newlines), then
    # check whether the immediately preceding text already opens a
    # ``<div class="table-scroll">``. If it does, leave the block alone;
    # otherwise wrap it.
    out: list[str] = []
    cursor = 0
    for match in re.finditer(r"<table\b[^>]*>.*?</table>", html_text, flags=re.DOTALL):
        start, end = match.span()
        before = html_text[cursor:start]
        out.append(before)
        # Look at the trailing chunk of ``before`` for an existing wrapper.
        # We check the last ~120 chars — a reasonable window for whitespace
        # plus the opening ``<div class="table-scroll">``.
        tail = before[-160:].rstrip()
        already_wrapped = tail.endswith('<div class="table-scroll">')
        if already_wrapped:
            out.append(match.group(0))
        else:
            out.append('<div class="table-scroll">')
            out.append(match.group(0))
            out.append("</div>")
        cursor = end
    out.append(html_text[cursor:])
    return "".join(out)


def _unique_heading_anchors(
    html_text: str,
) -> Tuple[str, List[Tuple[int, str, str]]]:
    """Extract ``(level, text, anchor)`` triples for h2/h3 in ``html_text``.

    The markdown slugger (``_slug_anchor``) collapses non-ASCII characters
    to ``-`` and falls back to ``"section"`` when the heading text has no
    ASCII alphanumerics. Two H3s in Korean text therefore both end up with
    ``id="section"``, which would cause every TOC link in the rail to jump
    to the first one. We deduplicate the anchors here and rewrite the
    rendered HTML so each heading's ``id`` matches the unique anchor.

    Headings without an ``id`` attribute are skipped — there's no anchor
    to scroll-spy against. ``rail-section-label`` headings (used by the
    left rail) are skipped too in case any leak into the body.

    Returns ``(rewritten_html, headings)``.
    """
    if not html_text:
        return html_text, []

    seen: dict[str, int] = {}
    headings: List[Tuple[int, str, str]] = []

    def _replace(match: re.Match[str]) -> str:
        level = int(match.group(1))
        attrs = match.group(2) or ""
        inner = match.group(3) or ""
        id_match = _HEADING_ID_RE.search(attrs)
        if not id_match:
            # No anchor → no scrollspy target. Leave the heading alone.
            return match.group(0)
        class_match = _HEADING_CLASS_RE.search(attrs)
        if class_match and "rail-section-label" in class_match.group(1):
            return match.group(0)
        original_id = id_match.group(1)
        count = seen.get(original_id, 0) + 1
        seen[original_id] = count
        if count == 1:
            unique_id = original_id
        else:
            unique_id = f"{original_id}-{count}"
        # Strip HTML tags from the label so ``<code>foo</code>`` becomes ``foo``.
        label = _HEADING_TAG_STRIP_RE.sub("", inner)
        # Decode the few entities the inline renderer emits so the TOC label
        # isn't double-escaped when the ``toc()`` builder calls ``_esc``.
        label = (
            label.replace("&amp;", "&")
            .replace("&lt;", "<")
            .replace("&gt;", ">")
            .replace("&quot;", '"')
            .replace("&#39;", "'")
        )
        label = label.strip()
        if not label:
            return match.group(0)
        headings.append((level, label, unique_id))
        if unique_id == original_id:
            return match.group(0)
        # Rewrite the id= so the anchor in the body matches the TOC link.
        new_attrs = _HEADING_ID_RE.sub(f'id="{unique_id}"', attrs, count=1)
        return f"<h{level}{new_attrs}>{inner}</h{level}>"

    rewritten = _HEADING_TAG_RE.sub(_replace, html_text)
    return rewritten, headings


def _render_markdown_body(
    absolute: Path,
    project_root: Optional[Path] = None,
    project_relative_path: Optional[str] = None,
    wiki_link_resolver: Optional[WikiLinkResolver] = None,
) -> str:
    """Render the raw markdown body and rewrite neighbor ``.md`` links.

    Relative links inside a raw paper file (e.g. ``[repo](repo.md)`` or
    ``[paper](../2604.20329/paper.md)``) used to stay literal, which
    produced 404s under ``/raw/`` since the raw viewer flattens every
    source path into a single ``raw/<safe>.html`` slug. The rewriter
    here resolves any relative ``.md`` link against the raw doc's own
    directory, computes the project-relative path, and calls
    ``raw_href(...)`` so the link lands on the corresponding raw page.
    Cross-arxiv ``papers/<id>/(paper|main|abstract).md`` links still go
    out to arxiv.org via the same ``arxiv_paper_match`` rule that
    ``pages._wiki_link_rewriter`` uses.

    Curated digests ship pseudo-paths like ``papers/<arxiv>/repo.md`` or
    ``repos/<owner>_<repo>.md`` that don't exist on disk but DO have a
    canonical analysis page in the rendered site. ``wiki_link_resolver``
    (when threaded in) maps those tokens to the on-disk slug so the link
    lands on the real ``../repos/<slug>.html`` / ``../papers/<slug>.html``
    instead of 404'ing.
    """
    text = absolute.read_text(encoding="utf-8", errors="replace")

    def _link_rewriter(target: str) -> str:
        if _is_external_or_root_url(target):
            return target
        # Split off fragment + query so the remainder is a clean path.
        rest, query, fragment = _split_url_suffix(target)
        # Project-file references in docs (``[research_graph.py](../llm_wiki/research_graph.py)``)
        # used to fall through unchanged and 404. The raw viewer can render
        # any project-relative file path, so resolve these against the doc's
        # own directory and emit a ``../raw/<safe>.html`` href.
        if (
            project_root is not None
            and not rest.endswith(".md")
            and "." in rest.rsplit("/", 1)[-1]
            and not any(part in {".", ".."} for part in rest.split("/")[-1:])
        ):
            try:
                resolved = (absolute.parent / rest).resolve()
                if resolved.is_file():
                    project_rel = resolved.relative_to(project_root)
                    project_rel_s = str(project_rel).replace("\\", "/")
                    if is_binary_extension(resolved.suffix):
                        return f"{_raw_asset_href_for_project_rel(project_rel_s, depth=1)}{query}{fragment}"
                    href = raw_href(project_root, project_rel_s, depth=1)
                    if href:
                        return f"{href}{query}{fragment}"
            except (ValueError, OSError):
                pass
        if not rest.endswith(".md"):
            return target
        # arxiv-paper shorthand: papers/<id>/paper|main|abstract.md.
        # Priority order:
        #   (1) ../papers/<slug>.html — the LLM-Wiki paper page is where
        #       the analysis lives, so a "[논문 분석]" link should land
        #       there first.
        #   (2) https://arxiv.org/abs/<id> — fall back to arxiv only when
        #       the wiki has no extracted paper page yet.
        m = re.fullmatch(
            r"(?:\.\./)*papers/(\d{4}\.\d{4,6})/(?:paper|main|abstract)\.md",
            rest,
            flags=re.IGNORECASE,
        )
        if m:
            arxiv_id = m.group(1)
            if wiki_link_resolver is not None:
                slug = wiki_link_resolver("papers", arxiv_id)
                if slug:
                    return f"../papers/{slug}.html{query}{fragment}"
            return f"https://arxiv.org/abs/{arxiv_id}{query}{fragment}"
        # arxiv-repo shorthand: papers/<id>/repo.md → canonical repo page.
        # Weekly digests prefix the same shorthand with ``daily/<date>/``;
        # accept both forms so links from weekly summaries also resolve.
        if wiki_link_resolver is not None:
            m_repo = re.fullmatch(
                r"(?:\.\./)*(?:daily/\d{4}-\d{2}-\d{2}/)?papers/(\d{4}\.\d{4,6})/repo\.md",
                rest,
                flags=re.IGNORECASE,
            )
            if m_repo:
                slug = wiki_link_resolver("papers-repo", m_repo.group(1))
                if slug:
                    return f"../repos/{slug}.html{query}{fragment}"
            # Weekly-digest paper shorthand: ``daily/<date>/papers/<id>/(paper|main|abstract).md``.
            m_paper_daily = re.fullmatch(
                r"(?:\.\./)*daily/\d{4}-\d{2}-\d{2}/papers/(\d{4}\.\d{4,6})/(?:paper|main|abstract)\.md",
                rest,
                flags=re.IGNORECASE,
            )
            if m_paper_daily:
                arxiv_id = m_paper_daily.group(1)
                slug = wiki_link_resolver("papers", arxiv_id)
                if slug:
                    return f"../papers/{slug}.html{query}{fragment}"
                return f"https://arxiv.org/abs/{arxiv_id}{query}{fragment}"
            # repo shorthand: repos/<owner>_<repo>.md → canonical repo page.
            # ``daily/<date>/repos/<owner>_<repo>.md`` is the weekly digest
            # form of the same convention.
            m_repo2 = re.fullmatch(
                r"(?:\.\./)*(?:daily/\d{4}-\d{2}-\d{2}/)?repos/([^/]+)\.md",
                rest,
                flags=re.IGNORECASE,
            )
            if m_repo2:
                stem = m_repo2.group(1)
                # ``Owner_Repo`` underscored → ``Owner/Repo`` for lookup.
                key = stem.replace("_", "/", 1)
                slug = wiki_link_resolver("repos", key)
                if slug:
                    return f"../repos/{slug}.html{query}{fragment}"
        if project_root is None:
            return target
        # Resolve the relative path against the raw doc's own directory.
        try:
            resolved = (absolute.parent / rest).resolve()
            project_rel = resolved.relative_to(project_root)
        except (ValueError, OSError):
            return target
        href = raw_href(project_root, str(project_rel), depth=1)
        if not href:
            return target
        return f"{href}{query}{fragment}"

    def _rewrite_raw_html_url_attrs(markdown_text: str) -> str:
        def repl(match: re.Match[str]) -> str:
            prefix = match.group("prefix")
            quote = match.group("quote")
            url = match.group("url")
            rewritten = _link_rewriter(url)
            return f"{prefix}{quote}{rewritten.replace(quote, '')}{quote}"

        return _HTML_URL_ATTR_RE.sub(repl, markdown_text)

    body, _ = render_markdown(_rewrite_raw_html_url_attrs(text), link_rewriter=_link_rewriter)
    body = _wrap_tables_in_scroll(body)
    return f'<section class="markdown-body raw-markdown">{body}</section>'


def _render_text_body(absolute: Path) -> str:
    try:
        text = absolute.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f'<p class="muted">Unable to read file: {_esc(exc)}</p>'
    return f'<pre class="raw-text"><code>{html.escape(text)}</code></pre>'


def _render_data_body(absolute: Path) -> str:
    suffix = absolute.suffix.lower()
    try:
        text = absolute.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f'<p class="muted">Unable to read file: {_esc(exc)}</p>'
    pretty = text
    if suffix == ".json":
        try:
            pretty = json.dumps(
                json.loads(text), indent=2, ensure_ascii=False, sort_keys=True
            )
        except (json.JSONDecodeError, ValueError):
            pretty = text
    lang_class = f' class="language-{suffix.lstrip(".")}"' if suffix else ""
    return f'<pre class="raw-text"><code{lang_class}>{html.escape(pretty)}</code></pre>'


def _render_html_body(absolute: Path) -> str:
    try:
        text = absolute.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f'<p class="muted">Unable to read file: {_esc(exc)}</p>'
    # Render inside an iframe with srcdoc so the embedded HTML's CSS / scripts
    # can't bleed into the wiki chrome.
    return (
        '<iframe class="raw-html-frame" sandbox="allow-same-origin" '
        'title="Embedded HTML preview" '
        f'srcdoc="{_esc(text)}"></iframe>'
    )


def _render_image_body(asset_href: str, alt: str) -> str:
    return (
        f'<figure class="raw-asset raw-image">'
        f'<img src="{_esc(asset_href)}" alt="{_esc(alt)}" loading="lazy">'
        f"</figure>"
    )


def _render_pdf_body(asset_href: str) -> str:
    return (
        f'<div class="raw-asset raw-pdf">'
        f'<embed src="{_esc(asset_href)}" type="application/pdf" '
        f'width="100%" height="900">'
        f'<p class="muted small"><a href="{_esc(asset_href)}">'
        f"Download PDF</a></p>"
        f"</div>"
    )


def _render_download_body(asset_href: str, *, oversized: bool) -> str:
    note = "binary or oversized — download to view"
    return (
        f'<div class="raw-asset raw-download">'
        f'<p><a href="{_esc(asset_href)}" download>Download original file</a></p>'
        f'<p class="muted small">{_esc(note)}</p>'
        f"</div>"
    )


def render_raw_view(
    *,
    site_title: str,
    project_relative_path: str,
    absolute_path: Path,
    asset_filename: Optional[str] = None,
    counts: Optional[dict] = None,
    doc_tree_html: str = "",
    wiki_link_resolver: Optional[WikiLinkResolver] = None,
) -> str:
    """Render the full ``raw/<safe>.html`` document.

    ``asset_filename`` (when provided) is the basename written under
    ``raw-assets/`` for binary types — the renderer points the embed/img/href
    at ``../raw-assets/<asset_filename>``. For text-style files the asset isn't
    needed and this argument is ignored.
    """
    suffix = absolute_path.suffix.lower()
    try:
        stat = absolute_path.stat()
    except OSError:
        size_label = "unknown"
        mtime_label = ""
    else:
        size_label = _format_size(stat.st_size)
        mtime_label = _format_mtime(stat.st_mtime)

    asset_href = (
        f"../{RAW_ASSETS_DIR}/{asset_filename}" if asset_filename else ""
    )

    # Recover project_root by stripping the project-relative path off the
    # absolute path. We use this to resolve relative ``.md`` links inside
    # raw markdown bodies into ``raw/<safe>.html`` URLs.
    project_root: Optional[Path] = None
    try:
        rel_str = project_relative_path.replace("\\", "/")
        abs_str = str(absolute_path).replace("\\", "/")
        if abs_str.endswith(rel_str):
            candidate = abs_str[: -len(rel_str)].rstrip("/")
            if candidate:
                project_root = Path(candidate)
    except (TypeError, ValueError):
        project_root = None

    if is_markdown_source_path(absolute_path):
        body_html = _render_markdown_body(
            absolute_path,
            project_root=project_root,
            project_relative_path=project_relative_path,
            wiki_link_resolver=wiki_link_resolver,
        )
    elif suffix in _TEXT_EXTS and absolute_path.stat().st_size <= _TEXT_INLINE_LIMIT:
        body_html = _render_text_body(absolute_path)
    elif suffix in _DATA_EXTS and absolute_path.stat().st_size <= _DATA_INLINE_LIMIT:
        body_html = _render_data_body(absolute_path)
    elif suffix in _HTML_EXTS:
        body_html = _render_html_body(absolute_path)
    elif suffix in _PDF_EXTS:
        body_html = _render_pdf_body(asset_href) if asset_href else _render_download_body(asset_href, oversized=False)
    elif suffix in _IMAGE_EXTS:
        body_html = _render_image_body(asset_href, alt=project_relative_path) if asset_href else _render_download_body(asset_href, oversized=False)
    else:
        body_html = _render_download_body(asset_href, oversized=True)

    # Extract h2/h3 anchors from the rendered body so the right rail
    # ("On this page") can scroll-spy through the markdown sections. The
    # helper also dedupes any colliding ids the slugger emitted (common for
    # non-ASCII headings that all collapse to ``id="section"``) and rewrites
    # the body HTML so each heading's id matches the unique TOC anchor.
    body_html, headings = _unique_heading_anchors(body_html)
    toc_html = toc(headings) if headings else ""

    eyebrow_bits: list[str] = []
    if size_label:
        eyebrow_bits.append(size_label)
    if mtime_label:
        eyebrow_bits.append(f"updated {mtime_label}")
    ext_label = suffix.lstrip(".") or "file"
    eyebrow_bits.append(ext_label)
    eyebrow = (
        f'<p class="eyebrow raw-meta">{_esc(" · ".join(eyebrow_bits))}</p>'
    )

    bc = _render_breadcrumb_long_path(project_relative_path)
    title = absolute_path.name or project_relative_path

    body = f"""{eyebrow}
<h1 class="raw-title" title="{_esc(project_relative_path)}">{_esc(title)}</h1>
<p class="raw-path"><code>{_esc(project_relative_path)}</code></p>
<article class="raw-page">{body_html}</article>"""

    return page_shell(
        title=title,
        head="",
        body=body,
        depth=1,
        active="sources",
        site_title=site_title,
        counts=dict(counts or {}),
        breadcrumbs_html=bc,
        doc_tree_html=doc_tree_html,
        toc_html=toc_html,
    )
