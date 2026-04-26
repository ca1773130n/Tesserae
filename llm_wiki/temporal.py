"""Temporal fact projection and competitive analysis helpers.

This module absorbs the strongest open-source memory/KG patterns we evaluated:
Graphiti-style temporal facts with provenance, MegaMem-style project/vault
artifacts, and MCP-friendly fact search surfaces — while keeping LLM-Wiki's
controlled ontology and no-API-key local workflow.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from .research_graph import ResearchGraph, ResearchNode, ResearchNodeType, stable_id


CLAIM_TYPES = {
    ResearchNodeType.CLAIM,
    ResearchNodeType.CONTRIBUTION_CLAIM,
    ResearchNodeType.PERFORMANCE_CLAIM,
    ResearchNodeType.COMPARISON_CLAIM,
    ResearchNodeType.LIMITATION_CLAIM,
    ResearchNodeType.CAUSAL_CLAIM,
    ResearchNodeType.OPEN_QUESTION,
}


@dataclass(frozen=True)
class TemporalFact:
    id: str
    subject_id: str
    subject_name: str
    subject_type: str
    predicate: str
    object_id: str
    object_name: str
    object_type: str
    evidence: Optional[str] = None
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None
    current: bool = True
    invalidated_by: List[str] = field(default_factory=list)
    confidence: str = "medium"
    provenance: Dict[str, object] = field(default_factory=dict)
    metadata: Dict[str, object] = field(default_factory=dict)

    def model_dump(self) -> Dict[str, object]:
        return asdict(self)


class TemporalFactProjector:
    """Project validated ResearchGraph edges into temporal, provenance-rich facts."""

    def project(self, graph: ResearchGraph) -> List[TemporalFact]:
        nodes = {node.id: node for node in graph.nodes}
        facts: List[TemporalFact] = []
        edge_to_fact_id: Dict[tuple, str] = {}
        for edge in graph.edges:
            subject = nodes.get(edge.source)
            obj = nodes.get(edge.target)
            if not subject or not obj:
                continue
            fact = self._fact_from_edge(subject, edge.type, obj, edge.evidence, edge.metadata)
            facts.append(fact)
            edge_to_fact_id[(fact.subject_id, fact.predicate, fact.object_id)] = fact.id

        invalidators: Dict[str, List[str]] = {}
        for fact in facts:
            if fact.predicate not in {"contradicts_claim", "supersedes", "invalidates"}:
                continue
            invalidators.setdefault(fact.object_id, []).append(fact.id)

        updated: List[TemporalFact] = []
        for fact in facts:
            invalidating_ids = invalidators.get(fact.object_id if fact.subject_type in {item.value for item in CLAIM_TYPES} else fact.object_id, [])
            if fact.object_id in invalidators and fact.predicate not in {"contradicts_claim", "supersedes", "invalidates"}:
                updated.append(
                    TemporalFact(
                        **{**fact.model_dump(), "current": False, "invalidated_by": invalidators[fact.object_id]}
                    )
                )
            else:
                updated.append(fact)
        return updated

    def write_jsonl(self, graph: ResearchGraph, path: str | Path) -> List[TemporalFact]:
        facts = self.project(graph)
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("".join(json.dumps(fact.model_dump(), ensure_ascii=False, sort_keys=True) + "\n" for fact in facts), encoding="utf-8")
        return facts

    def _fact_from_edge(self, subject: ResearchNode, predicate: str, obj: ResearchNode, evidence: Optional[str], metadata: Dict[str, object]) -> TemporalFact:
        valid_from = first_string(subject.metadata.get("analysis_date"), obj.metadata.get("analysis_date"), metadata.get("analysis_date"))
        source_path = first_string(subject.source_path, obj.source_path, metadata.get("source_path"))
        confidence = first_string(metadata.get("confidence"), subject.metadata.get("confidence"), obj.metadata.get("confidence")) or infer_confidence(subject, obj, evidence)
        fact_id = stable_id("TemporalFact", f"{subject.id}|{predicate}|{obj.id}|{evidence or ''}")
        return TemporalFact(
            id=fact_id,
            subject_id=subject.id,
            subject_name=subject.name,
            subject_type=subject.type.value,
            predicate=predicate,
            object_id=obj.id,
            object_name=obj.name,
            object_type=obj.type.value,
            evidence=evidence,
            valid_from=valid_from or "undated",
            confidence=confidence,
            provenance={"source_path": source_path, "subject_source_path": subject.source_path, "object_source_path": obj.source_path},
            metadata=dict(metadata or {}),
        )


def first_string(*values: object) -> Optional[str]:
    for value in values:
        if value:
            return str(value)
    return None


def infer_confidence(subject: ResearchNode, obj: ResearchNode, evidence: Optional[str]) -> str:
    if subject.type in CLAIM_TYPES or obj.type in CLAIM_TYPES:
        return "medium" if evidence else "low"
    return "high" if evidence else "medium"


def search_facts(facts: Iterable[TemporalFact], query: str, limit: int = 10, current_only: bool = False) -> Dict[str, object]:
    terms = [term.casefold() for term in query.split() if term.strip()]
    scored = []
    for index, fact in enumerate(facts):
        if current_only and not fact.current:
            continue
        text = json.dumps(fact.model_dump(), ensure_ascii=False).casefold()
        score = sum(1 for term in terms if term in text)
        if not terms or score > 0:
            scored.append((score, index, fact))
    scored.sort(key=lambda item: (-item[0], item[1]))
    matches = [fact.model_dump() for score, _index, fact in scored if score > 0 or not terms]
    bounded = max(1, min(limit, 100))
    return {"query": query, "total_matches": len(matches), "facts": matches[:bounded]}


def timeline(facts: Iterable[TemporalFact], query: str = "", limit: int = 50) -> Dict[str, object]:
    result = search_facts(facts, query=query, limit=10_000)
    events = list(result["facts"])
    events.sort(key=lambda item: (str(item.get("valid_from") or ""), str(item.get("subject_name") or ""), str(item.get("predicate") or "")))
    return {"query": query, "total_events": len(events), "events": events[: max(1, min(limit, 200))]}


def render_competitive_report() -> str:
    return """# LLM-Wiki Competitive Hardening Report

