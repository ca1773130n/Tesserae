"""Task 4: feedback events are collected during the vault overlay (unconditional)."""

from __future__ import annotations

from pathlib import Path

import tesserae.vault_pull as vault_pull
from tesserae.extraction_feedback import read_events
from tesserae.project import ProjectWiki
from tesserae.research_graph import ResearchGraph, ResearchNode, ResearchNodeType
from tesserae.vault_pull import VaultOverride


def _graph_with_claim() -> ResearchGraph:
    node = ResearchNode(
        id="Claim:x",
        name="some claim",
        type=ResearchNodeType.CLAIM,
        description="verbose framing about background",
        source_path="docs/a.md",
    )
    return ResearchGraph(nodes=[node], edges=[])


def test_apply_vault_overlay_records_feedback_events(tmp_path: Path, monkeypatch):
    wiki = ProjectWiki(tmp_path)
    wiki.root.mkdir(parents=True, exist_ok=True)

    # A vault directory must exist so overlay does not early-return.
    vault_dir = wiki.paths.obsidian_vault
    vault_dir.mkdir(parents=True, exist_ok=True)

    graph = _graph_with_claim()

    # Force exactly one override for the existing Claim node, and no link
    # changes, so the overlay produces one vault_override event. We patch the
    # vault_pull functions the overlay imports inside the method body.
    one_override = [
        VaultOverride(
            node_id="Claim:x",
            field="description",
            vault_value="concise result",
            snapshot_value="verbose framing about background",
        )
    ]
    monkeypatch.setattr(vault_pull, "compute_overrides", lambda *a, **k: one_override)
    monkeypatch.setattr(vault_pull, "compute_user_link_changes", lambda *a, **k: [])
    monkeypatch.setattr(vault_pull, "_load_vault_files", lambda *a, **k: {})
    monkeypatch.setattr(vault_pull, "apply_overrides", lambda g, ov: g)
    monkeypatch.setattr(vault_pull, "apply_user_link_changes", lambda g, lc: g)
    # A non-None snapshot makes compute_overrides run.
    monkeypatch.setattr("tesserae.vault_snapshot.read_snapshot", lambda p: {})

    wiki._apply_vault_overlay(graph)

    events = read_events(wiki.paths.extraction_feedback)
    assert len(events) == 1
    assert events[0]["node_type"] == "Claim"            # captured at event time
    assert events[0]["target_extractor"] in ("doc_graph", "session_findings")
    assert events[0]["source"] == "vault_override"
    assert events[0]["after_value"] == "concise result"
