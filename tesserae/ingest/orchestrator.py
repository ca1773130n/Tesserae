"""Orchestrate single-source ingest: resolve inputs, (fetch URLs), drive compile, report.

This is the B->C seam: v1 drives compile synchronously. A future v2 (--async) splits the
compile() call into an instant approximate write + an enqueued reconcile.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import List, Optional, Sequence

from tesserae.ingest.fetch import fetch_to_source, is_url


def _ensure_in_corpus(wiki, path_str: str) -> str:
    """Return a path INSIDE the project corpus.

    Files already under the project root are used in place (compile() will discover them).
    Files outside the project root are copied into ``data/ingested/`` so a later full compile
    reproduces them identically.
    """
    root = Path(wiki.project_root).resolve()
    p = Path(path_str).resolve()
    try:
        p.relative_to(root)
        return str(p)
    except ValueError:
        dest_dir = root / "data" / "ingested"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / p.name
        shutil.copy2(p, dest)
        return str(dest)


def ingest_sources(
    wiki,
    inputs: Sequence[str],
    *,
    source_kind: Optional[str] = None,
    title: Optional[str] = None,
    exact: bool = False,
    dry_run: bool = False,
    lock_wait: Optional[float] = None,
) -> dict:
    """Ingest one or more file paths / URLs into ``wiki``'s knowledge base.

    The default (``exact=False``) takes the incremental fast path, with the compile layer
    automatically falling back to a full recompile when an incremental run is not safe.
    Passing ``exact=True`` (CLI ``--exact``) forces a full recompile (correct by
    construction). The fast path is parity-gated by ``tests/test_ingest_parity.py``.
    Returns a report dict with keys:
    ``path_taken``, ``node_count``, ``edge_count``, ``processed_files``, ``skipped_files``,
    ``graph_path``, ``sources`` (resolved input paths).
    """
    resolved: List[str] = []
    dest = Path(wiki.project_root) / "data" / "ingested"
    for item in inputs:
        if is_url(item):
            resolved.append(str(fetch_to_source(item, dest, title=title)))
        else:
            resolved.append(_ensure_in_corpus(wiki, item))

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

    # Drive a corpus-wide compile so baseline nodes are preserved. Ingesting a single
    # explicit file with changed_only=False would drop the rest of the corpus (data loss).
    if exact:
        result = wiki.compile(changed_only=False, source_kind=source_kind, lock_wait=lock_wait)
        path_taken = "full-recompile"
    else:
        result = wiki.compile(
            changed_only=True, incremental_override=True, source_kind=source_kind,
            lock_wait=lock_wait,
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
