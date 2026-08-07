"""Per-agent distillation pass — the Phase-2 core of the layered agent KG.

Spec: docs/superpowers/specs/2026-07-19-layered-agent-kg.md §5-§7. Reads the
L0 project graph and writes ONE canonicalized artifact per agent under
``.tesserae/agents/<agent_key>/distilled.graph.json`` — never touching
``graph.json`` itself (L0 is sealed against distillation, §2). The pass is
**organize / compact / polish / refine / forget**:

1. *Scope* (§5.1) — deterministic monotone closure: the agent's Sessions (via
   ``performed_by`` edges already minted by ``session_graph_structural``) →
   findings via ``derived_from_session`` → ≤2-hop sorted BFS over an
   allowlisted typed-edge set. Explicitly NOT PPR.
2. *Cluster* (§5.2) — ``memory/distill.py``'s union-find + Jaccard-on-names +
   ``supersedes``-edge signals, hardened for scale: token inverted index for
   candidate generation, memoized tokenise, stopword/domain-token filter,
   100-member cap split by session-time buckets, assignment memoized in the
   ``agent_distill_state`` sidecar table.
3. *Compact & polish* (§5.3) — the only paraphrase step. The summarizer is an
   **injected callable** (see :data:`Summarizer`); :class:`LLMSummarizer` is
   the default LLM-backed implementation — bounded ``complete_json``
   map-reduce over the ``pack_blocks`` chunks of an
   :class:`~tesserae.llm_json.LLMJsonClient` (build one via
   :func:`build_llm_summarizer`). Output is validated
   (typed schema, citation whitelist, deterministic faithfulness lint) and
   cached content-keyed under ``.tesserae/distill_cache/``. Failures fall back
   to a deterministic title-concatenation body and the fallback verdict is
   ALSO cached (``{"fallback": true}``) so two runs over identical inputs never
   flip bytes just because the provider recovered.
4. *Mint* (§5.4) — one ``DistilledNote`` per qualifying cluster with
   ``lineage_key`` identity, verbatim anchor-node copies, and metadata drawn
   exactly from the closed §4 allowlists.
5. *Emit & forget* (§5.5, §6) — Agent node + ``reports_to``, structural
   ``ExpertiseProfile``, distillates + anchors, raw remainder (top-K with the
   mandated ``(-recurring_confidence, node_id)`` tiebreak + hysteresis),
   structural Index and Activity notes. Forgetting is absorption or
   demote-to-index — NEVER deletion; every run appends a deterministic diff to
   the append-only forget ledger in ``agent_distill_state``.

Determinism contract (§7): no ``datetime.now()`` / RNG / counters anywhere in
artifact bytes — lifecycle math runs at the **corpus clock** (max session
timestamp in scope; ``--as-of`` override only). All mutable bookkeeping
(watermarks, negative cache, cluster memo, LRU cache tracking, forget ledger)
lives in the ``agent_distill_state`` table of the existing
``.tesserae/sqlite.db`` sidecar. The connection pattern is copied from
:mod:`tesserae.graph_stores.sqlite` (short-lived connect, static
``_ensure_schema``) — NOT imported, per the ``harness_sessions_db`` precedent.

This module COEXISTS with :mod:`tesserae.memory.distill` (the Runbook/Gotcha
pass): that pass writes INTO the compiled project graph under
``TESSERAE_RUNBOOK_DISTILLATION``; this one writes OUTSIDE it, per agent,
under ``TESSERAE_AGENT_DISTILL``. Only the cluster-signal helpers are shared.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import sqlite3
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import (
    Callable,
    Dict,
    List,
    Mapping,
    Optional,
    Sequence,
    Set,
    Tuple,
)

from .agent_identity import ORG_ROOT, AgentRegistry, sanitize_agent_key
from .context_compiler import fit_to_budget
from .llm_chunking import chunk_char_budget, pack_blocks
from .llm_json import LLMJsonClient, build_default_json_client
from .memory.decay import compute_decay_score
from .memory.distill import _UnionFind
from .memory.reinforce import compute_recurring_confidence
from .memory.store import NodeMemoryRow, read_memory
from .memory.supersede import _tokenise as _base_tokenise
from .memory.supersede import jaccard
from .research_graph import (
    SESSION_FINDING_TYPES,
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
    graph_from_payload,
    stable_id,
)
from .session_graph_structural import _agent_metadata

logger = logging.getLogger(__name__)

__all__ = [
    "ARTIFACT_CHAR_BUDGET",
    "DistillError",
    "DistillSizeError",
    "DistillOptions",
    "DistillRequest",
    "DistillResult",
    "DistillStateStore",
    "LLMSummarizer",
    "Summarizer",
    "SummarizerTransportError",
    "agent_artifact_path",
    "agent_distill_enabled",
    "build_llm_summarizer",
    "compute_lineage_key",
    "distill_agent",
    "distill_all",
    "distill_cache_dir",
    "artifact_size_level",
    "set_agent_distill_test_client",
]

# --------------------------------------------------------------------------- constants

#: Cache envelope schema version — bump with ``--recheck`` when the validation
#: contract tightens (§5.3).
CACHE_SCHEMA_VERSION = 1

#: Version of the distill prompt contract the summarizer implements. Part of
#: the cache key so prompt changes invalidate cached outputs.
PROMPT_VERSION = 1

#: Jaccard threshold shared with ``memory.distill`` / ``memory.reinforce`` so
#: all three passes agree on cluster shape.
_NEAR_DUP_THRESHOLD = 0.55

#: §5.1 typed-edge allowlist for the ≤2-hop closure, mapped onto the real
#: edge vocabulary of ``ALLOWED_EDGE_TYPES`` (the spec's ``mentions``/``about``
#: spell as ``mentioned_in`` / ``discussed_in`` / ``references`` here).
#:
#: ``discusses`` is deliberately absent. Only the insight↔symbol linker ever
#: minted it, always finding→code-symbol, and this pass never had a code
#: symbol to reach: it walks the research layer. Every such edge in the
#: compiled store dangled anyway — 15,873 of 15,873 — so the hop it bought
#: here was always a hop to nothing.
_SCOPE_EDGE_TYPES: Set[str] = {
    "derived_from",
    "supersedes",
    "part_of",
    "references",
    "discussed_in",
    "mentioned_in",
}

#: §2/§7.2 one-read bound for the rendered L1 artifact. A module constant on
#: purpose: ``chunk_char_budget()`` honors the ``TESSERAE_LLM_CHUNK_CHARS``
#: env var, and an env var is NOT a §7.2 declared input — two checkouts with
#: different values must never render different artifact bytes. The env knob
#: keeps steering LLM chunk *packing* only.
ARTIFACT_CHAR_BUDGET = 48_000

#: Edge set folded into the watermark ``input_hash`` (§7.2): everything whose
#: change alters a fresh run's bytes — anchor selection, profile top_concepts,
#: supersede winners, clustering signals, and the session/finding partition.
_WATERMARK_EDGE_TYPES: Set[str] = _SCOPE_EDGE_TYPES | {
    "derived_from_session",
    "performed_by",
}

#: Cluster size cap (§5.2) — oversized clusters split deterministically by
#: session-time buckets.
_CLUSTER_SIZE_CAP = 100

#: Mixed into the cluster-assignment memo key — bump when the clustering
#: contract changes (_NEAR_DUP_THRESHOLD, _STOPWORD_TOKENS,
#: _TOKEN_POSTING_CAP, _CLUSTER_SIZE_CAP or the signal set) so stale memo
#: rows cannot survive an algorithm change.
_CLUSTERING_VERSION = 2

#: Tokens too generic to generate candidate pairs — they would chain
#: mega-clusters (§5.2). Applied on top of the ``supersede._tokenise`` split.
_STOPWORD_TOKENS: Set[str] = {
    "about", "add", "added", "after", "all", "also", "and", "are", "ateach",
    "because", "before", "but", "can", "change", "changed", "code", "did",
    "does", "file", "files", "fix", "fixed", "for", "from", "has", "have",
    "into", "its", "make", "more", "new", "not", "now", "one", "only",
    "session", "sessions", "should", "test", "tests", "than", "that", "the",
    "their", "them", "then", "они", "this", "update", "updated", "use",
    "used", "uses", "using", "was", "were", "when", "which", "will", "with",
}

#: Posting-list cap — a token carried by more nodes than this is treated as a
#: domain stopword for candidate generation (deterministic; §5.2 hardening).
_TOKEN_POSTING_CAP = 64

#: Anchor node types a distillate may cite into the artifact verbatim (§5.4).
#: ``Repository`` is a document type (the page a repo-shaped source compiles
#: to), not a code type. The five code-symbol entries this used to carry
#: (CodeFile / CodeModule / CodeClass / CodeFunction / CodeMethod) were dead
#: on arrival: this pass reaches anchors over ``_SCOPE_EDGE_TYPES`` from the
#: research layer, which never contained a code symbol.
_ANCHOR_TYPES: Set[ResearchNodeType] = {
    ResearchNodeType.CONCEPT,
    ResearchNodeType.REPOSITORY,
    ResearchNodeType.PAPER,
    ResearchNodeType.SOURCE_DOCUMENT,
}

#: Cap on verbatim anchor copies per distillate, by ``(-citations, id)``.
_ANCHOR_CAP = 40

#: Pitfall keywords for the structural ``kind`` hint handed to the summarizer.
_PITFALL_HINTS: Tuple[str, ...] = (
    "fail", "error", "gotcha", "avoid", "wrong", "broke", "pitfall", "regression",
)

#: §6.1 absorption gates: decay below this AND confidence below the promote bar.
_ABSORB_DECAY_BELOW = 0.2
_ABSORB_CONFIDENCE_BAR = 0.5

#: §6.2 hysteresis: enter the remainder at decay ≥ 0.3, demote only below 0.15.
_REMAINDER_ENTER_DECAY = 0.3
_REMAINDER_EXIT_DECAY = 0.15

#: Consecutive summarizer *transport* failures (exceptions) that trip the
#: circuit breaker and abort the LLM stage for the run (§5.3).
_CIRCUIT_BREAKER_LIMIT = 3

#: Cache entries unused for this many executed runs are pruned (LRU age, §6.3).
_CACHE_KEEP_RUNS = 20

_FALSY = {"0", "false", "no", "off"}

_GUIDANCE_DIGEST_EMPTY = hashlib.sha256(b"").hexdigest()


def _compute_guidance_digest(guidance: str) -> str:
    """Cache-fork key for a guidance stream (§12 Phase-5).

    Single source of truth for the digest folded into cluster-cache envelopes,
    the negative-cache key and the per-agent watermark, so an empty stream is
    always the ``_GUIDANCE_DIGEST_EMPTY`` sentinel and a non-empty edit forks
    only the agents whose combined text changed.
    """
    return (
        hashlib.sha256(guidance.encode("utf-8")).hexdigest()
        if guidance
        else _GUIDANCE_DIGEST_EMPTY
    )

# Faithfulness lint (§5.3): identifier / number / version / quoted-error
# tokens appearing in an LLM body must appear in some cited member.
_BACKTICK_RE = re.compile(r"`([^`\n]{2,})`")
_VERSION_RE = re.compile(r"\bv\d+[\w.\-]*\b", re.IGNORECASE)
_NUMBER_RE = re.compile(r"\b\d[\d.,]+\b")
_QUOTED_RE = re.compile(r"\"([^\"\n]{4,})\"")


# --------------------------------------------------------------------------- errors


class DistillError(RuntimeError):
    """Fail-loud error in the distill pass (corpus clock, ref resolution, ...)."""


class DistillSizeError(DistillError):
    """The rendered L1 artifact cannot fit the one-read size bound (§2/§7.2)."""


class SummarizerTransportError(RuntimeError):
    """The LLM backend gave no usable answer (outage / exhausted retries).

    Raised by :class:`LLMSummarizer` — the :class:`~tesserae.llm_json.LLMJsonClient`
    protocol itself never raises, it returns ``None``, so the wrapper converts
    that into an exception the ``_LLMStage`` counts toward the §5.3 circuit
    breaker. Deliberately NOT a :class:`DistillError`: it is caught and
    degraded to the cached fallback verdict, never propagated.
    """


# --------------------------------------------------------------------------- enablement


def agent_distill_enabled(
    cfg: Optional[dict] = None,
    env: Optional[Mapping[str, str]] = None,
) -> bool:
    """Opt-in gate for the agent distill pass (``TESSERAE_AGENT_DISTILL``).

    Mirrors :func:`tesserae.memory.distill.distillation_enabled` resolution:
    an explicit env spelling wins, then ``cfg["agent_distill"]["enabled"]``,
    else disabled. Deliberately a DIFFERENT env var than the Runbook/Gotcha
    pass — the two passes are independent features.
    """
    env = env if env is not None else os.environ
    raw = (env.get("TESSERAE_AGENT_DISTILL") or "").strip().lower()
    if raw:
        return raw not in _FALSY
    section = (cfg or {}).get("agent_distill")
    if isinstance(section, Mapping):
        flag = section.get("enabled")
        if isinstance(flag, str):
            return flag.strip().lower() not in _FALSY and bool(flag.strip())
        return bool(flag)
    return False


# --------------------------------------------------------------------------- paths


def agent_artifact_path(project_root: Path | str, agent_key: str) -> Path:
    """``.tesserae/agents/<sanitized_key>/distilled.graph.json``.

    The dirname is the sanitized agent key VERBATIM (colons included) —
    the same convention ``tesserae agents rename`` migrates
    (``registry.path.parent / <key>``), so renames carry distill artifacts.
    """
    root = Path(project_root)
    return (
        root / ".tesserae" / "agents" / sanitize_agent_key(agent_key) / "distilled.graph.json"
    )


def distill_cache_dir(project_root: Path | str) -> Path:
    """Shared, project-level cluster cache root (§5.3 — shared, not per-agent)."""
    return Path(project_root) / ".tesserae" / "distill_cache"


def _state_db_path(project_root: Path | str) -> Path:
    """The EXISTING ``.tesserae/sqlite.db`` sidecar (spec §4 'Persistence')."""
    return Path(project_root) / ".tesserae" / "sqlite.db"


# --------------------------------------------------------------------------- sidecar state


class DistillStateStore:
    """Accessor layer over the ``agent_distill_state`` sidecar table.

    Single home for ALL mutable distill bookkeeping — per-agent watermarks,
    the negative cache / backoff, the cluster-assignment memo, LRU cache-use
    tracking, and the append-only forget ledger. NEVER serialized into any
    graph artifact (§7.2). Modeled on :mod:`tesserae.memory.store`: no
    call-site SQL, short-lived connection per call (pattern copied from
    ``graph_stores/sqlite.py``, not imported).
    """

    # Row namespaces. Keyed scopes upsert on (scope, agent_key, key); ledger
    # scopes are append-only.
    SCOPE_WATERMARK = "watermark"
    SCOPE_NEGATIVE = "negative"
    SCOPE_CLUSTER_MEMO = "cluster_memo"
    SCOPE_CACHE_USE = "cache_use"
    SCOPE_FORGET_LEDGER = "forget_ledger"
    SCOPE_META = "meta"

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.path)
        self._ensure_schema(con)
        return con

    @staticmethod
    def _ensure_schema(con: sqlite3.Connection) -> None:
        con.execute(
            """
            create table if not exists agent_distill_state (
                id        integer primary key autoincrement,
                scope     text not null,
                agent_key text not null default '',
                key       text not null default '',
                value     text not null default ''
            )
            """
        )
        con.execute(
            "create index if not exists idx_agent_distill_state_lookup"
            " on agent_distill_state(scope, agent_key, key)"
        )

    # ---------------- keyed rows (upsert semantics) ----------------

    def get(self, scope: str, agent_key: str = "", key: str = "") -> Optional[str]:
        con = self._connect()
        try:
            row = con.execute(
                "select value from agent_distill_state"
                " where scope = ? and agent_key = ? and key = ?"
                " order by id desc limit 1",
                (scope, agent_key, key),
            ).fetchone()
            return row[0] if row else None
        finally:
            con.close()

    def put(self, scope: str, agent_key: str = "", key: str = "", value: str = "") -> None:
        con = self._connect()
        try:
            with con:
                con.execute(
                    "delete from agent_distill_state"
                    " where scope = ? and agent_key = ? and key = ?",
                    (scope, agent_key, key),
                )
                con.execute(
                    "insert into agent_distill_state (scope, agent_key, key, value)"
                    " values (?, ?, ?, ?)",
                    (scope, agent_key, key, value),
                )
        finally:
            con.close()

    # ---------------- append-only rows (ledger semantics) ----------------

    def append(self, scope: str, agent_key: str, value: str) -> None:
        con = self._connect()
        try:
            with con:
                con.execute(
                    "insert into agent_distill_state (scope, agent_key, key, value)"
                    " values (?, ?, '', ?)",
                    (scope, agent_key, value),
                )
        finally:
            con.close()

    def rows(self, scope: str, agent_key: Optional[str] = None) -> List[Tuple[int, str, str, str]]:
        """All rows in a scope, id-ordered — ``(id, agent_key, key, value)``."""
        con = self._connect()
        try:
            if agent_key is None:
                cur = con.execute(
                    "select id, agent_key, key, value from agent_distill_state"
                    " where scope = ? order by id",
                    (scope,),
                )
            else:
                cur = con.execute(
                    "select id, agent_key, key, value from agent_distill_state"
                    " where scope = ? and agent_key = ? order by id",
                    (scope, agent_key),
                )
            return [tuple(row) for row in cur.fetchall()]
        finally:
            con.close()

    # ---------------- run counter (LRU clock for cache GC) ----------------

    def bump_run_seq(self) -> int:
        raw = self.get(self.SCOPE_META, "", "run_seq")
        try:
            seq = int(raw) if raw is not None else 0
        except ValueError:
            seq = 0
        seq += 1
        self.put(self.SCOPE_META, "", "run_seq", str(seq))
        return seq

    def current_run_seq(self) -> int:
        raw = self.get(self.SCOPE_META, "", "run_seq")
        try:
            return int(raw) if raw is not None else 0
        except ValueError:
            return 0


# --------------------------------------------------------------------------- summarizer contract


@dataclass(frozen=True)
class DistillRequest:
    """One compact/polish request handed to the injected summarizer.

    ``members`` are ``(node_id, name, description)`` triples, id-sorted;
    ``chunks`` is the ``pack_blocks``-packed rendering (§5.3 bounded reads).
    ``mode`` is ``"distill"`` for a full pass or ``"fold"`` for the
    incremental refine step, in which case ``prior_output`` carries the prior
    cached note and ``new_members`` the members to fold in.

    The summarizer must return a mapping with keys ``kind`` (one of
    ``runbook`` / ``gotcha`` / ``note``), ``title``, ``body`` and
    ``citations`` (member node ids), or ``None`` on failure — the
    ``extract_with_llm`` drop-don't-crash contract. Any exception counts as a
    transport failure toward the circuit breaker.
    """

    agent_key: str
    lineage_key: str
    kind_hint: str
    members: Tuple[Tuple[str, str, str], ...]
    chunks: Tuple[str, ...]
    mode: str = "distill"
    prior_output: Optional[Mapping[str, object]] = None
    new_members: Tuple[Tuple[str, str, str], ...] = ()
    #: Combined per-agent guidance stream (§12 Phase-5) the summarizer should
    #: obey. Empty by default → prompts are byte-identical to pre-Phase-5.
    guidance: str = ""


Summarizer = Callable[[DistillRequest], Optional[Mapping[str, object]]]


# --------------------------------------------------------------------------- LLM summarizer (§5.3)

# The distill prompt (§5.3): organize, compact, polish; produce a
# runbook/gotcha/note; cite member ids. All three variants state the citation
# whitelist explicitly — the stage still enforces it after the fact.
_LLM_RESPONSE_SHAPE = (
    'Respond with JSON: {"kind": "runbook|gotcha|note", "title": "...", '
    '"body": "...", "citations": ["member id", "..."]}'
)

_LLM_MAP_SYSTEM = (
    "You distill a cluster of related agent-session findings into ONE "
    "higher-order note for an agent's knowledge base: organize, compact, "
    "polish. Write a reusable runbook, a gotcha (pitfall + how to avoid it), "
    "or a note. Cite the ids of the members you drew on — only ids from the "
    "provided list; never invent identifiers, numbers, versions or quoted "
    "strings that do not appear in the findings."
)

_LLM_REDUCE_SYSTEM = (
    "You merge partial distilled notes over ONE cluster of agent-session "
    "findings into a single final note. Organize, compact, polish; do not "
    "drop cited facts. Citations may only use ids from the provided list."
)

_LLM_FOLD_SYSTEM = (
    "You maintain a distilled note in an agent's knowledge base. Fold the "
    "new findings into the existing note; do not drop cited facts or "
    "existing citations. Citations may only use ids from the provided list."
)

#: Member blocks render as ``[id] name`` (see ``_render_member_block``), so a
#: chunk's own member ids are recoverable from its line starts.
_BLOCK_ID_RE = re.compile(r"(?m)^\[([^\]\n]+)\]")


def _normalize_note(payload: Mapping[str, object]) -> Dict[str, object]:
    """Project a note payload onto the 4-key contract for prompt rendering."""
    citations = payload.get("citations")
    return {
        "kind": str(payload.get("kind") or ""),
        "title": str(payload.get("title") or ""),
        "body": str(payload.get("body") or ""),
        "citations": (
            [str(c) for c in citations] if isinstance(citations, (list, tuple)) else []
        ),
    }


def _usable_note(payload: Mapping[str, object]) -> bool:
    """Structural floor for intermediate (map/fold) responses."""
    return bool(str(payload.get("title") or "").strip()) and bool(
        str(payload.get("body") or "").strip()
    )


def _planned_provider_calls(request: DistillRequest) -> int:
    """Provider calls one request costs: map per chunk + a reduce when >1.

    The unit for ``--max-llm-calls`` budgeting and ``--dry-run`` estimates. A
    fold threads sequentially (one call per chunk, no reduce). Charged up
    front and deterministically from the request shape — an early transport
    failure may make fewer wire calls than charged.
    """
    count = max(1, len(request.chunks))
    if request.mode == "fold":
        return count
    return count + (1 if count > 1 else 0)


class LLMSummarizer:
    """Default :data:`Summarizer` over an :class:`~tesserae.llm_json.LLMJsonClient`.

    §5.3's bounded reads: the stage packs member blocks with ``pack_blocks``
    at ``chunk_char_budget()``; this summarizer runs one ``complete_json``
    map call per chunk plus one reduce call when there is more than one
    chunk, and threads a fold request sequentially through the prior output
    ("fold these in; do not drop cited facts"). Cluster size never changes
    the per-call read size.

    Failure semantics follow the session_graph rule — a failed call is never
    surfaced as an empty success: any exception, ``None`` or non-mapping
    answer raises :class:`SummarizerTransportError`, which the stage counts
    toward the circuit breaker and records as a *fallback verdict*
    (re-attemptable via ``--retry-fallbacks``), never as an empty cached
    output. Content validation (typed schema, citation whitelist,
    faithfulness lint) stays in the stage; this class only guarantees a
    structurally usable mapping came back off the wire.
    """

    def __init__(self, client: LLMJsonClient, *, max_retries: int = 1) -> None:
        self.client = client
        self.max_retries = max_retries
        #: Exact wire calls made — provider-health reporting for the CLI.
        self.provider_calls = 0

    def __call__(self, request: DistillRequest) -> Optional[Mapping[str, object]]:
        chunks = [chunk for chunk in request.chunks if chunk.strip()]
        if request.mode == "fold" and request.prior_output is not None:
            return self._fold(request, chunks)
        return self._distill(request, chunks)

    # -- modes ---------------------------------------------------------------

    def _distill(
        self, request: DistillRequest, chunks: Sequence[str]
    ) -> Optional[Mapping[str, object]]:
        if not chunks:
            return None  # nothing to read — degrade, the stage records a failure
        if len(chunks) == 1:
            return self._ask(
                _LLM_MAP_SYSTEM, self._map_user(request, chunks[0]), "agent_distill_map"
            )
        partials: List[Mapping[str, object]] = []
        for chunk in chunks:
            partial = self._ask(
                _LLM_MAP_SYSTEM, self._map_user(request, chunk), "agent_distill_map"
            )
            if not _usable_note(partial):
                raise SummarizerTransportError(
                    "agent_distill_map: no usable partial note"
                )
            partials.append(partial)
        return self._ask(
            _LLM_REDUCE_SYSTEM, self._reduce_user(request, partials), "agent_distill_reduce"
        )

    def _fold(
        self, request: DistillRequest, chunks: Sequence[str]
    ) -> Optional[Mapping[str, object]]:
        current: Mapping[str, object] = request.prior_output or {}
        for chunk in chunks:
            current = self._ask(
                _LLM_FOLD_SYSTEM, self._fold_user(request, current, chunk), "agent_distill_fold"
            )
            if not _usable_note(current):
                raise SummarizerTransportError("agent_distill_fold: no usable note")
        return current

    # -- wire ----------------------------------------------------------------

    def _ask(self, system: str, user: str, schema_name: str) -> Mapping[str, object]:
        self.provider_calls += 1
        try:
            payload = self.client.complete_json(
                system=system,
                user=user,
                schema_name=schema_name,
                cache_key=f"agent-distill-v{PROMPT_VERSION}::{schema_name}",
                max_retries=self.max_retries,
            )
        except Exception as exc:  # noqa: BLE001 — protocol says never-raise; treat as transport
            raise SummarizerTransportError(f"{schema_name}: client raised") from exc
        if not isinstance(payload, Mapping):
            raise SummarizerTransportError(f"{schema_name}: no usable JSON answer")
        return payload

    # -- prompt rendering (pure functions of the request) --------------------

    @staticmethod
    def _guidance_lines(request: DistillRequest) -> List[str]:
        """Per-agent guidance block for the user prompt (§12 Phase-5).

        Empty when the agent has no stream, so a guidance-less request renders
        byte-identically to a pre-Phase-5 prompt.
        """
        guidance = request.guidance.strip()
        if not guidance:
            return []
        return ["Extraction guidance (obey this steering):", guidance, ""]

    @staticmethod
    def _citation_ids(request: DistillRequest, chunk: Optional[str] = None) -> List[str]:
        """The citation whitelist a call may use — chunk-scoped for map calls."""
        member_ids = [member_id for member_id, _name, _desc in request.members]
        if chunk is not None:
            member_id_set = set(member_ids)
            in_chunk = [
                found for found in _BLOCK_ID_RE.findall(chunk) if found in member_id_set
            ]
            if in_chunk:
                return sorted(set(in_chunk))
        return member_ids

    def _map_user(self, request: DistillRequest, chunk: str) -> str:
        return "\n".join(
            [
                f"Agent: {request.agent_key}",
                f"Preferred kind: {request.kind_hint}",
                "Valid citation ids: " + ", ".join(self._citation_ids(request, chunk)),
                "",
                *self._guidance_lines(request),
                "Findings:",
                chunk,
                "",
                _LLM_RESPONSE_SHAPE,
            ]
        )

    def _reduce_user(
        self, request: DistillRequest, partials: Sequence[Mapping[str, object]]
    ) -> str:
        rendered = json.dumps(
            [_normalize_note(partial) for partial in partials],
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        return "\n".join(
            [
                f"Agent: {request.agent_key}",
                f"Preferred kind: {request.kind_hint}",
                "Valid citation ids: " + ", ".join(self._citation_ids(request)),
                "",
                *self._guidance_lines(request),
                "Partial notes:",
                rendered,
                "",
                "Merge into ONE note. " + _LLM_RESPONSE_SHAPE,
            ]
        )

    def _fold_user(
        self, request: DistillRequest, current: Mapping[str, object], chunk: str
    ) -> str:
        return "\n".join(
            [
                f"Agent: {request.agent_key}",
                "Valid citation ids: " + ", ".join(self._citation_ids(request)),
                "",
                *self._guidance_lines(request),
                "Existing note:",
                json.dumps(
                    _normalize_note(current), ensure_ascii=False, indent=2, sort_keys=True
                ),
                "",
                "New findings:",
                chunk,
                "",
                "Fold the new findings in. " + _LLM_RESPONSE_SHAPE,
            ]
        )


_TEST_CLIENT: Optional[LLMJsonClient] = None


def set_agent_distill_test_client(client: Optional[LLMJsonClient]) -> None:
    """Inject a fake JSON client for tests (mirrors ``memory.distill``)."""
    global _TEST_CLIENT
    _TEST_CLIENT = client


def build_llm_summarizer(
    client: Optional[LLMJsonClient] = None,
    *,
    model: Optional[str] = None,
    provider: Optional[str] = None,
) -> Optional[LLMSummarizer]:
    """Best-available LLM summarizer, or ``None`` (structural-only pass).

    Resolution: explicit ``client`` → injected test client →
    :func:`~tesserae.llm_json.build_default_json_client` (Claude CLI → SDK →
    Codex CLI). ``None`` keeps the pass functional — every cluster then takes
    the deterministic fallback path and is counted in ``estimated_llm_calls``.
    """
    resolved = (
        client
        or _TEST_CLIENT
        or build_default_json_client(model=model, provider=provider)
    )
    if resolved is None:
        return None
    return LLMSummarizer(resolved)


# --------------------------------------------------------------------------- options / result


@dataclass
class DistillOptions:
    """API-level knobs mirroring the ``tesserae distill`` CLI surface (§5)."""

    min_cluster_size: int = 2
    remainder_top_k: int = 50
    max_llm_calls: Optional[int] = None
    dry_run: bool = False
    full: bool = False
    retry_fallbacks: bool = False
    recheck: bool = False
    as_of: Optional[str] = None
    jobs: int = 1  # accepted for CLI parity; execution is sequential here
    guidance: str = ""  # Phase-5 per-agent guidance; forks the cache when set
    char_budget: Optional[int] = None  # LLM chunk-packing override (tests)
    artifact_char_budget: Optional[int] = None  # §2 one-read bound override (tests)


@dataclass
class DistillResult:
    """Outcome of one per-agent distill run — raw data for the CLI/tests."""

    agent_key: str
    status: str  # written | unchanged | skipped-watermark | dry-run | no-sessions
    artifact_path: Optional[Path] = None
    artifact_chars: int = 0
    size_level: str = "ok"  # ok | warning
    distilled_through: str = ""
    input_hash: str = ""
    session_count: int = 0
    finding_count: int = 0
    scope_count: int = 0
    cluster_count: int = 0
    distilled_count: int = 0
    remainder_count: int = 0
    index_count: int = 0
    absorbed_count: int = 0
    estimated_llm_calls: int = 0
    llm_calls: int = 0
    llm_cache_hits: int = 0
    llm_folds: int = 0
    llm_failed: int = 0
    llm_rejected: int = 0
    llm_fallbacks: int = 0
    llm_aborted: bool = False
    forget_diff: Dict[str, List[str]] = field(default_factory=dict)


# --------------------------------------------------------------------------- small helpers


def _kind(node: ResearchNode) -> str:
    return node.type.value if hasattr(node.type, "value") else str(node.type)


def _node_content_hash(node: ResearchNode) -> str:
    """Change-detection hash for ``member_refs`` (§4 — never identity).

    Prefers the extractor-stamped ``metadata['content_hash']`` (session
    findings carry one); otherwise a pure hash of name + description.
    """
    meta = node.metadata or {}
    existing = str(meta.get("content_hash") or "").strip()
    if existing:
        return existing
    digest = hashlib.sha256(
        f"{node.name}\n{node.description or ''}".encode("utf-8")
    ).hexdigest()
    return digest[:24]


def _parse_iso(value: object) -> Optional[datetime]:
    """ISO-8601 → aware UTC datetime (mirrors ``memory.decay`` parsing)."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


