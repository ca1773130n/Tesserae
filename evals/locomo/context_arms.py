"""The context-efficiency arms: same question, same budget, different context.

**BM25 is the retriever, not the rival.** Two of the three arms rank with it and
the comparison is about what is done with the ranking, so a recall@k table
between "BM25" and "Tesserae" is not a thing this module can produce and is not
the question it exists to ask. The axis is TOKENS-TO-CORRECT-ANSWER: every arm
is handed the same token budget, fits its own context into it with its own knob,
and the request it will send is built and counted BEFORE any model sees it.

    A  bm25_docs      BM25 ranks; whole session documents go in, best first,
                      until the budget is spent. The incumbent, and the
                      token-hungry one — this is what a RAG system does.
    B  bm25_compiled  BM25 finds the REGION; the ranked sessions become seeds
                      for ``tesserae.context_compiler.compile_context``, which
                      compiles background from the graph for that region.
    C  graph_only     No document retrieval at all. compile_context from the
                      question alone — can the graph orient by itself?

Two controls, because without them the ladder cannot be read:

    F  closed_book    No evidence. Reads the refusal free-lunch directly: on
                      LoCoMo the adversarial category's gold answer IS a
                      refusal, so an arm starved of context scores that whole
                      category for nothing.
    E  whole_corpus   Every staged session, unbudgeted. On a corpus that fits
                      in a context window this arm should win, and the
                      instrument declares that outcome a falsifier of the
                      CORPUS as evidence for the at-scale claim — not of the
                      claim. conv-26's 19 sessions are that corpus.

Every arm answers one interface — ``prompt(question, budget_tokens)`` — and
every arm is constructed per CONVERSATION, so no arm can compile context from a
conversation the question was not asked about.

Nothing here calls a model. The compiled arms call ``compile_context``, which is
a pure function over an already-compiled graph; measured warm on conv-26 at
about 4.7 ms per call (18 calls in 0.084 s), which is why the budget fitting can
afford to SCAN its knob rather than assume the knob is monotone.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ..qa.run_qa_eval import Skip
from .tokens import (
    SCHEMA_NAME,
    Prompt,
    count_tokens,
    fit_by_prefix,
    serialized_request,
    user_turn,
)

#: The requested CHARACTER budgets ``compile_context`` is scanned over. It takes
#: a char budget on the packed body and the assembled request is bigger than
#: that body — measured on conv-26: a requested 4,000 produced
#: ``char_budget_used`` 3,469 and a 7,689-character body — so the requested
#: number cannot be derived from a token rung and has to be searched for.
#:
#: A SCAN and not a binary search, because the knob is not monotone in the
#: quantity being fitted: measured on conv-26, requested budgets 4,000 and 8,000
#: selected 20 and 8 nodes respectively. A scan needs no monotonicity assumption
#: and costs 25 pure-function calls.
#:
#: Geometric at a ratio of about 1.25. The grid's SPACING is the arm's fitting
#: tolerance — a coarser grid leaves the compiled arms under-filling their rung
#: while the document arm fills it, which hands one arm less of the rationed
#: resource than the other at the same nominal budget. The realised tokens are
#: printed per rung so the residual under-fill is visible rather than assumed
#: away.
COMPILE_BUDGET_GRID: Tuple[int, ...] = (
    250, 320, 400, 500, 640, 800, 1_000, 1_250, 1_600, 2_000, 2_500, 3_200,
    4_000, 5_000, 6_400, 8_000, 10_000, 12_500, 16_000, 20_000, 25_000,
    32_000, 40_000, 50_000, 64_000,
)

#: How many BM25-ranked sessions arm B hands ``compile_context`` as seeds.
#: DECLARED, NOT TUNED — no run has been spent choosing it. It is a CLI flag so
#: a sweep is possible, and the report prints the value it ran with.
DEFAULT_REGION_K = 3

ARM_NAMES = ("bm25_docs", "bm25_compiled", "graph_only",
             "closed_book", "whole_corpus")


class ContextArm:
    """One way of turning a question and a budget into a request."""

    #: What the report and every persisted row call this arm.
    name = "arm"
    #: Prints in the controls table. Overridden per arm.
    description = ""

    def __init__(self, conversation: str, system: str) -> None:
        #: The conversation this arm may read. Isolation is per-instance and
        #: not per-call, so an arm cannot be pointed at another corpus by a
        #: caller that forgot which one it was iterating.
        self.conversation = conversation
        self.system = system

    def prompt(self, question: str, *, budget_tokens: Optional[int]) -> Prompt:
        """The request for ``question``, fitted to ``budget_tokens``.

        ``budget_tokens=None`` means unbudgeted, which only the controls use.
        """
        raise NotImplementedError

    @property
    def controls(self) -> Dict[str, Any]:
        return {"arm": self.name, "description": self.description,
                "conversation": self.conversation}


# --------------------------------------------------------------------------
# The two controls
# --------------------------------------------------------------------------


class ClosedBookArm(ContextArm):
    """No evidence at all. The unbudgeted FLOOR, and the free-lunch meter.

    Whatever this arm scores is available to every other arm for zero evidence
    tokens, so a headline that does not clear it is not a result about context.
    """

    name = "closed_book"
    description = "no evidence; the model answers from what it already knows"

    def prompt(self, question: str, *, budget_tokens: Optional[int]) -> Prompt:
        return Prompt(system=self.system, user=user_turn(question, []),
                      schema_name=SCHEMA_NAME, items=(),
                      fit={"budget_tokens": budget_tokens, "unbudgeted": True})


class WholeCorpusArm(ContextArm):
    """Every staged session, in session order, unbudgeted. The CEILING.

    Legal here only because conv-26's whole corpus fits in a context window,
    which is exactly what makes conv-26 unable to demonstrate the at-scale
    claim. Building the arm is how that limitation is reported rather than
    omitted.
    """

    name = "whole_corpus"
    description = "every staged session document, no budget"

    def __init__(self, conversation: str, system: str,
                 documents: Sequence[Tuple[int, str]]) -> None:
        super().__init__(conversation, system)
        self.documents = list(documents)

    def prompt(self, question: str, *, budget_tokens: Optional[int]) -> Prompt:
        items = [body for _, body in self.documents]
        return Prompt(system=self.system, user=user_turn(question, items),
                      schema_name=SCHEMA_NAME, items=items,
                      fit={"budget_tokens": budget_tokens, "unbudgeted": True,
                           "n_documents": len(items)})


# --------------------------------------------------------------------------
# A — BM25 + whole documents
# --------------------------------------------------------------------------


class Bm25DocumentsArm(ContextArm):
    """BM25 ranks; whole session documents fill the budget, best first.

    The knob is HOW MANY documents, and the arm turns it itself: documents are
    added while the whole request still fits, and a document that would overflow
    is skipped rather than half-included. Only when the top-ranked document
    alone overflows is it cut — by :func:`evals.locomo.tokens.fit_by_prefix`,
    on a token measurement rather than a character slice — and that row is
    flagged ``truncated``. A fixed-budget ladder measures truncation skill
    unless truncation is counted and printed, so it is both.

    ``ranker`` is anything with ``search_documents(question, k=...) -> [index]``
    — :class:`evals.lme_mab.baselines.LexicalArm` in the run, a stub in tests.
    """

    name = "bm25_docs"
    description = ("BM25 over the staged sessions; whole documents, best first, "
                   "until the budget is spent")

    def __init__(self, conversation: str, system: str, ranker: Any,
                 documents: Mapping[int, str]) -> None:
        super().__init__(conversation, system)
        self.ranker = ranker
        self.documents = dict(documents)

    def _ranked(self, question: str) -> List[int]:
        # k is the whole corpus: the BUDGET decides how much is used, and a
        # smaller k would be a second, undeclared budget hiding inside the
        # ranking. BM25 already drops documents it scored at zero.
        return list(self.ranker.search_documents(
            question, k=max(1, len(self.documents))))

    def prompt(self, question: str, *, budget_tokens: Optional[int]) -> Prompt:
        ranked = self._ranked(question)
        bodies = [self.documents[i] for i in ranked if i in self.documents]
        if budget_tokens is None:
            return Prompt(system=self.system,
                          user=user_turn(question, bodies),
                          schema_name=SCHEMA_NAME, items=bodies,
                          fit={"ranked": ranked, "unbudgeted": True})

        kept: List[str] = []
        skipped = 0
        for body in bodies:
            candidate = kept + [body]
            request = serialized_request(
                self.system, user_turn(question, candidate), SCHEMA_NAME)
            if count_tokens(request) <= budget_tokens:
                kept = candidate
            else:
                skipped += 1
        truncated = False
        if not kept and bodies:
            cut = fit_by_prefix(self.system, question, bodies[0],
                                budget_tokens=budget_tokens)
            if cut:
                kept = [cut]
                truncated = True
        return Prompt(
            system=self.system, user=user_turn(question, kept),
            schema_name=SCHEMA_NAME, items=kept, truncated=truncated,
            fit={"budget_tokens": budget_tokens,
                 "ranked": ranked,
                 "n_ranked": len(bodies),
                 "n_kept": len(kept),
                 "n_skipped_for_budget": skipped},
        )


# --------------------------------------------------------------------------
# B and C — compiled context
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class _Compiled:
    """One grid point: what was asked for, and what it actually cost."""

    requested: int
    body: str
    tokens: int
    n_nodes: int
    char_budget_used: int


class CompiledContextArm(ContextArm):
    """``compile_context`` fitted to a token budget by scanning its char knob.

    Shared by arms B and C; they differ only in whether BM25 supplies seeds.
    The scan evaluates every point of :data:`COMPILE_BUDGET_GRID` and keeps the
    one with the MOST realised tokens that still fits — the arm's own knob doing
    the fitting, which is the condition under which a fixed budget measures
    compilation rather than truncation.

    Every grid point that was evaluated is recorded on the row, so the fitting
    is auditable without re-running it.
    """

    name = "compiled"
    description = "compile_context, fitted to the budget by scanning its char knob"

    def __init__(self, conversation: str, system: str, graph: Any,
                 project_root: str, *, grid: Sequence[int] = COMPILE_BUDGET_GRID,
                 depth: int = 2) -> None:
        super().__init__(conversation, system)
        self.graph = graph
        self.project_root = project_root
        self.grid = tuple(grid)
        self.depth = depth

    def seeds_for(self, question: str) -> List[str]:
        """Node ids to seed the walk with. Arm C returns none."""
        return []

    def _compile(self, question: str, seeds: Sequence[str],
                 requested: int) -> _Compiled:
        from tesserae.context_compiler import compile_context

        bundle = compile_context(
            self.graph, project_root=self.project_root, query=question,
            seeds=list(seeds) or None, depth=self.depth, budget=requested)
        body = bundle.body or ""
        request = serialized_request(
            self.system, user_turn(question, [body] if body else []),
            SCHEMA_NAME)
        return _Compiled(requested=requested, body=body,
                         tokens=count_tokens(request),
                         n_nodes=len(bundle.selected_nodes),
                         char_budget_used=int(bundle.char_budget_used))

    def prompt(self, question: str, *, budget_tokens: Optional[int]) -> Prompt:
        seeds = self.seeds_for(question)
        if budget_tokens is None:
            compiled = self._compile(question, seeds, self.grid[-1])
            items = [compiled.body] if compiled.body else []
            return Prompt(system=self.system, user=user_turn(question, items),
                          schema_name=SCHEMA_NAME, items=items,
                          fit={"unbudgeted": True, "seeds": list(seeds),
                               "requested_chars": compiled.requested})

        scanned = [self._compile(question, seeds, requested)
                   for requested in self.grid]
        fitting = [c for c in scanned if c.tokens <= budget_tokens]
        truncated = False
        if fitting:
            best = max(fitting, key=lambda c: (c.tokens, c.requested))
            body = best.body
        else:
            # Even the smallest grid point overflows. Cut it, on a token
            # measurement, and SAY SO on the row.
            best = scanned[0]
            body = fit_by_prefix(self.system, question, best.body,
                                 budget_tokens=budget_tokens)
            truncated = bool(body) or bool(best.body)
        items = [body] if body else []
        return Prompt(
            system=self.system, user=user_turn(question, items),
            schema_name=SCHEMA_NAME, items=items, truncated=truncated,
            fit={"budget_tokens": budget_tokens,
                 "seeds": list(seeds),
                 "requested_chars": best.requested,
                 "compile_char_budget_used": best.char_budget_used,
                 "n_nodes": best.n_nodes,
                 "grid_tokens": {str(c.requested): c.tokens for c in scanned},
                 "n_grid_points_fitting": len(fitting)},
        )


class Bm25CompiledArm(CompiledContextArm):
    """B — BM25 finds the region, ``compile_context`` compiles it.

    The ranked sessions are resolved to their ``SourceDocument`` node ids and
    passed as seeds. Resolution FAILS LOUD: ``compile_context`` silently drops a
    seed id it does not recognise (measured — an unknown id came back with a
    full bundle and ten substituted seeds), so an off-by-one in the mapping
    would degrade this arm into arm C while every row still said ``seeds``.
    """

    name = "bm25_compiled"
    description = ("BM25 ranks the sessions; their SourceDocument nodes seed "
                   "compile_context, fitted to the budget")

    def __init__(self, conversation: str, system: str, graph: Any,
                 project_root: str, ranker: Any,
                 seed_nodes: Mapping[int, str], *,
                 region_k: int = DEFAULT_REGION_K,
                 grid: Sequence[int] = COMPILE_BUDGET_GRID,
                 depth: int = 2) -> None:
        super().__init__(conversation, system, graph, project_root,
                         grid=grid, depth=depth)
        self.ranker = ranker
        self.seed_nodes = dict(seed_nodes)
        self.region_k = int(region_k)
        known = {node.id for node in getattr(graph, "nodes", [])}
        unknown = sorted(v for v in self.seed_nodes.values() if v not in known)
        if known and unknown:
            raise Skip(
                f"{len(unknown)} session(s) map to node ids the graph does not "
                f"hold (e.g. {unknown[:2]})",
                "compile_context drops an unknown seed silently, which would "
                "turn this arm into graph_only while every row still claimed "
                "seeds; recompile the conversation or fix the mapping",
            )

    def seeds_for(self, question: str) -> List[str]:
        ranked = self.ranker.search_documents(question, k=self.region_k)
        return [self.seed_nodes[i] for i in ranked[:self.region_k]
                if i in self.seed_nodes]

    @property
    def controls(self) -> Dict[str, Any]:
        return {**super().controls, "region_k": self.region_k}


class GraphOnlyArm(CompiledContextArm):
    """C — no document retrieval. ``compile_context`` from the question alone."""

    name = "graph_only"
    description = ("no document retrieval; compile_context seeds itself from "
                   "the question")


# --------------------------------------------------------------------------
# Construction
# --------------------------------------------------------------------------


def source_document_nodes(graph: Any) -> Dict[int, str]:
    """``session number -> SourceDocument node id``, from the graph's own paths.

    Keyed on the number in ``session-0007.md``, which is what a ``dia_id``
    names and what :func:`evals.locomo.adapter.document_name` writes — so the
    mapping is an inversion of the staging rule rather than a second convention.
    """
    from ..lme_mab.adapter import document_index

    nodes: Dict[int, str] = {}
    for node in getattr(graph, "nodes", []):
        kind = getattr(getattr(node, "type", None), "value", getattr(node, "type", ""))
        if str(kind) != "SourceDocument":
            continue
        index = document_index(str(getattr(node, "source_path", "") or ""))
        if index is None:
            continue
        nodes.setdefault(index, str(node.id))
    return nodes


def build_arms(
    names: Sequence[str],
    *,
    conversation: str,
    system: str,
    documents: Mapping[int, str],
    ranker: Any = None,
    graph: Any = None,
    project_root: str = "",
    region_k: int = DEFAULT_REGION_K,
    grid: Sequence[int] = COMPILE_BUDGET_GRID,
) -> List[ContextArm]:
    """The requested arms for ONE conversation, or a :class:`Skip` saying why not.

    Refusing loudly matters more here than anywhere else in the harness: an arm
    that quietly falls back to a different context source still produces a
    complete report, and the report's whole subject is which context source was
    used.
    """
    ordered = [(i, body) for i, body in sorted(documents.items())]
    arms: List[ContextArm] = []
    for name in names:
        if name == "closed_book":
            arms.append(ClosedBookArm(conversation, system))
        elif name == "whole_corpus":
            arms.append(WholeCorpusArm(conversation, system, ordered))
        elif name == "bm25_docs":
            if ranker is None:
                raise Skip("the bm25_docs arm has no ranker",
                           "construct it with a LexicalArm over the staged "
                           "documents")
            arms.append(Bm25DocumentsArm(conversation, system, ranker, documents))
        elif name in ("bm25_compiled", "graph_only"):
            if graph is None:
                raise Skip(f"the {name} arm has no compiled graph",
                           "point --work at a directory holding a compiled "
                           "<conversation>/.tesserae/graph.json")
            if name == "graph_only":
                arms.append(GraphOnlyArm(conversation, system, graph,
                                         project_root, grid=grid))
            else:
                if ranker is None:
                    raise Skip("the bm25_compiled arm has no ranker",
                               "construct it with a LexicalArm over the staged "
                               "documents")
                arms.append(Bm25CompiledArm(
                    conversation, system, graph, project_root, ranker,
                    source_document_nodes(graph), region_k=region_k, grid=grid))
        else:
            raise Skip(f"unknown arm {name!r}",
                       f"pick from {', '.join(ARM_NAMES)}")
    return arms


def parse_arms(value: str) -> List[str]:
    names = [part.strip() for part in str(value).split(",") if part.strip()]
    if not names:
        raise Skip("--arms is empty", f"pass one of {', '.join(ARM_NAMES)}")
    unknown = [n for n in names if n not in ARM_NAMES]
    if unknown:
        raise Skip(f"unknown arm(s): {', '.join(unknown)}",
                   f"pick from {', '.join(ARM_NAMES)}")
    seen: List[str] = []
    for name in names:
        if name not in seen:
            seen.append(name)
    return seen


def parse_budgets(value: str) -> List[int]:
    """The token ladder. Rungs, ascending, deduplicated, all positive."""
    try:
        rungs = sorted({int(part) for part in str(value).split(",") if part.strip()})
    except ValueError as exc:
        raise Skip(f"--budgets {value!r} is not a comma-separated integer list",
                   "pass something like --budgets 512,2048,8192") from exc
    if not rungs:
        raise Skip("--budgets is empty", "pass something like 512,2048,8192")
    if rungs[0] <= 0:
        raise Skip(f"--budgets holds a non-positive rung ({rungs[0]})",
                   "a token budget is a positive number of tokens")
    return rungs


def staged_bodies(work: Path, conversation: str) -> Dict[int, str]:
    """``session number -> document text`` read from a staged corpus on disk.

    Read from the SAME files the graph was compiled from, so the three arms
    cannot disagree about what the corpus is.
    """
    from ..lme_mab.adapter import document_index

    corpus = Path(work) / conversation / "corpus"
    if not corpus.is_dir():
        raise Skip(f"no staged corpus at {corpus}",
                   "stage one with `python -m evals.locomo.run --stage-only`, "
                   "or point --work at a directory that already holds one")
    bodies: Dict[int, str] = {}
    for path in sorted(corpus.glob("session-*.md")):
        index = document_index(str(path))
        if index is None:
            continue
        bodies[index] = path.read_text(encoding="utf-8")
    if not bodies:
        raise Skip(f"{corpus} holds no session-*.md documents",
                   "stage the conversation before measuring against it")
    return bodies


__all__ = [
    "ARM_NAMES",
    "COMPILE_BUDGET_GRID",
    "DEFAULT_REGION_K",
    "Bm25CompiledArm",
    "Bm25DocumentsArm",
    "ClosedBookArm",
    "CompiledContextArm",
    "ContextArm",
    "GraphOnlyArm",
    "WholeCorpusArm",
    "build_arms",
    "parse_arms",
    "parse_budgets",
    "source_document_nodes",
    "staged_bodies",
]
