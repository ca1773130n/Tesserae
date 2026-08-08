"""Orchestrate single-source ingest: resolve inputs, (fetch URLs), drive compile, report.

This is the B->C seam: v1 drives compile synchronously. A future v2 (--async) splits the
compile() call into an instant approximate write + an enqueued reconcile.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import List, Optional, Sequence

from tesserae.ingest.fetch import UnsupportedSourceError, fetch_to_source, is_url
from tesserae.project import COMPILE_SOURCE_EXTENSIONS

__all__ = ["UnsupportedSourceError", "ingest_sources"]


def _unsupported_local_input_error(offenders: List[Path]) -> UnsupportedSourceError:
    """Build the refusal for local inputs the compile walker would never read.

    Shape copied from ``raganything_refresh._verify_parsers_or_raise``: a header
    line saying why it cannot run, then one indented ``  - <thing>: <hint>`` per
    remedy, each hint a command the user can actually run.
    """
    from tesserae.raganything_refresh import _SUPPORTED_EXT

    supported = ", ".join(COMPILE_SOURCE_EXTENSIONS)
    lines = [
        f"tesserae ingest cannot read these files — compile only extracts from {supported}:"
    ]
    for path in offenders:
        suffix = path.suffix.lower() or "<no suffix>"
        if suffix in _SUPPORTED_EXT:
            hint = (
                "RAG-Anything parses this format, but it is opt-in and it is a "
                "SEPARATE pass — enable it with `tesserae setup` (external_tools "
                "entry id=raganything), then `tesserae refresh raganything` to "
                "parse the file and `tesserae compile` to merge the result."
            )
        else:
            hint = (
                "No Tesserae backend parses this format. Convert it to markdown "
                f"first, then `tesserae ingest {path.stem}.md`."
            )
        lines.append(f"  - {path.name} ({suffix}): {hint}")
    return UnsupportedSourceError("\n".join(lines))


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
    Passing ``exact=True`` (CLI ``--full``) forces a full recompile (correct by
    construction). The fast path is parity-gated by ``tests/test_ingest_parity.py``.
    Returns a report dict with keys:
    ``path_taken``, ``node_count``, ``edge_count``, ``processed_files``, ``skipped_files``,
    ``graph_path``, ``sources`` (resolved input paths).
    """
    # Validate local inputs up front: a typo'd path must error immediately
    # (the CLI maps FileNotFoundError to exit 2), never silently compile nothing.
    #
    # The same applies to a file that exists but that compile will never read.
    # ``ingest paper.pdf`` used to copy the PDF into data/ingested/, drive a
    # compile whose walker matches COMPILE_SOURCE_EXTENSIONS only, and then
    # print node/edge counts belonging to the REST of the corpus — reporting
    # success for work it had not done. Checking HERE rather than at the copy
    # site is deliberate: ``_ensure_in_corpus`` returns in-corpus paths in
    # place, so a PDF already under the project root never reaches the copy and
    # would fail identically but invisibly.
    #
    # The refusal is unconditional, including when the RAG-Anything backend is
    # enabled: that backend merges a manifest built by a separate
    # ``refresh raganything`` pass (``_merge_configured_raganything_graph``), so
    # a file this command just copied in is not in it either way.
    unsupported: List[Path] = []
    for item in inputs:
        if is_url(item):
            continue
        path = Path(item)
        if not path.exists():
            raise FileNotFoundError(f"Input path does not exist: {item}")
        if path.is_file() and path.suffix.lower() not in COMPILE_SOURCE_EXTENSIONS:
            unsupported.append(path)
    if unsupported:
        raise _unsupported_local_input_error(unsupported)

    if dry_run:
        # Truly dry: no URL fetch, no copy into data/ingested, no compile —
        # just report what WOULD be ingested.
        return {
            "path_taken": "dry-run",
            "sources": [item if is_url(item) else str(Path(item).resolve()) for item in inputs],
            "node_count": 0,
            "edge_count": 0,
            "processed_files": 0,
            "skipped_files": 0,
            "graph_path": str(wiki.paths.graph),
        }

    resolved: List[str] = []
    dest = Path(wiki.project_root) / "data" / "ingested"
    for item in inputs:
        if is_url(item):
            resolved.append(str(fetch_to_source(item, dest, title=title)))
        else:
            resolved.append(_ensure_in_corpus(wiki, item))

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
