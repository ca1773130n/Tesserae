# Extraction-Feedback Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn human corrections Tesserae already captures (vault edits, review accept/reject) into clustered, LLM-phrased guidance bullets that are injected (opt-in) into the extractor prompts so the extractor stops repeating fixed mistakes.

**Architecture:** Unconditional append-only event collection at the vault-overlay + review-apply sites → `.tesserae/extraction-feedback.jsonl`. A `tesserae project evolve` command clusters events by `(extractor, node_type, field, source)`, LLM-phrases each cluster ≥ MIN_EVENTS (cached by cluster-hash, deterministic fallback when no LLM), and writes `.tesserae/extraction-guidance.md`. Compile with `--use-extraction-feedback` slices that guidance by extractor+node_type and injects it into the two LLM extractor prompts. Collection is always on; injection is flag-gated.

**Tech Stack:** Python 3.9+, stdlib (`json`, `hashlib`, `dataclasses`, `pathlib`), existing `LLMJsonClient`, pytest. Mirrors `tesserae/community_summaries.py` for the cache + LLM-phrase pattern.

**Spec:** `docs/superpowers/specs/2026-05-26-extraction-feedback-loop-design.md`

---

## File structure

| File | Responsibility |
|---|---|
| `tesserae/extraction_feedback.py` (new) | `FeedbackEvent` dataclass, `event_id`/`cluster_key` derivation, JSONL append/read with dedup, `events_from_vault_overlay()` + `events_from_review_decisions()` adapters |
| `tesserae/guidance_markdown.py` (new) | render guidance bullets → `.md`; parse `.md` → bullets; slice by `(extractor, node_type)` |
| `tesserae/extraction_guidance.py` (new) | cluster events, LLM-phrase each cluster (cached, deterministic fallback), negative_value bullet-filter, top-level `build_guidance()` |
| `tesserae/project.py` (modify) | `ProjectPaths` += 3 paths; collect events inside `_apply_vault_overlay`; `evolve()` method; thread guidance into compile when enabled |
| `tesserae/cli.py` (modify) | `tesserae project evolve` subcommand; `compile --use-extraction-feedback` flag |
| `tesserae/llm_extractor.py` (modify) | append doc_graph guidance in `build_research_extraction_prompt` |
| `tesserae/session_graph_llm.py` (modify) | append session_findings guidance to the system prompt in `extract_with_llm` |

**Dependency order:** Task 1 (events) → Task 2 (markdown) → Task 3 (guidance build) → Task 4 (project paths + collection) → Task 5 (evolve + cli) → Task 6 (prompt injection) → Task 7 (end-to-end + flag-off regression).

---

## Task 1: Feedback event model + JSONL store

**Files:**
- Create: `tesserae/extraction_feedback.py`
- Test: `tests/test_extraction_feedback.py`

Real types to integrate (already in repo, do NOT redefine):
- `tesserae/vault_pull.py:65` `VaultOverride(node_id, field, vault_value, snapshot_value)` (frozen)
- `tesserae/vault_pull.py:81` `VaultUserLinkChange(source_node_id, target_slug, target_node_id, action)` (frozen; action ∈ {"add","remove"})
- `tesserae/canonicalization.py:59` `ReviewDecision` and `:43` `ReviewItem`

- [ ] **Step 1: Write failing tests** in `tests/test_extraction_feedback.py`:

```python
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
```

- [ ] **Step 2: Run, verify fail** — `PYTHONPATH=$PWD .venv/bin/pytest -q tests/test_extraction_feedback.py` → FAIL (module missing).

- [ ] **Step 3: Implement `tesserae/extraction_feedback.py`:**

```python
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
```

- [ ] **Step 4: Run, verify pass** — same pytest command → 5 passed.

- [ ] **Step 5: Commit** — `git add tesserae/extraction_feedback.py tests/test_extraction_feedback.py && git commit -m "feat(feedback): FeedbackEvent model + deduped JSONL store + vault-overlay adapter"`

---

## Task 2: Guidance markdown render / parse / slice

