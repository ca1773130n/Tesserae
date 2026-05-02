"""Filesystem :class:`SourceLoader` adapter.

Walks one or more directory roots and yields one :class:`Source` per file
matching the configured extensions. Replaces the inline ``iter_markdown_files``
walk that lived inside :meth:`ProjectWiki.compile` so the pipeline can swap
in a different :class:`SourceLoader` (e.g. the HypePaper Postgres loader)
without touching extraction or canonicalization.

Behavior parity with the legacy walker (``llm_wiki.project.iter_markdown_files``):

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
            if any(part.startswith(".") for part in rel.parts):
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
