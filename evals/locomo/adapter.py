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
    except (OSError, ValueError):
        return set()
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
    ) -> None:
        self._compile_fn = compile_fn or _default_compile
        self._search_fn = search_fn
        self._backend = backend
        self._embedding_prefer = embedding_prefer
        self._mode = mode
        self._weights = weights
        self.work: Optional[Path] = None
        self.conversation: Optional[str] = None
        self._graph: Any = None
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
        from tesserae.retrieval.hybrid import hybrid_search

        self._search_fn = hybrid_search
        return self._search_fn

    def query_hits(self, question: str, *, k: int) -> List[MabHit]:
        """Up to ``k`` hits for ``question``. Never more, never padded.

        The one search both the answering path and the retrieval score are built
        on, so the evidence a run answers from and the documents its retrieval is
        scored on can never come from two different rankings. Fewer than ``k`` is
        recorded in :attr:`shortfalls` and returned short: padding would make an
        under-filled budget indistinguishable from a full one.
        """
        result = self._resolve_search()(
            self._resolve_graph(),
            question,
            top_k=k,
            weights=self._weights,
            mode=self._mode,
            backend=self.embedding_backend(),
            # The extraction pipeline builds a node's searchable text from its
            # name and description, so a whole chat session would be retrievable
            # only through a short concept summary. Handing the lexical lanes
            # the session file itself is what closes that gap, and it is
            # confined to the directory this adapter staged into.
            source_root=self.work,
        )
        hits = [
            MabHit(
                text=evidence_text(scored.node),
                source_path=str(getattr(scored.node, "source_path", "") or ""),
                name=str(getattr(scored.node, "name", "") or ""),
            )
            for scored in result.scored
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

        Only document anchors expand, and each source file at most once. A
        concept node keeps its summary, which is honestly all the text it has;
        and a session's text goes to the FIRST hit that stands for it, so two
        nodes extracted from one session cannot spend two evidence items on the
        same bytes.

        ``source_path`` arrives from document frontmatter and is UNTRUSTED, and
        this is the side where that matters most — ranking buries a stolen file
        in a score, answering pastes it into a prompt — so the read goes through
        ``hybrid._confined_source`` rooted at the directory this adapter staged
        into.

        ``expand=False`` returns the node summaries alone. It is the control arm
        that makes the expansion measurable over ONE frozen retrieval rather
        than across two checkouts; nothing selects it by default.
        """
        if not expand:
            return [hit.text for hit in hits]

        from tesserae.retrieval.hybrid import _confined_source

        root = self.work
        cache: Dict[str, str] = {}
        spent: set = set()
        evidence: List[str] = []
        for hit in hits:
            raw = ""
            if (root is not None and hit.is_document_anchor
                    and hit.source_path not in spent):
                raw = _confined_source(hit.source_path, root, cache)
                if raw:
                    spent.add(hit.source_path)
            evidence.append(f"{hit.text}\n{raw[:EVIDENCE_SOURCE_CHARS]}"
                            if raw else hit.text)
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
]