**Files:**
- Create: `tesserae/guidance_markdown.py`
- Test: `tests/test_guidance_markdown.py`

- [ ] **Step 1: Write failing tests:**

```python
from tesserae.guidance_markdown import GuidanceBullet, render_guidance, parse_guidance, slice_guidance


def _bullets():
    return [
        GuidanceBullet(extractor="doc_graph", node_type="Claim",
                       cluster_hash="sha256:abc", source="vault_override",
                       field="description", events=7,
                       text="Prefer concise claim descriptions; omit broad framing."),
        GuidanceBullet(extractor="session_findings", node_type="SessionDecision",
                       cluster_hash="sha256:def", source="vault_override",
                       field="body", events=4,
                       text="Phrase decisions as accepted choices, not next steps."),
    ]


def test_render_parse_roundtrip():
    md = render_guidance(_bullets())
    parsed = parse_guidance(md)
    assert {b.text for b in parsed} == {b.text for b in _bullets()}
    assert {b.extractor for b in parsed} == {"doc_graph", "session_findings"}
    assert any(b.events == 7 for b in parsed)


def test_slice_returns_only_matching_extractor_and_type():
    md = render_guidance(_bullets())
    parsed = parse_guidance(md)
    sliced = slice_guidance(parsed, extractor="doc_graph", node_types={"Claim", "Dataset"})
    assert len(sliced) == 1 and sliced[0].node_type == "Claim"
    assert slice_guidance(parsed, extractor="doc_graph", node_types={"Dataset"}) == []


def test_user_deleted_bullet_stays_deleted():
    md = render_guidance(_bullets())
    # Simulate the user deleting the SessionDecision bullet line.
    kept = "\n".join(l for l in md.splitlines() if "accepted choices" not in l)
    parsed = parse_guidance(kept)
    assert all("accepted choices" not in b.text for b in parsed)
```

- [ ] **Step 2: Run, verify fail.**

- [ ] **Step 3: Implement `tesserae/guidance_markdown.py`:**

```python
"""Render/parse/slice the human-curatable extraction-guidance markdown.

Headings carry routing (## Extractor: / ### Node Type:); HTML comments carry
machine identity (cluster hash, source, field, event count) without hurting
readability. Users may delete bullets; deletions survive because parse only
reads what's present.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Set

_SCHEMA_LINE = "<!-- tesserae-guidance-schema: 1 -->"
_CLUSTER_RE = re.compile(
    r"<!--\s*cluster:\s*(?P<hash>sha256:[0-9a-f]+)\s+source=(?P<source>\S+)\s+"
    r"field=(?P<field>\S+)\s+events=(?P<events>\d+)\s*-->"
)


@dataclass
class GuidanceBullet:
    extractor: str
    node_type: str
    cluster_hash: str
    source: str
    field: str
    events: int
    text: str


def render_guidance(bullets: List[GuidanceBullet]) -> str:
    lines = ["# Tesserae Extraction Guidance", "", _SCHEMA_LINE, ""]
    by_ext: dict = {}
    for b in bullets:
        by_ext.setdefault(b.extractor, {}).setdefault(b.node_type, []).append(b)
    for ext in sorted(by_ext):
        lines.append(f"## Extractor: {ext}")
        lines.append("")
        for nt in sorted(by_ext[ext]):
            lines.append(f"### Node Type: {nt}")
            lines.append("")
            for b in by_ext[ext][nt]:
                lines.append(
                    f"<!-- cluster: {b.cluster_hash} source={b.source} "
                    f"field={b.field} events={b.events} -->"
                )
                lines.append(f"- {b.text}")
                lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def parse_guidance(md: str) -> List[GuidanceBullet]:
    bullets: List[GuidanceBullet] = []
    ext = nt = None
    pending = None
    for line in md.splitlines():
        s = line.strip()
        if s.startswith("## Extractor:"):
            ext = s[len("## Extractor:"):].strip(); nt = None
        elif s.startswith("### Node Type:"):
            nt = s[len("### Node Type:"):].strip()
        elif (m := _CLUSTER_RE.search(s)):
            pending = m
        elif s.startswith("- ") and ext and nt:
            text = s[2:].strip()
            if pending:
                bullets.append(GuidanceBullet(
                    extractor=ext, node_type=nt, cluster_hash=pending["hash"],
                    source=pending["source"], field=pending["field"],
                    events=int(pending["events"]), text=text))
            else:
                bullets.append(GuidanceBullet(
                    extractor=ext, node_type=nt, cluster_hash="",
                    source="", field="", events=0, text=text))
            pending = None
    return bullets


def slice_guidance(bullets: List[GuidanceBullet], *, extractor: str,
                   node_types: Set[str]) -> List[GuidanceBullet]:
    return [b for b in bullets if b.extractor == extractor and b.node_type in node_types]
```

