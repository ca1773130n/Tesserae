"""The LongMemEval-MAB memory backend: ingest a haystack, query it for evidence.

Two operations, and the protocol from ``README.md`` decides both:

* :meth:`MabMemory.ingest` materializes one group's dialogue as markdown
  documents in a scratch project **outside this repository** and compiles there;
* :meth:`MabMemory.query` returns **exactly K = 10** evidence strings through
  :func:`tesserae.retrieval.hybrid.hybrid_search`.

Nothing here reads a wall clock. Every filename and every document body is a
function of the group's own bytes, so re-ingesting the same group produces a
byte-identical corpus directory.


The retrieval unit is a SESSION, not a turn-window
--------------------------------------------------

This is the choice that moves the score, so it is written down rather than
tuned. A group's dialogue is split at session boundaries — one markdown
document per session, ~112 per group — and NOT into fixed turn windows.

Three reasons, in the order they matter:

1. **K = 10 is a fixed control, so the unit size is one too.** The protocol
   gives every compared method the same evidence budget, which only means the
   same thing if a unit means the same thing. Measured on the real parquet, a
   group is ~112 sessions and ~1,290 turns; splitting by turn would make K = 10
   cover an eighth of the dialogue it covers by session. That moves the number
   for a reason that is not the memory architecture, which is precisely what the
   controls exist to prevent.
2. **A session is the benchmark's own unit.** LongMemEval builds the haystack
   out of sessions and marks ``has_answer`` on turns *within* one, so the
   evidence for a question is contained in a session by construction. A fixed
   turn window can straddle a boundary and fuse two unrelated conversations into
   one retrieval unit; nothing about the benchmark's construction bounds how bad
   that gets.
3. **Only sessions carry dates.** The ``Chat Time:`` header sits at the session
   boundary. ``temporal-reasoning`` is one of the benchmark's question types, so
   a split that discards the boundary discards the stratum.

The cost, stated rather than hidden: a session averages ~14k characters, so a
retrieved session carries a lot of text that has nothing to do with the
question, and per-turn retrieval would return tighter evidence. That is a real
trade, and it is settled by (1) — the unit is part of the protocol, not a knob.
"""

from __future__ import annotations

import ast
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from .dataset import MabGroup

#: Repository root — the directory this adapter must never compile into. Same
#: derivation as ``evals/growth/run.py``'s ``REPO``.
REPO = Path(__file__).resolve().parents[2]

# --------------------------------------------------------------------------
# The published protocol, as constants. See README.md — arXiv:2606.04555 §5.2.
# --------------------------------------------------------------------------

#: Evidence budget. **Not a tuning knob**: every method in the published table
#: was given ten pieces of evidence, so a run at any other K is measuring a
#: different experiment and :func:`protocol_blockers` says so.
PROTOCOL_K = 10
PROTOCOL_BACKBONE = "gpt-5.4-mini"
PROTOCOL_EMBEDDING_MODEL = "text-embedding-3-small"
PROTOCOL_EMBEDDING_DIM = 1536
#: The ``active_embedding_backend`` preference that resolves
#: :data:`PROTOCOL_EMBEDDING_MODEL`. Asking for it is what bills, so it is also
#: the only value of ``--embedding-prefer`` that needs ``OPENAI_API_KEY``.
PROTOCOL_EMBEDDING_PREFER = "openai"
PROTOCOL_JUDGE = "gpt-4o-mini"

#: The four controls, mapped to the sentence explaining why drifting off one
#: invalidates the comparison rather than merely complicating it. Keys are the
#: declaration names a run reports in its ``meta`` block.
PROTOCOL_CONTROLS: Dict[str, str] = {
    "llm_model": (
        f"the backbone must be {PROTOCOL_BACKBONE} for memory construction AND "
        f"answer generation — a different model measures the model, not the memory"
    ),
    "embedding_model": (
        f"retrieval must run on {PROTOCOL_EMBEDDING_MODEL} — a different embedder "
        f"makes every recall gap unattributable to the architecture"
    ),
    "judge": (
        f"accuracy must be judged by {PROTOCOL_JUDGE} — a different judge rescales "
        f"every score in the published table"
    ),
    "evidence_budget": (
        f"every compared method was given K={PROTOCOL_K} evidence items — more "
        f"context is a bigger answer, not a better memory"
    ),
}

