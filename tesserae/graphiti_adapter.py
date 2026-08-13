"""Optional Graphiti/Zep export and sync helpers.

The core Tesserae graph remains the source of truth. This module projects the
validated research graph into Graphiti-style episodes so projects can optionally
sync temporal facts into a Graphiti backend without making Graphiti a hard
runtime dependency.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass, field
from importlib.util import find_spec
from pathlib import Path
from typing import Any, Dict, List, Optional

from .research_graph import ResearchGraph, stable_id
from .temporal import TemporalFactProjector


class GraphitiSyncUnavailableError(RuntimeError):
    """Raised when the optional Graphiti dependency is not installed."""


@dataclass(frozen=True)
class GraphitiEpisode:
    """Serializable episode payload suitable for Graphiti ingestion."""

    uuid: str
    name: str
    content: str
    source: str = "tesserae"
    source_description: str = "Tesserae controlled research graph temporal fact"
    reference_time: str = "undated"
    group_id: str = "tesserae"
    metadata: Dict[str, object] = field(default_factory=dict)

    def model_dump(self) -> Dict[str, object]:
        return asdict(self)


class GraphitiResearchGraphAdapter:
    """Project a ``ResearchGraph`` into Graphiti-compatible episodes.

    Export is dependency-free. Live sync is optional and raises a helpful error
    if ``graphiti_core`` is not installed.
    """

    def __init__(self, group_id: str = "tesserae") -> None:
        self.group_id = sanitize_graphiti_group_id(group_id)

    def episodes(
        self,
        graph: ResearchGraph,
        memory_by_id: Optional[Dict[str, Any]] = None,
    ) -> List[GraphitiEpisode]:
        # memory_by_id is NOT optional in spirit. Without it every exported
        # confidence falls back to infer_confidence()'s heuristic label, so an
        # external Graphiti consumer read numbers Tesserae itself does not use
        # — temporal_facts.jsonl, written from the same projector three lines
        # earlier in compile, carries the reinforced value. Same graph, same
        # fact, two different confidences in two .tesserae/ artifacts.
        facts = TemporalFactProjector().project(graph, memory_by_id=memory_by_id)
        episodes: List[GraphitiEpisode] = []
        for fact in facts:
            content = (
                f"{fact.subject_name} --{fact.predicate}--> {fact.object_name}\n"
                f"Subject type: {fact.subject_type}\n"
                f"Object type: {fact.object_type}\n"
                f"Evidence: {fact.evidence or 'not provided'}\n"
                f"Confidence: {fact.confidence}\n"
                f"Current: {fact.current}"
            )
            metadata = {
                "fact_id": fact.id,
                "subject_id": fact.subject_id,
                "subject_type": fact.subject_type,
                "object_id": fact.object_id,
                "object_type": fact.object_type,
                "predicate": fact.predicate,
                "confidence": fact.confidence,
                "current": fact.current,
                "invalidated_by": fact.invalidated_by,
                "provenance": fact.provenance,
                "tesserae_metadata": fact.metadata,
            }
            episodes.append(
                GraphitiEpisode(
                    uuid=stable_id("GraphitiEpisode", fact.id),
                    name=f"{fact.subject_name} {fact.predicate} {fact.object_name}",
                    content=content,
                    reference_time=str(fact.valid_from or "undated"),
                    group_id=self.group_id,
                    metadata=metadata,
                )
            )
        return episodes

    def write_episodes(
        self,
        graph: ResearchGraph,
        path: str | Path,
        memory_by_id: Optional[Dict[str, Any]] = None,
    ) -> List[GraphitiEpisode]:
        episodes = self.episodes(graph, memory_by_id=memory_by_id)
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            "".join(json.dumps(episode.model_dump(), ensure_ascii=False, sort_keys=True) + "\n" for episode in episodes),
            encoding="utf-8",
        )
        return episodes

    def sync(
        self,
        graph: ResearchGraph,
        neo4j_uri: str,
        neo4j_user: str,
        neo4j_password: str,
        dry_run: bool = False,
        memory_by_id: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, object]:
        episodes = self.episodes(graph, memory_by_id=memory_by_id)
        if dry_run:
            return {"dry_run": True, "episodes": len(episodes), "group_id": self.group_id}
        ensure_graphiti_available()
        return asyncio.run(self._sync_async(episodes, neo4j_uri, neo4j_user, neo4j_password))

    async def _sync_async(self, episodes: List[GraphitiEpisode], neo4j_uri: str, neo4j_user: str, neo4j_password: str) -> Dict[str, object]:
        from graphiti_core import Graphiti  # type: ignore

        client = Graphiti(neo4j_uri, neo4j_user, neo4j_password)
        try:
            for episode in episodes:
                try:
                    await client.add_episode(**episode.model_dump())
                except TypeError:
                    # Graphiti versions vary on accepted keyword names; retry
                    # without metadata/uuid for older clients.
                    await client.add_episode(
                        name=episode.name,
                        episode_body=episode.content,
                        source=episode.source,
                        source_description=episode.source_description,
                        reference_time=episode.reference_time,
                        group_id=episode.group_id,
                    )
        finally:
            close = getattr(client, "close", None)
            if close is not None:
                result = close()
                if asyncio.iscoroutine(result):
                    await result
        return {"dry_run": False, "episodes": len(episodes), "group_id": self.group_id}


def ensure_graphiti_available() -> None:
    if find_spec("graphiti_core") is None:
        raise GraphitiSyncUnavailableError(
            "Optional dependency graphiti_core is not installed. Install Graphiti first, e.g. "
            "`python3 -m pip install --user graphiti-core`, or use `project export-graphiti` / "
            "`project sync-graphiti --dry-run` for dependency-free episode export."
        )


def sanitize_graphiti_group_id(value: str) -> str:
    cleaned = "".join(ch if (ch.isalnum() or ch in {"_", "-"}) else "_" for ch in value).strip("_-")
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return cleaned or "tesserae"