- [ ] **Step 4: Run, verify pass** (3 passed).

- [ ] **Step 5: Commit** — `git commit -m "feat(feedback): guidance markdown render/parse/slice"`

---

## Task 3: Cluster + LLM-phrase guidance build (cached, fallback, negative-filter)

**Files:**
- Create: `tesserae/extraction_guidance.py`
- Test: `tests/test_extraction_guidance.py`

Mirror cache pattern from `tesserae/community_summaries.py` (`_cache_path`/`_read_cache`/`_write_cache`, and the `json_client.complete_json(...)` call). `MIN_EVENTS = 3`.

- [ ] **Step 1: Write failing tests:**

```python
import json
from pathlib import Path
from tesserae.extraction_guidance import build_guidance, MIN_EVENTS


class _ScriptedClient:
    def __init__(self, text="Prefer concise descriptions."):
        self.calls = 0; self.text = text
    def complete_json(self, *, system, user, schema_name, cache_key=None):
        self.calls += 1
        return {"bullet": self.text}


def _events(n, **over):
    base = dict(source="vault_override", target_extractor="doc_graph",
                node_type="Claim", field="description", action="replace",
                node_id="Claim:x", source_path="docs/a.md",
                before_value="verbose framing", after_value="concise",
                negative_value="verbose framing",
                cluster_key=["doc_graph", "Claim", "description", "vault_override"])
    return [{**base, "event_id": f"sha256:{i}"} for i in range(n)]


def test_cluster_below_min_events_yields_no_bullet(tmp_path: Path):
    bullets = build_guidance(_events(MIN_EVENTS - 1), cache_dir=tmp_path/"c",
                             json_client=_ScriptedClient())
    assert bullets == []


def test_cluster_at_min_events_phrases_one_bullet(tmp_path: Path):
    client = _ScriptedClient()
    bullets = build_guidance(_events(MIN_EVENTS), cache_dir=tmp_path/"c",
                             json_client=client)
    assert len(bullets) == 1 and bullets[0].extractor == "doc_graph"
    assert client.calls == 1


def test_cache_hit_skips_llm_on_unchanged_cluster(tmp_path: Path):
    client = _ScriptedClient()
    cache = tmp_path / "c"
    build_guidance(_events(MIN_EVENTS), cache_dir=cache, json_client=client)
    build_guidance(_events(MIN_EVENTS), cache_dir=cache, json_client=client)
    assert client.calls == 1  # second run served from cache


def test_no_llm_falls_back_to_deterministic_bullet(tmp_path: Path):
    bullets = build_guidance(_events(MIN_EVENTS), cache_dir=tmp_path/"c",
                             json_client=None)
    assert len(bullets) == 1
    assert bullets[0].text  # non-empty deterministic phrasing


def test_negative_value_bullet_is_filtered(tmp_path: Path):
    # Client returns a bullet that literally recommends the corrected-away value.
    bullets = build_guidance(_events(MIN_EVENTS), cache_dir=tmp_path/"c",
                             json_client=_ScriptedClient(text="Use verbose framing."))
    assert bullets == []  # dropped: recommends a negative_value pattern
```

- [ ] **Step 2: Run, verify fail.**

- [ ] **Step 3: Implement `tesserae/extraction_guidance.py`:**