_TOKEN_CACHE: Dict[str, frozenset] = {}


def _tokens(text: str) -> frozenset:
    """Memoized, stopword-filtered token set for candidate generation (§5.2)."""
    cached = _TOKEN_CACHE.get(text)
    if cached is None:
        cached = frozenset(_base_tokenise(text) - _STOPWORD_TOKENS)
        _TOKEN_CACHE[text] = cached
    return cached


def compute_lineage_key(member_ids_by_node: Mapping[str, Sequence[str]]) -> str:
    """sha256 of the sorted TRANSITIVE raw L0 member ids (§4).

    ``member_ids_by_node`` maps each direct member id to its raw L0 roots —
    the member's own id for a raw node, or its ``member_refs`` node ids for a
    distillate. Recursion-stable by construction: a manager distillate's
    lineage is the sorted union of its constituents' raw roots.
    """
    roots: Set[str] = set()
    for member_id, raw_roots in member_ids_by_node.items():
        expanded = [str(r) for r in raw_roots if str(r).strip()]
        if expanded:
            roots.update(expanded)
        else:
            roots.add(str(member_id))
    return hashlib.sha256("\n".join(sorted(roots)).encode("utf-8")).hexdigest()


def _raw_roots_for(node: ResearchNode) -> List[str]:
    """Transitive raw L0 roots of one member (§4 / §6.4 flattened refs)."""
    if node.type is ResearchNodeType.DISTILLED_NOTE:
        refs = (node.metadata or {}).get("member_refs")
        if isinstance(refs, list):
            roots = [
                str(ref.get("node_id"))
                for ref in refs
                if isinstance(ref, Mapping) and str(ref.get("node_id") or "").strip()
            ]
            if roots:
                return roots
    return [node.id]