## Open-source advantages absorbed

| System | Advantage | LLM-Wiki absorption |
|---|---|---|
| MegaMem | Obsidian/project-local graph artifacts plus MCP exposure | `.llm-wiki/` project workspaces, project compile, Cognee bundle, SQLite, markdown projection, MCP config |
| MegaMem | Sync state and analytics | content-hash manifest, processed/skipped counts, durable report output |
| Graphiti/Zep | temporal facts with validity and provenance | `temporal_facts.jsonl` projects every validated edge into temporal facts with `valid_from`, `current`, `invalidated_by`, confidence, evidence, and source provenance |
| Graphiti/Zep | custom entity/edge types | controlled research ontology and edge whitelist, rejecting schema drift instead of generic `Entity` sprawl |
| Graphiti MCP | fact/entity MCP tools | dependency-light stdio MCP `search_facts`, `timeline`, `search_nodes`, `node_context`, and schema tools |
| Agentic RAG/Qdrant-style systems | semantic retrieval substrate | Cognee export plus local Qwen/Ollama embedding path, no API key required |

## LLM-Wiki differentiators retained

- controlled ontology rather than auto-discovered schema drift
- claim/evidence-first graph model for research intelligence
- project-local and no API key by default
- markdown is a projection, not the graph source of truth
- MCP server works without requiring Neo4j, FalkorDB, Qdrant, or Python MCP SDK

## Remaining next advantages to consider

- optional HTTP/SSE MCP transport with scoped tokens
- richer sync analytics dashboard
- graph diff/review UX for temporal invalidation decisions
- optional hybrid lexical+dense reranking over `temporal_facts.jsonl`
"""
