"""The ``.tesserae/`` sidecar registry — who owns each entry, and what a
deletion costs (roadmap step 10).

``.tesserae/`` holds ~60 entries on a mature project and nothing on disk says
which of them a compile can rebuild, which hold state a compile CANNOT
re-derive, and which are crash debris. The concrete failure that motivates
this: ``candidate-same-as.json`` carries human "no, these are different"
verdicts, and a compile that cannot find it does not error — it silently
re-asks a question a human already answered. The same directory also holds
``compile.lock`` and five zero-byte ``manifest.tmp.<pid>.<hex>`` orphans, and
today they look exactly alike to anyone reading a directory listing.

Borrowed from agent-memory's ``SchemaManager``, whose ``_is_memory_schema``
matches its own schema objects by name so ``drop_all()`` and
``get_schema_info()`` touch only what the library created and never a user's
objects. :func:`is_tesserae_sidecar` is that predicate; :func:`classify` is
what it consults.

**Namespacing is expressed by enumeration, not by a name prefix, and nothing
on disk moves.** agent-memory can prefix because it CREATES its schema objects
on a database it is handed. Tesserae's sidecars already exist, under these
exact names, in every installed project — so a rename would either orphan the
one file a compile cannot rebuild (``candidate-same-as.json``) or add a
permanent read-the-old-location fallback to every writer for no gain. The
registry is the namespace: an entry here is Tesserae's, an unmatched entry is
somebody else's and is reported rather than touched.

The two fields carry different questions and are deliberately independent:

* ``kind`` — where the bytes come from. ``derived`` is republished by a
  compile, ``accumulated`` is appended to over time and re-derivable by
  nothing, ``cache`` is a stored answer to a question that can be asked again,
  ``scratch`` is process bookkeeping and debris.
* ``safe_to_delete`` — whether a bulk reset may remove it *without asking*.
  A ``cache`` whose answer comes from an LLM is NOT safe to delete: rebuilding
  it re-runs a non-deterministic extractor, so the next ``graph.json`` differs
  in bytes from the last — the byte-idempotence break this repo has taken four
  times. A held ``compile.lock`` is not safe to delete either, for a reason
  that has nothing to do with what it contains.

Deliberately NOT in scope here: byte-stability. ``output_snapshot``'s
allowlist answers "is this artifact proven byte-stable enough to hash", which
is a different question with a different answer for the same file
(``report.md`` is ``derived`` and excluded from that hash), and merging the
two lists would make one of them wrong.

Per the roadmap, this module adds NO destructive verb. A ``reset`` command is
what the classification makes possible; shipping one in the same change that
first writes the classification down is the wrong order.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

__all__ = [
    "KIND_ACCUMULATED",
    "KIND_CACHE",
    "KIND_DERIVED",
    "KIND_SCRATCH",
    "KINDS",
    "SCOPE_PROJECT",
    "SCOPE_USER",
    "SCOPES",
    "SIDECARS",
    "Sidecar",
    "classify",
    "is_tesserae_sidecar",
    "of_kind",
    "tmp_owner_pid",
    "unclassified_entries",
]

#: Republished by a compile from the sources. Losing it costs a recompile.
KIND_DERIVED = "derived"
#: Appended to over time; no compile can re-derive it. Losing it is data loss.
KIND_ACCUMULATED = "accumulated"
#: A stored answer to a question that can be asked again. Losing it costs time
#: — and, when the answer came from an LLM, costs determinism as well.
KIND_CACHE = "cache"
#: Process bookkeeping and debris: locks, pidfiles, orphaned tmp files.
KIND_SCRATCH = "scratch"

KINDS: frozenset = frozenset({KIND_DERIVED, KIND_ACCUMULATED, KIND_CACHE, KIND_SCRATCH})

#: ``<project>/.tesserae/`` — per-project workspace.
SCOPE_PROJECT = "project"
#: ``~/.tesserae/`` — machine-wide state shared by every project. Same
#: directory name, different contents; ``config.json`` exists in both and means
#: something different in each, which is why scope is a field and not a guess.
SCOPE_USER = "user"

SCOPES: frozenset = frozenset({SCOPE_PROJECT, SCOPE_USER})


@dataclass(frozen=True)
class Sidecar:
    """One declared ``.tesserae/`` entry.

    ``name`` is a basename relative to the scope root, or an ``fnmatch``
    pattern when the writer generates the name (pids, random tmp suffixes).
    ``why`` states what is lost by deleting it — the field that makes the
    registry a decision record rather than a list.
    """

    name: str
    owner: str
    kind: str
    safe_to_delete: bool
    why: str
    scope: str = SCOPE_PROJECT

    @property
    def is_pattern(self) -> bool:
        return any(ch in self.name for ch in "*?[")


# ---------------------------------------------------------------------------
# the registry
# ---------------------------------------------------------------------------
#
# Ordered by kind so a reader scanning for "what must never be deleted" finds
# it as one block. Within a kind, alphabetical.

SIDECARS: Tuple[Sidecar, ...] = (
    # -- derived: a compile republishes these ------------------------------
    Sidecar("agent_harness", "tesserae.agent_harness", KIND_DERIVED, True,
            "compiled per-agent context briefs; regenerated by the harness pass"),
    Sidecar("code-graph.json", "tesserae.code_graph", KIND_DERIVED, True,
            "code-symbol graph; re-extracted from the working tree"),
    Sidecar("combined-graph.json", "tesserae.project", KIND_DERIVED, True,
            "research graph unioned with the code graph"),
    Sidecar("competitive_report.md", "tesserae.report", KIND_DERIVED, True,
            "rendered comparison report; a compile re-renders it from the graph"),
    Sidecar("diverged-fields.md", "tesserae.vault_pull", KIND_DERIVED, True,
            "per-compile audit of vault divergences; the next compile rewrites it"),
    Sidecar("doctor-report.json", "tesserae.doctor", KIND_DERIVED, True,
            "last doctor run; rerun to rebuild"),
    Sidecar("doctor-report.md", "tesserae.doctor", KIND_DERIVED, True,
            "last doctor run; rerun to rebuild"),
    Sidecar("graph.json", "tesserae.project", KIND_DERIVED, True,
            "the compiled artifact — a pure function of sources plus the "
            "accumulated sidecars, which is why THOSE are the ones to protect"),
    Sidecar("graph.kuzu", "tesserae.kuzu_adapter", KIND_DERIVED, True,
            "one-way kuzu export beside okf/ and the Graphiti episodes"),
    Sidecar("graphiti_episodes.jsonl", "tesserae.graphiti_adapter", KIND_DERIVED, True,
            "one-way Graphiti export projected from the temporal facts"),
    Sidecar("hierarchy.json", "tesserae.project", KIND_DERIVED, True,
            "Louvain dendrogram sidecar; a pure function of graph content"),
    Sidecar("lint-report.json", "tesserae.lint", KIND_DERIVED, True,
            "last lint run; rerun to rebuild"),
    Sidecar("lint-report.md", "tesserae.lint", KIND_DERIVED, True,
            "last lint run; rerun to rebuild"),
    Sidecar("log.md", "tesserae.karpathy_layer", KIND_DERIVED, True,
            "build log rendered from .build-history.jsonl — the LEDGER is the "
            "state, this is its projection"),
    Sidecar("markdown_projection", "tesserae.markdown_projection", KIND_DERIVED, True,
            "markdown projection of the graph"),
    Sidecar("merge-ledger.json", "tesserae.merge_ledger", KIND_DERIVED, True,
            "loser->survivor map, revalidated against the published graph on "
            "every ingest — derived, unlike candidate-same-as.json, which is "
            "the deliberate contrast the merge ledger's own docstring draws"),
    Sidecar("okf", "tesserae.okf", KIND_DERIVED, True,
            "OKF bundle export directory"),
    Sidecar("okf-imported.graph.json", "tesserae.okf", KIND_DERIVED, True,
            "graph reconstructed from an OKF bundle import"),
    Sidecar("output-snapshot.json", "tesserae.output_snapshot", KIND_DERIVED, True,
            "artifact digests; the next compile rewrites both halves"),
    Sidecar("report.md", "tesserae.report", KIND_DERIVED, True,
            "rendered compile report; a compile re-renders it from the graph"),
    Sidecar("research", "tesserae.research_mode", KIND_DERIVED, True,
            "research-mode report output, one markdown file per run"),
    Sidecar("schema-drift.md", "tesserae.schema_drift", KIND_DERIVED, True,
            "human-readable rendering of the drift run; the APPROVALS live in "
            "schema-drift-proposals.json, not here"),
    Sidecar("schema-drift-proposals.json", "tesserae.schema_drift", KIND_DERIVED, False,
            "sub-type proposals are LLM-derived AND carry the human 'approved' "
            "gate plus an editable proposed_type in the same record, so a "
            "rebuild both costs an LLM pass and discards the approvals — "
            "derived bytes, non-derivable content"),
    Sidecar("site", "tesserae.site", KIND_DERIVED, True,
            "static site; the builder clears and rewrites it every compile"),
    Sidecar("summaries", "tesserae.activity_summary", KIND_DERIVED, True,
            "rendered activity summaries per project and day"),
    Sidecar("temporal_facts.jsonl", "tesserae.temporal", KIND_DERIVED, True,
            "fact projection; valid_from/valid_to are source-derived, and the "
            "transaction-time axis lives in sqlite.db, not here"),
    Sidecar("wiki", "tesserae.wiki_store", KIND_DERIVED, True,
            "wiki pages projected from the graph"),

    # -- accumulated: nothing can re-derive these --------------------------
    Sidecar(".build-history.jsonl", "tesserae.project", KIND_ACCUMULATED, False,
            "one line per build with the git_head the graph was compiled at; "
            "deleting it makes graph staleness permanently unknown"),
    Sidecar("agent-writes.jsonl", "tesserae.agent_write", KIND_ACCUMULATED, False,
            "the agent-authored overlay, replayed as a 5th producer on every "
            "compile — deleting it erases every agent write, which is exactly "
            "what replay exists to prevent"),
    Sidecar("agents", "tesserae.agent_harness", KIND_ACCUMULATED, False,
            "per-agent registry.json and the human-editable purpose.md beside "
            "the regenerable distilled artifacts"),
    Sidecar("candidate-same-as.json", "tesserae.candidate_ledger", KIND_ACCUMULATED, False,
            "human same-as verdicts. THE file this registry exists for: a "
            "compile that cannot find it does not fail, it silently re-asks a "
            "question a human already answered, and a rejected pair comes back "
            "un-rejected"),
    Sidecar("charter", "tesserae.charter", KIND_ACCUMULATED, False,
            "the project charter is authored, not extracted"),
    Sidecar("config.json", "tesserae.project", KIND_ACCUMULATED, False,
            "project configuration, including obsidian.vault_path — user input, "
            "never regenerated"),
    Sidecar("discovered_links.json", "tesserae.memory.associate", KIND_ACCUMULATED, False,
            "the association overlay accumulates scored shares_concept_with "
            "links across runs; a single run does not reconstruct it"),
    Sidecar("extraction-feedback.jsonl", "tesserae.extraction_feedback", KIND_ACCUMULATED, False,
            "human corrections captured during vault overlay and review-apply"),
    Sidecar("extraction-guidance.md", "tesserae.extraction_guidance", KIND_ACCUMULATED, False,
            "human-curatable guidance distilled from the feedback ledger; "
            "hand edits live here and an evolve pass merges into them"),
    Sidecar("harness_sessions", "tesserae.harness_sessions", KIND_ACCUMULATED, False,
            "imported session state"),
    Sidecar("harness_sessions.db", "tesserae.harness_sessions_db", KIND_ACCUMULATED, False,
            "imported agent sessions — input state whose upstream transcripts "
            "rotate away, so a re-import does not reconstruct it"),
    Sidecar("manifest.json", "tesserae.batch", KIND_ACCUMULATED, False,
            "per-source ingest state; deleting it makes the next batch re-ingest "
            "everything and re-run extraction against sources it already read"),
    Sidecar("obsidian_vault", "tesserae.obsidian_adapter", KIND_ACCUMULATED, False,
            "bidirectional and user-owned: user edits here are pulled back into "
            "the graph, so it is not a projection that can simply be redrawn"),
    Sidecar("session_chunks.db", "tesserae.session_chunks", KIND_ACCUMULATED, False,
            "normalised turns bucketed by day, written live by the daemon's "
            "tailer from transcripts that do not stay available"),
    Sidecar("sqlite.db", "tesserae.graph_stores.sqlite", KIND_ACCUMULATED, False,
            "MIXED, and classified by its most valuable table: the graph mirror "
            "is derived, node_vectors and bm25_docs/bm25_postings are caches, "
            "but node_memory (decay, "
            "access counts, reinforced confidence), fact_observed (transaction "
            "time — a real wall clock that only ever moves forward) and "
            "read_audit are all unrecoverable. Dropping the file to reclaim the "
            "vector cache or the inverted index resets every fact's 'when we "
            "learned it' to now"),
    Sidecar("vault_snapshot.json", "tesserae.vault_snapshot", KIND_ACCUMULATED, False,
            "the baseline vault_pull diffs against. Deleting it mid-edit makes "
            "the next compile unable to tell a user's edit from its own prior "
            "projection — the vault's whole override mechanism"),

    # -- cache: re-askable, but read safe_to_delete before dropping --------
    Sidecar(".watch-cache.json", "tesserae.watch", KIND_CACHE, True,
            "watcher's seen-file state"),
    Sidecar("arxiv-cache.json", "tesserae.research_graph", KIND_CACHE, True,
            "arXiv metadata lookups; refetched on demand"),
    Sidecar("code-graph-cache.json", "tesserae.code_graph", KIND_CACHE, True,
            "stat manifest for delta-scoped re-extraction; deterministic to "
            "rebuild because the extractor is a parser, not a model"),
    Sidecar("community_summaries", "tesserae.community_summaries", KIND_CACHE, False,
            "LLM-written community summaries keyed on the member hash — "
            "rebuilding calls a model, so the rebuilt bytes differ"),
    Sidecar("distill_cache", "tesserae.agent_distill", KIND_CACHE, False,
            "LLM distillation results; rebuilding calls a model"),
    Sidecar("distillation_cache", "tesserae.project", KIND_CACHE, False,
            "LLM distillation results; rebuilding calls a model"),
    Sidecar("external", "tesserae.raganything_refresh", KIND_CACHE, True,
            "third-party backend artifacts; `tesserae integrations refresh` "
            "rebuilds them"),
    Sidecar("extraction_guidance_cache", "tesserae.extraction_guidance", KIND_CACHE, False,
            "LLM-phrased bullet per feedback cluster; rebuilding calls a model"),
    Sidecar("schema_drift_cache", "tesserae.schema_drift", KIND_CACHE, False,
            "LLM sub-type proposals per host type; rebuilding calls a model"),
    Sidecar("session_findings", "tesserae.session_graph", KIND_CACHE, False,
            "LLM-minted findings per session chunk. The sharpest case on this "
            "list: these findings become NODES, so deleting the cache re-runs a "
            "non-deterministic extractor and the next graph.json differs in "
            "bytes — the byte-idempotence break this repo has taken four times"),
    Sidecar("supersede_cache", "tesserae.project", KIND_CACHE, False,
            "LLM supersede arbitration; rebuilding calls a model"),

    # -- scratch: process bookkeeping and debris ---------------------------
    Sidecar(".recompile.lock.d", "hooks/posttooluse-edit.sh", KIND_SCRATCH, False,
            "mkdir-based hook mutex; removing a held one lets two recompiles race"),
    Sidecar("*.tmp*", "tesserae (atomic tmp+replace writers)", KIND_SCRATCH, True,
            "orphaned half of a tmp+replace write. Named <target>.tmp.<pid>."
            "<hex> since the fixed-name form let two writers race on one path; "
            "only removable once the owning pid is gone, because a live writer "
            "is mid-rename"),
    Sidecar(".*-hook.log*", "hooks/*.sh", KIND_SCRATCH, True,
            "shell-hook diagnostics; doctor rotates the oversized ones"),
    Sidecar("compile.lock", "tesserae.locking", KIND_SCRATCH, False,
            "the compile mutex. NEVER removed by any automated path — the "
            "recorded failure mode is SessionEnd compile pile-ups, and doctor's "
            "compile_lock check is report-only for the same reason"),
    Sidecar("daemon*.pid", "tesserae.engine.daemon", KIND_SCRATCH, False,
            "engine pidfile, host-scoped as daemon.<host>.pid. Only doctor's "
            "daemon_pid fix removes one, and only after confirming the recorded "
            "owner is dead ON THIS machine"),
    Sidecar("graph.json.bak-*", "operator (manual backup)", KIND_SCRATCH, False,
            "no Tesserae code path writes these; they are hand-made copies from "
            "a restore session. Reported, never removed — a human made them"),
    Sidecar("session_chunks.lock", "tesserae.session_chunks", KIND_SCRATCH, False,
            "backfill's skip-if-held flock; removing a held one lets two "
            "backfills write the same day"),

    # -- user scope: ~/.tesserae/ ------------------------------------------
    Sidecar("config.json", "tesserae.llm_json", KIND_ACCUMULATED, False,
            "machine-wide LLM configuration; user input", SCOPE_USER),
    Sidecar("engine.pid", "tesserae.engine.fleet", KIND_SCRATCH, False,
            "fleet pidfile; a stale one held a 6-day-dead pid once, which is "
            "why pidlock validates rather than trusts", SCOPE_USER),
    Sidecar("engine.pid.lock", "tesserae.engine.pidlock", KIND_SCRATCH, False,
            "fleet pidfile mutex; removing a held one lets two fleets start", SCOPE_USER),
    Sidecar("federation", "tesserae.federation", KIND_CACHE, True,
            "cross-project link and vector caches", SCOPE_USER),
    Sidecar("harness_sessions", "tesserae.harness_sessions", KIND_ACCUMULATED, False,
            "machine-wide session import state", SCOPE_USER),
    Sidecar("host_id", "tesserae.harness_sessions", KIND_ACCUMULATED, False,
            "this machine's identity. Regenerating it makes every host-scoped "
            "pidfile and session record on shared storage look foreign", SCOPE_USER),
    Sidecar("llm_cache", "tesserae.llm_json", KIND_CACHE, False,
            "cached LLM responses; a rebuild calls models and does not "
            "reproduce them", SCOPE_USER),
    Sidecar("registry.json", "tesserae.mcp_server", KIND_ACCUMULATED, False,
            "the project registry. Deleting it unregisters every project on "
            "this machine", SCOPE_USER),
    Sidecar("*.bak*", "operator / release migration", KIND_SCRATCH, False,
            "pre-migration copies of registry.json and config.json. No code "
            "path writes them, so they exist because somebody wanted them "
            "kept — reported, never removed", SCOPE_USER),
    Sidecar("wiki", "tesserae.cli", KIND_DERIVED, True,
            "machine-scoped serve scratch", SCOPE_USER),
)


# ---------------------------------------------------------------------------
# the predicate
# ---------------------------------------------------------------------------

#: ``<target>.tmp.<pid>.<hex>`` — the tmp+replace name every atomic writer in
#: the package builds (``batch.py``, ``project.py``, ``session_graph.py``, ...).
_TMP_NAME = re.compile(r"\.tmp\.(\d+)\.[0-9a-f]+$")


def _entries(scope: str) -> List[Sidecar]:
    if scope not in SCOPES:
        raise ValueError(f"unknown sidecar scope: {scope!r} (expected one of {sorted(SCOPES)})")
    return [s for s in SIDECARS if s.scope == scope]


def classify(name: str, *, scope: str = SCOPE_PROJECT) -> Optional[Sidecar]:
    """Return the registry entry owning ``name``, or ``None`` if it is not ours.

    ``name`` is a basename relative to the scope root. Exact entries win over
    patterns so a literal name is never captured by a wildcard that happens to
    match it — ``graph.json`` must not resolve through ``graph.json.bak-*``.
    """
    candidates = _entries(scope)
    for sidecar in candidates:
        if not sidecar.is_pattern and sidecar.name == name:
            return sidecar
    for sidecar in candidates:
        if sidecar.is_pattern and fnmatch.fnmatchcase(name, sidecar.name):
            return sidecar
    return None


def is_tesserae_sidecar(name: str, *, scope: str = SCOPE_PROJECT) -> bool:
    """Is ``name`` an entry Tesserae writes?

    The direct analogue of agent-memory's ``_is_memory_schema``: it exists so
    an automated pass can act on Tesserae's own entries and leave everything
    else — a user's notes, another tool's cache — alone.
    """
    return classify(name, scope=scope) is not None


def of_kind(kind: str, *, scope: str = SCOPE_PROJECT) -> Tuple[Sidecar, ...]:
    """Every registered entry of one ``kind``, in registry order."""
    if kind not in KINDS:
        raise ValueError(f"unknown sidecar kind: {kind!r} (expected one of {sorted(KINDS)})")
    return tuple(s for s in _entries(scope) if s.kind == kind)


def tmp_owner_pid(name: str) -> Optional[int]:
    """The pid embedded in a ``*.tmp.<pid>.<hex>`` name, or ``None``.

    Callers use it to decide whether an orphan is really orphaned. It answers
    about the writing process only — whether that pid is alive, and whether it
    is alive on THIS machine, is the caller's question, because several hosts
    can mount one ``.tesserae``.
    """
    match = _TMP_NAME.search(name)
    if match is None:
        return None
    try:
        return int(match.group(1))
    except ValueError:  # pragma: no cover - regex already guarantees digits
        return None


def unclassified_entries(root: Path, *, scope: str = SCOPE_PROJECT) -> List[str]:
    """Names directly under ``root`` that no registry entry claims, sorted.

    Report-only by contract. An unclassified entry is more likely to be
    somebody else's file than a bug — on this project it is a hand-redirected
    ``compile-restore5.log`` and three vendored eval bundles — so the answer to
    finding one is to name it, not to remove it. It is also how a new Tesserae
    sidecar that nobody registered becomes visible.
    """
    try:
        names = [p.name for p in root.iterdir()]
    except OSError:
        return []
    return sorted(n for n in names if not is_tesserae_sidecar(n, scope=scope))


def summary(scope: str = SCOPE_PROJECT) -> Dict[str, int]:
    """``{kind: count}`` over the registry — the shape doctor reports."""
    counts = {kind: 0 for kind in sorted(KINDS)}
    for sidecar in _entries(scope):
        counts[sidecar.kind] += 1
    return counts