def _refs_digest(pairs: Sequence[Tuple[str, str]]) -> str:
    """sha256 over sorted ``(node_id, content_hash)`` pairs — the members_digest
    formula, shared between live members and prior-artifact ``member_refs`` so
    the two are directly comparable (§7.2 verbatim reuse)."""
    payload = "\n".join(f"{node_id}\t{chash}" for node_id, chash in sorted(pairs))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _members_digest(members: Sequence[ResearchNode]) -> str:
    return _refs_digest([(node.id, _node_content_hash(node)) for node in members])


def _instant_key(stamp: str) -> Tuple[datetime, str]:
    """Sort key ordering ISO-8601 stamps by parsed instant, then raw string.

    Raw lexicographic order breaks across timestamp spellings ('...00Z' >
    '...00.500+00:00' though the latter is the later instant); unparseable
    stamps sort earliest deterministically.
    """
    parsed = _parse_iso(stamp)
    return (parsed or datetime.min.replace(tzinfo=timezone.utc), stamp)


def artifact_size_level(char_count: int, budget: int) -> str:
    """Size lint level for a rendered L1 artifact: ok | warning | error (§7.2)."""
    if char_count > budget:
        return "error"
    if char_count >= int(budget * 0.9):
        return "warning"
    return "ok"


# --------------------------------------------------------------------------- scope (§5.1)


def _scope_for_agent(
    graph: ResearchGraph, agent_key: str
) -> Tuple[List[ResearchNode], List[ResearchNode], List[ResearchNode]]:
    """Deterministic monotone closure — returns (sessions, findings, extras).

    ``sessions`` are the agent's Session nodes (``performed_by`` targets the
    Agent node id), ``findings`` their session findings, ``extras`` the ≤2-hop
    expansion over :data:`_SCOPE_EDGE_TYPES` (anchors and related nodes).
    Sorted traversal throughout; edges walked undirected. No PPR — closure
    over an additive-only L0 is monotone, so scope never flaps (§5.1).
    """
    nodes_by_id = {node.id: node for node in graph.nodes}
    agent_node_id = stable_id(ResearchNodeType.AGENT.value, f"agent:{agent_key}")

    session_ids = sorted(
        {
            edge.source
            for edge in graph.edges
            if edge.type == "performed_by" and edge.target == agent_node_id
        }
    )
    sessions = [
        nodes_by_id[sid]
        for sid in session_ids
        if sid in nodes_by_id and nodes_by_id[sid].type is ResearchNodeType.SESSION
    ]
    session_id_set = {node.id for node in sessions}

    finding_values = {t.value for t in SESSION_FINDING_TYPES}
    finding_ids = sorted(
        edge.source
        for edge in graph.edges
        if edge.type == "derived_from_session"
        and edge.target in session_id_set
        and edge.source in nodes_by_id
        and _kind(nodes_by_id[edge.source]) in finding_values
    )
    findings = [nodes_by_id[fid] for fid in sorted(set(finding_ids))]

    # Undirected adjacency over the allowlisted edge set, sorted for
    # deterministic BFS order.
    adjacency: Dict[str, List[str]] = {}
    for edge in graph.edges:
        if edge.type not in _SCOPE_EDGE_TYPES:
            continue
        adjacency.setdefault(edge.source, []).append(edge.target)
        adjacency.setdefault(edge.target, []).append(edge.source)
    for neighbors in adjacency.values():
        neighbors.sort()

    visited: Set[str] = {node.id for node in findings}
    frontier: List[str] = [node.id for node in findings]
    extras: List[str] = []
    for _hop in range(2):
        next_frontier: List[str] = []
        for node_id in frontier:
            for neighbor in adjacency.get(node_id, ()):
                if neighbor in visited or neighbor not in nodes_by_id:
                    continue
                visited.add(neighbor)
                next_frontier.append(neighbor)
                if neighbor not in session_id_set:
                    extras.append(neighbor)
        frontier = sorted(next_frontier)

    extra_nodes = [nodes_by_id[nid] for nid in sorted(set(extras))]
    return sessions, findings, extra_nodes


def _corpus_clock(sessions: Sequence[ResearchNode], as_of: Optional[str]) -> str:
    """§7.1 — ``max(ended_at or started_at)`` over scope sessions, or --as-of.

    The max is taken by PARSED instant (mixed importer spellings — 'Z' vs
    '+00:00' vs fractional seconds — do not order lexicographically) while the
    returned value stays the raw source-derived string. NO timestamps in the
    input and no ``as_of`` → hard fail; the fallback-to-``datetime.now()``
    class of bug is exactly what this guards.
    """
    if as_of and as_of.strip():
        return as_of.strip()
    stamps = []
    for session in sessions:
        meta = session.metadata or {}
        stamp = str(meta.get("ended_at") or meta.get("started_at") or "").strip()
        if stamp:
            stamps.append(stamp)
    if not stamps:
        raise DistillError(
            "No session timestamps in scope to derive the corpus clock; "
            "pass as_of=<ISO-8601> (--as-of) to distill this corpus (spec §7.1)."
        )
    return max(stamps, key=_instant_key)


def _slice_input_hash(
    scope_nodes: Sequence[ResearchNode],
    graph: ResearchGraph,
    findings: Sequence[ResearchNode],
    confidence: Mapping[str, float],
) -> str:
    """Watermark over every byte-affecting input of the slice (§7.2).

    Beyond the sorted ``(node_id, content_hash)`` pairs of the scope closure,
    the hash folds in (a) the allowlisted edges incident to scope nodes —
    anchor selection, profile top_concepts, supersede winners, clustering
    signals and the session/finding partition all read edges — and (b) the
    resolved ``recurring_confidence`` of scope findings, a WHOLE-graph signal
    (near-dup clusters span other agents' findings and re-root as the corpus
    grows) that ranks the remainder and gates absorption. Without those, a
    new edge or an out-of-scope near-dup would change what a fresh run
    renders while the skip watermark stays green — sidecar state deciding
    artifact content instead of merely skipping identical work.
    """
    scope_ids = {node.id for node in scope_nodes}
    lines = [
        f"node\t{node.id}\t{_node_content_hash(node)}"
        for node in sorted(scope_nodes, key=lambda n: n.id)
    ]
    lines.extend(
        sorted(
            {
                f"edge\t{edge.source}\t{edge.type}\t{edge.target}"
                for edge in graph.edges
                if edge.type in _WATERMARK_EDGE_TYPES
                and (edge.source in scope_ids or edge.target in scope_ids)
            }
        )
    )
    lines.extend(
        f"confidence\t{node_id}\t{confidence.get(node_id, 0.0)!r}"
        for node_id in sorted({node.id for node in findings})
    )
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- cluster (§5.2)


def _cluster_scope_findings(
    findings: Sequence[ResearchNode],
    graph: ResearchGraph,
    state: Optional[DistillStateStore],
    *,
    use_memo: bool = True,
) -> List[List[ResearchNode]]:
    """Union-find clustering with inverted-index candidate generation.

    Signals are exactly ``memory.distill``'s: ``supersedes`` edges + Jaccard
    on names above the shared near-dup threshold. Hardened for scale: pairs
    are generated only for nodes sharing ≥1 non-stopword token (posting lists
    over :data:`_TOKEN_POSTING_CAP` are skipped as domain stopwords), the
    result is memoized in ``agent_distill_state``, and clusters above
    :data:`_CLUSTER_SIZE_CAP` split by session-time buckets.

    The memo key covers EVERY clustering input — ``(id, name, first_seen_at)``
    per node, the ``supersedes`` pairs among the findings, and
    :data:`_CLUSTERING_VERSION` — so a new edge (or an algorithm change) can
    never resurrect a stale partition. ``use_memo=False`` (``--full`` /
    ``--recheck``) skips the read and recomputes, refreshing the memo row.
    """
    ordered = sorted(findings, key=lambda n: n.id)
    if not ordered:
        return []
    by_id = {node.id: node for node in ordered}
    finding_ids = set(by_id)

    memo_lines = [f"v{_CLUSTERING_VERSION}"]
    memo_lines.extend(
        f"node\t{node.id}\t{node.name}\t{(node.metadata or {}).get('first_seen_at') or ''}"
        for node in ordered
    )
    memo_lines.extend(
        sorted(
            {
                f"supersedes\t{edge.source}\t{edge.target}"
                for edge in graph.edges
                if edge.type == "supersedes"
                and edge.source in finding_ids
                and edge.target in finding_ids
            }
        )
    )
    memo_key = hashlib.sha256("\n".join(memo_lines).encode("utf-8")).hexdigest()
    if state is not None and use_memo:
        raw = state.get(DistillStateStore.SCOPE_CLUSTER_MEMO, "", memo_key)
        if raw:
            try:
                memoized = json.loads(raw)
            except json.JSONDecodeError:
                memoized = None
            if isinstance(memoized, list):
                clusters = [
                    [by_id[mid] for mid in member_ids if mid in by_id]
                    for member_ids in memoized
                    if isinstance(member_ids, list)
                ]
                covered = {n.id for cluster in clusters for n in cluster}
                if covered == set(by_id):
                    return clusters

    uf = _UnionFind()
    for node in ordered:
        uf.add(node.id)

    for edge in graph.edges:
        if edge.type != "supersedes":
            continue
        if edge.source in finding_ids and edge.target in finding_ids:
            uf.union(edge.source, edge.target)

    # Token inverted index → candidate pairs (near-linear; §5.2).
    postings: Dict[str, List[str]] = {}
    for node in ordered:
        for token in sorted(_tokens(node.name)):
            postings.setdefault(token, []).append(node.id)
    candidate_pairs: Set[Tuple[str, str]] = set()
    for token in sorted(postings):
        ids = postings[token]
        if len(ids) < 2 or len(ids) > _TOKEN_POSTING_CAP:
            continue
        for i, left in enumerate(ids):
            for right in ids[i + 1 :]:
                candidate_pairs.add((left, right))

    for left_id, right_id in sorted(candidate_pairs):
        if uf.find(left_id) == uf.find(right_id):
            continue
        if jaccard(by_id[left_id].name, by_id[right_id].name) > _NEAR_DUP_THRESHOLD:
            uf.union(left_id, right_id)

    grouped: Dict[str, List[ResearchNode]] = {}
    for node in ordered:
        grouped.setdefault(uf.find(node.id), []).append(node)

    clusters: List[List[ResearchNode]] = []
    for _root, members in sorted(grouped.items(), key=lambda kv: kv[0]):
        members = sorted(members, key=lambda n: n.id)
        if len(members) <= _CLUSTER_SIZE_CAP:
            clusters.append(members)
        else:
            clusters.extend(_split_by_time_buckets(members))

    if state is not None:
        state.put(
            DistillStateStore.SCOPE_CLUSTER_MEMO,
            "",
            memo_key,
            json.dumps([[n.id for n in cluster] for cluster in clusters]),
        )
    return clusters


