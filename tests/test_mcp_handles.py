"""Read-discipline handles: compile_context preview + get_handle paging."""

from __future__ import annotations

import pytest

from tesserae.mcp_server import _HANDLES, _HandleStore, LLMWikiMCPServer
from tesserae.research_graph import ResearchEdge, ResearchGraph, ResearchNode, ResearchNodeType


def test_handle_store_slice_and_eof():
    s = _HandleStore()
    h = s.put("abcdefghij")  # 10 chars
    first = s.slice(h, 0, 4)
    assert first["slice"] == "abcd" and first["eof"] is False and first["total_chars"] == 10
    last = s.slice(h, 8, 4)
    assert last["slice"] == "ij" and last["eof"] is True
    assert s.slice("nope", 0, 4) is None


def test_handle_store_is_content_keyed_and_lru_capped():
    s = _HandleStore(capacity=2)
    a = s.put("aaa")
    assert s.put("aaa") == a  # same content -> same handle
    s.put("bbb")
    s.put("ccc")  # evicts the least-recently-used ("aaa")
    assert s.slice(a, 0, 3) is None
    assert s.slice(s.put("ccc"), 0, 3)["slice"] == "ccc"


def _graph_path(tmp_path):
    g = ResearchGraph(
        nodes=[
            ResearchNode(id="c1", name="Caching", type=ResearchNodeType.CONCEPT,
                         description="A way to make things fast. " * 40),
            ResearchNode(id="c2", name="Latency", type=ResearchNodeType.CONCEPT,
                         description="Time to first byte. " * 40),
        ],
        edges=[ResearchEdge(source="c1", target="c2", type="references")],
    )
    p = tmp_path / "graph.json"
    p.write_text(g.to_json(), encoding="utf-8")
    return p


def test_get_handle_tool_reassembles(tmp_path):
    server = LLMWikiMCPServer(default_graph_path=_graph_path(tmp_path))
    h = _HANDLES.put("X" * 9000)
    out, acc, off = None, "", 0
    while True:
        out = server.call_tool("get_handle", {"handle": h, "offset": off, "limit": 4000})
        acc += out["slice"]
        if out["eof"]:
            break
        off = out["offset"] + len(out["slice"])
    assert acc == "X" * 9000
    with pytest.raises(ValueError):
        server.call_tool("get_handle", {"handle": "missing"})


def test_compile_context_preview_returns_handle(tmp_path):
    server = LLMWikiMCPServer(default_graph_path=_graph_path(tmp_path))
    full = server.call_tool("compile_context", {"query": "caching latency", "budget": 0})
    body = full.get("body", "")
    if len(body) <= 5:
        pytest.skip("compiled body too short to exercise preview on this graph")
    prev = server.call_tool("compile_context", {"query": "caching latency", "budget": 0, "preview": 5})
    assert "body" not in prev and prev["truncated"] is True
    assert prev["preview"] == body[:5]
    # The handle reconstructs the full body.
    sl = server.call_tool("get_handle", {"handle": prev["handle"], "offset": 0, "limit": 10_000_000})
    assert sl["slice"] == body
