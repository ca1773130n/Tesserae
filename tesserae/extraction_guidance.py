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