def _split_by_time_buckets(members: List[ResearchNode]) -> List[List[ResearchNode]]:
    """Split an oversized cluster into ≤cap session-time buckets (§5.2).

    Members sort by ``(first_seen_at, id)`` and split into the minimal number
    of contiguous, evenly sized buckets — deterministic, content-derived.
    """
    def _stamp(node: ResearchNode) -> str:
        return str((node.metadata or {}).get("first_seen_at") or "")

    ordered = sorted(members, key=lambda n: (_stamp(n), n.id))
    parts = (len(ordered) + _CLUSTER_SIZE_CAP - 1) // _CLUSTER_SIZE_CAP
    base, extra = divmod(len(ordered), parts)
    buckets: List[List[ResearchNode]] = []
    start = 0
    for index in range(parts):
        size = base + (1 if index < extra else 0)
        bucket = sorted(ordered[start : start + size], key=lambda n: n.id)
        buckets.append(bucket)
        start += size
    return buckets


# --------------------------------------------------------------------------- compact & polish (§5.3)


def _render_member_block(node: ResearchNode) -> str:
    body = (node.description or "").strip()
    return f"[{node.id}] {node.name}" + (f"\n{body}" if body else "")


def _kind_hint(members: Sequence[ResearchNode]) -> str:
    for node in members:
        blob = f"{node.name} {node.description or ''}".lower()
        if any(hint in blob for hint in _PITFALL_HINTS):
            return "gotcha"
    return "runbook"


def _validate_summary(
    payload: object, member_ids: Set[str]
) -> Tuple[Optional[Dict[str, object]], str]:
    """(validated output, "") or (None, reason) — the §5.3 validation contract.

    Typed schema, citation whitelist (fabricated ids → reject), and the
    deterministic faithfulness lint: identifiers, numbers, version tokens and
    quoted strings in the body must appear in some cited member.
    """
    if not isinstance(payload, Mapping):
        return None, "not-a-mapping"
    kind = str(payload.get("kind") or "").strip().lower()
    if kind not in {"runbook", "gotcha", "note"}:
        return None, "bad-kind"
    title = str(payload.get("title") or "").strip()
    body = str(payload.get("body") or "").strip()
    if not title or not body:
        return None, "empty-title-or-body"
    citations_raw = payload.get("citations")
    if not isinstance(citations_raw, (list, tuple)) or not citations_raw:
        return None, "missing-citations"
    citations = [str(c) for c in citations_raw]
    if not set(citations) <= member_ids:
        return None, "citation-outside-members"
    return (
        {"kind": kind, "title": title, "body": body, "citations": sorted(set(citations))},
        "",
    )


def _faithfulness_ok(
    body: str, cited: Sequence[ResearchNode]
) -> bool:
    """Deterministic faithfulness lint (§5.3) — no LLM, pure string checks."""
    corpus = "\n".join(
        f"{node.name}\n{node.description or ''}" for node in cited
    ).lower()
    suspects: Set[str] = set()
    for regex in (_BACKTICK_RE, _VERSION_RE, _NUMBER_RE, _QUOTED_RE):
        for match in regex.findall(body):
            suspects.add(str(match).strip().lower())
    return all(token in corpus for token in suspects if token)


def _deterministic_fallback(
    members: Sequence[ResearchNode], kind_hint: str
) -> Dict[str, object]:
    """§5.3 fallback: concatenated member titles + refs — pure member content."""
    dominant = max(members, key=lambda n: (len(n.name or ""), n.id))
    bullets = "\n".join(f"- {node.name} ({node.id})" for node in members)
    return {
        "kind": kind_hint,
        "title": f"Undistilled cluster: {dominant.name}".strip(),
        "body": f"Fallback digest of {len(members)} findings:\n{bullets}",
        "citations": sorted(node.id for node in members),
    }


def _cache_path(cache_root: Path, lineage_key: str) -> Path:
    return cache_root / lineage_key[:2] / f"{lineage_key}.json"


