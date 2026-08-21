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
        lines = [f"# {document_title(self.index)}", ""]
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


def document_title(index: int) -> str:
    """The H1 of the staged document for session ``index``.

    Written by :meth:`Session.render` and read back by
    :attr:`MabHit.is_document_anchor`, and spelled out here so those two cannot
    drift. A compile records this string as the name of the node that STANDS
    FOR the document — ``tesserae.research_graph`` names a document anchor
    ``extract_title(text, source_path)``, which for these documents is exactly
    this heading — so equality with it is what distinguishes the anchor from
    every other node the same file produced.
    """
    return f"Session {index:04d}"


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
    #: True when a compiled graph already in ``work`` was reused instead of
    #: rebuilt. ``compiled`` is then False — no compile ran in THIS run — and
    #: the two flags together separate "never compiled" from "compiled
    #: earlier", which the report must not print as the same thing.
    reused: bool = False

    @property
    def approx_tokens(self) -> int:
        """Chars/4, as crude here as it is in :class:`MabGroup`."""
        return self.chars // 4



def _graph_missing_sessions(graph_path: Path, corpus: Path) -> set:
    """Staged session documents that the compiled graph does not index.

    Empty means every document under ``corpus`` is reachable through some
    node's ``source_path``. Compares BASENAMES: the compile records absolute
    paths, and a work dir moved between runs would otherwise read as foreign
    when it is merely relocated.
    """
    import json as _json

    try:
        payload = _json.loads(graph_path.read_bytes())
    except (OSError, ValueError):
        # An unreadable graph is not a mismatch; the caller's is_file check
        # already passed, so let the normal load path report it.
        return set()
    indexed = set()
    for node in payload.get("nodes") or []:
        sp = node.get("source_path") if isinstance(node, dict) else None
        if sp:
            indexed.add(Path(str(sp)).name)
    staged = {p.name for p in corpus.glob("*.md")}
    return staged - indexed

def _verify_staged(corpus: Path, sessions: Sequence[Any]) -> tuple:
    """``(turns, chars)`` of ``sessions``, having proved they are already staged.

    Raises unless every session renders byte for byte to the file already in
    ``corpus``, and unless ``corpus`` holds nothing else. Both halves matter:
    a CHANGED document means the compiled graph was built from text this run
    would not stage, and an EXTRA document means the graph indexes a session
    this group does not contain — retrievable evidence from a haystack the
    questions were never asked about. Either way the reused graph is not this
    group's graph, and the run must stop rather than report a number for it.
    """
    if not corpus.is_dir():
        raise FileNotFoundError(
            f"--reuse-compile: no staged corpus at {corpus}. There is nothing "
            f"to reuse; run without the flag to stage and compile."
        )
    turns = chars = 0
    mismatched: List[str] = []
    for session in sessions:
        body = session.render()
        staged = corpus / session.document_name
        if not staged.is_file():
            mismatched.append(f"{session.document_name} (missing)")
        elif staged.read_bytes() != body.encode("utf-8"):
            mismatched.append(f"{session.document_name} (differs)")
        turns += len(session.turns)
        chars += len(body)
    extra = sorted(
        p.name for p in corpus.glob("*.md")
        if p.name not in {s.document_name for s in sessions}
    )
    if mismatched or extra:
        raise ValueError(
            f"--reuse-compile: {corpus} is not this group's corpus — "
            f"{len(mismatched)} document(s) missing or changed"
            f"{': ' + ', '.join(mismatched[:5]) if mismatched else ''}"
            f"{f'; {len(extra)} unexpected: ' + ', '.join(extra[:5]) if extra else ''}. "
            f"The compiled graph there answers about a different haystack. "
            f"Re-run without --reuse-compile to rebuild it."
        )
    return turns, chars


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