#: What each control must equal. ``evidence_budget`` is compared as a string so
#: an int 10 and a string "10" agree, which is what a JSON round-trip produces.
PROTOCOL_VALUES: Dict[str, str] = {
    "llm_model": PROTOCOL_BACKBONE,
    "embedding_model": PROTOCOL_EMBEDDING_MODEL,
    "judge": PROTOCOL_JUDGE,
    "evidence_budget": str(PROTOCOL_K),
}


def protocol_blockers(meta: Mapping[str, Any]) -> List[str]:
    """Reasons this run's numbers may NOT be quoted next to published ones.

    :func:`evals.qa.scorer.fairness_blockers` was tried first and does not fit:
    it checks whether two runs *in this repo* declared the same thing, and the
    question here is whether ONE run matches a value fixed by somebody else's
    paper. Nothing in this repo holds the baselines' numbers — #178 retracted
    the last comparative claims that were not measured — so there is no second
    report to diff against, only a constant.

    A **missing** declaration is a blocker on the same terms as a wrong one:
    "we did not record which model answered" is not "the model matched".
    """
    blockers: List[str] = []
    for key, why in PROTOCOL_CONTROLS.items():
        declared = meta.get(key)
        if declared in (None, ""):
            blockers.append(f"{key}: not declared — {why}")
            continue
        if not _declaration_matches(key, str(declared)):
            blockers.append(
                f"{key}: this run used {declared}, the protocol fixes "
                f"{PROTOCOL_VALUES[key]} — {why}"
            )

    # Declarations are CLAIMS. Requiring evidence is what stops the gate being
    # satisfied by typing flags.
    #
    # Measured before this existed: a hand-written answers.json asserting all
    # four controls returned NO blockers, and the report then printed the
    # comparable section captioned "in the same units as the published table" —
    # with no key, no judge and no run. Meanwhile an HONEST run against the
    # real OpenAI embedder FAILED, because the backend names itself
    # "openai:text-embedding-3-small" while the constant is bare. The gate
    # rejected the truth and accepted the fiction.
    evidence = meta.get("evidence")
    if not isinstance(evidence, Mapping):
        blockers.append(
            "evidence: absent — every control above is an unverified claim. A "
            "run records what it actually did; a hand-written declaration "
            "cannot, and must not unlock a comparable table"
        )
        return blockers
    if not _as_int(evidence.get("judge_calls")):
        blockers.append(
            f"judge_calls: 0 — nothing scored these answers with "
            f"{PROTOCOL_JUDGE}. There is no judge implementation in this "
            "repository, so this control cannot currently be met by any run; "
            "declaring the model name is not judging"
        )
    if not _as_int(evidence.get("answer_calls")):
        blockers.append(
            "answer_calls: 0 — no answers were generated by this run"
        )
    return blockers


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _declaration_matches(key: str, declared: str) -> bool:
    """Does a declared value match the protocol's, allowing a provider prefix?

    ``active_embedding_backend("openai")`` names itself
    ``openai:text-embedding-3-small`` — the provider it resolved plus the model.
    The protocol fixes the MODEL. Comparing the raw strings made the honest run
    the only one that could never pass, which is the opposite of the gate's
    purpose. The prefix is stripped for the comparison; anything after the model
    name still fails.
    """
    want = PROTOCOL_VALUES[key]
    if declared == want:
        return True
    if key in _PREFIXED_CONTROLS and ":" in declared:
        provider, _, model = declared.partition(":")
        return bool(provider) and model == want
    return False


#: Controls whose declaration may legitimately carry a ``provider:`` prefix.
_PREFIXED_CONTROLS = frozenset({"embedding_model"})


# --------------------------------------------------------------------------
# Splitting a haystack into retrieval units
# --------------------------------------------------------------------------

#: The date header the MAB ``context`` puts at every session boundary.
_CHAT_TIME = re.compile(r"^\s*Chat Time:\s*(.+?)\s*$")


