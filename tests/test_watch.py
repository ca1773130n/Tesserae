"""Tests for the polling-based ``project watch`` loop."""

from __future__ import annotations

import io
import json
import time
from pathlib import Path

import pytest

from tesserae.watch import WatchLoop


def _make_project(tmp_path: Path) -> Path:
    project = tmp_path / "demo-project"
    project.mkdir()
    (project / ".tesserae").mkdir()
    (project / ".tesserae" / "config.json").write_text(
        json.dumps(
            {
                "name": "demo_wiki",
                "project_root": str(project),
                "sources": ["docs"],
            }
        ),
        encoding="utf-8",
    )
    (project / "docs").mkdir()
    (project / "data").mkdir()
    (project / "docs" / "a.md").write_text("# A\n", encoding="utf-8")
    (project / "data" / "b.md").write_text("# B\n", encoding="utf-8")
    return project


def test_snapshot_collects_mtime_and_size(tmp_path):
    project = _make_project(tmp_path)
    loop = WatchLoop(project)
    snap = loop.snapshot()

    keys = {p.name for p in snap.keys()}
    assert {"a.md", "b.md"} <= keys
    for _, value in snap.items():
        assert isinstance(value, tuple)
        assert len(value) == 2
        mtime, size = value
        assert isinstance(mtime, float)
        assert isinstance(size, int)
        assert size > 0


def test_diff_reports_modified(tmp_path):
    project = _make_project(tmp_path)
    loop = WatchLoop(project)
    before = loop.snapshot()
    target = (project / "docs" / "a.md").resolve()
    # Force mtime difference: write more content, advance mtime explicitly.
    target.write_text("# A updated\nMore content\n", encoding="utf-8")
    new_mtime = target.stat().st_mtime + 10
    import os
    os.utime(target, (new_mtime, new_mtime))
    after = loop.snapshot()
    added, modified, removed = WatchLoop.diff(before, after)
    assert added == []
    assert removed == []
    assert target in modified


def test_diff_reports_added(tmp_path):
    project = _make_project(tmp_path)
    loop = WatchLoop(project)
    before = loop.snapshot()
    new_path = (project / "docs" / "c.md").resolve()
    new_path.write_text("# C\n", encoding="utf-8")
    after = loop.snapshot()
    added, modified, removed = WatchLoop.diff(before, after)
    assert added == [new_path]
    assert modified == []
    assert removed == []


def test_diff_reports_removed(tmp_path):
    project = _make_project(tmp_path)
    loop = WatchLoop(project)
    before = loop.snapshot()
    target = (project / "docs" / "a.md").resolve()
    target.unlink()
    after = loop.snapshot()
    added, modified, removed = WatchLoop.diff(before, after)
    assert removed == [target]
    assert added == []
    assert modified == []


def test_non_markdown_files_are_ignored(tmp_path):
    project = _make_project(tmp_path)
    (project / "docs" / "image.png").write_text("not markdown", encoding="utf-8")
    (project / "docs" / "notes.txt").write_text("not markdown", encoding="utf-8")
    loop = WatchLoop(project)
    snap = loop.snapshot()
    extensions = {p.suffix.lower() for p in snap.keys()}
    assert extensions <= {".md", ".markdown"}
    assert ".png" not in extensions
    assert ".txt" not in extensions


def test_run_once_no_changes_writes_cache(tmp_path):
    project = _make_project(tmp_path)
    received: list[list[Path]] = []
    stream = io.StringIO()
    loop = WatchLoop(project, on_change=received.append, stream=stream)

    # First run: cache empty, so every file is "new" -> callback fires.
    loop.run(once=True)
    assert received and len(received[0]) == 2

    received.clear()

    # Second run: cache matches reality -> no callback.
    loop.run(once=True)
    assert received == []
    assert "no changes" in stream.getvalue()
    assert (project / ".tesserae" / ".watch-cache.json").exists()


def test_run_once_after_edit_fires_callback_with_path(tmp_path):
    project = _make_project(tmp_path)
    received: list[list[Path]] = []
    loop = WatchLoop(project, on_change=received.append, quiet=True)
    # Prime the cache.
    loop.run(once=True)
    received.clear()

    target = (project / "docs" / "a.md").resolve()
    target.write_text("# A edited\n", encoding="utf-8")
    import os
    new_mtime = target.stat().st_mtime + 5
    os.utime(target, (new_mtime, new_mtime))

    loop.run(once=True)
    assert len(received) == 1
    assert target in received[0]


def test_debounce_collapses_burst_into_one_callback(tmp_path, monkeypatch):
    project = _make_project(tmp_path)
    received: list[list[Path]] = []
    loop = WatchLoop(
        project,
        interval=0.01,
        debounce=0.05,
        on_change=received.append,
        quiet=True,
    )

    # Snapshots get called repeatedly inside run(); we mutate the filesystem
    # mid-run by hooking time.sleep so each "tick" inside the loop edits a
    # different file. Once we've fired three edits and the snapshot stops
    # changing, the loop should debounce and call on_change exactly once.
    targets = [
        (project / "docs" / "a.md").resolve(),
        (project / "docs" / "b.md").resolve(),
        (project / "docs" / "c.md").resolve(),
    ]
    state = {"step": 0, "stop_after": 8}

    real_sleep = time.sleep

    import os

    def fake_sleep(seconds):  # noqa: ARG001
        step = state["step"]
        state["step"] += 1
        if step == 0:
            targets[0].write_text("# A v2\n", encoding="utf-8")
            mt = targets[0].stat().st_mtime + 1.0
            os.utime(targets[0], (mt, mt))
        elif step == 1:
            targets[1].write_text("# B v2 longer\n", encoding="utf-8")
            mt = targets[1].stat().st_mtime + 2.0
            os.utime(targets[1], (mt, mt))
        elif step == 2:
            targets[2].write_text("# C content\n", encoding="utf-8")
            mt = targets[2].stat().st_mtime + 3.0
            os.utime(targets[2], (mt, mt))
        # After step 2, snapshot stabilizes — debounce should fire on_change
        # exactly once. Raise KeyboardInterrupt only after we've given the
        # loop enough cycles to debounce + retrigger.
        if step >= state["stop_after"]:
            raise KeyboardInterrupt

    monkeypatch.setattr(time, "sleep", fake_sleep)

    loop.run()  # exits cleanly on KeyboardInterrupt
    real_sleep(0)  # touch real_sleep to avoid linter warning

    assert len(received) == 1
    paths_set = set(received[0])
    assert targets[0] in paths_set
    assert targets[1] in paths_set
    assert targets[2] in paths_set


def test_quiet_suppresses_banner(tmp_path):
    project = _make_project(tmp_path)
    stream = io.StringIO()
    loop = WatchLoop(project, on_change=lambda _changed: None, quiet=True, stream=stream)
    loop.run(once=True)
    assert stream.getvalue() == ""


def test_custom_paths_override_defaults(tmp_path):
    project = _make_project(tmp_path)
    extra = project / "extra"
    extra.mkdir()
    (extra / "x.md").write_text("# X\n", encoding="utf-8")
    loop = WatchLoop(project, watch_paths=[extra])
    snap = loop.snapshot()
    assert {p.name for p in snap.keys()} == {"x.md"}


def test_cli_project_watch_once_runs(tmp_path, monkeypatch, capsys):
    project = _make_project(tmp_path)
    from tesserae.cli import main

    rc = main(["export", "site", "--watch", "--project", str(project), "--once", "--quiet"])
    assert rc == 0
