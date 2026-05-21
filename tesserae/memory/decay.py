"""A-MEM / MemoryBank-style decay scoring for session-finding nodes.

Each :class:`tesserae.research_graph.ResearchNode` whose type is one of the
``Session<Kind>`` finding types carries three pieces of memory metadata,
populated by :class:`tesserae.session_graph.SessionGraphExtractor`:

* ``first_seen_at`` — ISO-8601 timestamp the finding was first minted.
* ``last_accessed_at`` — ISO-8601 of the most recent read (currently the
  same as ``first_seen_at`` until an access-recording surface lands).
* ``access_count`` — integer bump-on-read counter.

This module computes a single ``decay_score`` in ``[0, 1]`` from those
fields using the Ebbinghaus-inspired formula adopted by MemoryBank
(see ``/tmp/tesserae-innovation/03-memory.md``)::

    score = exp(-ln(2) * age_days / half_life_days)
          + 0.1 * min(access_count, 10)

The exponential half-life means a finding starts at ``1.0`` the day it
is minted, decays to ``0.5`` after ``half_life_days``, and so on. Each
recorded access bumps the score by ``0.1`` (capped at 10 accesses /
``+1.0``) — a coarse stand-in for "I keep looking at this, it matters".
The final value is clamped to ``[0, 1]`` so downstream consumers never
have to worry about saturating.

Pure function, no I/O — safe to call from any compile-time pass or MCP
tool. The dict-shaped ``node`` accepted here is intentionally loose
(both real ``ResearchNode`` and plain dicts work) so we don't drag the
research_graph import into call sites that only need a score.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

_LN2 = math.log(2.0)
DEFAULT_HALF_LIFE_DAYS: float = 14.0


def _coerce_metadata(node: Any) -> Mapping[str, Any]:
    """Return the metadata mapping from either a ResearchNode or a dict."""
    if hasattr(node, "metadata"):
        meta = getattr(node, "metadata", None) or {}
        return meta if isinstance(meta, Mapping) else {}
    if isinstance(node, Mapping):
        # Caller may pass either the raw metadata dict or a full node dict.
        if "metadata" in node and isinstance(node["metadata"], Mapping):
            return node["metadata"]
        return node
    return {}


def _parse_ts(value: Any) -> Optional[datetime]:
    """Parse an ISO-8601 string into an aware datetime (UTC)."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def compute_decay_score(
    node: Any,
    now: datetime,
    half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
) -> float:
    """Return the decay score in ``[0, 1]`` for ``node`` at ``now``.

    Falls back gracefully when memory metadata is missing: a node with
    no ``first_seen_at`` is treated as freshly minted (score ``1.0``)
    so the bookkeeping degrades safely.
    """
    if half_life_days <= 0:
        raise ValueError("half_life_days must be positive")
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    meta = _coerce_metadata(node)
    # Prefer last_accessed_at — that's the timestamp A-MEM decays from.
    # Fall back to first_seen_at, then to "now" (i.e. brand new node).
    anchor = _parse_ts(meta.get("last_accessed_at")) or _parse_ts(
        meta.get("first_seen_at")
    )
    if anchor is None:
        age_days = 0.0
    else:
        age_days = max((now - anchor).total_seconds() / 86400.0, 0.0)

    base = math.exp(-_LN2 * age_days / float(half_life_days))

    try:
        access_count = int(meta.get("access_count") or 0)
    except (TypeError, ValueError):
        access_count = 0
    bump = 0.1 * min(max(access_count, 0), 10)

    return max(0.0, min(1.0, base + bump))