@dataclass(frozen=True)
class Session:
    """One dialogue session — the retrieval unit. See the module docstring."""

    index: int
    #: ``"2022/11/17 (Thu) 12:04"``, or ``""`` when the haystack view this
    #: session came from carried no date. Never a clock reading.
    date: str
    turns: Sequence[Mapping[str, Any]]

    @property
    def document_name(self) -> str:
        """Deterministic filename. Derived from the index alone — no clock, no
        hash of mutable state — so re-staging the same group is byte-identical.
        """
        return f"session-{self.index:04d}.md"

    def render(self) -> str:
        """The markdown document a compile reads.

        The date is repeated in the body and not left in the heading alone:
        BM25 and the embedding lane score the document text, and a date that
        only exists in a filename or a heading level is a date the
        ``temporal-reasoning`` stratum cannot retrieve.
        """
        header = f"# Session {self.index:04d}"
        lines = [header, ""]
        if self.date:
            lines += [f"Chat Time: {self.date}", ""]
        else:
            lines += ["Chat Time: not recorded in this haystack.", ""]
        for turn in self.turns:
            role = str(turn.get("role") or "unknown").strip()
            content = str(turn.get("content") or "").strip()
            # ``has_answer`` is the benchmark's gold-evidence marker. It is read
            # NOWHERE on this path: staging it into the document would let
            # retrieval key on "this is the answer" and score the leak.
            lines += [f"**{role}:**", "", content, ""]
        return "\n".join(lines).rstrip() + "\n"


#: The inverse of :attr:`Session.document_name`. ``\d{4,}`` rather than ``\d+``
#: so it matches what that property writes and nothing that merely looks like
#: it; the leading separator class lets a bare name, a relative path and an
#: absolute one all resolve.
_DOCUMENT_NAME = re.compile(r"(?:^|[\\/])session-(\d{4,})\.md$")


def document_index(name: Any) -> Optional[int]:
    """The session index behind a staged document name, or ``None``.

    The inverse of :attr:`Session.document_name`, and the only place either
    direction of that mapping is spelled out. A retrieved node carries its
    provenance as a ``source_path`` — ``corpus/session-0007.md``, or whatever
    absolute path the compile recorded — and a caller that wants the session
    back must not re-derive the format itself: two spellings of
    ``session-%04d.md`` that drift by one zero map every hit to nothing, and
    "no document matched" is indistinguishable from a memory that retrieved
    badly.

    Strict on purpose. Anything this module did not write — ``session-7.md``, a
    concept page, an empty path — is ``None``, and the caller counts it rather
    than resolving it to the nearest plausible index. A fabricated document
    index is a fabricated hit.
    """
    match = _DOCUMENT_NAME.search(str(name or "").strip())
    return int(match.group(1)) if match else None


def _sessions_from_context(context: str) -> Optional[List[Session]]:
    """Parse the ``context`` view: a flat literal alternating date and turns.

    Returns ``None`` — rather than raising — when the string is not that shape,
    so :func:`split_sessions` can fall back to the parsed view. Uses
    ``ast.literal_eval``, which evaluates literals only and never executes the
    corpus.
    """
    if not context.strip():
        return None
    try:
        parsed = ast.literal_eval(context)
    except (ValueError, SyntaxError, MemoryError, RecursionError):
        return None
    if not isinstance(parsed, list):
        return None

    sessions: List[Session] = []
    pending_date = ""
    for item in parsed:
        if isinstance(item, str):
            match = _CHAT_TIME.match(item)
            pending_date = match.group(1) if match else item.strip()
            continue
        if isinstance(item, list) and item and all(isinstance(t, dict) for t in item):
            sessions.append(Session(index=len(sessions), date=pending_date, turns=item))
            pending_date = ""
            continue
        return None  # an element that is neither a header nor a turn list
    return sessions or None


def _sessions_from_haystack(group: MabGroup) -> Optional[List[Session]]:
    """Flatten ``metadata.haystack_sessions``. Dateless — see :class:`MabGroup`."""
    flat: List[Session] = []
    for per_question in group.haystack_sessions:
        for session in per_question:
            turns = [t for t in session if isinstance(t, dict)]
            if turns:
                flat.append(Session(index=len(flat), date="", turns=turns))
    return flat or None