#: How much of a document anchor's OWN session file joins its ANSWERING
#: evidence. Deliberately a separate constant from
#: :data:`tesserae.retrieval.hybrid.SOURCE_LEXICAL_CHARS` (8,000), because
#: ranking and answering are different questions and sharing the number would
#: couple the reproducible half of this benchmark to the unstable one.
#:
#: Ranking needs a query term to appear ANYWHERE in the scored prefix, and term
#: presence saturates: a topically coherent session places itself on its first
#: few thousand characters. Answering needs the answer SPAN itself, which is a
#: different quantity — a session that ranks first on a term in its opening
#: paragraph, with the answer at character 11,000, is a perfect retrieval and an
#: unanswerable prompt.
#:
#: 2,400 is where three independent derivations land, within 5% of each other:
#:
#: * this corpus's own mean ROUND is 2,388 characters (642 rounds measured on
#:   group 0; median 2,483, p90 3,300);
#: * a round is the evidence unit of the published benchmark's best
#:   configuration — arXiv:2410.10813 §E.5 provides "top-10 items" as rounds,
#:   and §5.2 measures that replacing them with summaries or facts LOSES, which
#:   is the substitution this adapter was making at 234 characters a hit;
#: * ``tesserae.ask_planner._EVIDENCE_CLIP`` is 2,500 — the house figure for
#:   chars per evidence block fed to synthesis.
#:
#: The cost is REAL, and its size is NOT established. A gold-answer survival
#: ladder once stood here (53 verbatim golds, 71.7% surviving 2,400 chars,
#: 84.9% at 4,000, 92.5% at 8,000). It does not reproduce: an independent
#: review measured 36 verbatim and 58.3% under three normalisations, and a
#: third implementation found 0 verbatim. Three methods, three answers — which
#: means "appears verbatim in its aligned session" is not a well-defined
#: predicate at this precision, not that one count is right. The numbers are
#: removed rather than replaced with whichever is newest.
#:
#: What survives that disagreement: truncation loses answer spans the ranking
#: cap never paid for, so 4,000 is the sensitivity arm if answering ever comes
#: out below what retrieval predicts. What it buys is a budget
#: that stays the same ORDER as the published top-10-rounds one rather than six
#: times it: measured over the same 60 questions, K=10 costs 22,800 prompt
#: characters on average (max 26,629) against 2,420 before.
#:
#: Truncation is from the FRONT because the answer is front-loaded here: gold
#: offsets have median 268 and the benchmark's own ``has_answer`` turn has mean
#: relative position 0.15, median 0.00. No smarter windowing is justified by
#: that distribution.
EVIDENCE_SOURCE_CHARS = 2_400


