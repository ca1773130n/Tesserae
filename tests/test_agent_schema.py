"""Tests for the agent-layer schema (2026-07-19 layered-agent-kg spec, Phase 1).

Covers the structural data-model surface only — no LLM anywhere:
the three new node types (Agent / DistilledNote / ExpertiseProfile), their
aggressive-dedup exemption and canonicalization exclusion, the new
``performed_by`` / ``reports_to`` edge strings, federation identity keys,
and the closed metadata-allowlist lint probe.
"""

from __future__ import annotations

import json
from pathlib import Path

from tesserae.canonicalization import CANONICALIZABLE_TYPES
from tesserae.federation import identity_key
from tesserae.lint import WikiLinter
from tesserae.research_graph import (
    AGENT_LAYER_TYPES,
    ALLOWED_EDGE_TYPES,
    DISTILLED_MEMORY_TYPES,
    PRIVATE_PUBLIC_RESEARCH_TYPES,
    ResearchEdge,
    ResearchGraphBuilder,
    ResearchNode,
    ResearchNodeType,
    is_public_research_node,
)


# --------------------------------------------------------------------------- helpers


def _agent_node(node_id: str, metadata: dict) -> ResearchNode:
    return ResearchNode(id=node_id, name="Reviewer", type=ResearchNodeType.AGENT, metadata=metadata)


def _scaffold(tmp_path: Path, graph: dict) -> Path:
    """Create a minimal `.tesserae/` layout and return the project root."""
    project = tmp_path / "demo"
    (project / ".tesserae").mkdir(parents=True)
    (project / ".tesserae" / "graph.json").write_text(json.dumps(graph), encoding="utf-8")
    return project


def _graph_node(node_id: str, type_: str, name: str, metadata: dict) -> dict:
    return {
        "id": node_id,
        "type": type_,
        "name": name,
        "aliases": [],
        "description": "",
        "source_path": None,
        "metadata": metadata,
    }


def _agent_findings(project: Path) -> list:
    report = WikiLinter(project).run()
    return [f for f in report.findings if f.code == "AGENT_METADATA_KEY"]


_VALID_AGENT_METADATA = {
    "agent_key": "claude-code:ca1773130n@gmail.com:reviewer",
    "harness": "claude-code",
    "account": "ca1773130n@gmail.com",
    "role": "reviewer",
    "label": "Claude Code",
}

_VALID_NOTE_METADATA = {
    "agent": "claude-code:ca1773130n@gmail.com:reviewer",
    "kind": "runbook",
    "lineage_key": "a" * 64,
    "content_hash": "b" * 64,
    "member_count": 3,
    "member_refs": [{"node_id": "n1", "content_hash": "c" * 64}],
    "absorbed_refs": [],
    "distill_quality": "structural",
    "first_seen_at": "2026-01-01T00:00:00Z",
    "distilled_through": "2026-07-01T00:00:00Z",
}

_VALID_PROFILE_METADATA = {
    "agent": "claude-code:ca1773130n@gmail.com:reviewer",
    "session_count": 4,
    "finding_counts": {"SessionInsight": 2},
    "top_concepts": ["Concept:byte-idempotence:abc123def456"],
    "distilled_through": "2026-07-01T00:00:00Z",
}


# --------------------------------------------------------------------------- enum + sets


def test_agent_layer_enum_members_exist() -> None:
    assert ResearchNodeType.AGENT.value == "Agent"
    assert ResearchNodeType.DISTILLED_NOTE.value == "DistilledNote"
    assert ResearchNodeType.EXPERTISE_PROFILE.value == "ExpertiseProfile"


def test_agent_layer_types_set() -> None:
    assert AGENT_LAYER_TYPES == {
        ResearchNodeType.AGENT,
        ResearchNodeType.DISTILLED_NOTE,
        ResearchNodeType.EXPERTISE_PROFILE,
    }