```python
"""Cluster feedback events and phrase each cluster as one guidance bullet.

Hybrid: deterministic clustering by cluster_key, then a small LLM pass phrases
each cluster (cached by cluster-hash, mirroring community_summaries). Falls
back to deterministic templated phrasing when no LLM is available. Drops any
bullet that recommends a corrected-away (negative_value) pattern.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .guidance_markdown import GuidanceBullet

MIN_EVENTS = 3


def _cluster_hash(key: Sequence[str], event_ids: Sequence[str]) -> str:
    basis = "|".join(key) + "::" + "|".join(sorted(event_ids))
    return "sha256:" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:32]


def _cache_path(cache_dir: Path, h: str) -> Path:
    return cache_dir / (h.replace(":", "_") + ".json")


def _read_cache(p: Path) -> Optional[dict]:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_cache(p: Path, payload: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + f".tmp.{os.getpid()}.{secrets.token_hex(4)}")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
        tmp.rename(p)
    finally:
        if tmp.exists():
            try: tmp.unlink()
            except OSError: pass


def _deterministic_bullet(key, events) -> str:
    extractor, node_type, field, source = key
    return (f"Users repeatedly corrected the `{field}` of {node_type} nodes "
            f"({len(events)} times via {source}); review extraction of this field.")


def _recommends_negative(text: str, events: Sequence[Mapping[str, Any]]) -> bool:
    low = text.lower()
    for e in events:
        neg = e.get("negative_value")
        if neg and isinstance(neg, str) and neg.strip() and neg.strip().lower() in low:
            return True
    return False


def build_guidance(events: Sequence[Mapping[str, Any]], *, cache_dir: Path,
                   json_client=None) -> List[GuidanceBullet]:
    clusters: Dict[tuple, List[Mapping[str, Any]]] = defaultdict(list)
    for e in events:
        key = tuple(e.get("cluster_key") or [])
        if len(key) == 4:
            clusters[key].append(e)

    bullets: List[GuidanceBullet] = []
    for key, evs in sorted(clusters.items()):
        if len(evs) < MIN_EVENTS:
            continue
        extractor, node_type, field, source = key
        h = _cluster_hash(key, [e.get("event_id", "") for e in evs])
        cpath = _cache_path(cache_dir, h)
        cached = _read_cache(cpath)
        if cached and cached.get("text"):
            text = cached["text"]
        elif json_client is not None:
            resp = json_client.complete_json(
                system=_PHRASE_SYSTEM,
                user=_phrase_user(key, evs),
                schema_name="extraction-guidance-bullet-v1",
                cache_key=h,
            )
            text = (resp or {}).get("bullet") or _deterministic_bullet(key, evs)
            _write_cache(cpath, {"text": text, "events": len(evs)})
        else:
            text = _deterministic_bullet(key, evs)
        if _recommends_negative(text, evs):
            continue
        bullets.append(GuidanceBullet(
            extractor=extractor, node_type=node_type, cluster_hash=h,
            source=source, field=field, events=len(evs), text=text))
    return bullets


_PHRASE_SYSTEM = (
    "You write ONE terse extraction-guidance bullet (<= 30 words) from a cluster "
    "of human corrections. State the corrected behavior as a positive instruction. "
    "Never recommend the values users corrected away. Respond JSON: {\"bullet\": \"...\"}."
)


def _phrase_user(key, evs) -> str:
    extractor, node_type, field, source = key
    examples = "\n".join(
        f"- before: {e.get('before_value')!r} → after: {e.get('after_value')!r}"
        for e in evs[:8]
    )
    return (f"Extractor={extractor} node_type={node_type} field={field} source={source}\n"
            f"{len(evs)} corrections, examples:\n{examples}")
```

- [ ] **Step 4: Run, verify pass** (5 passed).

- [ ] **Step 5: Commit** — `git commit -m "feat(feedback): cluster + LLM-phrase guidance build (cached, fallback, negative-filter)"`

---

## Task 4: ProjectPaths + event collection in compile

**Files:**
- Modify: `tesserae/project.py` (`ProjectPaths` ~159-230; `_apply_vault_overlay` ~1079-1129)
- Test: `tests/test_project_feedback_collection.py`

