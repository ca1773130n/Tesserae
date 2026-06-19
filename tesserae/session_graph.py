"""SessionGraphExtractor — orchestrator for the two-pass session extraction.

Combines the deterministic structural pass
(:mod:`tesserae.session_graph_structural`) with the LLM-backed
finding extraction (:mod:`tesserae.session_graph_llm`) into a single
:class:`ResearchGraph` slice that
:func:`tesserae.project.merge_graphs` can fold into the doc graph.

Caching (per-CHUNK, SESS-02): each session's LLM-extracted findings are
persisted PER CHUNK to
``.tesserae/session_findings/<session_id>/chunk-<K>.json`` with a
chunk_content_hash AND a project_root_hash envelope. Turns are
partitioned into stable, NON-overlapping chunks of
``max_turns_per_chunk`` aligned to the ORIGINAL transcript indices
(chunk k = turns[k*size : (k+1)*size]). On the next compile we skip the
LLM call for every chunk whose content hash is unchanged and only
re-extract the chunks that changed.

Why chunk-level instead of per-turn? The extractor (:func:`extract_with_llm`)
produces findings that can span multiple turns WITHIN a chunk. Extracting
one turn at a time would (a) renumber each turn's id to 0 and (b) make
cross-turn findings impossible — so the "incremental == whole-session"
merge guarantee would be false for the real extractor. Caching at the
extractor's natural chunk granularity preserves cross-turn findings AND
the original turn ids (we pass each chunk's ORIGINAL transcript index as
an offset and remap returned ``turn_ids`` back to original indices),
while still skipping unchanged chunks.

Incrementality: appending a turn invalidates only the LAST (now-changed)
chunk; an inserted/mutated middle turn invalidates that chunk and every
downstream chunk (their content hashes shift). A 20-turn session with
``max_turns_per_chunk=10`` that grew by one turn re-extracts exactly 1 of
its 2 chunks (chunk hit ratio 1/2). The project_root_hash prevents
cross-project cache replay if a user copies a vault between checkouts.
The cache stays content-keyed (not wall-clock-keyed) to preserve the
deterministic byte-identical compile guarantee.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

from .harness_sessions import HarnessSession, session_matches_project
from .llm_json import LLMJsonClient
from .research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchGraphBuilder,
    ResearchNode,
    ResearchNodeType,
    stable_id,
)
from .session_graph_llm import Finding, extract_with_llm
from .session_graph_path_index import DocPathIndex
from .session_graph_structural import extract_structural

logger = logging.getLogger(__name__)


CACHE_SCHEMA_VERSION = 3


# Map from Finding.kind (lowercase string) to ResearchNodeType.
_KIND_TO_NODE_TYPE: Dict[str, ResearchNodeType] = {
    "insight": ResearchNodeType.SESSION_INSIGHT,
    "decision": ResearchNodeType.SESSION_DECISION,
    "question": ResearchNodeType.SESSION_QUESTION,
    "todo": ResearchNodeType.SESSION_TODO,
    "hypothesis": ResearchNodeType.SESSION_HYPOTHESIS,
    "takeaway": ResearchNodeType.SESSION_TAKEAWAY,
}


@dataclass
class SessionGraphExtractor:
    """Drive both extraction passes for a project's sessions."""

    project_root: Path
    cache_dir: Path
    doc_graph: ResearchGraph
    sessions: List[HarnessSession]
    json_client: Optional[LLMJsonClient] = None
    llm_enabled: str = "auto"  # "auto" | "true" | "false"
    max_turns_per_chunk: int = 30
    include_doc_id_context: int = 200
    model: Optional[str] = None
    # Extraction-feedback guidance (session_findings slice) injected into the
    # LLM system prompt. Empty by default → byte-identical off-flag behavior.
    guidance: str = ""

    def extract(self) -> ResearchGraph:
        """Return the merged structural + LLM slice for the project."""
        # LLM-call accounting for the loud-failure surface (set before the
        # per-session pass increments them).
        self._llm_calls = 0
        self._llm_failed = 0
        in_project = [
            s for s in self.sessions
            if session_matches_project(s, self.project_root)
        ]
        if not in_project:
            return ResearchGraph()

        path_index = DocPathIndex.from_graph(self.doc_graph, self.project_root)
        structural = extract_structural(
            in_project, path_index, project_root=self.project_root
        )

        if not self._should_run_llm():
            return structural

        doc_id_context = self._build_doc_id_context()
        builder = ResearchGraphBuilder()

        # Start with the structural slice — every Session and structural
        # Decision node carries over.
        for node in structural.nodes:
            builder.add_node(
                name=node.name,
                node_type=node.type,
                aliases=node.aliases,
                description=node.description,
                source_path=node.source_path,
                metadata=node.metadata,
                # Reuse the same id by passing back the seed that produced it.
                # The cleanest way is to recover the seed from the id —
                # ResearchNode ids look like ``<Type>:<seed-slug>:<short-hash>``.
                # Simpler: use a no-op id_seed reconstruction via name +
                # rely on builder's id-dedup. But since we know the exact
                # node ids, we just re-emit via the builder's structures.
                id_seed=None,
            )
        # Actually, the builder's add_node would mint NEW ids. We need to
        # preserve the original ids. Use the slice's nodes directly via
        # the builder's internal dict.
        for node in structural.nodes:
            builder._nodes[node.id] = node  # type: ignore[attr-defined]
        for edge in structural.edges:
            key = (edge.source, edge.type, edge.target)
            builder._edges[key] = edge  # type: ignore[attr-defined]

        # Per-session LLM pass.
        for session in in_project:
            findings = self._llm_findings_for_session(
                session, doc_id_context
            )
            self._mint_findings(builder, session, findings, structural)

        # Prune cache files for sessions that no longer exist.
        self._prune_stale_caches({s.id for s in in_project})

        # Loud-failure surface: if the LLM backend was reachable-but-failing
        # (rate-limited / auth / wrong model), extraction silently produces no
        # findings. Make that unmissable instead of caching zeros. The failed
        # chunks were intentionally NOT cached above, so a recompile with a
        # working backend re-extracts them.
        if self._llm_failed:
            msg = (
                f"[tesserae] ⚠ session extraction: {self._llm_failed}/{self._llm_calls} "
                f"LLM call(s) FAILED (rate-limit / auth / unavailable backend). "
                f"Those chunks were NOT cached and yielded no findings. Run "
                f"`tesserae config status` to check your LLM backend, then recompile."
            )
            logger.warning(msg)
            print(msg, flush=True)

        return builder.build()

    # ------------------------------------------------------------------
    # LLM pass
    # ------------------------------------------------------------------

    def _should_run_llm(self) -> bool:
        if self.json_client is None:
            return False
        mode = (self.llm_enabled or "auto").lower()
        if mode == "false":
            return False
        # "true" or "auto" — both run when a client is present.
        return True

    def _build_doc_id_context(self) -> List[Tuple[str, str]]:
        """Top-N doc node ids passed to the LLM as legal reference targets."""
        from .research_graph import is_public_research_node

        ctx: List[Tuple[str, str]] = []
        for node in self.doc_graph.nodes:
            if node.type in {
                ResearchNodeType.SESSION,
                ResearchNodeType.SESSION_INSIGHT,
                ResearchNodeType.SESSION_DECISION,
                ResearchNodeType.SESSION_QUESTION,
                ResearchNodeType.SESSION_TODO,
                ResearchNodeType.SESSION_HYPOTHESIS,
                ResearchNodeType.SESSION_TAKEAWAY,
            }:
                continue
            if not is_public_research_node(node):
                continue
            ctx.append((node.id, node.name))
            if len(ctx) >= self.include_doc_id_context:
                break
        return ctx

    def _llm_findings_for_session(
        self,
        session: HarnessSession,
        doc_id_context: List[Tuple[str, str]],
    ) -> List[Finding]:
        """Per-CHUNK, cache-aware LLM extraction for one session.

        Partitions the session's normalised turns into stable,
        non-overlapping chunks of ``max_turns_per_chunk`` aligned to the
        ORIGINAL transcript indices (chunk k = turns[k*size:(k+1)*size]).
        A chunk whose content hash matches its cached envelope loads from
        disk (hit); a chunk that changed is re-extracted (miss). Each
        chunk is extracted with its turns carrying their ORIGINAL indices
        — ``extract_with_llm`` renders turn_ids from 0 within the chunk it
        receives, so we remap the returned ``turn_ids`` back to original
        transcript indices (chunk-local + offset). This preserves
        cross-turn findings (they span turns within a chunk) AND the real
        turn ids, so the concatenation over chunks is byte-identical to a
        whole-session extraction over the same non-overlapping chunking.

        Only changed chunks hit the LLM: appending one turn re-extracts
        just the last chunk (hit ratio (chunks-1)/chunks); an inserted or
        mutated middle turn re-extracts that chunk and all downstream
        chunks (their content hashes shift).
        """
        turns = _normalised_turns(session)
        if not turns:
            return []
        project_root_hash = _project_root_hash(self.project_root)
        size = max(1, int(self.max_turns_per_chunk))

        all_findings: List[Finding] = []
        for chunk_index, start in enumerate(range(0, len(turns), size)):
            chunk = turns[start : start + size]
            cpath = _chunk_cache_path(self.cache_dir, session.id, chunk_index)
            chash = _chunk_content_hash(chunk)

            # Cache hit?
            if cpath.exists():
                cached = _read_cache(cpath)
                if (
                    cached
                    and cached.get("schema_version") == CACHE_SCHEMA_VERSION
                    and cached.get("chunk_hash") == chash
                    and cached.get("project_root_hash") == project_root_hash
                ):
                    all_findings.extend(
                        _finding_from_dict(d) for d in cached.get("findings") or []
                    )
                    continue

            # Cache miss → extract this chunk. Pass max_turns_per_chunk >=
            # len(chunk) so extract_with_llm treats the chunk as a single
            # window (no internal re-chunking) — the chunking decision is
            # ours so it stays aligned to original indices.
            chunk_stats: dict = {}
            raw_findings = extract_with_llm(
                session,
                chunk,
                doc_id_context,
                self.json_client,
                max_turns_per_chunk=max(size, len(chunk)),
                overlap=0,
                cache_key=f"sessions-v{CACHE_SCHEMA_VERSION}",
                guidance=self.guidance,
                stats=chunk_stats,
            )
            self._llm_calls += chunk_stats.get("calls", 0)
            # A FAILED LLM call (no answer: rate-limit / auth / dead backend)
            # must NOT be cached as an empty result — otherwise the outage is
            # baked in and every later compile reuses zero findings. Skip the
            # cache write so this chunk is re-extracted once the backend works.
            if chunk_stats.get("failed", 0):
                self._llm_failed += chunk_stats["failed"]
                continue
            # Remap chunk-local turn_ids (0-based within the chunk) back to
            # ORIGINAL transcript indices. extract_with_llm enumerates the
            # passed chunk from 0, so finding turn_id j → original start+j.
            findings = [_offset_turn_ids(f, start) for f in raw_findings]
            _write_cache(
                cpath,
                {
                    "schema_version": CACHE_SCHEMA_VERSION,
                    "chunk_hash": chash,
                    "project_root_hash": project_root_hash,
                    "chunk_index": chunk_index,
                    "turn_start": start,
                    "session_id": session.id,
                    "findings": [_finding_to_dict(f) for f in findings],
                },
            )
            all_findings.extend(findings)

        return all_findings

    def _mint_findings(
        self,
        builder: ResearchGraphBuilder,
        session: HarnessSession,
        findings: List[Finding],
        structural: ResearchGraph,
    ) -> None:
        """Convert Finding records into ResearchGraph nodes + edges."""
        if not findings:
            return

        # Live doc-graph nodes by id. Finding references are filtered against
        # the doc graph at EXTRACTION time (session_graph_llm: known_doc_ids),
        # but cached findings replay their stored references on a LATER compile
        # — and a referenced node whose id has since changed (e.g. a
        # CodeFunction whose content-hash id shifted when the code changed, or
        # was removed) leaves the cached reference STALE. Emitting an edge for a
        # stale id fabricates a wrong-typed pseudo node that dangles into the
        # graph and KeyErrors downstream. Re-validate here: link to the real
        # node when it still exists, drop the reference otherwise.
        live_nodes_by_id = {n.id: n for n in self.doc_graph.nodes}

        # Find the structural Session node id so we can edge findings to it.
        session_id_str = session.id
        session_node = next(
            (
                n for n in structural.nodes
                if n.type == ResearchNodeType.SESSION
                and n.metadata.get("session_id") == session_id_str
            ),
            None,
        )
        if session_node is None:
            return

        for f in findings:
            node_type = _KIND_TO_NODE_TYPE.get(f.kind)
            if node_type is None:
                continue
            finding_id_seed = (
                f"session:{session_id_str}:{f.kind}:{_short_hash(f.body)}"
            )
            # Deterministic decay anchor ONLY. ``first_seen_at`` is derived
            # from the session's own ``started_at`` (a property of the source
            # corpus), so it is byte-stable across compiles and safe to
            # serialize into graph.json. Mutable memory state
            # (``access_count`` / ``last_accessed_at``) is DELIBERATELY absent
            # here: those columns live exclusively in the ``node_memory``
            # sidecar (see tesserae.memory.store / project._run_memory_passes).
            # Stamping them onto node.metadata would leak wall-clock sidecar
            # state into graph.json — ``ResearchNode.model_dump`` serializes
            # the whole metadata dict — and break byte-idempotence on the
            # session compile path (the Phase-5 BLOCKER). We NEVER fall back to
            # ``datetime.now()``: a session with no ``started_at`` simply omits
            # ``first_seen_at`` and decay treats it as freshly minted (1.0).
            session_started_at = (session_node.metadata or {}).get("started_at")
            finding_metadata: Dict[str, object] = {
                "session_id": session_id_str,
                "extractor": "session-llm",
                "turn_ids": list(f.turn_ids),
                "content_hash": _short_hash(f.body),
            }
            if session_started_at:
                finding_metadata["first_seen_at"] = str(session_started_at)
            if self.model:
                finding_metadata["llm_model"] = self.model
            # Extraction QUALITY signals (Jonasb8/memex ideas; AGPL, no code
            # copied). These come from the content-keyed cached LLM output, so
            # like body/turn_ids they are byte-stable across compiles of
            # unchanged sources — safe in graph.json, unlike wall-clock decay
            # state. They flag, never guarantee, finding quality.
            if f.confidence is not None:
                finding_metadata["confidence"] = f.confidence
            if f.confidence_rationale:
                finding_metadata["confidence_rationale"] = f.confidence_rationale
            if f.revisit_signals:
                finding_metadata["revisit_signals"] = list(f.revisit_signals)
            finding_node = builder.add_node(
                name=f.body,
                node_type=node_type,
                id_seed=finding_id_seed,
                metadata=finding_metadata,
            )
            # derived_from_session edge
            builder.add_edge(finding_node, "derived_from_session", session_node)
            # references edges — only to ids that still exist in the live doc
            # graph (drops stale cached refs; see live_nodes_by_id note above).
            for ref_id in f.references:
                target = live_nodes_by_id.get(ref_id)
                if target is None:
                    continue
                builder.add_edge(finding_node, "references", target)

    # ------------------------------------------------------------------
    # Cache pruning
    # ------------------------------------------------------------------

    def _prune_stale_caches(self, live_ids: Set[str]) -> None:
        """Remove per-session cache dirs for ids no longer in the live set.

        The v3 layout stores each session's per-chunk findings under
        ``cache_dir/<safe_id>/chunk-<K>.json``, so we garbage-collect by
        directory keyed on ``_safe(id)``. Defensive against OSError so a
        permission hiccup never aborts a compile.
        """
        if not self.cache_dir.exists():
            return
        live_safe = {_safe(sid) for sid in live_ids}
        for child in self.cache_dir.iterdir():
            if not child.is_dir():
                continue
            if child.name in live_safe:
                continue
            try:
                for f in child.iterdir():
                    try:
                        f.unlink()
                    except OSError:
                        pass
                child.rmdir()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Helpers (module-level so they're testable in isolation)
# ---------------------------------------------------------------------------


def _session_content_hash(session: HarnessSession) -> str:
    """Stable hash over the session's normalised payload."""
    payload = json.dumps(session.to_dict(), sort_keys=True, ensure_ascii=False)
    return "sha256-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _chunk_content_hash(chunk: List[dict]) -> str:
    """Stable, content-keyed hash over a chunk of normalised turns.

    Hashing the canonical JSON (sorted keys) keeps the key deterministic
    and format-agnostic — it depends only on the chunk's turns' role +
    text, not on any Codex turn_id — so the byte-identical compile
    guarantee holds across reimports. Including all turns in the chunk
    means a mutation to ANY turn in the chunk shifts the hash and forces
    a re-extract, which is exactly the invalidation semantics we want
    (cross-turn findings can change when any constituent turn changes).
    """
    payload = json.dumps(chunk, sort_keys=True, ensure_ascii=False)
    return "sha256-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _chunk_cache_path(cache_dir: Path, session_id: str, chunk_index: int) -> Path:
    """Path to one chunk's cache file: ``<cache_dir>/<safe_id>/chunk-<K>.json``."""
    return cache_dir / _safe(session_id) / f"chunk-{chunk_index}.json"


def _offset_turn_ids(finding: Finding, offset: int) -> Finding:
    """Remap a finding's chunk-local turn_ids to original transcript indices.

    ``extract_with_llm`` enumerates the chunk it receives from 0, so a
    finding referencing chunk-local turn ``j`` actually refers to original
    transcript turn ``offset + j``. Negative or non-int ids are passed
    through unchanged (defensive — validation already coerced to int).
    """
    return Finding(
        kind=finding.kind,
        body=finding.body,
        turn_ids=[offset + t for t in finding.turn_ids],
        references=list(finding.references),
        confidence=finding.confidence,
        confidence_rationale=finding.confidence_rationale,
        revisit_signals=list(finding.revisit_signals),
    )


def _project_root_hash(project_root: Path | str) -> str:
    """Hash of the project_root path so caches don't replay across projects."""
    return "sha256-" + hashlib.sha256(
        str(Path(project_root).resolve()).encode("utf-8")
    ).hexdigest()


def _short_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def _safe(s: str) -> str:
    """Filesystem-safe basename for cache filenames."""
    out = "".join(c if (c.isalnum() or c in "-_.") else "-" for c in (s or ""))
    return out[:120]


def _read_cache(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_cache(path: Path, payload: dict) -> None:
    """Atomic write via tmp + rename, matching the project-wide pattern.

    The tmp name carries pid + a short random suffix so two concurrent
    compiles (e.g. the SessionEnd hook running a background compile
    while the user manually runs /tesserae:refresh) don't collide on
    the same `.tmp` file, race on `rename`, and crash one of them with
    FileNotFoundError. Worst case both writers finish: last rename wins,
    and the payload is identical anyway because the cache key is a
    content hash.
    """
    import os as _os
    import secrets as _secrets

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(
        path.suffix + f".tmp.{_os.getpid()}.{_secrets.token_hex(4)}"
    )
    try:
        tmp.write_text(
            json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        tmp.rename(path)
    finally:
        # If rename failed for any reason, clean up the tmp file so the
        # cache dir doesn't accumulate stale .tmp.NNNN.XXXX detritus.
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _finding_from_dict(d: dict) -> Finding:
    conf = d.get("confidence")
    return Finding(
        kind=str(d.get("kind") or ""),
        body=str(d.get("body") or ""),
        turn_ids=list(d.get("turn_ids") or []),
        references=list(d.get("references") or []),
        confidence=None if conf is None else float(conf),
        confidence_rationale=str(d.get("confidence_rationale") or ""),
        revisit_signals=list(d.get("revisit_signals") or []),
    )


def _finding_to_dict(f: Finding) -> dict:
    d: dict = {"kind": f.kind, "body": f.body, "turn_ids": f.turn_ids, "references": f.references}
    # Only persist quality signals when present — keeps caches that predate the
    # feature byte-identical to new ones for findings that have no signals.
    if f.confidence is not None:
        d["confidence"] = f.confidence
    if f.confidence_rationale:
        d["confidence_rationale"] = f.confidence_rationale
    if f.revisit_signals:
        d["revisit_signals"] = list(f.revisit_signals)
    return d


def _normalised_turns(session: HarnessSession) -> List[dict]:
    """Extract a list of {role, text} turns from the session metadata.

    Per the spec, v1 uses ``session.metadata["turns"]`` ONLY — we never
    read the raw transcript from disk. Falls back to an empty list if
    the harness import didn't populate normalized turns.
    """
    raw = session.metadata.get("turns") if session.metadata else None
    if not isinstance(raw, list):
        return []
    out: List[dict] = []
    for turn in raw:
        if not isinstance(turn, dict):
            continue
        role = str(turn.get("role") or "").lower()
        text = str(turn.get("text") or "").strip()
        if not text:
            continue
        out.append({"role": role, "text": text})
    return out
