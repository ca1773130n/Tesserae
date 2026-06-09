"""Single-source ingest: merge one document or URL into the KB.

See docs/superpowers/specs/2026-06-10-ingest-command-design.md.
"""

from tesserae.ingest.fetch import is_url

__all__ = ["ingest_sources", "fetch_to_source", "is_url"]


def __getattr__(name: str):
    # Resolve heavier symbols lazily so importing fetch helpers does not
    # require the orchestrator module (added in a later task) to exist yet.
    if name == "ingest_sources":
        from tesserae.ingest.orchestrator import ingest_sources

        return ingest_sources
    if name == "fetch_to_source":
        from tesserae.ingest.fetch import fetch_to_source

        return fetch_to_source
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
