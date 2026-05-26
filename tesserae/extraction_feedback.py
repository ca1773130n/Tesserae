"""Feedback events — human corrections captured for the extraction loop.

Append-only JSONL store. Events are deduped by a content hash so the same
vault edit seen across multiple compiles is recorded once. CRITICAL: cluster
on (extractor, node_type, field, source) captured AT EVENT TIME — never on
node_id, which renames/merges/vanishes after projection.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field as dc_field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

SCHEMA_VERSION = 1


def _norm(v: Any) -> str:
    return " ".join(str(v or "").split()).strip().lower()


@dataclass
class FeedbackEvent:
    source: str                  # vault_override | vault_link_change | review_decision
    target_extractor: str        # doc_graph | session_findings | canonicalization
    node_type: str
    field: str
    action: str                  # replace | add_link | remove_link | merge | keep_separate
    node_id: str = ""
    source_path: str = ""
    before_value: Any = None
    after_value: Any = None
    negative_value: Any = None
    related_node_ids: List[str] = dc_field(default_factory=list)
    recorded_at: str = ""

    def cluster_key(self) -> Tuple[str, str, str, str]:
        return (self.target_extractor, self.node_type, self.field, self.source)


def event_id(e: FeedbackEvent) -> str:
    if e.source == "vault_link_change":
        basis = f"{e.source}|{e.node_id}|{e.action}|{e.field}|{e.after_value}"
    elif e.source == "review_decision":
        basis = f"{e.source}|{e.node_id}|{e.action}|{_norm(e.after_value)}"
    else:
        basis = (f"{SCHEMA_VERSION}|{e.source}|{e.node_id}|{e.field}|{e.action}|"
                 f"{_norm(e.before_value)}|{_norm(e.after_value)}")
    return "sha256:" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:32]


def _to_record(e: FeedbackEvent) -> Dict[str, Any]:
    rec = asdict(e)
    rec["schema_version"] = SCHEMA_VERSION
    rec["event_id"] = event_id(e)
    rec["cluster_key"] = list(e.cluster_key())
    if not rec.get("recorded_at"):
        rec["recorded_at"] = datetime.now(timezone.utc).isoformat()
    return rec


def read_events(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def append_events(path: Path, events: Sequence[FeedbackEvent]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = {r.get("event_id") for r in read_events(path)}
    new_records = []
    for e in events:
        rec = _to_record(e)
        if rec["event_id"] in existing:
            continue
        existing.add(rec["event_id"])
        new_records.append(rec)
    if new_records:
        with path.open("a", encoding="utf-8") as fh:
            for rec in new_records:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return len(new_records)


def events_from_vault_overlay(
    overrides, link_changes,
    node_types: Mapping[str, str], source_paths: Mapping[str, str],
) -> List[FeedbackEvent]:
    events: List[FeedbackEvent] = []
    for ov in overrides:
        nt = node_types.get(ov.node_id, "")
        events.append(FeedbackEvent(
            source="vault_override",
            target_extractor=_route(nt),
            node_type=nt, field=ov.field, action="replace",
            node_id=ov.node_id, source_path=source_paths.get(ov.node_id, ""),
            before_value=ov.snapshot_value, after_value=ov.vault_value,
            negative_value=ov.snapshot_value,
        ))
    for lc in link_changes:
        nt = node_types.get(lc.source_node_id, "")
        events.append(FeedbackEvent(
            source="vault_link_change",
            target_extractor=_route(nt),
            node_type=nt, field="user_link",
            action="remove_link" if lc.action == "remove" else "add_link",
            node_id=lc.source_node_id,
            source_path=source_paths.get(lc.source_node_id, ""),
            after_value=lc.target_slug,
            negative_value=lc.target_slug if lc.action == "remove" else None,
            related_node_ids=[lc.target_node_id] if lc.target_node_id else [],
        ))
    return events


# Session-finding node types → session_findings extractor; everything else → doc_graph.
_SESSION_TYPES = {
    "SessionInsight", "SessionDecision", "SessionQuestion",
    "SessionTodo", "SessionHypothesis", "SessionTakeaway",
}


def _route(node_type: str) -> str:
    return "session_findings" if node_type in _SESSION_TYPES else "doc_graph"