- [ ] **Step 1: Add 3 paths to `ProjectPaths`** (the dataclass at ~159) and the constructor (~204-230, next to `diverged_fields`):

```python
    extraction_feedback: Path
    extraction_guidance: Path
    extraction_guidance_cache: Path
```
constructor additions (mirror `diverged_fields=self.root / "diverged-fields.md"`):
```python
            extraction_feedback=self.root / "extraction-feedback.jsonl",
            extraction_guidance=self.root / "extraction-guidance.md",
            extraction_guidance_cache=self.root / "extraction_guidance_cache",
```

- [ ] **Step 2: Collect events in `_apply_vault_overlay`** — right after the existing `write_diverged_fields_report(overrides, self.paths.diverged_fields, user_link_changes)` call (project.py:1127), insert:

```python
            from .extraction_feedback import events_from_vault_overlay, append_events
            node_types = {n.id: n.type.value for n in graph.nodes}
            source_paths = {n.id: (n.source_path or "") for n in graph.nodes}
            events = events_from_vault_overlay(
                overrides, user_link_changes, node_types, source_paths
            )
            if events:
                append_events(self.paths.extraction_feedback, events)
```

- [ ] **Step 3: Write test** `tests/test_project_feedback_collection.py` — construct a ProjectWiki on tmp_path, monkeypatch `effective_obsidian_vault` + the override/link computation to return one `VaultOverride`, run `_apply_vault_overlay`, assert `paths.extraction_feedback` now has 1 event with `node_type` populated. (Use the existing project-test fixtures in `tests/test_project_*.py` for the ProjectWiki construction pattern — read one first.)

```python
# Skeleton — adapt ProjectWiki construction to match existing project tests.
def test_apply_vault_overlay_records_feedback_events(tmp_path, monkeypatch):
    from tesserae.extraction_feedback import read_events
    wiki = _make_minimal_wiki(tmp_path)            # follow existing test helper
    _seed_one_vault_override(wiki, monkeypatch)    # returns 1 VaultOverride for an existing node
    wiki._apply_vault_overlay(_graph_with_that_node())
    events = read_events(wiki.paths.extraction_feedback)
    assert len(events) == 1
    assert events[0]["node_type"]            # captured at event time
    assert events[0]["target_extractor"] in ("doc_graph", "session_findings")
```

- [ ] **Step 4: Run** the new test + the existing project-overlay tests → all pass (collection is additive; existing behavior unchanged).

- [ ] **Step 5: Commit** — `git commit -m "feat(feedback): collect correction events during vault overlay"`

---

## Task 5: `tesserae project evolve` + `compile --use-extraction-feedback`

**Files:**
- Modify: `tesserae/project.py` (add `evolve()` method), `tesserae/cli.py` (subcommand + flag)
- Test: `tests/test_evolve_cli.py`

- [ ] **Step 1: Add `ProjectWiki.evolve()`** in project.py:

```python
    def evolve(self, json_client=None) -> dict:
        """Distill collected feedback into extraction-guidance.md."""
        from .extraction_feedback import read_events
        from .extraction_guidance import build_guidance
        from .guidance_markdown import render_guidance
        events = read_events(self.paths.extraction_feedback)
        bullets = build_guidance(events, cache_dir=self.paths.extraction_guidance_cache,
                                 json_client=json_client)
        self.paths.extraction_guidance.write_text(
            render_guidance(bullets), encoding="utf-8")
        return {"events": len(events), "bullets": len(bullets),
                "guidance_path": str(self.paths.extraction_guidance)}
```

- [ ] **Step 2: Wire CLI** — add an `evolve` subparser under `tesserae project` (mirror how `schema-drift` is wired) that builds the default json_client (`build_default_json_client`) and calls `wiki.evolve(...)`, printing `events=N bullets=M guidance at <path>`. Add `--use-extraction-feedback` (store_true) to the `compile` subparser.

