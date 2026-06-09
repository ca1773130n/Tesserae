"""Fetch a URL into a tracked markdown source. Behind the [ingest-url] extra."""

from __future__ import annotations


def is_url(value: str) -> bool:
    """True only for http(s) URLs — everything else is treated as a local path."""
    return value.startswith("http://") or value.startswith("https://")
