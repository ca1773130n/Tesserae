"""The LoCoMo memory backend: stage a conversation, compile it, query it.

Modelled directly on :mod:`evals.lme_mab.adapter`, and reusing its pieces
wherever the two benchmarks genuinely share one — ``guard_work_dir``,
``document_title``, ``document_index``, ``evidence_text``. Those are facts about
how THIS repository stages a session document and recovers it from a retrieved
node's ``source_path``, not facts about LongMemEval, and a second spelling of
``session-%04d.md`` that drifts by one zero maps every hit to nothing while
printing a plausible number.

What is NOT shared, and why each one is different here:

* **One project per CONVERSATION, not one per run.** Speaker names repeat
  across LoCoMo's ten conversations, so a pooled corpus lets a question about
  one conversation retrieve another conversation's turns about a different
  person with the same name — undetectable from any reported number. Each
  conversation gets ``<work>/<sample_id>/``.
* **Gold alignment is a dictionary lookup.** LoCoMo names its evidence as
  ``dia_id`` strings, so :mod:`evals.locomo.retrieval` resolves them directly
  and LongMemEval's content-signature machinery has nothing to do here.
* **The document number is the session's own.** ``session_1`` stages as
  ``session-0001.md``, so ``D1:3`` resolves without a table.
* **The evidence cap is the whole session.** See
  :data:`EVIDENCE_SOURCE_CHARS`.

Nothing here reads a wall clock. Every filename and every document body is a
function of the conversation's own bytes, so re-staging is byte-identical.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from ..lme_mab.adapter import (
    REPO,
    MabHit,
    RefusedToCompileInRepo,
    document_index,
    document_title,
    evidence_text,
    guard_work_dir,
)
# Imported at module level, unlike `rerank`'s constants: `fanout` is pure
# Python over the same modules `hybrid_search` already needs (0.05 s cold), so
# there is nothing here to defer and no reason to duplicate a default that
# could then drift from the library's.
from tesserae.retrieval.fanout import DEFAULT_OVERFETCH, DEFAULT_SOURCE_CAP
from tesserae.retrieval.query_decompose import DEFAULT_UBIQUITY_DF_RATIO
from .dataset import Conversation, LocomoSession

# --------------------------------------------------------------------------
# The published protocol, as constants
# --------------------------------------------------------------------------

#: The judge Protocol B fixes — the ``gpt-4o-mini`` grader that produced the
#: ~66% era numbers and has since been copied verbatim into most memory-benchmark
#: harnesses. **There is no judge running in this repository today**, which is
#: why :func:`protocol_blockers` reports this control UNMET on every run this
#: phase can perform. That refusal is the feature: the deterministic judge in
#: :mod:`evals.locomo.judge` measures something real and is not this.
PROTOCOL_JUDGE = "gpt-4o-mini"

#: The judge temperature Protocol B fixes.
PROTOCOL_JUDGE_TEMPERATURE = 0.0

#: Protocol B grades every question three times and reports the mean and the
#: standard deviation ACROSS whole-run accuracies. It is the one piece of good
#: hygiene in this corner of the field and it is copied deliberately: a
#: generative arm in this repo has swung 0.043 token F1 between two runs of an
#: identical configuration, so a single generative number is not a measurement.
PROTOCOL_JUDGE_RUNS = 3

#: The answerer Protocol B fixes. Same model as the judge.
PROTOCOL_BACKBONE = "gpt-4o-mini"

#: sha256 prefix of the ``locomo10.json`` this harness was written against,
#: measured this phase from the checkout of ``snap-research/locomo`` at
#: ``3eb6f2c``. Declared per run by :func:`evals.locomo.dataset.dataset_revision`
#: and compared here: a benchmark whose answer key changed under it has not
#: measured what its report says it measured.
PROTOCOL_DATASET_REVISION = "sha256:79fa87e90f04"

#: Set when the published protocol fixes no value for a control. The control is
#: still REQUIRED to be declared — "we did not record which embedder retrieved"
#: is not a run anybody can reproduce — but it cannot be compared against a
#: constant, because there is no constant to compare it to. LoCoMo's published
#: protocols let every compared system bring its own retriever and its own
#: evidence budget, and pretending otherwise would invent a control the field
#: never agreed on.
UNFIXED = None


@dataclass(frozen=True)
class Control:
    """One protocol control: what it is, what it must equal, and why it matters."""

    key: str
    required: Optional[str]
    why: str

    @property
    def is_fixed(self) -> bool:
        return self.required is not None


#: The controls every artifact declares. Order is the order the report prints.
PROTOCOL_CONTROLS: Sequence[Control] = (
    Control(
        "llm_model", PROTOCOL_BACKBONE,
        f"the answering backbone must be {PROTOCOL_BACKBONE} — a different "
        f"model measures the model, not the memory, and the published spread "
        f"between two LoCoMo headlines is partly a spread between two backbones",
    ),
    Control(
        "judge", PROTOCOL_JUDGE,
        f"accuracy must be graded by {PROTOCOL_JUDGE} at temperature "
        f"{PROTOCOL_JUDGE_TEMPERATURE:g} — a different judge rescales every "
        f"score, and an independent audit of this benchmark's judges reports a "
        f"false-accept rate larger than the gaps people argue about",
    ),
    Control(
        "judge_runs", str(PROTOCOL_JUDGE_RUNS),
        f"the grade must be the mean of {PROTOCOL_JUDGE_RUNS} independent runs "
        f"with the spread reported — a single generative number is not a "
        f"measurement",
    ),
    Control(
        "dataset_revision", PROTOCOL_DATASET_REVISION,
        "the answer key must be the one this harness was written against — a "
        "changed locomo10.json changes every denominator in the report",
    ),
    Control(
        "embedding_model", UNFIXED,
        "the published protocols let every compared system bring its own "
        "retriever, so there is no value to match — it is declared so the run "
        "is reproducible, and any comparison that rests on it is this "
        "machine's own",
    ),
    Control(
        "evidence_budget", UNFIXED,
        "K is not fixed by the published protocols either — one of them "
        "retrieves 200 memories per question — so it is declared rather than "
        "matched, and every retrieval table here prints its own K",
    ),
)


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def protocol_blockers(meta: Mapping[str, Any]) -> List[str]:
    """Reasons this run's numbers may NOT be printed as published-comparable.

    A **missing** declaration blocks on the same terms as a wrong one: "we did
    not record which model answered" is not "the model matched". A control the
    publication does not fix (:data:`UNFIXED`) blocks only when it is missing.

    Declarations are CLAIMS, so evidence is required on top of them — the same
    hole :func:`evals.lme_mab.adapter.protocol_blockers` closed after a
    hand-written answers file asserted every control and unlocked a comparable
    table with no run behind it. ``llm_judge_calls`` is counted separately from
    any other judging: the deterministic judge in :mod:`evals.locomo.judge`
    grades every question and calls no model, so a run that used it declares
    zero here and is blocked, which is correct.
    """
    blockers: List[str] = []
    for control in PROTOCOL_CONTROLS:
        declared = meta.get(control.key)
        if declared in (None, ""):
            blockers.append(f"{control.key}: not declared — {control.why}")
            continue
        if control.is_fixed and str(declared) != control.required:
            blockers.append(
                f"{control.key}: this run used {declared}, the protocol fixes "
                f"{control.required} — {control.why}"
            )

    evidence = meta.get("evidence")
    if not isinstance(evidence, Mapping):
        blockers.append(
            "evidence: absent — every control above is an unverified claim. A "
            "run records what it actually did; a hand-written declaration "
            "cannot, and must not unlock a comparable table"
        )
        return blockers
    if not _as_int(evidence.get("llm_judge_calls")):
        blockers.append(
            f"llm_judge_calls: 0 — nothing graded these answers with "
            f"{PROTOCOL_JUDGE}. The deterministic judge grades without a model "
            f"and is honest about it; declaring a judge model is not judging"
        )
    if not _as_int(evidence.get("answer_calls")):
        blockers.append(
            "answer_calls: 0 — no answers were generated by this run, so there "
            "is nothing for a judge to have graded"
        )
    if not _as_int(evidence.get("canary_calls")):
        blockers.append(
            "canary_calls: 0 — no canary proved the backbone was alive. A dead "
            "provider chain returns None, which becomes \"\", which is_refusal "
            "reads as a refusal: the run then prints refusal_rate 1.000 with "
            "error_rate 0.000, and on the adversarial category that broken "
            "system scores a perfect result"
        )
    return blockers


# --------------------------------------------------------------------------
# Staging
# --------------------------------------------------------------------------


def document_name(session_number: int) -> str:
    """``session-0001.md`` for ``session_1``.

    The session's OWN number, not a zero-based position, so
    :func:`evals.lme_mab.adapter.document_index` inverts a retrieved
    ``source_path`` straight back to the number a ``dia_id`` names. A position
    would put a lookup table between ``D1:3`` and its document, and a lookup
    table is a place for an off-by-one to hide inside a plausible recall score.
    """
    return f"session-{session_number:04d}.md"


def render_session(session: LocomoSession) -> str:
    """The markdown document a compile reads for one session.

    The date is in the BODY and not only in a heading: the lexical and embedding
    lanes score document text, and a date that exists only in a filename is a
    date the temporal category cannot retrieve. 321 of the 1,986 questions are
    temporal.

    Turn rendering is :meth:`evals.locomo.dataset.Turn.render` — the published
    ``<speaker> said, "<text>" and shared <caption>`` form, so this corpus is
    the same text the reference harness embeds, plus the ``dia_id``.
    """
    lines = [f"# {document_title(session.number)}", ""]
    lines += [f"Chat Time: {session.date}" if session.date
              else "Chat Time: not recorded in this conversation.", ""]
    for turn in session.turns:
        lines += [turn.render(), ""]
    return "\n".join(lines).rstrip() + "\n"


#: How much of a document anchor's own session file joins its ANSWERING
#: evidence.
#:
#: 8,000 — the WHOLE session, every time — and it is re-derived here rather than
#: carried over from :data:`evals.lme_mab.adapter.EVIDENCE_SOURCE_CHARS` (2,400,
#: which that module derives from LongMemEval's own mean round length). The
#: arithmetic inverts between the two benchmarks. Measured this phase over all
#: 272 staged documents of ``locomo10.json``, one renders to 3,553 characters on
#: average (median 3,247, p90 5,253, max 7,275, min 1,558) — so ZERO of them
#: exceed ``tesserae.retrieval.hybrid``'s ``SOURCE_LEXICAL_CHARS`` of 8,000.
#:
#: That equality is the point, and it removes a confound rather than adding a
#: knob: the lexical lane ranks a document on the first 8,000 characters of its
#: source, so at this cap the text the backbone reads is exactly the text the
#: retriever scored. A smaller cap would reintroduce the failure LongMemEval's
#: constant exists to bound — a session that ranks first on a term in its
#: opening paragraph, with the answer past the cap, is a perfect retrieval and
#: an unanswerable prompt — and here it would do so for no saving, because
#: there is no session long enough for the cap to bind.
EVIDENCE_SOURCE_CHARS = 8_000

#: How many candidates the lanes are asked for per unit of budget WHEN a
#: cross-encoder is reranking them.
#:
#: A reranker can only reorder what it is handed, so the candidate set is the
#: recall ceiling of the whole stage: at overfetch 1 it can never move a
#: document the lanes ranked 11th into a top-10 budget, and the run measures
#: nothing but reordering noise. 4 is the smallest multiple that lets rank 40
#: reach rank 1, and costs 4x the cross-encoder forward passes — the only cost
#: that scales with it, because the lanes were already scoring the whole corpus.
RERANK_OVERFETCH = 4

#: Tokens per (query, candidate) pair the cross-encoder reads.
#:
#: Duplicated from :data:`tesserae.retrieval.rerank.DEFAULT_MAX_LENGTH` rather
#: than imported, because importing that module pulls in torch and this one is
#: imported by every LoCoMo run including the ones with no reranker. The two
#: are pinned equal by ``test_the_harness_default_matches_the_library_default``
#: so the duplication cannot drift silently.
RERANK_MAX_LENGTH = 512

#: How much session text ONE question's evidence may carry BEYOND what document
#: anchors already bring.
#:
#: :data:`EVIDENCE_SOURCE_CHARS` bounds a single document; this bounds the extra
#: expansions :meth:`LocomoMemory.answer_evidence` gained when it stopped
#: requiring a hit to BE its session before pasting it. It is deliberately a
#: budget on the ADDITION and not a cap on the total, and that is the whole
#: safety argument: every document the anchors-only rule pasted is still pasted,
#: so no question can come out of this change with less evidence than it had.
#:
#: A total cap was measured first and rejected. Spending one 12,000-character
#: budget across anchors and concept hits alike — even with anchors given first
#: claim — moved 24 of the 150 gradeable conv-26 questions INTO gold-session
#: coverage and moved **14 out of it**, because the anchors-only rule had no
#: budget at all and a question whose ten hits were nine anchors used to paste
#: all nine (prompt max 40,826 characters). The aggregate hid the regression:
#: coverage still rose, 53.3% -> 60.0%. Losses only reached zero at a 32,000
#: budget, by which point the prompt was larger than the additive design's. A
#: net gain that silently takes 14 questions backwards is not the change worth
#: shipping when a strictly additive one is available for the same prompt.
#:
#: 8,000 — measured, over one frozen retrieval of all 199 conv-26 questions of
#: the 2026-08-21 run. It buys 1.76 more expanded sessions per question (2.68 ->
#: 4.44, and the minimum rises from 0 to 1, so no prompt is summaries alone),
#: takes gold-session coverage on the 150 gradeable questions from 53.3% to
#: 78.0% — 37 questions gained the gold session's text and 0 lost it — and on
#: the 30 refusals specifically from 5/30 to 15/30. The cost is the prompt:
#: mean 14,143 -> 20,798 over
#: all 199, and on the adversarial category — the one a refusal fix most
#: endangers — 15,277 -> 21,697. THAT COST IS NOT ESTIMATED HERE. Abstention on
#: adversarial questions is measured beside accuracy on every run, and the
#: decision rule is fixed before the run rather than after it: accuracy must rise
#: by more than adversarial abstention falls.
#:
#: The next rung, 12,000, reaches 85.3% coverage for a 24,850-character
#: adversarial prompt. It is the obvious follow-up if this budget's abstention
#: holds, and the obvious thing not to have shipped if it does not.
EVIDENCE_EXTRA_SOURCE_CHARS = 8_000

#: ``Chat Time:`` as :func:`render_session` writes it, read back off a staged
#: document. Bounded to the document's head because a turn is free to contain
#: the words "Chat Time:" and the header is line 3.
_CHAT_TIME = re.compile(r"^Chat Time:[ \t]*(.+)$", re.MULTILINE)
_CHAT_TIME_HEAD_CHARS = 400
#: A leading clock reading and whatever joins it to the date — ``1:56 pm on``,
#: ``2:35 pm,``. Both spellings occur in ``locomo10.json``.
_CLOCK = re.compile(r"^\d{1,2}:\d{2}\s*(?:am|pm)?\s*(?:on\b|,)?\s*", re.I)
_YEAR_COMMA = re.compile(r",\s*((?:19|20)\d{2})\b")
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")


def session_date(document: str) -> str:
    """The calendar date a staged session states about itself, or ``""``.

    ``"1:56 pm on 8 May, 2023"`` -> ``"8 May 2023"``. The clock reading is
    dropped because no LoCoMo gold answer is a time of day, and the comma before
    the year is dropped because the golds are written ``"8 May 2023"``.

    **A year is required.** :func:`render_session` writes ``Chat Time: not
    recorded in this conversation.`` for a session the file did not date, and
    stamping an evidence item with that sentence would put a confident-looking
    non-date next to a claim. No date is the honest rendering of no date.

    This reads the STAGED DOCUMENT and not node metadata on purpose. The
    extractor's dating is at the model's discretion — the compiled conv-26 graph
    carries nine distinct date-ish keys in two incompatible formats across 27 of
    its 345 nodes, and 218 nodes carry none — whereas the header is written by
    :func:`render_session` from ``session_<n>_date_time`` and is present on every
    document this adapter stages.
    """
    match = _CHAT_TIME.search((document or "")[:_CHAT_TIME_HEAD_CHARS])
    if not match:
        return ""
    text = _YEAR_COMMA.sub(r" \1", _CLOCK.sub("", match.group(1).strip()).strip())
    return text if _YEAR.search(text) else ""


@dataclass
class IngestResult:
    """What one conversation's ingest put on disk, and what it cost in units."""

    conversation: str
    work: Path
    corpus_dir: Path
    documents: int
    turns: int
    chars: int
    dated_sessions: int
    captioned_turns: int
    compiled: bool
    reused: bool = False

    @property
    def approx_tokens(self) -> int:
        """Chars/4 — deliberately crude, and only ever printed as an estimate."""
        return self.chars // 4


