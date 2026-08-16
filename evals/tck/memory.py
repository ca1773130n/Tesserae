"""The Tesserae-backed core the TCK adapter drives, with no TCK import.

Split out from :mod:`evals.tck.adapter` so the behaviour that matters — what
Tesserae can and cannot do when asked to act as an agent-memory service — is
exercisable on a checkout that has never cloned the kit (see
``tests/test_tck_adapter.py``). :mod:`evals.tck.adapter` is a thin translation
layer from these results into ``tck.adapters.base_adapter`` Pydantic models.

**Which Tesserae the adapter is allowed to use.** Two constraints define it:

* **No compile.** ``graph.json`` is produced by ``ProjectWiki.compile``; every
  MCP read loads it off disk and nothing merges a pending write at read time
  (``mcp_server._load_graph_cached``). A memory service that needs a compile
  between a write and the read that sees it is not a memory service, so the
  adapter is restricted to the two Tesserae write paths that are durable
  without one.
* **No LLM.** Neither path calls a model.

That leaves exactly two substrates:

``SessionChunksDB`` (``tesserae/session_chunks.py``)
    A live SQLite store of normalised transcript turns, bucketed by KST day.
    This is where short-term memory goes. It is the *only* place in Tesserae
    where an individual utterance is written and read back without a compile.

``record_agent_write`` (``tesserae/agent_write.py``)
    The append-only typed overlay. A validated write reaches
    ``.tesserae/agent-writes.jsonl`` in about a millisecond and is replayed into
    the graph by the next compile. Long-term entity writes go here. They are
    durable immediately and **unreadable until a compile**, which is why every
    read-back of one is a refusal below rather than a lookup.

**What this module refuses, and why that is the point.** Where Tesserae has no
implementation of a contract operation, the method raises :class:`Unsupported`
naming the guarantee or the absent vocabulary that blocks it. It does not fall
back to a private side-store. An adapter that keeps its own dict passes the
whole kit in a fifth of a second while measuring nothing about the system it
claims to represent; the refusals are the measurement.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, NoReturn, Optional, Tuple

from tesserae.agent_write import record_agent_write
from tesserae.session_chunks import SessionChunksDB, chunks_db_path, day_label

#: Harness label for turns this adapter writes. Deliberately not one of
#: ``session_chunks.ALL_HARNESSES`` ("claude-code", "codex") — TCK traffic is
#: not a captured agent session and must never be read back as one by the
#: activity summary. The consequence is that every read here has to pass
#: ``harnesses=`` explicitly; the store's default read filter returns nothing.
TCK_HARNESS = "tck"

#: Namespace for the UUIDv5 translation between Tesserae's string ids and the
#: ``UUID``-typed ids the TCK models require. Fixed so the mapping is
#: reproducible across runs and across processes.
TCK_UUID_NAMESPACE = uuid.UUID("6f9619ff-8b86-d011-b42d-00c04fc964ff")

#: TCK entity type → Tesserae ``ResearchNodeType`` value, for the two types
#: where a genuine correspondence exists. The kit's other three required types
#: are handled by :data:`ENTITY_TYPE_REFUSALS`; nothing is coerced into a
#: neighbouring type to make a scenario pass.
ENTITY_TYPE_MAP: Dict[str, str] = {
    "PERSON": "Person",
    "ORGANIZATION": "Organization",
}

#: TCK entity type → why Tesserae cannot store it. LOCATION and OBJECT are
#: absent from a 76-member vocabulary that is research-shaped rather than
#: world-shaped; EVENT exists but ``agent_write.DENIED_NODE_TYPES`` reserves it
#: for the session-graph producer, because two owners of one id space give a
#: node two ``__`` provenance sources.
ENTITY_TYPE_REFUSALS: Dict[str, str] = {
    "LOCATION": (
        "ResearchNodeType has no LOCATION member. The vocabulary is "
        "research-domain shaped (Paper, Dataset, Benchmark, Metric); places are "
        "not entities Tesserae models."
    ),
    "OBJECT": (
        "ResearchNodeType has no OBJECT member, for the same reason as LOCATION."
    ),
    "EVENT": (
        "ResearchNodeType.EVENT exists but is in agent_write.DENIED_NODE_TYPES: "
        "its id space is owned by the session-graph producer, which re-derives "
        "Event nodes from transcripts every compile."
    ),
}


#: Why both preference operations refuse. Shared so the write and the read
#: cannot drift into telling the reader two different stories.
_NO_PREFERENCE = (
    "no Preference node type exists in ResearchNodeType's 76 members and no "
    "`has_preference` edge exists in the edge vocabulary; Tesserae models what "
    "a project knows, not what a user likes"
)


class Unsupported(NotImplementedError):
    """A TCK contract operation Tesserae has no implementation for.

    Subclasses ``NotImplementedError`` so the kit's Gold and Platinum tiers,
    which catch that exception and skip, behave as their authors intended.
    Bronze and Silver do not catch it, so a refusal there is a **failure** —
    which is the honest outcome and the reason this exception exists instead of
    a fallback implementation.

    ``blocked_by`` names the Tesserae guarantee or absence responsible, so a
    failing scenario can be reported as a property of the system rather than as
    an unexplained red line.
    """

    def __init__(self, operation: str, blocked_by: str) -> None:
        super().__init__(f"{operation}: {blocked_by}")
        self.operation = operation
        self.blocked_by = blocked_by


@dataclass(frozen=True)
class StoredMessage:
    """One turn as ``SessionChunksDB`` holds it, plus the metadata it drops."""

    id: uuid.UUID
    session_id: str
    role: str
    content: str
    timestamp: datetime
    #: What the caller passed. **Not persisted** — see
    #: :meth:`TesseraeMemory.add_message`. Present on the write result and
    #: absent from every read.
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StoredEntity:
    """An entity recorded into the agent-write overlay."""

    id: uuid.UUID
    name: str
    tck_type: str
    tesserae_type: str
    tesserae_id: str
    description: Optional[str]
    created_at: datetime
    #: ``record_agent_write``'s own status: "recorded" or "duplicate".
    write_status: str
    write_id: str


def _uuid_for(*parts: str) -> uuid.UUID:
    return uuid.uuid5(TCK_UUID_NAMESPACE, "\x00".join(parts))


class TesseraeMemory:
    """Agent-memory operations over the two compile-free Tesserae substrates.

    One instance owns one scratch project root. Nothing is written outside it,
    and it must never point at a real project's ``.tesserae/``.
    """

    def __init__(self, project_root: str | Path) -> None:
        self.project_root = Path(project_root)
        self.tesserae_dir = self.project_root / ".tesserae"
        self.tesserae_dir.mkdir(parents=True, exist_ok=True)
        self._db = SessionChunksDB(chunks_db_path(self.project_root))
        self._last_ts: Optional[datetime] = None

    # ------------------------------------------------------------------ #
    # Harness lifecycle                                                    #
    # ------------------------------------------------------------------ #

    @property
    def writes_path(self) -> Path:
        return self.tesserae_dir / "agent-writes.jsonl"

    def reset(self) -> None:
        """Discard the substrate and rebuild it empty.

        This is harness lifecycle, not a contract operation: the TCK calls
        ``clear_all_data`` before each of its 189 scenarios purely for
        isolation, and asserts nothing about it. Deleting the SQLite file and
        the overlay journal is a filesystem operation, **not** evidence that
        Tesserae can delete anything — the operations the kit does assert on
        (``delete_message``, ``clear_session``) are refusals below, because the
        stores are append-only.
        """
        for path in (
            chunks_db_path(self.project_root),
            self.writes_path,
        ):
            path.unlink(missing_ok=True)
        for suffix in ("-wal", "-shm"):
            Path(str(chunks_db_path(self.project_root)) + suffix).unlink(missing_ok=True)
        self._db = SessionChunksDB(chunks_db_path(self.project_root))
        self._last_ts = None

    # ------------------------------------------------------------------ #
    # Short-term memory — SessionChunksDB                                  #
    # ------------------------------------------------------------------ #

    def _next_timestamp(self) -> datetime:
        """A strictly increasing write timestamp.

        The store's uniqueness key is ``(session_path, ts, role, text_hash)``,
        so two identical messages written inside the same microsecond collapse
        into one — ``record_turns`` returns 0 and raises nothing. Assigning
        strictly increasing timestamps is the adapter's job (a memory service
        stamps its own writes) and makes ordering total; it does not paper over
        the collapse, which is recorded in the README and still reachable by
        writing two turns at a caller-supplied identical timestamp.
        """
        now = datetime.now(timezone.utc)
        if self._last_ts is not None and now <= self._last_ts:
            now = self._last_ts + timedelta(microseconds=1)
        self._last_ts = now
        return now

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        *,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> StoredMessage:
        """Append one turn.

        ``metadata`` is returned on the result and **not stored**: the ``turns``
        table's ``meta`` column is written by ``record_turns`` as
        ``{"name": ...}`` or ``{}`` and carries nothing else, so a metadata
        round-trip through this store is impossible. Returning it on the write
        while every read lacks it is the accurate report of that.
        """
        ts = self._next_timestamp()
        raw_ts = ts.isoformat()
        inserted = self._db.record_turns(
            TCK_HARNESS,
            session_id,
            session_id,
            [{"timestamp": raw_ts, "role": role, "text": content}],
        )
        if inserted != 1:
            # record_turns swallows an unparseable timestamp and a duplicate
            # identity key alike, returning 0 without raising. A memory service
            # that silently stores nothing is worse than one that refuses, so
            # the adapter converts the silence into a loud failure.
            raise RuntimeError(
                f"SessionChunksDB.record_turns stored {inserted} of 1 turn for "
                f"session {session_id!r} at {raw_ts} — the row collided with the "
                "(session_path, ts, role, text_hash) identity index or the "
                "timestamp did not parse"
            )
        return StoredMessage(
            id=self._message_id(session_id, raw_ts, role, content),
            session_id=session_id,
            role=role,
            content=content,
            timestamp=ts,
            metadata=dict(metadata or {}),
        )

    @staticmethod
    def _message_id(session_id: str, raw_ts: str, role: str, content: str) -> uuid.UUID:
        """A UUID derived from the store's own uniqueness key.

        The ``turns`` table has no id column, so there is no identity to read
        back — this reconstructs the store's key, which is content-addressed in
        the same way ``research_graph.stable_id`` is. Two messages get two ids
        because their timestamps differ, not because either was given one.
        """
        digest = hashlib.sha1(content.encode("utf-8", "replace")).hexdigest()
        return _uuid_for("message", session_id, raw_ts, role, digest)

    def _read_window_days(self) -> List[str]:
        """The KST day labels a read has to sweep.

        ``turns_for_day`` is the store's only public read and it takes one day.
        Nothing exposes "which days does this session span", so a caller must
        already know — the gap that makes per-session retrieval awkward. A run
        lasting seconds touches today and, across a KST midnight, its
        neighbours; sweeping all three uses only the public API.
        """
        now = datetime.now(timezone.utc)
        return [
            day_label(now - timedelta(days=1)),
            day_label(now),
            day_label(now + timedelta(days=1)),
        ]

    def _all_turns(self) -> List[dict]:
        rows: List[dict] = []
        for day in self._read_window_days():
            rows.extend(self._db.turns_for_day(day, harnesses=(TCK_HARNESS,)))
        return rows

    def messages(self, session_id: str) -> List[StoredMessage]:
        """Every stored turn for one session, in insertion order."""
        rows = [r for r in self._all_turns() if r.get("session_id") == session_id]
        rows.sort(key=lambda r: str(r.get("ts") or ""))
        return [
            StoredMessage(
                id=self._message_id(
                    session_id, str(r["ts"]), str(r["role"]), str(r["text"])
                ),
                session_id=session_id,
                role=str(r["role"]),
                content=str(r["text"]),
                timestamp=self._parse_ts(str(r["ts"])),
                metadata={},
            )
            for r in rows
        ]

    @staticmethod
    def _parse_ts(raw: str) -> datetime:
        parsed = datetime.fromisoformat(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed

    def conversation_id(self, session_id: str) -> uuid.UUID:
        """Stable per-session id.

        Tesserae mints no Conversation node — ``ResearchNodeType`` has no
        MESSAGE or CONVERSATION member, and ``SESSION`` is denied to agent
        writes — so this is derived from the session key rather than read.
        """
        return _uuid_for("conversation", session_id)

    def sessions(self) -> List[Tuple[str, int, datetime, datetime]]:
        """``(session_id, message_count, created_at, updated_at)`` per session."""
        grouped: Dict[str, List[str]] = {}
        for row in self._all_turns():
            grouped.setdefault(str(row.get("session_id") or ""), []).append(
                str(row.get("ts") or "")
            )
        out: List[Tuple[str, int, datetime, datetime]] = []
        for session_id, stamps in grouped.items():
            stamps.sort()
            out.append(
                (
                    session_id,
                    len(stamps),
                    self._parse_ts(stamps[0]),
                    self._parse_ts(stamps[-1]),
                )
            )
        out.sort(key=lambda item: item[2])
        return out

    def search_messages(self, query: str) -> NoReturn:
        """Refused: Tesserae has no retrieval over uncompiled turns.

        Tesserae's retrieval is real — BM25 (``tesserae/bm25_index.py``),
        embeddings, personalised PageRank — and every bit of it indexes
        ``graph.json``, which a compile produces. The ``turns`` table has no
        index beyond ``day`` and its uniqueness key.

        A substring scan over the rows would pass all seven Bronze search
        scenarios: they pass ``threshold=0.0`` and assert little more than
        ``len(results) > 0``. It would also be an implementation this adapter
        invented, telling the reader nothing about Tesserae. The refusal is the
        accurate answer.
        """
        raise Unsupported(
            "search_messages",
            "Tesserae's retrieval (BM25, embeddings, PPR) indexes the compiled "
            "graph.json; the live turns table has no index, and inventing a "
            "substring scan here would measure the adapter rather than Tesserae",
        )

    def delete_message(self, message_id: uuid.UUID) -> NoReturn:
        """Refused: the turn store is append-only and rows have no id.

        ``SessionChunksDB``'s public surface is ``record_turns``,
        ``turns_for_day``, ``mark_coverage``, ``covered_days``,
        ``coverage_rows``. There is no delete and no update, and the ``turns``
        table has no id column for one to address.
        """
        raise Unsupported(
            "delete_message",
            "SessionChunksDB exposes no delete or update, and its turns table "
            "has no id column; the agent-write overlay is append-only by design "
            "(retraction is a `retracts` edge, which adds a row rather than "
            "removing one)",
        )

    def clear_session(self, session_id: str) -> NoReturn:
        """Refused: same append-only store, no per-session delete."""
        raise Unsupported(
            "clear_session",
            "SessionChunksDB exposes no delete; turns are bucketed by KST day "
            "rather than owned by a session, so there is no per-session extent "
            "to remove even if one existed",
        )

    # ------------------------------------------------------------------ #
    # Long-term memory — the agent-write overlay                           #
    # ------------------------------------------------------------------ #

    def add_entity(
        self,
        name: str,
        entity_type: str,
        *,
        description: Optional[str] = None,
        agent_key: str = "tck:local:worker",
        session_id: str = "tck",
    ) -> StoredEntity:
        """Record one entity into the append-only overlay.

        Durable immediately; invisible to every read until the next compile.
        The type must be one ``ENTITY_TYPE_MAP`` covers — the other three the
        kit requires are refused with the reason, rather than filed under a
        neighbouring type that would make the assertion pass and the graph
        wrong.
        """
        wanted = str(entity_type or "").strip().upper()
        refusal = ENTITY_TYPE_REFUSALS.get(wanted)
        if refusal is not None:
            raise Unsupported(f"add_entity(type={entity_type!r})", refusal)
        tesserae_type = ENTITY_TYPE_MAP.get(wanted)
        if tesserae_type is None:
            raise Unsupported(
                f"add_entity(type={entity_type!r})",
                "no ResearchNodeType corresponds to this TCK entity type; "
                f"mapped types are {sorted(ENTITY_TYPE_MAP)}",
            )
        response = record_agent_write(
            self.writes_path,
            {
                "nodes": [
                    {
                        "name": name,
                        "type": tesserae_type,
                        "description": description or "",
                    }
                ],
                "edges": [],
                # `agent` plus one external anchor is mandatory: a claim whose
                # only support is the graph itself cannot be verified against
                # anything outside it. A TCK write always has a session_id.
                "provenance": {"agent": agent_key, "session_id": session_id},
            },
            agent_key,
        )
        node = (response.get("nodes") or [{}])[0]
        tesserae_id = str(node.get("id") or "")
        return StoredEntity(
            id=_uuid_for("entity", tesserae_id),
            name=name,
            tck_type=wanted,
            tesserae_type=tesserae_type,
            tesserae_id=tesserae_id,
            description=description,
            created_at=datetime.now(timezone.utc),
            write_status=str(response.get("status") or ""),
            write_id=str(response.get("write_id") or ""),
        )

    def add_preference(self, category: str, preference: str) -> NoReturn:
        """Refused: Tesserae has no preference concept at any layer."""
        raise Unsupported("add_preference", _NO_PREFERENCE)

    def search_preferences(self, query: str) -> NoReturn:
        """Refused: nothing to search — see :meth:`add_preference`."""
        raise Unsupported("search_preferences", _NO_PREFERENCE)

    def add_fact(self, subject: str, predicate: str, obj: str) -> NoReturn:
        """Refused: the edge vocabulary is closed, so a free predicate has no home."""
        raise Unsupported(
            "add_fact",
            "ALLOWED_EDGE_TYPES is a closed vocabulary (uses_dataset, "
            "evaluated_on, achieves_score, ...) and agent_write refuses an edge "
            "type outside it rather than coercing; an arbitrary predicate like "
            "WORKS_AT cannot be asserted, and flattening the triple into a Claim "
            "node's text would lose the subject/predicate/object structure the "
            "kit reads back",
        )

    # ------------------------------------------------------------------ #
    # Reasoning memory                                                     #
    # ------------------------------------------------------------------ #

    def reasoning(self, operation: str) -> NoReturn:
        """Refused: the single reason no reasoning-memory operation maps."""
        raise Unsupported(
            operation,
            "Tesserae's reasoning trace is the Event node "
            "(turn_id/actor/action/state-change, ordered by `precedes`), which "
            "is minted only by the session-graph producer during a compile and "
            "is in agent_write.DENIED_NODE_TYPES; there is no Trace, Step or "
            "ToolCall type an agent may write",
        )

    # ------------------------------------------------------------------ #
    # Read-back of long-term memory                                        #
    # ------------------------------------------------------------------ #

    def long_term_read(self, operation: str) -> NoReturn:
        """Refused: the single reason no long-term read maps."""
        raise Unsupported(
            operation,
            "an agent write is durable in .tesserae/agent-writes.jsonl and "
            "enters the graph only when ProjectWiki.compile replays it; every "
            "read path loads graph.json off disk and nothing merges the overlay "
            "at read time, so there is no read-after-write without a compile",
        )


__all__ = [
    "ENTITY_TYPE_MAP",
    "ENTITY_TYPE_REFUSALS",
    "StoredEntity",
    "StoredMessage",
    "TCK_HARNESS",
    "TCK_UUID_NAMESPACE",
    "TesseraeMemory",
    "Unsupported",
]
