"""Minimal stdio MCP server for Tesserae research graphs.

This module intentionally avoids a hard dependency on the Python MCP SDK so the
repository can expose a useful MCP interface in the user's current no-extra-setup
local environment. It implements the JSON-RPC methods Hermes and other MCP
clients need for initialization, tool discovery, and tool calls.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .ports import GraphStore
from .retrieval.hybrid import (
    DEFAULT_WEIGHTS as _HYBRID_DEFAULT_WEIGHTS,
    ScoredNode as _HybridScoredNode,
    active_embedding_backend as _active_embedding_backend,
    hybrid_search as _hybrid_search,
)
from .research_graph import (
    ALLOWED_EDGE_TYPES,
    ALLOWED_NODE_TYPES,
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
    is_public_research_node,
)
from .retrieval.ppr import personalized_pagerank
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
    """Load a ResearchGraph JSON file emitted by ``tesserae.cli``."""

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


DEFAULT_REGISTRY_PATH = Path.home() / ".tesserae" / "registry.json"


def _sanitize_project_name(raw: str) -> str:
    cleaned = "".join(c if c.isalnum() or c in "-_" else "_" for c in raw.strip().lower())
    cleaned = cleaned.strip("_-")
    return cleaned or "project"


class ProjectRegistry:
    """File-backed registry of Tesserae project graphs.

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
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.rename(self.path)

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

    # ---------------- vault-root extensions for multi-project sync ----------

    def get_vault_root(self) -> Optional[Path]:
        """Return the registry-wide Obsidian vault root, or None if unset.

        When set, every registered project's :meth:`ProjectWiki.effective_obsidian_vault`
        defaults to ``<vault_root>/<alias>/`` so a single command can sync many
        projects into one Obsidian vault without per-project ``--vault`` setup.
        """
        data = self.load()
        configured = (data.get("obsidian") or {}).get("vault_root")
        return Path(configured).expanduser() if configured else None

    def set_vault_root(self, path: Optional[str | Path]) -> None:
        """Persist the registry-wide Obsidian vault root.

        Pass ``None`` to clear. Path is stored verbatim (with ``~`` preserved
        as written) so the registry is portable between accounts that share
        the same home-relative layout.
        """
        data = self.load()
        if path is None:
            if "obsidian" in data:
                data["obsidian"].pop("vault_root", None)
                if not data["obsidian"]:
                    data.pop("obsidian")
        else:
            data.setdefault("obsidian", {})["vault_root"] = str(path)
        self.save(data)

    def alias_for_root(self, project_root: str | Path) -> Optional[str]:
        """Return the registered alias for a project root, or None.

        Used by :meth:`ProjectWiki.effective_obsidian_vault` to compute the
        per-project subdir under the registry vault root.
        """
        target = Path(project_root).resolve()
        data = self.load()
        for name, entry in data.get("projects", {}).items():
            entry_root = Path(entry.get("root", "")).resolve()
            if entry_root == target:
                return name
        return None

    def iter_registered_projects(self) -> Iterable[tuple[str, Path]]:
        """Yield ``(alias, project_root)`` for every registered project."""
        data = self.load()
        for name, entry in sorted(data.get("projects", {}).items()):
            root = entry.get("root")
            if root:
                yield name, Path(root)


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

    Recognizes the canonical layout ``<root>/.tesserae/graph.json``. Returns
    ``None`` for ad-hoc paths so filesystem-backed tools fall back gracefully.
    """
    p = Path(graph_path).resolve() if Path(graph_path).exists() else Path(graph_path)
    if p.parent.name == ".tesserae":
        return p.parent.parent
    return None


from contextlib import contextmanager


@contextmanager
def _claude_config_dir_override(value: Optional[str]):
    """Temporarily set CLAUDE_CONFIG_DIR for the wrapped block.

    The raganything LLM adapter reads CLAUDE_CONFIG_DIR at call time, so
    setting it here lets MCP clients target a multi-account Claude setup
    (e.g. ``~/.claude-personal2``) for a single `ask` invocation without
    leaking the change to other tools or future calls. ``None`` is a no-op
    so callers don't have to branch.
    """
    if not value:
        yield
        return
    previous = os.environ.get("CLAUDE_CONFIG_DIR")
    os.environ["CLAUDE_CONFIG_DIR"] = value
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = previous


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
      - a project root containing ``.tesserae/graph.json``
      - the ``.tesserae`` directory itself
      - a graph.json file (anywhere)
    """

    p = path.resolve() if path.exists() else path
    if p.is_file() and p.suffix == ".json":
        if p.parent.name == ".tesserae":
            return p, p.parent.parent
        return p, p.parent
    if p.is_dir():
        if p.name == ".tesserae" and (p / "graph.json").is_file():
            return p / "graph.json", p.parent
        nested = p / ".tesserae" / "graph.json"
        if nested.is_file():
            return nested, p
        raise ValueError(f"No .tesserae/graph.json found at {p}")
    raise ValueError(f"Path does not exist: {p}")


