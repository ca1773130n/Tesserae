"""Unit tests for :mod:`llm_wiki.wiki_store`."""

from __future__ import annotations

from pathlib import Path

import pytest

from llm_wiki.wiki_store import WikiPage, WikiPageStore


# --------------------------------------------------------------------- helpers


def _store(tmp_path: Path) -> WikiPageStore:
    return WikiPageStore(tmp_path / "wiki")


def _page(store: WikiPageStore, kind: str, slug: str, body: str, **fm: object) -> WikiPage:
    return WikiPage(
        kind=kind,
        slug=slug,
        title=fm.get("title", slug),  # type: ignore[arg-type]
        body=body,
        path=store.path_for(kind, slug),
        frontmatter=dict(fm),
    )


# ----------------------------------------------------------------------- slug


def test_slug_for_is_deterministic_and_url_safe():
    store = WikiPageStore("/tmp/anywhere")
    cases = [
        ("Gaussian Splatting", "gaussian-splatting"),
        ("3D Reconstruction", "3d-reconstruction"),
        ("Foo / Bar :: Baz!", "foo-bar-baz"),
        ("  trailing-and-leading  ", "trailing-and-leading"),
        ("multi   spaces", "multi-spaces"),
    ]
    for value, expected in cases:
        assert store.slug_for(value) == expected
        # determinism
        assert store.slug_for(value) == store.slug_for(value)


def test_slug_for_handles_non_ascii_and_mixed_punctuation():
    store = WikiPageStore("/tmp/anywhere")
    # Korean: alphanumerics survive (Hangul is alnum in Python's str.isalnum).
    korean = store.slug_for("한글 제목")
    assert korean
    assert korean == store.slug_for("한글 제목")
    # All-punctuation falls back to a stable sha1 prefix (12 hex chars).
    fallback = store.slug_for("!!!---???")
    assert fallback
    assert len(fallback) == 12 and all(c in "0123456789abcdef" for c in fallback)
    assert fallback == store.slug_for("!!!---???")
    # Mixed punctuation collapses to single dashes, no trailing dash.
    mixed = store.slug_for("Hello, world! -- foo.bar")
    assert mixed == "hello-world-foo-bar"


def test_path_for_lays_out_one_folder_per_kind(tmp_path: Path):
    store = _store(tmp_path)
    assert store.path_for("concepts", "diffusion") == tmp_path / "wiki" / "concepts" / "diffusion.md"


# ------------------------------------------------------------------ idempotent


def test_write_page_first_time_returns_true_and_creates_file(tmp_path: Path):
    store = _store(tmp_path)
    page = _page(store, "concepts", "alpha", "# Alpha\n\nBody text.\n", title="Alpha")
    assert store.write_page(page) is True
    assert page.path.exists()
    text = page.path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert "title: Alpha" in text
    assert "# Alpha" in text


def test_write_page_is_idempotent_when_body_unchanged(tmp_path: Path):
    store = _store(tmp_path)
    page = _page(store, "concepts", "alpha", "# Alpha\n\nBody.\n", title="Alpha")
    assert store.write_page(page) is True
    assert store.write_page(page) is False  # second write skipped
    # Mtime/content sanity: file still readable.
    assert page.path.read_text(encoding="utf-8").count("# Alpha") == 1


def test_write_page_rewrites_when_body_changes(tmp_path: Path):
    store = _store(tmp_path)
    first = _page(store, "concepts", "alpha", "# Alpha\n\nBody.\n", title="Alpha")
    assert store.write_page(first) is True
    second = _page(store, "concepts", "alpha", "# Alpha\n\nNew body.\n", title="Alpha")
    assert store.write_page(second) is True
    assert "New body." in second.path.read_text(encoding="utf-8")


def test_write_page_skips_when_only_volatile_frontmatter_changes(tmp_path: Path):
    """Idempotence is keyed on body sha256 alone — frontmatter churn is ignored."""

    store = _store(tmp_path)
    body = "# Pulse\n\nProject pulse synthesis.\n"
    first = _page(
        store,
        "syntheses",
        "pulse",
        body,
        title="Pulse",
        synthesis_kind="pulse",
        generated_at="2026-04-27T12:00:00Z",
        content_hash="sha256-aaa",
    )
    assert store.write_page(first) is True

    second = _page(
        store,
        "syntheses",
        "pulse",
        body,  # identical body
        title="Pulse",
        synthesis_kind="pulse",
        generated_at="2026-04-27T13:30:00Z",  # only this changed
        content_hash="sha256-aaa",
    )
    assert store.write_page(second) is False
    # File on disk must still hold the original timestamp (we did not rewrite).
    assert "2026-04-27T12:00:00Z" in first.path.read_text(encoding="utf-8")
    assert "2026-04-27T13:30:00Z" not in first.path.read_text(encoding="utf-8")


def test_write_page_creates_parent_directories(tmp_path: Path):
    store = _store(tmp_path)
    page = _page(store, "topics", "deep-nested", "# Deep\n", title="Deep")
    # Parent does not yet exist.
    assert not page.path.parent.exists()
    assert store.write_page(page) is True
    assert page.path.parent.is_dir()


# ----------------------------------------------------------------- read / list


def test_read_page_round_trips_frontmatter_and_body(tmp_path: Path):
    store = _store(tmp_path)
    body = "# Splatting\n\nGaussian splatting is a real-time radiance field method.\n"
    page = _page(
        store,
        "concepts",
        "gaussian-splatting",
        body,
        title="Gaussian Splatting",
        synthesis_kind="topic",
        sources=["paper-a.md", "paper-b.md"],
    )
    assert store.write_page(page) is True

    loaded = store.read_page(page.path)
    assert loaded.kind == "concepts"
    assert loaded.slug == "gaussian-splatting"
    assert loaded.title == "Gaussian Splatting"
    assert loaded.body == body
    assert loaded.frontmatter["synthesis_kind"] == "topic"
    assert loaded.frontmatter["sources"] == ["paper-a.md", "paper-b.md"]


def test_read_page_falls_back_to_h1_then_slug_for_title(tmp_path: Path):
    store = _store(tmp_path)
    # No title in frontmatter — first H1 wins.
    body_h1 = "# Hello World\n\nbody\n"
    page = _page(store, "concepts", "hello", body_h1)
    store.write_page(page)
    loaded = store.read_page(page.path)
    assert loaded.title == "Hello World"

    # No title and no H1 — fall back to slug.
    body_no_h1 = "Plain body without a heading.\n"
    page2 = _page(store, "concepts", "plain", body_no_h1)
    store.write_page(page2)
    loaded2 = store.read_page(page2.path)
    assert loaded2.title == "plain"


def test_list_pages_returns_sorted_results_and_skips_non_md(tmp_path: Path):
    store = _store(tmp_path)
    for slug in ("zeta", "alpha", "mu"):
        store.write_page(_page(store, "concepts", slug, f"# {slug}\n", title=slug))

    # Drop noise: a non-md file and a directory.
    noise_dir = store.root / "concepts"
    (noise_dir / "README.txt").write_text("ignore me", encoding="utf-8")
    (noise_dir / "subdir").mkdir()

    listed = store.list_pages("concepts")
    assert [p.slug for p in listed] == ["alpha", "mu", "zeta"]


def test_list_pages_returns_empty_for_unknown_kind(tmp_path: Path):
    store = _store(tmp_path)
    assert store.list_pages("does-not-exist") == []