def _read_cache_entry(path: Path) -> Optional[Dict[str, object]]:
    """Tolerant cache read — malformed entries degrade to a miss, never crash."""
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _write_cache_entry(path: Path, payload: Dict[str, object]) -> None:
    """Atomic pid+random tmp-rename write (the blessed session_graph pattern)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".tmp.{os.getpid()}.{secrets.token_hex(4)}")
    try:
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _cache_envelope_matches(
    entry: Mapping[str, object], members_digest: str, guidance_digest: str
) -> bool:
    return (
        entry.get("schema_version") == CACHE_SCHEMA_VERSION
        and entry.get("prompt_version") == PROMPT_VERSION
        and str(entry.get("guidance_digest") or "") == guidance_digest
        and str(entry.get("members_digest") or "") == members_digest
    )


class _CircuitBreaker:
    """§5.3 transport-failure breaker, scoped to one CLI/API invocation.

    ``distill_all`` shares a single instance across its agents so that "3
    consecutive transport failures aborts the LLM stage for the run" holds
    for the whole invocation, not per agent — a dead provider costs at most
    :data:`_CIRCUIT_BREAKER_LIMIT` attempts per sweep.
    """

    __slots__ = ("consecutive", "tripped")

    def __init__(self) -> None:
        self.consecutive = 0
        self.tripped = False

    def record_failure(self) -> None:
        self.consecutive += 1
        if self.consecutive >= _CIRCUIT_BREAKER_LIMIT:
            self.tripped = True

    def record_success(self) -> None:
        self.consecutive = 0


class _LLMStage:
    """Per-run compact/polish executor: cache, fold, fallback, breaker, caps."""

    def __init__(
        self,
        *,
        agent_key: str,
        summarizer: Optional[Summarizer],
        cache_root: Path,
        state: Optional[DistillStateStore],
        options: DistillOptions,
        prior_distillates: Sequence[Mapping[str, object]],
        run_seq: int,
        result: DistillResult,
        breaker: Optional[_CircuitBreaker] = None,
    ) -> None:
        self.agent_key = agent_key
        self.summarizer = summarizer
        self.cache_root = cache_root
        self.state = state
        self.options = options
        self.prior_distillates = list(prior_distillates)
        self.run_seq = run_seq
        self.result = result
        self.guidance_digest = _compute_guidance_digest(options.guidance)
        self.breaker = breaker if breaker is not None else _CircuitBreaker()
        if self.breaker.tripped:
            self.result.llm_aborted = True

    # -- negative cache / backoff -------------------------------------------------

    def _backoff_blocks(self, lineage_key: str, members_digest: str) -> bool:
        if self.state is None or self.options.retry_fallbacks:
            return False
        raw = self.state.get(DistillStateStore.SCOPE_NEGATIVE, "", lineage_key)
        if not raw:
            return False
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            return False
        # A changed members_digest is NEW input, not a repeat of the failed
        # one — the stale backoff must not block it (a fresh sidecar would
        # attempt; blocking here would let sidecar state pick the bytes).
        if str(entry.get("members_digest") or "") != members_digest:
            return False
        # A changed guidance_digest is likewise NEW input (§12 Phase-5): a
        # fallback recorded under the old per-agent guidance must not suppress
        # a retry under the edited stream — the positive cache already forks on
        # guidance_digest, so the negative cache must fork too or the fork is
        # incomplete for clusters that last took a fallback verdict.
        if str(entry.get("guidance_digest") or "") != self.guidance_digest:
            return False
        retry_after = int(entry.get("retry_after_run") or 0)
        return self.run_seq < retry_after

    def _record_failure(self, lineage_key: str, members_digest: str) -> None:
        if self.state is None:
            return
        failures = 1
        raw = self.state.get(DistillStateStore.SCOPE_NEGATIVE, "", lineage_key)
        if raw:
            try:
                entry = json.loads(raw)
                # Failure streaks only accumulate over the SAME input digest —
                # both the member bytes and the guidance stream (§12 Phase-5).
                if (
                    str(entry.get("members_digest") or "") == members_digest
                    and str(entry.get("guidance_digest") or "") == self.guidance_digest
                ):
                    failures = int(entry.get("failures") or 0) + 1
            except (json.JSONDecodeError, ValueError, TypeError):
                failures = 1
        # Exponential backoff in units of executed runs (sidecar-only state).
        self.state.put(
            DistillStateStore.SCOPE_NEGATIVE,
            "",
            lineage_key,
            json.dumps(
                {
                    "failures": failures,
                    "retry_after_run": self.run_seq + 2 ** min(failures, 6),
                    "members_digest": members_digest,
                    "guidance_digest": self.guidance_digest,
                }
            ),
        )

    def _clear_failures(self, lineage_key: str) -> None:
        if self.state is not None:
            self.state.put(
                DistillStateStore.SCOPE_NEGATIVE, "", lineage_key, json.dumps({"failures": 0, "retry_after_run": 0})
            )

    def _touch_cache_use(self, lineage_key: str) -> None:
        if self.state is not None and not self.options.dry_run:
            self.state.put(
                DistillStateStore.SCOPE_CACHE_USE, "", lineage_key, str(self.run_seq)
            )

    # -- fold detection (§5.3 refine) ---------------------------------------------

    def _fold_candidate(
        self, member_ids: Set[str]
    ) -> Optional[Mapping[str, object]]:
        """Prior distillate strictly contained in the new member set, <30% growth.

        A *merge* (two prior distillates inside one new cluster) disables the
        fold — full re-distill per spec.
        """
        contained = [
            prior
            for prior in self.prior_distillates
            if prior["member_ids"] and set(prior["member_ids"]) < member_ids
        ]
        if len(contained) != 1:
            return None
        prior = contained[0]
        grown = len(member_ids) - len(prior["member_ids"])
        if grown <= 0 or grown / len(prior["member_ids"]) >= 0.30:
            return None
        return prior

    def _prior_verbatim(self, members_digest: str) -> Optional[Dict[str, object]]:
        """§7.2 cold parity for the steady state: reuse the prior artifact note.

        When the prior committed artifact — a declared determinism input —
        already carries an llm-quality note over EXACTLY this member set
        (same ids, same content hashes), its text is reused verbatim on a
        cache miss instead of re-asking the summarizer. Without this, a
        cluster whose last output came from a fold replays differently once
        the cache is gone (`_fold_candidate` needs strict growth, so the
        rerun would take the full-distill path and mint different text) —
        cache state leaking into artifact bytes, the historically-breaking
        class. ``--recheck`` disables the reuse so a prompt/schema bump can
        force a genuine re-audit through the LLM.
        """
        for prior in self.prior_distillates:
            if (
                prior.get("quality") == "llm"
                and prior.get("members_digest") == members_digest
                and str(prior.get("title") or "").strip()
                and str(prior.get("body") or "").strip()
            ):
                return {
                    "kind": prior["kind"],
                    "title": prior["title"],
                    "body": prior["body"],
                    "citations": sorted(str(m) for m in prior["member_ids"]),
                }
        return None

    # -- main entry ----------------------------------------------------------------

    def summarize_cluster(
        self, members: Sequence[ResearchNode], lineage_key: str
    ) -> Tuple[Dict[str, object], str]:
        """Return ``(output, distill_quality)`` for one cluster.

        Resolution order: shared cache → fold → full summarize → fallback.
        Fallback verdicts from a real failed/rejected attempt are cached as
        ``{"fallback": true}`` (§5.3 — deliberately INVERTS session_graph's
        failed-call-not-cached rule); capped/aborted/backoff clusters fall
        back WITHOUT caching so later runs still converge.
        """
        member_ids = {node.id for node in members}
        kind_hint = _kind_hint(members)
        digest = _members_digest(members)
        path = _cache_path(self.cache_root, lineage_key)
        entry = _read_cache_entry(path)

        if entry is not None and _cache_envelope_matches(entry, digest, self.guidance_digest):
            if entry.get("fallback") is True:
                if not self.options.retry_fallbacks:
                    self.result.llm_cache_hits += 1
                    self._touch_cache_use(lineage_key)
                    return _deterministic_fallback(members, kind_hint), "fallback"
            else:
                output, reason = _validate_summary(entry.get("output"), member_ids)
                if output is not None and (
                    not self.options.recheck
                    or _faithfulness_ok(str(output["body"]), members)
                ):
                    self.result.llm_cache_hits += 1
                    self._touch_cache_use(lineage_key)
                    return output, "llm"
                logger.info(
                    "agent_distill: cached output for %s invalid on recheck (%s)",
                    lineage_key[:16],
                    reason or "faithfulness",
                )

        # Cache miss — before paying the summarizer, check whether the prior
        # committed artifact (a declared §7.2 input) already holds this exact
        # cluster's llm-quality note; replaying it keeps warm and cold runs
        # byte-identical without any cache state.
        if not self.options.recheck:
            reused = self._prior_verbatim(digest)
            if reused is not None:
                return reused, "llm"

        # Build the (pure) request first: the provider-call cost is a function
        # of its chunk shape, and both the dry-run estimate and the
        # --max-llm-calls budget are measured in that unit.
        request = self._build_request(members, lineage_key, kind_hint, member_ids)
        planned = _planned_provider_calls(request)
        if self.options.dry_run:
            self.result.estimated_llm_calls += planned
            return _deterministic_fallback(members, kind_hint), "fallback"
        if self.summarizer is None:
            # A real run with no backend is a FALLBACK, not an estimate —
            # §5.3's provider-health stats must show it (dry_run alone owns
            # estimated_llm_calls).
            self.result.llm_fallbacks += 1
            return _deterministic_fallback(members, kind_hint), "fallback"
        if self.result.llm_aborted or self._backoff_blocks(lineage_key, digest):
            self.result.llm_fallbacks += 1
            return _deterministic_fallback(members, kind_hint), "fallback"
        if (
            self.options.max_llm_calls is not None
            and self.result.llm_calls + planned > self.options.max_llm_calls
        ):
            self.result.llm_fallbacks += 1
            return _deterministic_fallback(members, kind_hint), "fallback"

        self.result.llm_calls += planned
        try:
            raw = self.summarizer(request)
        except Exception:  # noqa: BLE001 — transport failure, degrade-never-crash
            logger.exception("agent_distill: summarizer raised for %s", lineage_key[:16])
            self.breaker.record_failure()
            if self.breaker.tripped:
                self.result.llm_aborted = True
            return self._failed(members, lineage_key, digest, kind_hint)
        self.breaker.record_success()
        if raw is None:
            return self._failed(members, lineage_key, digest, kind_hint)

        output, reason = _validate_summary(raw, member_ids)
        if output is None:
            self.result.llm_rejected += 1
            logger.info(
                "agent_distill: summary rejected for %s (%s)", lineage_key[:16], reason
            )
            return self._failed(members, lineage_key, digest, kind_hint, rejected=True)
        cited = [node for node in members if node.id in set(output["citations"])]
        if not _faithfulness_ok(str(output["body"]), cited):
            self.result.llm_rejected += 1
            logger.info(
                "agent_distill: summary failed faithfulness lint for %s", lineage_key[:16]
            )
            return self._failed(members, lineage_key, digest, kind_hint, rejected=True)

        if request.mode == "fold":
            self.result.llm_folds += 1
        self._clear_failures(lineage_key)
        _write_cache_entry(
            path,
            {
                "schema_version": CACHE_SCHEMA_VERSION,
                "prompt_version": PROMPT_VERSION,
                "guidance_digest": self.guidance_digest,
                "members_digest": digest,
                "lineage_key": lineage_key,
                "output": output,
            },
        )
        self._touch_cache_use(lineage_key)
        return output, "llm"

    def _build_request(
        self,
        members: Sequence[ResearchNode],
        lineage_key: str,
        kind_hint: str,
        member_ids: Set[str],
    ) -> DistillRequest:
        triples = tuple(
            (node.id, node.name, node.description or "")
            for node in sorted(members, key=lambda n: n.id)
        )
        blocks = [_render_member_block(node) for node in sorted(members, key=lambda n: n.id)]
        budget = self.options.char_budget or chunk_char_budget()
        prior = self._fold_candidate(member_ids)
        if prior is not None:
            prior_ids = set(prior["member_ids"])
            new_triples = tuple(t for t in triples if t[0] not in prior_ids)
            new_blocks = [
                _render_member_block(node)
                for node in sorted(members, key=lambda n: n.id)
                if node.id not in prior_ids
            ]
            return DistillRequest(
                agent_key=self.agent_key,
                lineage_key=lineage_key,
                kind_hint=kind_hint,
                members=triples,
                chunks=tuple(pack_blocks(new_blocks, budget)),
                mode="fold",
                prior_output={
                    "kind": prior["kind"],
                    "title": prior["title"],
                    "body": prior["body"],
                    "citations": sorted(prior_ids),
                },
                new_members=new_triples,
                guidance=self.options.guidance,
            )
        return DistillRequest(
            agent_key=self.agent_key,
            lineage_key=lineage_key,
            kind_hint=kind_hint,
            members=triples,
            chunks=tuple(pack_blocks(blocks, budget)),
            guidance=self.options.guidance,
        )

    def _failed(
        self,
        members: Sequence[ResearchNode],
        lineage_key: str,
        digest: str,
        kind_hint: str,
        rejected: bool = False,
    ) -> Tuple[Dict[str, object], str]:
        """Cache-and-return the fallback verdict for a real failed attempt."""
        if not rejected:
            self.result.llm_failed += 1
        self.result.llm_fallbacks += 1
        self._record_failure(lineage_key, digest)
        _write_cache_entry(
            _cache_path(self.cache_root, lineage_key),
            {
                "schema_version": CACHE_SCHEMA_VERSION,
                "prompt_version": PROMPT_VERSION,
                "guidance_digest": self.guidance_digest,
                "members_digest": digest,
                "lineage_key": lineage_key,
                "fallback": True,
            },
        )
        self._touch_cache_use(lineage_key)
        return _deterministic_fallback(members, kind_hint), "fallback"


# --------------------------------------------------------------------------- prior artifact


def _load_prior_artifact(path: Path) -> Dict[str, object]:
    """Parse the prior committed artifact (§6.2 declared hysteresis input).

    Returns ``{"remainder_ids", "absorbed_ids", "distillates"}``; a missing or
    corrupt artifact degrades to the empty prior (tolerant read).
    """
    empty: Dict[str, object] = {
        "remainder_ids": set(),
        "absorbed_ids": set(),
        "distillates": [],
        "distillate_ids": [],
    }
    if not path.is_file():
        return empty
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("agent_distill: prior artifact unreadable at %s", path)
        return empty
    finding_values = {t.value for t in SESSION_FINDING_TYPES}
    remainder_ids: Set[str] = set()
    absorbed_ids: Set[str] = set()
    distillates: List[Dict[str, object]] = []
    distillate_ids: List[str] = []
    for raw in payload.get("nodes", []) if isinstance(payload, dict) else []:
        if not isinstance(raw, dict):
            continue
        type_value = str(raw.get("type") or "")
        if type_value in finding_values:
            remainder_ids.add(str(raw.get("id")))
            continue
        if type_value != ResearchNodeType.DISTILLED_NOTE.value:
            continue
        distillate_ids.append(str(raw.get("id")))
        metadata = raw.get("metadata") or {}
        if not isinstance(metadata, dict):
            continue
        member_pairs = [
            (str(ref.get("node_id")), str(ref.get("content_hash") or ""))
            for ref in metadata.get("member_refs") or []
            if isinstance(ref, dict) and str(ref.get("node_id") or "").strip()
        ]
        for ref in metadata.get("absorbed_refs") or []:
            if isinstance(ref, dict) and str(ref.get("node_id") or "").strip():
                absorbed_ids.add(str(ref["node_id"]))
        if str(metadata.get("kind") or "") in {"runbook", "gotcha", "note"}:
            distillates.append(
                {
                    "kind": str(metadata.get("kind") or "note"),
                    "title": str(raw.get("name") or ""),
                    "body": str(raw.get("description") or ""),
                    "member_ids": [node_id for node_id, _chash in member_pairs],
                    # For the §7.2 verbatim-reuse path: what the note covered,
                    # content-exact, comparable against a live members_digest.
                    "members_digest": _refs_digest(member_pairs),
                    "quality": str(metadata.get("distill_quality") or ""),
                }
            )
    return {
        "remainder_ids": remainder_ids,
        "absorbed_ids": absorbed_ids,
        "distillates": distillates,
        "distillate_ids": distillate_ids,
    }


# --------------------------------------------------------------------------- forgetting (§6)


def _supersede_winners(graph: ResearchGraph) -> Set[str]:
    """Winners of live supersede chains: sources never themselves superseded."""
    sources = {e.source for e in graph.edges if e.type == "supersedes"}
    targets = {e.target for e in graph.edges if e.type == "supersedes"}
    return sources - targets


def _read_access_state(project_root: Path | str) -> Mapping[str, NodeMemoryRow]:
    """Load ``{node_id: NodeMemoryRow}`` MCP read-access state for LRU decay.

    Sidecar-only and best-effort. The live ``last_accessed_at`` / ``access_count``
    that drive forgetting-by-disuse are accumulated by the MCP read surfaces into
    the ``node_memory`` table of ``.tesserae/sqlite.db`` (see
    :mod:`tesserae.memory.store`); this pass only READS them — it never records
    access. Gated on the db file already existing so a pure-fixture distill that
    never compiled does not lazily mint the sidecar (mirrors
    ``mcp_server._read_node_memory``), and any error degrades to an empty map.

    Determinism: with no sidecar rows the result is ``{}`` -> :func:`_decay_view`
    is a no-op -> :func:`compute_decay_score` falls back to ``first_seen_at`` ->
    the distillate is byte-identical to the pre-LRU artifact. The byte-parity
    guards depend on this fallback staying exact.
    """
    db_path = _state_db_path(project_root)
    if not db_path.exists():
        return {}
    try:
        return read_memory(db_path)
    except Exception:  # pragma: no cover — defensive; missing/locked/foreign db
        return {}


def _decay_view(node: ResearchNode, access: Mapping[str, NodeMemoryRow]) -> object:
    """Return a decay-scoring view of ``node`` overlaid with live LRU access state.

    Mirrors ``project.py``'s compile-time ``decay_node`` merge EXACTLY: when the
    ``node_memory`` sidecar has a row for this node, copy the node's metadata and
    overlay ``access_count`` / ``last_accessed_at`` onto the COPY, then score that
    view — so a finding not retrieved for a long time decays toward absorb/demote
    while a recently-read one is kept. The original ``node.metadata`` is never
    touched (``model_dump`` would otherwise serialize wall-clock sidecar state
    into ``graph.json`` and break byte-idempotence). With no matching row the
    node is returned verbatim, so the empty-sidecar path is byte-identical.
    """
    prev = access.get(node.id)
    if prev is None:
        return node
    base_meta = getattr(node, "metadata", None)
    merged_meta = dict(base_meta) if isinstance(base_meta, dict) else {}
    if prev.access_count:
        merged_meta["access_count"] = prev.access_count
    if prev.last_accessed_at:
        merged_meta["last_accessed_at"] = prev.last_accessed_at
    return SimpleNamespace(metadata=merged_meta)


def _absorbable(
    node: ResearchNode,
    corpus_now: datetime,
    confidence: Mapping[str, float],
    winners: Set[str],
    access: Mapping[str, NodeMemoryRow],
) -> bool:
    """§6.1 Tier-1 gate — llm-quality distillates only; anchors never absorbed.

    Decay is scored through :func:`_decay_view` so LRU access recency (when a
    sidecar exists) drives absorption: a stale, never-retrieved finding falls
    below ``_ABSORB_DECAY_BELOW`` and is absorbed, while a recently-read one is
    kept. With no sidecar the view is a no-op and this reduces to the prior
    creation-age behavior.
    """
    if _kind(node) not in {t.value for t in SESSION_FINDING_TYPES}:
        return False
    if node.id in winners:
        return False
    if confidence.get(node.id, 0.0) >= _ABSORB_CONFIDENCE_BAR:
        return False
    return compute_decay_score(_decay_view(node, access), corpus_now) < _ABSORB_DECAY_BELOW


# --------------------------------------------------------------------------- the pass


def _resolve_options_guidance(
    options: DistillOptions, project_root: Path | str, agent_key: str
) -> DistillOptions:
    """Populate ``options.guidance`` from the on-disk per-agent stream (§12).

    Returns a per-agent COPY so ``distill_all``'s one shared ``options`` is
    never mutated across agents — each agent forks its own ``guidance_digest``
    (cluster cache + watermark) from its own combined stream. An explicit
    caller-set ``options.guidance`` wins (test/CLI override); an absent stream
    leaves ``options`` untouched so the empty-guidance path is byte-identical
    to today.
    """
    if options.guidance:
        return options
    from .extraction_guidance import resolve_agent_guidance

    combined = resolve_agent_guidance(project_root, agent_key)
    if not combined:
        return options
    return replace(options, guidance=combined)


def distill_agent(
    graph: ResearchGraph,
    agent_key: str,
    *,
    project_root: Path | str,
    registry: Optional[AgentRegistry] = None,
    summarizer: Optional[Summarizer] = None,
    options: Optional[DistillOptions] = None,
    state: Optional[DistillStateStore] = None,
    _breaker: Optional[_CircuitBreaker] = None,
) -> DistillResult:
    """Run the full distill pass for one agent over the L0 ``graph``.

    Writes ``.tesserae/agents/<key>/distilled.graph.json`` atomically and only
    if bytes changed; all mutable bookkeeping goes to the
    ``agent_distill_state`` sidecar table. Deterministic given (graph bytes,
    registry file, shared cache dir, prior artifact bytes, options) — §7.2.
    ``_breaker`` is internal: ``distill_all`` threads one circuit breaker
    through every agent so the §5.3 abort spans the whole invocation.
    """
    options = options or DistillOptions()
    project_root = Path(project_root)
    registry = registry if registry is not None else AgentRegistry.for_project(project_root)
    canonical_key = registry.resolve_alias(sanitize_agent_key(agent_key))
    result = DistillResult(agent_key=canonical_key, status="no-sessions")

    # §12 Phase-5: resolve this agent's combined guidance stream BEFORE the
    # manager dispatch so both worker and manager passes fork on it. Never
    # mutates the shared ``options`` (distill_all reuses one instance).
    options = _resolve_options_guidance(options, project_root, canonical_key)

    if state is None:
        state = DistillStateStore(_state_db_path(project_root))

    # §8.3 dispatch: an agent with direct reports is a MANAGER — its artifact
    # is the L2' rollup over the children's L1s (selective, verbatim-carrying,
    # arbitration-only LLM), not a worker pass over raw findings.
    # ponytail: a manager that ALSO has own sessions rolls up children only in
    # v1 — fold its own worker-pass distillates into the input when a real
    # working-manager corpus shows up.
    children = manager_children(graph, registry, canonical_key)
    if children:
        return _distill_manager(
            graph,
            canonical_key,
            children,
            project_root=project_root,
            registry=registry,
            summarizer=summarizer,
            options=options,
            state=state,
            breaker=_breaker,
            result=result,
        )

    sessions, findings, extras = _scope_for_agent(graph, canonical_key)
    result.session_count = len(sessions)
    result.finding_count = len(findings)
    if not sessions:
        return result

    scope_nodes: List[ResearchNode] = sorted(
        {node.id: node for node in [*sessions, *findings, *extras]}.values(),
        key=lambda n: n.id,
    )
    result.scope_count = len(scope_nodes)

    # Whole-graph promotion signal (§7.1) — computed before the watermark so
    # the input_hash can cover it (it ranks the remainder and gates
    # absorption, and it shifts when out-of-scope near-dups appear).
    confidence = compute_recurring_confidence(graph)

    input_hash = _slice_input_hash(scope_nodes, graph, findings, confidence)
    # §12 Phase-5: a guidance edit changes no scope byte, so fold its digest
    # into the watermark or an edited stream would be watermark-skipped and
    # never re-distill. Gated on a non-empty stream so the default path stays
    # byte-identical to pre-Phase-5 runs.
    if options.guidance:
        input_hash = hashlib.sha256(
            f"{input_hash}\nguidance\t{_compute_guidance_digest(options.guidance)}".encode(
                "utf-8"
            )
        ).hexdigest()
    result.input_hash = input_hash
    artifact_path = agent_artifact_path(project_root, canonical_key)
    result.artifact_path = artifact_path

    if not options.full and not options.dry_run:
        watermark = state.get(DistillStateStore.SCOPE_WATERMARK, canonical_key, "")
        if watermark == input_hash and artifact_path.is_file():
            result.status = "skipped-watermark"
            return result

    corpus_now_iso = _corpus_clock(sessions, options.as_of)
    corpus_now = _parse_iso(corpus_now_iso)
    if corpus_now is None:
        raise DistillError(
            f"Corpus clock {corpus_now_iso!r} is not ISO-8601; "
            "fix session timestamps or pass a valid as_of (spec §7.1)."
        )
    result.distilled_through = corpus_now_iso

    # LRU forgetting-by-disuse (§6 / Phase-5 KB-01): load the node_memory
    # sidecar's live read-access state ONCE, then thread it into every decay
    # score (absorption gate + remainder ranking) via ``_decay_view``. This makes
    # "not retrieved for a long time" — not merely "old" — drive forgetting.
    # Read-only and sidecar-only: we never record access here (that is the MCP
    # read surfaces' job) and never stamp it onto node.metadata, so graph.json
    # stays byte-idempotent. With no sidecar the map is empty and the pass is
    # byte-identical to the pre-LRU distillate (the determinism guards rely on it).
    access = _read_access_state(project_root)

    # ---------------- cluster (§5.2) ----------------
    clusters = _cluster_scope_findings(
        findings,
        graph,
        None if options.dry_run else state,
        use_memo=not (options.full or options.recheck),
    )
    qualifying = [c for c in clusters if len(c) >= max(2, int(options.min_cluster_size))]
    small = [c for c in clusters if len(c) < max(2, int(options.min_cluster_size))]
    result.cluster_count = len(qualifying)

    # ---------------- compact & polish (§5.3) ----------------
    prior = _load_prior_artifact(artifact_path)
    run_seq = state.current_run_seq() + 1 if not options.dry_run else state.current_run_seq()
    stage = _LLMStage(
        agent_key=canonical_key,
        summarizer=summarizer,
        cache_root=distill_cache_dir(project_root),
        state=None if options.dry_run else state,
        options=options,
        prior_distillates=prior["distillates"],
        run_seq=run_seq,
        result=result,
        breaker=_breaker,
    )

    winners = _supersede_winners(graph)
    nodes_by_id = {node.id: node for node in graph.nodes}

    distillate_nodes: List[ResearchNode] = []
    distillate_edges: List[ResearchEdge] = []
    anchor_nodes: Dict[str, ResearchNode] = {}
    absorbed_ids: Set[str] = set()

    for members in qualifying:
        lineage_key = compute_lineage_key(
            {node.id: _raw_roots_for(node) for node in members}
        )
        output, quality = stage.summarize_cluster(members, lineage_key)

        member_refs = [
            {"node_id": node.id, "content_hash": _node_content_hash(node)}
            for node in sorted(members, key=lambda n: n.id)
        ]
        # Write-time resolution check (§5.4): every ref must resolve against
        # the input graph or the run fails loud.
        for ref in member_refs:
            if ref["node_id"] not in nodes_by_id:
                raise DistillError(
                    f"member_refs entry {ref['node_id']!r} does not resolve "
                    "against the input graph (spec §5.4)."
                )

        # §6.1 absorption — llm-quality only; fallback never absorbs.
        cluster_absorbed: List[ResearchNode] = []
        if quality == "llm":
            cluster_absorbed = [
                node
                for node in members
                if _absorbable(node, corpus_now, confidence, winners, access)
            ]
        absorbed_refs = [
            {"node_id": node.id, "content_hash": _node_content_hash(node)}
            for node in sorted(cluster_absorbed, key=lambda n: n.id)
        ]
        absorbed_ids.update(node.id for node in cluster_absorbed)

        first_seen = [
            str((node.metadata or {}).get("first_seen_at"))
            for node in members
            if (node.metadata or {}).get("first_seen_at")
        ]
        metadata: Dict[str, object] = {
            "agent": canonical_key,
            "kind": str(output["kind"]),
            "lineage_key": lineage_key,
            "content_hash": hashlib.sha256(
                str(output["body"]).encode("utf-8")
            ).hexdigest()[:24],
            "member_count": len(member_refs),
            "member_refs": member_refs,
            "absorbed_refs": absorbed_refs,
            "distill_quality": quality,
            "distilled_through": corpus_now_iso,
        }
        if first_seen:
            metadata["first_seen_at"] = min(first_seen)

        note = ResearchNode(
            id=stable_id(
                ResearchNodeType.DISTILLED_NOTE.value,
                f"distilled:{canonical_key}:{lineage_key[:16]}",
            ),
            name=str(output["title"]),
            type=ResearchNodeType.DISTILLED_NOTE,
            description=str(output["body"]),
            metadata=metadata,
        )
        distillate_nodes.append(note)

        # §5.4 anchors: allowlisted-type neighbors of the CLUSTER members,
        # copied verbatim with their original L0 stable ids. Deliberately not
        # the LLM's citation subset: citations gate validation/faithfulness
        # only, and LLM output may steer note text but never artifact
        # structure — otherwise a re-summarization could re-key anchors and
        # the §7.2 verbatim-reuse replay could not reproduce them.
        member_id_set = {node.id for node in members}
        anchor_counts: Dict[str, int] = {}
        for edge in graph.edges:
            if edge.type not in _SCOPE_EDGE_TYPES:
                continue
            for member_id, other_id in ((edge.source, edge.target), (edge.target, edge.source)):
                if member_id not in member_id_set:
                    continue
                other = nodes_by_id.get(other_id)
                if other is None or other.type not in _ANCHOR_TYPES:
                    continue
                anchor_counts[other_id] = anchor_counts.get(other_id, 0) + 1
        chosen_anchors = sorted(
            anchor_counts.items(), key=lambda kv: (-kv[1], kv[0])
        )[:_ANCHOR_CAP]
        for anchor_id, _count in chosen_anchors:
            anchor_nodes.setdefault(anchor_id, nodes_by_id[anchor_id])
            distillate_edges.append(
                ResearchEdge(source=note.id, target=anchor_id, type="derived_from")
            )

    result.distilled_count = len(distillate_nodes)
    result.absorbed_count = len(absorbed_ids)

    # ---------------- emit & forget (§5.5 / §6.2) ----------------
    remainder_pool = [
        node
        for cluster in [*qualifying, *small]
        for node in cluster
        if node.id not in absorbed_ids
    ]
    remainder_pool = sorted(
        {node.id: node for node in remainder_pool}.values(), key=lambda n: n.id
    )

    prior_remainder: Set[str] = set(prior["remainder_ids"])
    eligible: List[ResearchNode] = []
    for node in remainder_pool:
        # LRU: score through the sidecar-merged view so a recently-retrieved
        # finding survives the decay cutoff while a stale one is demoted.
        decay = compute_decay_score(_decay_view(node, access), corpus_now)
        threshold = (
            _REMAINDER_EXIT_DECAY if node.id in prior_remainder else _REMAINDER_ENTER_DECAY
        )
        if decay >= threshold:
            eligible.append(node)

    # The mandated tiebreak (§5.5): confidence takes few discrete values, so
    # cutoff ties are guaranteed — never lean on serialization order.
    eligible.sort(key=lambda n: (-confidence.get(n.id, 0.0), n.id))
    remainder = eligible[: max(0, int(options.remainder_top_k))]
    remainder_ids = {node.id for node in remainder}
    result.remainder_count = len(remainder)

    index_pool = [node for node in remainder_pool if node.id not in remainder_ids]
    # Newest-first by first_seen_at, id tiebreak (§5.5 item 5). Two stable
    # sorts: id ascending, then stamp descending — equal stamps keep id order,
    # and stampless nodes ("" sorts last under reverse) age to the back where
    # truncation rolls them into the count line first.
    index_pool.sort(key=lambda n: n.id)
    index_pool.sort(
        key=lambda n: str((n.metadata or {}).get("first_seen_at") or ""), reverse=True
    )
    result.index_count = len(index_pool)

    agent_node, parent_node, reports_edge = _mint_agent_nodes(
        canonical_key, registry, sessions
    )
    profile_node = _mint_profile(
        canonical_key, sessions, findings, anchor_counts_source=graph, corpus_now_iso=corpus_now_iso
    )

    def _assemble(index_entries: List[ResearchNode], truncated: int) -> ResearchGraph:
        nodes: List[ResearchNode] = [agent_node, profile_node]
        if parent_node is not None:
            nodes.append(parent_node)
        nodes.extend(distillate_nodes)
        nodes.extend(anchor_nodes.values())
        nodes.extend(remainder)
        nodes.append(
            _mint_index_note(
                canonical_key, index_entries, truncated, corpus_now_iso
            )
        )
        nodes.append(_mint_activity_note(canonical_key, sessions, corpus_now_iso))
        edges: List[ResearchEdge] = []
        if reports_edge is not None:
            edges.append(reports_edge)
        node_ids = {node.id for node in nodes}
        for edge in distillate_edges:
            if edge.source in node_ids and edge.target in node_ids:
                edges.append(edge)
        # derived_from to members that ARE in the artifact (remainder copies).
        for note in distillate_nodes:
            for ref in note.metadata.get("member_refs", []):  # type: ignore[union-attr]
                target = str(ref.get("node_id"))
                if target in remainder_ids:
                    edges.append(
                        ResearchEdge(source=note.id, target=target, type="derived_from")
                    )
        deduped = {(e.source, e.type, e.target): e for e in edges}
        return ResearchGraph(
            nodes=list({n.id: n for n in nodes}.values()),
            edges=[deduped[key] for key in sorted(deduped)],
        )

    # §2 one-read bound — a module constant, NOT chunk_char_budget(): the
    # TESSERAE_LLM_CHUNK_CHARS env var is no §7.2 declared input and must
    # never move the index-truncation point (= artifact bytes).
    budget = options.artifact_char_budget or ARTIFACT_CHAR_BUDGET
    # CTX-01 helper in render mode (§5.3): drop the OLDEST index entries from
    # the tail (32 per step) into the deterministic count line until the
    # rendered artifact fits — byte-identical to the historical inline loop
    # (the distill determinism tests are the oracle).
    fit = fit_to_budget(
        index_pool,
        budget,
        render=lambda kept, dropped: (
            _assemble(kept, dropped).canonicalized().to_json(indent=2) + "\n"
        ),
    )
    rendered = fit.payload or ""

    result.artifact_chars = len(rendered)
    size_level = artifact_size_level(len(rendered), budget)
    if size_level == "error":
        raise DistillSizeError(
            f"Distilled artifact for {canonical_key} is {len(rendered)} chars — "
            f"exceeds the one-read bound of {budget} chars even after index "
            "truncation (spec §2/§7.2)."
        )
    result.size_level = size_level

    if options.dry_run:
        result.status = "dry-run"
        return result

    # ---------------- write-if-changed (§5 preamble) ----------------
    run_seq = state.bump_run_seq()
    new_bytes = rendered.encode("utf-8")
    existing = artifact_path.read_bytes() if artifact_path.is_file() else None
    if existing == new_bytes:
        result.status = "unchanged"
    else:
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = artifact_path.with_suffix(
            f".tmp.{os.getpid()}.{secrets.token_hex(4)}"
        )
        try:
            tmp.write_bytes(new_bytes)
            os.replace(tmp, artifact_path)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
        result.status = "written"

    # ---------------- forget ledger (§6.2) ----------------
    new_remainder_ids = sorted(remainder_ids)
    diff = {
        "promoted": sorted(remainder_ids - prior_remainder),
        "demoted": sorted(prior_remainder - remainder_ids),
        "absorbed": sorted(absorbed_ids - set(prior["absorbed_ids"])),
    }
    result.forget_diff = diff
    state.append(
        DistillStateStore.SCOPE_FORGET_LEDGER,
        canonical_key,
        json.dumps(
            {
                "distilled_through": corpus_now_iso,
                "input_hash": input_hash,
                "remainder": new_remainder_ids,
                **diff,
            },
            sort_keys=True,
        ),
    )

    state.put(DistillStateStore.SCOPE_WATERMARK, canonical_key, "", input_hash)
    _prune_cache_lru(state, distill_cache_dir(project_root), run_seq)
    return result


def known_agent_keys(graph: ResearchGraph, registry: AgentRegistry) -> List[str]:
    """Observed (L0 Agent nodes) ∪ declared (registry) agent keys, sorted."""
    keys = {
        str((node.metadata or {}).get("agent_key") or "")
        for node in graph.nodes
        if node.type is ResearchNodeType.AGENT
    }
    keys.discard("")
    declared = registry.load().get("agents")
    if isinstance(declared, dict):
        keys.update(declared.keys())
    keys.discard(ORG_ROOT)
    return sorted(keys)


def manager_children(
    graph: ResearchGraph, registry: AgentRegistry, manager_key: str
) -> List[str]:
    """Direct reports of ``manager_key`` — keys whose effective parent is it."""
    return [
        key
        for key in known_agent_keys(graph, registry)
        if key != manager_key and registry.effective_parent(key) == manager_key
    ]


def _org_depth(registry: AgentRegistry, key: str) -> int:
    """Distance to org:root via effective_parent (cycle-guarded upstream)."""
    depth = 0
    cursor = key
    seen = {cursor}
    while cursor != ORG_ROOT and depth < 64:
        cursor = registry.effective_parent(cursor)
        if cursor in seen:
            break
        seen.add(cursor)
        depth += 1
    return depth


def distill_all(
    graph: ResearchGraph,
    *,
    project_root: Path | str,
    registry: Optional[AgentRegistry] = None,
    summarizer: Optional[Summarizer] = None,
    options: Optional[DistillOptions] = None,
) -> List[DistillResult]:
    """Distill every known agent, leaves first (children before managers).

    Deepest org level runs first so a manager's pass always federates its
    children's FRESH artifacts in the same sweep (§8.3); within a level the
    order is sorted-key. One circuit breaker spans the whole sweep (§5.3):
    once a dead provider trips it, the remaining agents' clusters take the
    un-cached deterministic fallback without paying further transport
    attempts.
    """
    registry = (
        registry if registry is not None else AgentRegistry.for_project(Path(project_root))
    )
    agent_keys = sorted(
        known_agent_keys(graph, registry),
        key=lambda key: (-_org_depth(registry, key), key),
    )
    state = DistillStateStore(_state_db_path(Path(project_root)))
    breaker = _CircuitBreaker()
    return [
        distill_agent(
            graph,
            key,
            project_root=project_root,
            registry=registry,
            summarizer=summarizer,
            options=options,
            state=state,
            _breaker=breaker,
        )
        for key in agent_keys
    ]


# --------------------------------------------------------------------------- manager pass (§8.3)


_MANAGER_GROUP_JACCARD = 0.5
_NEGATION_MARKERS = ("do not ", "don't ", "never ", "avoid ", "must not ")


def _load_child_artifact(project_root: Path, agent_key: str) -> ResearchGraph:
    path = agent_artifact_path(project_root, agent_key)
    payload = json.loads(path.read_text(encoding="utf-8"))
    return graph_from_payload(payload)


def _note_member_ids(note: ResearchNode) -> List[str]:
    return sorted(
        {
            str(ref.get("node_id"))
            for ref in (note.metadata or {}).get("member_refs") or []
            if isinstance(ref, dict) and str(ref.get("node_id") or "").strip()
        }
    )


def _merged_refs(notes: Sequence[ResearchNode]) -> List[Dict[str, str]]:
    """Union of the notes' member_refs by node_id (§6.4: flattened raw roots).

    On a content_hash disagreement for the same node_id (children distilled at
    different times), the lexicographically smallest hash wins — deterministic
    regardless of child iteration order; drift is surfaced by drill_down, not
    hidden here.
    """
    merged: Dict[str, str] = {}
    for note in notes:
        for ref in (note.metadata or {}).get("member_refs") or []:
            if not isinstance(ref, dict):
                continue
            node_id = str(ref.get("node_id") or "").strip()
            if not node_id:
                continue
            chash = str(ref.get("content_hash") or "")
            if node_id not in merged or chash < merged[node_id]:
                merged[node_id] = chash
    return [
        {"node_id": node_id, "content_hash": merged[node_id]}
        for node_id in sorted(merged)
    ]


def _conflicting_note_pairs(
    notes: Sequence[ResearchNode],
) -> List[Tuple[ResearchNode, ResearchNode]]:
    """Content-keyed conflict detection between grouped sibling notes.

    Reuses contradiction.py's topic machinery (§8.3 step 4). ponytail: the
    conflict signal is topic overlap + one-sided negation markers — upgrade to
    the full marker taxonomy (or an LLM judge) when real manager corpora show
    missed conflicts.
    """
    from .memory.contradiction import _node_text, _share_topic

    pairs: List[Tuple[ResearchNode, ResearchNode]] = []
    ordered = sorted(notes, key=lambda n: n.id)
    for i, left in enumerate(ordered):
        for right in ordered[i + 1 :]:
            if str(left.metadata.get("agent")) == str(right.metadata.get("agent")):
                continue  # §8.3 step 5: conflicts matter ACROSS agents
            left_text = _node_text(left).lower()
            right_text = _node_text(right).lower()
            if not _share_topic(left_text, right_text):
                continue
            left_neg = any(m in left_text for m in _NEGATION_MARKERS)
            right_neg = any(m in right_text for m in _NEGATION_MARKERS)
            if left_neg != right_neg:
                pairs.append((left, right))
    return pairs


def _distill_manager(
    graph: ResearchGraph,
    manager_key: str,
    children: List[str],
    *,
    project_root: Path,
    registry: AgentRegistry,
    summarizer: Optional[Summarizer],
    options: DistillOptions,
    state: DistillStateStore,
    breaker: Optional[_CircuitBreaker],
    result: DistillResult,
) -> DistillResult:
    """The L2' materialization — same pass, different input (§8.3).

    Selective, not paraphrasing: child distillates are deduped by lineage,
    grouped by raw-root overlap (Jaccard ≥ 0.5 on member_refs — never by
    LLM-authored titles), and the representative of each group is carried
    with its body VERBATIM. The only prose generation at this level is
    contradiction arbitration (kind: arbitration, cached like any cluster).
    Refs stay flattened to L0 roots, so recursion never compounds a
    hallucination and a child re-distill that changes no constituent
    content_hash triggers zero manager work (cluster keys are lineage-set
    derived). The corpus clock is recursive: max over children's
    ``distilled_through`` — never wall-clock.
    """
    missing = [
        key for key in children if not agent_artifact_path(project_root, key).is_file()
    ]
    if missing:
        remedy = "; ".join(f"tesserae distill --agent {key}" for key in missing)
        raise DistillError(
            f"children of {manager_key} have no distilled artifact: "
            f"{', '.join(missing)}; run: {remedy}"
        )

    child_notes: List[Tuple[str, ResearchNode]] = []
    child_agent_nodes: Dict[str, ResearchNode] = {}
    child_profiles: Dict[str, ResearchNode] = {}
    stamps: List[str] = []
    for key in children:  # children is registry-derived, sorted upstream
        child = _load_child_artifact(project_root, key)
        for node in sorted(child.nodes, key=lambda n: n.id):
            if node.type is ResearchNodeType.DISTILLED_NOTE:
                if str((node.metadata or {}).get("kind")) in {"runbook", "gotcha", "note"}:
                    child_notes.append((key, node))
                stamp = str((node.metadata or {}).get("distilled_through") or "")
                if stamp:
                    stamps.append(stamp)
            elif node.type is ResearchNodeType.AGENT:
                child_agent_nodes.setdefault(node.id, node)
            elif node.type is ResearchNodeType.EXPERTISE_PROFILE:
                child_profiles.setdefault(node.id, node)
                stamp = str((node.metadata or {}).get("distilled_through") or "")
                if stamp:
                    stamps.append(stamp)

    result.session_count = 0
    result.finding_count = len(child_notes)
    result.scope_count = len(child_notes)
    if not child_notes:
        result.status = "no-sessions"
        return result

    # Recursive corpus clock (§8.3): the manager's "now" is the freshest
    # instant any child has distilled through — corpus-derived, never
    # wall-clock, so two runs any wall-time apart are byte-identical.
    if options.as_of:
        corpus_now_iso = str(options.as_of)
    elif stamps:
        corpus_now_iso = max(stamps, key=_instant_key)
    else:
        raise DistillError(
            f"manager {manager_key}: no child artifact carries distilled_through "
            "and no as_of was given (spec §7.1/§8.3)."
        )
    result.distilled_through = corpus_now_iso

    # Watermark: lineage-set + content_hash derived (§8.3 step 6). A child
    # re-distill that changes no constituent's content bytes reproduces this
    # hash exactly → skipped-watermark, zero manager work.
    hash_lines = sorted(
        f"{key}|{note.id}|{note.metadata.get('lineage_key')}|{note.metadata.get('content_hash')}"
        for key, note in child_notes
    )
    hash_lines.append("children:" + ",".join(sorted(children)))
    # §12 Phase-5: fold the manager's own guidance stream into its watermark so
    # an edit re-triggers the rollup (gated — empty stream is byte-identical).
    if options.guidance:
        hash_lines.append("guidance:" + _compute_guidance_digest(options.guidance))
    input_hash = hashlib.sha256("\n".join(hash_lines).encode("utf-8")).hexdigest()
    result.input_hash = input_hash
    artifact_path = agent_artifact_path(project_root, manager_key)
    result.artifact_path = artifact_path

    if not options.full and not options.dry_run:
        watermark = state.get(DistillStateStore.SCOPE_WATERMARK, manager_key, "")
        if watermark == input_hash and artifact_path.is_file():
            result.status = "skipped-watermark"
            return result

    # ---------------- select & dedup (§8.3 steps 1-2) ----------------
    # Step 1 — dedup by lineage identity: same lineage_key from two children
    # is the same knowledge; representative = (-member_count, id).
    by_lineage: Dict[str, List[ResearchNode]] = {}
    for _key, note in child_notes:
        lineage = str((note.metadata or {}).get("lineage_key") or note.id)
        by_lineage.setdefault(lineage, []).append(note)
    reps: List[ResearchNode] = []
    lineage_dups: List[ResearchNode] = []
    for lineage in sorted(by_lineage):
        group = sorted(
            by_lineage[lineage],
            key=lambda n: (-int((n.metadata or {}).get("member_count") or 0), n.id),
        )
        reps.append(group[0])
        lineage_dups.extend(group[1:])

    # Step 2 — group by raw-root ref-set overlap (Jaccard ≥ 0.5), NEVER by
    # titles. ponytail: O(n²) pairwise over deduped reps — a manager rolls up
    # tens of notes, not thousands; index it like §5.2 if that changes.
    reps.sort(key=lambda n: n.id)
    uf = _UnionFind()
    for note in reps:
        uf.add(note.id)
    member_sets = {n.id: set(_note_member_ids(n)) for n in reps}
    for i, left in enumerate(reps):
        for right in reps[i + 1 :]:
            a, b = member_sets[left.id], member_sets[right.id]
            union_size = len(a | b)
            overlap = len(a & b) / union_size if union_size else 0.0
            if overlap >= _MANAGER_GROUP_JACCARD:
                uf.union(left.id, right.id)
    grouped: Dict[str, List[ResearchNode]] = {}
    for note in reps:
        grouped.setdefault(uf.find(note.id), []).append(note)

    # ---------------- carry & arbitrate (§8.3 steps 3-5) ----------------
    prior = _load_prior_artifact(artifact_path)
    run_seq = state.current_run_seq() + 1 if not options.dry_run else state.current_run_seq()
    stage = _LLMStage(
        agent_key=manager_key,
        summarizer=summarizer,
        cache_root=distill_cache_dir(project_root),
        state=None if options.dry_run else state,
        options=options,
        prior_distillates=prior["distillates"],
        run_seq=run_seq,
        result=result,
        breaker=breaker,
    )

    carried: List[ResearchNode] = []
    sibling_index_pool: List[ResearchNode] = []
    for root_id in sorted(grouped):
        group = sorted(
            grouped[root_id],
            key=lambda n: (-int((n.metadata or {}).get("member_count") or 0), n.id),
        )
        rep = group[0]
        siblings = group[1:]
        group_with_dups = group + [
            d for d in lineage_dups
            if str((d.metadata or {}).get("lineage_key"))
            in {str((n.metadata or {}).get("lineage_key")) for n in group}
        ]
        union_lineage = compute_lineage_key(
            {n.id: _note_member_ids(n) for n in group_with_dups}
        )
        union_refs = _merged_refs(group_with_dups)
        first_seen = sorted(
            str((n.metadata or {}).get("first_seen_at"))
            for n in group_with_dups
            if (n.metadata or {}).get("first_seen_at")
        )

        # Step 3 — verbatim carry: body/title/kind untouched from the
        # representative. No paraphrase-of-paraphrase (LLM depth stays 1).
        metadata: Dict[str, object] = {
            "agent": manager_key,
            "kind": str((rep.metadata or {}).get("kind") or "note"),
            "lineage_key": union_lineage,
            "content_hash": str((rep.metadata or {}).get("content_hash") or ""),
            "member_count": len(union_refs),
            "member_refs": union_refs,
            "absorbed_refs": [],
            "distill_quality": str((rep.metadata or {}).get("distill_quality") or "structural"),
            "distilled_through": corpus_now_iso,
        }
        if first_seen:
            metadata["first_seen_at"] = first_seen[0]
        carried.append(
            ResearchNode(
                id=stable_id(
                    ResearchNodeType.DISTILLED_NOTE.value,
                    f"distilled:{manager_key}:{union_lineage[:16]}",
                ),
                name=rep.name,
                type=ResearchNodeType.DISTILLED_NOTE,
                description=rep.description,
                metadata=metadata,
            )
        )
        sibling_index_pool.extend(siblings)

        # Step 4 — arbitration: the ONLY prose minted at manager level.
        for left, right in _conflicting_note_pairs(group):
            arb_lineage = compute_lineage_key(
                {left.id: _note_member_ids(left), right.id: _note_member_ids(right)}
            )
            output, quality = stage.summarize_cluster([left, right], arb_lineage)
            arb_refs = _merged_refs([left, right])
            carried.append(
                ResearchNode(
                    id=stable_id(
                        ResearchNodeType.DISTILLED_NOTE.value,
                        f"distilled:{manager_key}:arb:{arb_lineage[:16]}",
                    ),
                    name=str(output["title"]),
                    type=ResearchNodeType.DISTILLED_NOTE,
                    description=str(output["body"]),
                    metadata={
                        "agent": manager_key,
                        "kind": "arbitration",
                        "lineage_key": arb_lineage,
                        "content_hash": hashlib.sha256(
                            str(output["body"]).encode("utf-8")
                        ).hexdigest()[:24],
                        "member_count": len(arb_refs),
                        "member_refs": arb_refs,
                        "absorbed_refs": [],
                        "distill_quality": quality,
                        "distilled_through": corpus_now_iso,
                    },
                )
            )

    result.cluster_count = len(grouped)
    result.distilled_count = len(carried)

    # ---------------- emit (§5.5 shape, manager flavor) ----------------
    agent_node, parent_node, reports_edge = _mint_agent_nodes(manager_key, registry, [])
    # Child artifacts carry their PARENT Agent node too (the manager itself,
    # minted by the child's worker pass) — never a report of its own.
    child_agent_nodes.pop(agent_node.id, None)
    reports_edges: List[ResearchEdge] = [] if reports_edge is None else [reports_edge]
    child_keys_set = set(children)
    for node in child_agent_nodes.values():
        if str((node.metadata or {}).get("agent_key") or "") not in child_keys_set:
            continue  # grandparents/org:root riding along in a child artifact
        reports_edges.append(
            ResearchEdge(source=node.id, target=agent_node.id, type="reports_to")
        )

    def _assemble(index_entries: List[ResearchNode], truncated: int) -> ResearchGraph:
        nodes: List[ResearchNode] = [agent_node]
        if parent_node is not None:
            nodes.append(parent_node)
        nodes.extend(child_agent_nodes[k] for k in sorted(child_agent_nodes))
        nodes.extend(child_profiles[k] for k in sorted(child_profiles))
        nodes.extend(carried)
        nodes.append(
            _mint_index_note(manager_key, index_entries, truncated, corpus_now_iso)
        )
        deduped_edges = {
            (e.source, e.type, e.target): e for e in reports_edges
        }
        return ResearchGraph(
            nodes=list({n.id: n for n in nodes}.values()),
            edges=[deduped_edges[key] for key in sorted(deduped_edges)],
        )

    budget = options.artifact_char_budget or ARTIFACT_CHAR_BUDGET
    # CTX-01 helper in render mode (§5.3) — same byte-identical migration as
    # the worker loop above.
    fit = fit_to_budget(
        sorted(sibling_index_pool, key=lambda n: n.id),
        budget,
        render=lambda kept, dropped: (
            _assemble(kept, dropped).canonicalized().to_json(indent=2) + "\n"
        ),
    )
    rendered = fit.payload or ""

    result.artifact_chars = len(rendered)
    size_level = artifact_size_level(len(rendered), budget)
    if size_level == "error":
        raise DistillSizeError(
            f"Distilled artifact for {manager_key} is {len(rendered)} chars — "
            f"exceeds the one-read bound of {budget} chars even after index "
            "truncation (spec §2/§7.2)."
        )
    result.size_level = size_level

    if options.dry_run:
        result.status = "dry-run"
        return result

    run_seq = state.bump_run_seq()
    new_bytes = rendered.encode("utf-8")
    existing = artifact_path.read_bytes() if artifact_path.is_file() else None
    if existing == new_bytes:
        result.status = "unchanged"
    else:
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = artifact_path.with_suffix(f".tmp.{os.getpid()}.{secrets.token_hex(4)}")
        try:
            tmp.write_bytes(new_bytes)
            os.replace(tmp, artifact_path)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
        result.status = "written"

    # Forget ledger (§6.2): at manager level "carried" plays the remainder
    # role — the diff records which rollup notes appeared / disappeared.
    carried_ids = sorted(n.id for n in carried)
    prior_ids = sorted(prior.get("distillate_ids") or [])
    diff = {
        "promoted": sorted(set(carried_ids) - set(prior_ids)),
        "demoted": sorted(set(prior_ids) - set(carried_ids)),
        "absorbed": [],
    }
    result.forget_diff = diff
    state.append(
        DistillStateStore.SCOPE_FORGET_LEDGER,
        manager_key,
        json.dumps(
            {
                "distilled_through": corpus_now_iso,
                "input_hash": input_hash,
                "remainder": carried_ids,
                **diff,
            },
            sort_keys=True,
        ),
    )
    state.put(DistillStateStore.SCOPE_WATERMARK, manager_key, "", input_hash)
    _prune_cache_lru(state, distill_cache_dir(project_root), run_seq)
    return result


# --------------------------------------------------------------------------- refresh trigger (§8.2)


def undistilled_slice_chars(
    graph: ResearchGraph, agent_key: str, project_root: Path | str
) -> int:
    """Rendered size of the agent's scope findings no distillate covers.

    The §8.2 memory-pressure signal: when this exceeds half the LLM chunk
    budget, raw recall for the agent no longer fits one read and
    consolidation should fire.
    """
    _sessions, findings, _extras = _scope_for_agent(graph, agent_key)
    if not findings:
        return 0
    covered: Set[str] = set()
    path = agent_artifact_path(project_root, agent_key)
    if path.is_file():
        prior = _load_prior_artifact(path)
        covered.update(prior["absorbed_ids"])
        for distillate in prior["distillates"]:
            covered.update(distillate["member_ids"])
    return sum(
        len(_render_member_block(node))
        for node in findings
        if node.id not in covered
    )


def maybe_distill_on_refresh(
    project_root: Path | str,
    graph: ResearchGraph,
    *,
    cfg: Optional[dict] = None,
    env: Optional[Mapping[str, str]] = None,
    summarizer: Optional[Summarizer] = None,
    options: Optional[DistillOptions] = None,
) -> Dict[str, object]:
    """Refresh-flow hook (§8.2): distill under memory pressure, never always.

    Gated three ways: the ``TESSERAE_AGENT_DISTILL`` opt-in, a changed
    watermark (something actually new), and the memory-pressure signal
    (undistilled slice > half the LLM chunk budget). Managers are re-rolled
    afterwards only if any child wrote. Returns a summary dict for the
    pipeline step output; never raises for per-agent failures — refresh must
    not die because one agent's distill did.
    """
    if not agent_distill_enabled(cfg, env):
        return {"skipped": "agent distill gate off (TESSERAE_AGENT_DISTILL)"}
    project_root = Path(project_root)
    registry = AgentRegistry.for_project(project_root)
    state = DistillStateStore(_state_db_path(project_root))
    pressure_floor = chunk_char_budget() // 2
    ran: List[str] = []
    skipped: List[str] = []
    failed: List[str] = []
    workers = [
        key
        for key in known_agent_keys(graph, registry)
        if not manager_children(graph, registry, key)
    ]
    for key in workers:
        slice_chars = undistilled_slice_chars(graph, key, project_root)
        if slice_chars <= pressure_floor:
            skipped.append(key)
            continue
        try:
            outcome = distill_agent(
                graph,
                key,
                project_root=project_root,
                registry=registry,
                summarizer=summarizer,
                options=options,
            )
        except DistillError as exc:
            logger.warning("refresh distill for %s failed: %s", key, exc)
            failed.append(key)
            continue
        if outcome.status in {"written", "unchanged"}:
            ran.append(key)
        else:
            skipped.append(key)
    wrote = {key for key in ran}
    # Managers roll whenever every child artifact exists — their lineage-set
    # watermark makes the repeat case one hash comparison. A manager with a
    # missing child is a loud FAILURE only when this run's writes made the
    # rollup stale; otherwise it just isn't ready yet (skip, don't nag).
    managers = [
        key
        for key in known_agent_keys(graph, registry)
        if manager_children(graph, registry, key)
    ]
    for key in managers:
        kids = manager_children(graph, registry, key)
        ready = all(agent_artifact_path(project_root, k).is_file() for k in kids)
        if not ready:
            (failed if any(k in wrote for k in kids) else skipped).append(key)
            continue
        try:
            outcome = distill_agent(
                graph,
                key,
                project_root=project_root,
                registry=registry,
                summarizer=summarizer,
                options=options,
            )
        except DistillError as exc:
            logger.warning("refresh distill for manager %s failed: %s", key, exc)
            failed.append(key)
            continue
        (ran if outcome.status in {"written", "unchanged"} else skipped).append(key)
    return {"distilled": sorted(ran), "skipped": sorted(skipped), "failed": sorted(failed)}


# --------------------------------------------------------------------------- minting helpers


def _mint_agent_nodes(
    agent_key: str,
    registry: AgentRegistry,
    sessions: Sequence[ResearchNode],
) -> Tuple[ResearchNode, Optional[ResearchNode], Optional[ResearchEdge]]:
    """Agent node + parent + ``reports_to`` edge (artifact section 1, §5.5).

    Reuses ``session_graph_structural._agent_metadata`` so the artifact's
    Agent node metadata byte-matches the L0 one (anchor convergence by id).
    """
    registry_agents = registry.load().get("agents") or {}
    observed_labels = {
        str((node.metadata or {}).get("agent_label") or "").strip()
        for node in sessions
    } - {""}
    agent_node = ResearchNode(
        id=stable_id(ResearchNodeType.AGENT.value, f"agent:{agent_key}"),
        name=agent_key,
        type=ResearchNodeType.AGENT,
        metadata=_agent_metadata(
            agent_key, registry_agents.get(agent_key), observed_labels or None
        ),
    )
    parent_key = registry.effective_parent(agent_key)
    if parent_key == agent_key:
        return agent_node, None, None
    parent_node = ResearchNode(
        id=stable_id(ResearchNodeType.AGENT.value, f"agent:{parent_key}"),
        name=parent_key,
        type=ResearchNodeType.AGENT,
        metadata=_agent_metadata(parent_key, registry_agents.get(parent_key), None),
    )
    edge = ResearchEdge(source=agent_node.id, target=parent_node.id, type="reports_to")
    return agent_node, parent_node, edge


def _mint_profile(
    agent_key: str,
    sessions: Sequence[ResearchNode],
    findings: Sequence[ResearchNode],
    *,
    anchor_counts_source: ResearchGraph,
    corpus_now_iso: str,
) -> ResearchNode:
    """Structural ``ExpertiseProfile`` (§8.2) — same id seed as the L0 one."""
    finding_counts: Dict[str, int] = {}
    for node in findings:
        finding_counts[_kind(node)] = finding_counts.get(_kind(node), 0) + 1

    finding_ids = {node.id for node in findings}
    nodes_by_id = {node.id: node for node in anchor_counts_source.nodes}
    concept_counts: Dict[str, int] = {}
    for edge in anchor_counts_source.edges:
        if edge.type not in _SCOPE_EDGE_TYPES:
            continue
        for member_id, other_id in ((edge.source, edge.target), (edge.target, edge.source)):
            if member_id not in finding_ids:
                continue
            other = nodes_by_id.get(other_id)
            if other is None or other.type not in _ANCHOR_TYPES:
                continue
            concept_counts[other_id] = concept_counts.get(other_id, 0) + 1
    top_concepts = [
        node_id
        for node_id, _count in sorted(
            concept_counts.items(), key=lambda kv: (-kv[1], kv[0])
        )[:10]
    ]
    return ResearchNode(
        id=stable_id(ResearchNodeType.EXPERTISE_PROFILE.value, f"profile:{agent_key}"),
        name=f"Expertise: {agent_key}",
        type=ResearchNodeType.EXPERTISE_PROFILE,
        metadata={
            "agent": agent_key,
            "session_count": len(sessions),
            "finding_counts": dict(sorted(finding_counts.items())),
            "top_concepts": top_concepts,
            "distilled_through": corpus_now_iso,
        },
    )


def _mint_index_note(
    agent_key: str,
    entries: Sequence[ResearchNode],
    truncated: int,
    corpus_now_iso: str,
) -> ResearchNode:
    """§5.5 item 5 — structural Index note; demotion target, never deletion.

    Deliberate interpretation of the §5.5/§6.2 wording: entries cover
    non-absorbed scope FINDINGS outside the remainder (matching the spec's
    own "+ N older undistilled findings" truncation line). Non-finding scope
    extras are not indexed — anchors travel with the distillates that cite
    them and Sessions with the Activity note; uncited extras stay reachable
    from L0. Widening this to literally every scope node is a spec-text
    question, not an oversight.
    """
    member_refs = [
        {"node_id": node.id, "content_hash": _node_content_hash(node)}
        for node in entries
    ]
    lines = [f"- {node.name} ({node.id})" for node in entries]
    if truncated:
        lines.append(
            f"+ {truncated} older undistilled findings — drill_down or lint backlog"
        )
    body = "Undistilled scope findings, newest first:\n" + "\n".join(lines) if lines else (
        "No undistilled findings outside the remainder."
    )
    first_seen = [
        str((node.metadata or {}).get("first_seen_at"))
        for node in entries
        if (node.metadata or {}).get("first_seen_at")
    ]
    metadata: Dict[str, object] = {
        "agent": agent_key,
        "kind": "index",
        "lineage_key": hashlib.sha256(
            "\n".join(sorted(ref["node_id"] for ref in member_refs)).encode("utf-8")
        ).hexdigest(),
        "content_hash": hashlib.sha256(body.encode("utf-8")).hexdigest()[:24],
        "member_count": len(member_refs),
        "member_refs": member_refs,
        "absorbed_refs": [],
        "distill_quality": "structural",
        "distilled_through": corpus_now_iso,
    }
    if first_seen:
        metadata["first_seen_at"] = min(first_seen)
    return ResearchNode(
        id=stable_id(
            ResearchNodeType.DISTILLED_NOTE.value, f"distilled:{agent_key}:index"
        ),
        name=f"Index: undistilled findings ({agent_key})",
        type=ResearchNodeType.DISTILLED_NOTE,
        description=body,
        metadata=metadata,
    )


def _mint_activity_note(
    agent_key: str,
    sessions: Sequence[ResearchNode],
    corpus_now_iso: str,
) -> ResearchNode:
    """§5.5 item 6 — structural Activity note: last-10 session titles + dates."""
    def _clock(node: ResearchNode) -> str:
        meta = node.metadata or {}
        return str(meta.get("ended_at") or meta.get("started_at") or "")

    # Recency by parsed instant (mixed timestamp spellings do not order
    # lexicographically), raw string + id as deterministic tiebreaks.
    recent = sorted(
        sessions, key=lambda n: (*_instant_key(_clock(n)), n.id), reverse=True
    )[:10]
    lines = [f"- {_clock(node) or 'undated'} — {node.name}" for node in recent]
    body = "Recent sessions:\n" + "\n".join(lines) if lines else "No sessions in scope."
    member_refs = [
        {"node_id": node.id, "content_hash": _node_content_hash(node)}
        for node in sorted(recent, key=lambda n: n.id)
    ]
    stamps = [_clock(node) for node in recent if _clock(node)]
    metadata: Dict[str, object] = {
        "agent": agent_key,
        "kind": "activity",
        "lineage_key": hashlib.sha256(
            "\n".join(sorted(ref["node_id"] for ref in member_refs)).encode("utf-8")
        ).hexdigest(),
        "content_hash": hashlib.sha256(body.encode("utf-8")).hexdigest()[:24],
        "member_count": len(member_refs),
        "member_refs": member_refs,
        "absorbed_refs": [],
        "distill_quality": "structural",
        "distilled_through": corpus_now_iso,
    }
    if stamps:
        metadata["first_seen_at"] = min(stamps)
    return ResearchNode(
        id=stable_id(
            ResearchNodeType.DISTILLED_NOTE.value, f"distilled:{agent_key}:activity"
        ),
        name=f"Activity: {agent_key}",
        type=ResearchNodeType.DISTILLED_NOTE,
        description=body,
        metadata=metadata,
    )


# --------------------------------------------------------------------------- cache GC (§6.3)


def _prune_cache_lru(
    state: DistillStateStore, cache_root: Path, run_seq: int, keep_runs: int = _CACHE_KEEP_RUNS
) -> None:
    """Prune cache entries unused for ``keep_runs`` executed runs (LRU age).

    LRU — not exact-set difference — so a cluster that flaps and returns hits
    its old entry (§5.3). Touch bookkeeping lives in ``agent_distill_state``.
    """
    if not cache_root.is_dir():
        return
    for _entry_id, _agent, lineage_key, raw in state.rows(DistillStateStore.SCOPE_CACHE_USE):
        try:
            last_used = int(raw)
        except ValueError:
            continue
        if run_seq - last_used <= keep_runs:
            continue
        path = _cache_path(cache_root, lineage_key)
        if path.is_file():
            try:
                path.unlink()
            except OSError:
                continue
        # The cache_use row stays behind; the keyed put() overwrites on reuse.
