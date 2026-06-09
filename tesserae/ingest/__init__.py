"""Single-source ingest: merge one document or URL into the KB.

See docs/superpowers/specs/2026-06-10-ingest-command-design.md.
"""

from tesserae.ingest.fetch import fetch_to_source, is_url
from tesserae.ingest.orchestrator import ingest_sources

__all__ = ["ingest_sources", "fetch_to_source", "is_url"]
