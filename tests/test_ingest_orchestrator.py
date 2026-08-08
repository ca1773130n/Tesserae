import re
from pathlib import Path

import pytest

from tesserae.project import ProjectWiki
from tesserae.ingest.orchestrator import ingest_sources


def _seed_project(root: Path) -> ProjectWiki:
    (root / "data").mkdir(parents=True, exist_ok=True)
    (root / "data" / "seed.md").write_text(
        "---\ntype: paper\n---\n# Seed\n\nGraph neural networks for retrieval.\n",
        encoding="utf-8",
    )
    return ProjectWiki.init(root, name="ingest_test")


def test_ingest_local_file_merges_and_reports(tmp_path):
    wiki = _seed_project(tmp_path)
    wiki.compile(changed_only=False)  # establish baseline graph

    # NOTE: the deterministic extractor surfaces the heading (node name) into
    # graph.json, not body prose. Put the distinguishing token in the heading so
    # the assertion below tests real merge behavior, not a false fixture assumption.
    new = tmp_path / "data" / "new.md"
    new.write_text(
        "---\ntype: paper\n---\n# Diffusion Models for Planning\n\nDiffusion models for planning.\n",
        encoding="utf-8",
    )

    result = ingest_sources(wiki, [str(new)], exact=True)

    assert result["path_taken"] == "full-recompile"
    assert result["node_count"] > 0
    assert "graph_path" in result
    graph_text = Path(result["graph_path"]).read_text(encoding="utf-8")
    assert "Diffusion" in graph_text or "diffusion" in graph_text.lower()


def test_ingest_url_fetches_persists_and_merges(tmp_path, monkeypatch):
    wiki = _seed_project(tmp_path)
    wiki.compile(changed_only=False)

    class _Resp:
        status_code = 200
        text = "<h1>Remote</h1><p>Reinforcement learning from human feedback.</p>"
        headers = {"content-type": "text/html"}
        def raise_for_status(self): pass

    monkeypatch.setattr(
        "tesserae.ingest.fetch._http_get",
        lambda url, timeout=None, follow_redirects=True, headers=None: _Resp(),
    )
    result = ingest_sources(wiki, ["https://example.com/rlhf"], exact=True)

    persisted = tmp_path / "data" / "ingested"
    assert any(persisted.glob("*.md"))
    assert result["path_taken"] == "full-recompile"
    assert result["node_count"] > 0


def test_ingest_dry_run_writes_no_graph_but_fetches(tmp_path, monkeypatch):
    wiki = _seed_project(tmp_path)
    new = tmp_path / "data" / "dry.md"
    new.write_text("---\ntype: paper\n---\n# Dry\n\ncontent\n", encoding="utf-8")

    result = ingest_sources(wiki, [str(new)], dry_run=True)
    assert result["path_taken"] == "dry-run"
    assert result["sources"] == [str(new)]


def test_ingest_fast_path_uses_incremental_override(tmp_path, monkeypatch):
    wiki = _seed_project(tmp_path)
    wiki.compile(changed_only=False)
    seen = {}
    real_ingest = wiki.ingest
    def _spy(inputs, **kw):
        seen.update(kw)
        return real_ingest(inputs, **kw)
    monkeypatch.setattr(wiki, "ingest", _spy)

    (tmp_path / "data" / "f.md").write_text("---\ntype: paper\n---\n# F\n\ncontent\n", encoding="utf-8")
    result = ingest_sources(wiki, [str(tmp_path / "data" / "f.md")], exact=False)

    assert seen.get("changed_only") is True
    assert seen.get("incremental_override") is True
    assert result["path_taken"] == "incremental"


def test_ingest_preserves_baseline_and_copies_out_of_corpus(tmp_path):
    wiki = _seed_project(tmp_path)
    wiki.compile(changed_only=False)
    import json
    before = json.loads(wiki.paths.graph.read_text())
    baseline_ids = {n["id"] for n in before["nodes"]}
    assert baseline_ids  # seed produced nodes

    # a file OUTSIDE the project corpus
    external = tmp_path.parent / "external_paper.md"
    external.write_text("---\ntype: paper\n---\n# External Topic\n\nbody\n", encoding="utf-8")
    result = ingest_sources(wiki, [str(external)], exact=True)

    after = json.loads(wiki.paths.graph.read_text())
    after_ids = {n["id"] for n in after["nodes"]}
    assert baseline_ids <= after_ids, "ingest must NOT drop baseline nodes"
    assert len(after_ids) > len(baseline_ids), "the new external doc must be added"
    # the external file was copied into the tracked corpus
    assert (tmp_path / "data" / "ingested" / "external_paper.md").exists()


