"""Filesystem :class:`SourceLoader` adapter.

Walks one or more directory roots and yields one :class:`Source` per file
matching the configured extensions. Replaces the inline ``iter_markdown_files``
walk that lived inside :meth:`ProjectWiki.compile` so the pipeline can swap
in a different :class:`SourceLoader` (e.g. the HypePaper Postgres loader)
without touching extraction or canonicalization.

Behavior parity with the legacy walker (``tesserae.project.iter_markdown_files``):

* Recursive walk under each path that exists on disk.
* Sorted by path (``rglob`` + ``sorted``) for deterministic iteration order.
* Hidden directories/files (any path component starting with ``.``) are
  skipped — same dot-prefix filter as the legacy walker.
* When the root path is itself a single file, it is yielded if its extension
  matches; otherwise it is silently skipped (mirrors the legacy ``is_file``
  branch).

The previous walker raised ``FileNotFoundError`` when a *path* did not exist;
this loader accepts a ``List[Path]`` from the caller and silently skips
non-existent roots so :meth:`discover` is forgiving for partial trees. The
:meth:`ProjectWiki` caller (``resolve_project_input``) already filters the
``sources`` list to existing roots, so this is not a behavior regression in
practice.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterator, List, Sequence, Tuple

from ..ports import Source

DEFAULT_EXTENSIONS: Tuple[str, ...] = (".md", ".txt", ".py", ".rst")


# Generated / build / dependency / translation directories that the walker
# should NEVER descend into when discovering source markdown. Mirrors the
# common .gitignore conventions plus Tesserae's own output dir. A user with
# an unusual layout can still pass these explicitly as a source root —
# the filter only applies during recursive descent under a normal root.
#
# `i18n` is included because translations are derived from source docs, not
# source themselves; otherwise every heading appears once per supported
# language and the Concept layer fills with "Examples" / "Quickstart" /
# "2) Paste it into your MCP client" duplicates in Korean, Chinese, etc.
_EXCLUDED_TOPLEVEL_DIRS = frozenset({
    "output",
    "build",
    "dist",
    "target",
    "node_modules",
    "venv",
    "__pycache__",
    "site-packages",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "data-gym-cache",
    "i18n",
})

# Same idea, but for directory names whose tail is not knowable at import time.
# pytest's ``tmp_path_factory`` roots at ``<basetemp>/pytest-of-<user>/pytest-N/``
# and ``<basetemp>`` is ``$TMPDIR`` — which, when TMPDIR resolves to the repo
# (see .gitignore:26-28,48-50), plants thousands of throwaway fixture markdown
# files INSIDE a configured source root. They then churn every test run, so the
# candidate set never repeats and changed-only can never no-op.
#
# ``pip-install-`` is the same story from pip's scratch dirs; .gitignore already
# enumerates it, so it is a documented junk family with no cost to include.
#
# ponytail: a two-entry prefix tuple, not a glob engine. If a third junk-dir
# family shows up (tsx-*, pip-build-env-*, mkdtemp's tmpXXXXXXXX), upgrade this
# to fnmatch against a config-overridable pattern list rather than growing the
# tuple forever. Those three are deliberately absent today: they measured at 32
# ghost rows and ZERO live files on the repo, and the manifest reconciliation in
# ``ProjectWiki.ingest`` self-heals their rows anyway. The real fix is
# containment (keep TMPDIR out of the repo); this only stops the measured bleed.
#
# Deliberately NOT .gitignore parsing — the walker's ignore set stays hermetic
# and deterministic (same rationale as ``code_graph_extractor.DEFAULT_EXCLUDES``).
_EXCLUDED_DIR_PREFIXES: Tuple[str, ...] = ("pytest-of-", "pip-install-")


class FilesystemSourceLoader:
    """Walks filesystem roots and yields :class:`Source` records.

    Parameters
    ----------
    paths:
        One or more directory (or file) roots to walk. Each root is walked
        recursively; files matching ``extensions`` are yielded as Sources.
    extensions:
        Tuple of lowercase file suffixes to include. Defaults to
        ``(".md", ".txt", ".py", ".rst")``. The legacy ``ProjectWiki``
        walker matched ``.md`` only — pass ``extensions=(".md",)`` when
        constructing for that codepath to preserve byte-identical behavior.
    """

    def __init__(
        self,
        paths: Sequence[Path],
        extensions: Tuple[str, ...] = DEFAULT_EXTENSIONS,
    ) -> None:
        self._paths: List[Path] = [Path(p) for p in paths]
        # Normalize to lowercase for case-insensitive suffix matching, matching
        # the ``path.suffix.lower() == ".md"`` check in the legacy walker.
        self._extensions: Tuple[str, ...] = tuple(ext.lower() for ext in extensions)
        # Discovery cache: maps Source.id (relative path) → absolute Path. Used
        # by :meth:`fetch` to re-read a previously discovered file. Populated
        # on every call to :meth:`discover` so callers can rely on the latest
        # tree state.
        self._discovered: Dict[str, Path] = {}

    # ------------------------------------------------------------------
    # SourceLoader protocol
    # ------------------------------------------------------------------

    def discover(self) -> Iterator[Source]:
        """Yield one :class:`Source` per file under any configured root."""
        # Reset the discovery cache so :meth:`fetch` reflects the latest walk.
        self._discovered = {}
        seen: set = set()
        for root in self._paths:
            for absolute in self.iter_paths(root):
                resolved = absolute.resolve()
                if resolved in seen:
                    continue
                seen.add(resolved)
                rel = self._relative_id(root, absolute)
                self._discovered[rel] = absolute
                yield self._build_source(rel, absolute)

    def fetch(self, source_id: str) -> Source:
        """Re-read a previously discovered :class:`Source` by id.

        Always re-reads the file from disk, so callers see the current
        on-disk content (not a snapshot from :meth:`discover`).

        Raises
        ------
        KeyError
            When ``source_id`` was never registered by :meth:`discover` —
            i.e. an unknown/stale id (programmer or lookup error).
        FileNotFoundError
            When the id is known but the underlying file has been deleted
            from disk between :meth:`discover` and this call (environmental
            error).
        """
        absolute = self._discovered.get(source_id)
        if absolute is None:
            raise KeyError(source_id)
        if not absolute.exists():
            raise FileNotFoundError(
                f"Source file is gone: {absolute} (id={source_id!r})"
            )
        return self._build_source(source_id, absolute)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def iter_paths(self, root: Path) -> Iterator[Path]:
        """Yield absolute :class:`Path` objects for files matching this loader's filter under ``root``.

        Public helper that exposes the filesystem walk without the
        :class:`Source` body-read overhead of :meth:`discover`. Use this when
        a caller only needs path enumeration (e.g. ``iter_markdown_files``
        delegating to the loader for discovery semantics).

        Behavior:

        * Single-file ``root``: yielded if its suffix is in ``extensions``,
          otherwise skipped silently.
        * Directory ``root``: walked recursively with ``rglob('*')`` and
          sorted for deterministic order.
        * Non-existent ``root``: yields nothing (forgiving — mirrors
          :meth:`discover`).
        * Hidden components (path parts starting with ``.``) are skipped.
        * Non-matching suffixes are skipped.

        Notes:
            Unlike :meth:`discover`, this method does not populate the
            discovery cache and does not read file contents. Callers using
            it for path enumeration cannot subsequently call :meth:`fetch`
            with the resulting paths — call :meth:`discover` first if you
            need the cache populated.
        """
        if root.is_file():
            if root.suffix.lower() in self._extensions:
                yield root
            return
        if not root.exists():
            return
        for child in sorted(root.rglob("*")):
            if not child.is_file():
                continue
            if child.suffix.lower() not in self._extensions:
                continue
            try:
                rel = child.relative_to(root)
            except ValueError:
                # rglob results are always under root; guard for symlink edge
                # cases by skipping anything that doesn't relativize.
                continue
            if any(part.startswith(".") for part in rel.parts[:-1]):
                continue
            # Skip well-known generated/output directories. Without this the
            # walker sweeps up everything under output/, build/, dist/, etc.,
            # which on a repo that's been through previous compiles turns
            # thousands of stale generated markdown files into "source" docs
            # and balloons the typed graph with garbage Paper/Concept nodes.
            if any(part in _EXCLUDED_TOPLEVEL_DIRS for part in rel.parts[:-1]):
                continue
            # Same, for families whose exact name isn't knowable (``pytest-of-
            # $USER``). MUST stay on ``rel.parts`` — components strictly BELOW
            # the root — never on ``root`` itself: every fixture project in the
            # test suite is handed a ``tmp_path``, which IS a
            # ``pytest-of-<user>/pytest-N/<test>`` directory. Matching the root
            # would silently empty every one of those corpora, and the suite
            # would still pass because an empty corpus yields an empty graph
            # that nothing asserts against. Pinned by
            # ``test_iter_paths_walks_a_pytest_tmpdir_passed_as_the_root``.
            # ``rel.parts[:-1]`` — DIRECTORY components only. The last part is
            # the filename, and this is a directory rule: matching it would drop
            # ordinary documents whose NAME merely starts with a prefix.
            # ``pip-install-guide.md`` is not a pytest artifact, and Tesserae's
            # own concept slugifier mints exactly that shape from any "pip
            # install <x>" heading — 597 such pages exist in this project's
            # markdown projection today. Dropping them would be silent: the file
            # never becomes a candidate, so nothing logs it.
            if any(part.startswith(_EXCLUDED_DIR_PREFIXES) for part in rel.parts[:-1]):
                continue
            yield child

    def _relative_id(self, root: Path, absolute: Path) -> str:
        """Compute the deterministic id (relative path string) for a file."""
        if root.is_file():
            # Single-file root: id is just the file name (matches the legacy
            # ``[path]`` behavior of ``iter_markdown_files`` for file inputs).
            return absolute.name
        try:
            return absolute.relative_to(root).as_posix()
        except ValueError:
            # Fallback: absolute path. Should be unreachable in practice.
            return absolute.as_posix()

    def _build_source(self, source_id: str, absolute: Path) -> Source:
        """Read ``absolute`` and wrap it as a :class:`Source` record."""
        stat = absolute.stat()
        try:
            content = absolute.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            # Mirror tolerant behavior: surface the raw bytes as latin-1 so
            # the pipeline can still see (corrupted) content rather than
            # crashing the whole walk on one bad file.
            content = absolute.read_text(encoding="latin-1")
        return Source(
            id=source_id,
            path=absolute.resolve().as_uri(),
            content=content,
            metadata={
                "mtime": float(stat.st_mtime),
                "size": int(stat.st_size),
                "extension": absolute.suffix.lower(),
            },
        )
