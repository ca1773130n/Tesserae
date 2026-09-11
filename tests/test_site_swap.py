"""The served site must never be absent while it is being rebuilt.

`StaticSiteBuilder.write_site` rmtree's its target before writing. With the
daemon serving `.tesserae/site` out of the same process that recompiles it,
that made "a source edit propagates to a live-served page" deliver as "the
whole site 404s for the length of a compile, then comes back changed".

`build_site` now writes to a staging sibling and swaps with two renames.
"""
from __future__ import annotations

import threading

import pytest
from pathlib import Path

from tesserae.project import ProjectWiki


def _seed(tmp_path: Path) -> ProjectWiki:
    (tmp_path / "docs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "docs" / "a.md").write_text(
        "# Alpha\n\nRetrieval-Augmented Generation grounds answers.\n", encoding="utf-8"
    )
    wiki = ProjectWiki.init(tmp_path, name="swap")
    wiki.compile()
    return wiki


def test_the_site_index_never_disappears_during_a_rebuild(tmp_path: Path) -> None:
    """Hammer the served path while build_site runs. Zero misses allowed."""
    wiki = _seed(tmp_path)
    index = wiki.paths.site / "index.html"
    assert index.is_file(), "fixture is wrong: no site was built"

    misses: list[str] = []
    stop = threading.Event()

    def hammer() -> None:
        while not stop.is_set():
            try:
                index.read_bytes()
            except FileNotFoundError:
                misses.append("index.html vanished")
            except OSError:
                # A partially-swapped directory can raise other errors; those
                # are the same defect wearing a different errno.
                misses.append("index.html unreadable")

    t = threading.Thread(target=hammer, daemon=True, name="site-reader")
    t.start()
    try:
        for _ in range(3):
            wiki.build_site()
    finally:
        stop.set()
        t.join(timeout=5)

    assert not misses, f"served site went missing {len(misses)}x during rebuild"


def test_the_swap_leaves_no_scratch_directories(tmp_path: Path) -> None:
    """A crash-free build must not leave .site.staging/.site.retired behind."""
    wiki = _seed(tmp_path)
    wiki.build_site()
    leftovers = [
        p.name for p in wiki.paths.site.parent.iterdir()
        if p.name in {".site.staging", ".site.retired"}
    ]
    assert not leftovers, f"scratch dirs left behind: {leftovers}"


def test_an_explicit_output_keeps_the_old_behaviour(tmp_path: Path) -> None:
    """`export site --output` is a one-shot export, not the served root."""
    wiki = _seed(tmp_path)
    out = tmp_path / "exported"
    wiki.build_site(output=out)
    assert (out / "index.html").is_file()
    assert not (out.parent / ".site.staging").exists()


def test_the_exchange_is_atomic_on_this_platform(tmp_path: Path) -> None:
    """The hammer test above is only honest while the one-syscall swap is live.

    If ctypes lookup breaks, build_site silently degrades to the two-rename
    fallback, which a tight-loop reader hits 20–300 times per rebuild. Pin it.
    """
    import sys

    from tesserae.project import _exchange_directories

    if sys.platform not in ("darwin",) and not sys.platform.startswith("linux"):
        pytest.skip("no atomic directory exchange on this platform")
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(); b.mkdir()
    (a / "who").write_text("a", encoding="utf-8")
    (b / "who").write_text("b", encoding="utf-8")
    assert _exchange_directories(a, b) is True
    assert (a / "who").read_text(encoding="utf-8") == "b"
    assert (b / "who").read_text(encoding="utf-8") == "a"
