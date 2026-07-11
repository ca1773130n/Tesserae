"""Tests for the polling vault watcher (Tier 2)."""

from __future__ import annotations

from pathlib import Path
from typing import List
from unittest.mock import MagicMock

import pytest

from tesserae.vault_watch import VaultFingerprint, VaultWatcher, VaultWatchResult


# ---------------------------------------------------------------- fingerprint


def test_fingerprint_of_empty_dir_returns_zeros(tmp_path: Path) -> None:
    assert VaultFingerprint.of(tmp_path) == VaultFingerprint(0, 0)


def test_fingerprint_of_missing_dir_returns_zeros(tmp_path: Path) -> None:
    assert VaultFingerprint.of(tmp_path / "nope") == VaultFingerprint(0, 0)


def test_fingerprint_counts_md_files_and_skips_dot_directories(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("x")
    (tmp_path / "b.md").write_text("x")
    (tmp_path / "c.txt").write_text("not markdown, ignored")
    (tmp_path / ".obsidian").mkdir()
    (tmp_path / ".obsidian" / "workspace.md").write_text("hidden, ignored")
    fp = VaultFingerprint.of(tmp_path)
    assert fp.file_count == 2
    assert fp.total_mtime_ns > 0


def test_fingerprint_changes_when_a_file_is_added(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("x")
    before = VaultFingerprint.of(tmp_path)
    (tmp_path / "b.md").write_text("y")
    after = VaultFingerprint.of(tmp_path)
    assert before != after


# ---------------------------------------------------------------- watcher loop


class _FakeWiki:
    """Minimal stand-in for ProjectWiki — just enough for VaultWatcher.run."""

    def __init__(self, vault: Path, graph: Path, project_root: Path):
        self.project_root = project_root
        from tesserae.project import ProjectPaths
        # Build a real ProjectPaths so the watcher's path checks work without
        # actually mocking each individual path attribute.
        self.paths = ProjectPaths(
            root=project_root / ".tesserae",
            config=project_root / ".tesserae" / "config.json",
            graph=graph,
            code_graph=project_root / ".tesserae" / "code-graph.json",
            combined_graph=project_root / ".tesserae" / "combined-graph.json",
            build_history=project_root / ".tesserae" / ".build-history.jsonl",
            manifest=project_root / ".tesserae" / "manifest.json",
            sqlite=project_root / ".tesserae" / "sqlite.db",
            markdown_projection=project_root / ".tesserae" / "markdown_projection",
            report=project_root / ".tesserae" / "report.md",
            temporal_facts=project_root / ".tesserae" / "temporal_facts.jsonl",
            competitive_report=project_root / ".tesserae" / "competitive_report.md",
            graphiti_episodes=project_root / ".tesserae" / "graphiti_episodes.jsonl",
            agent_harness=project_root / ".tesserae" / "agent_harness",
            harness_sessions=project_root / ".tesserae" / "harness_sessions",
            obsidian_vault=vault,
            site=project_root / ".tesserae" / "site",
            wiki=project_root / ".tesserae" / "wiki",
            vault_snapshot=project_root / ".tesserae" / "vault_snapshot.json",
            diverged_fields=project_root / ".tesserae" / "diverged-fields.md",
        )
        self.reproject_calls: List[VaultWatchResult] = []

    def reproject_after_vault_change(self) -> VaultWatchResult:
        # Each call returns the same canned result for assertion purposes.
        result = VaultWatchResult(overrides_applied=2, user_link_changes_applied=1, stubs_minted=0)
        self.reproject_calls.append(result)
        return result

    def effective_obsidian_vault(self) -> Path:
        # Stubs follow the production resolver shape — see
        # ProjectWiki.effective_obsidian_vault. Tests don't exercise the
        # override path so this just returns the default location.
        return self.paths.obsidian_vault


def _bootstrap_wiki(tmp_path: Path) -> _FakeWiki:
    """Create the on-disk skeleton VaultWatcher.run requires (graph + vault dir)."""
    project = tmp_path / "project"
    project.mkdir()
    vault = project / ".tesserae" / "obsidian_vault"
    vault.mkdir(parents=True)
    graph = project / ".tesserae" / "graph.json"
    graph.write_text('{"nodes":[],"edges":[]}')
    return _FakeWiki(vault=vault, graph=graph, project_root=project)


def test_watcher_does_nothing_when_vault_is_unchanged(tmp_path: Path) -> None:
    wiki = _bootstrap_wiki(tmp_path)
    sleeps: List[float] = []
    watcher = VaultWatcher(wiki, poll_interval=0.1, sleep=sleeps.append)  # type: ignore[arg-type]
    watcher.run(max_iterations=3)
    assert wiki.reproject_calls == []  # never re-projected


def test_watcher_reprojects_when_vault_file_appears(tmp_path: Path) -> None:
    wiki = _bootstrap_wiki(tmp_path)
    sleeps: List[float] = []

    # Inject a file change after the first poll-sleep so the next fingerprint check sees it.
    def sleeping_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) == 1:
            (wiki.paths.obsidian_vault / "new.md").write_text("hello")

    watcher = VaultWatcher(wiki, poll_interval=0.01, sleep=sleeping_sleep)
    watcher.run(max_iterations=2)
    assert len(wiki.reproject_calls) == 1


def test_watcher_self_writes_do_not_loop(tmp_path: Path) -> None:
    """When reproject writes to the vault (as the real projector does),
    the watcher resets its baseline AFTER the reproject and so doesn't
    trigger again. This is the whole point of the self-write avoidance."""
    wiki = _bootstrap_wiki(tmp_path)
    sleeps: List[float] = []

    def reproject_simulating_projector():
        # The real ProjectWiki.reproject calls export_obsidian which rewrites
        # every vault file. Emulate that by touching a file in the vault.
        (wiki.paths.obsidian_vault / f"projected_{len(wiki.reproject_calls)}.md").write_text("p")
        result = VaultWatchResult(overrides_applied=1, user_link_changes_applied=0, stubs_minted=0)
        wiki.reproject_calls.append(result)
        return result

    wiki.reproject_after_vault_change = reproject_simulating_projector  # type: ignore[method-assign]

    def sleeping_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) == 1:
            (wiki.paths.obsidian_vault / "user.md").write_text("user content")

    watcher = VaultWatcher(wiki, poll_interval=0.01, sleep=sleeping_sleep)
    watcher.run(max_iterations=6)
    # Only the user's edit triggered a reproject; the reproject's own
    # vault writes did NOT trigger a second reproject.
    assert len(wiki.reproject_calls) == 1


