"""Source loader port: input adapter interface for LLM-Wiki extraction.

A `SourceLoader` yields `Source` records (extraction substrates with stable
ids) into the pipeline. Implementations include `FilesystemSourceLoader`
(walks markdown directories) and `HypePaperSourceLoader` (reads papers and
per-language analyses from Postgres).
"""

from __future__ import annotations

from typing import Any, Dict, Iterator, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, Field


class Source(BaseModel):
    """A single extraction substrate handed to the pipeline.

    Fields:
        id: Stable identifier (paper UUID for HypePaper, file hash for FS).
        path: Human-readable location for citations
            (e.g. ``"hypepaper://paper/{uuid}"`` or ``"file:///path/to.md"``).
            ``None`` when the source has no canonical location.
        content: The actual extraction substrate (markdown, fulltext, ...).
        metadata: Free-form metadata: references, language,
            substrate_provenance, etc.
    """

    id: str
    path: Optional[str] = None
    content: str
    metadata: Dict[str, Any] = Field(default_factory=dict)


@runtime_checkable
class SourceLoader(Protocol):
    """Port for discovering and fetching extraction sources."""

    def discover(self) -> Iterator[Source]:
        """Yield all sources currently visible to this loader."""
        ...

    def fetch(self, source_id: str) -> Source:
        """Fetch a single source by its stable id."""
        ...