def test_agent_layer_types_stay_out_of_distilled_memory_types() -> None:
    # DISTILLED_MEMORY_TYPES drives the existing memory.distill pass; the
    # agent layer must not entangle with it.
    assert AGENT_LAYER_TYPES.isdisjoint(DISTILLED_MEMORY_TYPES)


def test_agent_layer_types_excluded_from_canonicalization() -> None:
    assert AGENT_LAYER_TYPES.isdisjoint(CANONICALIZABLE_TYPES)


def test_agent_layer_types_are_projection_private() -> None:
    for node_type in AGENT_LAYER_TYPES:
        assert node_type.value in PRIVATE_PUBLIC_RESEARCH_TYPES
        node = ResearchNode(id=f"x:{node_type.value}", name="X", type=node_type)
        assert not is_public_research_node(node)


# --------------------------------------------------------------------------- edges


def test_new_edge_strings_are_allowed() -> None:
    assert "performed_by" in ALLOWED_EDGE_TYPES
    assert "reports_to" in ALLOWED_EDGE_TYPES
    # Construction must not raise (ResearchEdge validates against the set).
    ResearchEdge(source="s", target="a", type="performed_by")
    ResearchEdge(source="a", target="b", type="reports_to")


def test_distills_to_is_not_an_edge_type() -> None:
    # Distillation provenance reuses ``derived_from`` (spec §4).
    assert "distills_to" not in ALLOWED_EDGE_TYPES
    assert "derived_from" in ALLOWED_EDGE_TYPES


# --------------------------------------------------------------------------- dedup exemption


def test_same_text_agent_layer_nodes_do_not_fuse() -> None:
    # Two agents with the same display name (two accounts running an
    # identically labeled role) are distinct provenance — the aggressive
    # same-name dedup pass must never collapse them.
    seeds = {
        ResearchNodeType.AGENT: ("agent:claude-code:a@x:reviewer", "agent:codex:b@y:reviewer"),
        ResearchNodeType.DISTILLED_NOTE: ("distilled:a@x:0011223344556677", "distilled:b@y:8899aabbccddeeff"),
        ResearchNodeType.EXPERTISE_PROFILE: ("profile:claude-code:a@x:reviewer", "profile:codex:b@y:reviewer"),
    }
    for node_type, (left_seed, right_seed) in seeds.items():
        builder = ResearchGraphBuilder()
        left = builder.add_node("Reviewer", node_type, id_seed=left_seed)
        right = builder.add_node("Reviewer", node_type, id_seed=right_seed)
        assert left.id != right.id
        graph = builder.build()
        survivors = [n for n in graph.nodes if n.type is node_type]
        assert {n.id for n in survivors} == {left.id, right.id}


# --------------------------------------------------------------------------- federation identity


def test_identity_key_agent() -> None:
    node = _agent_node("Agent:reviewer:abc", _VALID_AGENT_METADATA)
    assert identity_key(node) == ("Agent", "claude-code:ca1773130n@gmail.com:reviewer")


def test_identity_key_agent_without_key_never_merges() -> None:
    node = _agent_node("Agent:reviewer:abc", {"harness": "claude-code"})
    assert identity_key(node) is None


def test_identity_key_distilled_note_uses_lineage_key() -> None:
    node = ResearchNode(
        id="DistilledNote:x:abc",
        name="Release flow",
        type=ResearchNodeType.DISTILLED_NOTE,
        metadata={"lineage_key": "f" * 64},
    )
    assert identity_key(node) == ("DistilledNote", "f" * 64)
    bare = ResearchNode(id="DistilledNote:y:def", name="Y", type=ResearchNodeType.DISTILLED_NOTE)
    assert identity_key(bare) is None


def test_identity_key_distilled_runbook_gotcha_use_lineage_key() -> None:
    for node_type in (ResearchNodeType.RUNBOOK, ResearchNodeType.GOTCHA):
        distilled = ResearchNode(
            id=f"{node_type.value}:x:abc",
            name="X",
            type=node_type,
            metadata={"lineage_key": "e" * 64},
        )
        assert identity_key(distilled) == (node_type.value, "e" * 64)
        # Pre-agent-layer Runbook/Gotcha nodes carry no lineage_key and keep
        # the never-auto-merge behavior.
        legacy = ResearchNode(id=f"{node_type.value}:y:def", name="Y", type=node_type)
        assert identity_key(legacy) is None


