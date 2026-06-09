"""Fetch a URL into a tracked markdown source. Behind the [ingest-url] extra."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Optional


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


def _render_frontmatter(meta: Dict[str, str]) -> str:
    """Render a deterministic YAML front-matter block (keys sorted)."""
    lines = ["---"]
    for key in sorted(meta):
        lines.append(f"{key}: {meta[key]}")
    lines.append("---")
    return "\n".join(lines) + "\n"


_ARXIV_RE = re.compile(r"arxiv\.org/abs/(\d{4}\.\d{4,5})")


def _arxiv_id_from_url(url: str) -> Optional[str]:
    m = _ARXIV_RE.search(url)
    return m.group(1) if m else None


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
