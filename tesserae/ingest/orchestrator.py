"""Orchestrate single-source ingest: resolve inputs, (fetch URLs), drive compile, report.

This is the B->C seam: v1 drives compile synchronously. A future v2 (--async) splits the
compile() call into an instant approximate write + an enqueued reconcile.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Sequence

from tesserae.ingest.fetch import fetch_to_source, is_url


def ingest_sources(
    wiki,
    inputs: Sequence[str],
    *,
    source_kind: Optional[str] = None,
    title: Optional[str] = None,
    exact: bool = True,
    dry_run: bool = False,
) -> dict:
    """Ingest one or more file paths / URLs into ``wiki``'s knowledge base.

    ``exact=True`` forces a full recompile (correct by construction). ``exact=False`` opts
    the run into the incremental fast path (Phase 2). Returns a report dict with keys:
    ``path_taken``, ``node_count``, ``edge_count``, ``processed_files``, ``skipped_files``,
    ``graph_path``, ``sources`` (resolved input paths).
    """
    resolved: List[str] = []
    dest = Path(wiki.project_root) / "data" / "ingested"
    for item in inputs:
        if is_url(item):
            path = fetch_to_source(item, dest, title=title)
            resolved.append(str(path))
        else:
            resolved.append(item)

    if dry_run:
        return {
            "path_taken": "dry-run",
            "sources": resolved,
            "node_count": 0,
            "edge_count": 0,
            "processed_files": 0,
            "skipped_files": 0,
            "graph_path": str(wiki.paths.graph),
        }

    if exact:
        result = wiki.ingest(resolved, source_kind=source_kind, changed_only=False)
        path_taken = "full-recompile"
    else:
        result = wiki.ingest(
            resolved, source_kind=source_kind, changed_only=True, incremental_override=True
        )
        path_taken = "incremental"
    return {
        "path_taken": path_taken,
        "sources": resolved,
        "node_count": result["node_count"],
        "edge_count": result["edge_count"],
        "processed_files": result["processed_files"],
        "skipped_files": result["skipped_files"],
        "graph_path": result["graph_path"],
    }
