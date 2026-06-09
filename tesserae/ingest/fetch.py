"""Fetch a URL into a tracked markdown source. Behind the [ingest-url] extra."""

from __future__ import annotations

import hashlib
import re
from typing import Dict


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
