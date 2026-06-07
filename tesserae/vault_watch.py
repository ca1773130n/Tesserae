"""Long-running watcher that re-runs the vault overlay on filesystem changes.

Tier 2 of the bidirectional Obsidian sync feature
(docs/integrations/obsidian-sync.md). Where Tier 1a/1b only react when the
user runs ``tesserae compile``, this module turns the vault into a
live editing surface: edit a file in Obsidian, save, see the overlay
applied + the projection updated within a couple of seconds.

Design choices:

* **Polling, not inotify/watchdog.** A polling loop hits the ``stat()``
  syscall every ~1.5s for the vault tree, comparing aggregate fingerprints.
  Zero new dependencies, works on every platform, and the latency is fine
  for a developer-facing tool. If sub-second response becomes important
  later, an optional ``watchdog`` backend can slot in behind the same
  :class:`VaultWatcher` interface.

* **Self-write avoidance.** Every time we re-project, the watcher writes
  hundreds of vault files. Those writes would re-trigger the loop in the
  next poll cycle. After each apply step the watcher takes a fresh
  fingerprint and treats it as the new baseline; changes from THAT baseline
  are user-driven, so the loop is self-quenching.

* **Debounce.** When the user saves a file, editors often write
  twice in rapid succession (temp file + rename). The watcher waits one
  full poll interval after seeing a change to confirm the file has stopped
  mutating before reacting.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:
    from .project import ProjectWiki


@dataclass(frozen=True)
class VaultFingerprint:
    """A cheap aggregate hash of the vault state.

    Two vaults that have the same fingerprint are very likely identical for
    our purposes: same files, same mtimes. Collisions are theoretically
    possible (re-save with identical mtime) but the worst-case is one
    missed change cycle, recovered on the next user save. Comparing whole
    file hashes would be sounder but is far too slow for a poll loop on a
    1000-file vault.
    """

    file_count: int
    total_mtime_ns: int

    @classmethod
    def of(cls, root: Path) -> "VaultFingerprint":
        if not root.is_dir():
            return cls(0, 0)
        files = [
            p
            for p in root.rglob("*.md")
            if not any(part.startswith(".") for part in p.relative_to(root).parts)
        ]
        total = 0
        for p in files:
            try:
                total += p.stat().st_mtime_ns
            except OSError:
                continue
        return cls(len(files), total)


@dataclass
class VaultWatchResult:
    """Summary of one apply-and-reproject pass, returned for testing/logging."""

    overrides_applied: int
    user_link_changes_applied: int
    stubs_minted: int


class VaultWatcher:
    """Poll-based vault watcher. Construct, then call :meth:`run`."""

    def __init__(
        self,
        wiki: "ProjectWiki",
        *,
        poll_interval: float = 1.5,
        on_change: Optional[Callable[[VaultWatchResult], None]] = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.wiki = wiki
        self.poll_interval = float(poll_interval)
        self.on_change = on_change
        # Injectable sleep so tests can run the loop without burning wall time.
        self._sleep = sleep
        # Baseline fingerprint that represents "vault state we caused" — any
        # change away from this is user-driven and needs reacting to.
        self._baseline = VaultFingerprint.of(self.wiki.effective_obsidian_vault())

    def run(self, max_iterations: Optional[int] = None) -> None:
        """Block until interrupted (or ``max_iterations`` poll cycles elapse).

        ``max_iterations`` is for tests; production use leaves it ``None``
        so the watcher runs until the user hits Ctrl-C.
        """
        vault = self.wiki.effective_obsidian_vault()
        if not vault.is_dir():
            print(f"[tesserae] vault directory does not exist: {vault}", flush=True)
            print("[tesserae] run `tesserae compile` first.", flush=True)
            return
        if not self.wiki.paths.graph.is_file():
            print(f"[tesserae] no graph at {self.wiki.paths.graph}", flush=True)
            print("[tesserae] run `tesserae compile` first.", flush=True)
            return

        print(
            f"[tesserae] watching {vault.relative_to(self.wiki.project_root)} "
            f"(poll every {self.poll_interval}s, Ctrl-C to stop)",
            flush=True,
        )
        iterations = 0
        try:
            while max_iterations is None or iterations < max_iterations:
                self._sleep(self.poll_interval)
                iterations += 1
                if not self._tick():
                    continue
        except KeyboardInterrupt:
            print("\n[tesserae] stopped.", flush=True)

    def _tick(self) -> bool:
        """One poll cycle. Returns True iff a reproject happened."""
        vault = self.wiki.effective_obsidian_vault()
        current = VaultFingerprint.of(vault)
        if current == self._baseline:
            return False

        # Debounce: wait one more poll interval and recheck. If still
        # changing, defer until the next cycle to avoid reacting to a
        # half-written file.
        self._sleep(self.poll_interval)
        settled = VaultFingerprint.of(vault)
        if settled != current:
            # File is still being modified; let next tick pick it up.
            return False

        print("[tesserae] vault change detected, re-applying overlay...", flush=True)
        result = self.wiki.reproject_after_vault_change()
        # Reset baseline to the state we just produced — any new change is
        # the user's, not ours.
        self._baseline = VaultFingerprint.of(vault)
        self._report(result)
        if self.on_change is not None:
            self.on_change(result)
        return True

    @staticmethod
    def _report(result: VaultWatchResult) -> None:
        bits: list[str] = []
        if result.overrides_applied:
            bits.append(f"{result.overrides_applied} field override(s)")
        if result.user_link_changes_applied:
            bits.append(f"{result.user_link_changes_applied} user-link change(s)")
        if result.stubs_minted:
            bits.append(f"{result.stubs_minted} new Stub node(s)")
        if not bits:
            print("[tesserae] applied (no changes — vault was already in sync).", flush=True)
        else:
            print(f"[tesserae] applied: {', '.join(bits)}", flush=True)


__all__ = ["VaultFingerprint", "VaultWatcher", "VaultWatchResult"]
