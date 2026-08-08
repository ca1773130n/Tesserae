"""Fetch a URL into a tracked markdown source. Behind the [ingest-url] extra."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Optional


class UnsupportedSourceError(ValueError):
    """A source exists and is reachable, but nothing in Tesserae can read it.

    Distinct from :class:`FileNotFoundError` (the input is not there) and from a
    transport error (the fetch failed): the bytes arrived, and refusing them is
    the correct outcome. ``tesserae.cli.main`` catches this centrally and turns
    it into a one-line message plus exit 2, never a traceback.
    """


def is_url(value: str) -> bool:
    """True only for http(s) URLs — everything else is treated as a local path."""
    return value.startswith("http://") or value.startswith("https://")


_SLUG_MAX = 80


def _slugify(value: str) -> str:
    """Filesystem-safe, deterministic slug. Long values get a hash suffix to avoid collisions."""
    stripped = re.sub(r"^https?://", "", value)
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", stripped).strip("-").lower()
    if len(slug) <= _SLUG_MAX:
        return slug
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
    return slug[: _SLUG_MAX - 9].rstrip("-") + "-" + digest


# A bare YAML scalar is unsafe if it contains a newline, a colon acting as a
# mapping indicator (``:`` followed by whitespace or at end of value), a comment
# indicator (whitespace then ``#``), a quote, or has leading/trailing whitespace.
# A colon inside a token like ``https://x.com`` is safe and stays unquoted.
_UNSAFE_SCALAR_RE = re.compile(r"""[\n"']|:(?:\s|$)|(?:^|\s)#""")


def _yaml_scalar(value: str) -> str:
    """Render a string as a YAML scalar, quoting only when it would be unsafe bare.

    Unsafe values (newlines, mapping/comment indicators, quotes, edge whitespace)
    are emitted as a JSON string — valid, round-trippable YAML. Simple values pass
    through unquoted so common front-matter (URLs, hashes, timestamps) stays readable.
    """
    if value != value.strip() or _UNSAFE_SCALAR_RE.search(value):
        return json.dumps(value)
    return value


def _render_frontmatter(meta: Dict[str, str]) -> str:
    """Render a deterministic YAML front-matter block (keys sorted)."""
    lines = ["---"]
    for key in sorted(meta):
        lines.append(f"{key}: {_yaml_scalar(meta[key])}")
    lines.append("---")
    return "\n".join(lines) + "\n"


_ARXIV_RE = re.compile(r"arxiv\.org/abs/(\d{4}\.\d{4,5})")


def _arxiv_id_from_url(url: str) -> Optional[str]:
    m = _ARXIV_RE.search(url)
    return m.group(1) if m else None


# Non-``text/*`` media types whose bodies are still character data we can
# reasonably persist as markdown. Anything else declared is binary to us.
_TEXTY_APPLICATION_TYPES = (
    "application/json",
    "application/xml",
    "application/xhtml+xml",
    "application/atom+xml",
    "application/rss+xml",
    "application/ld+json",
    "application/javascript",
)


def _is_texty(content_type: str) -> bool:
    """True when a declared content-type names character data, not bytes."""
    media = content_type.split(";", 1)[0].strip().lower()
    if media.startswith("text/"):
        return True
    if media.endswith("+json") or media.endswith("+xml"):
        return True
    return media in _TEXTY_APPLICATION_TYPES


_http_get: Optional[Callable] = None
_html_to_markdown: Optional[Callable] = None


def _load_url_deps() -> None:
    """Bind _http_get / _html_to_markdown from the optional extra, or raise."""
    global _http_get, _html_to_markdown
    if _http_get is not None and _html_to_markdown is not None:
        return
    try:
        import httpx
        from markdownify import markdownify as _md
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "URL ingest requires the optional extra: pip install tesserae[ingest-url]"
        ) from exc
    if _http_get is None:
        _http_get = lambda url, timeout=None, follow_redirects=True, headers=None: httpx.get(
            url, timeout=timeout, follow_redirects=follow_redirects, headers=headers or {}
        )
    if _html_to_markdown is None:
        _html_to_markdown = lambda html: _md(html)


def fetch_to_source(url: str, dest_dir: Path, *, title: Optional[str] = None) -> Path:
    """Fetch ``url``, convert to markdown, persist under ``dest_dir`` with provenance.

    Returns the written file path. Raises on non-2xx, on a missing [ingest-url] extra,
    and writes nothing on failure.
    """
    if _http_get is None or _html_to_markdown is None:
        _load_url_deps()

    response = _http_get(url, timeout=30.0, follow_redirects=True, headers={"User-Agent": "tesserae-ingest"})
    response.raise_for_status()
    content_type = response.headers.get("content-type", "")

    # Refuse BEFORE touching ``.text``. Decoding a PDF/image body produced
    # mojibake, hashed the mojibake as ``content_sha256`` (a hash over garbage,
    # not over what the server sent), and wrote the result under a ``.md``
    # suffix — so unlike a local PDF, which the markdown walker at least
    # ignores, this one WAS picked up and fed to the extractor as prose.
    #
    # Known gap: a server that sends no content-type at all still reaches the
    # decode below. Sniffing magic bytes would close it, but declared-type is
    # what the header is for and every real offender (arxiv.org/pdf/...) sets it.
    if content_type and not _is_texty(content_type):
        raise UnsupportedSourceError(
            f"tesserae ingest cannot read this URL — it served {content_type.split(';')[0].strip()}, not text:\n"
            f"  - {url}: Download it and convert it to markdown, then "
            f"`tesserae ingest <file>.md`. RAG-Anything parses PDFs and images, "
            f"but only from local files via `tesserae refresh raganything`."
        )

    raw = response.text
    if "html" in content_type:
        body = _html_to_markdown(raw)
    else:
        body = raw

    sha = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    meta = {
        "source_url": url,
        "content_sha256": sha,
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if title:
        meta["title"] = title

    arxiv_id = _arxiv_id_from_url(url)
    if arxiv_id:
        meta["arxiv_id"] = arxiv_id

    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / f"{_slugify(url)}.md"
    path.write_text(_render_frontmatter(meta) + "\n" + body.strip() + "\n", encoding="utf-8")
    return path
