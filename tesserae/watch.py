"""Polling-based file watcher for `project watch`.

Standard library only. Re-snapshots ``*.md`` / ``*.markdown`` files under a set
of watched directories at a fixed interval, debounces rapid bursts of edits,
and triggers ``ProjectWiki(project_root).compile(changed_only=True)`` on every
quiescent batch of changes. ``--once`` mode persists the previous snapshot to
``.tesserae/.watch-cache.json`` so the watcher can be driven from cron.
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Callable, Iterable, List, Sequence


_RED = "\033[31m"
_DIM = "\033[2m"
_RESET = "\033[0m"


class WatchLoop:
    """Polling watcher that fires a callback when markdown files change.

    Defaults watch ``data/`` and ``docs/`` plus any directories listed under
    ``sources`` in ``.tesserae/config.json``. Non-``.md``/``.markdown`` files
    are ignored. ``run(once=True)`` performs a single diff against the cache
    on disk and exits, suitable for cron-style rebuilds.
    """

    CACHE_FILENAME = ".watch-cache.json"

    def __init__(
        self,
        project_root: Path,
        *,
        interval: float = 2.0,
        debounce: float = 1.0,
        watch_paths: Sequence[str | Path] | None = None,
        on_change: Callable[[Sequence[Path]], None] | None = None,
        quiet: bool = False,
        stream=None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.interval = float(interval)
        self.debounce = float(debounce)
        self._on_change_override = on_change
        self.quiet = quiet
        self.stream = stream if stream is not None else sys.stderr
        self.watch_paths = self._resolve_watch_paths(watch_paths)
        self._cycles = 0
        self._cache_path = self.project_root / ".tesserae" / self.CACHE_FILENAME

    # ------------------------------------------------------------------
    # Path resolution
    # ------------------------------------------------------------------
    def _resolve_watch_paths(self, watch_paths: Sequence[str | Path] | None) -> List[Path]:
        candidates: List[Path] = []
        if watch_paths:
            for entry in watch_paths:
                candidates.append(self._abspath(entry))
        else:
            for default in ("data", "docs"):
                candidates.append(self.project_root / default)
            cfg_path = self.project_root / ".tesserae" / "config.json"
            if cfg_path.exists():
                try:
                    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    cfg = {}
                for src in cfg.get("sources") or []:
                    candidate = self._abspath(src)
                    if candidate.is_dir():
                        candidates.append(candidate)
        # de-dupe while keeping order; only keep existing directories
        seen: set[Path] = set()
        resolved: List[Path] = []
        for path in candidates:
            try:
                path = path.resolve()
            except OSError:
                continue
            if path in seen:
                continue
            if not path.is_dir():
                continue
            seen.add(path)
            resolved.append(path)
        return resolved

    def _abspath(self, entry: str | Path) -> Path:
        path = Path(entry)
        if not path.is_absolute():
            path = self.project_root / path
        return path

    # ------------------------------------------------------------------
    # Snapshotting
    # ------------------------------------------------------------------
    def snapshot(self) -> dict[Path, tuple[float, int]]:
        """Return ``{path: (mtime, size)}`` for every watched markdown file."""
        snap: dict[Path, tuple[float, int]] = {}
        for root in self.watch_paths:
            if not root.exists():
                continue
            for dirpath, dirnames, filenames in os.walk(root):
                # Skip dotfile dirs (e.g. ``.git``, ``.tesserae``)
                dirnames[:] = [d for d in dirnames if not d.startswith(".")]
                for name in filenames:
                    lower = name.lower()
                    if not (lower.endswith(".md") or lower.endswith(".markdown")):
                        continue
                    full = Path(dirpath) / name
                    try:
                        st = full.stat()
                    except OSError:
                        continue
                    snap[full.resolve()] = (st.st_mtime, st.st_size)
        return snap

    @staticmethod
    def diff(
        a: dict[Path, tuple[float, int]],
        b: dict[Path, tuple[float, int]],
    ) -> tuple[list[Path], list[Path], list[Path]]:
        """Return (added, modified, removed) — ``b`` is the newer snapshot."""
        a_keys = set(a.keys())
        b_keys = set(b.keys())
        added = sorted(b_keys - a_keys)
        removed = sorted(a_keys - b_keys)
        modified = sorted(p for p in (a_keys & b_keys) if a[p] != b[p])
        return added, modified, removed

    # ------------------------------------------------------------------
    # Cache (for --once mode)
    # ------------------------------------------------------------------
    def _load_cache(self) -> dict[Path, tuple[float, int]]:
        if not self._cache_path.exists():
            return {}
        try:
            payload = json.loads(self._cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        out: dict[Path, tuple[float, int]] = {}
        for key, value in (payload or {}).items():
            try:
                out[Path(key)] = (float(value[0]), int(value[1]))
            except (KeyError, ValueError, TypeError, IndexError):
                continue
        return out

    def _save_cache(self, snap: dict[Path, tuple[float, int]]) -> None:
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {str(path): [mtime, size] for path, (mtime, size) in snap.items()}
        self._cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # ------------------------------------------------------------------
    # Output helpers
    # ------------------------------------------------------------------
    def _emit(self, message: str) -> None:
        if self.quiet:
            return
        print(message, file=self.stream, flush=True)

    def _banner(self) -> None:
        path_lines = "\n".join(f"  - {p}" for p in self.watch_paths) or "  (no directories)"
        self._emit(
            "watching for markdown changes:\n"
            f"{path_lines}\n"
            f"  interval={self.interval:g}s  debounce={self.debounce:g}s"
        )

    @staticmethod
    def _format_changes(paths: Sequence[Path], project_root: Path) -> str:
        rels: list[str] = []
        for p in paths:
            try:
                rel = p.relative_to(project_root)
                rels.append(str(rel))
            except ValueError:
                rels.append(str(p))
        if len(rels) <= 3:
            return ", ".join(rels)
        head = ", ".join(rels[:3])
        return f"{head}, +{len(rels) - 3} more"

    # ------------------------------------------------------------------
    # Compile dispatch
    # ------------------------------------------------------------------
    def _trigger(self, changed: Sequence[Path]) -> bool:
        if self._on_change_override is not None:
            self._on_change_override(list(changed))
            return True
        # Lazy import: keeps unit tests for diff/snapshot free of project deps
        from .project import ProjectWiki

        start = time.time()
        try:
            wiki = ProjectWiki.load(self.project_root)
            result = wiki.compile(changed_only=True)
        except Exception:
            self._emit(f"{_RED}rebuild failed:{_RESET}")
            self._emit(traceback.format_exc())
            return False
        elapsed = time.time() - start
        rels = self._format_changes(changed, self.project_root)
        nodes = result.get("node_count", "?")
        edges = result.get("edge_count", "?")
        self._emit(
            f"rebuild: {len(changed)} changed ({rels}) -> nodes={nodes} edges={edges}  ({elapsed:.2f}s)"
        )
        return True

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------
    def run(self, *, once: bool = False) -> None:
        """Run the watcher. ``once=True`` does one diff vs. on-disk cache."""
        if once:
            self._run_once()
            return

        self._banner()
        previous = self.snapshot()
        try:
            while True:
                self._cycles += 1
                time.sleep(self.interval)
                current = self.snapshot()
                added, modified, removed = self.diff(previous, current)
                changed = list(_combine(added, modified, removed))
                if not changed:
                    previous = current
                    continue

                # Debounce: wait for snapshot to settle.
                stable = current
                deadline = time.time() + self.debounce
                while time.time() < deadline:
                    time.sleep(min(self.interval, max(self.debounce / 2.0, 0.05)))
                    later = self.snapshot()
                    if later == stable:
                        break
                    stable = later
                    deadline = time.time() + self.debounce

                final_added, final_modified, final_removed = self.diff(previous, stable)
                final_changed = list(_combine(final_added, final_modified, final_removed))
                if final_changed:
                    if self._trigger(final_changed):
                        previous = stable
                else:
                    previous = stable
        except KeyboardInterrupt:
            self._emit(f"{_DIM}watch stopped after {self._cycles} cycles{_RESET}")

    def _run_once(self) -> None:
        previous = self._load_cache()
        current = self.snapshot()
        added, modified, removed = self.diff(previous, current)
        changed = list(_combine(added, modified, removed))
        if not changed:
            self._emit("no changes")
            self._save_cache(current)
            return
        if self._trigger(changed):
            self._save_cache(current)


def _combine(added: Iterable[Path], modified: Iterable[Path], removed: Iterable[Path]) -> Iterable[Path]:
    seen: set[Path] = set()
    for group in (added, modified, removed):
        for path in group:
            if path in seen:
                continue
            seen.add(path)
            yield path
