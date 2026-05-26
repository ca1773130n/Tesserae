import json
from pathlib import Path
from tesserae.extraction_feedback import (
    FeedbackEvent, event_id, append_events, read_events,
    events_from_vault_overlay,
)
from tesserae.vault_pull import VaultOverride, VaultUserLinkChange


def test_event_id_stable_and_dedups_identical_corrections():
    e1 = FeedbackEvent(source="vault_override", target_extractor="doc_graph",
                       node_type="Claim", field="description", action="replace",
                       node_id="Claim:x", source_path="docs/a.md",
                       before_value="long bg framing", after_value="concise result",
                       negative_value="long bg framing")
    e2 = FeedbackEvent(**{**e1.__dict__})
    assert event_id(e1) == event_id(e2)  # identical → same id


def test_event_id_differs_on_value_change():
    base = dict(source="vault_override", target_extractor="doc_graph",
                node_type="Claim", field="description", action="replace",
                node_id="Claim:x", source_path="docs/a.md",
                before_value="a", after_value="b", negative_value="a")
    e1 = FeedbackEvent(**base)
    e2 = FeedbackEvent(**{**base, "after_value": "c"})
    assert event_id(e1) != event_id(e2)


def test_append_dedups_across_calls(tmp_path: Path):
    p = tmp_path / "feedback.jsonl"
    e = FeedbackEvent(source="vault_override", target_extractor="doc_graph",
                      node_type="Claim", field="description", action="replace",
                      node_id="Claim:x", source_path="docs/a.md",
                      before_value="a", after_value="b", negative_value="a")
    assert append_events(p, [e]) == 1     # first write: 1 new
    assert append_events(p, [e]) == 0     # second write: deduped
    assert len(read_events(p)) == 1


def test_cluster_key_excludes_node_id():
    e = FeedbackEvent(source="vault_override", target_extractor="doc_graph",
                      node_type="Claim", field="description", action="replace",
                      node_id="Claim:renamed", source_path="docs/a.md",
                      before_value="a", after_value="b", negative_value="a")
    assert e.cluster_key() == ("doc_graph", "Claim", "description", "vault_override")


def test_events_from_vault_overlay_maps_override_and_link():
    overrides = [VaultOverride(node_id="Claim:x", field="description",
                               vault_value="concise", snapshot_value="verbose")]
    links = [VaultUserLinkChange(source_node_id="SessionInsight:y",
                                 target_slug="some-doc", target_node_id="Doc:z",
                                 action="remove")]
    node_types = {"Claim:x": "Claim", "SessionInsight:y": "SessionInsight"}
    source_paths = {"Claim:x": "docs/a.md", "SessionInsight:y": "sess/1"}
    events = events_from_vault_overlay(overrides, links, node_types, source_paths)
    kinds = {e.source for e in events}
    assert kinds == {"vault_override", "vault_link_change"}
    ov = next(e for e in events if e.source == "vault_override")
    assert ov.field == "description" and ov.after_value == "concise"
    assert ov.negative_value == "verbose"   # corrected-away value
