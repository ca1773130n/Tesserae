"""Tests for :class:`FilesystemSourceLoader`.

Verifies the FS source-loader adapter that satisfies the ``SourceLoader``
protocol by walking one or more directory trees and yielding one ``Source``
per file matching the configured extensions.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tesserae.ports import Source, SourceLoader
from tesserae.source_loaders import FilesystemSourceLoader


def test_discover_yields_one_source_per_md_file(tmp_path: Path) -> None:
    """Three .md files under one root should yield three Sources."""
    (tmp_path / "a.md").write_text("alpha", encoding="utf-8")
    (tmp_path / "b.md").write_text("bravo", encoding="utf-8")
    (tmp_path / "c.md").write_text("charlie", encoding="utf-8")

    loader = FilesystemSourceLoader([tmp_path], extensions=(".md",))
    sources = list(loader.discover())

    assert len(sources) == 3
    contents = sorted(s.content for s in sources)
    assert contents == ["alpha", "bravo", "charlie"]
    for source in sources:
        assert isinstance(source, Source)


def test_discover_skips_excluded_extensions(tmp_path: Path) -> None:
    """A .md file is kept; a .pyc file is skipped under default-ish filtering."""
    (tmp_path / "kept.md").write_text("kept", encoding="utf-8")
    (tmp_path / "skipped.pyc").write_bytes(b"\x00\x01\x02")

    loader = FilesystemSourceLoader([tmp_path], extensions=(".md",))
    sources = list(loader.discover())

    assert len(sources) == 1
    assert sources[0].content == "kept"


def test_discover_yields_id_as_relative_path(tmp_path: Path) -> None:
    """``Source.id`` should be the relative path string from the root."""
    nested = tmp_path / "sub" / "deep"
    nested.mkdir(parents=True)
    (nested / "note.md").write_text("body", encoding="utf-8")

    loader = FilesystemSourceLoader([tmp_path], extensions=(".md",))
    sources = list(loader.discover())

    assert len(sources) == 1
    # Relative path with forward slashes (deterministic across platforms).
    assert sources[0].id == "sub/deep/note.md"


def test_discover_includes_metadata(tmp_path: Path) -> None:
    """Every discovered Source must carry mtime/size/extension metadata."""
    target = tmp_path / "x.md"
    target.write_text("hello", encoding="utf-8")

    loader = FilesystemSourceLoader([tmp_path], extensions=(".md",))
    [source] = list(loader.discover())

    assert "mtime" in source.metadata
    assert "size" in source.metadata
    assert "extension" in source.metadata
    assert source.metadata["extension"] == ".md"
    assert source.metadata["size"] == len("hello")
    assert isinstance(source.metadata["mtime"], float)


def test_fetch_returns_source_by_id(tmp_path: Path) -> None:
    """``fetch`` must return the same Source ``discover`` yielded for that id."""
    (tmp_path / "doc.md").write_text("payload", encoding="utf-8")

    loader = FilesystemSourceLoader([tmp_path], extensions=(".md",))
    [discovered] = list(loader.discover())
    fetched = loader.fetch(discovered.id)

    assert fetched.id == discovered.id
    assert fetched.content == "payload"
    assert fetched.path == discovered.path


def test_fetch_raises_keyerror_for_unknown_id(tmp_path: Path) -> None:
    """``fetch`` on an id that was never discovered should raise KeyError.

    Unknown ids are a programmer/lookup error (the id was never registered
    by ``discover``), distinct from the environmental case where the id is
    known but the file has been deleted on disk.
    """
    loader = FilesystemSourceLoader([tmp_path], extensions=(".md",))
    list(loader.discover())  # populate discovery cache (no files)

    with pytest.raises(KeyError):
        loader.fetch("nonexistent.md")


def test_fetch_raises_filenotfound_when_file_deleted_after_discover(
    tmp_path: Path,
) -> None:
    """``fetch`` on a known id whose file was deleted on disk raises FileNotFoundError.

    This separates the environmental error (file is gone) from the
    programmer error (id was never registered) tested above.
    """
    target = tmp_path / "ephemeral.md"
    target.write_text("here-and-gone", encoding="utf-8")

    loader = FilesystemSourceLoader([tmp_path], extensions=(".md",))
    [discovered] = list(loader.discover())

    target.unlink()

    with pytest.raises(FileNotFoundError):
        loader.fetch(discovered.id)


def test_discover_repopulates_cache_after_filesystem_change(tmp_path: Path) -> None:
    """Re-running discover() after files change reflects the new state."""
    (tmp_path / "a.md").write_text("alpha", encoding="utf-8")
    (tmp_path / "b.md").write_text("beta", encoding="utf-8")
    loader = FilesystemSourceLoader([tmp_path], extensions=(".md",))

    sources = list(loader.discover())
    assert {s.id for s in sources} == {"a.md", "b.md"}

    (tmp_path / "a.md").unlink()
    (tmp_path / "c.md").write_text("gamma", encoding="utf-8")

    sources_v2 = list(loader.discover())
    assert {s.id for s in sources_v2} == {"b.md", "c.md"}

    # Cache for the dropped id should now miss with KeyError (unknown id).
    with pytest.raises(KeyError):
        loader.fetch("a.md")


def test_fetch_rereads_file_after_discovery(tmp_path: Path) -> None:
    """``fetch()`` returns current disk content, not a stale snapshot from discover()."""
    f = tmp_path / "x.md"
    f.write_text("v1", encoding="utf-8")
    loader = FilesystemSourceLoader([tmp_path], extensions=(".md",))
    list(loader.discover())  # populate cache

    f.write_text("v2-updated", encoding="utf-8")
    fetched = loader.fetch("x.md")

    assert fetched.content == "v2-updated"


def test_filesystem_source_loader_is_runtime_checkable_source_loader(
    tmp_path: Path,
) -> None:
    """The loader must satisfy the runtime-checkable ``SourceLoader`` protocol."""
    loader = FilesystemSourceLoader([tmp_path], extensions=(".md",))
    assert isinstance(loader, SourceLoader)


def test_iter_paths_skips_pytest_tmpdirs_below_a_root(tmp_path: Path) -> None:
    """``pytest-of-<user>/`` inside a source root is throwaway fixture output.

    When ``$TMPDIR`` resolves to the repo, ``tmp_path_factory`` plants thousands
    of fixture .md files under the project root; they then churn every test run
    so the candidate set never repeats and changed-only can never no-op. On the
    Tesserae repo this was 418 live files feeding 301 graph nodes. The name is
    ``$USER``-suffixed, hence prefix matching rather than a set member.
    """
    (tmp_path / "real.md").write_text("kept", encoding="utf-8")
    junk = tmp_path / "pytest-of-neo" / "pytest-50" / "test_thing0"
    junk.mkdir(parents=True)
    (junk / "fixture.md").write_text("dropped", encoding="utf-8")
    # Same class, pip's scratch dirs — already enumerated in .gitignore.
    pip_junk = tmp_path / "pip-install-8xk2j1" / "somepkg"
    pip_junk.mkdir(parents=True)
    (pip_junk / "README.md").write_text("dropped", encoding="utf-8")

    loader = FilesystemSourceLoader([tmp_path], extensions=(".md",))

    assert [p.name for p in loader.iter_paths(tmp_path)] == ["real.md"]


def test_iter_paths_walks_a_pytest_tmpdir_passed_as_the_root(tmp_path: Path) -> None:
    """The exclusion is on components BELOW the root, never the root itself.

    Every test in this suite hands ``tmp_path`` — which *is* a
    ``pytest-of-<user>/pytest-N/<test>`` directory — to the walker as a source
    root. Matching the root would silently empty the corpus in every fixture
    project, so the escape hatch documented on ``_EXCLUDED_TOPLEVEL_DIRS``
    (name a filtered directory explicitly and it is walked) must hold here too.
    """
    root = tmp_path / "pytest-of-someone" / "pytest-7" / "test_case0"
    (root / "nested").mkdir(parents=True)
    (root / "doc.md").write_text("kept", encoding="utf-8")
    (root / "nested" / "deep.md").write_text("kept", encoding="utf-8")

    loader = FilesystemSourceLoader([root], extensions=(".md",))
    found = list(loader.iter_paths(root))

    # A NON-ZERO count is the point of the assertion. If the filter is ever
    # moved to match ``root`` instead of ``rel.parts``, every fixture corpus in
    # the suite empties — and a membership-only check would still pass on the
    # empty list, as would every downstream test that merely asserts a graph
    # compiled without error.
    assert len(found) == 2
    assert {p.name for p in found} == {"doc.md", "deep.md"}


def test_iter_paths_keeps_similarly_named_real_directories(tmp_path: Path) -> None:
    """The prefixes must not swallow legitimate directories that merely start
    with the same letters. ``pytest-helpers/`` is real source; ``pipeline/`` and
    ``tmpl/`` are the near-misses for the prefixes deliberately left OUT of the
    tuple (``pip-install-``, and mkdtemp's ``tmp``). Over-broad matching here
    silently deletes corpus, which is the failure mode a prefix blocklist is
    most prone to.
    """
    for name in ("pipeline", "pytest-helpers", "tmpl", "pip"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "doc.md").write_text("kept", encoding="utf-8")

    loader = FilesystemSourceLoader([tmp_path], extensions=(".md",))

    assert len(list(loader.iter_paths(tmp_path))) == 4


def test_excluded_prefixes_are_a_directory_rule_not_a_filename_rule(tmp_path):
    """A document whose NAME starts with an excluded prefix must still be indexed.

    `rel.parts` includes the filename, so matching it would silently drop ordinary
    docs — and Tesserae's own concept slugifier mints `pip-install-<x>.md` from any
    "pip install <x>" heading. Silent under-indexing is worse than the pollution
    this exclusion exists to prevent.
    """
    from tesserae.source_loaders import FilesystemSourceLoader

    docs = tmp_path / "docs"
    docs.mkdir()
    for name in (
        "pip-install-guide.md",        # prefix 'pip' at the filename
        "pytest-of-note.md",           # prefix 'pytest-of' at the filename
        "tmp-notes.md",                # prefix 'tmp' at the filename
        "overview.md",
    ):
        (docs / name).write_text("# doc", encoding="utf-8")
    # ...but a real excluded DIRECTORY still gets skipped.
    junk = tmp_path / "pytest-of-neo" / "pytest-3"
    junk.mkdir(parents=True)
    (junk / "fixture.md").write_text("# junk", encoding="utf-8")

    found = sorted(p.name for p in FilesystemSourceLoader([tmp_path], extensions=(".md",)).iter_paths(tmp_path))
    assert found == ["overview.md", "pip-install-guide.md", "pytest-of-note.md", "tmp-notes.md"], found
    assert "fixture.md" not in found  # the directory rule still bites
