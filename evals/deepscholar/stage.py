"""Put one query's corpus on disk, compile it, and prove it is that query's.

Modelled on :mod:`evals.locomo.adapter`, and reusing the two pieces that are
facts about how THIS repository stages a corpus rather than facts about any one
benchmark: :func:`~evals.lme_mab.adapter.guard_work_dir` and
:class:`~evals.lme_mab.adapter.RefusedToCompileInRepo`.

Three decisions here would each produce a plausible wrong number if made
differently:

**One project per QUERY.** ``<work>/<file-id>/`` gets its own corpus and its own
``.tesserae/graph.json``. DeepScholar's ``paper.csv`` is per-file-id, so a
sentence citing a paper outside this query's reference set resolves to ``{}`` in
the parser's ``reference_map`` and is scored against an empty abstract — a zero
with no error. Compiling all 63 parents into one shared corpus to save
extraction calls would put every other parent's papers within reach of the
writer, and the cost of that leak is paid silently.

**``paper.csv`` comes from the dataset, never from the graph.** It is the
answer key half of the contract: the parser resolves the writer's links against
it. Deriving it from what the graph happens to hold would let an extraction gap
quietly shrink the set of ids that can resolve, and the arm would score itself
against its own omissions.

**The staged bytes are a pure function of the dataset row.** No clock, no
counter, no ordering that depends on how the run was invoked, so re-staging is
byte-identical and :func:`verify_staged` can be an equality check rather than a
heuristic.
"""

from __future__ import annotations

import csv
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from ..lme_mab.adapter import REPO, RefusedToCompileInRepo, guard_work_dir
from .dataset import CitedPaper, Query

__all__ = [
    "REPO",
    "RefusedToCompileInRepo",
    "StagedQuery",
    "default_compile",
    "document_dir_name",
    "guard_work_dir",
    "paper_document",
    "stage_query",
    "verify_staged",
    "write_paper_csv",
]

#: The filename inside each paper's directory. One document per paper keeps
#: ``Paper.source_path`` pointing at exactly one abstract, which is what makes
#: the ``_source_text`` fallback in :mod:`evals.deepscholar.evidence` safe.
DOCUMENT_NAME = "paper.md"


def document_dir_name(arxiv_bare: str) -> str:
    """``2004.07667`` -> ``arxiv-2004-07667``.

    Dots are replaced because the directory name becomes part of the node id
    slug; keeping them produced ids that read as file extensions.
    """
    return "arxiv-" + arxiv_bare.replace(".", "-")


def _yaml_quote(value: str) -> str:
    """Double-quoted YAML scalar. Titles carry colons, quotes and backslashes."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def paper_document(paper: CitedPaper) -> str:
    """The markdown one cited paper stages to.

    Frontmatter carries ``arxiv`` (bare) so ``extract_source_metadata`` mints
    the Paper with ``metadata["arxiv_id"]`` and a real title rather than the
    ``arXiv:NNNN.NNNNN`` placeholder, and ``arxiv_versioned`` because nothing
    downstream in Tesserae preserves the version suffix and the version suffix
    is what every citation URL must carry. The body repeats the title as a
    ``Title:`` line for ``resolve_missing_paper_title``'s offline path.
    """
    lines = [
        "---",
        "type: Paper",
        f"arxiv: {paper.arxiv_bare}",
        f"arxiv_versioned: {paper.arxiv_versioned}",
        f"title: {_yaml_quote(paper.title)}",
        "---",
        "",
        f"# {paper.title}",
        "",
        f"Title: {paper.title}",
        "",
        "## Abstract",
        "",
        paper.abstract,
        "",
    ]
    return "\n".join(lines)


def abstract_of(document: str) -> str:
    """The abstract back out of :func:`paper_document`.

    The inverse of the writer above, used by the evidence fallback when a paper
    produced no claim. Kept beside its forward direction so the two cannot
    drift into disagreeing about where the abstract begins.
    """
    marker = "\n## Abstract\n"
    index = document.find(marker)
    if index < 0:
        return ""
    return document[index + len(marker):].strip()


@dataclass(frozen=True)
class StagedQuery:
    """Where one query's corpus and answer key landed, and what it cost."""

    file_id: str
    work: Path
    corpus_dir: Path
    paper_csv: Path
    papers: int
    chars: int


