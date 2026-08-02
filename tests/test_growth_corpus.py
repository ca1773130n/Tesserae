"""The growth eval's corpus listing: what gets sliced, and where its dates come from.

`corpus_docs()` derives a date for every document kind that does not carry one,
so slicing covers the whole corpus rather than only papers. Derivation is the
part worth pinning: a rule that silently stops matching (a renamed front-matter
key, an arxiv id written a new way) would drop documents from the curve without
failing anything, and the curve would just look smaller.

These run against the real corpus and need no compile.
"""

from __future__ import annotations

from datetime import date

from evals.growth.run import CORPUS, corpus_docs, paper_dates

DOCS = corpus_docs()
BY_KIND = {}
for _iso, _path, _arxiv, _kind in DOCS:
    BY_KIND.setdefault(_kind, []).append((_iso, _path))


def test_every_kind_in_the_corpus_is_represented():
    """A kind silently dropping to zero is the failure this file exists to catch."""
    on_disk = {d.name for d in CORPUS.iterdir() if d.is_dir()}
    assert on_disk == {"papers", "repos", "daily", "weekly", "questions"}
    assert set(BY_KIND) == {"paper", "repo", "daily", "weekly", "question"}


def test_nothing_datable_is_skipped():
    """Counts, so a derivation that quietly matches fewer files fails here."""
    assert len(BY_KIND["paper"]) == len(paper_dates()) == 50
    assert len(BY_KIND["repo"]) == 12
    assert len(BY_KIND["daily"]) == 6
    assert len(BY_KIND["weekly"]) == 2
    assert len(BY_KIND["question"]) == 3


def test_dates_are_sorted_and_well_formed():
    isos = [iso for iso, _, _, _ in DOCS]
    assert isos == sorted(isos)
    for iso in isos:
        date.fromisoformat(iso)          # raises if a derivation emitted junk


def test_a_repo_is_dated_by_its_canonical_paper():
    """Not by mtime, not by a written-in date — by the paper it implements."""
    papers = {d.name: iso for iso, d, _ in paper_dates()}
    nerf = next(iso for iso, p in BY_KIND["repo"] if p.name == "bmild-nerf")
    assert nerf == papers["arxiv-2003-08934"]


def test_a_weekly_is_dated_to_the_monday_of_its_iso_week():
    w17 = next(iso for iso, p in BY_KIND["weekly"] if p.name == "2026-W17")
    assert w17 == date.fromisocalendar(2026, 17, 1).isoformat()


def test_a_question_is_dated_by_the_last_paper_it_surfaced_in():
    """The earliest point the question could have been asked, not earlier."""
    papers = {d.name: iso for iso, d, _ in paper_dates()}
    iso, path = next((i, p) for i, p in BY_KIND["question"]
                     if p.name == "multi-view-consistency.md")
    listed = [papers[n] for n in papers if n in path.read_text(encoding="utf-8")]
    assert listed and iso == max(listed)


def test_no_document_is_staged_before_a_paper_it_names():
    """The floor that matters. A document naming a paper the slice has not staged
    makes the extractor mint that paper's content from the staged document — the
    missing paper arrives early, grounded, through the side door. Only id-form
    references are detectable; prose name-drops still leak, which is what
    `connected_early` is for."""
    papers = {d.name: iso for iso, d, _ in paper_dates()}
    early = []
    for iso, path, _arxiv, kind in DOCS:
        files = sorted(path.glob("*.md")) if path.is_dir() else [path]
        text = "\n".join(f.read_text(encoding="utf-8") for f in files)
        for name, pdate in papers.items():
            if name in text and pdate > iso:
                early.append(f"{kind}:{path.name} ({iso}) names {name} ({pdate})")
    assert not early, "documents staged before a paper they reference: " + "; ".join(early)


def test_only_papers_carry_an_arxiv_id():
    """`requires:` in questions.yaml is checked against staged arxiv ids, so a
    non-paper leaking one would let a question look sourced when it is not."""
    assert all(bool(ax) == (kind == "paper") for _, _, ax, kind in DOCS)


def test_repos_and_questions_interleave_with_the_papers():
    """The point of the change: if every added kind sorted to the end, slicing
    would gain mass and no new steps. Digests and syntheses do land at the end
    (they are 2026-dated); repos and questions must not."""
    last_paper = max(iso for iso, _ in BY_KIND["paper"])
    assert sum(iso < last_paper for iso, _ in BY_KIND["repo"]) >= 10
    assert sum(iso < last_paper for iso, _ in BY_KIND["question"]) >= 2