# ---------------------------------------------------------------------------
# Binary inputs must fail LOUDLY. Before this guard, `ingest paper.pdf` copied
# the file into data/ingested/, drove a compile whose walker matches .md only
# (project.py:iter_markdown_files returns [] for any other suffix), and printed
# node/edge counts belonging to the REST of the corpus — reported success for
# work it had not done.
# ---------------------------------------------------------------------------


def _minimal_pdf() -> bytes:
    return (
        b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"
    )


def test_ingest_local_pdf_raises_and_names_the_raganything_remedy(tmp_path):
    from tesserae.ingest.orchestrator import UnsupportedSourceError

    wiki = _seed_project(tmp_path)
    outside = tmp_path.parent / f"{tmp_path.name}-paper.pdf"
    outside.write_bytes(_minimal_pdf())

    with pytest.raises(UnsupportedSourceError) as exc:
        ingest_sources(wiki, [str(outside)], exact=True)

    message = str(exc.value)
    assert ".pdf" in message
    assert "raganything" in message
    # Same shape as raganything_refresh._verify_parsers_or_raise: a header line
    # saying why it cannot run, then one indented "  - <thing>: <hint>" remedy.
    assert message.splitlines()[0].endswith(":")
    assert any(line.startswith("  - ") for line in message.splitlines()[1:])
    # Nothing was copied into the corpus: the guard runs before the copy.
    assert not (tmp_path / "data" / "ingested" / outside.name).exists()


def test_ingest_pdf_already_inside_the_project_root_also_raises(tmp_path):
    """_ensure_in_corpus returns in-corpus paths IN PLACE, so a guard at the
    copy site alone would miss every binary that already lives under the root."""
    from tesserae.ingest.orchestrator import UnsupportedSourceError

    wiki = _seed_project(tmp_path)
    inside = tmp_path / "data" / "inside.pdf"
    inside.write_bytes(_minimal_pdf())

    with pytest.raises(UnsupportedSourceError, match=r"\.pdf"):
        ingest_sources(wiki, [str(inside)], exact=True)


@pytest.mark.parametrize("suffix", [".png", ".jpg", ".jpeg", ".docx"])
def test_ingest_image_and_office_inputs_are_rejected(tmp_path, suffix):
    from tesserae.ingest.orchestrator import UnsupportedSourceError

    wiki = _seed_project(tmp_path)
    binary = tmp_path / "data" / f"figure{suffix}"
    binary.write_bytes(b"\x89PNG\r\n\x1a\n not really an image")

    with pytest.raises(UnsupportedSourceError, match=re.escape(suffix)):
        ingest_sources(wiki, [str(binary)], exact=True)


def test_ingest_dry_run_also_rejects_a_binary_input(tmp_path):
    """--dry-run must not report a PDF as something it WOULD ingest: the
    validation loop already runs before the dry-run short-circuit."""
    from tesserae.ingest.orchestrator import UnsupportedSourceError

    wiki = _seed_project(tmp_path)
    binary = tmp_path / "data" / "dry.pdf"
    binary.write_bytes(_minimal_pdf())

    with pytest.raises(UnsupportedSourceError):
        ingest_sources(wiki, [str(binary)], dry_run=True)


def test_ingest_url_binary_response_is_not_decoded_hashed_or_written_as_md(
    tmp_path, monkeypatch
):
    """A PDF URL used to be decoded lossily into mojibake, hashed as TEXT, and
    written under a .md suffix — so unlike a local PDF it WAS picked up by the
    markdown walker and fed to the extractor as prose.

    The stub must return real BYTES: a hand-rolled response whose ``.text`` is
    already a ``str`` cannot exercise the decode at all.
    """
    import httpx

    from tesserae.ingest.orchestrator import UnsupportedSourceError

    wiki = _seed_project(tmp_path)
    url = "https://arxiv.org/pdf/2310.11511v1"
    response = httpx.Response(
        200,
        content=b"%PDF-1.4\n\x89\xa0\xfe\x0c binary \xff\xfe\x00\x01\n%%EOF\n",
        headers={"content-type": "application/pdf"},
        request=httpx.Request("GET", url),
    )
    monkeypatch.setattr(
        "tesserae.ingest.fetch._http_get",
        lambda u, timeout=None, follow_redirects=True, headers=None: response,
    )
    monkeypatch.setattr("tesserae.ingest.fetch._html_to_markdown", lambda html: html)

    with pytest.raises(UnsupportedSourceError) as exc:
        ingest_sources(wiki, [url], exact=True)

    assert "application/pdf" in str(exc.value)
    # Writes nothing on refusal — no mojibake .md left in the corpus.
    assert not list((tmp_path / "data" / "ingested").glob("*.md"))
