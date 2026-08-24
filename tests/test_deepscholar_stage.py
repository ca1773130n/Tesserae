"""DeepScholar staging: the arXiv id contract, isolation, and the reuse gate.

Offline and synthetic — no dataset clone, no compile, no model, no network. The
tables are written inline so the tests pin OUR reading of the schema rather than
one checkout's data.

What is pinned here is the handful of decisions that would otherwise produce a
plausible wrong number rather than an error: whether the id in ``paper.csv`` and
the id inside a citation URL are the same string, whether two runs stage the
same bytes, and whether a reused graph is proved to belong to the query being
scored.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

import pytest

from evals.deepscholar.dataset import CitedPaper, load_queries, split_arxiv_id
from evals.deepscholar.stage import (
    RefusedToCompileInRepo,
    abstract_of,
    document_dir_name,
    guard_work_dir,
    paper_document,
    stage_query,
    verify_staged,
    write_intro,
    write_paper_csv,
)

PAPERS_HEADER = ["arxiv_id", "title", "abstract", "published_date"]
CITATIONS_HEADER = [
    "parent_paper_arxiv_id",
    "cited_paper_title",
    "cited_paper_arxiv_link",
    "cited_paper_abstract",
]


def _write_csv(path: Path, header, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=header)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


@pytest.fixture()
def dataset(tmp_path: Path) -> Path:
    """A two-parent dataset with every awkward case the real tables contain."""
    directory = tmp_path / "dataset"
    _write_csv(
        directory / "papers_with_related_works.csv",
        PAPERS_HEADER,
        [
            {
                "arxiv_id": "2506.02838v1",
                "title": "TaxAgent: How Large Language Models Design\n  Fiscal Policy",
                "abstract": "We study fiscal policy design with language agents.",
                "published_date": "2025-06-03",
            },
            {
                "arxiv_id": "2504.11007v1",
                "title": "A Parent With Nothing Citable",
                "abstract": "Nothing here cites arXiv.",
                "published_date": "2025-04-15",
            },
        ],
    )
    _write_csv(
        directory / "citations.csv",
        CITATIONS_HEADER,
        [
            {
                "parent_paper_arxiv_id": "2506.02838v1",
                "cited_paper_title": "The AI Economist: Improving Equality and\n  Productivity",
                "cited_paper_arxiv_link": "http://arxiv.org/abs/2004.13332v1",
                "cited_paper_abstract": "We present a simulation. We propose a method.",
            },
            {   # same bare id, later version — must not stage twice
                "parent_paper_arxiv_id": "2506.02838v1",
                "cited_paper_title": "The AI Economist (v2)",
                "cited_paper_arxiv_link": "http://arxiv.org/abs/2004.13332v2",
                "cited_paper_abstract": "A second version of the same paper.",
            },
            {   # no abstract — no entailment premise, so it cannot be scored
                "parent_paper_arxiv_id": "2506.02838v1",
                "cited_paper_title": "Abstract Missing",
                "cited_paper_arxiv_link": "http://arxiv.org/abs/2101.00001v1",
                "cited_paper_abstract": "",
            },
            {   # not an arXiv link — the benchmark's own regex cannot key on it
                "parent_paper_arxiv_id": "2506.02838v1",
                "cited_paper_title": "A Journal Paper",
                "cited_paper_arxiv_link": "https://doi.org/10.1000/xyz",
                "cited_paper_abstract": "Published elsewhere.",
            },
            {
                "parent_paper_arxiv_id": "2506.02838v1",
                "cited_paper_title": "CompeteAI",
                "cited_paper_arxiv_link": "http://arxiv.org/abs/2310.17512v2",
                "cited_paper_abstract": "We study competition among agents.",
            },
        ],
    )
    return directory


# ----------------------------------------------------------------- the id


def test_versioned_id_survives_and_bare_id_is_separate():
    assert split_arxiv_id("http://arxiv.org/abs/2108.02755v1") == (
        "2108.02755v1",
        "2108.02755",
    )
    assert split_arxiv_id("http://arxiv.org/abs/2108.02755") == (
        "2108.02755",
        "2108.02755",
    )
    assert split_arxiv_id("https://doi.org/10.1000/xyz") is None


def test_corpus_admits_only_rows_a_citation_could_score(dataset: Path):
    query = load_queries(dataset, file_ids=[0])[0]
    ids = [paper.arxiv_versioned for paper in query.corpus]
    # v2 of an already-seen bare id, the abstract-less row and the DOI row are
    # all dropped; staging the v2 would have overwritten the v1's document
    # while both ids stayed resolvable in paper.csv.
    assert ids == ["2004.13332v1", "2310.17512v2"]


def test_latex_line_wraps_are_flattened_out_of_titles(dataset: Path):
    query = load_queries(dataset, file_ids=[0])[0]
    assert "\n" not in query.parent.title
    assert query.corpus[0].title == (
        "The AI Economist: Improving Equality and Productivity"
    )


def test_a_parent_with_no_citable_reference_yields_an_empty_corpus(dataset: Path):
    assert load_queries(dataset, file_ids=[1])[0].corpus == ()


def test_file_id_is_the_row_index_not_the_arxiv_id(dataset: Path):
    queries = load_queries(dataset)
    assert [q.file_id for q in queries] == ["0", "1"]


def test_out_of_range_file_id_names_the_range(dataset: Path):
    with pytest.raises(IndexError, match="has 2 rows"):
        load_queries(dataset, file_ids=[7])


# -------------------------------------------------- paper.csv <-> the link


def _deepscholar_resolve(intro: str, paper_csv: Path):
    """A transcription of ``eval/parsers/deepscholar_base.py``'s own parsing.

    Their repo is a sibling clone, not an importable dependency, so the parser
    that decides whether our output can be scored at all is restated here
    line for line: the inline-http-only citation regex, the id extractor, and
    the ``DictReader`` keyed on the raw ``id`` column. Returns their ``docs``.

    This is the single highest-value assertion in this file. A link written
    without its version suffix against a ``paper.csv`` row that has one
    resolves to ``{}`` — empty title, empty abstract — and the entailment judge
    then scores that sentence 0 with no error anywhere.
    """
    reference_map = {}
    with paper_csv.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            reference_map[row["id"].strip()] = {
                "title": row["title"].strip(),
                "abstract": row["snippet"].strip(),
            }
    docs = []
    seen = {}
    for _visible, url in re.compile(r"\[([^\]]+?)\]\((https?://[^\)]+)\)").findall(intro):
        match = re.search(r"arxiv\.org/abs/([^)\s]+)", url)
        if match is None:
            continue
        arxiv_id = match.group(1)
        if arxiv_id in seen:
            continue
        seen[arxiv_id] = len(seen) + 1
        info = reference_map.get(arxiv_id, {})
        docs.append({"title": info.get("title", ""), "sent": info.get("abstract", "")})
    return docs


def test_a_citation_built_from_cited_paper_url_resolves_in_paper_csv(
    dataset: Path, tmp_path: Path
):
    query = load_queries(dataset, file_ids=[0])[0]
    csv_path = write_paper_csv(query, tmp_path / "paper.csv")
    intro = " ".join(
        f"Prior work does something [{p.title}]({p.url})." for p in query.corpus
    )
    docs = _deepscholar_resolve(intro, csv_path)
    assert len(docs) == len(query.corpus)
    assert all(doc["title"] and doc["sent"] for doc in docs), docs


def test_dropping_the_version_suffix_resolves_to_an_empty_doc(
    dataset: Path, tmp_path: Path
):
    """The failure this harness exists to make impossible, demonstrated."""
    query = load_queries(dataset, file_ids=[0])[0]
    csv_path = write_paper_csv(query, tmp_path / "paper.csv")
    paper = query.corpus[0]
    intro = f"A claim [{paper.title}](http://arxiv.org/abs/{paper.arxiv_bare})."
    docs = _deepscholar_resolve(intro, csv_path)
    assert docs == [{"title": "", "sent": ""}]


def test_paper_csv_is_the_whole_corpus_not_what_was_cited(dataset: Path, tmp_path: Path):
    query = load_queries(dataset, file_ids=[0])[0]
    csv_path = write_paper_csv(query, tmp_path / "paper.csv")
    with csv_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert [r["id"] for r in rows] == [p.arxiv_versioned for p in query.corpus]
    assert list(rows[0]) == ["id", "title", "snippet"]


# ------------------------------------------------------------- the corpus


def test_paper_document_round_trips_its_abstract():
    paper = CitedPaper("1234.5678v1", "1234.5678", 'A "quoted" title: with colon',
                       "One sentence. Two sentences.")
    document = paper_document(paper)
    assert abstract_of(document) == "One sentence. Two sentences."
    assert "arxiv_versioned: 1234.5678v1" in document
    assert 'title: "A \\"quoted\\" title: with colon"' in document


def test_document_dir_name_has_no_dots():
    assert document_dir_name("2004.13332") == "arxiv-2004-13332"


def test_staging_twice_is_byte_identical(dataset: Path, tmp_path: Path):
    query = load_queries(dataset, file_ids=[0])[0]
    work = tmp_path / "work"
    first = stage_query(query, work)
    bytes_first = {
        p.relative_to(first.corpus_dir): p.read_bytes()
        for p in sorted(first.corpus_dir.rglob("*.md"))
    }
    second = stage_query(query, work)
    bytes_second = {
        p.relative_to(second.corpus_dir): p.read_bytes()
        for p in sorted(second.corpus_dir.rglob("*.md"))
    }
    assert bytes_first == bytes_second
    assert first.papers == 2


def test_each_query_stages_into_its_own_directory(dataset: Path, tmp_path: Path):
    work = tmp_path / "work"
    staged = [stage_query(q, work) for q in load_queries(dataset)]
    assert staged[0].work != staged[1].work
    assert staged[0].work.name == "0" and staged[1].work.name == "1"


def test_verify_staged_accepts_what_stage_query_wrote(dataset: Path, tmp_path: Path):
    query = load_queries(dataset, file_ids=[0])[0]
    staged = stage_query(query, tmp_path / "work")
    assert verify_staged(query, staged.corpus_dir) == (2, staged.chars)


def test_verify_staged_rejects_a_changed_document(dataset: Path, tmp_path: Path):
    query = load_queries(dataset, file_ids=[0])[0]
    staged = stage_query(query, tmp_path / "work")
    victim = next(staged.corpus_dir.rglob("paper.md"))
    victim.write_text(victim.read_text(encoding="utf-8") + "\nedited\n", encoding="utf-8")
    with pytest.raises(ValueError, match="differs"):
        verify_staged(query, staged.corpus_dir)


def test_verify_staged_rejects_a_paper_this_query_does_not_cite(
    dataset: Path, tmp_path: Path
):
    """A leaked paper is retrievable evidence ``paper.csv`` cannot resolve."""
    query = load_queries(dataset, file_ids=[0])[0]
    staged = stage_query(query, tmp_path / "work")
    intruder = staged.corpus_dir / "papers" / "arxiv-9999-99999"
    intruder.mkdir(parents=True)
    (intruder / "paper.md").write_text("leaked", encoding="utf-8")
    with pytest.raises(ValueError, match="unexpected"):
        verify_staged(query, staged.corpus_dir)


def test_verify_staged_on_a_missing_corpus_says_what_to_do(dataset: Path, tmp_path: Path):
    query = load_queries(dataset, file_ids=[0])[0]
    with pytest.raises(FileNotFoundError, match="without the flag"):
        verify_staged(query, tmp_path / "nowhere")


# ------------------------------------------------------------- the guards


def test_compiling_inside_the_repo_is_refused(dataset: Path):
    query = load_queries(dataset, file_ids=[0])[0]
    with pytest.raises(RefusedToCompileInRepo):
        stage_query(query, Path(__file__).resolve().parent.parent)


def test_guard_work_dir_refuses_any_other_checkout(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    with pytest.raises(RefusedToCompileInRepo):
        guard_work_dir(tmp_path)


def test_write_intro_writes_the_text_and_nothing_else(tmp_path: Path):
    path = write_intro("Only this.", tmp_path / "out", "3")
    assert path == tmp_path / "out" / "3" / "intro.md"
    assert path.read_text(encoding="utf-8") == "Only this."
