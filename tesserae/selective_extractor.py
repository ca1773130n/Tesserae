"""Selective Claude enrichment for cost-aware extraction."""

from __future__ import annotations

from fnmatch import fnmatch
from pathlib import Path
from typing import Optional, Protocol, Sequence

from .research_graph import ResearchGraph


class ExtractorLike(Protocol):
    def extract_file(self, path: str | Path, source_kind: str = "SourceDocument") -> ResearchGraph: ...


class SelectiveClaudeResearchExtractor:
    """Route only selected documents through Claude, fallback to deterministic extraction.

    This keeps the whole corpus process cheap while allowing manual/targeted
    Claude enrichment for important papers or path subsets.
    """

    def __init__(
        self,
        deterministic: ExtractorLike,
        claude: ExtractorLike,
        include_patterns: Sequence[str],
        claude_limit: Optional[int] = None,
    ) -> None:
        self.deterministic = deterministic
        self.claude = claude
        self.include_patterns = list(include_patterns)
        self.claude_limit = claude_limit
        self.claude_calls = 0

    @property
    def guidance(self) -> str:
        return getattr(self.claude, "guidance", "")

    @guidance.setter
    def guidance(self, value: str) -> None:
        # Forward extraction-feedback guidance to the Claude sub-extractor;
        # the deterministic baseline ignores guidance entirely.
        if hasattr(self.claude, "guidance"):
            self.claude.guidance = value

    def extract_file(self, path: str | Path, source_kind: str = "SourceDocument") -> ResearchGraph:
        file_path = Path(path)
        if self._should_use_claude(file_path):
            self.claude_calls += 1
            return self.claude.extract_file(file_path, source_kind=source_kind)
        return self.deterministic.extract_file(file_path, source_kind=source_kind)

    def _should_use_claude(self, path: Path) -> bool:
        if not self.include_patterns:
            return False
        if self.claude_limit is not None and self.claude_calls >= self.claude_limit:
            return False
        path_text = str(path)
        return any(fnmatch(path_text, pattern) or fnmatch(path.name, pattern) for pattern in self.include_patterns)
