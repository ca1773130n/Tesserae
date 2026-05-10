"""Minimal stdio MCP server for LLM-Wiki research graphs.

This module intentionally avoids a hard dependency on the Python MCP SDK so the
repository can expose a useful MCP interface in the user's current no-extra-setup
local environment. It implements the JSON-RPC methods Hermes and other MCP
clients need for initialization, tool discovery, and tool calls.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .ports import GraphStore
from .research_graph import (
    ALLOWED_EDGE_TYPES,
    ALLOWED_NODE_TYPES,
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
    is_public_research_node,
)
from .temporal import TemporalFactProjector, search_facts, timeline
from .wiki_projector import is_code_graph_node, kind_for_node
from .wiki_store import WikiPageStore


JSONDict = Dict[str, Any]


# Cap raw payload sizes returned to MCP clients so a malicious / huge file
# can't blow up the agent's context window.
RAW_SOURCE_BYTE_CAP = 16 * 1024
LINT_REPORT_BYTE_CAP = 64 * 1024
WIKI_BODY_BYTE_CAP = 64 * 1024


_INTERNAL_LINK_RE = re.compile(r"\[\[([^\]\|]+?)(?:\|[^\]]+)?\]\]")
_MARKDOWN_LINK_RE = re.compile(r"\]\(([^)]+)\)")


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


DEFAULT_REGISTRY_PATH = Path.home() / ".llm-wiki" / "registry.json"


def _sanitize_project_name(raw: str) -> str:
    cleaned = "".join(c if c.isalnum() or c in "-_" else "_" for c in raw.strip().lower())
    cleaned = cleaned.strip("_-")
    return cleaned or "project"


class ProjectRegistry:
    """File-backed registry of LLM-Wiki project graphs.

    Each entry maps a friendly name to a project root and its compiled
    graph.json so a single MCP server can serve many projects.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else DEFAULT_REGISTRY_PATH

    def load(self) -> JSONDict:
        if not self.path.exists():
            return {"version": 1, "active": None, "projects": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Corrupt registry at {self.path}: {exc}") from exc
        data.setdefault("version", 1)
        data.setdefault("active", None)
        data.setdefault("projects", {})
        return data

    def save(self, data: JSONDict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def register(self, path: str | Path, name: Optional[str] = None) -> JSONDict:
        graph_path, project_root = _discover_graph_and_root(Path(path).expanduser())
        derived = _sanitize_project_name(name) if name else _sanitize_project_name(project_root.name)
        data = self.load()
        data["projects"][derived] = {
            "root": str(project_root),
            "graph_path": str(graph_path),
        }
        self.save(data)
        return {"name": derived, "root": str(project_root), "graph_path": str(graph_path)}

    def activate(self, name: str) -> JSONDict:
        data = self.load()
        if name not in data["projects"]:
            raise ValueError(f"Unknown project: {name}. Register it first via register_project.")
        data["active"] = name
        self.save(data)
        entry = data["projects"][name]
        return {"name": name, **entry}

    def unregister(self, name: str) -> JSONDict:
        data = self.load()
        if name not in data["projects"]:
            raise ValueError(f"Unknown project: {name}")
        del data["projects"][name]
        if data.get("active") == name:
            data["active"] = None
        self.save(data)
        return {"removed": name}

    def list_projects(self) -> JSONDict:
        data = self.load()
        return {
            "active": data.get("active"),
            "projects": [
                {"name": name, **entry}
                for name, entry in sorted(data["projects"].items())
            ],
        }

    def resolve_graph_path(self, name: str) -> Optional[Path]:
        data = self.load()
        entry = data["projects"].get(name)
        return Path(entry["graph_path"]) if entry else None

    def active_graph_path(self) -> Optional[Path]:
        data = self.load()
        active = data.get("active")
        if not active:
            return None
        entry = data["projects"].get(active)
        return Path(entry["graph_path"]) if entry else None


def _materialize_graph(store: GraphStore) -> ResearchGraph:
    """Snapshot a :class:`GraphStore` into an in-memory :class:`ResearchGraph`.

    Used by the MCP server when the operator points it at a backing store
    (via ``--graph-store-url``) instead of a serialized ``graph.json`` file.
    All tool semantics still operate on a :class:`ResearchGraph`, so we
    materialize one on demand using only the :class:`GraphStore` protocol
    methods. ``query_subgraph`` with every node id as a seed and depth 1
    pulls every edge incident to any node — i.e., the full edge set.
    """
    nodes = list(store.iterate_nodes())
    if not nodes:
        return ResearchGraph(nodes=[], edges=[])
    seeds = [node.id for node in nodes]
    subgraph = store.query_subgraph(seeds, depth=1)
    return ResearchGraph(nodes=nodes, edges=list(subgraph.edges))


# Public ontology types (everything in ALLOWED_NODE_TYPES minus the code-graph
# layer). These are the only types ever surfaced by the MCP `schema` tool —
# CodeProject/SourceFile/CodeClass/CodeFunction/CodeModule/Dependency live in
# code-graph.json and stay invisible to external coding agents.
_CODE_GRAPH_TYPE_VALUES: frozenset[str] = frozenset({
    ResearchNodeType.CODE_PROJECT.value,
    ResearchNodeType.SOURCE_FILE.value,
    ResearchNodeType.CODE_MODULE.value,
    ResearchNodeType.CODE_CLASS.value,
    ResearchNodeType.CODE_FUNCTION.value,
    ResearchNodeType.DEPENDENCY.value,
})
_PUBLIC_NODE_TYPE_VALUES: frozenset[str] = frozenset(ALLOWED_NODE_TYPES) - _CODE_GRAPH_TYPE_VALUES
_KNOWN_WIKI_KINDS: frozenset[str] = frozenset({
    "papers", "concepts", "entities", "topics", "questions", "syntheses", "sources", "repos",
})


def _coerce_str_list(value: Any) -> List[str]:
    """Accept a string, list of strings, or None and return a flat list."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if item is not None and str(item) != ""]
    return [str(value)]


def _project_root_for_graph_path(graph_path: str | Path) -> Optional[Path]:
    """Return the project root for a graph.json path, or None if unrecognizable.

    Recognizes the canonical layout ``<root>/.llm-wiki/graph.json``. Returns
    ``None`` for ad-hoc paths so filesystem-backed tools fall back gracefully.
    """
    p = Path(graph_path).resolve() if Path(graph_path).exists() else Path(graph_path)
    if p.parent.name == ".llm-wiki":
        return p.parent.parent
    return None


def _extract_internal_links(body: str) -> List[JSONDict]:
    """Pull wiki-style and markdown links out of a page body.

    Returns a deduped list of ``{"href": str, "kind": "wikilink"|"markdown"}``
    so agents can crawl page-to-page without re-parsing markdown themselves.
    Wiki-style links are emitted verbatim (no slug coercion) to match the
    static-site renderer's resolution rules.
    """
    seen: dict[str, JSONDict] = {}
    for match in _INTERNAL_LINK_RE.finditer(body):
        href = match.group(1).strip()
        if href and href not in seen:
            seen[href] = {"href": href, "kind": "wikilink"}
    for match in _MARKDOWN_LINK_RE.finditer(body):
        href = match.group(1).strip()
        # Skip absolute external links — agents care about graph-internal nav.
        if not href or href.startswith(("http://", "https://", "mailto:", "#")):
            continue
        if href in seen:
            continue
        seen[href] = {"href": href, "kind": "markdown"}
    return list(seen.values())


def _discover_graph_and_root(path: Path) -> tuple[Path, Path]:
    """Resolve a user-provided path to (graph.json path, project root).

    Accepts:
      - a project root containing ``.llm-wiki/graph.json``
      - the ``.llm-wiki`` directory itself
      - a graph.json file (anywhere)
    """

    p = path.resolve() if path.exists() else path
    if p.is_file() and p.suffix == ".json":
        if p.parent.name == ".llm-wiki":
            return p, p.parent.parent
        return p, p.parent
    if p.is_dir():
        if p.name == ".llm-wiki" and (p / "graph.json").is_file():
            return p / "graph.json", p.parent
        nested = p / ".llm-wiki" / "graph.json"
        if nested.is_file():
            return nested, p
        raise ValueError(f"No .llm-wiki/graph.json found at {p}")
    raise ValueError(f"Path does not exist: {p}")


class LLMWikiMCPServer:
    """Tool implementation backing the LLM-Wiki MCP JSON-RPC server."""

    def __init__(
        self,
        default_graph_path: str | Path | None = None,
        registry_path: str | Path | None = None,
        graph_store: Optional[GraphStore] = None,
    ) -> None:
        self.default_graph_path = Path(default_graph_path) if default_graph_path else None
        self.registry = ProjectRegistry(registry_path)
        self.graph_store = graph_store

    def list_tools(self) -> List[JSONDict]:
        graph_path_prop = {"type": "string", "description": "Path to a ResearchGraph JSON file. Defaults to active project, then server --graph."}
        project_prop = {"type": "string", "description": "Registered project name (see list_projects). Overridden by graph_path."}
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
                    "properties": {"graph_path": graph_path_prop, "project": project_prop},
                    "additionalProperties": False,
                },
            },
            {
                "name": "search_nodes",
                "description": (
                    "Search public research-graph nodes by name, aliases, description, type, "
                    "kind (papers/concepts/entities/topics/questions/syntheses/sources/repos), "
                    "and metadata text. Code-graph nodes (CodeProject/SourceFile/CodeClass/"
                    "CodeFunction/CodeModule/Dependency) are filtered out."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "graph_path": graph_path_prop,
                        "project": project_prop,
                        "query": {"type": "string", "description": "Whitespace-separated search terms (optional)."},
                        "q": {"type": "string", "description": "Alias for 'query' for short call sites."},
                        "type": {
                            "oneOf": [
                                {"type": "string"},
                                {"type": "array", "items": {"type": "string"}},
                            ],
                            "description": "Single ontology type or list of types to filter by (e.g. 'Paper').",
                        },
                        "types": {"type": "array", "items": {"type": "string"}, "description": "Backwards-compatible alias for 'type' (list form)."},
                        "kind": {
                            "oneOf": [
                                {"type": "string"},
                                {"type": "array", "items": {"type": "string"}},
                            ],
                            "description": "Wiki kind filter: papers, concepts, entities, topics, questions, syntheses, sources, repos.",
                        },
                        "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 10},
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "node_context",
                "description": "Return a node plus incident edges and neighboring nodes by node_id or name.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "graph_path": graph_path_prop,
                        "project": project_prop,
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
                        "graph_path": graph_path_prop,
                        "project": project_prop,
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
                        "graph_path": graph_path_prop,
                        "project": project_prop,
                        "query": {"type": "string", "description": "Optional fact search terms."},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "wiki_page",
                "description": (
                    "Return the rendered markdown body of a wiki page for a graph node, "
                    "plus the internal links it references. Reads from .llm-wiki/wiki/<kind>/<slug>.md."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "graph_path": graph_path_prop,
                        "project": project_prop,
                        "node_id": {"type": "string", "description": "Exact node id whose wiki page to return."},
                        "name": {"type": "string", "description": "Exact case-insensitive node name if node_id is omitted."},
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "raw_source",
                "description": (
                    "Return the raw markdown contents of a project-relative source path "
                    "(capped at 16 KB). Used to inspect the original document behind a node."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "graph_path": graph_path_prop,
                        "project": project_prop,
                        "source_path": {"type": "string", "description": "Project-relative source path (e.g. data/research/...)."},
                    },
                    "required": ["source_path"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "lint_report",
                "description": (
                    "Return the contents of .llm-wiki/lint-report.md for the active/given "
                    "project (capped at 64 KB). Empty if the report does not exist."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "graph_path": graph_path_prop,
                        "project": project_prop,
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "ask",
                "description": (
                    "Ask a natural-language question and get an answer from a configured "
                    "memory backend (raganything, cognee, or compiled wiki search). Mirrors "
                    "`llm_wiki project ask`."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string", "description": "The natural-language question."},
                        "backend": {
                            "type": "string",
                            "enum": ["auto", "raganything", "cognee", "wiki"],
                            "default": "auto",
                            "description": "Which backend to use. 'auto' tries raganything (if enabled), then cognee, then compiled wiki search.",
                        },
                        "project": project_prop,
                        "graph_path": graph_path_prop,
                        "top_k": {"type": "integer", "description": "Maximum results/context items.", "default": 5, "minimum": 1, "maximum": 100},
                    },
                    "required": ["question"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "list_projects",
                "description": "List registered LLM-Wiki projects and the active project alias.",
                "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            },
            {
                "name": "register_project",
                "description": "Register a project so future tool calls can reference it by name. Accepts a project root containing .llm-wiki/, the .llm-wiki directory itself, or a graph.json path.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Project root, .llm-wiki dir, or graph.json file."},
                        "name": {"type": "string", "description": "Optional alias; defaults to the project directory name."},
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "activate_project",
                "description": "Set the active project so subsequent tool calls without graph_path/project use it.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "unregister_project",
                "description": "Remove a project from the registry. Clears active if it matched.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                    "additionalProperties": False,
                },
            },
        ]

    def call_tool(self, name: str, arguments: Optional[JSONDict] = None) -> JSONDict:
        args = arguments or {}
        if name == "schema":
            return {
                "node_types": sorted(_PUBLIC_NODE_TYPE_VALUES),
                "edge_types": sorted(ALLOWED_EDGE_TYPES),
                "wiki_kinds": sorted(_KNOWN_WIKI_KINDS),
            }
        if name == "graph_summary":
            return self.graph_summary(self._load_requested_graph(args))
        if name == "search_nodes":
            # Accept both 'query' and 'q' (short alias), plus singular 'type'
            # alongside the legacy 'types' list. Either may be omitted.
            query = str(args.get("query") or args.get("q") or "")
            type_arg = args.get("type")
            types_arg = args.get("types")
            type_filter = _coerce_str_list(type_arg) + _coerce_str_list(types_arg)
            kind_filter = _coerce_str_list(args.get("kind"))
            return self.search_nodes(
                self._load_requested_graph(args),
                query=query,
                types=type_filter or None,
                kinds=kind_filter or None,
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
        if name == "wiki_page":
            graph, project_root = self._load_requested_graph_with_root(args)
            return self.wiki_page(
                graph,
                project_root,
                node_id=args.get("node_id"),
                node_name=args.get("name"),
            )
        if name == "raw_source":
            source_path = args.get("source_path")
            if not source_path:
                raise ValueError("raw_source requires 'source_path'")
            _, project_root = self._load_requested_graph_with_root(args)
            return self.raw_source(project_root, str(source_path))
        if name == "lint_report":
            _, project_root = self._load_requested_graph_with_root(args)
            return self.lint_report(project_root)
        if name == "ask":
            question = str(args.get("question") or "").strip()
            if not question:
                raise ValueError("ask requires 'question'")
            backend = str(args.get("backend") or "auto")
            if backend not in {"auto", "raganything", "cognee", "wiki"}:
                raise ValueError(f"ask: unknown backend {backend!r}")
            top_k = int(args.get("top_k") or 5)
            return self._mcp_ask(args, question=question, backend=backend, top_k=top_k)
        if name == "list_projects":
            return self.registry.list_projects()
        if name == "register_project":
            path = args.get("path")
            if not path:
                raise ValueError("register_project requires 'path'")
            return self.registry.register(str(path), name=args.get("name"))
        if name == "activate_project":
            project = args.get("name")
            if not project:
                raise ValueError("activate_project requires 'name'")
            return self.registry.activate(str(project))
        if name == "unregister_project":
            project = args.get("name")
            if not project:
                raise ValueError("unregister_project requires 'name'")
            return self.registry.unregister(str(project))
        raise ValueError(f"Unknown LLM-Wiki MCP tool: {name}")

    def graph_summary(self, graph: ResearchGraph) -> JSONDict:
        # Code-graph nodes live in code-graph.json; never count them in the
        # MCP-visible summary even if a graph.json happens to include them.
        public_nodes = [node for node in graph.nodes if not is_code_graph_node(node)]
        public_node_ids = {node.id for node in public_nodes}
        public_edges = [
            edge for edge in graph.edges
            if edge.source in public_node_ids and edge.target in public_node_ids
        ]
        return {
            "node_count": len(public_nodes),
            "edge_count": len(public_edges),
            "node_types": dict(sorted(Counter(node.type.value for node in public_nodes).items())),
            "edge_types": dict(sorted(Counter(edge.type for edge in public_edges).items())),
        }

    def search_nodes(
        self,
        graph: ResearchGraph,
        query: str = "",
        types: Optional[Iterable[str]] = None,
        kinds: Optional[Iterable[str]] = None,
        limit: int = 10,
    ) -> JSONDict:
        terms = [term.casefold() for term in query.split() if term.strip()]
        type_filter = {str(item) for item in types or []}
        kind_filter = {str(item).lower() for item in kinds or []}
        scored = []
        for index, node in enumerate(graph.nodes):
            # Code-graph nodes never surface via MCP search.
            if is_code_graph_node(node):
                continue
            if type_filter and node.type.value not in type_filter:
                continue
            if kind_filter:
                node_kind = kind_for_node(node)
                if node_kind is None or node_kind not in kind_filter:
                    continue
            haystack_parts = [
                node.id,
                node.name,
                node.type.value,
                node.description,
                " ".join(node.aliases),
                json.dumps(node.metadata, ensure_ascii=False),
            ]
            haystack = " ".join(haystack_parts).casefold()
            score = sum(1 for term in terms if term in haystack)
            if not terms or score > 0:
                scored.append((score, index, node))
        scored.sort(key=lambda item: (-item[0], item[1]))
        matches = [node_to_dict(node) for score, _index, node in scored if score > 0 or not terms]
        bounded_limit = max(1, min(limit, 100))
        return {"query": query, "total_matches": len(matches), "nodes": matches[:bounded_limit]}

    # ------------------------------------------------------------------ wiki / raw / lint

    def wiki_page(
        self,
        graph: ResearchGraph,
        project_root: Optional[Path],
        node_id: Optional[str] = None,
        node_name: Optional[str] = None,
    ) -> JSONDict:
        node = self._find_node(graph, node_id=node_id, node_name=node_name)
        if not node:
            raise ValueError("wiki_page: node not found; provide an exact node_id or node name")
        kind = kind_for_node(node)
        if kind is None:
            raise ValueError(
                f"wiki_page: node {node.id!r} ({node.type.value}) has no public wiki page "
                f"(it is a code-graph or assertion-layer node)."
            )
        if project_root is None:
            raise ValueError(
                "wiki_page requires a project root — pass graph_path or project, or set a default graph."
            )
        wiki_root = project_root / ".llm-wiki" / "wiki"
        store = WikiPageStore(wiki_root)
        slug = store.slug_for(node.name)
        page_path = store.path_for(kind, slug)
        if not page_path.exists():
            raise ValueError(
                f"wiki_page: no wiki page found at {page_path.relative_to(project_root)} "
                f"for node {node.id!r}. The wiki layer may not be projected."
            )
        page = store.read_page(page_path)
        body = page.body
        if len(body.encode("utf-8")) > WIKI_BODY_BYTE_CAP:
            truncated = body.encode("utf-8")[:WIKI_BODY_BYTE_CAP].decode("utf-8", errors="ignore")
            body = truncated + "\n\n<!-- truncated -->\n"
            truncated_flag = True
        else:
            truncated_flag = False
        return {
            "node_id": node.id,
            "kind": kind,
            "slug": page.slug,
            "title": page.title,
            "path": str(page_path.relative_to(project_root)),
            "body": body,
            "frontmatter": dict(page.frontmatter),
            "internal_links": _extract_internal_links(page.body),
            "truncated": truncated_flag,
        }

    def raw_source(self, project_root: Optional[Path], source_path: str) -> JSONDict:
        if project_root is None:
            raise ValueError(
                "raw_source requires a project root — pass graph_path or project, or set a default graph."
            )
        # Normalize and confine the path to the project root to prevent escapes.
        rel = Path(source_path)
        if rel.is_absolute():
            try:
                rel = Path(rel).resolve().relative_to(project_root.resolve())
            except ValueError as exc:
                raise ValueError(f"raw_source: path is outside the project root: {source_path}") from exc
        target = (project_root / rel).resolve()
        try:
            target.relative_to(project_root.resolve())
        except ValueError as exc:
            raise ValueError(f"raw_source: path escapes the project root: {source_path}") from exc
        if not target.exists() or not target.is_file():
            raise ValueError(f"raw_source: file not found: {source_path}")
        raw = target.read_bytes()
        truncated = len(raw) > RAW_SOURCE_BYTE_CAP
        body = raw[:RAW_SOURCE_BYTE_CAP].decode("utf-8", errors="ignore")
        return {
            "source_path": str(target.relative_to(project_root.resolve())),
            "body": body,
            "byte_count": len(raw),
            "truncated": truncated,
            "cap_bytes": RAW_SOURCE_BYTE_CAP,
        }

    def lint_report(self, project_root: Optional[Path]) -> JSONDict:
        if project_root is None:
            raise ValueError(
                "lint_report requires a project root — pass graph_path or project, or set a default graph."
            )
        report_path = project_root / ".llm-wiki" / "lint-report.md"
        if not report_path.exists():
            return {
                "exists": False,
                "path": str(report_path.relative_to(project_root)),
                "body": "",
                "byte_count": 0,
                "truncated": False,
            }
        raw = report_path.read_bytes()
        truncated = len(raw) > LINT_REPORT_BYTE_CAP
        body = raw[:LINT_REPORT_BYTE_CAP].decode("utf-8", errors="ignore")
        return {
            "exists": True,
            "path": str(report_path.relative_to(project_root)),
            "body": body,
            "byte_count": len(raw),
            "truncated": truncated,
            "cap_bytes": LINT_REPORT_BYTE_CAP,
        }

    def _resolve_project_root_for_ask(self, args: JSONDict) -> Path:
        """Resolve the project root for ``ask`` even when no graph.json exists yet.

        ``ask`` doesn't need a parsed ResearchGraph — it dispatches to memory
        backends or the compiled-wiki helper, both of which want the project
        root. We accept ``project`` (registered alias), ``graph_path`` (any
        path under a ``.llm-wiki`` layout), or fall back to the active
        registry entry. Raises a clear error if none of those resolve.
        """
        raw_path = args.get("graph_path")
        if raw_path:
            root = _project_root_for_graph_path(str(raw_path))
            if root is None:
                raise ValueError(f"ask: graph_path is not under a .llm-wiki layout: {raw_path}")
            return root
        project = args.get("project")
        if project:
            entry_path = self.registry.resolve_graph_path(str(project))
            if entry_path is None:
                raise ValueError(f"ask: unknown project {project!r}. Use list_projects or register_project.")
            root = _project_root_for_graph_path(entry_path)
            if root is None:
                raise ValueError(f"ask: registered project {project!r} has no .llm-wiki layout")
            return root
        active = self.registry.active_graph_path()
        if active is not None:
            root = _project_root_for_graph_path(active)
            if root is not None:
                return root
        if self.default_graph_path:
            root = _project_root_for_graph_path(self.default_graph_path)
            if root is not None:
                return root
        raise ValueError(
            "ask: no project specified. Pass 'project' or 'graph_path', activate a project, "
            "or start the MCP server with --graph pointing at a .llm-wiki layout."
        )

    def _mcp_ask(self, args: JSONDict, *, question: str, backend: str, top_k: int) -> JSONDict:
        """Dispatch ``ask`` to raganything, cognee, or compiled-wiki search.

        Thin adapter around :func:`llm_wiki.query.ask_project` so the MCP
        ``ask`` tool, the ``llm_wiki project ask`` CLI handler, and the new
        top-level ``llm_wiki ask`` command share one dispatcher.
        """
        from .project import ProjectWiki
        from .query import ask_project

        project_root = self._resolve_project_root_for_ask(args)
        wiki = ProjectWiki.load(project_root)
        return ask_project(wiki, question, backend=backend, top_k=top_k)

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
        graph, _root = self._load_requested_graph_with_root(args)
        return graph

    def _load_requested_graph_with_root(self, args: JSONDict) -> Tuple[ResearchGraph, Optional[Path]]:
        """Load the requested graph plus the project root for filesystem lookups.

        ``project_root`` is the directory containing ``.llm-wiki/`` for the
        active source. Returns ``None`` for stores that have no on-disk root
        (e.g. an in-memory ``GraphStore``), which makes filesystem-backed
        tools (``wiki_page``/``raw_source``/``lint_report``) raise a clear
        error instead of misreading paths.
        """
        raw_path = args.get("graph_path")
        if raw_path:
            graph_path = Path(str(raw_path))
            return load_graph(graph_path), _project_root_for_graph_path(graph_path)
        project = args.get("project")
        if project:
            resolved = self.registry.resolve_graph_path(str(project))
            if resolved is None:
                raise ValueError(f"Unknown project: {project}. Use list_projects or register_project.")
            return load_graph(resolved), _project_root_for_graph_path(resolved)
        active = self.registry.active_graph_path()
        if active is not None:
            return load_graph(active), _project_root_for_graph_path(active)
        if self.graph_store is not None:
            return _materialize_graph(self.graph_store), None
        if self.default_graph_path:
            return load_graph(self.default_graph_path), _project_root_for_graph_path(self.default_graph_path)
        raise ValueError(
            "No graph specified. Pass graph_path, project, activate a project, "
            "start the MCP server with --graph, or pass --graph-store-url."
        )

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


def _resolve_auth_token_to_user_id(token: str) -> str:
    """Resolve a HypePaper MCP auth token to its owning user_id.

    Lazy-imports the HypePaper backend so the LLM-Wiki package keeps
    zero hard dependency on it. Runs the async lookup in a fresh event
    loop and returns the user_id as a string. Raises ``RuntimeError``
    with a clear message if the token is unknown / expired / revoked,
    or if the HypePaper backend isn't importable.
    """
    try:
        from src.core.database import AsyncSessionLocal
        from src.features.wiki.mcp_token_service import WikiMcpTokenService
    except ImportError as exc:  # pragma: no cover — import error path
        raise RuntimeError(
            "--auth-token requires the HypePaper backend to be importable "
            "(set PYTHONPATH to hypepaper/backend, or install it as a package)."
        ) from exc

    async def _lookup() -> Optional[str]:
        async with AsyncSessionLocal() as session:
            user = await WikiMcpTokenService.get_user_from_token(token, session)
            return str(user.id) if user else None

    import asyncio

    user_id = asyncio.run(_lookup())
    if not user_id:
        raise RuntimeError(
            "Auth token is invalid, expired, or revoked. Mint a fresh "
            "token from your HypePaper account settings."
        )
    return user_id


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run the LLM-Wiki ResearchGraph MCP stdio server.")
    parser.add_argument("--graph", help="Default ResearchGraph JSON file used when tool calls omit graph_path")
    parser.add_argument(
        "--registry",
        help=f"Path to project registry (default: {DEFAULT_REGISTRY_PATH})",
    )
    parser.add_argument(
        "--graph-store-url",
        help=(
            "URL of a backing GraphStore, e.g. sqlite:///path/to.db or "
            "hypepaper-postgres://user:pass@host/db (HypePaper integration). "
            "When set, tool calls without graph_path/project read from this store."
        ),
    )
    parser.add_argument(
        "--auth-token",
        help=(
            "HypePaper MCP token (mint via Account Settings > MCP Tokens). "
            "When set, the token is resolved to the owning user_id at startup, "
            "and Postgres tool calls are scoped to that user's private graph layer. "
            "Requires --graph-store-url to point at a hypepaper-postgres:// URL."
        ),
    )
    args = parser.parse_args(argv)

    # Resolve auth token → user_id at startup so a bad token fails fast.
    owner_user_id: Optional[str] = None
    if args.auth_token:
        owner_user_id = _resolve_auth_token_to_user_id(args.auth_token)

    graph_store = None
    if args.graph_store_url:
        # Lazy import to keep the resolver path independent of the rest of the module.
        from .graph_stores.url_resolver import resolve_graph_store

        graph_store = resolve_graph_store(
            args.graph_store_url, owner_user_id=owner_user_id
        )
    serve_stdio(
        LLMWikiMCPServer(
            default_graph_path=args.graph,
            registry_path=args.registry,
            graph_store=graph_store,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
