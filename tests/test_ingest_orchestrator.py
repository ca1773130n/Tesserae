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
# Inputs compile will never read must fail LOUDLY. Before this guard,
# `ingest paper.pdf` copied the file into data/ingested/, drove a compile whose
# walker matches .md only (project.py:iter_markdown_files returns [] for any
# other suffix), and printed node/edge counts belonging to the REST of the
# corpus — reported success for work it had not done.
#
# These tests deliberately do NOT import UnsupportedSourceError. A test that
# imports a symbol the old code lacks fails at collection and never reaches the
# call, so it proves nothing about behaviour. Catching Exception and asserting
# on the message makes the old code fail the way it actually fails: by
# returning a success report.
# ---------------------------------------------------------------------------


def _minimal_pdf() -> bytes:
    return (
        b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"
    )


def test_ingest_local_pdf_refuses_instead_of_reporting_success(tmp_path):
    wiki = _seed_project(tmp_path)
    outside = tmp_path.parent / f"{tmp_path.name}-paper.pdf"
    outside.write_bytes(_minimal_pdf())

    with pytest.raises(Exception) as exc:
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


def test_raganything_remedy_starts_by_getting_the_file_under_the_project_root(tmp_path):
    """raganything_refresh.discover_sources only walks under the project root,
    and the refusal fires BEFORE the copy into data/ingested — so for the
    primary case (a PDF outside the corpus) a user who runs the suggested
    `tesserae refresh raganything` verbatim parses nothing at all. The hint has
    to name that first step."""
    wiki = _seed_project(tmp_path)
    outside = tmp_path.parent / f"{tmp_path.name}-remote.pdf"
    outside.write_bytes(_minimal_pdf())

    with pytest.raises(Exception) as exc:
        ingest_sources(wiki, [str(outside)], exact=True)

    message = str(exc.value)
    remedy = next(line for line in message.splitlines() if line.startswith("  - "))
    # The copy step has to come first, and it has to name a real destination.
    assert "data/ingested" in remedy
    assert remedy.index("data/ingested") < remedy.index("refresh raganything")


def test_ingest_pdf_already_inside_the_project_root_also_refuses(tmp_path):
    """_ensure_in_corpus returns in-corpus paths IN PLACE, so a guard at the
    copy site alone would miss every binary that already lives under the root."""
    wiki = _seed_project(tmp_path)
    inside = tmp_path / "data" / "inside.pdf"
    inside.write_bytes(_minimal_pdf())

    with pytest.raises(Exception, match=r"\.pdf"):
        ingest_sources(wiki, [str(inside)], exact=True)


@pytest.mark.parametrize("suffix", [".png", ".jpg", ".jpeg", ".docx"])
def test_ingest_image_and_office_inputs_are_refused(tmp_path, suffix):
    wiki = _seed_project(tmp_path)
    binary = tmp_path / "data" / f"figure{suffix}"
    binary.write_bytes(b"\x89PNG\r\n\x1a\n not really an image")

    with pytest.raises(Exception, match=re.escape(suffix)):
        ingest_sources(wiki, [str(binary)], exact=True)


def test_unknown_format_hint_names_a_path_that_will_actually_resolve(tmp_path):
    """A .zip is in no backend's supported set, so it takes the else-branch:
    "convert it to markdown, then ingest <that>". Building that path from
    ``path.stem`` alone yielded `tesserae ingest figure.md` for an absolute
    input — a command that does not resolve from the user's cwd."""
    wiki = _seed_project(tmp_path)
    archive = tmp_path.parent / f"{tmp_path.name}-figures.zip"
    archive.write_bytes(b"PK\x03\x04 not a real archive")

    with pytest.raises(Exception) as exc:
        ingest_sources(wiki, [str(archive)], exact=True)

    message = str(exc.value)
    assert "raganything" not in message  # no backend parses .zip
    remedy = next(line for line in message.splitlines() if line.startswith("  - "))
    suggested = remedy.split("tesserae ingest ", 1)[1].split()[0].rstrip("`.")
    assert suggested == str(archive.with_suffix(".md"))


def test_ingest_dry_run_also_refuses_a_binary_input(tmp_path):
    """--dry-run must not report a PDF as something it WOULD ingest: the
    validation loop already runs before the dry-run short-circuit."""
    wiki = _seed_project(tmp_path)
    binary = tmp_path / "data" / "dry.pdf"
    binary.write_bytes(_minimal_pdf())

    with pytest.raises(Exception):
        ingest_sources(wiki, [str(binary)], dry_run=True)


# --- directories -----------------------------------------------------------
# iter_markdown_files handles directories deliberately, so a directory is a
# supported input shape — and the file-only guard skipped every one of them.


def test_ingest_directory_with_nothing_readable_refuses(tmp_path):
    """The headline defect, verbatim, for a directory: exit 0 and
    "processed=1 ... nodes=1" where processed=1 counted the DIRECTORY."""
    wiki = _seed_project(tmp_path)
    papers = tmp_path / "data" / "papers"
    papers.mkdir(parents=True)
    (papers / "a.pdf").write_bytes(_minimal_pdf())
    (papers / "b.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    with pytest.raises(Exception) as exc:
        ingest_sources(wiki, [str(papers)], exact=True)

    message = str(exc.value)
    assert "a.pdf" in message
    assert "b.png" in message


def test_ingest_empty_directory_refuses(tmp_path):
    """An empty directory produces the same lie — a success report for a
    compile that read nothing the user pointed at."""
    wiki = _seed_project(tmp_path)
    empty = tmp_path / "data" / "empty"
    empty.mkdir(parents=True)

    with pytest.raises(Exception, match="empty"):
        ingest_sources(wiki, [str(empty)], exact=True)


def test_ingest_directory_holding_any_markdown_is_still_accepted(tmp_path):
    """Over-refusal would be its own regression: a docs/ directory that also
    holds screenshots is a normal, working input. Refuse only when NOTHING in
    the directory is readable."""
    wiki = _seed_project(tmp_path)
    mixed = tmp_path / "data" / "mixed"
    mixed.mkdir(parents=True)
    (mixed / "note.md").write_text(
        "---\ntype: paper\n---\n# Retrieval Augmented Planning\n\nprose.\n",
        encoding="utf-8",
    )
    (mixed / "figure.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    result = ingest_sources(wiki, [str(mixed)], exact=True)

    assert result["node_count"] > 0


def test_ingest_nested_markdown_deeper_in_a_directory_is_accepted(tmp_path):
    """The readability check has to walk the tree the compile walker walks,
    not just the top level of the directory."""
    wiki = _seed_project(tmp_path)
    top = tmp_path / "data" / "tree"
    (top / "sub" / "deeper").mkdir(parents=True)
    (top / "cover.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (top / "sub" / "deeper" / "note.md").write_text(
        "---\ntype: paper\n---\n# Nested Retrieval Note\n\nprose.\n", encoding="utf-8"
    )

    result = ingest_sources(wiki, [str(top)], exact=True)

    assert result["node_count"] > 0


# --- import cost -----------------------------------------------------------


def test_importing_the_orchestrator_does_not_drag_in_tesserae_project():
    """tesserae/ingest/__init__.py resolves ingest_sources through a lazy
    __getattr__ specifically so importing the fetch helpers stays cheap. A
    module-scope `from tesserae.project import ...` in the orchestrator defeats
    that: it pulls in the whole compile stack for a constant."""
    import subprocess
    import sys

    probe = (
        "import sys, tesserae.ingest.orchestrator as o;"
        "print('project' if 'tesserae.project' in sys.modules else 'clean')"
    )
    out = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )

    assert out.stdout.strip() == "clean", out.stdout
