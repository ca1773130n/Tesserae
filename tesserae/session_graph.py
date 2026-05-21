"""SessionGraphExtractor — orchestrator for the two-pass session extraction.

Combines the deterministic structural pass
(:mod:`tesserae.session_graph_structural`) with the LLM-backed
finding extraction (:mod:`tesserae.session_graph_llm`) into a single
:class:`ResearchGraph` slice that
:func:`tesserae.project.merge_graphs` can fold into the doc graph.

Caching: every session's LLM-extracted findings are persisted to
``.tesserae/session_findings/<session_id>.findings.json`` with a
content_hash AND a project_root_hash envelope. On the next compile we
skip the LLM call when both hashes match. The project_root_hash
prevents cross-project cache replay if a user copies a vault between
checkouts.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
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


CACHE_SCHEMA_VERSION = 1


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

    def extract(self) -> ResearchGraph:
        """Return the merged structural + LLM slice for the project."""
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
        """Cache-aware LLM extraction for one session."""
        content_hash = _session_content_hash(session)
        project_root_hash = _project_root_hash(self.project_root)
        cache_path = self.cache_dir / f"{_safe(session.id)}.findings.json"

        # Cache hit?
        if cache_path.exists():
            cached = _read_cache(cache_path)
            if (
                cached
                and cached.get("schema_version") == CACHE_SCHEMA_VERSION
                and cached.get("content_hash") == content_hash
                and cached.get("project_root_hash") == project_root_hash
            ):
                return [_finding_from_dict(d) for d in cached.get("findings") or []]

        # Cache miss → extract.
        turns = _normalised_turns(session)
        if not turns:
            return []
        findings = extract_with_llm(
            session,
            turns,
            doc_id_context,
            self.json_client,
            max_turns_per_chunk=self.max_turns_per_chunk,
            cache_key=f"sessions-v{CACHE_SCHEMA_VERSION}",
        )
        _write_cache(
            cache_path,
            {
                "schema_version": CACHE_SCHEMA_VERSION,
                "content_hash": content_hash,
                "project_root_hash": project_root_hash,
                "session_id": session.id,
                "findings": [
                    {
                        "kind": f.kind,
                        "body": f.body,
                        "turn_ids": f.turn_ids,
                        "references": f.references,
                    }
                    for f in findings
                ],
            },
        )
        return findings

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
            # Memory metadata (A-MEM / MemoryBank style) — drives
            # tesserae.memory.decay.compute_decay_score. Initial values
            # treat the finding as newly minted; future surfaces will
            # bump access_count on read.
            now_iso = datetime.now(timezone.utc).isoformat()
            session_started_at = str(
                (session_node.metadata or {}).get("started_at") or now_iso
            )
            finding_metadata: Dict[str, object] = {
                "session_id": session_id_str,
                "extractor": "session-llm",
                "turn_ids": list(f.turn_ids),
                "content_hash": _short_hash(f.body),
                # Anchor the decay clock at the session's start_time so
                # importing a year-old session backdates its findings
                # correctly. Falls back to "now" when start_time is
                # missing.
                "first_seen_at": session_started_at,
                "last_accessed_at": session_started_at,
                "access_count": 0,
            }
            if self.model:
                finding_metadata["llm_model"] = self.model
            finding_node = builder.add_node(
                name=f.body,
                node_type=node_type,
                id_seed=finding_id_seed,
                metadata=finding_metadata,
            )
            # derived_from_session edge
            builder.add_edge(finding_node, "derived_from_session", session_node)
            # references edges
            for ref_id in f.references:
                pseudo = ResearchNode(
                    id=ref_id,
                    name="",
                    type=ResearchNodeType.SOURCE_DOCUMENT,
                )
                builder.add_edge(finding_node, "references", pseudo)

    # ------------------------------------------------------------------
    # Cache pruning
    # ------------------------------------------------------------------

    def _prune_stale_caches(self, live_ids: Set[str]) -> None:
        if not self.cache_dir.exists():
            return
        for path in self.cache_dir.glob("*.findings.json"):
            sid = path.stem.rsplit(".", 1)[0]  # strip ".findings"
            if sid not in live_ids:
                try:
                    path.unlink()
                except OSError:
                    pass


# ---------------------------------------------------------------------------
# Helpers (module-level so they're testable in isolation)
# ---------------------------------------------------------------------------


def _session_content_hash(session: HarnessSession) -> str:
    """Stable hash over the session's normalised payload."""
    payload = json.dumps(session.to_dict(), sort_keys=True, ensure_ascii=False)
    return "sha256-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


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
    return Finding(
        kind=str(d.get("kind") or ""),
        body=str(d.get("body") or ""),
        turn_ids=list(d.get("turn_ids") or []),
        references=list(d.get("references") or []),
    )


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