def split_sessions(group: MabGroup) -> List[Session]:
    """The group's dialogue as retrieval units, dates preferred.

    ``context`` first because it is the only view with ``Chat Time:`` headers;
    ``metadata.haystack_sessions`` is the fallback and yields dateless sessions.
    Raises when neither view produces anything: a group that stages zero
    documents would compile an empty project and score zero, and "the memory
    system cannot answer these questions" is the wrong thing for an empty
    corpus to look like.
    """
    sessions = _sessions_from_context(group.context) or _sessions_from_haystack(group)
    if not sessions:
        raise ValueError(
            f"group {group.index} ({group.source}) yielded no sessions: its "
            f"context did not parse as the MAB alternating-literal shape and "
            f"metadata.haystack_sessions was empty"
        )
    return sessions


def sessions_agree(group: MabGroup, sessions: Sequence[Session]) -> Optional[bool]:
    """Whether the two views of the haystack hold the same number of sessions.

    ``None`` when ``haystack_sessions`` is absent and there is nothing to
    cross-check. ``False`` is not fatal and does not stop a run — it is carried
    into the report, because a disagreement means one of the two views is not
    the whole haystack and a reader should know which numbers rest on it.
    """
    if not group.haystack_sessions:
        return None
    flat = sum(len(per_question) for per_question in group.haystack_sessions)
    return flat == len(sessions)


# --------------------------------------------------------------------------
# Ingest
# --------------------------------------------------------------------------


class RefusedToCompileInRepo(RuntimeError):
    """The work directory is inside this repository. See :func:`guard_work_dir`."""


def guard_work_dir(work: Path) -> Path:
    """Resolve ``work`` and refuse if a compile there would land in the repo.

    ``evals/growth/run.py`` makes the same check with ``sys.exit``; this raises
    instead, for one reason: ``sys.exit`` inside a library cannot be asserted on
    without catching ``SystemExit``, so growth's guard has never been executed
    by a test. This one is, and the test is what keeps it correct.

    Two conditions, because either alone is escapable: the path IS the repo (or
    lives under it), or it holds a ``pyproject.toml`` — a checkout somewhere
    else is still somebody's project to overwrite.
    """
    resolved = Path(work).expanduser().resolve()
    inside_repo = resolved == REPO or REPO in resolved.parents
    if inside_repo or (resolved / "pyproject.toml").exists():
        raise RefusedToCompileInRepo(
            f"refusing to compile inside the repo — that overwrites "
            f"{REPO / '.tesserae' / 'graph.json'} (asked for {resolved}); "
            f"pass a scratch directory such as ~/.blackhole/Tesserae/lme-mab/work"
        )
    return resolved


@dataclass
class IngestResult:
    """What one group's ingest put on disk, and what it cost in units."""

    group_index: int
    work: Path
    corpus_dir: Path
    documents: int
    turns: int
    chars: int
    dated_sessions: int
    #: ``"context"`` when the dated view parsed, ``"haystack_sessions"`` when
    #: the fallback was used. Named in the report: the two views differ on
    #: whether the temporal stratum is answerable at all.
    session_source: str
    #: :func:`sessions_agree` — ``None`` when there was nothing to cross-check.
    views_agree: Optional[bool]
    compiled: bool

    @property
    def approx_tokens(self) -> int:
        """Chars/4, as crude here as it is in :class:`MabGroup`."""
        return self.chars // 4


def _default_compile(work: Path) -> None:
    """``tesserae init`` then ``tesserae compile``, in ``work``.

    The checkout's own venv when there is one, else the running interpreter —
    the same resolution ``evals/growth/run.py`` settled on after hardcoding the
    first form killed every run inside a git worktree.
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


def evidence_text(node: Any) -> str:
    """One retrieved node as the evidence string handed to the backbone.

    Deliberately not :func:`tesserae.retrieval.hybrid._node_text`, which exists
    to feed the scoring lanes and includes the node id and every metadata pair.
    Those are retrieval features, not evidence: a backbone reading them spends
    its context on slugs.
    """
    parts = [str(getattr(node, "name", "") or "").strip()]
    description = str(getattr(node, "description", "") or "").strip()
    if description:
        parts.append(description)
    source_path = getattr(node, "source_path", None)
    if source_path:
        parts.append(f"source: {source_path}")
    return " — ".join(part for part in parts if part)


@dataclass(frozen=True)
class MabHit:
    """One retrieved node: the evidence text, and the document behind it.

    The two travel together because one search answers both questions — what
    the memory said, and which session it said it from. Recovering the
    provenance with a second search would not be the same search, and the
    retrieval comparison scores exactly the documents the answer was built on.
    """

    #: What the backbone reads. :func:`evidence_text` of the node.
    text: str
    #: The node's ``source_path``, ``""`` when it has none.
    source_path: str

    @property
    def document(self) -> Optional[int]:
        """The staged session index, or ``None`` when the node's provenance is
        not one of this adapter's documents. See :func:`document_index`."""
        return document_index(self.source_path)


