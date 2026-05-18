"""Multi-key path index for resolving ``files_touched`` entries to graph node IDs.

The session graph extractor's structural pass needs to answer "what doc
node was the agent looking at when it touched this file path?" The
answer is non-trivial because ``source_path`` values in the existing
graph come from at least five different conventions:

* absolute (``/Users/neo/Developer/.../paper.md``),
* project-relative (``data/research/.../paper.md``),
* POSIX-normalized of either of the above on Windows checkouts,
* raw loader IDs from non-filesystem source loaders, and
* historical values that pre-date the resolve() normalization landing
  in the codebase.

``files_touched`` on a `HarnessSession` is even more varied: Claude
Code emits absolute paths, Codex emits project-relative ones, and
older transcripts sometimes carry both.

A single ``path → node_id`` map keyed on one of those forms would
silently miss half the legitimate matches. The :class:`DocPathIndex`
indexes every plausible form per node at build time and resolves
queries through the same forms in confidence-decreasing order.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

from .research_graph import ResearchGraph, ResearchNode


@dataclass
class DocPathIndex:
    """Resolves a file-path query to a graph node ID across path conventions.

    Build via :meth:`from_graph`. Query via :meth:`lookup`. The
    confidence tiers in the lookup keep ``basename``-only matches from
    swallowing files that legitimately share a name (e.g. every paper
    directory has a ``paper.md``).
    """

    project_root: Path
    # High-confidence keys (resolved absolute, project-relative, POSIX, raw).
    # Each form maps to at most one node id.
    _absolute: Dict[str, str] = field(default_factory=dict)
    _project_relative: Dict[str, str] = field(default_factory=dict)
    _posix: Dict[str, str] = field(default_factory=dict)
    _raw: Dict[str, str] = field(default_factory=dict)
    # Low-confidence fallback. Same basename can resolve to multiple
    # node ids — we only use it when no high-confidence tier matched
    # AND only when exactly one node owns the basename.
    _basename: Dict[str, str] = field(default_factory=dict)
    _basename_collisions: set = field(default_factory=set)

    @classmethod
    def from_graph(cls, graph: ResearchGraph, project_root: Path | str) -> "DocPathIndex":
        idx = cls(project_root=Path(project_root).resolve())
        for node in graph.nodes:
            idx._index_node(node)
        return idx

    def _index_node(self, node: ResearchNode) -> None:
        source_path = (node.source_path or "").strip()
        if not source_path:
            return
        node_id = node.id

        # Raw — defensive against loader-id strings that aren't real paths.
        self._raw.setdefault(source_path, node_id)

        # Treat the source_path as a filesystem path for all the rest.
        # Don't actually require it to exist on disk — historical
        # transcripts might reference files that were renamed since.
        candidate = Path(source_path)
        # If the source_path is relative, anchor it to project_root.
        anchored = (
            candidate
            if candidate.is_absolute()
            else (self.project_root / candidate)
        )

        # Resolve symlinks where possible; fall back to absolute() when the
        # file doesn't exist on disk (Path.resolve() with strict=False is
        # the default and tolerates missing components).
        absolute = str(anchored.resolve())
        self._absolute.setdefault(absolute, node_id)

        # Project-relative — only stored when the file is under the project
        # root, otherwise this key collapses with absolute and adds nothing.
        try:
            relative = str(Path(absolute).relative_to(self.project_root))
            self._project_relative.setdefault(relative, node_id)
            # POSIX-normalize the relative form so Windows checkouts on the
            # same vault don't lose links.
            self._posix.setdefault(relative.replace(os.sep, "/"), node_id)
        except ValueError:
            # source_path resolves outside project_root — skip the
            # project-relative tier.
            pass

        # POSIX-normalize the absolute form too.
        self._posix.setdefault(absolute.replace(os.sep, "/"), node_id)

        # Basename — track collisions so we can suppress the fallback when
        # multiple nodes own the same basename.
        basename = Path(absolute).name
        if basename in self._basename and self._basename[basename] != node_id:
            self._basename_collisions.add(basename)
        else:
            self._basename.setdefault(basename, node_id)

    def lookup(self, query: str) -> Optional[str]:
        """Return the node ID matching ``query``, or ``None``.

        Tries each key tier in decreasing-confidence order. ``basename``
        only fires when no other tier matched AND the basename uniquely
        identifies a single node (no collisions across the graph).
        """
        if not query:
            return None
        q = query.strip()
        if not q:
            return None

        # Tier 1 — raw match (handles loader-id-style source_paths and
        # exact-match historical strings).
        if q in self._raw:
            return self._raw[q]

        # Tier 2 — resolve the query the same way we resolved nodes.
        candidate = Path(q)
        anchored = (
            candidate
            if candidate.is_absolute()
            else (self.project_root / candidate)
        )
        absolute = str(anchored.resolve())
        if absolute in self._absolute:
            return self._absolute[absolute]

        # Tier 3 — project-relative.
        try:
            relative = str(Path(absolute).relative_to(self.project_root))
            if relative in self._project_relative:
                return self._project_relative[relative]
            posix = relative.replace(os.sep, "/")
            if posix in self._posix:
                return self._posix[posix]
        except ValueError:
            pass

        # Tier 4 — POSIX of the absolute form (catches Windows-on-mac
        # roundtrips).
        if absolute.replace(os.sep, "/") in self._posix:
            return self._posix[absolute.replace(os.sep, "/")]

        # Tier 5 — basename, ONLY when unambiguous.
        basename = Path(q).name
        if basename and basename not in self._basename_collisions:
            if basename in self._basename:
                return self._basename[basename]

        return None