def test_watcher_invokes_on_change_callback(tmp_path: Path) -> None:
    wiki = _bootstrap_wiki(tmp_path)
    received: List[VaultWatchResult] = []
    sleeps: List[float] = []

    def sleeping_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) == 1:
            (wiki.paths.obsidian_vault / "x.md").write_text("change")

    watcher = VaultWatcher(
        wiki, poll_interval=0.01, on_change=received.append, sleep=sleeping_sleep,
    )
    watcher.run(max_iterations=2)
    assert len(received) == 1
    assert received[0].overrides_applied == 2


def test_watcher_returns_early_when_vault_missing(tmp_path: Path, capsys) -> None:
    wiki = _bootstrap_wiki(tmp_path)
    # Remove the vault dir so the precondition check fails.
    import shutil
    shutil.rmtree(wiki.paths.obsidian_vault)
    watcher = VaultWatcher(wiki, poll_interval=0.01)
    watcher.run(max_iterations=10)
    out = capsys.readouterr().out
    assert "vault directory does not exist" in out
    assert wiki.reproject_calls == []


def test_watcher_returns_early_when_graph_missing(tmp_path: Path, capsys) -> None:
    wiki = _bootstrap_wiki(tmp_path)
    wiki.paths.graph.unlink()
    watcher = VaultWatcher(wiki, poll_interval=0.01)
    watcher.run(max_iterations=10)
    out = capsys.readouterr().out
    assert "no graph at" in out
    assert wiki.reproject_calls == []