class MabMemory:
    """A memory system under test: ingest a haystack, answer with evidence.

    ``compile_fn`` and ``search_fn`` are injection points and not decoration.
    Every test in this repo passes stubs for both, because the real pair is an
    hours-long LLM extraction and a metered embedding call, and a harness whose
    wiring can only be checked by running the benchmark does not get checked.
    """

    def __init__(
        self,
        *,
        compile_fn: Optional[Callable[[Path], None]] = None,
        search_fn: Optional[Callable[..., Any]] = None,
        backend: Any = None,
        embedding_prefer: str = "openai",
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
        self._graph: Any = None
        #: One entry per query that returned fewer than K items. Never padded —
        #: a padded evidence list would report a full budget the retrieval never
        #: filled, and K is the control the whole comparison rests on.
        self.shortfalls: List[Dict[str, Any]] = []
        #: Retrieved nodes whose provenance is not a staged session document.
        #: See :meth:`search_documents` — this is the size of the gap between
        #: what Tesserae retrieved and what its retrieval can be SCORED on.
        self.n_unmapped_hits = 0

    # ------------------------------------------------------------------ ingest

    def ingest(
        self,
        group: MabGroup,
        *,
        work: Path,
        compile_project: bool = True,
    ) -> IngestResult:
        """Stage the group as one document per session, then compile in ``work``.

        The corpus directory is removed and rebuilt so a re-run cannot inherit
        documents from a previous group — a stale ``session-0113.md`` from a
        larger group would be retrievable evidence from a haystack this run
        never saw.
        """
        resolved = guard_work_dir(work)
        sessions = split_sessions(group)

        corpus = resolved / "corpus"
        shutil.rmtree(corpus, ignore_errors=True)
        corpus.mkdir(parents=True, exist_ok=True)
        turns = chars = 0
        for session in sessions:
            body = session.render()
            (corpus / session.document_name).write_text(body, encoding="utf-8")
            turns += len(session.turns)
            chars += len(body)

        if compile_project:
            self._compile_fn(resolved)
        self.work = resolved
        self._graph = None  # a new corpus invalidates any graph already loaded

        return IngestResult(
            group_index=group.index,
            work=resolved,
            corpus_dir=corpus,
            documents=len(sessions),
            turns=turns,
            chars=chars,
            dated_sessions=sum(1 for s in sessions if s.date),
            session_source=(
                "context" if _sessions_from_context(group.context) else "haystack_sessions"
            ),
            views_agree=sessions_agree(group, sessions),
            compiled=compile_project,
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

        Public because the runner declares its ``name`` and ``dim`` into the
        report, and a declaration read from anywhere other than the live object
        is the failure ``evals/qa/benchmark_tesserae.py`` documents: a hardcoded
        one makes the control check pass while the run used something else.
        """
        if self._backend is not None:
            return self._backend
        from tesserae.retrieval.hybrid import active_embedding_backend

        # prefer="openai" is explicit-only and RAISES without OPENAI_API_KEY
        # rather than degrading to model2vec. That is the point: silently
        # falling back would vary the embedder and the architecture at once and
        # still print a number.
        self._backend = active_embedding_backend(self._embedding_prefer)
        return self._backend

    def _resolve_search(self) -> Callable[..., Any]:
        if self._search_fn is not None:
            return self._search_fn
        from tesserae.retrieval.hybrid import hybrid_search

        self._search_fn = hybrid_search
        return self._search_fn

    def query_hits(self, question: str, *, k: int = PROTOCOL_K) -> List[MabHit]:
        """Up to ``k`` hits for ``question``. Never more, never padded.

        The one search both :meth:`query` and :meth:`search_documents` are
        built on, so the evidence a run answers from and the documents its
        retrieval is scored on can never come from two different rankings.
        Fewer than ``k`` is recorded in :attr:`shortfalls` and returned short.
        Padding to length — with blanks, with repeats, with lower-ranked
        anything — would make an under-filled evidence budget indistinguishable
        from a full one, and the budget is the control every compared method
        shares.
        """
        result = self._resolve_search()(
            self._resolve_graph(),
            question,
            top_k=k,
            weights=self._weights,
            mode=self._mode,
            backend=self.embedding_backend(),
            # The extraction pipeline builds a node's searchable text from its
            # name and description, so a 14k-character chat session was
            # retrievable only through 88-character concept summaries. Handing
            # the lexical lanes the session file itself recovers that loss:
            # recall@10 0.705 -> 0.823, MRR 0.584 -> 0.721 on group 0. Confined
            # to the work directory, which is where this harness staged the
            # sessions and the only tree its source_paths may name.
            source_root=self.work,
        )
        hits = [
            MabHit(
                text=evidence_text(scored.node),
                source_path=str(getattr(scored.node, "source_path", "") or ""),
            )
            for scored in result.scored
        ][:k]
        if len(hits) < k:
            self.shortfalls.append({
                "question": question,
                "requested": k,
                "returned": len(hits),
                "total_matches": int(getattr(result, "total_matches", 0) or 0),
            })
        return hits

    def query(self, question: str, *, k: int = PROTOCOL_K) -> List[str]:
        """The evidence strings of :meth:`query_hits`. See it for the contract."""
        return [hit.text for hit in self.query_hits(question, k=k)]

    def search_documents(self, question: str, *, k: int = PROTOCOL_K) -> List[int]:
        """The session indices behind :meth:`query_hits`, ranked and de-duplicated.

        The same shape ``baselines.LexicalArm`` and ``baselines.DenseArm``
        return, which is what lets one scorer measure all three arms. Two hits
        from one session are one document at their FIRST rank: a node and its
        neighbour are not two pieces of evidence about where the answer lives.
        So this can return fewer than ``k`` documents from a full ``k`` hits —
        ten nodes may come from four sessions — and that is the budget doing
        its job rather than a shortfall to fix. K is the evidence the backbone
        reads, and topping the list up to ten DISTINCT sessions would hand this
        arm a bigger budget than the baselines got.

        **This is a LOWER BOUND on Tesserae's retrieval, and the report must
        say so.** A node keeps one ``source_path``, and
        ``tesserae.canonicalization.merge_node_group`` keeps the canonical
        node's when it collapses a concept that was extracted from many
        sessions — so a concept mentioned in twenty sessions points at one of
        them, and the other nineteen are structurally unreachable through
        provenance no matter how well the memory retrieved. Hits that map to no
        staged document at all (a node the compile gave no source, a page this
        adapter did not write) are counted in :attr:`n_unmapped_hits` and
        dropped. Neither case is guessed at: a hit resolved to a nearby session
        index would be a fabricated retrieval, which scores better than the
        honest number and means nothing.
        """
        return self.documents_of(self.query_hits(question, k=k))

    def documents_of(self, hits: Sequence[MabHit]) -> List[int]:
        """The mapping half of :meth:`search_documents`, over hits already read.

        Split out so a run that ANSWERS and scores retrieval derives both from
        one :meth:`query_hits` call. Calling ``query`` and ``search_documents``
        for the same question searches twice, records the shortfall twice, and
        scores a ranking the answer never read.
        """
        documents: List[int] = []
        for hit in hits:
            index = hit.document
            if index is None:
                self.n_unmapped_hits += 1
                continue
            if index not in documents:
                documents.append(index)
        return documents


__all__ = [
    "IngestResult",
    "MabHit",
    "MabMemory",
    "PROTOCOL_BACKBONE",
    "PROTOCOL_CONTROLS",
    "PROTOCOL_EMBEDDING_DIM",
    "PROTOCOL_EMBEDDING_MODEL",
    "PROTOCOL_JUDGE",
    "PROTOCOL_K",
    "PROTOCOL_VALUES",
    "REPO",
    "RefusedToCompileInRepo",
    "Session",
    "document_index",
    "evidence_text",
    "guard_work_dir",
    "protocol_blockers",
    "sessions_agree",
    "split_sessions",
]