def _graph_missing_sessions(graph_path: Path, corpus: Path) -> set:
    """Staged documents the compiled graph does not index, by basename."""
    import json as _json

    try:
        payload = _json.loads(graph_path.read_bytes())
    except (OSError, ValueError) as exc:
        # An UNREADABLE graph is not an empty one. Returning set() here made the
        # caller read "no missing documents" — i.e. "the graph indexes every
        # staged document" — from a file it could not parse. Measured: a
        # truncated graph.json ('{"nodes": [{"source_path": "corp') was ACCEPTED
        # by --reuse-compile, and only the well-formed '{}' case that the tests
        # exercise was refused.
        #
        # This is the identical defect fixed in evals/lme_mab/adapter.py, and it
        # was reproduced here by copying the shape without the reasoning. Refuse
        # loudly: a graph that cannot be read cannot be reused.
        raise ValueError(
            f"--reuse-compile: {graph_path} could not be parsed "
            f"({type(exc).__name__}: {exc}). A graph that cannot be read cannot "
            f"be verified against the staged corpus; recompile."
        ) from exc
    indexed = set()
    for node in payload.get("nodes") or []:
        source = node.get("source_path") if isinstance(node, dict) else None
        if source:
            indexed.add(Path(str(source)).name)
    return {p.name for p in corpus.glob("*.md")} - indexed


