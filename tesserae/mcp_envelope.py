"""One response envelope for every MCP tool call: hard caps, paging, tokens.

Before this, only ``context_compiler`` and ``content_resolver`` carried a
budget, so most tools could hand an agent an unbounded list and nothing in the
response said how big it was. The envelope is applied centrally, at the MCP
transport (``MCPRequestHandler`` -> ``call_tool(..., envelope=True)``), never
inside a tool, so no tool can forget it. In-process callers of ``call_tool``
(the CLI, resources, tests) keep the exact payloads they always had.

What every enveloped response gains, additively — no existing key is renamed
or removed:

* ``est_tokens`` — estimated tokens of the response as sent on the wire,
  ``ceil(chars / CHARS_PER_TOKEN)``. A heuristic, not a tokenizer count: it is
  what lets an agent and the read audit compare calls, not a billing number.
* ``truncated`` — ``True`` when the caller did not get everything (more pages,
  or a string clamped to fit ``max_chars``). A tool's own ``truncated`` flag is
  kept and OR-ed in, never overwritten with ``False``.
* ``next_cursor`` — pass it back as ``page_cursor`` to get the next page;
  ``None`` on the last page.
* ``page`` — ``{"offset", "returned", "total"}``, present only when the
  response is a page of a larger result.

Paging contract. A capped tool's primary list (``ToolCaps.items_key``) is cut
into pages of at most ``max_items`` items whose serialized response fits
``max_chars``. The FULL result is snapshotted in-process the first time it
pages, and every later page is served from that snapshot without re-running
the tool. So the pages of one call concatenate to exactly the items an
uncapped call returns — same items, same order — even when the tool's own
ranking reads state the first call mutated (access counts). It also means a
paged read is recorded (access bump, audit row) once, by the first call.
Snapshots are content-keyed and LRU-bounded; an evicted or unknown cursor is a
structured error telling the caller to re-issue the original call, never a
silent restart from page one.

The one case the multiset guarantee cannot hold: a single item whose own
serialization exceeds ``max_chars``. It is returned alone with its longest
strings clamped and ``truncated: True``, because a hard cap that one item can
break is not a cap.

Timeouts are ADVISORY. The stdio server is single-threaded and several tools
write (sidecars, the graph); abandoning a call mid-write to meet a deadline is
worse than a slow answer. A call that overruns its tool's ``timeout_s`` is
logged at WARNING and flagged ``timeout_exceeded: True`` in the response.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import math
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .context_compiler import _truncate_to_budget

_LOG = logging.getLogger(__name__)

JSONDict = Dict[str, Any]

#: Chars per token for ``est_tokens``. 4 is the usual English-text rule of
#: thumb; JSON punctuation tokenizes worse, so the estimate leans low.
CHARS_PER_TOKEN = 4
#: Default hard ceiling on items in one page of a capped list.
DEFAULT_MAX_ITEMS = 100
#: Default hard ceiling on one response's wire size (~30k tokens). Generous on
#: purpose: the tools' own budgets (``budget_chars`` 32,000, the 64 KiB report
#: caps) stay well under it, so the envelope only bites on genuinely unbounded
#: responses — an uncapped ``budget_chars=0``, a large ``limit``.
DEFAULT_MAX_CHARS = 120_000
#: Default advisory deadline for a read tool, in seconds.
DEFAULT_TIMEOUT_S = 30.0

#: Chars held back from ``max_chars`` for the envelope's own keys
#: (``truncated``, ``next_cursor``, ``est_tokens``), which are added after the
#: page is measured.
_ENVELOPE_RESERVE = 256

#: The arguments the envelope consumes. Stripped before the tool sees its
#: arguments, so no tool needs to know they exist.
ENVELOPE_ARGS = ("page_cursor", "max_items")


@dataclass(frozen=True)
class ToolCaps:
    """Per-tool caps. ``items_key`` names the list that pages (None: none does)."""

    items_key: Optional[str] = None
    max_items: int = DEFAULT_MAX_ITEMS
    max_chars: int = DEFAULT_MAX_CHARS
    #: Advisory deadline; 0 means none (writes, and calls that wait on an LLM
    #: for as long as the caller is willing to).
    timeout_s: float = DEFAULT_TIMEOUT_S


_DEFAULT_CAPS = ToolCaps()

#: Tools whose response has one primary list, plus the tools whose deadline
#: differs from the default. Anything unlisted gets ``ToolCaps()``: no paging,
#: the default char cap (strings clamped), the default deadline.
TOOL_CAPS: Mapping[str, ToolCaps] = {
    "search_nodes": ToolCaps("nodes"),
    "search_facts": ToolCaps("facts"),
    "timeline": ToolCaps("events"),
    "node_context": ToolCaps("neighbors"),
    "graph_map": ToolCaps("cards"),
    "graph_ppr": ToolCaps("results"),
    "find_session_findings": ToolCaps("findings"),
    "fresh_insights": ToolCaps("findings"),
    "find_code_symbol_mentions": ToolCaps("mentions"),
    "list_sessions": ToolCaps("sessions"),
    "list_communities": ToolCaps("communities"),
    "list_projects": ToolCaps("projects"),
    "query_decisions": ToolCaps("decisions"),
    "read_audit": ToolCaps("reads"),
    "query": ToolCaps("hits", timeout_s=120.0),
    "compile_context": ToolCaps(timeout_s=120.0),
    "ask": ToolCaps(timeout_s=0.0),
    "activity_summary": ToolCaps(timeout_s=0.0),
    "graph_write": ToolCaps(timeout_s=0.0),
    "ingest": ToolCaps(timeout_s=0.0),
    "doctor_run": ToolCaps(timeout_s=0.0),
    "tesserae_setup_apply": ToolCaps(timeout_s=0.0),
    "tesserae_setup_plan": ToolCaps(timeout_s=0.0),
}


def caps_for(tool: str) -> ToolCaps:
    return TOOL_CAPS.get(tool, _DEFAULT_CAPS)


def wire_text(payload: Any) -> str:
    """The payload exactly as ``MCPRequestHandler`` sends it (indent=2)."""
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def envelope_schema_properties() -> JSONDict:
    """The JSON-schema properties every tool advertises on the MCP transport."""
    return {
        "page_cursor": {
            "type": "string",
            "description": (
                "Resume token from a previous response's next_cursor. With it, "
                "the next page of THAT call is returned from a server-side "
                "snapshot and every other argument except max_items is ignored."
            ),
        },
        "max_items": {
            "type": "integer",
            "minimum": 1,
            "description": (
                "Page size for the tool's primary list; clamped to the tool's "
                "hard cap. Responses also stay under a per-tool character cap "
                "and report est_tokens, truncated and next_cursor."
            ),
        },
    }


def with_envelope_schema(tools: List[JSONDict]) -> List[JSONDict]:
    """``tools`` with the envelope arguments added to every input schema."""
    out: List[JSONDict] = []
    extra = envelope_schema_properties()
    for tool in tools:
        tool = dict(tool)
        schema = dict(tool.get("inputSchema") or {"type": "object"})
        props = dict(schema.get("properties") or {})
        for key, spec in extra.items():
            props.setdefault(key, spec)
        schema["properties"] = props
        tool["inputSchema"] = schema
        out.append(tool)
    return out


class PageStore:
    """In-process LRU of full results that paged, keyed on their content."""

    def __init__(self, capacity: int = 16) -> None:
        self._cap = capacity
        self._items: "OrderedDict[str, Tuple[str, JSONDict]]" = OrderedDict()

    def put(self, tool: str, payload: JSONDict) -> str:
        digest = hashlib.sha256((tool + "\0" + wire_text(payload)).encode("utf-8"))
        sid = "pg_" + digest.hexdigest()[:32]
        self._items[sid] = (tool, payload)
        self._items.move_to_end(sid)
        while len(self._items) > self._cap:
            self._items.popitem(last=False)
        return sid

    def get(self, sid: str) -> Optional[Tuple[str, JSONDict]]:
        hit = self._items.get(sid)
        if hit is not None:
            self._items.move_to_end(sid)
        return hit


def _page_size(caps: ToolCaps, requested: Any) -> int:
    if requested is None:
        return caps.max_items
    try:
        size = int(requested)
    except (TypeError, ValueError):
        raise ValueError(f"max_items must be an integer, got {requested!r}") from None
    return max(1, min(size, caps.max_items))


def _longest_string_path(obj: Any, path: Tuple[Any, ...] = ()) -> Tuple[Tuple[Any, ...], int]:
    best: Tuple[Tuple[Any, ...], int] = ((), -1)
    if isinstance(obj, str):
        return path, len(obj)
    if isinstance(obj, dict):
        children = obj.items()
    elif isinstance(obj, list):
        children = enumerate(obj)
    else:
        return best
    for key, value in children:
        found = _longest_string_path(value, path + (key,))
        if found[1] > best[1]:
            best = found
    return best


def _clamp_to_chars(payload: JSONDict, max_chars: int) -> Tuple[JSONDict, bool]:
    """Cut the longest strings until ``payload`` fits; returns (payload, clamped).

    Works on a deep copy. Stops when nothing long is left to cut — then the
    payload is returned over the cap and the overrun is logged, because the
    remaining bulk is structure (ids, keys) that cannot be shortened honestly.
    """
    limit = max_chars - _ENVELOPE_RESERVE
    size = len(wire_text(payload))
    if size <= limit:
        return payload, False
    out = copy.deepcopy(payload)
    for _ in range(64):
        size = len(wire_text(out))
        if size <= limit:
            return out, True
        path, length = _longest_string_path(out)
        if length < 64:
            break
        parent: Any = out
        for key in path[:-1]:
            parent = parent[key]
        text = parent[path[-1]]
        parent[path[-1]] = _truncate_to_budget(text, max(0, len(text) - (size - limit) - 16))
    _LOG.warning(
        "MCP envelope: response still %d chars after clamping strings (cap %d)",
        len(wire_text(out)),
        max_chars,
    )
    return out, True


def _finish(payload: JSONDict, *, truncated: bool, next_cursor: Optional[str]) -> JSONDict:
    payload["truncated"] = bool(payload.get("truncated") is True or truncated)
    payload["next_cursor"] = next_cursor
    payload["est_tokens"] = 0
    payload["est_tokens"] = estimate_tokens(wire_text(payload))
    return payload


def _page(
    tool: str, sid: str, full: JSONDict, offset: int, size: int, caps: ToolCaps
) -> JSONDict:
    key = caps.items_key
    assert key is not None
    items = full[key]
    total = len(items)
    offset = max(0, min(offset, total))
    limit = caps.max_chars - _ENVELOPE_RESERVE

    def build(n: int) -> JSONDict:
        page = dict(full)
        page[key] = items[offset : offset + n]
        page["page"] = {"offset": offset, "returned": n, "total": total}
        return page

    def fits(n: int) -> bool:
        return len(wire_text(build(n))) <= limit

    # Largest n <= size whose page fits. Size is monotone in n, so bisect; a
    # page always carries at least one item, or paging could never advance.
    avail = total - offset
    n = min(size, avail)
    if n > 1 and not fits(n):
        lo, hi, n = 2, n - 1, 1
        while lo <= hi:
            mid = (lo + hi) // 2
            if fits(mid):
                n, lo = mid, mid + 1
            else:
                hi = mid - 1
    page, clamped = _clamp_to_chars(build(n), caps.max_chars)
    nxt = offset + n
    # The page size rides in the cursor so following next_cursor alone keeps
    # the size the first call asked for.
    cursor = f"{sid}:{nxt}:{size}" if nxt < total else None
    return _finish(page, truncated=clamped or cursor is not None, next_cursor=cursor)


def apply_envelope(
    tool: str, payload: JSONDict, *, max_items: Any = None, pages: PageStore
) -> JSONDict:
    """Cap, page and measure one fresh tool response."""
    caps = caps_for(tool)
    size = _page_size(caps, max_items)
    key = caps.items_key
    items = payload.get(key) if key else None
    if isinstance(items, list) and (
        len(items) > size
        or len(wire_text(payload)) > caps.max_chars - _ENVELOPE_RESERVE
    ):
        sid = pages.put(tool, payload)
        return _page(tool, sid, payload, 0, size, caps)
    clamped_payload, clamped = _clamp_to_chars(payload, caps.max_chars)
    return _finish(dict(clamped_payload), truncated=clamped, next_cursor=None)


def serve_page(tool: str, cursor: Any, *, max_items: Any = None, pages: PageStore) -> JSONDict:
    """The page ``cursor`` points at, from the snapshot its first call stored."""
    raw = str(cursor or "")
    parts = raw.split(":")
    if (
        len(parts) != 3
        or not parts[0].startswith("pg_")
        or not (parts[1].isdigit() and parts[2].isdigit())
    ):
        return _finish(
            {"error": f"malformed page_cursor {raw!r}; pass a next_cursor value back unchanged"},
            truncated=False,
            next_cursor=None,
        )
    sid, offset_text, size_text = parts
    hit = pages.get(sid)
    if hit is None:
        return _finish(
            {
                "error": (
                    "page_cursor expired or unknown — the server keeps only its "
                    "most recent paged results in memory (and none across a "
                    "restart). Re-issue the original call to page it again."
                )
            },
            truncated=False,
            next_cursor=None,
        )
    owner, full = hit
    if owner != tool:
        return _finish(
            {"error": f"page_cursor belongs to tool {owner!r}, not {tool!r}"},
            truncated=False,
            next_cursor=None,
        )
    caps = caps_for(tool)
    size = _page_size(caps, int(size_text) if max_items is None else max_items)
    return _page(tool, sid, full, int(offset_text), size, caps)
