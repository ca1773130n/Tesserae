"""Minimal stdio MCP server for LLM-Wiki research graphs.

This module intentionally avoids a hard dependency on the Python MCP SDK so the
repository can expose a useful MCP interface in the user's current no-extra-setup
local environment. It implements the JSON-RPC methods Hermes and other MCP
clients need for initialization, tool discovery, and tool calls.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .research_graph import ALLOWED_EDGE_TYPES, ALLOWED_NODE_TYPES, ResearchEdge, ResearchGraph, ResearchNode, ResearchNodeType
from .temporal import TemporalFactProjector, search_facts, timeline


JSONDict = Dict[str, Any]


def load_graph(path: str | Path) -> ResearchGraph:
    """Load a ResearchGraph JSON file emitted by ``llm_wiki.cli``."""

    graph_path = Path(path)
    payload = json.loads(graph_path.read_text(encoding="utf-8"))
    nodes = [
        ResearchNode(
            id=str(raw["id"]),
            name=str(raw["name"]),
            type=ResearchNodeType(str(raw["type"])),
            aliases=[str(alias) for alias in raw.get("aliases", [])],
            description=str(raw.get("description") or ""),
            source_path=raw.get("source_path"),
            metadata=dict(raw.get("metadata") or {}),
        )
        for raw in payload.get("nodes", [])
    ]
    edges = [
        ResearchEdge(
            source=str(raw["source"]),
            target=str(raw["target"]),
            type=str(raw["type"]),
            evidence=raw.get("evidence"),
            metadata=dict(raw.get("metadata") or {}),
        )
        for raw in payload.get("edges", [])
    ]
    return ResearchGraph(nodes=nodes, edges=edges)


def node_to_dict(node: ResearchNode) -> JSONDict:
    return node.model_dump()


def edge_to_dict(edge: ResearchEdge) -> JSONDict:
    return edge.model_dump()


class LLMWikiMCPServer:
    """Tool implementation backing the LLM-Wiki MCP JSON-RPC server."""

    def __init__(self, default_graph_path: str | Path | None = None) -> None:
        self.default_graph_path = Path(default_graph_path) if default_graph_path else None

    def list_tools(self) -> List[JSONDict]:
        return [
            {
                "name": "schema",
                "description": "Return the controlled LLM-Wiki research graph node and edge type schema.",
                "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            },
            {
                "name": "graph_summary",
                "description": "Summarize a ResearchGraph JSON file with node/edge counts and type distributions.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"graph_path": {"type": "string", "description": "Path to a ResearchGraph JSON file. Defaults to server --graph."}},
                    "additionalProperties": False,
                },
            },
            {
                "name": "search_nodes",
                "description": "Search graph nodes by name, aliases, description, type, and metadata text.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "graph_path": {"type": "string", "description": "Path to a ResearchGraph JSON file. Defaults to server --graph."},
                        "query": {"type": "string", "description": "Whitespace-separated search terms."},
                        "types": {"type": "array", "items": {"type": "string"}, "description": "Optional whitelist of node types."},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 10},
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "node_context",
                "description": "Return a node plus incident edges and neighboring nodes by node_id or name.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "graph_path": {"type": "string", "description": "Path to a ResearchGraph JSON file. Defaults to server --graph."},
                        "node_id": {"type": "string", "description": "Exact node id to inspect."},
                        "name": {"type": "string", "description": "Exact case-insensitive node name if node_id is omitted."},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "search_facts",
                "description": "Search Graphiti-style temporal facts projected from the validated ResearchGraph, including evidence and provenance.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "graph_path": {"type": "string", "description": "Path to a ResearchGraph JSON file. Defaults to server --graph."},
                        "query": {"type": "string", "description": "Whitespace-separated fact search terms."},
                        "current_only": {"type": "boolean", "default": False},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 10},
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "timeline",
                "description": "Return a temporal timeline of matching facts ordered by valid_from/source time.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "graph_path": {"type": "string", "description": "Path to a ResearchGraph JSON file. Defaults to server --graph."},
                        "query": {"type": "string", "description": "Optional fact search terms."},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
                    },
                    "additionalProperties": False,
                },
            },
        ]

    def call_tool(self, name: str, arguments: Optional[JSONDict] = None) -> JSONDict:
        args = arguments or {}
        if name == "schema":
            return {"node_types": sorted(ALLOWED_NODE_TYPES), "edge_types": sorted(ALLOWED_EDGE_TYPES)}
        if name == "graph_summary":
            return self.graph_summary(self._load_requested_graph(args))
        if name == "search_nodes":
            return self.search_nodes(
                self._load_requested_graph(args),
                query=str(args.get("query", "")),
                types=args.get("types"),
                limit=int(args.get("limit", 10)),
            )
        if name == "node_context":
            return self.node_context(
                self._load_requested_graph(args),
                node_id=args.get("node_id"),
                node_name=args.get("name"),
                limit=int(args.get("limit", 50)),
            )
        if name == "search_facts":
            facts = TemporalFactProjector().project(self._load_requested_graph(args))
            return search_facts(facts, query=str(args.get("query", "")), limit=int(args.get("limit", 10)), current_only=bool(args.get("current_only", False)))
        if name == "timeline":
            facts = TemporalFactProjector().project(self._load_requested_graph(args))
            return timeline(facts, query=str(args.get("query", "")), limit=int(args.get("limit", 50)))
        raise ValueError(f"Unknown LLM-Wiki MCP tool: {name}")

    def graph_summary(self, graph: ResearchGraph) -> JSONDict:
        return {
            "node_count": len(graph.nodes),
            "edge_count": len(graph.edges),
            "node_types": dict(sorted(Counter(node.type.value for node in graph.nodes).items())),
            "edge_types": dict(sorted(Counter(edge.type for edge in graph.edges).items())),
        }

    def search_nodes(self, graph: ResearchGraph, query: str, types: Optional[Iterable[str]] = None, limit: int = 10) -> JSONDict:
        terms = [term.casefold() for term in query.split() if term.strip()]
        type_filter = {str(item) for item in types or []}
        scored = []
        for index, node in enumerate(graph.nodes):
            if type_filter and node.type.value not in type_filter:
                continue
            haystack_parts = [node.id, node.name, node.type.value, node.description, " ".join(node.aliases), json.dumps(node.metadata, ensure_ascii=False)]
            haystack = " ".join(haystack_parts).casefold()
            score = sum(1 for term in terms if term in haystack)
            if not terms or score > 0:
                scored.append((score, index, node))
        scored.sort(key=lambda item: (-item[0], item[1]))
        matches = [node_to_dict(node) for score, _index, node in scored if score > 0 or not terms]
        bounded_limit = max(1, min(limit, 100))
        return {"query": query, "total_matches": len(matches), "nodes": matches[:bounded_limit]}

    def node_context(self, graph: ResearchGraph, node_id: Optional[str] = None, node_name: Optional[str] = None, limit: int = 50) -> JSONDict:
        node = self._find_node(graph, node_id=node_id, node_name=node_name)
        if not node:
            raise ValueError("Node not found; provide an exact node_id or node name")
        bounded_limit = max(1, min(limit, 200))
        node_by_id = {candidate.id: candidate for candidate in graph.nodes}
        incident_edges = [edge for edge in graph.edges if edge.source == node.id or edge.target == node.id][:bounded_limit]
        neighbor_ids = []
        for edge in incident_edges:
            other_id = edge.target if edge.source == node.id else edge.source
            if other_id not in neighbor_ids:
                neighbor_ids.append(other_id)
        neighbors = [node_to_dict(node_by_id[neighbor_id]) for neighbor_id in neighbor_ids if neighbor_id in node_by_id]
        return {"node": node_to_dict(node), "edges": [edge_to_dict(edge) for edge in incident_edges], "neighbors": neighbors}

    def _load_requested_graph(self, args: JSONDict) -> ResearchGraph:
        raw_path = args.get("graph_path")
        graph_path = Path(raw_path) if raw_path else self.default_graph_path
        if not graph_path:
            raise ValueError("graph_path is required when the MCP server was not started with --graph")
        return load_graph(graph_path)

    def _find_node(self, graph: ResearchGraph, node_id: Optional[str], node_name: Optional[str]) -> Optional[ResearchNode]:
        if node_id:
            for node in graph.nodes:
                if node.id == node_id:
                    return node
        if node_name:
            wanted = str(node_name).casefold()
            for node in graph.nodes:
                if node.name.casefold() == wanted:
                    return node
        return None


class MCPRequestHandler:
    """Small JSON-RPC handler for the MCP methods used by tool clients."""

    def __init__(self, server: LLMWikiMCPServer) -> None:
        self.server = server

    def handle_message(self, message: JSONDict) -> Optional[JSONDict]:
        request_id = message.get("id")
        method = message.get("method")
        if request_id is None:
            return None
        try:
            if method == "initialize":
                return self._result(
                    request_id,
                    {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {"listChanged": False}},
                        "serverInfo": {"name": "llm-wiki", "version": "0.1.0"},
                    },
                )
            if method == "tools/list":
                return self._result(request_id, {"tools": self.server.list_tools()})
            if method == "tools/call":
                params = message.get("params") or {}
                tool_name = params.get("name")
                arguments = params.get("arguments") or {}
                payload = self.server.call_tool(str(tool_name), arguments)
                return self._result(
                    request_id,
                    {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2)}], "isError": False},
                )
            return self._error(request_id, -32601, f"Method not found: {method}")
        except Exception as exc:  # MCP tools should surface errors as JSON-RPC errors.
            return self._error(request_id, -32000, str(exc))

    def _result(self, request_id: Any, result: JSONDict) -> JSONDict:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def _error(self, request_id: Any, code: int, message: str) -> JSONDict:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def serve_stdio(server: LLMWikiMCPServer, stdin=sys.stdin, stdout=sys.stdout) -> None:
    handler = MCPRequestHandler(server)
    for line in stdin:
        if not line.strip():
            continue
        response = handler.handle_message(json.loads(line))
        if response is not None:
            stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            stdout.flush()


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run the LLM-Wiki ResearchGraph MCP stdio server.")
    parser.add_argument("--graph", help="Default ResearchGraph JSON file used when tool calls omit graph_path")
    args = parser.parse_args(argv)
    serve_stdio(LLMWikiMCPServer(default_graph_path=args.graph))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
