"""``TesseraeAdapter`` — Tesserae behind the Neo4j agent-memory TCK interface.

A translation layer and nothing more: every operation either delegates to
:class:`evals.tck.memory.TesseraeMemory`, which uses only Tesserae's two
compile-free write paths, or raises the refusal that class already carries. No
state lives here, and there is deliberately no private fallback store — see the
:mod:`evals.tck.memory` docstring for why one would invalidate the result.

``BaseAdapter`` declares 23 abstract methods and Python will not instantiate a
subclass missing any of them, so the Silver-tier methods are defined here even
though a Bronze-only run is what this adapter targets. Each is a one-line
delegation to the refusal that explains it.

The class is built inside :func:`build_adapter_class` rather than at module
scope so importing this module does not require the TCK, which is not on PyPI
and absent from a fresh checkout.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .memory import TesseraeMemory
from .vendor_tck import require_tck

_ADAPTER_CLASS: Optional[type] = None


def build_adapter_class() -> type:
    """Return ``TesseraeAdapter``, importing the TCK on first call."""
    global _ADAPTER_CLASS
    if _ADAPTER_CLASS is not None:
        return _ADAPTER_CLASS

    require_tck()

    from tck.adapters.base_adapter import (  # noqa: PLC0415 — see docstring
        BaseAdapter,
        TCKConversation,
        TCKEntity,
        TCKFact,
        TCKMessage,
        TCKPreference,
        TCKReasoningStep,
        TCKReasoningTrace,
        TCKSessionInfo,
        TCKToolCall,
        TCKToolStats,
        ToolCallStatus,
    )

    class TesseraeAdapter(BaseAdapter):
        """Tesserae as an agent-memory implementation, as far as it honestly goes."""

        def __init__(self, project_root: str | Path) -> None:
            self.memory = TesseraeMemory(project_root)

        # --- Lifecycle ---

        async def setup(self) -> None:
            """Nothing to connect to. Both substrates are local files."""

        async def teardown(self) -> None:
            """Nothing to close. ``SessionChunksDB`` opens a connection per call."""

        async def clear_all_data(self) -> None:
            self.memory.reset()

        # --- Short-term memory (Bronze) ---

        async def add_message(
            self,
            session_id: str,
            role: str,
            content: str,
            *,
            metadata: dict[str, Any] | None = None,
        ) -> "TCKMessage":
            stored = self.memory.add_message(
                session_id, role, content, metadata=metadata
            )
            return TCKMessage(
                id=stored.id,
                role=stored.role,
                content=stored.content,
                timestamp=stored.timestamp,
                metadata=stored.metadata,
            )

        async def get_conversation(
            self,
            session_id: str,
            *,
            limit: int | None = None,
        ) -> "TCKConversation":
            stored = self.memory.messages(session_id)
            messages = [
                TCKMessage(
                    id=m.id,
                    role=m.role,
                    content=m.content,
                    timestamp=m.timestamp,
                    # Empty by construction: the turns table drops everything
                    # but a tool name. See TesseraeMemory.add_message.
                    metadata=m.metadata,
                )
                for m in stored
            ]
            if limit is not None:
                messages = messages[: max(0, int(limit))]
            created = stored[0].timestamp if stored else datetime.now(timezone.utc)
            updated = stored[-1].timestamp if stored else None
            return TCKConversation(
                id=self.memory.conversation_id(session_id),
                session_id=session_id,
                messages=messages,
                title=None,
                created_at=created,
                updated_at=updated,
            )

        async def search_messages(
            self,
            query: str,
            *,
            session_id: str | None = None,
            limit: int = 10,
            threshold: float = 0.7,
        ) -> list["TCKMessage"]:
            return self.memory.search_messages(query)

        async def list_sessions(self, *, limit: int = 100) -> list["TCKSessionInfo"]:
            rows = self.memory.sessions()[: max(0, int(limit))]
            return [
                TCKSessionInfo(
                    session_id=session_id,
                    message_count=count,
                    created_at=created,
                    updated_at=updated,
                )
                for session_id, count, created, updated in rows
            ]

        async def delete_message(self, message_id: uuid.UUID) -> bool:
            return self.memory.delete_message(message_id)

        async def clear_session(self, session_id: str) -> None:
            self.memory.clear_session(session_id)

        # --- Long-term memory (Silver; three writers are also read by Bronze) ---

        async def add_entity(
            self,
            name: str,
            entity_type: str,
            *,
            description: str | None = None,
        ) -> "TCKEntity":
            stored = self.memory.add_entity(
                name, entity_type, description=description
            )
            return TCKEntity(
                id=stored.id,
                name=stored.name,
                # The TCK type the caller asked for, not Tesserae's. The two
                # differ ("PERSON" vs "Person") and ENTITY_TYPE_MAP is the
                # disclosed translation; anything it does not cover is refused.
                type=stored.tck_type,
                subtype=stored.tesserae_type,
                description=stored.description,
                canonical_name=stored.tesserae_id,
                created_at=stored.created_at,
            )

        async def add_preference(
            self,
            category: str,
            preference: str,
            *,
            context: str | None = None,
        ) -> "TCKPreference":
            return self.memory.add_preference(category, preference)

        async def add_fact(self, subject: str, predicate: str, obj: str) -> "TCKFact":
            return self.memory.add_fact(subject, predicate, obj)

        async def search_entities(
            self, query: str, *, limit: int = 10
        ) -> list["TCKEntity"]:
            return self.memory.long_term_read("search_entities")

        async def search_preferences(
            self,
            query: str,
            *,
            category: str | None = None,
            limit: int = 10,
        ) -> list["TCKPreference"]:
            return self.memory.search_preferences(query)

        async def get_entity_by_name(self, name: str) -> "TCKEntity | None":
            return self.memory.long_term_read("get_entity_by_name")

        async def get_related_entities(
            self,
            entity_id: uuid.UUID,
            *,
            relationship_type: str | None = None,
            depth: int = 1,
        ) -> list["TCKEntity"]:
            return self.memory.long_term_read("get_related_entities")

        # --- Reasoning memory (Silver) ---

        async def start_trace(self, session_id: str, task: str) -> "TCKReasoningTrace":
            return self.memory.reasoning("start_trace")

        async def add_step(
            self,
            trace_id: uuid.UUID,
            *,
            thought: str | None = None,
            action: str | None = None,
            observation: str | None = None,
        ) -> "TCKReasoningStep":
            return self.memory.reasoning("add_step")

        async def record_tool_call(
            self,
            step_id: uuid.UUID,
            tool_name: str,
            arguments: dict[str, Any],
            *,
            result: Any = None,
            status: "ToolCallStatus" = ToolCallStatus.SUCCESS,
            duration_ms: int | None = None,
            error: str | None = None,
        ) -> "TCKToolCall":
            return self.memory.reasoning("record_tool_call")

        async def complete_trace(
            self,
            trace_id: uuid.UUID,
            *,
            outcome: str | None = None,
            success: bool | None = None,
        ) -> "TCKReasoningTrace":
            return self.memory.reasoning("complete_trace")

        async def get_trace_with_steps(
            self, trace_id: uuid.UUID
        ) -> "TCKReasoningTrace | None":
            return self.memory.reasoning("get_trace_with_steps")

        async def list_traces(
            self, *, session_id: str | None = None, limit: int = 100
        ) -> list["TCKReasoningTrace"]:
            return self.memory.reasoning("list_traces")

        async def get_tool_stats(
            self, tool_name: str | None = None
        ) -> list["TCKToolStats"]:
            return self.memory.reasoning("get_tool_stats")

    _ADAPTER_CLASS = TesseraeAdapter
    return TesseraeAdapter


__all__ = ["build_adapter_class"]