class LLMWikiMCPServer:
    """Tool implementation backing the Tesserae MCP JSON-RPC server."""

    def __init__(
        self,
        default_graph_path: str | Path | None = None,
        registry_path: str | Path | None = None,
        graph_store: Optional[GraphStore] = None,
    ) -> None:
        self.default_graph_path = Path(default_graph_path) if default_graph_path else None
        self.registry = ProjectRegistry(registry_path)
        self.graph_store = graph_store
        self._graph_cache: Dict[Path, Tuple[float, ResearchGraph]] = {}

    def list_tools(self) -> List[JSONDict]:
        graph_path_prop = {"type": "string", "description": "Path to a ResearchGraph JSON file. Defaults to active project, then server --graph."}
        project_prop = {"type": "string", "description": "Registered project name (see list_projects). Overridden by graph_path."}
        return [
            {
                "name": "schema",
                "description": "Return the controlled Tesserae research graph node and edge type schema.",
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
                        "mode": {
                            "type": "string",
                            "enum": ["hybrid", "bm25", "lexical", "embedding", "legacy"],
                            "default": "hybrid",
                            "description": (
                                "Retrieval mode. 'hybrid' (default) fuses BM25 + lexical + "
                                "embedding lanes via reciprocal-rank-fusion. 'legacy' preserves "
                                "the original substring matcher for backwards compatibility."
                            ),
                        },
                        "weights": {
                            "type": "object",
                            "description": "Optional per-lane weight overrides (keys: bm25, lexical, embedding).",
                            "additionalProperties": {"type": "number"},
                        },
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "embedding_status",
                "description": "Report the active embedding backend powering hybrid search.",
                "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
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
                    "plus the internal links it references. Reads from .tesserae/wiki/<kind>/<slug>.md."
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
                    "Return the contents of .tesserae/lint-report.md for the active/given "
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
                    "`tesserae project ask`. Supports cross-vault fan-out via `scope`."
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
                        "scope": {
                            "type": "string",
                            "enum": ["current", "all-registered"],
                            "default": "current",
                            "description": "B2: 'current' (default) targets the resolved project; 'all-registered' fans out across every registered project and returns an aggregated envelope.",
                        },
                        "scope_aliases": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "When scope='all-registered', optionally restrict to this list of registered alias names.",
                        },
                        "claude_config_dir": {
                            "type": "string",
                            "description": (
                                "Override CLAUDE_CONFIG_DIR for this call. Lets MCP clients "
                                "target a multi-account Claude setup (e.g. ~/.claude-personal2). "
                                "Mirrors the CLI's --raganything-claude-config-dir."
                            ),
                        },
                    },
                    "required": ["question"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "list_projects",
                "description": "List registered Tesserae projects and the active project alias.",
                "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            },
            {
                "name": "register_project",
                "description": "Register a project so future tool calls can reference it by name. Accepts a project root containing .tesserae/, the .tesserae directory itself, or a graph.json path.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Project root, .tesserae dir, or graph.json file."},
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
            # Session-graph queries (see docs/superpowers/specs/
            # 2026-05-19-session-graph-extractor-design.md). Surfaces the
            # Session envelopes + their derived findings so an agent can
            # answer "what did we work on yesterday?" and "what did we
            # decide about this paper?" without scanning the full graph.
            {
                "name": "list_sessions",
                "description": (
                    "List Session nodes for the active project. Returns the "
                    "lightweight envelope per session (id, started_at, title, "
                    "files_touched, finding counts). Use find_session_findings "
                    "to pull the structured Insight / Decision / Question / "
                    "TODO / Hypothesis / Takeaway nodes for one session."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "since": {
                            "type": "string",
                            "description": "ISO date or datetime; only sessions started after this are returned.",
                        },
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 200,
                            "description": "Maximum number of sessions to return (default 20, newest first).",
                        },
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "find_session_findings",
                "description": (
                    "Return Session<Kind> findings related to a specific node. "
                    "The node is matched as either the source or the target of "
                    "`discussed_in` / `references` edges. Optionally filter to "
                    "specific finding kinds (insight, decision, question, todo, "
                    "hypothesis, takeaway)."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "node_id": {
                            "type": "string",
                            "description": "Exact node id (e.g. Paper:arxiv-…) to look up findings for.",
                        },
                        "kinds": {
                            "type": "array",
                            "items": {
                                "type": "string",
                                "enum": [
                                    "insight", "decision", "question",
                                    "todo", "hypothesis", "takeaway",
                                ],
                            },
                            "description": "Optional whitelist of finding kinds to include.",
                        },
                    },
                    "required": ["node_id"],
                    "additionalProperties": False,
                },
            },
            # HippoRAG-style multi-hop seed expansion. See
            # tesserae/retrieval/ppr.py and feature B of
            # /tmp/tesserae-innovation/SYNTHESIS.md. Given one or more
            # seed nodes (typically the entity hits for a query), runs
            # Personalized PageRank over the typed graph so callers can
            # union the result with vector / BM25 hits.
            {
                "name": "graph_ppr",
                "description": (
                    "Run Personalized PageRank seeded at one or more nodes "
                    "and return the top-K most relevant nodes. Useful for "
                    "multi-hop relevance — e.g. seeded at a SessionInsight, "
                    "it surfaces related Insights, Decisions, and Sessions "
                    "that aren't immediate 1-hop neighbours."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "graph_path": graph_path_prop,
                        "project": project_prop,
                        "seed_node_id": {
                            "oneOf": [
                                {"type": "string"},
                                {"type": "array", "items": {"type": "string"}},
                            ],
                            "description": (
                                "One node id or a list of node ids to use as "
                                "PPR teleport seeds."
                            ),
                        },
                        "top_k": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 200,
                            "default": 20,
                        },
                        "alpha": {
                            "type": "number",
                            "exclusiveMinimum": 0.0,
                            "maximum": 1.0,
                            "default": 0.15,
                            "description": "Teleport probability in (0, 1] (default 0.15 — classic PageRank).",
                        },
                        "directed": {
                            "type": "boolean",
                            "default": False,
                            "description": "Treat edges as directed. Default is undirected (better for relevance).",
                        },
                        "edge_type_weights": {
                            "type": "object",
                            "additionalProperties": {"type": "number"},
                            "description": (
                                "Optional per-edge-type weight overrides; "
                                "defaults upweight session-finding edges."
                            ),
                        },
                    },
                    "required": ["seed_node_id"],
                    "additionalProperties": False,
                },
            },
            # Community summaries (post-compile pass; opt-in via
            # ``TESSERAE_COMMUNITY_SUMMARIES=true``). GraphRAG-style global
            # themes view: each entry bundles a cluster title, description,
            # tags, and member node IDs.
            {
                "name": "list_communities",
                "description": (
                    "List COMMUNITY_SUMMARY nodes minted by the post-compile pass, "
                    "ranked by member count. Use node_context on the returned "
                    "community_id to walk `summarizes` edges back to members."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "graph_path": graph_path_prop,
                        "project": project_prop,
                        "min_size": {"type": "integer", "minimum": 2, "default": 3},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 20},
                    },
                    "additionalProperties": False,
                },
            },
            # A-MEM / MemoryBank-inspired freshness ranking. Returns
            # session findings ordered by ``compute_decay_score`` so the
            # caller can ask "what's still hot in this project's memory?"
            # without scanning the full graph. Skips findings that have
            # been superseded by a newer near-duplicate.
            {
                "name": "fresh_insights",
                "description": (
                    "Return session findings ranked by Ebbinghaus-style "
                    "decay score (newest + most-accessed first). Filters "
                    "out findings superseded by a newer near-duplicate. "
                    "Optionally restrict to a single finding kind."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "graph_path": graph_path_prop,
                        "project": project_prop,
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 200,
                            "description": "Maximum findings to return (default 10).",
                        },
                        "kind": {
                            "type": "string",
                            "enum": [
                                "insight", "decision", "question",
                                "todo", "hypothesis", "takeaway",
                            ],
                            "description": "Restrict to one finding kind.",
                        },
                    },
                    "additionalProperties": False,
                },
            },
        ]

    # ------------------------------------------------------------------ Resources
    #
    # MCP Resources are read-only context that clients can fetch by URI without
    # invoking a tool. Modern clients (Claude Code, Cursor) auto-load resources
    # the user picks from a palette, so exposing the schema, the latest lint
    # report, and individual wiki pages here means callers don't have to spend
    # tool turns on what amounts to "read this file".
    #
    # URI scheme: ``tesserae://<category>/...``. Static resources live under
    # ``graph/*``; project-relative artifacts live under ``lint-report``,
    # ``wiki/<kind>/<slug>``, and ``raw/<source-path>``.
    #
    # The latter three are exposed as resource templates (URI patterns) rather
    # than enumerated, because enumerating every wiki page on every list call
    # would balloon the response.

    _RESOURCE_TEMPLATES = (
        {
            "uriTemplate": "tesserae://graph/summary",
            "name": "Active project — graph summary",
            "description": (
                "JSON summary of the currently active Tesserae project's typed graph: "
                "node and edge counts plus type distributions. Cheaper than calling the "
                "graph_summary tool when you just need orientation."
            ),
            "mimeType": "application/json",
        },
        {
            "uriTemplate": "tesserae://graph/schema",
            "name": "Graph schema",
            "description": (
                "JSON listing of the controlled node types, edge types, and wiki kinds "
                "Tesserae recognises. Same payload as the schema tool but loadable "
                "without a tool call."
            ),
            "mimeType": "application/json",
        },
        {
            "uriTemplate": "tesserae://lint-report",
            "name": "Active project — latest lint report",
            "description": (
                "The markdown lint report from the most recent `tesserae project compile`. "
                "Capped at 64 KB."
            ),
            "mimeType": "text/markdown",
        },
        {
            "uriTemplate": "tesserae://wiki/{kind}/{slug}",
            "name": "Wiki page",
            "description": (
                "Compiled wiki page body for a typed node, addressed by wiki kind "
                "(papers, concepts, entities, topics, questions, syntheses, sources, "
                "repos) and slug. Returns the markdown projection."
            ),
            "mimeType": "text/markdown",
        },
        {
            "uriTemplate": "tesserae://raw/{source_path}",
            "name": "Raw source",
            "description": (
                "Raw markdown for a source path the typed graph references. Capped at "
                "16 KB. Matches the raw_source tool but loadable as a resource."
            ),
            "mimeType": "text/markdown",
        },
    )

    def list_resources(self) -> List[JSONDict]:
        """Concrete (non-templated) resources for ``resources/list``.

        We only enumerate the two static URIs here — schema is project-agnostic
        and summary keys off the active project. Wiki pages and raw sources are
        exposed via :meth:`list_resource_templates` so clients can construct
        URIs on demand instead of paging through hundreds of nodes.
        """
        return [
            {
                "uri": "tesserae://graph/schema",
                "name": "Graph schema",
                "description": "Controlled node/edge/kind vocabulary.",
                "mimeType": "application/json",
            },
            {
                "uri": "tesserae://graph/summary",
                "name": "Active project — graph summary",
                "description": "Node and edge counts for the active project.",
                "mimeType": "application/json",
            },
            {
                "uri": "tesserae://lint-report",
                "name": "Active project — lint report",
                "description": "Latest compile-time lint findings.",
                "mimeType": "text/markdown",
            },
        ]

    def list_resource_templates(self) -> List[JSONDict]:
        """Resource templates for ``resources/templates/list``."""
        return list(self._RESOURCE_TEMPLATES)

    def read_resource(self, uri: str) -> JSONDict:
        """Read a resource by URI. Returns a contents-list shaped per MCP spec."""
        parsed = self._parse_resource_uri(uri)
        if parsed is None:
            raise ValueError(
                f"Unsupported resource URI: {uri!r}. "
                f"Expected tesserae://graph/{{schema,summary}}, "
                f"tesserae://lint-report, tesserae://wiki/<kind>/<slug>, or "
                f"tesserae://raw/<source-path>."
            )
        category, rest = parsed
        if category == "graph" and rest == ("schema",):
            payload = self.call_tool("schema")
            return self._resource_text(uri, "application/json", json.dumps(payload, ensure_ascii=False, indent=2))
        if category == "graph" and rest == ("summary",):
            payload = self.call_tool("graph_summary")
            return self._resource_text(uri, "application/json", json.dumps(payload, ensure_ascii=False, indent=2))
        if category == "lint-report" and not rest:
            payload = self.call_tool("lint_report")
            text = str(payload.get("body") or payload.get("text") or "")
            return self._resource_text(uri, "text/markdown", text)
        if category == "wiki" and len(rest) == 2:
            kind, slug = rest
            payload = self.call_tool("wiki_page", {"name": slug})
            body = str(payload.get("body") or "")
            if not body:
                # wiki_page accepts node_id or name; try kind+slug as a node id
                payload = self.call_tool("wiki_page", {"node_id": f"{kind}:{slug}"})
                body = str(payload.get("body") or "")
            return self._resource_text(uri, "text/markdown", body)
        if category == "raw" and rest:
            source_path = "/".join(rest)
            payload = self.call_tool("raw_source", {"source_path": source_path})
            text = str(payload.get("body") or payload.get("text") or "")
            return self._resource_text(uri, "text/markdown", text)
        raise ValueError(f"Resource URI does not match any handler: {uri!r}")

    @staticmethod
    def _parse_resource_uri(uri: str) -> Optional[Tuple[str, Tuple[str, ...]]]:
        prefix = "tesserae://"
        if not uri.startswith(prefix):
            return None
        rest = uri[len(prefix):].strip("/")
        if not rest:
            return None
        parts = rest.split("/")
        return parts[0], tuple(parts[1:])

    @staticmethod
    def _resource_text(uri: str, mime: str, text: str) -> JSONDict:
        return {"contents": [{"uri": uri, "mimeType": mime, "text": text}]}

    # ------------------------------------------------------------------ Prompts
    #
    # MCP Prompts are templated user messages that an MCP client surfaces as
    # one-click templates (e.g. Claude Code's `/` palette). Each entry below
    # tells the model exactly which Tesserae tools/resources to chain to
    # answer a recurring research question, so the user doesn't have to
    # rewrite the same orchestration prompt every time.

    _PROMPTS = (
        {
            "name": "summarize-paper",
            "description": (
                "Produce a concise, cite-everything summary of a paper in the wiki — "
                "key contribution, method sketch, headline results, and limitations. "
                "Chains node_context + wiki_page + raw_source tools."
            ),
            "arguments": [
                {
                    "name": "slug",
                    "description": "Wiki slug or exact node name of the paper (e.g. 'arxiv-2308-04079' or '3D Gaussian Splatting...').",
                    "required": True,
                },
            ],
        },
        {
            "name": "find-related-work",
            "description": (
                "Given a topic or concept, surface the most related papers/repos in the "
                "corpus and explain why each is relevant. Uses search_nodes + node_context."
            ),
            "arguments": [
                {
                    "name": "topic",
                    "description": "Topic, concept slug, or free-text descriptor.",
                    "required": True,
                },
                {
                    "name": "limit",
                    "description": "Maximum related items to return (default 8).",
                    "required": False,
                },
            ],
        },
        {
            "name": "compare-approaches",
            "description": (
                "Side-by-side comparison of two approaches (architectures, methods, or "
                "frameworks): goals, mechanisms, headline results, where they diverge. "
                "Uses node_context on both nodes and search_facts for performance claims."
            ),
            "arguments": [
                {"name": "a", "description": "First approach slug or name.", "required": True},
                {"name": "b", "description": "Second approach slug or name.", "required": True},
            ],
        },
        {
            "name": "gap-analysis",
            "description": (
                "Identify gaps in the corpus for a topic — open questions, missing "
                "benchmarks, under-explored sub-areas. Combines search_facts and the "
                "OpenQuestion node type."
            ),
            "arguments": [
                {
                    "name": "topic",
                    "description": "Topic to analyse. Omit for a corpus-wide gap scan.",
                    "required": False,
                },
            ],
        },
        {
            "name": "triage-open-questions",
            "description": (
                "List every OpenQuestion node in the active project, group by topic, and "
                "propose a priority order based on dependency and recency. Pure "
                "search_nodes + node_context, no LLM needed for retrieval."
            ),
            "arguments": [],
        },
    )

    def list_prompts(self) -> List[JSONDict]:
        return [dict(p) for p in self._PROMPTS]

    def get_prompt(self, name: str, arguments: Optional[JSONDict] = None) -> JSONDict:
        """Render a prompt to its MCP ``messages`` payload.

        The model the client routes to receives the rendered user message and
        can chain Tesserae tools to fulfil it. We deliberately keep the prompt
        text concrete and tool-aware so models don't waste turns rediscovering
        the available surface.
        """
        args = arguments or {}
        if name == "summarize-paper":
            slug = str(args.get("slug") or "").strip()
            if not slug:
                raise ValueError("summarize-paper requires argument 'slug'")
            text = (
                f"Summarize the paper at wiki slug `{slug}` from the active Tesserae "
                f"project. Steps:\n"
                f"1. Call `node_context` with name=`{slug}` to load the paper node, its "
                f"incident edges, and immediate neighbours.\n"
                f"2. Call `wiki_page` with name=`{slug}` for the projected page body.\n"
                f"3. If the body references a `source_path`, optionally call `raw_source` "
                f"for the original markdown.\n"
                f"Return a structured summary: (a) headline contribution, (b) method "
                f"sketch, (c) headline results with metric+dataset, (d) limitations / "
                f"open questions raised, (e) the 3 most relevant connected nodes from "
                f"the corpus. Cite every claim with the node slug it came from."
            )
            return self._prompt_messages("Summarize a paper from the active wiki.", text)
        if name == "find-related-work":
            topic = str(args.get("topic") or "").strip()
            limit = int(args.get("limit") or 8)
            if not topic:
                raise ValueError("find-related-work requires argument 'topic'")
            text = (
                f"Find work in the active Tesserae project related to `{topic}`. Steps:\n"
                f"1. Call `search_nodes` with query=`{topic}` limit={limit + 4} and "
                f"narrow to kinds papers,repos,concepts.\n"
                f"2. For the top {limit} candidates, call `node_context` to inspect "
                f"their relations.\n"
                f"3. Return a ranked list with for each item: slug, type, a one-sentence "
                f"justification of relevance, and the connecting edge(s) to `{topic}`."
            )
            return self._prompt_messages("Find related work for a topic.", text)
        if name == "compare-approaches":
            a = str(args.get("a") or "").strip()
            b = str(args.get("b") or "").strip()
            if not (a and b):
                raise ValueError("compare-approaches requires arguments 'a' and 'b'")
            text = (
                f"Compare approaches `{a}` and `{b}` using the active Tesserae project. Steps:\n"
                f"1. Call `node_context` for both nodes.\n"
                f"2. Call `search_facts` with query=`{a}` and again with query=`{b}` to "
                f"pull headline performance / contribution claims.\n"
                f"3. Return a side-by-side table with columns: goal, mechanism / how it "
                f"works, headline result, known limitations, where they diverge.\n"
                f"4. End with a one-paragraph synthesis on when to pick `{a}` vs `{b}`. "
                f"Cite every cell."
            )
            return self._prompt_messages("Compare two approaches side-by-side.", text)
        if name == "gap-analysis":
            topic = str(args.get("topic") or "").strip()
            scoped = f" scoped to `{topic}`" if topic else " across the entire corpus"
            text = (
                f"Run a gap analysis{scoped} against the active Tesserae project. Steps:\n"
                f"1. Call `search_nodes` with type=OpenQuestion"
                + (f" and query=`{topic}`" if topic else "")
                + ".\n"
                f"2. Call `search_facts` "
                + (f"with query=`{topic}` " if topic else "")
                + "to surface limitation/contribution claims.\n"
                f"3. Group findings into: open questions still unresolved, "
                f"under-evidenced claims, missing benchmarks/datasets, papers cited but "
                f"not present.\n"
                f"4. Propose 3 concrete next steps the maintainer could take to close "
                f"the largest gap."
            )
            return self._prompt_messages("Surface gaps in the corpus.", text)
        if name == "triage-open-questions":
            text = (
                "Triage every OpenQuestion node in the active Tesserae project. Steps:\n"
                "1. Call `search_nodes` with type=OpenQuestion limit=100.\n"
                "2. For each, call `node_context` to see what it connects to.\n"
                "3. Group by topic/research field.\n"
                "4. Return a prioritised list with: slug, one-line restatement of the "
                "question, who/what it blocks (from incoming edges), and a "
                "priority score (high/med/low) with reasoning. No prose summary."
            )
            return self._prompt_messages("Triage open questions in the corpus.", text)
        raise ValueError(f"Unknown prompt: {name}")

    @staticmethod
    def _prompt_messages(description: str, text: str) -> JSONDict:
        return {
            "description": description,
            "messages": [
                {"role": "user", "content": {"type": "text", "text": text}},
            ],
        }

    # --------------------------------------------------------------- Tool dispatch

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
            mode = str(args.get("mode") or "hybrid")
            weights_arg = args.get("weights")
            weights = None
            if isinstance(weights_arg, dict):
                weights = {str(k): float(v) for k, v in weights_arg.items()}
            return self.search_nodes(
                self._load_requested_graph(args),
                query=query,
                types=type_filter or None,
                kinds=kind_filter or None,
                limit=int(args.get("limit", 10)),
                mode=mode,
                weights=weights,
            )
        if name == "embedding_status":
            return self.embedding_status()
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
            scope = str(args.get("scope") or "current")
            if scope not in {"current", "all-registered"}:
                raise ValueError(f"ask: unknown scope {scope!r}")
            claude_config_dir = args.get("claude_config_dir")
            claude_config_dir = str(claude_config_dir).strip() if claude_config_dir else None
            with _claude_config_dir_override(claude_config_dir):
                if scope == "all-registered":
                    return self._mcp_ask_all_registered(
                        question=question,
                        backend=backend,
                        top_k=top_k,
                        scope_aliases=_coerce_str_list(args.get("scope_aliases")),
                    )
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
        if name == "list_sessions":
            graph = self._load_requested_graph(args)
            return self._mcp_list_sessions(
                graph,
                since=args.get("since"),
                limit=int(args.get("limit") or 20),
            )
        if name == "find_session_findings":
            node_id = args.get("node_id")
            if not node_id:
                raise ValueError("find_session_findings requires 'node_id'")
            graph = self._load_requested_graph(args)
            return self._mcp_find_session_findings(
                graph,
                node_id=str(node_id),
                kinds=args.get("kinds"),
            )
        if name == "graph_ppr":
            seed = args.get("seed_node_id")
            if seed is None or (isinstance(seed, (list, tuple)) and not seed):
                raise ValueError("graph_ppr requires 'seed_node_id'")
            seed_ids = _coerce_str_list(seed) if not isinstance(seed, str) else [seed]
            graph = self._load_requested_graph(args)
            edge_weights = args.get("edge_type_weights") or None
            # Preserve an explicit alpha (even a tiny one like 0.05) rather
            # than collapsing it via ``or 0.15``. ``alpha=0`` is rejected
            # downstream by ``personalized_pagerank`` and by the schema's
            # ``exclusiveMinimum: 0``.
            alpha_arg = args.get("alpha")
            alpha = 0.15 if alpha_arg is None else float(alpha_arg)
            return self._mcp_graph_ppr(
                graph,
                seed_ids=seed_ids,
                top_k=int(args.get("top_k") or 20),
                alpha=alpha,
                directed=bool(args.get("directed") or False),
                edge_type_weights=edge_weights,
            )
        if name == "list_communities":
            graph = self._load_requested_graph(args)
            return self._mcp_list_communities(
                graph,
                min_size=int(args.get("min_size") or 3),
                limit=int(args.get("limit") or 20),
            )
        if name == "fresh_insights":
            graph = self._load_requested_graph(args)
            return self._mcp_fresh_insights(
                graph,
                limit=int(args.get("limit") or 10),
                kind=(str(args.get("kind")).strip() if args.get("kind") else None),
            )
        raise ValueError(f"Unknown Tesserae MCP tool: {name}")

    def _mcp_graph_ppr(
        self,
        graph: ResearchGraph,
        *,
        seed_ids: Sequence[str],
        top_k: int = 20,
        alpha: float = 0.15,
        directed: bool = False,
        edge_type_weights: Optional[Dict[str, float]] = None,
    ) -> JSONDict:
        """Run PPR and decorate results with node name/type for the agent."""
        ranked = personalized_pagerank(
            graph,
            seed_ids=seed_ids,
            alpha=alpha,
            top_k=top_k,
            edge_type_weights=edge_type_weights,
            directed=directed,
        )
        index = {node.id: node for node in graph.nodes}
        results: List[JSONDict] = []
        for node_id, score in ranked:
            node = index.get(node_id)
            if node is None:
                continue
            results.append({
                "node_id": node.id,
                "name": node.name,
                "type": node.type.value,
                "score": float(score),
            })
        return {
            "seed_ids": list(seed_ids),
            "alpha": alpha,
            "directed": directed,
            "results": results,
        }

    # ------------------------------------------------------------------
    # Session-graph tool implementations
    # ------------------------------------------------------------------

    _SESSION_FINDING_TYPES = {
        "SessionInsight",
        "SessionDecision",
        "SessionQuestion",
        "SessionTODO",
        "SessionHypothesis",
        "SessionTakeaway",
    }
    _KIND_TO_TYPE = {
        "insight": "SessionInsight",
        "decision": "SessionDecision",
        "question": "SessionQuestion",
        "todo": "SessionTODO",
        "hypothesis": "SessionHypothesis",
        "takeaway": "SessionTakeaway",
    }

    def _mcp_list_sessions(
        self,
        graph: ResearchGraph,
        *,
        since: Optional[str] = None,
        limit: int = 20,
    ) -> JSONDict:
        """Return Session envelopes for the resolved graph."""
        sessions = [n for n in graph.nodes if n.type.value == "Session"]
        if since:
            sessions = [
                s for s in sessions
                if str((s.metadata or {}).get("started_at") or "") >= since
            ]
        # Newest-first.
        sessions.sort(
            key=lambda n: str((n.metadata or {}).get("started_at") or ""),
            reverse=True,
        )

        # Pre-compute finding counts per session_id so each envelope can
        # advertise how many findings of each kind it produced.
        counts_by_session: Dict[str, Dict[str, int]] = {}
        for node in graph.nodes:
            if node.type.value not in self._SESSION_FINDING_TYPES:
                continue
            sid = str((node.metadata or {}).get("session_id") or "")
            if not sid:
                continue
            bucket = counts_by_session.setdefault(sid, {})
            bucket[node.type.value] = bucket.get(node.type.value, 0) + 1

        items: List[JSONDict] = []
        for session in sessions[: max(1, int(limit))]:
            meta = session.metadata or {}
            sid = str(meta.get("session_id") or "")
            items.append(
                {
                    "node_id": session.id,
                    "session_id": sid,
                    "started_at": meta.get("started_at"),
                    "ended_at": meta.get("ended_at"),
                    "title": meta.get("title") or session.name,
                    "harness": meta.get("harness"),
                    "model": meta.get("model"),
                    "files_touched_count": len(meta.get("files_touched") or []),
                    "finding_counts": counts_by_session.get(sid, {}),
                }
            )
        return {"sessions": items, "total": len(sessions)}

    def _mcp_find_session_findings(
        self,
        graph: ResearchGraph,
        *,
        node_id: str,
        kinds: Optional[List[str]] = None,
    ) -> JSONDict:
        """Return findings connected to ``node_id`` via discussed_in/references."""
        kind_filter: Optional[set] = None
        if kinds:
            kind_filter = {
                self._KIND_TO_TYPE[k]
                for k in kinds
                if k in self._KIND_TO_TYPE
            }

        # Walk edges to find the Session(s) the node was discussed in AND
        # the findings that directly reference the node.
        session_ids: set = set()
        direct_finding_ids: set = set()
        for edge in graph.edges:
            if edge.type == "discussed_in" and edge.source == node_id:
                session_ids.add(edge.target)
            if edge.type == "references" and edge.target == node_id:
                direct_finding_ids.add(edge.source)

        nodes_by_id = {n.id: n for n in graph.nodes}

        # Findings = direct references PLUS every finding derived from a
        # session that discussed the node (broader recall).
        finding_ids: set = set(direct_finding_ids)
        for edge in graph.edges:
            if edge.type != "derived_from_session":
                continue
            if edge.target in session_ids:
                finding_ids.add(edge.source)

        out: List[JSONDict] = []
        for fid in finding_ids:
            node = nodes_by_id.get(fid)
            if node is None:
                continue
            type_name = node.type.value
            if type_name not in self._SESSION_FINDING_TYPES:
                continue
            if kind_filter is not None and type_name not in kind_filter:
                continue
            meta = node.metadata or {}
            out.append(
                {
                    "node_id": node.id,
                    "kind": type_name,
                    "body": node.name,
                    "session_id": meta.get("session_id"),
                    "turn_ids": meta.get("turn_ids") or [],
                    "extractor": meta.get("extractor"),
                    "directly_references_node": fid in direct_finding_ids,
                }
            )
        # Deterministic ordering: by kind then body.
        out.sort(key=lambda d: (d["kind"], d["body"]))
        return {"node_id": node_id, "findings": out, "total": len(out)}

    def _mcp_list_communities(
        self, graph: ResearchGraph, *, min_size: int = 3, limit: int = 20,
    ) -> JSONDict:
        """Return COMMUNITY_SUMMARY nodes ranked by member count."""
        items: List[JSONDict] = []
        for node in graph.nodes:
            if node.type.value != "CommunitySummary":
                continue
            meta = node.metadata or {}
            member_ids = list(meta.get("member_ids") or [])
            count = int(meta.get("member_count") or len(member_ids))
            if count < max(2, int(min_size)):
                continue
            items.append({
                "community_id": node.id,
                "title": node.name,
                "description": node.description,
                "tags": list(meta.get("tags") or []),
                "member_count": count,
                "member_ids": member_ids,
            })
        items.sort(key=lambda d: (-int(d["member_count"]), d["community_id"]))
        items = items[: max(1, int(limit))]
        return {"communities": items, "total": len(items)}

    def _mcp_fresh_insights(
        self,
        graph: ResearchGraph,
        *,
        limit: int = 10,
        kind: Optional[str] = None,
    ) -> JSONDict:
        """Top session findings by decay_score, excluding superseded ones.

        ``kind`` is one of the short lowercase aliases used by other
        session-graph tools (``insight``, ``decision``, ...). The Node
        type filter normalises that to the matching ``Session<Kind>``
        enum value.
        """
        from datetime import datetime, timezone

        from .memory.decay import compute_decay_score

        kind_filter: Optional[str] = None
        if kind:
            kind_filter = self._KIND_TO_TYPE.get(kind.lower())
            if kind_filter is None:
                raise ValueError(f"fresh_insights: unknown kind {kind!r}")

        # Any node with an OUTGOING `supersedes` edge is the WINNER —
        # keep it. Filter out the LOSER: any node that is the target of
        # such an edge (i.e. has been superseded). This matches the
        # canonical orientation chosen by tesserae.memory.supersede.
        superseded_ids: set = {
            edge.target for edge in graph.edges if edge.type == "supersedes"
        }

        now = datetime.now(timezone.utc)
        scored: List[Tuple[float, ResearchNode]] = []
        for node in graph.nodes:
            type_name = node.type.value
            if type_name not in self._SESSION_FINDING_TYPES:
                continue
            if kind_filter is not None and type_name != kind_filter:
                continue
            if node.id in superseded_ids:
                continue
            score = compute_decay_score(node, now)
            scored.append((score, node))

        # Highest score first; ties broken by node id for determinism.
        scored.sort(key=lambda t: (-t[0], t[1].id))

        capped = max(1, min(int(limit), 200))
        out: List[JSONDict] = []
        for score, node in scored[:capped]:
            meta = node.metadata or {}
            out.append(
                {
                    "node_id": node.id,
                    "kind": node.type.value,
                    "body": node.name,
                    "session_id": meta.get("session_id"),
                    "first_seen_at": meta.get("first_seen_at"),
                    "last_accessed_at": meta.get("last_accessed_at"),
                    "access_count": meta.get("access_count") or 0,
                    "decay_score": round(score, 4),
                }
            )
        return {"findings": out, "total": len(scored)}

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
        mode: str = "hybrid",
        weights: Optional[Dict[str, float]] = None,
    ) -> JSONDict:
        """Search public ResearchGraph nodes.

        ``mode`` selects the retrieval strategy:

        * ``hybrid`` (default) — BM25 + lexical + embedding fused via RRF.
        * ``bm25`` / ``lexical`` / ``embedding`` — single-lane variants.
        * ``legacy`` — original casefolded substring matcher; preserved
          bit-for-bit so older callers and tests keep working.

        The return shape (``query``, ``total_matches``, ``nodes``) is
        unchanged; ``mode`` is appended so clients can confirm what ran.
        """
        type_filter = {str(item) for item in types or []}
        kind_filter = {str(item).lower() for item in kinds or []}
        public_nodes = [n for n in graph.nodes if not is_code_graph_node(n)]
        candidates: List[ResearchNode] = []
        for node in public_nodes:
            if type_filter and node.type.value not in type_filter:
                continue
            if kind_filter:
                node_kind = kind_for_node(node)
                if node_kind is None or node_kind not in kind_filter:
                    continue
            candidates.append(node)

        bounded_limit = max(1, min(limit, 100))

        if mode == "legacy":
            terms = [term.casefold() for term in query.split() if term.strip()]
            scored: List[Tuple[int, int, ResearchNode]] = []
            for index, node in enumerate(candidates):
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
            matches = [
                node_to_dict(node)
                for score, _index, node in scored
                if score > 0 or not terms
            ]
            return {
                "query": query,
                "mode": "legacy",
                "total_matches": len(matches),
                "nodes": matches[:bounded_limit],
            }

        result = _hybrid_search(
            graph,
            query=query,
            top_k=bounded_limit,
            weights=weights,
            mode=mode,
            candidate_filter=candidates,
        )
        nodes_out: List[JSONDict] = []
        for item in result.scored:
            payload = node_to_dict(item.node)
            payload["_retrieval"] = {
                "score": item.score,
                "per_lane": item.per_lane,
                "ranks": item.ranks,
            }
            nodes_out.append(payload)
        return {
            "query": query,
            "mode": result.mode,
            "backend": result.backend,
            "weights": result.weights,
            # Use the pre-slice candidate count surfaced by the retriever so
            # paged queries (limit < total) still report the true match count
            # rather than just the page size — matches the legacy contract.
            "total_matches": int(result.total_matches),
            "nodes": nodes_out,
        }

    def embedding_status(self) -> JSONDict:
        """Report the active embedding backend used by hybrid search."""
        try:
            backend = _active_embedding_backend()
            return {
                "available": True,
                "backend": backend.name,
                "dim": int(getattr(backend, "dim", 0)),
                "default_weights": dict(_HYBRID_DEFAULT_WEIGHTS),
                "modes": ["hybrid", "bm25", "lexical", "embedding", "legacy"],
            }
        except Exception as exc:  # pragma: no cover - defensive
            return {
                "available": False,
                "backend": None,
                "error": str(exc),
                "default_weights": dict(_HYBRID_DEFAULT_WEIGHTS),
                "modes": ["hybrid", "bm25", "lexical", "embedding", "legacy"],
            }

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
        wiki_root = project_root / ".tesserae" / "wiki"
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
        report_path = project_root / ".tesserae" / "lint-report.md"
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
        path under a ``.tesserae`` layout), or fall back to the active
        registry entry. Raises a clear error if none of those resolve.
        """
        raw_path = args.get("graph_path")
        if raw_path:
            root = _project_root_for_graph_path(str(raw_path))
            if root is None:
                raise ValueError(f"ask: graph_path is not under a .tesserae layout: {raw_path}")
            return root
        project = args.get("project")
        if project:
            entry_path = self.registry.resolve_graph_path(str(project))
            if entry_path is None:
                raise ValueError(f"ask: unknown project {project!r}. Use list_projects or register_project.")
            root = _project_root_for_graph_path(entry_path)
            if root is None:
                raise ValueError(f"ask: registered project {project!r} has no .tesserae layout")
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
            "or start the MCP server with --graph pointing at a .tesserae layout."
        )

    def _mcp_ask(self, args: JSONDict, *, question: str, backend: str, top_k: int) -> JSONDict:
        """Dispatch ``ask`` to raganything, cognee, or compiled-wiki search.

        Thin adapter around :func:`tesserae.query.ask_project` so the MCP
        ``ask`` tool, the ``tesserae project ask`` CLI handler, and the new
        top-level ``tesserae ask`` command share one dispatcher.
        """
        from .project import ProjectWiki
        from .query import ask_project

        project_root = self._resolve_project_root_for_ask(args)
        wiki = ProjectWiki.load(project_root)
        return ask_project(wiki, question, backend=backend, top_k=top_k)

    def _mcp_ask_all_registered(
        self,
        *,
        question: str,
        backend: str,
        top_k: int,
        scope_aliases: List[str],
    ) -> JSONDict:
        """B2 — fan ``ask`` out across every registered project.

        Aggregates the per-project envelopes under
        ``{"scope": "all-registered", "by_project": {...}}``. Mirrors
        the CLI handler exactly so MCP clients and the CLI return the
        same shape. Failures in one project are captured as
        ``{"error": "..."}`` entries; the aggregate call never raises
        on a single project's failure.
        """
        from .project import ProjectWiki
        from .query import ask_project

        data = self.registry.list_projects()
        projects = list(data.get("projects") or [])
        wanted = {a for a in scope_aliases if a}
        if wanted:
            projects = [p for p in projects if p.get("name") in wanted]
            missing = wanted - {p.get("name") for p in projects}
            if missing:
                raise ValueError(
                    f"ask: unknown scope alias(es): {sorted(missing)}. "
                    f"Use list_projects to see registered projects."
                )
        if not projects:
            raise ValueError(
                "ask: scope='all-registered' but the registry is empty. "
                "Use register_project to add a project first."
            )
        by_project: Dict[str, JSONDict] = {}
        for entry in projects:
            name = str(entry.get("name") or "")
            root_str = entry.get("root")
            if not root_str:
                gp = Path(str(entry.get("graph_path") or "")).resolve()
                project_root = gp.parent.parent if gp.parent.name == ".tesserae" else gp.parent
            else:
                project_root = Path(str(root_str)).resolve()
            try:
                wiki = ProjectWiki.load(project_root)
                by_project[name] = ask_project(wiki, question, backend=backend, top_k=top_k)
            except Exception as exc:
                by_project[name] = {"error": f"ask failed: {exc}"}
        return {
            "scope": "all-registered",
            "question": question,
            "by_project": by_project,
        }

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

        ``project_root`` is the directory containing ``.tesserae/`` for the
        active source. Returns ``None`` for stores that have no on-disk root
        (e.g. an in-memory ``GraphStore``), which makes filesystem-backed
        tools (``wiki_page``/``raw_source``/``lint_report``) raise a clear
        error instead of misreading paths.
        """
        raw_path = args.get("graph_path")
        if raw_path:
            graph_path = Path(str(raw_path))
            if not graph_path.is_file():
                raise ValueError(
                    f"graph_path does not exist or is not a file: {graph_path}. "
                    f"Compile the project first (`tesserae project compile`) or "
                    f"point at a different .tesserae/graph.json."
                )
            return self._load_graph_cached(graph_path), _project_root_for_graph_path(graph_path)
        project = args.get("project")
        if project:
            resolved = self.registry.resolve_graph_path(str(project))
            if resolved is None:
                raise ValueError(f"Unknown project: {project}. Use list_projects or register_project.")
            resolved_path = Path(resolved)
            if not resolved_path.is_file():
                raise ValueError(
                    f"Registered project {project!r} points at a missing graph file: "
                    f"{resolved}. Recompile the project or unregister and re-register it."
                )
            return self._load_graph_cached(resolved_path), _project_root_for_graph_path(resolved_path)
        active = self.registry.active_graph_path()
        if active is not None:
            active_path = Path(active)
            if not active_path.is_file():
                raise ValueError(
                    f"Active project's graph file is missing: {active}. "
                    f"Recompile, activate a different project, or unregister the stale entry."
                )
            return self._load_graph_cached(active_path), _project_root_for_graph_path(active_path)
        if self.graph_store is not None:
            return _materialize_graph(self.graph_store), None
        if self.default_graph_path:
            return self._load_graph_cached(self.default_graph_path), _project_root_for_graph_path(self.default_graph_path)
        raise ValueError(
            "No graph specified. Pass graph_path, project, activate a project, "
            "start the MCP server with --graph, or pass --graph-store-url."
        )

    def _load_graph_cached(self, graph_path: Path) -> ResearchGraph:
        """Load graph.json, returning a cached copy when mtime is unchanged."""
        mtime = graph_path.stat().st_mtime
        cached = self._graph_cache.get(graph_path)
        if cached and cached[0] == mtime:
            return cached[1]
        graph = load_graph(graph_path)
        self._graph_cache[graph_path] = (mtime, graph)
        return graph

    def _find_node(self, graph: ResearchGraph, node_id: Optional[str], node_name: Optional[str]) -> Optional[ResearchNode]:
        id_index = {n.id: n for n in graph.nodes}
        name_index = {n.name.casefold(): n for n in graph.nodes}
        if node_id:
            return id_index.get(node_id)
        if node_name:
            return name_index.get(str(node_name).casefold())
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
                        "capabilities": {
                            "tools": {"listChanged": False},
                            "resources": {"listChanged": False, "subscribe": False},
                            "prompts": {"listChanged": False},
                        },
                        "serverInfo": {"name": "tesserae", "version": "0.1.0"},
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
            if method == "resources/list":
                return self._result(request_id, {"resources": self.server.list_resources()})
            if method == "resources/templates/list":
                return self._result(request_id, {"resourceTemplates": self.server.list_resource_templates()})
            if method == "resources/read":
                params = message.get("params") or {}
                uri = params.get("uri")
                if not uri:
                    return self._error(request_id, -32602, "resources/read requires 'uri'")
                return self._result(request_id, self.server.read_resource(str(uri)))
            if method == "prompts/list":
                return self._result(request_id, {"prompts": self.server.list_prompts()})
            if method == "prompts/get":
                params = message.get("params") or {}
                prompt_name = params.get("name")
                arguments = params.get("arguments") or {}
                if not prompt_name:
                    return self._error(request_id, -32602, "prompts/get requires 'name'")
                return self._result(request_id, self.server.get_prompt(str(prompt_name), arguments))
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

    Lazy-imports the HypePaper backend so the Tesserae package keeps
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
    parser = argparse.ArgumentParser(description="Run the Tesserae ResearchGraph MCP stdio server.")
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
