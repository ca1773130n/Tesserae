"""Shared chunking so LLM consumers read conversation history IN FULL.

Session history fed to an LLM must be CHUNKED so the model reads all of it
regardless of total length — never truncated to fit, never sent as one
unbounded prompt that overflows the context window. This module is the one
place that policy lives:

* :func:`pack_blocks` — deterministically pack pre-rendered text blocks into
  the minimal number of chunks, each within the char budget, losing nothing.
* :func:`map_reduce_text` — run a prompt over every chunk (map), then merge
  the partial answers (reduce, hierarchically if needed).

Budget math: ``CHUNK_CHAR_BUDGET`` defaults to **48_000 chars ≈ 12k tokens**
at the conservative ~4 chars/token ratio. Every supported backend (Claude
CLI, Codex CLI, Anthropic SDK) offers a context window of at least ~100k
tokens, so a 12k-token user payload leaves ample headroom for the system
prompt and the model's output on all of them. Override with the
``TESSERAE_LLM_CHUNK_CHARS`` env var (floored at 4_000 — below that packing
degenerates into per-line calls).
"""

from __future__ import annotations

import logging
import os
from typing import List, Sequence

logger = logging.getLogger(__name__)

#: Default per-chunk character budget (~12k tokens at ~4 chars/token).
CHUNK_CHAR_BUDGET = 48_000

#: Floor for the env override — smaller budgets degenerate into noise.
MIN_CHUNK_CHARS = 4_000

#: Env var overriding :data:`CHUNK_CHAR_BUDGET` (floored at MIN_CHUNK_CHARS).
CHUNK_CHARS_ENV = "TESSERAE_LLM_CHUNK_CHARS"

#: Headroom reserved for the ``PART i/N`` label prepended to map prompts.
PART_LABEL_HEADROOM = 64

# Hierarchical-reduce safety valve — reduce rounds never exceed this.
_MAX_REDUCE_ROUNDS = 8


def chunk_char_budget() -> int:
    """The effective per-chunk char budget (env override, floored)."""
    raw = os.environ.get(CHUNK_CHARS_ENV, "").strip()
    if raw:
        try:
            return max(MIN_CHUNK_CHARS, int(raw))
        except ValueError:
            logger.warning(
                "%s=%r is not an int; using default %d",
                CHUNK_CHARS_ENV, raw, CHUNK_CHAR_BUDGET,
            )
    return CHUNK_CHAR_BUDGET


def split_text(text: str, budget: int) -> List[str]:
    """Split ``text`` into parts each <= ``budget`` chars, on line boundaries.

    Never splits mid-line unless a single line alone exceeds ``budget``, in
    which case that line is hard-split at ``budget`` chars. Deterministic and
    order-preserving; only newline placement differs from the input (parts are
    re-joined by the caller's separator), so no content characters are lost.
    """
    parts: List[str] = []
    cur: List[str] = []
    cur_len = 0

    def _flush() -> None:
        nonlocal cur, cur_len
        if cur:
            parts.append("\n".join(cur))
            cur, cur_len = [], 0

    for line in text.split("\n"):
        while len(line) > budget:  # single line over budget → hard split
            _flush()
            parts.append(line[:budget])
            line = line[budget:]
        extra = len(line) + (1 if cur else 0)  # +1 for the joining "\n"
        if cur and cur_len + extra > budget:
            _flush()
            extra = len(line)
        cur.append(line)
        cur_len += extra
    _flush()
    return [p for p in parts if p]


def pack_blocks(blocks: Sequence[str], budget: int | None = None) -> List[str]:
    """Pack pre-rendered text ``blocks`` into minimal chunks, each <= ``budget``.

    Blocks are kept whole and in order, joined by ``"\\n\\n"`` inside a chunk.
    A single block larger than ``budget`` is split on line boundaries via
    :func:`split_text` (hard-split only when one line alone exceeds the
    budget). Deterministic, order-preserving, loses NOTHING — the
    concatenation of all chunks equals the concatenation of all blocks modulo
    the newline separators.
    """
    if budget is None:
        budget = chunk_char_budget()
    budget = max(1, int(budget))

    pieces: List[str] = []
    for block in blocks:
        if not block:
            continue
        if len(block) <= budget:
            pieces.append(block)
        else:
            pieces.extend(split_text(block, budget))

    chunks: List[str] = []
    cur: List[str] = []
    cur_len = 0
    for piece in pieces:
        sep = 2 if cur else 0  # the "\n\n" joiner
        if cur and cur_len + sep + len(piece) > budget:
            chunks.append("\n\n".join(cur))
            cur, cur_len = [], 0
            sep = 0
        cur.append(piece)
        cur_len += sep + len(piece)
    if cur:
        chunks.append("\n\n".join(cur))
    return chunks


def _safe_complete(client: object, system: str, user: str) -> str:
    """One ``complete_text`` call that degrades to ``""`` instead of raising."""
    try:
        return (client.complete_text(system=system, user=user) or "").strip()
    except Exception as exc:  # noqa: BLE001 — per-part failures must degrade
        logger.warning("llm_chunking: complete_text failed: %s", exc)
        return ""


def map_reduce_text(
    client: object,
    *,
    map_system: str,
    reduce_system: str,
    chunks: Sequence[str],
    budget: int | None = None,
) -> str:
    """Run ``map_system`` over every chunk, then ``reduce_system`` over the partials.

    * One chunk → a single ``complete_text(map_system, chunk)`` call —
      bit-identical behavior to the pre-chunking code path for small inputs
      (exceptions propagate to the caller exactly as before).
    * Multiple chunks → one map call per chunk (each labeled ``PART i/N``),
      then one reduce call over the joined partial replies. If the joined
      partials exceed ``budget``, the reduce runs hierarchically: partials are
      packed into groups and reduced again until they fit.
    * A ``None``/empty/raising map reply becomes a ``[part i unavailable]``
      marker — never an exception. If EVERY map call fails, returns ``""``.
    """
    if budget is None:
        budget = chunk_char_budget()
    budget = max(1, int(budget))
    chunks = [c for c in chunks if c and c.strip()]
    if not chunks:
        return ""
    if len(chunks) == 1:
        return (client.complete_text(system=map_system, user=chunks[0]) or "").strip()

    n = len(chunks)
    partials: List[str] = []
    any_ok = False
    for i, chunk in enumerate(chunks, 1):
        reply = _safe_complete(client, map_system, f"PART {i}/{n}\n\n{chunk}")
        if reply:
            partials.append(reply)
            any_ok = True
        else:
            partials.append(f"[part {i} unavailable]")
    if not any_ok:
        return ""

    group_budget = max(256, budget - PART_LABEL_HEADROOM)
    for _round in range(_MAX_REDUCE_ROUNDS):
        joined = "\n\n".join(partials)
        if len(joined) <= budget or len(partials) == 1:
            return _safe_complete(client, reduce_system, joined)
        groups = pack_blocks(partials, budget=group_budget)
        if len(groups) >= len(partials):
            # Packing can't compress further (each partial already fills a
            # group) — send the joined text once rather than looping forever.
            return _safe_complete(client, reduce_system, joined)
        m = len(groups)
        partials = []
        for i, group in enumerate(groups, 1):
            reply = _safe_complete(client, reduce_system, f"PART {i}/{m}\n\n{group}")
            partials.append(reply if reply else f"[part {i} unavailable]")
    return _safe_complete(client, reduce_system, "\n\n".join(partials))
