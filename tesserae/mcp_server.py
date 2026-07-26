"""Minimal stdio MCP server for Tesserae research graphs.

This module intentionally avoids a hard dependency on the Python MCP SDK so the
repository can expose a useful MCP interface in the user's current no-extra-setup
local environment. It implements the JSON-RPC methods Hermes and other MCP
clients need for initialization, tool discovery, and tool calls.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .activity_summary import build_summary, resolve_windows
from .cli_tree import package_version as _package_version
from .context_compiler import (
    _truncate_to_budget as _truncate_text,
    fit_to_budget,
)
from .ports import GraphStore
from .retrieval.hybrid import (
    DEFAULT_WEIGHTS as _HYBRID_DEFAULT_WEIGHTS,
    ScoredNode as _HybridScoredNode,
    active_embedding_backend as _active_embedding_backend,
    backend_is_semantic as _backend_is_semantic,
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
from .verify import verify_claim
from .wiki_projector import is_code_graph_node, kind_for_node
from .wiki_store import WikiPageStore


JSONDict = Dict[str, Any]

_LOG = logging.getLogger(__name__)


# Cap raw payload sizes returned to MCP clients so a malicious / huge file
# can't blow up the agent's context window.
RAW_SOURCE_BYTE_CAP = 16 * 1024
LINT_REPORT_BYTE_CAP = 64 * 1024
DOCTOR_REPORT_BYTE_CAP = LINT_REPORT_BYTE_CAP
WIKI_BODY_BYTE_CAP = 64 * 1024


import hashlib
from collections import OrderedDict


class _HandleStore:
    """In-process content-keyed store for large tool payloads (read-discipline).

    A tool can stash a big body and return a short handle + preview; the agent
    pulls the rest in slices via ``get_handle`` instead of holding it all in
    context. Content-keyed ids sidestep invalidation: a recompiled/changed body
    yields a NEW handle, and an old handle keeps returning its own snapshot. LRU
    cap bounds memory. ponytail: a dict with a cap — no lifetimes, no GC thread.
    """

    def __init__(self, capacity: int = 64) -> None:
        self._cap = capacity
        self._items: "OrderedDict[str, str]" = OrderedDict()

    # Hard ceiling on a single slice — the whole point is read-discipline, so
    # get_handle must never be coaxed into dumping an arbitrarily large chunk
    # back into context (codex review).
    MAX_SLICE = 50_000

    def put(self, text: str) -> str:
        # Full SHA-256 hex: content-keyed dedup with a negligible collision
        # surface (a 64-bit prefix is needless attack surface — codex review).
        hid = "h_" + hashlib.sha256(text.encode("utf-8")).hexdigest()
        self._items[hid] = text
        self._items.move_to_end(hid)
        while len(self._items) > self._cap:
            self._items.popitem(last=False)
        return hid

    def slice(self, hid: str, offset: int, limit: int) -> Optional[dict]:
        text = self._items.get(hid)
        if text is None:
            return None
        self._items.move_to_end(hid)
        offset = max(0, min(offset, len(text)))
        limit = max(1, min(int(limit), self.MAX_SLICE))
        chunk = text[offset:offset + limit]
        end = offset + len(chunk)
        return {"handle": hid, "found": True, "offset": offset, "limit": limit,
                "total_chars": len(text), "slice": chunk, "eof": end >= len(text)}


_HANDLES = _HandleStore()


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


def _hit_node_ids(payload: Any) -> List[str]:
    """Pull the ``node_id`` off each hit in an ask/query envelope.

    Both ``ask_project`` and the wiki ``query`` path return an envelope with a
    ``hits`` list whose items carry a (possibly ``None``) ``node_id``. Missing
    ``hits`` (e.g. a not-enabled/no-answer envelope) yields an empty list, so
    the LRU bump degrades to a no-op rather than raising.
    """
    if not isinstance(payload, dict):
        return []
    hits = payload.get("hits")
    if not isinstance(hits, list):
        return []
    return [
        str(hit["node_id"])
        for hit in hits
        if isinstance(hit, dict) and hit.get("node_id")
    ]


def edge_to_dict(edge: ResearchEdge) -> JSONDict:
    return edge.model_dump()


# ------------------------------------------------------------------ CTX-01 clamps

#: Default per-response character budget (CTX-01, §5.3) — matches
#: ``compile_context``'s default ``budget`` so both bounds behave identically
#: (budget currency is chars everywhere; explicit 0 = uncapped).
DEFAULT_BUDGET_CHARS = 32_000

#: Deterministic stand-in for a metadata block too big for the per-entry cap
#: (mirrors the list_communities member_ids -> count elision precedent).
_ELIDED_METADATA = {
    "_elided": "metadata over the per-entry budget cap; refetch with budget_chars=0"
}


def _measure_item(item: JSONDict) -> int:
    """Serialized size of a payload item in chars — the unit CTX-01 admits in."""
    return len(json.dumps(item, ensure_ascii=False, default=str))


def _clamp_payload_item(item: JSONDict, cap: int, text_field: str) -> JSONDict:
    """Clamp one ``model_dump`` payload to the CTX-01 per-entry cap.

    Truncates the dominant ``text_field`` first (word-boundary cut, visible
    marker), then elides an oversized ``metadata`` block, re-measuring after
    each step — JSON escaping can shrink less than the raw cut, so the trim
    iterates (strictly decreasing, always terminates). Returns the item
    untouched when it already fits or ``cap <= 0`` (uncapped). When even the
    remaining structural fields exceed ``cap`` the item is returned best-effort
    — admission still measures its actual size. Deterministic, never raises.
    """
    if cap <= 0 or _measure_item(item) <= cap:
        return item
    clamped = dict(item)
    while True:
        size = _measure_item(clamped)
        if size <= cap:
            return clamped
        text = str(clamped.get(text_field) or "")
        if text:
            clamped[text_field] = _truncate_text(
                text, max(0, len(text) - (size - cap))
            )
            continue
        meta = clamped.get("metadata")
        if isinstance(meta, (dict, list)) and meta and meta != _ELIDED_METADATA:
            clamped["metadata"] = dict(_ELIDED_METADATA)
            continue
        return clamped


def _fit_payload_list(
    items: Sequence[JSONDict],
    budget_chars: int,
    text_field: str = "description",
) -> Tuple[List[JSONDict], Optional[str]]:
    """Apply CTX-01 to a list of ``model_dump`` payloads.

    Each item is clamped to the per-entry cap (``budget_chars // 8``), then
    whole items are greedily admitted in input order via
    :func:`tesserae.context_compiler.fit_to_budget` over their serialized
    forms. Returns ``(kept_items, continuation)`` where ``continuation`` is the
    single ``+N more, cursor=K`` line iff items were dropped.
    ``budget_chars <= 0`` is the uncapped passthrough.
    """
    items = list(items)
    if budget_chars <= 0 or not items:
        return items, None
    cap = budget_chars // 8
    clamped = [_clamp_payload_item(item, cap, text_field) for item in items]
    fit = fit_to_budget(
        [json.dumps(item, ensure_ascii=False, default=str) for item in clamped],
        budget_chars,
    )
    return clamped[: len(fit.entries)], fit.continuation


def _paginate_cards(
    header: JSONDict, cards: List[JSONDict], budget_chars: int, cursor: int
) -> JSONDict:
    """Shared ``graph_map`` response tail: absolute-cursor pagination + CTX-01.

    ``cursor`` is a resume offset over the full deterministic card ordering;
    the continuation line rewrites ``fit_to_budget``'s page-relative drop count
    as the absolute next cursor, so every scope kind (community, org, agent)
    paginates identically.
    """
    cursor = min(cursor, len(cards))
    header["total_cards"] = len(cards)
    header["cursor"] = cursor
    remaining = cards[cursor:]
    kept, continuation = _fit_payload_list(remaining, budget_chars, text_field="summary")
    result: JSONDict = {"header": header, "cards": kept}
    if continuation:
        dropped = len(remaining) - len(kept)
        result["continuation"] = f"+{dropped} more, cursor={cursor + len(kept)}"
    return result


def _budget_chars_arg(args: Mapping[str, Any]) -> int:
    """Parse the ``budget_chars`` tool argument, preserving an explicit 0.

    An explicit ``budget_chars=0`` means uncapped (compile_context's
    ``budget=0`` invariant) — so no ``or``-coercion; only default when the
    argument is absent/None.
    """
    raw = args.get("budget_chars")
    return DEFAULT_BUDGET_CHARS if raw is None else int(raw)


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
            return {"version": 1, "projects": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Corrupt registry at {self.path}: {exc}") from exc
        data.setdefault("version", 1)
        data.pop("active", None)  # active-project concept removed; ignore legacy key
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

    def unregister(self, name: str) -> JSONDict:
        data = self.load()
        if name not in data["projects"]:
            raise ValueError(f"Unknown project: {name}")
        del data["projects"][name]
        self.save(data)
        return {"removed": name}

    def list_projects(self) -> JSONDict:
        data = self.load()
        return {
            "projects": [
                {"name": name, **entry}
                for name, entry in sorted(data["projects"].items())
            ],
        }

    def resolve_graph_path(self, name: str) -> Optional[Path]:
        data = self.load()
        entry = data["projects"].get(name)
        return Path(entry["graph_path"]) if entry else None

    def all_project_names(self) -> List[str]:
        """Every registered alias (sorted). The basis for the federated default —
        with no active project, queries span all of these."""
        return [name for name, _root in self.iter_registered_projects()]

    def resolve_project_by_cwd(self, start: Optional[Path] = None) -> Optional[Path]:
        """Nearest ancestor of ``start`` (default cwd) that is a registered project
        root — the replacement for the active-project fallback for per-project ops
        (compile/ingest/status): you operate on the project you're standing in.
        Returns the project ROOT (deepest match wins for nested projects), or None."""
        try:
            here = (start or Path.cwd()).resolve()
        except OSError:
            return None
        matches = [
            root.resolve() for _name, root in self.iter_registered_projects()
            if root.resolve() == here or root.resolve() in here.parents
        ]
        if not matches:
            return None
        return max(matches, key=lambda r: len(r.parts))  # deepest = most specific

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


# Suppression set shared by every read path (search_nodes, fresh_insights,
# node_context, compile_context): supersedes targets + resolved_by sources.
# Lives in graph_filters so the context compiler suppresses the same losers.
from .graph_filters import superseded_ids as _superseded_ids


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


# ------------------------------------------------------- federated scopes (§6.3)

@dataclasses.dataclass(frozen=True)
class _FederatedChild:
    """One sibling project's read-only Descent inputs (§6.3 PR10).

    Everything ``graph_map`` needs to serve ``<alias>::`` scopes without
    touching the local graph: the parsed sibling graph + hierarchy, the
    derived id index and undirected degrees (so warm calls are pure dictionary
    work), and the sha256 content digests of its ``graph.json`` /
    ``hierarchy.json`` bytes — the GRAPH_REF identity that digest verification
    checks card staleness against.
    """

    root: Path
    graph: ResearchGraph
    hierarchy: "Hierarchy"  # noqa: F821 — tesserae.hierarchy, imported lazily
    by_id: Dict[str, ResearchNode]
    degrees: Dict[str, int]
    digests: Dict[str, str]


#: Mtime-keyed LRU over sibling projects' parsed graph + hierarchy (§6.3 PR10).
#: Key = resolved project root; signature = (mtime_ns, size) of graph.json and
#: hierarchy.json, so a sibling recompile is picked up on the very next call
#: while repeat descents parse nothing. 8 entries, mirroring
#: ``agent_view._VIEW_CACHE`` and the server's own mtime-cached graph loads.
#: READ-ONLY throughout: nothing in the federated path ever writes to the
#: sibling project's ``.tesserae/``.
_FED_CHILD_CACHE: "OrderedDict[str, Tuple[tuple, _FederatedChild]]" = OrderedDict()
_FED_CHILD_CACHE_MAX = 8


def _fed_path_signature(path: Path) -> Optional[Tuple[int, int]]:
    try:
        stat = path.stat()
        return (stat.st_mtime_ns, stat.st_size)
    except OSError:
        return None


def _load_federated_child(alias: str, graph_path: Path) -> _FederatedChild:
    """Load a sibling project READ-ONLY through the LRU — one graph per call.

    Exactly ONE child graph parse on a cold call and zero on a warm one; the
    federated scope path never loads the local graph. Deliberately skips the
    discovered-links overlay and lazy summary materialization: both are the
    LOCAL project's read-time enrichments, and the sibling is served verbatim
    from its own compiled bytes — the same bytes digest verification hashes.
    Fail-loud (missing graph / missing sidecar) with the recompile remedy.
    """
    root = _project_root_for_graph_path(graph_path)
    if root is None:
        raise ValueError(
            f"graph_map: registered project {alias!r} graph is not in the "
            f"canonical <root>/.tesserae/graph.json layout: {graph_path}. "
            f"Re-register the project root with register_project."
        )
    hierarchy_path = root / ".tesserae" / "hierarchy.json"
    signature = (_fed_path_signature(graph_path), _fed_path_signature(hierarchy_path))
    key = str(root)
    cached = _FED_CHILD_CACHE.get(key)
    if cached and cached[0] == signature:
        _FED_CHILD_CACHE.move_to_end(key)
        return cached[1]

    from .hierarchy import load_hierarchy, undirected_degrees

    if not graph_path.is_file():
        raise ValueError(
            f"Registered project {alias!r} points at a missing graph file: "
            f"{graph_path}. Recompile the project or unregister and "
            f"re-register it."
        )
    hierarchy = load_hierarchy(root)  # fail-loud with the compile remedy
    digests = {
        "graph.json": hashlib.sha256(graph_path.read_bytes()).hexdigest(),
        "hierarchy.json": hashlib.sha256(hierarchy_path.read_bytes()).hexdigest(),
    }
    graph = load_graph(graph_path)
    child = _FederatedChild(
        root=root,
        graph=graph,
        hierarchy=hierarchy,
        by_id={n.id: n for n in graph.nodes},
        degrees=undirected_degrees(graph),
        digests=digests,
    )
    _FED_CHILD_CACHE[key] = (signature, child)
    _FED_CHILD_CACHE.move_to_end(key)
    while len(_FED_CHILD_CACHE) > _FED_CHILD_CACHE_MAX:
        _FED_CHILD_CACHE.popitem(last=False)
    return child


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
        # Per-alias content digests recorded when an ``<alias>::`` root card
        # set was built (§6.3) — the reference digest verification checks
        # descent calls against. In-memory by design: staleness is a property
        # of the map THIS server handed out, not a persisted artifact.
        self._fed_digests: Dict[str, Dict[str, str]] = {}

    def list_tools(self) -> List[JSONDict]:
        graph_path_prop = {"type": "string", "description": "Path to a ResearchGraph JSON file. Defaults to the project you are in (cwd), then server --graph."}
        project_prop = {"type": "string", "description": "Registered project name (see list_projects). Overridden by graph_path."}
        agent_prop = {"type": "string", "description": "Agent-scoped view: a worker key (own raw + distilled memory), a manager key (federated reports' distillates), or 'org' (all distilled artifacts). Requires a project root; see agents list / tesserae distill."}
        budget_chars_prop = {"type": "integer", "minimum": 0, "default": DEFAULT_BUDGET_CHARS, "description": "CTX-01 response budget in characters: each returned item is clamped to budget_chars/8 and overflow items are dropped behind one '+N more, cursor=K' continuation line. 0 = uncapped."}
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
                    "properties": {"graph_path": graph_path_prop, "project": project_prop, "agent": agent_prop},
                    "additionalProperties": False,
                },
            },
            {
                "name": "graph_map",
                "description": (
                    "Budgeted map of the knowledge-graph hierarchy — the Descent "
                    "entry point (start here to orient). Scope grammar: call with "
                    "NO scope for the root card set (graph counts, top-hub names, "
                    "and one card per coarsest community, largest first); pass "
                    "scope='<a card's scope_id>' to descend one dendrogram level "
                    "(cards for its sub-communities; at the finest level, its "
                    "member nodes); pass a card's parent_scope to ascend (null "
                    "parent_scope = the root, i.e. call with no scope). Every "
                    "card has the uniform shape {scope_id, kind: community|node, "
                    "title, summary, size, children_count, leaf_member_count, "
                    "parent_scope, tags, quality: llm|structural, stale} — use "
                    "children_count/leaf_member_count to gauge branch mass "
                    "before descending. Graph-derived cards (kind=community|"
                    "node) add live_member_count: how many members the CURRENT "
                    "graph still carries. 0 means the scope is dead (a "
                    "sidecar/graph skew) — skip it rather than descend. "
                    "Registry-derived cards (kind=agent|note) have no such "
                    "skew and omit the field, so read it with a default. "
                    "Responses are budget-packed (CTX-01): "
                    "when cards are dropped, the single continuation line "
                    "'+N more, cursor=K' means re-call with cursor=K for the "
                    "next page. Typical loop: graph_map() -> pick a card by "
                    "tags/size -> graph_map(scope_id) -> repeat -> "
                    "compile_context/node_context on the leaf node ids. "
                    "Agent-org scopes: scope='org:root' maps the agent "
                    "registry tree (kind=agent cards, children_count = direct "
                    "reports; descend with scope='agent:<key>'); "
                    "scope='agent:<key>' lists that agent's distilled L1 "
                    "index as kind=note cards — distillate-only (sealed L0), "
                    "each carrying a drill block whose agent + member_refs "
                    "feed the drill_down tool to escalate one member to raw "
                    "L0. Federated scopes: scope='<alias>::' maps a sibling "
                    "registered project's root card set (alias = a name from "
                    "list_projects; its graph.json + hierarchy.json are loaded "
                    "READ-ONLY and their sha256 content digests ride on the "
                    "response header); scope='<alias>::<scope_id>' descends "
                    "that project's tree with alias::-namespaced cards. If the "
                    "sibling's bytes changed since its '<alias>::' card set "
                    "was built, descent cards come back stale:true with a "
                    "'stale — recompile' note — re-run graph_map('<alias>::') "
                    "to rebuild the map. Community scopes require the "
                    ".tesserae/hierarchy.json "
                    "sidecar written by `tesserae compile`; agent scopes need "
                    "only the agent registry and distilled artifacts."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "graph_path": graph_path_prop,
                        "project": project_prop, "agent": agent_prop,
                        "scope": {
                            "type": "string",
                            "description": (
                                "Scope to descend into (a card's scope_id from "
                                "a previous graph_map call): a community id, "
                                "'agent:<key>' for an agent's distilled index, "
                                "'org:root' for the agent org tree, or "
                                "'<alias>::' / '<alias>::<scope_id>' for a "
                                "sibling registered project's tree. Omit "
                                "for the root card set."
                            ),
                        },
                        "cursor": {
                            "type": "integer",
                            "minimum": 0,
                            "default": 0,
                            "description": (
                                "Resume offset from a previous '+N more, "
                                "cursor=K' continuation line. 0 starts from the "
                                "first card."
                            ),
                        },
                        "budget_chars": budget_chars_prop,
                    },
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
                        "project": project_prop, "agent": agent_prop,
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
                        "include_superseded": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "When true, include nodes superseded by a newer "
                                "near-duplicate (the loser of a `supersedes` edge). "
                                "Default false suppresses them."
                            ),
                        },
                        "budget_chars": budget_chars_prop,
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
                        "project": project_prop, "agent": agent_prop,
                        "node_id": {"type": "string", "description": "Exact node id to inspect."},
                        "name": {"type": "string", "description": "Exact case-insensitive node name if node_id is omitted."},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
                        "include_superseded": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "When true, include superseded neighbour nodes and "
                                "edges incident to them. Default false suppresses "
                                "both the superseded neighbours and their edges."
                            ),
                        },
                        "use_ppr": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "Rank neighbors via personalized PageRank seeded "
                                "by this node instead of a 1-hop walk."
                            ),
                        },
                        "budget_chars": budget_chars_prop,
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "verify_claim",
                "description": (
                    "Verify ONE triple against the graph — exact lookup, no LLM, "
                    "no fuzzy matching, no ranked results. This is not a search "
                    "tool: it returns {verdict, reason, triple, citation, "
                    "provenance} where verdict is SUPPORTED / CONTRADICTED / "
                    "NOT_FOUND. NOT_FOUND means the graph does not assert the "
                    "triple; it is NOT a refutation — read the `reason` slug "
                    "(subject_unresolved and ambiguous_object are resolution "
                    "failures, triple_absent is a genuine absence). Endpoints "
                    "resolve by exact node id, then exact case-folded name, then "
                    "exact case-folded alias, and nothing else; ambiguity is "
                    "refused rather than guessed. Chain search_nodes -> "
                    "verify_claim when you only have prose."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "graph_path": graph_path_prop,
                        "project": project_prop, "agent": agent_prop,
                        "subject": {
                            "type": "string",
                            "description": "Subject endpoint: node id, exact name, or exact alias.",
                        },
                        "predicate": {
                            "type": "string",
                            "description": (
                                "Edge type — must be a verbatim member of the "
                                "ontology (see the `schema` tool). Synonyms are "
                                "not resolved."
                            ),
                        },
                        "object": {
                            "type": "string",
                            "description": "Object endpoint: node id, exact name, or exact alias.",
                        },
                        "claim": {
                            "type": "string",
                            "description": (
                                "Natural-language convenience, ignored when "
                                "subject/predicate/object are supplied. Resolves "
                                "only when a verbatim scan finds exactly one "
                                "subject, one predicate token and one object; "
                                "otherwise NOT_FOUND/nl_not_resolvable."
                            ),
                        },
                        "reground": {
                            "type": "boolean",
                            "default": True,
                            "description": (
                                "Re-check the cited evidence span against the "
                                "source file on disk (evidence from outside the "
                                "graph). Only sets provenance.regrounded; never "
                                "changes the verdict. null means unknown."
                            ),
                        },
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
                        "project": project_prop, "agent": agent_prop,
                        "query": {"type": "string", "description": "Whitespace-separated fact search terms."},
                        "current_only": {"type": "boolean", "default": False},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 10},
                        "budget_chars": budget_chars_prop,
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
                        "project": project_prop, "agent": agent_prop,
                        "query": {"type": "string", "description": "Optional fact search terms."},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
                        "budget_chars": budget_chars_prop,
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
                        "project": project_prop, "agent": agent_prop,
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
                        "project": project_prop, "agent": agent_prop,
                        "source_path": {"type": "string", "description": "Project-relative source path (e.g. data/research/...)."},
                    },
                    "required": ["source_path"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "lint_report",
                "description": (
                    "Return the contents of .tesserae/lint-report.md for the resolved/given "
                    "project (capped at 64 KB). Empty if the report does not exist."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "graph_path": graph_path_prop,
                        "project": project_prop, "agent": agent_prop,
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "doctor_report",
                "description": (
                    "Return the contents of .tesserae/doctor-report.md for the resolved/given "
                    "project (capped at 64 KB). Empty if the report does not exist — run "
                    "`tesserae doctor` to (re)generate it."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "graph_path": graph_path_prop,
                        "project": project_prop, "agent": agent_prop,
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "doctor_run",
                "description": (
                    "Run the doctor health checks for the resolved/given project and return "
                    "the report as JSON (findings, exit_code 0/1/2). Always READ-ONLY: fixes "
                    "never run over MCP and no report artifact is written — use "
                    "`tesserae doctor --fix` on the CLI for repairs."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "graph_path": graph_path_prop,
                        "project": project_prop, "agent": agent_prop,
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "query",
                "description": (
                    "Raw retrieval, no LLM — mirrors `tesserae query`. backend='wiki' "
                    "(default) is deterministic BM25/semantic search over the compiled wiki "
                    "(ranked hits with excerpts); backend='raganything' queries the optional "
                    "multimodal RAG index when the project has it enabled. Use `ask` for a "
                    "synthesized, cited answer."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string", "description": "The search query text."},
                        "top_k": {"type": "integer", "default": 8, "minimum": 1, "maximum": 50},
                        "kind": {
                            "type": "string",
                            "description": "Optional wiki-kind filter (e.g. papers, concepts, repos, sources). wiki backend only.",
                        },
                        "backend": {
                            "type": "string",
                            "enum": ["wiki", "raganything"],
                            "default": "wiki",
                        },
                        "project": project_prop, "agent": agent_prop,
                        "graph_path": graph_path_prop,
                    },
                    "required": ["question"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "ask",
                "description": (
                    "Ask a natural-language question and get an LLM-planned, cited answer "
                    "over the compiled knowledge graph. Mirrors `tesserae ask` (llm defaults "
                    "true; pass llm=false for ranked search hits only). Supports cross-vault "
                    "fan-out via `scope`. For raw ranked hits or explicit raganything "
                    "retrieval use the `query` tool."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string", "description": "The natural-language question."},
                        "llm": {
                            "type": "boolean",
                            "default": True,
                            "description": "Synthesize an LLM-planned answer (default true, matching the CLI). false = ranked search hits only (beats TESSERAE_QUERY_LLM=1).",
                        },
                        "project": project_prop, "agent": agent_prop,
                        "graph_path": graph_path_prop,
                        "top_k": {"type": "integer", "description": "Maximum results/context items.", "default": 8, "minimum": 1, "maximum": 100},
                        "scope": {
                            "type": "string",
                            "enum": ["current", "all-registered", "federated"],
                            "description": "OMIT to let the smart router pick (federated fallback; reroutes across your consecutive questions). 'current' targets the cwd/--project project; 'all-registered' fans out, one answer per project (by_project); 'federated' merges projects into ONE graph for a single cross-referenced answer (defaults to ALL registered).",
                        },
                        "conversation_id": {
                            "type": "string",
                            "description": "Optional. Groups your consecutive omitted-scope questions so follow-ups reroute correctly without bleeding into another conversation on the same server.",
                        },
                        "scope_aliases": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Registered alias names. Optional filter for 'all-registered'; REQUIRED for 'federated' (the projects to federate).",
                        },
                        "semantic": {
                            "type": "boolean",
                            "default": True,
                            "description": "scope='federated' only: embedding-backed cross-project links so the answer bridges RELATED (not just identical) concepts. ON by default (opt-out); set false for identity-merge only. Degrades cleanly without a real embedding backend.",
                        },
                        "route": {
                            "type": "string",
                            "enum": ["auto", "lookup", "graph"],
                            "default": "auto",
                            "description": "How to answer, not where. Mirrors `tesserae ask --route`. 'auto' (default) routes by question shape. 'graph' FORCES the KG planner — use it when you know the question is temporal or multi-hop and the shape heuristic may not see it. 'lookup' pins the cheap BM25 wiki path. Ignored by scope='all-registered'/'federated'.",
                        },
                    },
                    "required": ["question"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "list_projects",
                "description": "List registered Tesserae projects (all active — no privileged project).",
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
                "name": "unregister_project",
                "description": "Remove a project from the registry.",
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
                    "List Session nodes for the resolved project. Returns the "
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
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 200,
                            "default": 50,
                            "description": "Maximum findings to return (default 50, clamped to 200).",
                        },
                    },
                    "required": ["node_id"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "find_code_symbol_mentions",
                "description": (
                    "Feature H — expand a session finding into the code "
                    "symbols (CodeFunction / CodeClass / CodeMethod) it "
                    "mentions. Reads `discusses` edges minted by the "
                    "opt-in insight_symbol_link post-compile pass when "
                    "available; otherwise falls back to a live scan of "
                    "the finding body against `.tesserae/code-graph.json` "
                    "(no edges are mutated). Useful for jumping from a "
                    "decision/insight straight to the symbols it was "
                    "about."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "node_id": {
                            "type": "string",
                            "description": (
                                "Exact id of a Session<Kind> finding node "
                                "(SessionInsight / SessionDecision / "
                                "SessionQuestion / SessionTODO / "
                                "SessionHypothesis / SessionTakeaway)."
                            ),
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
                        "project": project_prop, "agent": agent_prop,
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
                        "exclude_direct_neighbors": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "Drop the seeds and their 1-hop neighbours "
                                "(either edge direction) from the ranking "
                                "before capping to top_k, so only 2+ hop "
                                "'unexpected' connections are returned."
                            ),
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
            {
                "name": "compile_context",
                "description": (
                    "Compile a tailored, cited context doc for a query or seed "
                    "nodes. Deterministic unless synthesize=true."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "graph_path": graph_path_prop,
                        "project": project_prop, "agent": agent_prop,
                        "query": {
                            "type": "string",
                            "description": (
                                "Natural-language query to seed hybrid retrieval. "
                                "Optional if 'seeds' is provided."
                            ),
                        },
                        "seeds": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Explicit seed node ids to anchor the context.",
                        },
                        "depth": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 10,
                            "default": 2,
                            "description": "Neighborhood / ranking depth.",
                        },
                        "budget": {
                            "type": "integer",
                            "minimum": 0,
                            "default": 32000,
                            "description": (
                                "Character budget for the compiled body. "
                                "Use 0 for uncapped (no character limit)."
                            ),
                        },
                        "synthesize": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "When true, run an LLM synthesis pass over the "
                                "selected nodes. Default false is fully deterministic."
                            ),
                        },
                        "multi_pool": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "AgentRunbook multi-pool retrieval: decompose the "
                                "query into sub-queries and reserve budget slots for "
                                "the most relevant Runbook / Gotcha / Event "
                                "distilled-memory nodes. Default false = single-pool."
                            ),
                        },
                        "preview": {
                            "type": "integer",
                            "minimum": 0,
                            "default": 0,
                            "description": (
                                "Read-discipline (memex-style). When > 0, return only "
                                "the first N chars of the body plus a 'handle' id; fetch "
                                "the rest in slices with the 'get_handle' tool instead of "
                                "dumping the whole body into context. 0 = return full body."
                            ),
                        },
                        "include_superseded": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "When true, include superseded / arbitration-losing "
                                "nodes (losers of `supersedes` / `resolved_by` edges). "
                                "Default false suppresses them."
                            ),
                        },
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "get_handle",
                "description": (
                    "Fetch a slice of a large payload previously returned as a "
                    "'handle' (e.g. by compile_context with preview>0). Lets an agent "
                    "page through big results on demand instead of holding the whole "
                    "thing in context — a programmable read scratchpad."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "handle": {"type": "string", "description": "The handle id returned by a previous tool call."},
                        "offset": {"type": "integer", "minimum": 0, "default": 0, "description": "Start character offset."},
                        "limit": {"type": "integer", "minimum": 1, "default": 4000, "description": "Max characters to return in this slice."},
                    },
                    "required": ["handle"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "agent_view_explain",
                "description": (
                    "Explain an agent-scoped view without loading it into context: "
                    "resolution mode (worker/manager/org), member agents, each L1 "
                    "artifact's path, node count, and distilled_through staleness "
                    "watermark. The manager's 'which reports know this, and how "
                    "stale' audit surface."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "graph_path": graph_path_prop,
                        "project": project_prop,
                        "agent": {"type": "string", "description": "Agent key, manager key, or 'org'."},
                    },
                    "required": ["agent"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "drill_down",
                "description": (
                    "Resolve a distillate member_ref back to its raw L0 node — the "
                    "manager's explicit, audit-logged escalation past distilled "
                    "visibility. Returns the raw node with status: alive (hash "
                    "matches), changed (content moved on), absorbed (folded into a "
                    "distillate), or gone. Every call is logged to the sidecar."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "graph_path": graph_path_prop,
                        "project": project_prop,
                        "node_id": {"type": "string", "description": "member_refs[].node_id from a distilled note."},
                        "content_hash": {"type": "string", "description": "member_refs[].content_hash for staleness detection (optional)."},
                        "agent": {"type": "string", "description": "Owning agent key (optional; scopes the absorbed-status check to that agent's artifact)."},
                    },
                    "required": ["node_id"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "graph_write",
                "description": (
                    "Write typed nodes + edges into the project graph directly — "
                    "no markdown, no LLM extraction pass. The write is appended "
                    "to an append-only overlay and replayed as a compile producer, "
                    "so it SURVIVES recompilation instead of being erased by it. "
                    "Strict: an unknown node/edge type, an edge without evidence, "
                    "an edge endpoint not in this payload, or a provenance block "
                    "without an external anchor (url | file | commit | session_id) "
                    "is REFUSED — nothing is silently dropped. Node types owned by "
                    "compile producers (Session, CodeFile, CommunitySummary, "
                    "Agent, ...) are refused too. Writes are durable immediately "
                    "and appear in the graph on the next compile; pass "
                    "materialize=true to compile now and read them back. Check the "
                    "`existing` flag per node: false means you minted a NEW node "
                    "rather than attaching to the one you meant."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "graph_path": graph_path_prop,
                        "project": project_prop, "agent": agent_prop,
                        "nodes": {
                            "type": "array",
                            "description": "Nodes to assert.",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "type": {"type": "string", "description": "Member of the node-type ontology (see `schema`)."},
                                    "description": {"type": "string"},
                                    "aliases": {"type": "array", "items": {"type": "string"}},
                                    "metadata": {"type": "object"},
                                },
                                "required": ["name", "type"],
                            },
                        },
                        "edges": {
                            "type": "array",
                            "description": "Edges between nodes in this payload. `evidence` is required.",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "source": {"type": "string", "description": "A node `name` from this payload."},
                                    "target": {"type": "string", "description": "A node `name` from this payload."},
                                    "type": {"type": "string", "description": "Member of the edge-type ontology (see `schema`)."},
                                    "evidence": {"type": "string", "description": "Why this edge holds. Required, non-empty."},
                                },
                                "required": ["source", "target", "type", "evidence"],
                            },
                        },
                        "provenance": {
                            "type": "object",
                            "description": (
                                "Requires `agent` plus at least one external "
                                "anchor: url | file | commit | session_id."
                            ),
                        },
                        "materialize": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "Run compile(changed_only=True) so the write is "
                                "readable immediately. Costs seconds; the write "
                                "is already durable without it."
                            ),
                        },
                    },
                    "required": ["nodes", "agent", "provenance"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "ingest",
                "description": (
                    "Ingest raw web/text content (e.g. a browser clip) into "
                    "the resolved project's knowledge graph"
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "graph_path": graph_path_prop,
                        "project": project_prop, "agent": agent_prop,
                        "content": {
                            "type": "string",
                            "description": "Raw text/markdown content to ingest.",
                        },
                        "url": {
                            "type": "string",
                            "description": "Source URL the content was clipped from.",
                        },
                        "title": {
                            "type": "string",
                            "description": "Optional title for the ingested document.",
                        },
                        "note": {
                            "type": "string",
                            "description": "Optional user note to attach to the clip.",
                        },
                        "tags": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional tags for the ingested document.",
                        },
                        "tldr": {
                            "type": "boolean",
                            "default": True,
                            "description": (
                                "When true (default), best-effort generate a "
                                "TL;DR summary of the content."
                            ),
                        },
                    },
                    "required": ["content"],
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
                    "community_id to walk `summarizes` edges back to members. "
                    "Each entry carries member_count plus a member_ids_handle — "
                    "page the full member id list via get_handle instead of "
                    "receiving it inline."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "graph_path": graph_path_prop,
                        "project": project_prop, "agent": agent_prop,
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
                        "project": project_prop, "agent": agent_prop,
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
                        "include_superseded": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "When true, include findings superseded by a newer "
                                "near-duplicate. Default false suppresses them."
                            ),
                        },
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "tesserae_setup_plan",
                "description": (
                    "Detect the environment and propose a Tesserae setup plan as JSON. "
                    "Read-only: does not touch .tesserae/. Returns {plan, rendered_summary}. "
                    "Pass `overrides` to pre-set any SetupPlan field."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "project_root": {
                            "type": "string",
                            "description": "Project root path; defaults to '.'.",
                        },
                        "overrides": {
                            "type": "object",
                            "description": (
                                "Optional field overrides applied to build_plan() — any "
                                "SetupPlan field plus "
                                "include_raganything and install_* flags."
                            ),
                        },
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "tesserae_setup_apply",
                "description": (
                    "Apply a SetupPlan (from tesserae_setup_plan, possibly mutated): "
                    "writes .tesserae/config.json and runs gated install/run actions. "
                    "Pass confirm_install_actions=True to install dependencies, "
                    "confirm_run_actions=True to execute refresh commands."
                ),
                "inputSchema": {
                    "type": "object",
                    "required": ["plan"],
                    "properties": {
                        "plan": {
                            "type": "object",
                            "description": "SetupPlan JSON returned by tesserae_setup_plan.",
                        },
                        "confirm_install_actions": {"type": "boolean", "default": False},
                        "confirm_run_actions": {"type": "boolean", "default": False},
                        "drift_policy": {
                            "type": "string",
                            "enum": ["warn", "abort", "ignore"],
                            "default": "warn",
                        },
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "activity_summary",
                "description": (
                    "Daily/weekly activity digest for registered projects — "
                    "sessions, findings, git commits, PRs and ingested docs, "
                    "each windowed by its own timestamp (never a session's "
                    "started_at). Renders a deterministic markdown digest and, "
                    "unless disabled, prepends an LLM narrative. Answers "
                    "\"what happened today/this week?\". Writes the digest to "
                    ".tesserae/summaries/<project>/ and returns the combined "
                    "markdown plus the paths written."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "day": {
                            "type": "string",
                            "description": "Single day YYYY-MM-DD (default: today).",
                        },
                        "week": {
                            "type": "string",
                            "description": (
                                "Seven daily windows ending on YYYY-MM-DD; "
                                "empty string = the last 7 days ending today."
                            ),
                        },
                        "since": {
                            "type": "string",
                            "description": "Window start (ISO date/datetime).",
                        },
                        "until": {
                            "type": "string",
                            "description": "Window end (ISO date/datetime).",
                        },
                        "project": {
                            "type": "string",
                            "description": (
                                "Registered project name to scope to. Omit for "
                                "all registered projects."
                            ),
                        },
                        "synthesize": {
                            "type": "boolean",
                            "default": True,
                            "description": (
                                "Prepend an LLM narrative over the deterministic "
                                "digest (default true). Set false for the digest only."
                            ),
                        },
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "query_decisions",
                "description": (
                    "Decisions made across registered projects within a time "
                    "range — explicit HUMAN choices parsed deterministically from "
                    "Claude Code's AskUserQuestion tool (the question + the option "
                    "chosen) plus AGENT decisions mined from the conversation. Each "
                    "decision is dated by its own timestamp (never a session's "
                    "started_at). Answers e.g. \"what decisions were made since "
                    "last Monday?\" — the caller resolves relative dates like "
                    "'last Monday' to `since`. Returns a structured decision list."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "day": {
                            "type": "string",
                            "description": "Single day YYYY-MM-DD (default: today).",
                        },
                        "week": {
                            "type": "string",
                            "description": (
                                "Seven daily windows ending on YYYY-MM-DD; "
                                "empty string = the last 7 days ending today."
                            ),
                        },
                        "since": {
                            "type": "string",
                            "description": "Window start (ISO date/datetime).",
                        },
                        "until": {
                            "type": "string",
                            "description": "Window end (ISO date/datetime).",
                        },
                        "project": {
                            "type": "string",
                            "description": (
                                "Registered project name to scope to. Omit for "
                                "all registered projects."
                            ),
                        },
                        "include_agent": {
                            "type": "boolean",
                            "default": True,
                            "description": (
                                "Include LLM-mined agent decisions (default true). "
                                "Set false for the deterministic human "
                                "(AskUserQuestion) decisions only."
                            ),
                        },
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 200,
                            "default": 50,
                            "description": "Maximum decisions to return (default 50, clamped to 200).",
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
            "name": "Resolved project — graph summary",
            "description": (
                "JSON summary of the resolved Tesserae project's typed graph: "
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
            "name": "Resolved project — latest lint report",
            "description": (
                "The markdown lint report from the most recent `tesserae compile`. "
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
        and summary keys off the resolved project. Wiki pages and raw sources are
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
                "name": "Resolved project — graph summary",
                "description": "Node and edge counts for the resolved project.",
                "mimeType": "application/json",
            },
            {
                "uri": "tesserae://lint-report",
                "name": "Resolved project — lint report",
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
                "List every OpenQuestion node in the resolved project, group by topic, and "
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
                f"Summarize the paper at wiki slug `{slug}` from the resolved Tesserae "
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
                f"Find work in the resolved Tesserae project related to `{topic}`. Steps:\n"
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
                f"Compare approaches `{a}` and `{b}` using the resolved Tesserae project. Steps:\n"
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
                f"Run a gap analysis{scoped} against the resolved Tesserae project. Steps:\n"
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
                "Triage every OpenQuestion node in the resolved Tesserae project. Steps:\n"
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
        if name == "graph_map":
            return self._mcp_graph_map(args)
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
            graph, project_root = self._load_requested_graph_with_root(args)
            result = self.search_nodes(
                graph,
                query=query,
                types=type_filter or None,
                kinds=kind_filter or None,
                limit=int(args.get("limit", 10)),
                mode=mode,
                weights=weights,
                include_superseded=bool(args.get("include_superseded", False)),
                budget_chars=_budget_chars_arg(args),
            )
            # LRU: record a read of every node this search surfaced (sidecar only).
            self._bump_nodes_access(
                project_root, (n.get("id") for n in result.get("nodes", []))
            )
            return result
        if name == "embedding_status":
            return self.embedding_status()
        if name == "node_context":
            graph, project_root = self._load_requested_graph_with_root(args)
            return self.node_context(
                graph,
                project_root,
                node_id=args.get("node_id"),
                node_name=args.get("name"),
                limit=int(args.get("limit", 50)),
                include_superseded=bool(args.get("include_superseded", False)),
                use_ppr=bool(args.get("use_ppr") or False),
                budget_chars=_budget_chars_arg(args),
            )
        if name == "verify_claim":
            graph, project_root = self._load_requested_graph_with_root(args)
            return verify_claim(
                graph,
                subject=(str(args["subject"]) if args.get("subject") else None),
                predicate=(str(args["predicate"]) if args.get("predicate") else None),
                obj=(str(args["object"]) if args.get("object") else None),
                claim=(str(args["claim"]) if args.get("claim") else None),
                reground=bool(args.get("reground", True)),
                project_root=project_root,
            )
        if name == "search_facts":
            facts = TemporalFactProjector().project(self._load_requested_graph(args))
            result = search_facts(facts, query=str(args.get("query", "")), limit=int(args.get("limit", 10)), current_only=bool(args.get("current_only", False)))
            # CTX-01: per-fact truncation of evidence blocks (§5.3).
            result["facts"], continuation = _fit_payload_list(
                result["facts"], _budget_chars_arg(args), text_field="evidence"
            )
            if continuation:
                result["continuation"] = continuation
            return result
        if name == "timeline":
            facts = TemporalFactProjector().project(self._load_requested_graph(args))
            result = timeline(facts, query=str(args.get("query", "")), limit=int(args.get("limit", 50)))
            # CTX-01: per-fact truncation of evidence blocks (§5.3).
            result["events"], continuation = _fit_payload_list(
                result["events"], _budget_chars_arg(args), text_field="evidence"
            )
            if continuation:
                result["continuation"] = continuation
            return result
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
        if name == "doctor_report":
            _, project_root = self._load_requested_graph_with_root(args)
            return self.doctor_report(project_root)
        if name == "doctor_run":
            return self._mcp_doctor_run(args)
        if name == "query":
            return self._mcp_query(
                args,
                question=str(args.get("question", "")),
                top_k=int(args.get("top_k", 8)),
                kind=(str(args["kind"]) if args.get("kind") else None),
                backend=str(args.get("backend", "wiki") or "wiki"),
            )
        if name == "ask":
            question = str(args.get("question") or "").strip()
            if not question:
                raise ValueError("ask requires 'question'")
            # Clean-break stubs (lockstep with the CLI's removed flags).
            if "backend" in args:
                raise ValueError("ask: 'backend' has moved → tesserae query --backend")
            if "claude_config_dir" in args:
                raise ValueError("ask: 'claude_config_dir' was removed (configure providers via tesserae init/setup)")
            use_llm = bool(args.get("llm", True))
            no_llm = not use_llm
            top_k = int(args.get("top_k") or 8)
            raw_scope = args.get("scope")
            scope = str(raw_scope) if raw_scope else None
            if scope is not None and scope not in {"current", "all-registered", "federated"}:
                raise ValueError(f"ask: unknown scope {scope!r}")
            scope_aliases = _coerce_str_list(args.get("scope_aliases"))
            # No explicit scope => SMART ROUTER, with continuity across the agent's
            # consecutive questions (rolling per-server history). Federated fallback.
            if scope is None and not args.get("project") and not args.get("graph_path"):
                route = self._route_ask(question, conversation_id=args.get("conversation_id"))
                scope = route.scope
                if route.scope in ("federated", "all-registered") and route.aliases:
                    scope_aliases = route.aliases  # honor an LLM-narrowed subset
                elif route.scope == "current" and route.aliases:
                    args = {**args, "project": route.aliases[0]}
            elif scope is None:
                scope = "current"  # explicit project/graph_path => single project
            if scope == "all-registered":
                return self._mcp_ask_all_registered(
                    question=question,
                    top_k=top_k,
                    scope_aliases=scope_aliases,
                    use_llm=use_llm,
                    no_llm=no_llm,
                )
            if scope == "federated":
                return self._mcp_ask_federated(
                    question=question,
                    scope_aliases=scope_aliases,
                    semantic=bool(args.get("semantic", True)),  # opt-out: on unless disabled
                    synthesize=use_llm,
                )
            return self._mcp_ask(
                args, question=question, top_k=top_k, use_llm=use_llm, no_llm=no_llm,
                route=str(args.get("route") or "auto"),
            )
        if name == "list_projects":
            return self.registry.list_projects()
        if name == "register_project":
            path = args.get("path")
            if not path:
                raise ValueError("register_project requires 'path'")
            return self.registry.register(str(path), name=args.get("name"))
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
            graph, project_root = self._load_requested_graph_with_root(args)
            limit_arg = args.get("limit")
            result = self._mcp_find_session_findings(
                graph,
                node_id=str(node_id),
                kinds=args.get("kinds"),
                limit=50 if limit_arg is None else int(limit_arg),
            )
            # LRU: reading a node's findings via this tool is a read of each
            # surfaced finding — refresh their access so active use keeps them
            # above the absorb/demote threshold (mirrors search_nodes/node_context).
            self._bump_nodes_access(
                project_root, (f.get("node_id") for f in result.get("findings", []))
            )
            return result
        if name == "find_code_symbol_mentions":
            node_id = args.get("node_id")
            if not node_id:
                raise ValueError("find_code_symbol_mentions requires 'node_id'")
            graph, project_root = self._load_requested_graph_with_root(args)
            return self._mcp_find_code_symbol_mentions(
                graph, project_root, node_id=str(node_id),
            )
        if name == "graph_ppr":
            seed = args.get("seed_node_id")
            if seed is None or (isinstance(seed, (list, tuple)) and not seed):
                raise ValueError("graph_ppr requires 'seed_node_id'")
            seed_ids = _coerce_str_list(seed) if not isinstance(seed, str) else [seed]
            graph, project_root = self._load_requested_graph_with_root(args)
            edge_weights = args.get("edge_type_weights") or None
            # Preserve an explicit alpha (even a tiny one like 0.05) rather
            # than collapsing it via ``or 0.15``. ``alpha=0`` is rejected
            # downstream by ``personalized_pagerank`` and by the schema's
            # ``exclusiveMinimum: 0``.
            alpha_arg = args.get("alpha")
            alpha = 0.15 if alpha_arg is None else float(alpha_arg)
            result = self._mcp_graph_ppr(
                graph,
                seed_ids=seed_ids,
                top_k=int(args.get("top_k") or 20),
                alpha=alpha,
                directed=bool(args.get("directed") or False),
                edge_type_weights=edge_weights,
                exclude_direct_neighbors=bool(
                    args.get("exclude_direct_neighbors") or False
                ),
            )
            # LRU: navigating to and reading the ranked nodes is a read —
            # refresh access so actively-surfaced findings don't decay.
            self._bump_nodes_access(
                project_root, (r.get("node_id") for r in result.get("results", []))
            )
            return result
        if name == "compile_context":
            from .context_compiler import compile_context

            graph, project_root = self._load_requested_graph_with_root(args)
            query = str(args.get("query") or "")
            seeds = _coerce_str_list(args.get("seeds"))
            depth = int(args.get("depth") or 2)
            # Preserve an explicit budget=0 (uncapped, per core compile_context
            # semantics where ``budget <= 0`` means no cap). ``... or 32_000``
            # would coerce 0 -> 32000, making the documented uncapped mode
            # unreachable via MCP. Only default when budget is absent/None.
            budget_arg = args.get("budget")
            budget = 32_000 if budget_arg is None else int(budget_arg)
            synthesize = bool(args.get("synthesize") or False)
            multi_pool = bool(args.get("multi_pool") or False)
            bundle = compile_context(
                graph,
                project_root,
                query=query,
                seeds=seeds,
                depth=depth,
                budget=budget,
                synthesize=synthesize,
                multi_pool=multi_pool,
                include_superseded=bool(args.get("include_superseded", False)),
            )
            # LRU: the nodes actually selected into the bundle count as reads.
            self._bump_nodes_access(project_root, bundle.selected_nodes)
            preview = int(args.get("preview") or 0)
            if preview > 0 and len(bundle.body) > preview:
                handle = _HANDLES.put(bundle.body)
                return {
                    "handle": handle,
                    "preview": bundle.body[:preview],
                    "total_chars": len(bundle.body),
                    "truncated": True,
                    "hint": f"Body truncated. Fetch more with get_handle(handle='{handle}', offset=...).",
                    "citations": [dataclasses.asdict(c) for c in bundle.citations],
                    "selected_node_ids": bundle.selected_nodes,
                    "char_budget_used": bundle.char_budget_used,
                    "synthesized": bundle.synthesized,
                }
            # preview disabled (or body already short): EXACT original shape,
            # body first — back-compat for byte/order-sensitive callers.
            return {
                "body": bundle.body,
                "citations": [dataclasses.asdict(c) for c in bundle.citations],
                "selected_node_ids": bundle.selected_nodes,
                "char_budget_used": bundle.char_budget_used,
                "synthesized": bundle.synthesized,
            }
        if name == "get_handle":
            handle = str(args.get("handle") or "")
            try:
                offset, limit = int(args.get("offset") or 0), int(args.get("limit") or 4000)
            except (TypeError, ValueError):
                offset, limit = 0, 4000
            sliced = _HANDLES.slice(handle, offset, limit)
            if sliced is None:
                # LRU expiry / unknown handle is EXPECTED, not exceptional —
                # degrade with guidance instead of raising (codex review).
                return {"handle": handle, "found": False, "slice": "", "eof": True,
                        "error": "handle not found (LRU-evicted or never issued); "
                                 "re-run compile_context with preview>0 for a fresh handle"}
            return sliced
        if name == "agent_view_explain":
            graph, root = self._load_base_graph_with_root(args)
            if root is None:
                raise ValueError("agent_view_explain requires a project root — pass graph_path or project.")
            from .agent_view import resolve_agent_view

            _view, info = resolve_agent_view(root, str(args.get("agent") or ""), graph)
            return info
        if name == "drill_down":
            return self._drill_down(args)
        if name == "ingest":
            from .project import ProjectWiki
            from .clip import ingest_clip

            content = str(args.get("content") or "").strip()
            if not content:
                raise ValueError("ingest: 'content' is required and must be non-empty")

            project_root = self._resolve_project_root_for_ask(args)
            wiki = ProjectWiki.load(project_root)
            report = ingest_clip(
                wiki,
                content=content,
                url=str(args.get("url") or ""),
                title=args.get("title"),
                note=args.get("note"),
                tags=_coerce_str_list(args.get("tags")) or None,
                tldr=bool(args.get("tldr", True)),
            )
            return report
        if name == "graph_write":
            return self._mcp_graph_write(args)
        if name == "list_communities":
            graph = self._load_requested_graph(args)
            return self._mcp_list_communities(
                graph,
                min_size=int(args.get("min_size") or 3),
                limit=int(args.get("limit") or 20),
            )
        if name == "fresh_insights":
            graph, project_root = self._load_requested_graph_with_root(args)
            return self._mcp_fresh_insights(
                graph,
                project_root=project_root,
                limit=int(args.get("limit") or 10),
                kind=(str(args.get("kind")).strip() if args.get("kind") else None),
                include_superseded=bool(args.get("include_superseded", False)),
            )
        if name == "tesserae_setup_plan":
            from .setup import build_plan, detect
            from .setup.wizard import render_review

            project_root = args.get("project_root") or "."
            overrides = args.get("overrides") or None
            if isinstance(overrides, dict):
                # SECURITY: the api key is not settable over MCP (and must
                # never round-trip through a plan JSON an MCP client sees).
                overrides = {k: v for k, v in overrides.items() if k != "llm_api_key"}
            report = detect(project_root)
            plan = build_plan(report, overrides=overrides)
            plan_json = json.loads(plan.model_dump_json())
            # SECURITY: redact the api key everywhere it could surface in the
            # returned plan (top-level field + recorded intent). render_review
            # already masks it.
            if plan_json.get("llm_api_key"):
                plan_json["llm_api_key"] = None
            intent = plan_json.get("intent")
            if isinstance(intent, dict):
                intent.pop("llm_api_key", None)
            return {
                "plan": plan_json,
                "rendered_summary": render_review(plan),
            }
        if name == "tesserae_setup_apply":
            from .setup import SetupPlan, apply_plan, build_plan, detect

            plan_payload = args.get("plan")
            if not isinstance(plan_payload, dict):
                raise ValueError("tesserae_setup_apply requires 'plan' as an object")
            inbound = SetupPlan.model_validate(plan_payload)

            # SECURITY: An MCP caller controls the inbound plan body. If we
            # executed `inbound.install_actions[].command` directly, any client
            # with MCP access could inject arbitrary shell commands. Instead,
            # we extract only the *intent* fields the caller is allowed to
            # influence (booleans + enum choices), then regenerate the action
            # lists server-side via build_plan. Free-form command strings are
            # dropped — that escape hatch is only honored on the local CLI path.
            _MCP_SAFE_INTENT_KEYS = {
                "name",
                "source_kind",
                "sources",
                "extractor",
                "claude_config_dir",
                "claude_model",
                "codex_model",
                "include_raganything",
                "install_raganything",
                "raganything_parser",
                "raganything_extras",
                "install_agent_pointer",
                # Runtime LLM client keys. llm_api_key is deliberately ABSENT:
                # secrets are not settable over MCP (set it via `tesserae init`
                # flags / the wizard, or the ANTHROPIC_API_KEY env var).
                "llm_provider",
                "llm_model",
                "llm_base_url",
                "codex_home",
            }
            _ALLOWED_RAG_PARSERS = {"mineru", "docling", "paddleocr"}

            safe_intent: dict = {}
            for key, value in (inbound.intent or {}).items():
                if key not in _MCP_SAFE_INTENT_KEYS:
                    continue
                safe_intent[key] = value
            # Always honor the inbound top-level intent fields too — they're
            # the user's chosen settings even if `intent` wasn't recorded.
            for key in ("name", "source_kind", "sources", "extractor",
                        "claude_config_dir", "claude_model", "codex_model"):
                safe_intent.setdefault(key, getattr(inbound, key))
            # Runtime LLM keys: only when actually set, so an absent value
            # keeps build_plan's detection-recommended default instead of
            # clobbering it with None. (llm_api_key stays excluded: secrets
            # are not settable over MCP.)
            for key in ("codex_home", "llm_provider", "llm_model", "llm_base_url"):
                value = getattr(inbound, key)
                if value is not None:
                    safe_intent.setdefault(key, value)

            # Bounded-value validation: enum strings must be in their allowlist.
            rag_parser = safe_intent.get("raganything_parser")
            if rag_parser and rag_parser not in _ALLOWED_RAG_PARSERS:
                safe_intent.pop("raganything_parser", None)

            project_root = str(inbound.project_root)
            report = detect(project_root)
            regenerated = build_plan(report, overrides=safe_intent)

            result = apply_plan(
                regenerated,
                confirm_install_actions=bool(args.get("confirm_install_actions", False)),
                confirm_run_actions=bool(args.get("confirm_run_actions", False)),
                drift_policy=args.get("drift_policy", "warn"),
            )
            return {
                "config_path": str(result.config_path),
                "actions_taken": result.actions_taken,
                "warnings": result.warnings,
                "drift": result.drift,
                "wiki_root": str(result.wiki_root),
            }
        if name == "activity_summary":
            return self._mcp_activity_summary(args)
        if name == "query_decisions":
            return self._mcp_query_decisions(args)
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
        exclude_direct_neighbors: bool = False,
    ) -> JSONDict:
        """Run PPR and decorate results with node name/type for the agent."""
        # When excluding the seeds' 1-hop neighbourhood, over-fetch the FULL
        # ranking (same pattern as node_context's use_ppr branch): filtering
        # happens BEFORE the cap so ``top_k`` applies to the surviving 2+ hop
        # nodes, not the pre-filter ranking.
        fetch_k = max(1, len(graph.nodes)) if exclude_direct_neighbors else top_k
        ranked = personalized_pagerank(
            graph,
            seed_ids=seed_ids,
            alpha=alpha,
            top_k=fetch_k,
            edge_type_weights=edge_type_weights,
            directed=directed,
        )
        if exclude_direct_neighbors:
            seed_set = set(seed_ids)
            # 1-hop neighbours in EITHER direction — an "unexpected
            # connection" means not directly linked at all, regardless of
            # edge direction (and regardless of ``directed``).
            neighbor_ids = {
                edge.target if edge.source in seed_set else edge.source
                for edge in graph.edges
                if edge.source in seed_set or edge.target in seed_set
            }
            ranked = [
                (node_id, score)
                for node_id, score in ranked
                if node_id not in seed_set and node_id not in neighbor_ids
            ][:top_k]
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
            "exclude_direct_neighbors": exclude_direct_neighbors,
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

    def _mcp_activity_summary(self, args: JSONDict) -> JSONDict:
        """Adapter over :func:`build_summary` — resolve windows, gather, render.

        ``project`` optionally scopes to one registered project (default: all
        registered). ``synthesize`` (default true) prepends the LLM narrative;
        the deterministic digest is always written to
        ``.tesserae/summaries/<project>/``. Returns the combined markdown plus
        the string paths written.
        """
        try:
            windows = resolve_windows(
                day=args.get("day"),
                week=args.get("week"),
                since=args.get("since"),
                until=args.get("until"),
            )
        except ValueError as exc:
            return {"error": str(exc)}
        project = args.get("project")
        project_names = [str(project)] if project else None
        synthesize = bool(args.get("synthesize", True))
        try:
            result = build_summary(
                windows, project_names, synthesize=synthesize, write=True
            )
        except ValueError as exc:
            # Strict names: a typo'd project errors instead of silently
            # meaning "no projects" (activity_summary._resolve_projects).
            return {"error": str(exc)}
        return {
            "markdown": result.markdown,
            "paths": [str(p) for p in result.paths],
        }

    def _mcp_query_decisions(self, args: JSONDict) -> JSONDict:
        """Adapter over :func:`tesserae.decisions.gather_decisions` — resolve the
        window, return the structured decision list. ``project`` scopes to one
        registered project (default: all); ``include_agent`` (default true) adds
        LLM-mined agent decisions on top of the deterministic human ones.
        """
        from .decisions import gather_decisions

        try:
            windows = resolve_windows(
                day=args.get("day"),
                week=args.get("week"),
                since=args.get("since"),
                until=args.get("until"),
            )
        except ValueError as exc:
            return {"error": str(exc)}
        project = args.get("project")
        project_names = [str(project)] if project else None
        include_agent = bool(args.get("include_agent", True))
        try:
            decisions = gather_decisions(windows, project_names, include_agent=include_agent)
        except ValueError as exc:
            # Strict names: a typo'd project errors instead of silently
            # meaning "no projects" (activity_summary._resolve_projects).
            return {"error": str(exc)}
        # Descent PR1 safety clamp: a busy week can hold hundreds of
        # decisions; never dump an unbounded list into context. Preserve an
        # explicit 0 by clamping it (like alpha/budget: no ``or`` coercion).
        limit_arg = args.get("limit")
        limit = max(1, min(50 if limit_arg is None else int(limit_arg), 200))
        return {
            "decisions": [
                {
                    "ts": d.ts.isoformat(),
                    "source": d.source,
                    "project": d.project,
                    "session_id": d.session_id,
                    "question": d.question,
                    "answer": d.answer,
                    "options": d.options,
                    "header": d.header,
                }
                for d in decisions[:limit]
            ],
            "total": len(decisions),
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
        limit: int = 50,
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
        # Deterministic ordering: by kind then body. ``total`` reports the
        # full pre-limit count; the clamp (Descent PR1) bounds what enters
        # context for hub nodes with hundreds of findings.
        out.sort(key=lambda d: (d["kind"], d["body"]))
        total = len(out)
        out = out[: max(1, min(int(limit), 200))]
        return {"node_id": node_id, "findings": out, "total": total}

    def _mcp_find_code_symbol_mentions(
        self,
        graph: ResearchGraph,
        project_root: Optional[Path],
        *,
        node_id: str,
    ) -> JSONDict:
        """Feature H — return code symbols mentioned by a session finding.

        Two-stage resolution:

        1. Walk ``discusses`` edges already on ``graph`` (minted by the
           opt-in ``insight_symbol_link`` post-compile pass). These are
           the canonical, persisted matches.
        2. If no edges are present (pass never ran), fall back to a live
           scan of the finding body against ``.tesserae/code-graph.json``
           for the resolved project root. No graph mutation happens here.
        """
        from .memory.insight_symbol_link import (
            build_symbol_index,
            find_symbol_mentions,
            load_code_graph_nodes,
        )

        node = next((n for n in graph.nodes if n.id == node_id), None)
        if node is None:
            raise ValueError(f"find_code_symbol_mentions: unknown node_id {node_id!r}")
        if node.type.value not in self._SESSION_FINDING_TYPES:
            raise ValueError(
                f"find_code_symbol_mentions: node {node_id!r} is "
                f"{node.type.value}, not a Session<Kind> finding"
            )

        nodes_by_id = {n.id: n for n in graph.nodes}

        # Stage 1 — persisted ``discusses`` edges.
        mentions: List[JSONDict] = []
        seen_ids: set = set()
        for edge in graph.edges:
            if edge.type != "discusses" or edge.source != node_id:
                continue
            tgt = nodes_by_id.get(edge.target)
            if tgt is None or tgt.id in seen_ids:
                continue
            seen_ids.add(tgt.id)
            mentions.append({
                "symbol_node_id": tgt.id,
                "name": tgt.name,
                "type": tgt.type.value,
                "source_path": tgt.source_path,
                "source": "persisted_edge",
            })

        # Stage 2 — live scan when no edges exist.
        if not mentions and project_root is not None:
            code_graph_path = project_root / ".tesserae" / "code-graph.json"
            if code_graph_path.exists():
                raw_nodes = load_code_graph_nodes(code_graph_path)
                if raw_nodes:
                    index = build_symbol_index(raw_nodes)
                    for symbol in find_symbol_mentions(node, index):
                        sid = str(symbol.get("id") or "")
                        if not sid or sid in seen_ids:
                            continue
                        seen_ids.add(sid)
                        mentions.append({
                            "symbol_node_id": sid,
                            "name": str(symbol.get("name") or ""),
                            "type": str(symbol.get("type") or ""),
                            "source_path": symbol.get("source_path"),
                            "source": "live_scan",
                        })

        mentions.sort(key=lambda d: (d["type"], d["name"]))
        return {
            "node_id": node_id,
            "body": node.name,
            "mentions": mentions,
            "total": len(mentions),
        }

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
            item: JSONDict = {
                "community_id": node.id,
                "title": node.name,
                "description": node.description,
                "tags": list(meta.get("tags") or []),
                "member_count": count,
            }
            # Descent PR1 safety clamp: the member id list is unbounded
            # (a mega-community holds hundreds of ids), so it never enters
            # context inline. Stash it behind a content-keyed handle; a
            # caller that really needs the ids pages them via get_handle.
            if member_ids:
                item["member_ids_handle"] = _HANDLES.put(json.dumps(member_ids))
            items.append(item)
        items.sort(key=lambda d: (-int(d["member_count"]), d["community_id"]))
        items = items[: max(1, int(limit))]
        return {"communities": items, "total": len(items)}

    def _mcp_graph_map(self, args: JSONDict) -> JSONDict:
        """Budgeted Descent map over the hierarchy sidecar (§5.1 + §5.2).

        Cards are pure functions of ``graph.json`` + ``hierarchy.json`` +
        the summary caches: ``quality="llm"`` comes from in-graph
        COMMUNITY_SUMMARY nodes (coarsest level) or a warm level-scoped
        cache entry. The ONE exception is lazy materialization (PR6): the
        first visit to a cold scope pays exactly one ``complete_json`` call
        and caches the result; no client, call failure, invalid or
        citation-rejected output all degrade to the deterministic
        structural card — never blocks, never raises. Auto-coarsening below
        the root is built into ``Hierarchy.children`` (descent lands on the
        first finer level that actually splits the scope, skipping
        byte-identical pass-through levels); overflow beyond that is handled
        by cursor pagination, never a terminal rollup. Every returned card's
        scope_id is fed to ``_bump_nodes_access`` — spine traversal is
        memory "use", the demand signal the consolidation SUMMARIZE op (PR7)
        pre-warms from.

        Agent-org scopes (``org:root`` / ``agent:<key>``, §6.2 PR9) dispatch
        to :meth:`_graph_map_agent_scope` BEFORE the hierarchy sidecar loads —
        the org tree is the registry, not the Louvain dendrogram. Federated
        scopes (``<alias>::`` / ``<alias>::<cid>``, §6.3 PR10) dispatch to
        :meth:`_graph_map_federated_scope` for the same reason: a sibling
        project's tree is resolved through the registry and its OWN sidecar,
        and the local graph is never loaded for it.
        """
        from .agent_identity import ORG_ROOT
        from .community_summaries import materialize_community_summary
        from .hierarchy import (
            AGENT_SCOPE_PREFIX,
            SUMMARY_CHAR_CAP,
            community_card,
            load_hierarchy,
            node_card,
            split_federated_scope,
            undirected_degrees,
        )

        budget_chars = _budget_chars_arg(args)
        cursor = max(0, int(args.get("cursor") or 0))
        raw_scope = args.get("scope")
        scope = str(raw_scope) if raw_scope else None
        if scope is not None and (scope == ORG_ROOT or scope.startswith(AGENT_SCOPE_PREFIX)):
            return self._graph_map_agent_scope(
                args, scope, budget_chars=budget_chars, cursor=cursor
            )
        federated = split_federated_scope(scope) if scope is not None else None
        if federated is not None:
            return self._graph_map_federated_scope(
                federated[0], federated[1], budget_chars=budget_chars, cursor=cursor
            )

        graph, project_root = self._load_requested_graph_with_root(args)
        if project_root is None:
            raise ValueError(
                "graph_map requires a project root (graph stores have none). "
                "Pass graph_path/project or cd into a registered project."
            )
        hierarchy = load_hierarchy(project_root)
        by_id = {n.id: n for n in graph.nodes}
        degrees = undirected_degrees(graph)
        summary_cache_dir = project_root / ".tesserae" / "community_summaries"

        if scope is None:
            coarsest = hierarchy.coarsest
            cards = [
                community_card(
                    hierarchy, cid, members, by_id, degrees,
                    summary_cache_dir=summary_cache_dir,
                )
                for cid, members in sorted(
                    coarsest.items(), key=lambda kv: (-len(kv[1]), kv[0])
                )
            ]
            counts = self.graph_summary(graph)
            header: JSONDict = {
                "scope": None,
                "kind": "root",
                "levels": len(hierarchy.levels),
                "node_count": counts["node_count"],
                "edge_count": counts["edge_count"],
                "community_count": len(coarsest),
                "hubs": [by_id[h].name for h in hierarchy.hubs[:10] if h in by_id],
            }
        else:
            found = hierarchy.find_scope(scope)
            if found is None:
                raise ValueError(
                    f"graph_map: unknown scope {scope!r}. Valid scopes are "
                    f"community ids from a previous graph_map call (a card's "
                    f"scope_id, e.g. 'CommunitySummary:<hash>'), 'agent:<key>' "
                    f"for an agent's distilled index, or 'org:root' for the "
                    f"agent org tree; start from the root with graph_map() "
                    f"(no scope) and descend."
                )
            level, members = found
            community_children, loose = hierarchy.children(scope) or ([], list(members))
            scope_card = community_card(
                hierarchy, scope, members, by_id, degrees,
                summary_cache_dir=summary_cache_dir,
            )
            if scope_card["quality"] == "structural":
                # §5.2 lazy materialization: this visit pays at most ONE
                # complete_json call. Community children engage the citation
                # discipline (the prompt lists their cids; prose citing none
                # is rejected and stays uncached). Failure of any kind keeps
                # the deterministic structural card.
                materialized = materialize_community_summary(
                    [by_id[m] for m in members if m in by_id],
                    cid=scope,
                    member_ids=members,
                    level=level,
                    cache_dir=summary_cache_dir,
                    json_client=self._community_summary_json_client(),
                    child_cids=[child_cid for child_cid, _ in community_children],
                )
                if materialized is not None:
                    title, description, tags = materialized
                    scope_card = {
                        **scope_card,
                        "title": title,
                        "summary": _truncate_text(description, SUMMARY_CHAR_CAP),
                        "tags": tags,
                        "quality": "llm",
                    }
            cards = [
                community_card(
                    hierarchy, child_cid, child_members, by_id, degrees,
                    summary_cache_dir=summary_cache_dir,
                )
                for child_cid, child_members in community_children
            ]
            cards.extend(node_card(member_id, scope, by_id) for member_id in loose)
            header = {
                "scope": scope,
                "kind": "community",
                "level": level,  # dendrogram index, 0 = finest
                "title": scope_card["title"],
                "summary": scope_card["summary"],
                "quality": scope_card["quality"],
                "leaf_member_count": len(members),
                "parent_scope": scope_card["parent_scope"],
            }

        result = _paginate_cards(header, cards, budget_chars, cursor)
        # LRU + demand: every surfaced scope_id counts as a read. Coarsest
        # cids ARE graph node ids when summaries are minted; finer-level cids
        # are pseudo-id rows that exist ONLY as the SUMMARIZE demand signal
        # (daemon._summarize_once ranks by the cid row + member sums). Decay,
        # distill and forget-by-disuse all key node_memory by graph node id,
        # so those rows never leak into leaf memory semantics.
        self._bump_nodes_access(
            project_root, (str(c.get("scope_id")) for c in result["cards"])
        )
        return result

    def _graph_map_agent_scope(
        self, args: JSONDict, scope: str, *, budget_chars: int, cursor: int
    ) -> JSONDict:
        """Org-tree scopes for ``graph_map``: ``org:root`` / ``agent:<key>`` (§6.2).

        ``org:root`` renders the agent registry tree as agent cards
        (``children_count`` = direct reports; descent into a child is
        ``agent:<child key>``); ``agent:<key>`` renders that agent's distilled
        L1 Index as note cards, with a manager's direct-report agent cards
        first so org navigation continues downward. CRITICAL invariant, sealed
        L0: the base graph is consulted ONLY to enumerate observed agent keys
        and as :func:`resolve_agent_view` input — no raw L0 node ever becomes
        a card and no L0 content is rendered; manager/org callers see
        distillate-only knowledge, exactly like ``agent=`` reads. Escalation
        past that seal is each note card's ``drill`` block feeding the
        existing audited ``drill_down`` tool. Fail-loud on unknown agent keys
        and missing artifacts (AgentViewError names the distill remedy). No
        ``_bump_nodes_access``: agent/org cards are registry structure and
        distillate ids, not L0 graph nodes — the community pre-warm demand
        signal stays clean, and drill_down records the raw read on escalation.
        """
        from .agent_distill import agent_artifact_path
        from .agent_identity import ORG_ROOT, AgentRegistry
        from .agent_view import _known_agent_keys, resolve_agent_view
        from .hierarchy import AGENT_SCOPE_PREFIX, agent_card, distilled_note_card

        graph, project_root = self._load_base_graph_with_root(args)
        if project_root is None:
            raise ValueError(
                "graph_map agent scopes require a project root (graph stores "
                "have none). Pass graph_path/project or cd into a registered "
                "project."
            )
        registry = AgentRegistry.for_project(project_root)
        known = _known_agent_keys(graph, registry)
        parent_of = {key: registry.effective_parent(key) for key in known}
        children_of: Dict[str, List[str]] = {}
        for key in known:  # known is sorted → children lists stay key-sorted
            children_of.setdefault(parent_of[key], []).append(key)
        declared = registry.load().get("agents")
        labels = declared if isinstance(declared, dict) else {}

        def label_for(key: str) -> str:
            entry = labels.get(key)
            return str(entry.get("label") or "") if isinstance(entry, dict) else ""

        def subtree_keys(key: str) -> List[str]:
            out = [key]
            for child in children_of.get(key, []):
                out.extend(subtree_keys(child))
            return out

        def note_count(key: str) -> int:
            path = agent_artifact_path(project_root, key)
            if not path.is_file():
                return 0
            l1 = self._load_graph_cached(path)
            return sum(1 for n in l1.nodes if n.type is ResearchNodeType.DISTILLED_NOTE)

        def report_card(key: str) -> JSONDict:
            parent = parent_of[key]
            subtree = subtree_keys(key)
            return agent_card(
                key,
                label=label_for(key),
                parent_scope=parent if parent == ORG_ROOT else AGENT_SCOPE_PREFIX + parent,
                direct_reports=len(children_of.get(key, [])),
                subtree_agents=len(subtree),
                subtree_notes=sum(note_count(k) for k in subtree),
                distilled=agent_artifact_path(project_root, key).is_file(),
            )

        def report_cards(keys: List[str]) -> List[JSONDict]:
            cards = [report_card(key) for key in keys]
            cards.sort(key=lambda c: (-int(c["size"]), str(c["scope_id"])))
            return cards

        if scope == ORG_ROOT:
            cards = report_cards(children_of.get(ORG_ROOT, []))
            header: JSONDict = {
                "scope": ORG_ROOT,
                "kind": "org",
                "title": "Agent org",
                "agent_count": len(known),
                "parent_scope": None,
            }
        else:
            canonical = registry.resolve_alias(scope[len(AGENT_SCOPE_PREFIX):])
            if canonical not in known:
                raise ValueError(
                    f"graph_map: unknown agent scope {scope!r}. Known agents: "
                    f"{', '.join(known) or '(none)'}. Start from "
                    f"scope='org:root' and descend via the agent cards."
                )
            # Distillate-only resolution, READ-ONLY reuse of the agent-view
            # layer — fail-loud on missing artifacts, same as agent= reads.
            view, info = resolve_agent_view(project_root, canonical, graph)
            cards = report_cards(children_of.get(canonical, []))
            note_cards = [
                distilled_note_card(node)
                for node in view.nodes
                if node.type is ResearchNodeType.DISTILLED_NOTE
            ]
            note_cards.sort(key=lambda c: (-int(c["size"]), str(c["scope_id"])))
            cards.extend(note_cards)
            parent = parent_of[canonical]
            header = {
                "scope": scope,
                "kind": "agent",
                "agent": canonical,
                "mode": info["mode"],
                "title": label_for(canonical) or canonical,
                "note_count": len(note_cards),
                "direct_reports": len(children_of.get(canonical, [])),
                "parent_scope": parent if parent == ORG_ROOT else AGENT_SCOPE_PREFIX + parent,
            }
        return _paginate_cards(header, cards, budget_chars, cursor)

    def _graph_map_federated_scope(
        self, alias: str, sub: str, *, budget_chars: int, cursor: int
    ) -> JSONDict:
        """Federated scopes for ``graph_map``: ``<alias>::`` / ``<alias>::<cid>`` (§6.3 PR10).

        Resolves ``alias`` through the project registry and serves that
        sibling project's Descent tree READ-ONLY from its own compiled bytes,
        loading exactly ONE child graph per call via the mtime-keyed
        :data:`_FED_CHILD_CACHE` (the local graph is never loaded). Card ids
        are namespaced ``alias::id`` — ``federation.federate_graphs``
        semantics ONLY; the order-dependent ``batch.merge_graphs`` is
        ingest-only and never enters this path. Digest verification: the alias
        root call records sha256 content digests of the sibling's
        ``graph.json`` + ``hierarchy.json`` (returned as header metadata,
        GRAPH_REF-style); a descent whose current bytes no longer match those
        recorded digests is served from the CURRENT bytes but with
        ``stale: true`` on every card and a ``"stale — recompile"`` header
        note — never a silently outdated map. A missing/corrupt registry
        degrades to single-graph mode: the federated scope fails loud with an
        actionable error while every non-federated scope keeps serving.
        READ-ONLY invariants: no ``_bump_nodes_access`` (that would write the
        sibling's node-memory sqlite) and no lazy summary materialization
        (that would write its cache) — cards reuse the sibling's in-graph
        summaries and warm caches, else the deterministic structural title.
        """
        from .hierarchy import (
            FEDERATION_NS,
            community_card,
            federated_scope_id,
            namespace_card,
            node_card,
        )

        scope = alias + FEDERATION_NS + sub
        try:
            graph_path = self.registry.resolve_graph_path(alias)
            known = self.registry.all_project_names()
        except ValueError as exc:
            # Corrupt registry: degrade to single-graph mode — fail loud on
            # the federated scope only, never crash serve.
            raise ValueError(
                f"graph_map: federated scope {scope!r} is unavailable — the "
                f"project registry is unreadable ({exc}). Serving single-graph "
                f"mode: use non-federated scopes, or fix/remove the registry "
                f"file and retry."
            ) from exc
        if graph_path is None:
            raise ValueError(
                f"graph_map: unknown project alias {alias!r} in scope "
                f"{scope!r}. Registered projects: "
                f"{', '.join(known) or '(none)'}. Register the sibling with "
                f"register_project, or drop the '{alias}::' prefix to descend "
                f"the local graph."
            )
        child = _load_federated_child(alias, graph_path)
        summary_cache_dir = child.root / ".tesserae" / "community_summaries"

        if not sub:
            # Alias root: record the digests later descents verify against.
            self._fed_digests[alias] = dict(child.digests)
            coarsest = child.hierarchy.coarsest
            cards = [
                namespace_card(
                    community_card(
                        child.hierarchy, cid, members, child.by_id, child.degrees,
                        summary_cache_dir=summary_cache_dir,
                    ),
                    alias,
                )
                for cid, members in sorted(
                    coarsest.items(), key=lambda kv: (-len(kv[1]), kv[0])
                )
            ]
            counts = self.graph_summary(child.graph)
            header: JSONDict = {
                "scope": scope,
                "kind": "root",
                "project": alias,
                "levels": len(child.hierarchy.levels),
                "node_count": counts["node_count"],
                "edge_count": counts["edge_count"],
                "community_count": len(coarsest),
                "hubs": [
                    child.by_id[h].name
                    for h in child.hierarchy.hubs[:10]
                    if h in child.by_id
                ],
                "digests": dict(child.digests),
                "stale": False,
            }
        else:
            found = child.hierarchy.find_scope(sub)
            if found is None:
                raise ValueError(
                    f"graph_map: unknown scope {sub!r} in project {alias!r}. "
                    f"Valid federated scopes are '{alias}::' (that project's "
                    f"root card set) and '{alias}::<scope_id>' for a scope_id "
                    f"from a previous '{alias}::' card."
                )
            recorded = self._fed_digests.get(alias)
            stale = recorded is not None and recorded != child.digests
            level, members = found
            community_children, loose = child.hierarchy.children(sub) or ([], list(members))
            scope_card = community_card(
                child.hierarchy, sub, members, child.by_id, child.degrees,
                summary_cache_dir=summary_cache_dir,
            )
            cards = [
                namespace_card(
                    community_card(
                        child.hierarchy, child_cid, child_members, child.by_id,
                        child.degrees, summary_cache_dir=summary_cache_dir,
                    ),
                    alias,
                    stale=stale,
                )
                for child_cid, child_members in community_children
            ]
            cards.extend(
                namespace_card(node_card(member_id, sub, child.by_id), alias, stale=stale)
                for member_id in loose
            )
            header = {
                "scope": scope,
                "kind": "community",
                "project": alias,
                "level": level,  # dendrogram index in the SIBLING's tree
                "title": scope_card["title"],
                "summary": scope_card["summary"],
                "quality": scope_card["quality"],
                "leaf_member_count": len(members),
                "parent_scope": federated_scope_id(alias, scope_card["parent_scope"]),
                "digests": dict(child.digests),
                "stale": stale,
            }
            if stale:
                header["note"] = (
                    f"stale — recompile your map: {alias}'s graph.json/"
                    f"hierarchy.json changed since its '{alias}::' card set "
                    f"was built; re-run graph_map('{alias}::') before trusting "
                    f"cards held from the old map."
                )
        # READ-ONLY sibling: no _bump_nodes_access (a write to the sibling's
        # node-memory sqlite) — the pre-warm demand signal belongs to the
        # sibling's own sessions, and drill/compile_context reads over there
        # record their own use.
        return _paginate_cards(header, cards, budget_chars, cursor)

    def _community_summary_json_client(self) -> Optional[object]:
        """LLM client for lazy summary materialization (§5.2), or ``None``.

        Resolution mirrors ``project._merge_community_summaries``: the test
        seam wins, the ``TESSERAE_COMMUNITY_SUMMARIES`` opt-out disables, and
        otherwise the default client is built once and memoized — including a
        ``None``/failed build, so a clientless environment costs nothing per
        ``graph_map`` call. Never raises: no client just means every cold
        scope keeps its structural card.
        """
        from .community_summaries import is_enabled_via_env
        from .project import _get_community_summaries_test_client

        injected = _get_community_summaries_test_client()
        if injected is not None:
            return injected
        if not is_enabled_via_env():
            return None
        if not hasattr(self, "_lazy_summary_client"):
            try:
                from .llm_json import build_default_json_client

                self._lazy_summary_client = build_default_json_client()
            except Exception:  # noqa: BLE001
                self._lazy_summary_client = None
        return self._lazy_summary_client

    def _mcp_fresh_insights(
        self,
        graph: ResearchGraph,
        *,
        project_root: Optional[Path] = None,
        limit: int = 10,
        kind: Optional[str] = None,
        include_superseded: bool = False,
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
        # such an edge (i.e. has been superseded) unless include_superseded.
        # This matches the canonical orientation chosen by
        # tesserae.memory.supersede and is shared with
        # search_nodes/node_context via the helper.
        superseded_ids: set = set() if include_superseded else _superseded_ids(graph)

        # KB-02 byte-idempotence: access state (access_count/last_accessed_at/
        # decay) lives ONLY in the node_memory sidecar, never on
        # node.metadata. Read it directly so scoring + output do not depend on
        # the compile stamping memory fields into graph.json.
        memory_rows = self._read_node_memory(project_root)

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
            score = compute_decay_score(
                self._decay_view(node, memory_rows.get(node.id)), now
            )
            scored.append((score, node))

        # Highest score first; ties broken by node id for determinism.
        scored.sort(key=lambda t: (-t[0], t[1].id))

        capped = max(1, min(int(limit), 200))
        out: List[JSONDict] = []
        for score, node in scored[:capped]:
            # KB-02: these are the nodes the agent actually surfaced.
            self._bump_node_access(project_root, node.id)
            meta = node.metadata or {}
            row = memory_rows.get(node.id)
            out.append(
                {
                    "node_id": node.id,
                    "kind": node.type.value,
                    "body": node.name,
                    "session_id": meta.get("session_id"),
                    "first_seen_at": meta.get("first_seen_at"),
                    "last_accessed_at": (
                        row.last_accessed_at if row is not None else None
                    ),
                    "access_count": row.access_count if row is not None else 0,
                    "decay_score": round(score, 4),
                    # Extraction quality signals (when the extractor recorded
                    # them) — a flag for the reader, never a truth guarantee.
                    **({"confidence": meta["confidence"]} if "confidence" in meta else {}),
                    **({"confidence_rationale": meta["confidence_rationale"]} if meta.get("confidence_rationale") else {}),
                    **({"revisit_signals": meta["revisit_signals"]} if meta.get("revisit_signals") else {}),
                }
            )
        return {"findings": out, "total": len(scored)}

    @staticmethod
    def _decay_view(node: ResearchNode, row: Optional[Any]) -> Mapping[str, Any]:
        """Metadata view for decay scoring with sidecar access state overlaid.

        ``first_seen_at`` stays compile-owned (read from ``node.metadata``);
        ``last_accessed_at`` / ``access_count`` are read from the node_memory
        sidecar row when present so scoring never depends on access fields
        being stamped into ``graph.json`` (KB-02 byte-idempotence).
        """
        meta = dict(node.metadata or {})
        if row is not None:
            meta["last_accessed_at"] = row.last_accessed_at
            meta["access_count"] = row.access_count
        else:
            # No sidecar row: ignore any stale access fields on metadata so the
            # node scores as freshly minted from first_seen_at alone.
            meta.pop("last_accessed_at", None)
            meta.pop("access_count", None)
        return meta

    def _read_node_memory(self, project_root: Optional[Path]) -> Dict[str, Any]:
        """Return ``{node_id: NodeMemoryRow}`` from the sidecar, or ``{}``.

        Degrades to an empty mapping when the project root or sidecar db is
        unavailable — a read must never fail because the memory layer is.
        """
        if project_root is None:
            return {}
        try:
            from .memory.store import read_memory as _read_memory

            db_path = project_root / ".tesserae" / "sqlite.db"
            if not db_path.exists():
                return {}
            return _read_memory(db_path)
        except Exception:
            return {}

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
        include_superseded: bool = False,
        budget_chars: int = DEFAULT_BUDGET_CHARS,
    ) -> JSONDict:
        """Search public ResearchGraph nodes.

        ``mode`` selects the retrieval strategy:

        * ``hybrid`` (default) — BM25 + lexical + embedding fused via RRF.
        * ``bm25`` / ``lexical`` / ``embedding`` — single-lane variants.
        * ``legacy`` — original casefolded substring matcher; preserved
          bit-for-bit so older callers and tests keep working.

        The return shape (``query``, ``total_matches``, ``nodes``) is
        unchanged; ``mode`` is appended so clients can confirm what ran.

        ``budget_chars`` enforces CTX-01 on the response: each returned node
        payload is clamped to the per-entry cap (``budget_chars // 8`` —
        the count was already clamped by ``limit``, per-node size was not) and
        whole payloads are greedily admitted until the budget; a drop adds one
        ``continuation`` line. ``budget_chars=0`` = uncapped.
        """
        type_filter = {str(item) for item in types or []}
        kind_filter = {str(item).lower() for item in kinds or []}
        suppressed = set() if include_superseded else _superseded_ids(graph)
        public_nodes = [n for n in graph.nodes if not is_code_graph_node(n)]
        candidates: List[ResearchNode] = []
        for node in public_nodes:
            if node.id in suppressed:
                continue
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
            page, continuation = _fit_payload_list(
                matches[:bounded_limit], budget_chars
            )
            out: JSONDict = {
                "query": query,
                "mode": "legacy",
                "total_matches": len(matches),
                "nodes": page,
            }
            if continuation:
                out["continuation"] = continuation
            return out

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
        nodes_out, continuation = _fit_payload_list(nodes_out, budget_chars)
        out = {
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
        if continuation:
            out["continuation"] = continuation
        return out

    def embedding_status(self) -> JSONDict:
        """Report the active embedding backend used by hybrid search."""
        try:
            backend = _active_embedding_backend()
            return {
                "available": True,
                "backend": backend.name,
                "semantic": bool(_backend_is_semantic(backend)),
                "dim": int(getattr(backend, "dim", 0)),
                "default_weights": dict(_HYBRID_DEFAULT_WEIGHTS),
                "modes": ["hybrid", "bm25", "lexical", "embedding", "legacy"],
            }
        except Exception as exc:  # pragma: no cover - defensive
            return {
                "available": False,
                "backend": None,
                "semantic": False,
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

    def doctor_report(self, project_root: Optional[Path]) -> JSONDict:
        """Serve the persisted doctor report; regeneration stays a CLI action."""
        if project_root is None:
            raise ValueError(
                "doctor_report requires a project root — pass graph_path or project, or set a default graph."
            )
        report_path = project_root / ".tesserae" / "doctor-report.md"
        if not report_path.exists():
            return {
                "exists": False,
                "path": str(report_path.relative_to(project_root)),
                "body": "",
                "byte_count": 0,
                "truncated": False,
            }
        raw = report_path.read_bytes()
        truncated = len(raw) > DOCTOR_REPORT_BYTE_CAP
        body = raw[:DOCTOR_REPORT_BYTE_CAP].decode("utf-8", errors="ignore")
        return {
            "exists": True,
            "path": str(report_path.relative_to(project_root)),
            "body": body,
            "byte_count": len(raw),
            "truncated": truncated,
            "cap_bytes": DOCTOR_REPORT_BYTE_CAP,
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
        cwd_root = self.registry.resolve_project_by_cwd()
        if cwd_root is not None and (cwd_root / ".tesserae").exists():
            return cwd_root
        if self.default_graph_path:
            root = _project_root_for_graph_path(self.default_graph_path)
            if root is not None:
                return root
        raise ValueError(
            "ask: no project specified. Pass 'project' or 'graph_path', cd into a registered "
            "project, or start the MCP server with --graph pointing at a .tesserae layout."
        )

    def _route_ask(self, question: str, conversation_id: Optional[str] = None):
        """Pick the scope for a bare question, with continuity across an agent's
        consecutive calls. History is keyed by ``conversation_id`` so concurrent
        clients on one server don't cross-contaminate (the default bucket is used
        when a client doesn't supply one). The LLM classifier (lazy, no API key)
        fires only for the ambiguous middle; the federated fallback means a mixed
        history can never produce a *wrong* (vs. merely broader) answer."""
        from .ask_router import make_llm_classifier, route_ask
        from .llm_json import build_rotating_client

        histories = getattr(self, "_ask_route_histories", None)
        if histories is None:
            histories = self._ask_route_histories = {}
        # Coerce the (client-supplied) id to a hashable str and bound the number
        # of conversation buckets so a client spraying unique ids can't leak memory.
        key = str(conversation_id) if conversation_id is not None else "_default"
        history = histories.get(key)
        if history is None:
            if len(histories) >= 64:
                histories.pop(next(iter(histories)))  # evict oldest-inserted bucket
            history = histories[key] = []
        classifier = getattr(self, "_ask_classifier", None)
        if classifier is None:
            classifier = self._ask_classifier = make_llm_classifier(build_rotating_client)
        cwd_root = self.registry.resolve_project_by_cwd()
        cwd_alias = self.registry.alias_for_root(cwd_root) if cwd_root else None
        route = route_ask(
            question, self.registry.all_project_names(), history=history,
            cwd_alias=cwd_alias, llm_classify=classifier,
        )
        history.append(route)
        del history[:-8]  # keep only the recent window
        return route

    def _mcp_doctor_run(self, args: JSONDict) -> JSONDict:
        """Run the doctor checks read-only and return the report JSON.

        Never fixes, never writes the report artifacts — MCP callers get a
        fresh diagnosis; repairs stay an explicit CLI action (`doctor --fix`).
        """
        from .doctor import run_doctor, to_json

        project_root = self._resolve_project_root_for_ask(args)
        report = run_doctor(project_root, fix=False)
        return json.loads(to_json(report))

    def _mcp_query(
        self,
        args: JSONDict,
        *,
        question: str,
        top_k: int,
        kind: Optional[str],
        backend: str,
    ) -> JSONDict:
        """Raw-retrieval adapter mirroring `tesserae query` (no LLM).

        wiki: deterministic BM25/semantic hits via WikiQuery, force_no_llm.
        raganything: the optional multimodal backend through ask_project's
        explicit-backend path (its not-enabled/no-answer envelopes pass
        through unchanged).
        """
        cleaned = (question or "").strip()
        if not cleaned:
            raise ValueError("query: question is required")
        bounded = max(1, min(int(top_k), 50))
        project_root = self._resolve_project_root_for_ask(args)
        if backend == "raganything":
            from .project import ProjectWiki
            from .query import ask_project

            wiki = ProjectWiki.load(project_root)
            envelope = ask_project(wiki, cleaned, backend="raganything", top_k=bounded)
            self._bump_nodes_access(
                project_root, _hit_node_ids(envelope)
            )
            return envelope
        from .query import WikiQuery

        wq = WikiQuery(project_root, top_k=bounded, kind_filter=kind)
        result = wq.answer(cleaned, force_no_llm=True)
        payload = result.to_dict()
        payload["backend"] = "wiki"
        # LRU: the retrieved hits count as reads (sidecar only).
        self._bump_nodes_access(project_root, _hit_node_ids(payload))
        return payload

    def _mcp_ask(
        self,
        args: JSONDict,
        *,
        question: str,
        top_k: int,
        use_llm: bool = True,
        no_llm: bool = False,
        route: str = "auto",
    ) -> JSONDict:
        """Dispatch ``ask`` to the compiled-wiki planner/search path.

        Thin adapter around :func:`tesserae.query.ask_project` so the MCP
        ``ask`` tool and the top-level ``tesserae ask`` command share one
        dispatcher (LLM-planned answer by default; ``llm=false`` pins
        search-only).

        ``route`` mirrors ``tesserae ask --route``. Agents are the primary
        consumer of this server, so withholding the override left them with
        only the shape heuristic while a human at a terminal got an escape
        hatch — an agent that KNOWS its question is temporal had no way to say
        so. ``ask_project`` validates the value and raises on an unknown one.
        """
        from .project import ProjectWiki
        from .query import ask_project

        project_root = self._resolve_project_root_for_ask(args)
        wiki = ProjectWiki.load(project_root)
        envelope = ask_project(
            wiki, question, top_k=top_k, use_llm=use_llm, no_llm=no_llm, route=route
        )
        # LRU: the retrieved/cited hits count as reads (single-project scope;
        # the cross-project federated/all-registered fan-outs are not bumped).
        self._bump_nodes_access(project_root, _hit_node_ids(envelope))
        return envelope

    def _mcp_ask_federated(
        self, *, question: str, scope_aliases: List[str], semantic: bool = True,
        synthesize: bool = True,
    ) -> JSONDict:
        """Federated scope — merge the named projects into ONE identity-merged
        graph and compile a single cross-referenced, cited answer (vs the
        per-project ``by_project`` fan-out). ``scope_aliases`` is required;
        ``semantic=True`` adds embedding-backed cross-project concept links."""
        from .federation import federated_recall

        if not [a for a in scope_aliases if a]:
            raise ValueError(
                "ask: scope='federated' requires scope_aliases — the projects to "
                "federate. Use list_projects to see registered projects."
            )
        return federated_recall(
            scope_aliases, question, semantic=semantic, synthesize=synthesize,
            registry=self.registry,
        )

    def _mcp_ask_all_registered(
        self,
        *,
        question: str,
        top_k: int,
        scope_aliases: List[str],
        use_llm: bool = True,
        no_llm: bool = False,
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
                # Thread the LLM knobs into the fan-out (kept in lockstep with
                # the CLI's all-registered scope).
                by_project[name] = ask_project(
                    wiki, question, top_k=top_k, use_llm=use_llm, no_llm=no_llm
                )
            except Exception as exc:
                by_project[name] = {"error": f"ask failed: {exc}"}
        return {
            "scope": "all-registered",
            "question": question,
            "by_project": by_project,
        }

    def node_context(self, graph: ResearchGraph, project_root: Optional[Path] = None, node_id: Optional[str] = None, node_name: Optional[str] = None, limit: int = 50, include_superseded: bool = False, use_ppr: bool = False, budget_chars: int = DEFAULT_BUDGET_CHARS) -> JSONDict:
        node = self._find_node(graph, node_id=node_id, node_name=node_name)
        if not node:
            raise ValueError("Node not found; provide an exact node_id or node name")
        bounded_limit = max(1, min(limit, 200))
        # CTX-01 (§5.3): per-item size clamp — the counts were already clamped
        # by ``limit``, per-item size was not. Node payloads trim their
        # description, edge payloads their evidence; neighbours AND edges each
        # go through greedy admission against ``budget_chars`` (per-item
        # clamps alone cannot bound a hub's edge payload). Drops surface as
        # one ``continuation`` line for neighbours and one
        # ``edges_continuation`` line for edges, keeping the neighbour line's
        # format stable. ``budget_chars=0`` = uncapped
        # (``_clamp_payload_item`` no-ops on ``cap <= 0``).
        per_entry_cap = budget_chars // 8 if budget_chars > 0 else 0
        suppressed = set() if include_superseded else _superseded_ids(graph)
        node_by_id = {candidate.id: candidate for candidate in graph.nodes}
        # Incident edges whose OTHER endpoint is suppressed are dropped along
        # with the suppressed neighbour itself — otherwise an edge would leak a
        # reference to a node we deliberately filtered out (consistent with
        # node suppression). The requested node is always kept even if it is
        # itself suppressed (the caller asked for it by id/name). Filter BEFORE
        # capping so the limit applies to surfaced edges, not dropped ones.
        incident_edges = [
            edge
            for edge in graph.edges
            if (edge.source == node.id or edge.target == node.id)
            and (edge.target if edge.source == node.id else edge.source)
            not in suppressed
        ][:bounded_limit]
        if use_ppr:
            # CTX-03: rank the neighbourhood via Personalized PageRank seeded
            # by the focal node (instead of the unordered 1-hop walk). This can
            # surface multi-hop nodes the strict 1-hop path cannot. Suppression
            # and self-exclusion filtering still apply, matching the default
            # path.
            #
            # Over-fetch the FULL PPR ranking (``top_k = node count``) rather than
            # ``limit + 1``: excluding the focal node AND any suppressed neighbours
            # happens BEFORE the cap, so a high-ranked superseded neighbour can no
            # longer consume one of the slots and leave fewer than ``limit`` live
            # neighbours when more live neighbours exist. Returned edges are
            # derived from the FULL edge list over the selected neighbour set (not
            # the pre-capped ``incident_edges``), so edges for selected neighbours
            # are never lost to an earlier cap.
            ppr_ranked = personalized_pagerank(
                graph, seed_ids=[node.id], top_k=max(1, len(graph.nodes)), alpha=0.15
            )
            ppr_neighbor_ids = [
                nid
                for nid, _score in ppr_ranked
                if nid != node.id and nid in node_by_id and nid not in suppressed
            ][:bounded_limit]
            ppr_neighbor_set = set(ppr_neighbor_ids)
            incident_edges = [
                edge
                for edge in graph.edges
                if (edge.source == node.id or edge.target == node.id)
                and (edge.target if edge.source == node.id else edge.source)
                in ppr_neighbor_set
            ]
            neighbors = [
                node_to_dict(node_by_id[nid]) for nid in ppr_neighbor_ids
            ]
            neighbors, continuation = _fit_payload_list(neighbors, budget_chars)
            node_payload = node_to_dict(node)
            node_payload["superseded"] = node.id in _superseded_ids(graph)
            node_payload = _clamp_payload_item(node_payload, per_entry_cap, "description")
            # LRU: the focal node AND the neighbourhood actually RETURNED are
            # reads (budget-dropped neighbours were not surfaced).
            self._bump_nodes_access(
                project_root, [node.id, *(str(n.get("id")) for n in neighbors)]
            )
            edges_out, edges_continuation = _fit_payload_list(
                [edge_to_dict(edge) for edge in incident_edges],
                budget_chars,
                text_field="evidence",
            )
            out: JSONDict = {
                "node": node_payload,
                "edges": edges_out,
                "neighbors": neighbors,
            }
            if continuation:
                out["continuation"] = continuation
            if edges_continuation:
                out["edges_continuation"] = edges_continuation
            return out
        neighbor_ids = []
        for edge in incident_edges:
            other_id = edge.target if edge.source == node.id else edge.source
            if other_id not in neighbor_ids:
                neighbor_ids.append(other_id)
        # The requested node is ALWAYS returned (the caller asked for it by
        # id/name); flag it when superseded. Neighbours are filtered unless
        # include_superseded, matching search_nodes/fresh_insights.
        neighbors = [
            node_to_dict(node_by_id[neighbor_id])
            for neighbor_id in neighbor_ids
            if neighbor_id in node_by_id and neighbor_id not in suppressed
        ]
        neighbors, continuation = _fit_payload_list(neighbors, budget_chars)
        node_payload = node_to_dict(node)
        node_payload["superseded"] = node.id in _superseded_ids(graph)
        node_payload = _clamp_payload_item(node_payload, per_entry_cap, "description")
        # KB-02/LRU: record that an agent actually read this node AND the live
        # neighbours surfaced alongside it (the nodes actually RETURNED —
        # budget-dropped neighbours were not surfaced).
        self._bump_nodes_access(
            project_root, [node.id, *(str(n.get("id")) for n in neighbors)]
        )
        edges_out, edges_continuation = _fit_payload_list(
            [edge_to_dict(edge) for edge in incident_edges],
            budget_chars,
            text_field="evidence",
        )
        out = {
            "node": node_payload,
            "edges": edges_out,
            "neighbors": neighbors,
        }
        if continuation:
            out["continuation"] = continuation
        if edges_continuation:
            out["edges_continuation"] = edges_continuation
        return out

    def _bump_node_access(self, project_root: Optional[Path], node_id: str) -> None:
        """Atomically bump access_count/last_accessed_at for a read node.

        Delegates to the SQLite sidecar accessor (05-01); writes node_memory
        only, never graph.json. Degrades silently when the project root or
        sidecar db cannot be resolved — a read must never fail because the
        memory layer is unavailable.
        """
        if project_root is None:
            return
        try:
            from datetime import datetime, timezone

            from .memory.store import bump_access as _bump_access

            db_path = project_root / ".tesserae" / "sqlite.db"
            now = datetime.now(timezone.utc).isoformat()
            _bump_access(db_path, node_id, now)
        except Exception:
            # Best-effort signal; never propagate sidecar failures to a read.
            _LOG.debug("bump_access failed for node %s", node_id, exc_info=True)

    def _bump_nodes_access(
        self, project_root: Optional[Path], node_ids: Iterable[Optional[str]]
    ) -> None:
        """Record a read of every id in ``node_ids`` (LRU access signal).

        Batch form of :meth:`_bump_node_access` for read surfaces that return a
        list of nodes (search_nodes, compile_context selection, ask/query hits,
        drill_down). Dedups ids and bumps each through the sidecar accessor
        (one connection per distinct id). Writes the ``node_memory`` sidecar
        ONLY, never ``graph.json``. Best-effort: a
        sidecar failure is logged at debug and swallowed so a read never breaks.
        """
        if project_root is None:
            return
        try:
            from datetime import datetime, timezone

            from .memory.store import bump_access as _bump_access

            db_path = project_root / ".tesserae" / "sqlite.db"
            now = datetime.now(timezone.utc).isoformat()
            seen: set[str] = set()
            for raw in node_ids:
                nid = str(raw) if raw else ""
                if not nid or nid in seen:
                    continue
                seen.add(nid)
                try:
                    _bump_access(db_path, nid, now)
                except Exception:
                    _LOG.debug("bump_access failed for node %s", nid, exc_info=True)
        except Exception:
            _LOG.debug("bump_nodes_access setup failed", exc_info=True)

    def _load_requested_graph(self, args: JSONDict) -> ResearchGraph:
        graph, _root = self._load_requested_graph_with_root(args)
        return graph

    def _load_requested_graph_with_root(self, args: JSONDict) -> Tuple[ResearchGraph, Optional[Path]]:
        graph, root = self._load_base_graph_with_root(args)
        agent = args.get("agent")
        if agent:
            if root is None:
                raise ValueError(
                    "agent= requires a project root (graph stores have none). "
                    "Pass graph_path/project or cd into a registered project."
                )
            from .agent_view import resolve_agent_view

            view, _info = resolve_agent_view(root, str(agent), graph)
            return view, root
        return graph, root

    def _load_base_graph_with_root(self, args: JSONDict) -> Tuple[ResearchGraph, Optional[Path]]:
        """Load the requested graph (with discovered-link overlay) plus its root.

        Thin wrapper over :meth:`_resolve_base_graph_with_root` that merges the
        accumulated connection-discovery overlay (``associate.apply_overlay``)
        so discovered ``shares_concept_with`` edges are traversable by every
        read surface — search/PPR/federation/agent views built on top of this
        base. In-memory ONLY (``apply_overlay`` returns a fresh graph, never
        mutating the mtime-cached instance, and never writes ``graph.json``);
        a no-op when there is no project root or no overlay on disk. Best-effort
        — an overlay failure degrades to the un-overlaid graph.
        """
        graph, root = self._resolve_base_graph_with_root(args)
        if root is not None:
            try:
                from .memory.associate import apply_overlay

                graph = apply_overlay(root, graph)
            except Exception:
                _LOG.debug("apply_overlay failed for %s", root, exc_info=True)
        return graph, root

    def _resolve_base_graph_with_root(self, args: JSONDict) -> Tuple[ResearchGraph, Optional[Path]]:
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
                    f"Compile the project first (`tesserae compile`) or "
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
        cwd_root = self.registry.resolve_project_by_cwd()
        if cwd_root is not None:
            cwd_graph = cwd_root / ".tesserae" / "graph.json"
            if cwd_graph.is_file():
                return self._load_graph_cached(cwd_graph), cwd_root
        if self.graph_store is not None:
            return _materialize_graph(self.graph_store), None
        if self.default_graph_path:
            return self._load_graph_cached(self.default_graph_path), _project_root_for_graph_path(self.default_graph_path)
        raise ValueError(
            "No graph specified. Pass graph_path or project, cd into a registered project, "
            "start the MCP server with --graph, or pass --graph-store-url."
        )

    def _mcp_graph_write(self, args: JSONDict) -> JSONDict:
        """Append one typed agent write to the project's overlay.

        The write is durable in ~1 ms and lands in ``graph.json`` on the next
        compile. ``materialize=true`` compiles now (``changed_only=True``, which
        on an unchanged corpus takes the no-op arm — no extraction, no LLM).
        """
        from .agent_write import record_agent_write
        from .locking import CompileLockHeldError
        from .project import ProjectWiki

        agent_key = str(args.get("agent") or "").strip()
        if not agent_key:
            raise ValueError("graph_write requires 'agent' (the writing agent's key)")
        project_root = self._resolve_project_root_for_ask(args)
        wiki = ProjectWiki.load(project_root)

        graph = None
        if wiki.paths.graph.exists():
            try:
                graph = self._load_graph_cached(wiki.paths.graph)
            except Exception:  # noqa: BLE001 — an unreadable graph must not block a write
                graph = None

        result = record_agent_write(
            wiki.paths.agent_writes,
            {
                "nodes": args.get("nodes") or [],
                "edges": args.get("edges") or [],
                "provenance": args.get("provenance"),
            },
            agent_key,
            graph=graph,
        )
        result["materialized"] = False
        if args.get("materialize"):
            try:
                wiki.compile(changed_only=True)
                result["materialized"] = True
            except CompileLockHeldError as exc:
                # The write already succeeded; a busy compile must not turn a
                # durable write into an error (same shape ``ingest_clip`` uses).
                result["materialize_error"] = str(exc)
        return result

    def _drill_down(self, args: JSONDict) -> JSONDict:
        """Resolve a distillate member_ref against raw L0 — audit-logged (§6.4).

        Reads the UNSCOPED L0 graph (drill-down is the explicit escalation past
        distilled visibility, so it must not itself be filtered by the agent
        view). Statuses: ``gone`` (id absent from L0), ``absorbed`` (the owning
        agent's live artifact lists it in ``absorbed_refs``), ``changed``
        (caller's content_hash no longer matches), ``alive``.
        """
        base_args = {k: v for k, v in args.items() if k in {"graph_path", "project"}}
        graph, root = self._load_base_graph_with_root(base_args)
        if root is None:
            raise ValueError("drill_down requires a project root — pass graph_path or project.")

        from .agent_view import drill_down

        # Reuse the shared, audit-logged core. Inject the server's mtime-cached
        # L1 loader so behavior stays byte-identical to the in-process cache.
        result = drill_down(
            root,
            graph,
            str(args.get("node_id") or ""),
            content_hash=str(args.get("content_hash") or ""),
            agent=str(args.get("agent") or ""),
            l1_loader=self._load_graph_cached,
        )
        # LRU: an explicit drill escalation is a read of the resolved node —
        # unless it is `gone` (absent from L0), where there is nothing to bump.
        if isinstance(result, dict) and result.get("status") != "gone":
            self._bump_nodes_access(root, [result.get("node_id")])
        return result

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
                        "serverInfo": {"name": "tesserae", "version": _package_version()},
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

    # Run on the SAME persistent loop the graph-store tool calls use
    # (CMP-04 runtime). A private asyncio.run() here binds the shared
    # SQLAlchemy engine pool's first connection to a loop that closes at
    # startup, poisoning every later tool call ("Event loop is closed" ->
    # "another operation is in progress" on the pooled connection).
    from .graph_stores.url_resolver import _runtime

    user_id = _runtime().run(_lookup())
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