def write_paper_csv(query: Query, path: Path) -> Path:
    """The parser's ``id,title,snippet`` table, straight from the dataset.

    ``id`` is :attr:`CitedPaper.arxiv_versioned` — the same string the writer
    puts inside ``http://arxiv.org/abs/...`` — so a resolvable link and a
    resolvable csv row are the same fact rather than two facts that agree by
    habit.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["id", "title", "snippet"])
        writer.writeheader()
        for paper in query.corpus:
            writer.writerow(
                {
                    "id": paper.arxiv_versioned,
                    "title": paper.title,
                    "snippet": paper.abstract,
                }
            )
    return path


def stage_query(query: Query, work: Path) -> StagedQuery:
    """Write ``<work>/<file-id>/corpus/papers/...`` and the query's ``paper.csv``.

    ``paper.csv`` is written once, beside the corpus. The runner copies that one
    file into each arm's output folder rather than re-deriving it per arm, so
    two arms cannot end up scored against two answer keys.
    """
    root = guard_work_dir(work) / query.file_id
    corpus = root / "corpus"
    if corpus.exists():
        _clear(corpus)
    chars = 0
    for paper in query.corpus:
        directory = corpus / "papers" / document_dir_name(paper.arxiv_bare)
        directory.mkdir(parents=True, exist_ok=True)
        body = paper_document(paper)
        (directory / DOCUMENT_NAME).write_text(body, encoding="utf-8")
        chars += len(body)
    target = write_paper_csv(query, root / "paper.csv")
    return StagedQuery(
        file_id=query.file_id,
        work=root,
        corpus_dir=corpus,
        paper_csv=target,
        papers=len(query.corpus),
        chars=chars,
    )


def _clear(corpus: Path) -> None:
    shutil.rmtree(corpus)


def staged_documents(corpus: Path) -> List[Path]:
    return sorted(corpus.glob(f"papers/*/{DOCUMENT_NAME}"))


def verify_staged(query: Query, corpus: Path) -> Tuple[int, int]:
    """``(papers, chars)``, having proved this query is already staged there.

    Ported from :func:`evals.locomo.adapter._verify_staged` and for the same
    reason. A CHANGED document means the compiled graph was built from text
    this run would not stage; an EXTRA one means the graph indexes a paper this
    query does not cite — retrievable evidence the writer could cite, which
    ``paper.csv`` would then fail to resolve, scoring 0 with no error. Either
    way the reused graph is not this query's graph.
    """
    corpus = Path(corpus)
    if not corpus.is_dir():
        raise FileNotFoundError(
            f"--reuse-compile: no staged corpus at {corpus}. There is nothing "
            f"to reuse; run without the flag to stage and compile."
        )
    mismatched: List[str] = []
    expected = set()
    chars = 0
    for paper in query.corpus:
        name = document_dir_name(paper.arxiv_bare)
        expected.add(name)
        body = paper_document(paper)
        chars += len(body)
        staged = corpus / "papers" / name / DOCUMENT_NAME
        if not staged.is_file():
            mismatched.append(f"{name} (missing)")
        elif staged.read_bytes() != body.encode("utf-8"):
            mismatched.append(f"{name} (differs)")
    extra = sorted(
        path.parent.name
        for path in staged_documents(corpus)
        if path.parent.name not in expected
    )
    if mismatched or extra:
        raise ValueError(
            f"--reuse-compile: {corpus} is not query {query.file_id}'s corpus — "
            f"{len(mismatched)} document(s) missing or changed"
            f"{': ' + ', '.join(mismatched[:5]) if mismatched else ''}"
            f"{f'; {len(extra)} unexpected: ' + ', '.join(extra[:5]) if extra else ''}. "
            f"The compiled graph there answers about a different paper. "
            f"Re-run without --reuse-compile to rebuild it."
        )
    return len(query.corpus), chars


def graph_path(staged_work: Path) -> Path:
    return Path(staged_work) / ".tesserae" / "graph.json"


def default_compile(work: Path, *, extractor: str = "deterministic") -> None:
    """``tesserae init`` then ``tesserae compile``, in ``work``.

    ``--extractor deterministic`` is passed EXPLICITLY and is not a default
    being restated: ``tesserae compile`` defaults to ``llm``
    (``tesserae/cli.py:6252``), and on a graph built that way this arm's two
    anchors are gone — ``EvidenceSpan --part_of--> Paper`` was emitted 0 times
    out of 12,307 on the compiled graph measured for this phase, and
    ``title_quality`` was absent on every Paper node. It also costs one
    extraction call per abstract, which the control does not pay and so cannot
    be charged for.

    The checkout's own venv when there is one, else the running interpreter —
    the resolution :mod:`evals.locomo.adapter` settled on after hardcoding the
    first form killed every run inside a git worktree.
    """
    venv = REPO / ".venv" / "bin" / "python"
    python = str(venv if venv.is_file() else sys.executable)
    subprocess.run(
        [python, "-m", "tesserae", "init", "--yes", "--source", "./corpus"],
        cwd=work, check=True, capture_output=True,
    )
    result = subprocess.run(
        [python, "-m", "tesserae", "compile", "--extractor", extractor],
        cwd=work, capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"compile failed in {work}:\n{result.stdout[-2000:]}\n{result.stderr[-2000:]}"
        )


def load_graph(path: Path):
    """The compiled ``ResearchGraph`` at ``path``.

    Imported inside the function: :mod:`tesserae.research_graph` is a large
    module and ``--stage-only`` has no business paying for it.
    """
    from tesserae.research_graph import graph_from_payload

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return graph_from_payload(payload)


def write_intro(text: str, output_dir: Path, file_id: str) -> Path:
    """``<output_dir>/<file-id>/intro.md``, the generated text verbatim.

    Verbatim is the point. ``compile_context(synthesize=True)`` PREPENDS its
    prose and keeps the extractive bundle after a ``---``
    (``context_compiler.py:1245``); DeepScholar parses the whole file, so
    writing that concatenation feeds hundreds of uncited heading-and-excerpt
    sentences to ``cite_p``. Nothing but the model's answer goes here.
    """
    directory = Path(output_dir) / file_id
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "intro.md"
    path.write_text(text, encoding="utf-8")
    return path
