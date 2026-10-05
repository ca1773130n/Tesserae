"""Content resolver port (0.42, T5): fetch a node's content by pointer.

A graph node is an index entry, not the content. Tesserae's own projects keep
the content next to the graph (the wiki page, the source document), but a host
like HypePaper keeps it elsewhere — TL;DRs, key ideas, deep-analysis sections,
result tables — and puts typed pointers on the nodes instead of copies. This
port lets :func:`tesserae.context_compiler.compile_context` ask the host for
that content while it fills the bundle, so a selected node contributes its
evidence instead of its title.

The contract is deliberately small and synchronous (``compile_context`` is a
pure, sync function):

* :meth:`ContentResolver.list_views` names what is available for a node and
  what each piece would cost, WITHOUT fetching it. It must be cheap.
* :meth:`ContentResolver.fetch` returns at most ``max_chars`` of one view.

Both must degrade, never raise into the compiler: return ``[]`` / ``""`` for a
node or ref the host cannot serve. ``compile_context`` additionally swallows
any exception a resolver raises and falls back to the wiki page / description,
so a broken resolver costs content, never the bundle.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, Sequence, runtime_checkable

if TYPE_CHECKING:  # pragma: no cover - import cycle guard for type checkers
    from ..research_graph import ResearchNode

__all__ = ["ContentResolver", "ContentView"]


@dataclass(frozen=True)
class ContentView:
    """One fetchable piece of a node's content.

    ``ref`` is opaque to Tesserae (HypePaper uses ``hp://`` URIs). ``est_chars``
    is the host's cost estimate and drives the cheapest-first fill. ``prose``
    marks running text (full-text spans, analysis sections) as opposed to
    dense distillations (TL;DR, key ideas, result rows): prose obeys the same
    measured crossover as raw source excerpts — below
    ``context_compiler._MIN_SOURCE_EXCERPT`` characters per node it is worse
    evidence than the distillation it would replace, so it is skipped.
    """

    ref: str
    kind: str = ""
    est_chars: int = 0
    prose: bool = False
    title: str = ""


@runtime_checkable
class ContentResolver(Protocol):
    """Port for fetching node content from where the host keeps it."""

    def list_views(self, node: "ResearchNode") -> Sequence[ContentView]:
        """The views available for ``node``, cheapest information first is NOT
        required — the compiler orders them. Empty when there are none."""
        ...

    def fetch(self, ref: str, max_chars: int) -> str:
        """At most ``max_chars`` characters of the view ``ref``; ``""`` if absent."""
        ...
