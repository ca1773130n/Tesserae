"""Orchestrate single-source ingest: resolve inputs, (fetch URLs), drive compile, report.

This is the B->C seam: v1 drives compile synchronously. A future v2 (--async) splits the
compile() call into an instant approximate write + an enqueued reconcile.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import List, Optional, Sequence, Set

from tesserae.ingest.fetch import UnsupportedSourceError, fetch_to_source, is_url

__all__ = ["UnsupportedSourceError", "ingest_sources"]

# How many unreadable files a refused directory names before it stops. A
# refusal is read by a human; a hundred identical lines is not more actionable
# than five and the count says how many were elided.
_MAX_LISTED_PER_DIRECTORY = 5


def _raganything_hint(path: Path, project_root: Path) -> str:
    """Remedy for a format RAG-Anything can parse, in the order it must be done.

    ``raganything_refresh.discover_sources`` walks the PROJECT ROOT only, and
    this refusal fires BEFORE ``_ensure_in_corpus`` copies anything — so for the
    common case (a file the user points at from somewhere else on disk) the
    file is not, and never becomes, visible to the raganything pass. Telling
    that user to run `tesserae refresh raganything` and nothing else sends them
    to a command that parses zero documents. Getting the file into the corpus
    is step one, and it only belongs in the message when it is actually needed.
    """
    steps = []
    try:
        path.resolve().relative_to(project_root)
    except ValueError:
        steps.append(
            f"copy it into the corpus first (`mkdir -p '{project_root}/data/ingested' "
            f"&& cp '{path}' '{project_root}/data/ingested/'`)"
        )
    steps.append(
        "enable the backend with `tesserae setup` (external_tools entry "
        "id=raganything)"
    )
    steps.append("`tesserae refresh raganything` to parse it")
    steps.append("`tesserae compile` to merge the result")
    return (
        "RAG-Anything parses this format, but it is opt-in and it is a SEPARATE "
        "pass — " + ", then ".join(steps) + "."
    )


# Suffixes that ARE prose but that the compile walker does not pick up. They
# must never route into :func:`_raganything_hint`: RAG-Anything's _SUPPORTED_EXT
# lists them, so the hint fired on markdown and told the user to run a
# PDF/image parser over a `.md` file. The fix for a markdown-ish suffix is a
# rename, not a second backend.
_MARKDOWNISH_EXT = frozenset({".md", ".markdown", ".mdown", ".mkd", ".mdx"})


def _is_raganything_candidate(path: Path) -> bool:
    """True when RAG-Anything is genuinely the right tool for ``path``.

    ``_SUPPORTED_EXT`` includes the text formats too, because raganything's own
    pass can read them — but reaching THIS code means the compile walker did
    not, and for a markdown-ish suffix that is a naming problem, not a parsing
    one. Excluded here rather than at each call site so no future caller can
    reintroduce "RAG-Anything parses this format" about a `.md`.
    """
    from tesserae.raganything_refresh import _SUPPORTED_EXT

    suffix = path.suffix.lower()
    return suffix in _SUPPORTED_EXT and suffix not in _MARKDOWNISH_EXT


def _file_remedy(
    path: Path, project_root: Path, supported: Sequence[str], *, name_a_path: bool
) -> str:
    """The one-line remedy for a single unreadable file.

    ``name_a_path`` is False inside a directory listing, where suggesting a
    per-file ``tesserae ingest`` command would be wrong advice — the user
    pointed at the directory, not at the file.
    """
    suffix = path.suffix.lower()
    if suffix in _MARKDOWNISH_EXT:
        return (
            f"This is markdown, but compile only reads {', '.join(supported)} "
            f"— rename it to {path.with_suffix('.md').name} and ingest again."
        )
    if _is_raganything_candidate(path):
        return _raganything_hint(path, project_root)
    if name_a_path:
        # ``with_suffix``, not ``stem``: an absolute input must yield an
        # absolute suggestion. Rebuilding from the stem alone produced
        # `tesserae ingest figure.md` for /abs/path/figure.zip — a command
        # that does not resolve from the user's working directory.
        return (
            "No Tesserae backend parses this format. Convert it to markdown "
            f"first, then `tesserae ingest {path.with_suffix('.md')}`."
        )
    return "No Tesserae backend parses this format. Convert it to markdown first."


def _unsupported_local_input_error(
    offenders: List[Path], project_root: Path, supported: Sequence[str]
) -> UnsupportedSourceError:
    """Build the refusal for local inputs the compile walker would never read.

    Shape copied from ``raganything_refresh._verify_parsers_or_raise``: a header
    line saying why it cannot run, then one indented ``  - <thing>: <hint>`` per
    remedy, each hint a command the user can actually run.
    """
    lines = [
        "tesserae ingest cannot read these inputs — compile only extracts from "
        f"{', '.join(supported)}:"
    ]
    for path in offenders:
        if path.is_dir():
            lines.extend(_directory_remedy_lines(path, project_root, supported))
            continue
        suffix = path.suffix.lower() or "<no suffix>"
        hint = _file_remedy(path, project_root, supported, name_a_path=True)
        lines.append(f"  - {path.name} ({suffix}): {hint}")
    return UnsupportedSourceError("\n".join(lines))


def _walker_skipped_dir_names(directory: Path, files: Sequence[Path]) -> List[str]:
    """The directory names that kept ``files`` out of the walk, if any."""
    from tesserae.source_loaders.filesystem import (_EXCLUDED_DIR_PREFIXES,
                                                    _EXCLUDED_TOPLEVEL_DIRS)

    names: Set[str] = set()
    for path in files:
        try:
            rel = path.relative_to(directory)
        except ValueError:
            continue
        for part in rel.parts[:-1]:
            if (
                part in _EXCLUDED_TOPLEVEL_DIRS
                or part.startswith(_EXCLUDED_DIR_PREFIXES)
                or part.startswith(".")
            ):
                names.add(part)
    return sorted(names)


def _directory_remedy_lines(
    directory: Path, project_root: Path, supported: Sequence[str]
) -> List[str]:
    """Remedy lines for a directory holding nothing the compile walker reads.

    Describes the SAME file set the refusal decision was made on. The decision
    comes from ``iter_markdown_files`` -> ``FilesystemSourceLoader``, which
    never descends into ``_EXCLUDED_TOPLEVEL_DIRS`` (``i18n``, ``build``,
    ``node_modules``, ``dist``, ...) or hidden components; re-walking with a
    bare ``rglob("*")`` saw a larger set and reported
    "holds 3 file(s), none of them markdown" about three real ``.md`` files
    under ``docs/i18n/ko/``, ``docs/build/`` and ``docs/node_modules/pkg/``,
    then advised parsing each of them with RAG-Anything. ``docs/i18n/`` is
    mandatory in this repository, so that shape is not hypothetical — and a
    branch whose reason for existing is replacing a silent lie with a true
    statement cannot afford to emit a loud one.
    """
    from tesserae.project import iter_source_candidates

    candidates = iter_source_candidates(directory)
    if candidates:
        lines = [
            f"  - {directory.name}/: the compile walker sees "
            f"{len(candidates)} file(s) here and none of them is "
            f"{', '.join(supported)}, so a compile driven from it would read "
            "nothing:"
        ]
        for path in candidates[:_MAX_LISTED_PER_DIRECTORY]:
            suffix = path.suffix.lower() or "<no suffix>"
            hint = _file_remedy(path, project_root, supported, name_a_path=False)
            lines.append(f"      - {path.name} ({suffix}): {hint}")
        if len(candidates) > _MAX_LISTED_PER_DIRECTORY:
            lines.append(
                f"      ... and {len(candidates) - _MAX_LISTED_PER_DIRECTORY} more"
            )
        return lines

    on_disk = sorted(p for p in directory.rglob("*") if p.is_file())
    if not on_disk:
        return [
            f"  - {directory.name}/: the directory is empty, so a compile driven "
            "from it would read nothing. Put markdown in it, or ingest a "
            "different path."
        ]
    skipped = _walker_skipped_dir_names(directory, on_disk)
    where = f" ({', '.join(skipped)})" if skipped else ""
    return [
        f"  - {directory.name}/: holds {len(on_disk)} file(s), but every one of "
        f"them sits under a directory the compile walker never descends into"
        f"{where}, so a compile driven from it would read nothing. Ingest a "
        "path outside those directories, or move the documents you want read "
        "out of them."
    ]


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
    # Imported inside the function on purpose: tesserae/ingest/__init__.py
    # resolves ingest_sources through a lazy __getattr__ so that importing the
    # cheap fetch helpers does not drag in the compile stack. A module-scope
    # import here defeats that for every importer of this module (measured:
    # 11ms -> ~105ms, and tesserae.project loaded whether or not it is used).
    from tesserae.project import COMPILE_SOURCE_EXTENSIONS, iter_markdown_files

    project_root = Path(wiki.project_root).resolve()
    unsupported: List[Path] = []
    for item in inputs:
        if is_url(item):
            continue
        path = Path(item)
        if not path.exists():
            raise FileNotFoundError(f"Input path does not exist: {item}")
        if path.is_dir():
            # A directory ALREADY INSIDE the project root is a supported input
            # shape — iter_markdown_files walks it deliberately — so the
            # file-only check skipped every one of them, and
            # `ingest <dir-of-pdfs>` still reported "processed=1 ... nodes=1"
            # where the 1 counted the DIRECTORY.
            #
            # A directory from OUTSIDE the project root is NOT a supported
            # shape: ``_ensure_in_corpus`` reaches shutil.copy2 and dies with
            # IsADirectoryError. That predates this branch (it reproduces
            # identically on 9890e804) and is left alone here so the refusal
            # change stays reviewable on its own.
            #
            # Refuse only when the walk finds NOTHING readable: a docs/ tree
            # that also holds screenshots is a normal, working input and
            # refusing it would be its own regression.
            if not iter_markdown_files(path):
                unsupported.append(path)
        elif path.suffix.lower() not in COMPILE_SOURCE_EXTENSIONS:
            unsupported.append(path)
    if unsupported:
        raise _unsupported_local_input_error(
            unsupported, project_root, COMPILE_SOURCE_EXTENSIONS
        )

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