def _verify_staged(corpus: Path, sessions: Sequence[LocomoSession]) -> tuple:
    """``(turns, chars)``, having proved this conversation is already staged there.

    Raises unless every session renders byte for byte to the file already on
    disk, and unless the directory holds nothing else. A CHANGED document means
    the compiled graph was built from text this run would not stage; an EXTRA
    one means the graph indexes a session this conversation does not contain —
    retrievable evidence from a conversation the questions were never asked
    about. Either way the reused graph is not this conversation's graph.
    """
    if not corpus.is_dir():
        raise FileNotFoundError(
            f"--reuse-compile: no staged corpus at {corpus}. There is nothing "
            f"to reuse; run without the flag to stage and compile."
        )
    turns = chars = 0
    mismatched: List[str] = []
    for session in sessions:
        body = render_session(session)
        staged = corpus / document_name(session.number)
        if not staged.is_file():
            mismatched.append(f"{document_name(session.number)} (missing)")
        elif staged.read_bytes() != body.encode("utf-8"):
            mismatched.append(f"{document_name(session.number)} (differs)")
        turns += len(session.turns)
        chars += len(body)
    expected = {document_name(s.number) for s in sessions}
    extra = sorted(p.name for p in corpus.glob("*.md") if p.name not in expected)
    if mismatched or extra:
        raise ValueError(
            f"--reuse-compile: {corpus} is not this conversation's corpus — "
            f"{len(mismatched)} document(s) missing or changed"
            f"{': ' + ', '.join(mismatched[:5]) if mismatched else ''}"
            f"{f'; {len(extra)} unexpected: ' + ', '.join(extra[:5]) if extra else ''}. "
            f"The compiled graph there answers about a different conversation. "
            f"Re-run without --reuse-compile to rebuild it."
        )
    return turns, chars


