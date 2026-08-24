"""DeepScholar-Bench's tables, read as queries.

One query is one row of ``dataset/papers_with_related_works.csv`` — 63 of them,
addressed by ROW INDEX, because that is what ``--file-id`` means upstream
(``eval/parsers/parser.py:47`` does ``dataset.iloc[int(self.file_id)]``). A
query carries the parent paper the system is writing a Related Works section
for, and the corpus of papers that parent actually cited.

The one thing in this module that is easy to get silently wrong is the arXiv
id, so it is handled in exactly one place. DeepScholar's ``paper.csv`` is keyed
on the string that follows ``arxiv.org/abs/`` in the generated prose
(``eval/parsers/deepscholar_base.py:36-40``), and the dataset's
``cited_paper_arxiv_link`` carries a VERSION SUFFIX — ``2108.02755v1``. A link
written as ``.../abs/2108.02755`` against a ``paper.csv`` row ``2108.02755v1``
resolves to ``{}``: empty title, empty abstract, and an entailment call run
against nothing, which scores 0 without raising. So :class:`CitedPaper` keeps
both spellings, ``arxiv_versioned`` is the only one that ever reaches a URL or
a csv row, and :func:`write_paper_csv` and the writer's citation contract are
built from the same field.
"""

from __future__ import annotations

import csv
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

#: ``eval/parsers/deepscholar_base.py:36`` verbatim. Anything this does not
#: match is not a citation as far as the scorer is concerned.
ARXIV_ABS_RE = re.compile(r"arxiv\.org/abs/([^)\s]+)")

#: The bare id inside a versioned one. Deliberately NOT used to build links —
#: see the module docstring.
ARXIV_BARE_RE = re.compile(r"(\d{4}\.\d{4,6})")

#: ``data_pipeline/generate_queries.py:16``, character for character. Restated
#: rather than imported: their repo is a sibling clone, not a dependency, and a
#: harness that cannot state the task it was given cannot be audited.
QUERY_TEMPLATE = (
    "Your task is to write a Related Works section for an academic paper given "
    "the paper's abstract. Your response should provide the Related Works "
    "section and references. Only include references from arXiv that are "
    "published before {cutoff_date}. Mention them in a separate, numbered "
    "reference list at the end and use the reference numbers to provide in-line "
    "citations in the Related Works section for all claims referring to a "
    "source (e.g., description of source [3]. Further details [6][7][8][9][10].) "
    "Each in-line citation must consist of a single reference number within a "
    "pair of brackets. Do not use any other citation format. Do not exceed 600 "
    "words for the related works section. Here is the paper abstract:\n"
    "{abstract}"
)

#: The published word cap, from the template above.
WORD_CAP = 600

#: ``dataset/citations.csv`` is every reference; ``dataset/important_citations.csv``
#: is the subset ``reference_coverage`` scores against. Which one a run uses is
#: load-bearing and must be named in the report, so it is a parameter with no
#: default that reads as neutral.
CORPUS_ALL = "citations"
CORPUS_IMPORTANT = "important_citations"
CORPUS_CHOICES = (CORPUS_ALL, CORPUS_IMPORTANT)


def _widen_csv_limit() -> None:
    """``clean_latex_related_works`` runs to 7,960 chars; ``raw_`` runs longer.

    ``csv``'s default 131,072-char field limit is not obviously above every
    cell in these tables, and the failure mode is an exception mid-file rather
    than a wrong number — but a benchmark harness that dies on row 40 has still
    wasted the run. Raised once, at the largest value the platform accepts.
    """
    limit = sys.maxsize
    while True:
        try:
            csv.field_size_limit(limit)
            return
        except OverflowError:
            limit //= 2


@dataclass(frozen=True)
class CitedPaper:
    """One paper in a query's corpus, in the two spellings that matter."""

    #: The exact string after ``arxiv.org/abs/`` in the dataset link, version
    #: suffix included. This is the ``id`` column of ``paper.csv`` AND the id
    #: inside every citation URL the writer emits. One string, two uses, so
    #: they cannot drift.
    arxiv_versioned: str
    #: ``2108.02755``. Used ONLY to name the staged document directory, because
    #: :func:`tesserae.research_graph.extract_source_metadata` normalises to it
    #: and a Paper node's ``metadata["arxiv_id"]`` carries this form.
    arxiv_bare: str
    title: str
    abstract: str

    @property
    def url(self) -> str:
        return f"http://arxiv.org/abs/{self.arxiv_versioned}"


@dataclass(frozen=True)
class ParentPaper:
    """The paper whose Related Works section is being written."""

    row_index: int
    arxiv_id: str
    title: str
    abstract: str
    published_date: str


@dataclass(frozen=True)
class Query:
    """One benchmark query: a parent paper and the corpus it cited."""

    parent: ParentPaper
    corpus: Tuple[CitedPaper, ...]

    @property
    def file_id(self) -> str:
        """The ``--file-id`` this query is scored under upstream."""
        return str(self.parent.row_index)

    @property
    def prompt(self) -> str:
        return QUERY_TEMPLATE.format(
            cutoff_date=self.parent.published_date, abstract=self.parent.abstract
        )

    def by_versioned_id(self) -> Dict[str, CitedPaper]:
        return {paper.arxiv_versioned: paper for paper in self.corpus}


