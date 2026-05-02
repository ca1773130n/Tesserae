"""Tests for :class:`FilesystemSourceLoader`.

Verifies the FS source-loader adapter that satisfies the ``SourceLoader``
protocol by walking one or more directory trees and yielding one ``Source``
per file matching the configured extensions.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from llm_wiki.ports import Source, SourceLoader
from llm_wiki.source_loaders import FilesystemSourceLoader


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


def test_fetch_raises_filenotfound_for_unknown_id(tmp_path: Path) -> None:
    """``fetch`` on an id that was never discovered should raise FileNotFoundError."""
    loader = FilesystemSourceLoader([tmp_path], extensions=(".md",))
    list(loader.discover())  # populate discovery cache (no files)

    with pytest.raises(FileNotFoundError):
        loader.fetch("nonexistent.md")


def test_filesystem_source_loader_is_runtime_checkable_source_loader(
    tmp_path: Path,
) -> None:
    """The loader must satisfy the runtime-checkable ``SourceLoader`` protocol."""
    loader = FilesystemSourceLoader([tmp_path], extensions=(".md",))
    assert isinstance(loader, SourceLoader)