def _default_compile(work: Path) -> None:
    """``tesserae init`` then ``tesserae compile``, in ``work``.

    The checkout's own venv when there is one, else the running interpreter —
    the resolution ``evals/growth/run.py`` settled on after hardcoding the first
    form killed every run inside a git worktree.
    """
    venv = REPO / ".venv" / "bin" / "python"
    python = str(venv if venv.is_file() else sys.executable)
    subprocess.run(
        [python, "-m", "tesserae", "init", "--yes", "--source", "./corpus"],
        cwd=work, check=True, capture_output=True,
    )
    result = subprocess.run(
        [python, "-m", "tesserae", "compile"],
        cwd=work, capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"compile failed in {work}:\n{result.stdout[-2000:]}\n{result.stderr[-2000:]}"
        )


class LocomoMemory:
    """A memory system under test: stage one conversation, retrieve from it.

    ``compile_fn`` and ``search_fn`` are injection points and not decoration.
    Every test in this package passes stubs for both, because the real pair is
    an hours-long LLM extraction and a metered embedding call, and a harness
    whose wiring can only be checked by running the benchmark does not get
    checked.

    One instance per CONVERSATION. :meth:`ingest` resolves
    ``<work>/<sample_id>/`` and compiles there, so ten conversations are ten
    isolated graphs and no question can retrieve a conversation it was not
    asked about.
    """

    def __init__(
        self,
        *,
        compile_fn: Optional[Callable[[Path], None]] = None,
        search_fn: Optional[Callable[..., Any]] = None,
        backend: Any = None,
        embedding_prefer: str = "model2vec",
        mode: str = "hybrid",
        weights: Optional[Dict[str, float]] = None,
        reranker: Any = None,
        rerank_overfetch: int = RERANK_OVERFETCH,
        fanout: bool = False,
        fanout_overfetch: int = DEFAULT_OVERFETCH,
        source_cap: Optional[int] = DEFAULT_SOURCE_CAP,
        ubiquity_df_ratio: float = DEFAULT_UBIQUITY_DF_RATIO,
        extra_facets: int = 0,
        prefer_anchor_text: bool = False,
    ) -> None:
        self._compile_fn = compile_fn or _default_compile
        self._search_fn = search_fn
        self._backend = backend
        self._embedding_prefer = embedding_prefer
        self._mode = mode
        self._weights = weights
        #: Optional cross-encoder. ``None`` is the shipped path and returns the
        #: fused ranking untouched — not a degraded version of it.
        self._reranker = reranker
        if rerank_overfetch < 1:
            raise ValueError("rerank_overfetch must be >= 1")
        self._rerank_overfetch = rerank_overfetch
        #: Query fan-out with a document-disjoint merge. ``False`` is the
        #: shipped path and asks the lanes for exactly the budget, as before.
        self._fanout = fanout
        if fanout_overfetch < 1:
            raise ValueError("fanout_overfetch must be >= 1")
        self._fanout_overfetch = fanout_overfetch
        #: 1 HERE and ``None`` in the library, deliberately. This adapter's
        #: corpus is one node-set per session document, which is the shape one
        #: hit per document is correct for; a graph where thousands of nodes
        #: share one path is the shape it is wrong for, so the library refuses
        #: to choose on a caller's behalf.
        self._source_cap = source_cap
        self._ubiquity_df_ratio = ubiquity_df_ratio
        self._extra_facets = extra_facets
        self._prefer_anchor_text = prefer_anchor_text
        self.work: Optional[Path] = None
        self.conversation: Optional[str] = None
        self._graph: Any = None
        #: ``source_path`` -> the node that STANDS FOR that file, built once per
        #: graph. Only consulted when ``prefer_anchor_text`` is on.
        self._anchor_by_path: Optional[Dict[str, Any]] = None
        #: One entry per query that returned fewer than K items. Never padded.
        self.shortfalls: List[Dict[str, Any]] = []
        #: Retrieved nodes whose provenance is not a staged session document.
        self.n_unmapped_hits = 0

    # ------------------------------------------------------------------ ingest

    def project_dir(self, root: Path, conversation: Conversation) -> Path:
        """``<root>/<sample_id>`` — this conversation's own project."""
        return guard_work_dir(root) / conversation.sample_id

    def ingest(
        self,
        conversation: Conversation,
        *,
        work: Path,
        compile_project: bool = True,
        reuse_compiled: bool = False,
    ) -> IngestResult:
        """Stage one document per session under ``<work>/<sample_id>``, then compile.

        The corpus directory is removed and rebuilt so a re-run cannot inherit
        documents from another conversation — a stale ``session-0030.md`` from a
        longer conversation would be retrievable evidence from a corpus this run
        never saw.

        ``reuse_compiled`` measures against a graph a PREVIOUS run compiled,
        which is the only way to re-measure without paying the compile again. It
        writes nothing: it verifies that every document this conversation would
        stage is already on disk byte for byte AND that the compiled graph
        indexes them, then reuses. Verifying the corpus alone is not enough —
        ``ingest`` restages before compiling, so a directory can hold one
        conversation's fresh documents beside another's graph.
        """
        resolved = self.project_dir(work, conversation)
        resolved.mkdir(parents=True, exist_ok=True)
        sessions = list(conversation.sessions)
        if not sessions:
            raise ValueError(
                f"{conversation.sample_id} holds no session_<n> dialogue, so it "
                f"would stage an empty corpus and score zero — which is not "
                f"what a memory system failing to answer looks like"
            )
        corpus = resolved / "corpus"

        if reuse_compiled:
            turns, chars = _verify_staged(corpus, sessions)
            graph_path = resolved / ".tesserae" / "graph.json"
            if not graph_path.is_file():
                raise FileNotFoundError(
                    f"--reuse-compile: no compiled graph at {graph_path}. There "
                    f"is nothing to reuse; run without the flag to compile."
                )
            missing = _graph_missing_sessions(graph_path, corpus)
            if missing:
                raise ValueError(
                    f"--reuse-compile: the graph at {graph_path} does not index "
                    f"{len(missing)} of the {len(sessions)} staged session "
                    f"documents (e.g. {sorted(missing)[:3]}). It was compiled "
                    f"from a different conversation or an older corpus; recompile."
                )
        else:
            shutil.rmtree(corpus, ignore_errors=True)
            corpus.mkdir(parents=True, exist_ok=True)
            turns = chars = 0
            for session in sessions:
                body = render_session(session)
                (corpus / document_name(session.number)).write_text(
                    body, encoding="utf-8")
                turns += len(session.turns)
                chars += len(body)
            if compile_project:
                self._compile_fn(resolved)

        self.work = resolved
        self.conversation = conversation.sample_id
        self._graph = None  # a new corpus invalidates any graph already loaded
        self._anchor_by_path = None  # ...and every index derived from it

        return IngestResult(
            conversation=conversation.sample_id,
            work=resolved,
            corpus_dir=corpus,
            documents=len(sessions),
            turns=turns,
            chars=chars,
            dated_sessions=sum(1 for s in sessions if s.date),
            captioned_turns=sum(1 for s in sessions for t in s.turns
                                if t.blip_caption),
            compiled=compile_project and not reuse_compiled,
            reused=reuse_compiled,
        )

    # ------------------------------------------------------------------- query

    def _resolve_graph(self) -> Any:
        if self._graph is not None:
            return self._graph
        if self.work is None:
            raise RuntimeError("query() before ingest() — there is no compiled graph")
        graph_path = self.work / ".tesserae" / "graph.json"
        if not graph_path.is_file():
            raise FileNotFoundError(
                f"no compiled graph at {graph_path} — ingest() ran with "
                f"compile_project=False, or the compile failed"
            )
        from tesserae.project import load_graph_file

        self._graph = load_graph_file(graph_path)
        return self._graph

    def embedding_backend(self) -> Any:
        """The backend retrieval will use, constructed on first call.

        Public because the runner declares its ``name`` and ``dim`` into every
        artifact, and a declaration read from anywhere other than the live
        object is a declaration that can be true while the run did something
        else.
        """
        if self._backend is not None:
            return self._backend
        from tesserae.retrieval.hybrid import active_embedding_backend

        self._backend = active_embedding_backend(self._embedding_prefer)
        return self._backend

    def _resolve_search(self) -> Callable[..., Any]:
        if self._search_fn is not None:
            return self._search_fn
        if self._fanout:
            # `fanout_search` takes every parameter `hybrid_search` takes and
            # adds its own, so this swap is the whole wiring. An injected
            # `search_fn` still wins — the tests drive stubs through it.
            from tesserae.retrieval.fanout import fanout_search

            self._search_fn = fanout_search
            return self._search_fn
        from tesserae.retrieval.hybrid import hybrid_search

        self._search_fn = hybrid_search
        return self._search_fn

    def _anchor_index(self) -> Dict[str, Any]:
        """``source_path`` -> the node that STANDS FOR that file.

        Selection is by IDENTITY — the node whose name IS the file's H1 — and
        NOT by ``hybrid._SOURCE_ANCHOR_TYPES``, for the reason
        :meth:`MabHit.is_document_anchor` documents at length: type matches 214
        nodes of the compiled group-0 graph and only 111 of them are the
        transcripts. The other 103 are things somebody talked about, and they
        carry a ``session-NNNN.md`` path all the same.

        The two tests must agree because they are two halves of one decision.
        This one chooses the node a hit is REWRITTEN to; ``is_document_anchor``
        then decides whether that node's session text is pasted
        unconditionally. Choose an impostor here and the rewritten hit fails
        the second test, falls back into the shared
        :data:`EVIDENCE_EXTRA_SOURCE_CHARS` pool, and starves — which is
        precisely the failure ``prefer_anchor_text`` exists to repair.

        Measured on the type test, pooled over 272 session files: 33 picked a
        node that then failed ``is_document_anchor`` — conv-30 9 of 19,
        conv-47 11 of 31, conv-48 4 of 30, conv-50 4 of 30, and conv-26 zero,
        which is why a conv-26-only sweep could not see this at all.
        ``session-0013.md`` chose the Project "Fashion Styling Video
        Presentation"; ``session-0001.md`` chose "Dog walking and pet care
        app". On conv-47 the flag delivered 43.7% of hits still non-anchor
        after substitution and +0.084 gold-text coverage against conv-26's
        +0.244.

        Built once per graph, never per query: this walks every node, and
        conv-26 alone would repeat that 199 times a run otherwise.
        """
        if self._anchor_by_path is not None:
            return self._anchor_by_path
        index: Dict[str, Any] = {}
        for node in getattr(self._resolve_graph(), "nodes", []):
            path = str(getattr(node, "source_path", "") or "")
            if not path or path in index:
                continue
            document = document_index(path)
            if document is None:
                continue
            if str(getattr(node, "name", "") or "") == document_title(document):
                index[path] = node
        self._anchor_by_path = index
        return index

    def _hit_nodes(self, scored: Sequence[Any]) -> List[Any]:
        """The nodes ``scored`` becomes hits from, anchors substituted or not.

        REQUIRED whenever ``source_cap`` is on, and the reason is a measured
        regression rather than a preference. With one hit per session, ten
        sessions compete for the same 8,000-character
        :data:`EVIDENCE_EXTRA_SOURCE_CHARS` and fewer of them get their raw text
        pasted, so the DOCUMENT metric rises while the PROMPT starves: pooled,
        ALL-doc@10 0.823 -> 0.883 but gold-evidence-turn coverage 0.791 ->
        0.751, multi-hop turn coverage 0.468 -> 0.420, all-gold-turns-present
        20.6% -> 16.3%. Rebuilding each hit from the node that STANDS FOR its
        file — which ``answer_evidence`` expands unconditionally — is what
        repairs that: multi-hop turn coverage 0.436 -> 0.555 at a matched
        character budget.

        KNOWN SIDE EFFECT, stated rather than hidden: ``MabHit`` derives
        :attr:`~evals.lme_mab.adapter.MabHit.is_document_anchor` from ``name``,
        and ``answer_evidence`` expands anchors UNCONDITIONALLY before the extra
        budget opens, capping each only at :data:`EVIDENCE_SOURCE_CHARS` with no
        total. Ten anchors therefore bypass the extra-source budget entirely and
        grow the prompt from 19,547 to 37,213 characters. That is why the
        acceptance gate for this flag is run at a MATCHED budget. Giving
        ``answer_evidence`` a total anchor budget is a separate change that has
        to be measured on its own.

        Two hits from one session collapse to the same anchor node here, so this
        is only coherent alongside a ``source_cap`` that already made the head
        document-disjoint. ``answer_evidence``'s ``spent`` set still pastes each
        file once either way.
        """
        if not self._prefer_anchor_text:
            return [item.node for item in scored]
        index = self._anchor_index()
        out: List[Any] = []
        for item in scored:
            node = item.node
            path = str(getattr(node, "source_path", "") or "")
            out.append(index.get(path, node))
        return out

    def query_hits(self, question: str, *, k: int) -> List[MabHit]:
        """Up to ``k`` hits for ``question``. Never more, never padded.

        The one search both the answering path and the retrieval score are built
        on, so the evidence a run answers from and the documents its retrieval is
        scored on can never come from two different rankings. Fewer than ``k`` is
        recorded in :attr:`shortfalls` and returned short: padding would make an
        under-filled budget indistinguishable from a full one.

        RERANKER ORDERING, AND IT IS UNTESTED. ``rerank_nodes`` has no notion of
        documents and will happily re-cluster several hits from one session,
        undoing the cap. So with both stages on, the fan-out runs UNCAPPED, the
        cross-encoder reorders, and the cap is applied to what it produced.
        ``fanout`` and ``reranker`` are never both on in the sweep this was
        built for, so that ordering has been reasoned about and not measured.
        """
        # With a reranker the lanes are a CANDIDATE GENERATOR, not the final
        # ranking, so they are asked for more than the budget and the
        # cross-encoder chooses k of them. Without one, `top_k` is k exactly and
        # this line is what it always was.
        search_k = k * self._rerank_overfetch if self._reranker else k
        extra: Dict[str, Any] = {}
        if self._fanout:
            extra = {
                "overfetch": self._fanout_overfetch,
                # Capped here only when nothing downstream would undo it.
                "source_cap": None if self._reranker else self._source_cap,
                "ubiquity_df_ratio": self._ubiquity_df_ratio,
                "extra_facets": self._extra_facets,
            }
        result = self._resolve_search()(
            self._resolve_graph(),
            question,
            top_k=search_k,
            weights=self._weights,
            mode=self._mode,
            backend=self.embedding_backend(),
            # The extraction pipeline builds a node's searchable text from its
            # name and description, so a whole chat session would be retrievable
            # only through a short concept summary. Handing the lexical lanes
            # the session file itself is what closes that gap, and it is
            # confined to the directory this adapter staged into.
            source_root=self.work,
            **extra,
        )
        scored_nodes = result.scored
        capped_after_rerank = (
            self._reranker is not None
            and self._fanout
            and self._source_cap is not None
        )
        if self._reranker:
            from tesserae.retrieval.rerank import rerank_nodes

            scored_nodes = rerank_nodes(
                question,
                scored_nodes,
                # The cap is what bounds the result when it runs after this, and
                # it can only choose among what it is handed: truncating to k
                # here would leave it k items that may all name one session, and
                # the no-shrink clamp would then refill with that same session.
                top_n=None if capped_after_rerank else k,
                reranker=self._reranker,
                # The same text the lexical lanes scored. A reranker reading
                # different text would be reordering a ranking it never saw.
                source_root=self.work,
            )
            if capped_after_rerank:
                from tesserae.retrieval.fanout import (
                    _merge_document_disjoint,
                    _source_path_key,
                )

                scored_nodes = _merge_document_disjoint(
                    [scored_nodes],
                    top_k=k,
                    source_cap=self._source_cap,
                    group_key=_source_path_key,
                )
        hits = [
            MabHit(
                text=evidence_text(node),
                source_path=str(getattr(node, "source_path", "") or ""),
                name=str(getattr(node, "name", "") or ""),
            )
            for node in self._hit_nodes(scored_nodes)
        ][:k]
        if len(hits) < k:
            self.shortfalls.append({
                "question": question,
                "conversation": self.conversation,
                "requested": k,
                "returned": len(hits),
                "total_matches": int(getattr(result, "total_matches", 0) or 0),
            })
        return hits

    def answer_evidence(self, hits: Sequence[MabHit], *,
                        expand: bool = True) -> List[str]:
        """``hits`` as the strings the BACKBONE reads — the answering path only.

        **A hit expands the session it came from, whether or not it IS that
        session.** Document anchors expand unconditionally, exactly as before;
        the remaining hits then expand in rank order until
        :data:`EVIDENCE_EXTRA_SOURCE_CHARS` of further source has been spent.
        Each file is pasted at most once, at the first hit that names it, and
        every item — expanded or not — is stamped with its session's date.

        Restricting expansion to :attr:`MabHit.is_document_anchor` is what this
        method used to do, and measured on the 150 gradeable conv-26 questions
        of the 2026-08-21 run it is the largest single loss in the benchmark.
        ``documents_of`` credits a session for ANY hit whose ``source_path``
        names it, so retrieval scored the gold session as retrieved for 140 of
        150 questions (93.3%) — but the gold session's TEXT reached the prompt
        for only 80 (53.3%), because the other 60 were reached through a concept
        node, which contributed its name and a description whose median length
        is 75 characters. Refusal tracks that gap and nothing else: 6.2% (5/80)
        when the session's text was in the prompt against 35.7% (25/70) when it
        was not, and reading all 30 refusals, 25 were the model correctly
        declining a prompt that did not contain the answer. That is why two
        successive prompt fixes did not move the refusal rate: the prompt was
        never the defect.

        The anchor test is still the right answer to the question it was asked —
        which node STANDS FOR a file, so ``documents_of`` can score one document
        per file — and :attr:`MabHit.is_document_anchor` keeps doing that job
        here, deciding who is expanded before the budget opens. It was the wrong
        answer to a different question: which hits are worth spending prompt on.
        The duplication its docstring exists to prevent — eleven concepts from
        one chat pasting one file eleven times — is prevented by ``chosen`` and
        ``spent``, which are keyed on the FILE.

        **The addition is strictly additive, and that is a property of the code
        rather than a result.** Anchors are chosen before the budget is
        consulted, so every document the old rule pasted is still pasted and no
        question can end up with less evidence than it had. The alternative —
        one budget spanning anchors and concept hits alike — was implemented and
        measured first, and it regressed 14 of the 150 gradeable questions while
        the aggregate coverage still rose. See
        :data:`EVIDENCE_EXTRA_SOURCE_CHARS`.

        ``source_path`` arrives from document frontmatter and is UNTRUSTED, and
        this is the side where that matters most — ranking buries a stolen file
        in a score, answering pastes it into a prompt — so the read goes through
        ``hybrid._confined_source`` rooted at the directory this adapter staged
        into. Widening expansion widens nothing here: a path that escapes the
        staging root reads as ``""``, expands to nothing, and is stamped with no
        date, exactly as before.

        ``expand=False`` returns the node summaries alone, unstamped. It is the
        control arm that makes the expansion measurable over ONE frozen
        retrieval rather than across two checkouts; nothing selects it by
        default.
        """
        if not expand:
            return [hit.text for hit in hits]

        from tesserae.retrieval.hybrid import _confined_source

        root = self.work
        cache: Dict[str, str] = {}

        def source_of(hit: MabHit) -> str:
            if root is None:
                return ""
            return _confined_source(hit.source_path, root, cache)[
                :EVIDENCE_SOURCE_CHARS]

        # Who gets pasted, decided before anything is rendered. Anchors first
        # and unconditionally — that ordering is what makes this a superset of
        # the rule it replaces — then the rest until the extra budget runs out.
        # A document is admitted WHOLE OR NOT AT ALL: a session truncated
        # mid-way is the ranked-but-unanswerable failure EVIDENCE_SOURCE_CHARS
        # exists to prevent. One that does not fit is skipped rather than
        # ending the loop, so leftover budget can still buy a smaller one
        # further down the ranking.
        chosen: set = set()
        for hit in hits:
            if hit.is_document_anchor and source_of(hit):
                chosen.add(hit.source_path)
        budget = EVIDENCE_EXTRA_SOURCE_CHARS
        for hit in hits:
            source = source_of(hit)
            if source and hit.source_path not in chosen and len(source) <= budget:
                chosen.add(hit.source_path)
                budget -= len(source)

        spent: set = set()
        evidence: List[str] = []
        for hit in hits:
            source = source_of(hit)
            raw = ""
            if source and hit.source_path in chosen and hit.source_path not in spent:
                raw = source
                spent.add(hit.source_path)
            head = hit.text
            stamp = session_date(source)
            if stamp:
                head = f"{head} — session date: {stamp}"
            evidence.append(f"{head}\n{raw}" if raw else head)
        return evidence

    def documents_of(self, hits: Sequence[MabHit]) -> List[int]:
        """The session NUMBERS behind ``hits``, ranked and de-duplicated.

        Two hits from one session are one document at their FIRST rank: a node
        and its neighbour are not two pieces of evidence about where the answer
        lives. So a full ``k`` hits can yield fewer than ``k`` documents, and
        that is the budget doing its job rather than a shortfall to fix.

        **A LOWER BOUND on what the memory retrieved, and the report says so.**
        A node keeps one ``source_path``, and canonicalization keeps the
        canonical node's when it collapses a concept extracted from many
        sessions — so a concept mentioned in twenty sessions points at one of
        them. Hits that map to no staged document are counted in
        :attr:`n_unmapped_hits` and dropped, never resolved to a nearby index:
        a fabricated document number scores better than the honest one and
        means nothing.
        """
        documents: List[int] = []
        for hit in hits:
            index = document_index(hit.source_path)
            if index is None:
                self.n_unmapped_hits += 1
                continue
            if index not in documents:
                documents.append(index)
        return documents

    def search_documents(self, question: str, *, k: int) -> List[int]:
        """:meth:`documents_of` of :meth:`query_hits`. One search, both answers."""
        return self.documents_of(self.query_hits(question, k=k))


__all__ = [
    "EVIDENCE_EXTRA_SOURCE_CHARS",
    "EVIDENCE_SOURCE_CHARS",
    "PROTOCOL_BACKBONE",
    "PROTOCOL_CONTROLS",
    "PROTOCOL_DATASET_REVISION",
    "PROTOCOL_JUDGE",
    "PROTOCOL_JUDGE_RUNS",
    "PROTOCOL_JUDGE_TEMPERATURE",
    "UNFIXED",
    "Control",
    "IngestResult",
    "LocomoMemory",
    "RefusedToCompileInRepo",
    "document_name",
    "guard_work_dir",
    "protocol_blockers",
    "render_session",
    "session_date",
]