def split_arxiv_id(link: str) -> Optional[Tuple[str, str]]:
    """``(versioned, bare)`` from a ``cited_paper_arxiv_link``, or ``None``.

    ``None`` for a link this benchmark's own parser could not key on either —
    if ``ARXIV_ABS_RE`` does not match, no citation to it could ever resolve,
    so admitting the paper to the corpus would only manufacture a sentence that
    scores 0.
    """
    match = ARXIV_ABS_RE.search(link or "")
    if not match:
        return None
    versioned = match.group(1).strip().rstrip("/")
    bare = ARXIV_BARE_RE.match(versioned)
    if not bare:
        return None
    return versioned, bare.group(1)


def _flatten(text: str) -> str:
    """Collapse internal whitespace to single spaces.

    Titles in these tables are lifted out of LaTeX and carry its line wraps —
    ``"The AI Economist: Improving Equality and Productivity with AI-Driven Tax\n
    Policies"``. Left in, a raw newline inside a double-quoted YAML scalar makes
    the staged frontmatter title parse as its first line only (measured: the
    Paper node came back named ``... AI-Driven Tax`` with ``Policies`` lost),
    and the same newline breaks the one-line-per-field layout of the evidence
    table. Applied at load so every consumer — frontmatter, ``paper.csv``, the
    evidence table, the citation link text — sees one spelling.
    """
    return " ".join((text or "").split())


def _read_rows(path: Path) -> List[Dict[str, str]]:
    _widen_csv_limit()
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def corpus_for_parent(
    rows: Iterable[Mapping[str, str]], parent_arxiv_id: str
) -> Tuple[CitedPaper, ...]:
    """The cited papers of one parent that a citation to could actually score.

    Admitted only when the row has BOTH an arXiv link this benchmark's regex
    matches and a non-empty abstract: the abstract is the entailment premise,
    and a paper with no premise is a paper every citation to which scores 0
    whatever the writer says about it.

    De-duplicated on the BARE id, first occurrence winning, because two rows
    citing ``2108.02755v1`` and ``2108.02755v2`` would otherwise stage twice
    into the same document directory — the second silently overwriting the
    first while both ids stayed in ``paper.csv``, one of them then pointing at
    a document that is not it.
    """
    seen: Dict[str, CitedPaper] = {}
    for row in rows:
        if (row.get("parent_paper_arxiv_id") or "").strip() != parent_arxiv_id:
            continue
        ids = split_arxiv_id((row.get("cited_paper_arxiv_link") or "").strip())
        if ids is None:
            continue
        versioned, bare = ids
        abstract = _flatten(row.get("cited_paper_abstract") or "")
        title = _flatten(row.get("cited_paper_title") or "")
        if not abstract or not title or bare in seen:
            continue
        seen[bare] = CitedPaper(
            arxiv_versioned=versioned,
            arxiv_bare=bare,
            title=title,
            abstract=abstract,
        )
    return tuple(seen.values())


def load_queries(
    dataset_dir: Path,
    *,
    file_ids: Optional[Sequence[int]] = None,
    corpus: str = CORPUS_ALL,
) -> List[Query]:
    """Queries for ``file_ids`` (all 63 rows when ``None``), in the given order.

    ``corpus`` picks the citation table. ``CORPUS_IMPORTANT`` is the answer key
    ``reference_coverage`` scores against, so handing it to an arm as the
    retrievable corpus makes that metric a tautology; ``CORPUS_ALL`` is the
    superset and is the honest default. Named either way in the report.
    """
    if corpus not in CORPUS_CHOICES:
        raise ValueError(
            f"unknown corpus {corpus!r}; expected one of {', '.join(CORPUS_CHOICES)}"
        )
    dataset_dir = Path(dataset_dir)
    papers_path = dataset_dir / "papers_with_related_works.csv"
    citations_path = dataset_dir / f"{corpus}.csv"
    for path in (papers_path, citations_path):
        if not path.is_file():
            raise FileNotFoundError(f"DeepScholar dataset table missing: {path}")

    papers = _read_rows(papers_path)
    citations = _read_rows(citations_path)
    wanted = list(range(len(papers))) if file_ids is None else list(file_ids)

    queries: List[Query] = []
    for index in wanted:
        if index < 0 or index >= len(papers):
            raise IndexError(
                f"--file-id {index} is out of range; "
                f"{papers_path.name} has {len(papers)} rows (0..{len(papers) - 1})"
            )
        row = papers[index]
        parent = ParentPaper(
            row_index=index,
            arxiv_id=(row.get("arxiv_id") or "").strip(),
            title=_flatten(row.get("title") or ""),
            abstract=_flatten(row.get("abstract") or ""),
            published_date=(row.get("published_date") or "").strip(),
        )
        queries.append(
            Query(parent=parent, corpus=corpus_for_parent(citations, parent.arxiv_id))
        )
    return queries
