"""Unit tests for ``tesserae.clip`` — the web-clip ingestion helper.

These tests cover :func:`tesserae.clip.build_clip_markdown` (pure string
assembly) and :func:`tesserae.clip.ingest_clip` (write-to-corpus +
``ingest_sources`` dispatch).

No real network and no real LLM are exercised:

* ``build_clip_markdown`` is pure and touches neither.
* For ``ingest_clip`` we monkeypatch the summarizer
  (``tesserae.clip._summarize``) so the TL;DR path never shells out to the
  Claude CLI, and we monkeypatch ``tesserae.clip.ingest_sources`` so no real
  ``wiki.compile()`` / extraction runs.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tesserae import clip


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _bootstrap_project(tmp_path: Path) -> Path:
    """Minimal ``.tesserae/config.json`` so ``ProjectWiki.load`` succeeds."""
    project = tmp_path / "demo"
    project.mkdir()
    cfg = project / ".tesserae"
    cfg.mkdir()
    (cfg / "config.json").write_text(
        json.dumps({"name": "demo", "sources": ["README.md"], "external_tools": []}),
        encoding="utf-8",
    )
    (project / "README.md").write_text("# demo\n", encoding="utf-8")
    return project


# ---------------------------------------------------------------------------
# build_clip_markdown — pure string assembly
# ---------------------------------------------------------------------------


def test_build_clip_markdown_frontmatter_keys_present() -> None:
    md = clip.build_clip_markdown(
        content="Body paragraph.",
        url="https://example.com/article",
        title="An Article",
        tags=["python", "web"],
        note="read later",
        clipped_at="2026-06-13T00:00:00Z",
    )
    # Front-matter is the first block, delimited by --- fences.
    assert md.startswith("---\n")
    head, _, _ = md.partition("\n---\n")
    # The agreed schema: source: web-clip, url, title, tags, note, clipped_at.
    assert "source: web-clip" in head
    assert "url:" in head and "https://example.com/article" in head
    assert "title:" in head
    assert "An Article" in head
    assert "clipped_at: 2026-06-13T00:00:00Z" in head
    assert "tags:" in head
    assert "note:" in head


def test_build_clip_markdown_content_section_always_present() -> None:
    md = clip.build_clip_markdown(
        content="The body text.",
        url="https://example.com/x",
        title="X",
    )
    assert "## Content" in md
    assert "The body text." in md


def test_build_clip_markdown_tldr_only_when_given() -> None:
    without = clip.build_clip_markdown(
        content="body", url="https://e.com/a", title="A"
    )
    assert "## TL;DR" not in without

    with_tldr = clip.build_clip_markdown(
        content="body",
        url="https://e.com/a",
        title="A",
        tldr_text="This is the summary.",
    )
    assert "## TL;DR" in with_tldr
    assert "This is the summary." in with_tldr


def test_build_clip_markdown_note_only_when_given() -> None:
    without = clip.build_clip_markdown(
        content="body", url="https://e.com/a", title="A"
    )
    assert "## Note" not in without

    with_note = clip.build_clip_markdown(
        content="body",
        url="https://e.com/a",
        title="A",
        note="remember this",
    )
    assert "## Note" in with_note
    assert "remember this" in with_note


# ---------------------------------------------------------------------------
# ingest_clip — corpus write + ingest_sources dispatch
# ---------------------------------------------------------------------------


def _patch_ingest_sources(monkeypatch: pytest.MonkeyPatch, captured: dict):
    """Replace ``ingest_sources`` with a recorder that does no real compile.

    ``ingest_clip`` does a lazy ``from .ingest.orchestrator import ingest_sources``,
    so we patch the function on its defining module.
    """

    def _fake_ingest_sources(wiki, inputs, **kwargs):
        captured["wiki"] = wiki
        captured["inputs"] = list(inputs)
        captured["kwargs"] = kwargs
        return {
            "path_taken": "incremental",
            "sources": list(inputs),
            "node_count": 7,
            "edge_count": 3,
            "processed_files": list(inputs),
            "skipped_files": [],
            "graph_path": "graph.json",
        }

    monkeypatch.setattr(
        "tesserae.ingest.orchestrator.ingest_sources", _fake_ingest_sources
    )


def test_ingest_clip_writes_file_and_returns_report_with_tldr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _bootstrap_project(tmp_path)

    # No real LLM: summarizer returns a fixed string.
    monkeypatch.setattr(clip, "_summarize", lambda content: "A fixed TL;DR.")

    captured: dict = {}
    _patch_ingest_sources(monkeypatch, captured)

    from tesserae.project import ProjectWiki

    wiki = ProjectWiki.load(project)

    report = clip.ingest_clip(
        wiki,
        content="The full article body to be clipped.",
        url="https://example.com/post",
        title="A Post",
        note="for later",
        tags=["a", "b"],
        tldr=True,
        clipped_at="2026-06-13T00:00:00Z",
    )

    # Documented report keys.
    assert report["status"] == "ok"
    assert report["node_count"] == 7
    assert report["edge_count"] == 3
    assert report["tldr"] == "A fixed TL;DR."

    # A markdown file landed under data/ingested/.
    written = Path(report["path"])
    assert written.exists()
    assert written.parent == (project / "data" / "ingested")
    text = written.read_text(encoding="utf-8")
    assert "source: web-clip" in text
    assert "## TL;DR" in text
    assert "A fixed TL;DR." in text
    assert "## Content" in text
    assert "The full article body to be clipped." in text

    # ingest_sources received the written path.
    assert captured["inputs"] == [str(written)]
    assert captured["wiki"] is wiki


def test_ingest_clip_tldr_none_on_summarizer_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _bootstrap_project(tmp_path)

    # Summarizer yields None (best-effort skip) — no '## TL;DR' section.
    monkeypatch.setattr(clip, "_summarize", lambda content: None)

    captured: dict = {}
    _patch_ingest_sources(monkeypatch, captured)

    from tesserae.project import ProjectWiki

    wiki = ProjectWiki.load(project)

    report = clip.ingest_clip(
        wiki,
        content="Body without a summary.",
        url="https://example.com/no-summary",
        title="No Summary",
        tldr=True,
    )

    assert report["status"] == "ok"
    assert report["tldr"] is None

    written = Path(report["path"])
    assert written.exists()
    text = written.read_text(encoding="utf-8")
    assert "## TL;DR" not in text
    assert "## Content" in text
    assert "Body without a summary." in text


def test_ingest_clip_skips_summarizer_when_tldr_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _bootstrap_project(tmp_path)

    # If tldr=False the summarizer must never be invoked — make it explode if it is.
    def _boom(content):
        raise AssertionError("_summarize should not be called when tldr=False")

    monkeypatch.setattr(clip, "_summarize", _boom)

    captured: dict = {}
    _patch_ingest_sources(monkeypatch, captured)

    from tesserae.project import ProjectWiki

    wiki = ProjectWiki.load(project)

    report = clip.ingest_clip(
        wiki,
        content="Body.",
        url="https://example.com/plain",
        title="Plain",
        tldr=False,
    )

    assert report["status"] == "ok"
    assert report["tldr"] is None
    assert Path(report["path"]).exists()