- [ ] **Step 3: Write test** `tests/test_evolve_cli.py`: seed `extraction-feedback.jsonl` with MIN_EVENTS identical-cluster events (via `append_events`), run `tesserae project evolve` through `cli.main([...])` with a scripted/None client, assert `extraction-guidance.md` is created and contains a `## Extractor:` heading. Assert `compile --help` lists `--use-extraction-feedback`.

- [ ] **Step 4: Run, verify pass.**

- [ ] **Step 5: Commit** — `git commit -m "feat(feedback): tesserae project evolve + compile --use-extraction-feedback flag"`

---

## Task 6: Inject guidance into the two extractor prompts

**Files:**
- Modify: `tesserae/llm_extractor.py` (`build_research_extraction_prompt`, :250), `tesserae/session_graph_llm.py` (`extract_with_llm`, system prompt at :204), and the call sites that thread the flag through.
- Test: `tests/test_guidance_injection.py`

- [ ] **Step 1: Add an optional `guidance: str = ""` param** to `build_research_extraction_prompt(text, source_path, source_kind, guidance="")` — when non-empty, append a clearly-delimited block:

```python
    if guidance:
        prompt += (
            "\n\n## Project-specific extraction guidance "
            "(learned from prior human corrections)\n" + guidance
        )
```
Do the same in `session_graph_llm.extract_with_llm` — accept `guidance: str = ""`, append to the system prompt when non-empty.

- [ ] **Step 2: Thread the sliced guidance** from compile when `--use-extraction-feedback` is set: load + parse `extraction_guidance`, `slice_guidance(extractor="doc_graph", node_types=<doc types>)` for the doc extractor and `extractor="session_findings"` for the session extractor; join bullet texts into the `guidance=` string. When the flag is off, pass `guidance=""` (byte-for-byte unchanged prompt).

- [ ] **Step 3: Write tests:**

```python
def test_doc_prompt_unchanged_without_guidance():
    from tesserae.llm_extractor import build_research_extraction_prompt
    base = build_research_extraction_prompt("text", "a.md", "Paper")
    assert build_research_extraction_prompt("text", "a.md", "Paper", guidance="") == base

def test_doc_prompt_includes_guidance_block_when_present():
    from tesserae.llm_extractor import build_research_extraction_prompt
    p = build_research_extraction_prompt("text", "a.md", "Paper",
                                         guidance="- Be concise.")
    assert "Project-specific extraction guidance" in p and "Be concise." in p
```

- [ ] **Step 4: Run, verify pass.**

- [ ] **Step 5: Commit** — `git commit -m "feat(feedback): inject node-type-routed guidance into extractor prompts"`

---

## Task 7: End-to-end + flag-off regression

**Files:**
- Test: `tests/test_feedback_loop_e2e.py`

- [ ] **Step 1: Write an e2e test** that exercises the whole path with mocked LLM: seed feedback events (≥ MIN_EVENTS in a doc_graph/Claim cluster) → `wiki.evolve(scripted_client)` → assert guidance file has the bullet → parse+slice for doc_graph/Claim returns it → `build_research_extraction_prompt(..., guidance=sliced_text)` contains it. Plus: a `session_findings` cluster's bullet must NOT appear in the doc_graph slice (routing isolation).

- [ ] **Step 2: Run the full suite** — `PYTHONPATH=$PWD .venv/bin/pytest -q tests/` → only the known pre-existing baseline failures (kuzu/cognee/site_js/site_exports/etc.); zero new failures; the new test files all pass.

- [ ] **Step 3: Commit** — `git commit -m "test(feedback): end-to-end loop + routing isolation + flag-off regression"`

---

## Self-review notes (author)
- Spec coverage: event schema (T1), routing-by-node_type (T1 `_route` + T6 slice), guidance md format (T2), cluster+LLM-phrase+cache+MIN_EVENTS+fallback+negative-filter (T3), collection-unconditional (T4), evolve+flag (T5), injection (T6), flag-off byte-identical (T6 test), e2e+routing isolation (T7). ✓
- Guardrail: v1 = negative-value bullet filter only (T3 `_recommends_negative`); full holdout deferred to v2 per spec. ✓
- `node_id` never used for clustering — `cluster_key()` excludes it (T1 test asserts). ✓