def evidence_text(node: Any) -> str:
    """One retrieved node as the evidence string handed to the backbone.

    Deliberately not :func:`tesserae.retrieval.hybrid._node_text`, which exists
    to feed the scoring lanes and includes the node id and every metadata pair.
    Those are retrieval features, not evidence: a backbone reading them spends
    its context on slugs.

    This is the NODE's own text and stops there. The session behind it is
    appended only on the answering path, by :meth:`MabMemory.answer_evidence`,
    and only for the node that IS the session — see :data:`EVIDENCE_SOURCE_CHARS`.
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

    #: The node's own evidence string. :func:`evidence_text` of the node, and
    #: what the backbone reads unless :meth:`MabMemory.answer_evidence` expands
    #: it.
    text: str
    #: The node's ``source_path``, ``""`` when it has none.
    source_path: str
    #: The node's ``name``. Carried because it is the only field that separates
    #: the node that IS a document from the ~103 nodes that merely inherited its
    #: path — see :attr:`is_document_anchor`.
    name: str = ""

    @property
    def document(self) -> Optional[int]:
        """The staged session index, or ``None`` when the node's provenance is
        not one of this adapter's documents. See :func:`document_index`."""
        return document_index(self.source_path)

    @property
    def is_document_anchor(self) -> bool:
        """Does this node STAND FOR its ``source_path``, or merely come from it?

        The distinction decides who gets the session text on the answering path,
        and getting it wrong is the failure
        ``hybrid._SOURCE_ANCHOR_TYPES``' docstring documents for the ranking
        side: every node extracted from one document would carry that
        document's entire contents, so eleven concepts from one chat become
        eleven identical 22kB evidence items and the budget collapses to one
        session.

        **Node TYPE cannot make this call on this graph, and the measurement
        says so.** ``hybrid._SOURCE_ANCHOR_TYPES`` matches 214 nodes of the
        compiled group-0 graph — 127 ``SourceDocument``, 60 ``Project``, 17
        ``Repository``, 10 ``Paper`` — and all 214 carry a
        ``session-NNNN.md`` path, because every node in this graph came out of
        a chat transcript. Only 111 of them are the transcripts. The other 103
        are things somebody talked about: "20-Gallon Community Tank",
        "AuctionZip", and 16 ``SourceDocument`` nodes that are books mentioned
        in a chat ("Banksy: Wall and Piece", ``source_path`` session-0015.md).
        ``session-0077.md`` alone mints 11 of them. Under the lexical lane 219
        of 600 retrieved hits are such impostors — a third of all evidence.

        Identity with the file is the test that works: the compile names a
        document's anchor after the document's own H1, and
        :func:`document_title` is that H1. Measured on the compiled group-0
        graph, this rejects all 103 impostors and admits all 111 anchors.

        It admits **128** nodes, not 111, and that overshoot is why
        :meth:`MabMemory.answer_evidence` de-duplicates. Seventeen sessions also
        carry a ``Session``-typed summary node named exactly ``Session NNNN``
        beside their ``SourceDocument`` anchor — same name, same file — so both
        pass this test. They are still 128 nodes over 111 distinct files, and
        the property that matters is per FILE, not per node: over group 0's 60
        real queries, 11 of them retrieved two such twins in one top-10 and
        would have spent 12 of 600 evidence items on bytes the prompt already
        held.

        Metadata is not an alternative: those 111 anchors carry 16 distinct
        metadata key-sets (``chat_time``, ``chatTime``, ``chat_date``,
        ``session_date``, ...). It is LLM-extracted and inconsistent by
        construction.
        """
        index = self.document
        return index is not None and self.name == document_title(index)


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
        reuse_compiled: bool = False,
    ) -> IngestResult:
        """Stage the group as one document per session, then compile in ``work``.

        The corpus directory is removed and rebuilt so a re-run cannot inherit
        documents from a previous group — a stale ``session-0113.md`` from a
        larger group would be retrievable evidence from a haystack this run
        never saw.

        ``reuse_compiled`` measures against a graph a PREVIOUS run compiled,
        which is the only way to re-measure a group without paying its compile
        again. It writes nothing: rather than rebuild the corpus it VERIFIES
        that every document this group would stage is already on disk byte for
        byte, and raises if one differs. That check is the whole safety of the
        flag — a graph compiled from a different corpus would answer questions
        about a haystack this run never staged, and would look like a valid
        measurement while doing it. It overrides ``compile_project``.
        """
        resolved = guard_work_dir(work)
        sessions = split_sessions(group)

        corpus = resolved / "corpus"
        if reuse_compiled:
            turns, chars = _verify_staged(corpus, sessions)
            graph_path = resolved / ".tesserae" / "graph.json"
            if not graph_path.is_file():
                raise FileNotFoundError(
                    f"--reuse-compile: no compiled graph at {graph_path}. There "
                    f"is nothing to reuse; run without the flag to compile."
                )
            # Verifying the CORPUS is not verifying the GRAPH. ``ingest``
            # restages the corpus BEFORE compiling, so a work dir can hold
            # group 1's freshly staged documents beside group 0's graph, and
            # the corpus check passes on both. Reuse would then report
            # "reused (earlier run)" while retrieving from a different
            # haystack than the one being scored — silently, which is the
            # exact failure this flag's docstring claims to prevent.
            #
            # Tie them: every session document must be reachable as a
            # source_path in the graph. Cheap (one load, a set difference) and
            # it fails loudly rather than scoring the wrong corpus.
            _missing = _graph_missing_sessions(graph_path, corpus)
            if _missing:
                raise ValueError(
                    f"--reuse-compile: the graph at {graph_path} does not index "
                    f"{len(_missing)} of the {len(sessions)} staged session "
                    f"documents (e.g. {sorted(_missing)[:3]}). It was compiled "
                    f"from a different group or an older corpus; recompile."
                )
        else:
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
            # recall@10 0.705 -> 0.820, MRR 0.584 -> 0.707 on group 0. Confined
            # to the work directory, which is where this harness staged the
            # sessions and the only tree its source_paths may name.
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
                "requested": k,
                "returned": len(hits),
                "total_matches": int(getattr(result, "total_matches", 0) or 0),
            })
        return hits

    def query(self, question: str, *, k: int = PROTOCOL_K) -> List[str]:
        """The evidence strings of :meth:`query_hits`. See it for the contract.

        The NODE strings, unexpanded. Answering reads
        :meth:`answer_evidence` instead; this stays cheap so the retrieval path
        does no file I/O it has no use for.
        """
        return [hit.text for hit in self.query_hits(question, k=k)]

    def answer_evidence(self, hits: Sequence[MabHit], *,
                        expand: bool = True) -> List[str]:
        """``hits`` as the strings the BACKBONE reads — the answering path only.

        This closes a measured hole. ``query_hits`` passes ``source_root`` so
        the lexical lanes rank a document anchor on the first 8,000 characters
        of its own session file, and that text then dies as a local inside
        ``hybrid_search``: the string that reached the backbone was
        ``evidence_text``'s ``name — description — source: path``, measured over
        group 0's 600 retrieved hits at mean 234.2 characters against source
        files averaging 14,042. The backbone was reading **1.7% of the text the
        retriever scored**, which is
        ``tesserae.context_compiler``'s "the graph was contributing essentially
        no text the prompt did not already have" transposed to the answering
        side — except worse, because the prompt here has nothing else at all.
        arXiv:2410.10813 §5.2 measured that exact substitution and found it
        loses to raw dialogue text, so the arm was running the losing side of a
        published ablation.

        Two properties, both deliberate:

        * **Only anchors expand, and each file at most once.** See
          :attr:`MabHit.is_document_anchor`. A concept node keeps its summary,
          which is honestly all the text it has. And a session's text goes to
          the FIRST hit that stands for it, on the same rule
          :meth:`documents_of` scores by — two nodes from one session are one
          session, at the better rank. Without that, the 17 sessions carrying
          both a ``SourceDocument`` anchor and a ``Session`` summary node of
          the same name pay twice: measured, 11 of group 0's 60 questions
          retrieved such a pair and would have spent 12 of 600 evidence items
          re-sending bytes the prompt already had. No two evidence items in one
          prompt can now be the same bytes, which is the whole point of the
          gate.
        * **Only the answering path calls this.** ``query_hits``,
          :meth:`query`, :meth:`documents_of` and :meth:`search_documents` are
          untouched, so recall@K and MRR cannot move by a byte and
          ``--retrieval-only`` reads no session file it will not use.

        ``source_path`` is UNTRUSTED — it arrives from document frontmatter —
        and this side is where it matters most: ranking buries a stolen file in
        a BM25 score, answering pastes it verbatim into an LLM prompt. So the
        read goes through ``hybrid._confined_source``, rooted at the work
        directory this adapter staged into, rather than a hand-rolled open().

        ``expand=False`` returns ``[hit.text ...]`` — precisely what the
        backbone read before this method existed. It is here so the two
        evidence CONTENTS can be measured against each other over one frozen
        retrieval, in one process, on one tree. The alternative was checking
        out the parent commit to obtain the control arm, which moves the
        retrieval code underneath the comparison and makes any difference
        un-attributable. The closing of this hole was argued from a published
        ablation and from character counts, never measured on this corpus's own
        answers, so the control has to stay reachable. Nothing selects it by
        default and it is not a fallback: `run.py --answer-evidence summary`
        is the only caller.
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
    "EVIDENCE_SOURCE_CHARS",
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
    "document_title",
    "evidence_text",
    "guard_work_dir",
    "protocol_blockers",
    "sessions_agree",
    "split_sessions",
]