def test_identity_key_expertise_profile() -> None:
    node = ResearchNode(
        id="ExpertiseProfile:reviewer:abc",
        name="Reviewer",
        type=ResearchNodeType.EXPERTISE_PROFILE,
        metadata=_VALID_PROFILE_METADATA,
    )
    assert identity_key(node) == ("profile", "claude-code:ca1773130n@gmail.com:reviewer")
    bare = ResearchNode(
        id="ExpertiseProfile:x:def", name="X", type=ResearchNodeType.EXPERTISE_PROFILE
    )
    assert identity_key(bare) is None


# --------------------------------------------------------------------------- lint probe


def test_lint_accepts_valid_agent_layer_metadata(tmp_path: Path) -> None:
    graph = {
        "nodes": [
            _graph_node("a1", "Agent", "Reviewer", _VALID_AGENT_METADATA),
            _graph_node("d1", "DistilledNote", "Release flow", _VALID_NOTE_METADATA),
            _graph_node("e1", "ExpertiseProfile", "Reviewer", _VALID_PROFILE_METADATA),
        ],
        "edges": [],
    }
    assert _agent_findings(_scaffold(tmp_path, graph)) == []


def test_lint_rejects_unknown_agent_metadata_key(tmp_path: Path) -> None:
    metadata = dict(_VALID_AGENT_METADATA, color="blue")
    graph = {"nodes": [_graph_node("a1", "Agent", "Reviewer", metadata)], "edges": []}
    findings = _agent_findings(_scaffold(tmp_path, graph))
    assert len(findings) == 1
    assert findings[0].severity == "error"
    assert findings[0].node_id == "a1"
    assert "'color'" in findings[0].message


def test_lint_flags_timestamp_shaped_keys_with_idempotence_message(tmp_path: Path) -> None:
    # The 4x-broken blind spot: wall-clock / counter keys sneaking into
    # graph.json. The probe calls those out explicitly.
    metadata = dict(_VALID_PROFILE_METADATA, last_accessed_at="2026-07-19T00:00:00Z")
    graph = {"nodes": [_graph_node("e1", "ExpertiseProfile", "Reviewer", metadata)], "edges": []}
    findings = _agent_findings(_scaffold(tmp_path, graph))
    assert len(findings) == 1
    assert "timestamp/counter-shaped" in findings[0].message
    assert "CMP-03" in findings[0].message


def test_lint_allows_the_allowlisted_temporal_keys(tmp_path: Path) -> None:
    # first_seen_at / distilled_through / member_count are pure functions of
    # the corpus and explicitly allowed despite their timestamp/counter shape.
    graph = {
        "nodes": [_graph_node("d1", "DistilledNote", "Release flow", _VALID_NOTE_METADATA)],
        "edges": [],
    }
    assert _agent_findings(_scaffold(tmp_path, graph)) == []


def test_lint_rejects_unknown_distilled_note_kind(tmp_path: Path) -> None:
    metadata = dict(_VALID_NOTE_METADATA, kind="vibes")
    graph = {"nodes": [_graph_node("d1", "DistilledNote", "Release flow", metadata)], "edges": []}
    findings = _agent_findings(_scaffold(tmp_path, graph))
    assert len(findings) == 1
    assert "'vibes'" in findings[0].message


def test_lint_ignores_non_agent_layer_metadata(tmp_path: Path) -> None:
    # Session envelopes legitimately carry timestamp metadata; the closed
    # allowlist applies only to the three agent-layer types.
    graph = {
        "nodes": [
            _graph_node(
                "s1", "Session", "session slug", {"started_at": "2026-07-01T00:00:00Z", "turns": 12}
            )
        ],
        "edges": [],
    }
    assert _agent_findings(_scaffold(tmp_path, graph)) == []
