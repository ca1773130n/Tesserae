# Federation MVP — file-by-file build spec

**Date:** 2026-06-26
**Scope (approved design):** identity-merge links only (no embeddings) + federated
recall (assemble a namespaced + merged `ResearchGraph` from selected registered
projects, run `compile_context` over it) + `ask --scope federated` (CLI) and
`scope:"federated"` (MCP `ask`) with project selection.
**Invariants:** determinism (byte-stable output for a fixed registry + question);
per-project `graph.json` files are read-only — never written by federation code.

---

## 0. Findings that CONTRADICT the design — resolve before coding

1. **`compile_context(project_root=...)` takes ONE root; federation has N.**
   `compile_context` (`tesserae/context_compiler.py:143`) uses `project_root` only
   to load wiki page bodies from `<root>/.tesserae/wiki/<kind>/<slug>.md`. With
   multiple projects there is no single root. **Resolution (per the design's own
   gotcha):** call `compile_context(graph=federated, project_root=None, ...)`. Node
   bodies come from `node.description`, which is byte-stable and project-agnostic.
   The MVP does NOT serve federated wiki bodies. Documented, not a blocker.

2. **`merge_graphs` is the WRONG merge for federation.**
   `merge_graphs` (`tesserae/project.py:3029`) keys nodes by `node.id` and runs
   `merge_same_type_aliased_duplicates` + `merge_cross_type_duplicates` +
   `link_paper_repo_pairs`. Those passes do **fuzzy name-similarity** dedup — they
   are non-deterministic across project ordering and will collapse unrelated nodes
   that merely share a casefolded name. The design explicitly says *identity-merge
   links only*. **Resolution:** do NOT call `merge_graphs`. We write our own
   namespacing + identity-key union-find (Section 2) that merges ONLY on the
   verified identity-key table, leaving everything else namespaced and distinct.

3. **`stable_id` collisions are explicitly unsafe for cross-project matching.**
   Finding 3 gotcha: `ResearchNode.id` is stable *within* a project but differs
   across projects for the same entity, and `stable_id` must NOT be used to match.
   **Resolution:** matching is driven exclusively by the identity-key table
   (Section 3), never by raw `id` equality. Namespacing (Section 1) guarantees raw
   ids never collide across projects by accident.

4. **`prefer_research_node` drops provenance we need.**
   `prefer_research_node` (`research_graph.py:392`) merges metadata with
   `{**other.metadata, **chosen.metadata}` and picks one node's `source_path`.
   For federation we must record WHICH projects a merged node came from. We wrap it
   (Section 2 `_merge_two`) to additionally union a `federation_members` metadata
   list. We reuse its title-quality logic; we do not bypass it.

5. **CLI `--scope-aliases` is currently bound to `all-registered` only.**
   In `_build_top_level_ask_parser` (`cli.py:729`) the help text ties
   `--scope-aliases` to `all-registered`. Federated reuses the same arg. Update the
   help text; no new arg needed. For federated, an **empty** `--scope-aliases` is an
   error (design: "requires scope_aliases OR defaults to current"). We choose
   **error** for explicitness; documented in Section 4.

---

## 1. NEW FILE — `tesserae/federation.py`

The whole feature's engine. Pure functions, no disk writes, deterministic.

### 1.1 `namespace_graph`

```python
def namespace_graph(graph: ResearchGraph, alias: str) -> ResearchGraph:
    """Return a copy of `graph` with every node id prefixed `f"{alias}::{id}"`
    and every edge source/target rewritten to match. Records the origin alias
    in node.metadata['federation_alias'] and node.metadata['federation_origin_id'].
    Pure: input graph (frozen nodes/edges) is never mutated."""
```

- New id form: `f"{alias}::{node.id}"` (alias comes from registry, already sanitized
  by `_sanitize_project_name`). `::` is the namespace separator — chosen because it
  never appears in `stable_id` output (`Type:slug:sha1`).
- Build `id_map = {old_id: f"{alias}::{old_id}"}`. Reconstruct each `ResearchNode`
  (frozen — must rebuild) with new `id`, and `metadata={**n.metadata,
  "federation_alias": alias, "federation_origin_id": n.id}`.
- Reconstruct each `ResearchEdge` with `source=id_map[e.source]`,
  `target=id_map[e.target]`. Drop any edge whose endpoint is missing from `id_map`
  (defensive; shouldn't happen with a valid graph.json).
- Return `ResearchGraph(nodes=[...], edges=[...])`. Do NOT sort here — ordering is
  fixed deterministically in `federate_graphs`.

### 1.2 `identity_key`

```python
def identity_key(node: ResearchNode) -> Optional[tuple]:
    """Return a hashable cross-project identity key for `node`, or None if the
    node has no safe identity field (then it stays distinct per project).
    Uses ONLY verified metadata fields — never stable_id, never raw name for
    weak types. See the identity-key table (Section 3)."""
```

- Returns e.g. `("Paper", arxiv_id)` or `("Repository", github_repo_norm, repo_url_norm)`.
  Returns `None` for any node not covered by the table → node is never merged.
- Normalization helpers (module-private):
  - `_norm_repo_url(u)`: `u.strip().lower().rstrip("/")`, strip scheme variance
    (`http://`→`https://`).
  - `_norm_github_repo(r)`: `r.strip().lower().rstrip(".")`.

### 1.3 `federate_graphs` — the core assembler

```python
def federate_graphs(
    named_graphs: Sequence[tuple[str, ResearchGraph]],
) -> tuple[ResearchGraph, dict]:
    """Assemble a namespaced + identity-merged federated graph.

    1. Namespace each (alias, graph) via namespace_graph.
    2. Concatenate all namespaced nodes/edges (stable order: by alias asc,
       then node id asc within each graph).
    3. Union-find over nodes that share an identity_key (Section 2 algorithm).
    4. Re-point every edge endpoint to its cluster representative.
    5. Collapse cluster members into one node via _merge_two (chained).
    6. Return (ResearchGraph(canonicalized), stats) where stats =
       {"projects": [...], "nodes_in": int, "nodes_out": int,
        "merged_clusters": int, "edges_in": int, "edges_out": int}.

    Pure; deterministic given the same named_graphs order."""
```

- Final graph built as `ResearchGraph(nodes=sorted_nodes, edges=sorted_edges)`;
  `ResearchGraph.model_dump()` already sorts (nodes by id, edges by
  `(source,type,target)`) so byte-idempotence holds — but we sort defensively too.
- `named_graphs` MUST be passed alias-sorted by the caller (recall layer sorts).

---

## 2. Union-find merge + edge re-point algorithm (in `federation.py`)

Deterministic, identity-key-only. No fuzzy matching.

```
INPUT:  nodes: list[ResearchNode]   (already namespaced, stable order)
        edges: list[ResearchEdge]   (already namespaced)

# --- union-find over identity keys ---
parent: dict[str, str] = {n.id: n.id for n in nodes}       # node-id -> node-id
def find(x):  # iterative path-compression
    root = x
    while parent[root] != root: root = parent[root]
    while parent[x] != root: parent[x], x = root, parent[x]
    return root
def union(a, b):
    ra, rb = find(a), find(b)
    if ra == rb: return
    # deterministic representative: smaller id string wins
    lo, hi = (ra, rb) if ra < rb else (rb, ra)
    parent[hi] = lo

key_to_first: dict[tuple, str] = {}                         # identity_key -> first node id seen
for n in nodes:                                            # nodes are in stable order
    k = identity_key(n)
    if k is None: continue
    if k in key_to_first: union(key_to_first[k], n.id)
    else: key_to_first[k] = n.id

# --- collapse clusters: members -> representative node ---
clusters: dict[str, list[ResearchNode]] = defaultdict(list)
for n in nodes: clusters[find(n.id)].append(n)
rep_node: dict[str, ResearchNode] = {}
for root, members in clusters.items():
    members.sort(key=lambda n: n.id)                       # deterministic fold order
    merged = members[0]
    for m in members[1:]: merged = _merge_two(merged, m)
    # force merged.id == root so edge re-point lands on it
    merged = replace(merged, id=root)
    rep_node[root] = merged

# --- re-point edges to cluster representatives ---
seen: dict[tuple[str,str,str], ResearchEdge] = {}
for e in edges:
    s, t = find(e.source), find(e.target)
    if s == t: continue                                    # drop self-loops created by merge
    ne = replace(e, source=s, target=t)
    seen[(ne.source, ne.type, ne.target)] = ne             # dedup identical edges post-merge

OUTPUT: nodes = sorted(rep_node.values(), key=lambda n: n.id)
        edges = [seen[k] for k in sorted(seen)]
```

`_merge_two(a, b)`:
```python
def _merge_two(a: ResearchNode, b: ResearchNode) -> ResearchNode:
    merged = prefer_research_node(a, b)          # reuse title-quality + alias logic
    members = sorted(set(
        _members(a) + _members(b)                # metadata['federation_alias'] of each
    ))
    return replace(merged, metadata={**merged.metadata, "federation_members": members})
```
- `replace` = `dataclasses.replace` (nodes/edges are frozen dataclasses).
- `prefer_research_node` already unions aliases and merges metadata; we only add the
  `federation_members` provenance list on top.
- Representative id = lexicographically-smallest member id (the union root). Stable
  regardless of input project ordering because union always keeps the smaller id.

---

## 3. Identity-key extraction table (drives `identity_key`)

Only these types merge across projects. Everything else → `identity_key` returns
`None` → stays namespaced and distinct. Keys are taken from the verified findings
(Finding 3) — metadata fields only, never `stable_id`, never `node.id`.

| Node type (`ResearchNodeType`) | Merge key (tuple) | Source field(s) | Guard / normalization |
|---|---|---|---|
| `PAPER` | `("Paper", arxiv_id)` | `metadata["arxiv_id"]` | only if truthy non-empty; else `None` |
| `REPOSITORY` | `("Repository", gh_norm, url_norm)` | `metadata["github_repo"]`, `metadata["repo_url"]` | both must be truthy; `_norm_github_repo`, `_norm_repo_url` |
| `SOURCE_DOCUMENT` | `("SourceDocument", content_hash)` | `metadata["content_hash"]` | only if present (finding: SourceDocument often lacks it → then `None`) |
| any `CODE_*` type | `(type.value, source_path, qualified_name)` | `node.source_path`, `metadata["qualified_name"]` | both truthy; NB cross-project file paths rarely match → merges only true shared files |
| all other types | — | — | `None` (no auto-merge; risky per findings: Person, generic Concept) |

- `arxiv_id` is reliable ONLY for `PAPER` (finding gotcha). Do not read it on
  `REPOSITORY`.
- `content_hash` is a short 12-char hash → secondary only; we still merge on it for
  `SOURCE_DOCUMENT` because it's the only stable cross-project signal, but it is
  gated behind exact type match so collision blast-radius is one type.
- This table lives as a `dict`/`if`-ladder in `identity_key`; adding a type later is
  a one-line change.

---

## 4. NEW — federated recall + `compile_context` glue (in `federation.py`)

```python
def load_federated_graph(
    aliases: Sequence[str],
    *,
    registry=None,
) -> tuple[ResearchGraph, dict]:
    """Resolve `aliases` against the registry, load each graph.json read-only,
    namespace + identity-merge them, return (federated_graph, stats).

    Raises ValueError on unknown aliases or empty selection."""
```

Implementation:
```python
from .mcp_server import ProjectRegistry
from .project import load_graph_file

registry = registry or ProjectRegistry()
data = registry.list_projects()                      # {"active":..., "projects":[...]}
by_name = {p["name"]: p for p in (data.get("projects") or [])}
wanted = [str(a) for a in aliases if a]
if not wanted:
    raise ValueError("federated scope requires at least one project alias")
missing = [a for a in wanted if a not in by_name]
if missing:
    raise ValueError(f"Unknown scope alias(es): {sorted(missing)}")

named: list[tuple[str, ResearchGraph]] = []
for alias in sorted(wanted):                         # sorted -> deterministic order
    entry = by_name[alias]
    gp = entry.get("graph_path") or ""               # registry already has graph_path
    g = load_graph_file(gp)                           # READ-ONLY; never written
    named.append((alias, g))

return federate_graphs(named)                         # Section 1.3
```

- Uses `registry.list_projects()` (`mcp_server.py:212`) → each entry carries
  `graph_path`, so we load directly from `graph_path` and never need
  `ProjectWiki.load` (which would require `config.json` and is heavier). This also
  sidesteps Finding 1's `ProjectWiki.load` FileNotFoundError gotcha.
- If `graph_path` missing on an entry, infer from `root`/`graph_path` per the
  standard fallback (`gp.parent.parent if .tesserae else gp.parent`) — but registry
  entries always carry `graph_path` (see `register`, `mcp_server.py:182`), so this is
  defensive only.

```python
def federated_recall(
    aliases: Sequence[str],
    query: str,
    *,
    depth: int = 2,
    budget: int = 64_000,
    synthesize: bool = False,
    registry=None,
) -> JSONDict:
    """Federated recall: assemble graph, compile a cited bundle, return envelope."""
    graph, stats = load_federated_graph(aliases, registry=registry)
    from .context_compiler import compile_context
    bundle = compile_context(
        graph,
        project_root=None,            # Finding 1: no single root; bodies from node.description
        query=query,
        depth=depth,
        budget=budget,
        synthesize=synthesize,
    )
    return {
        "scope": "federated",
        "question": query,
        "projects": stats["projects"],
        "stats": stats,
        "body": bundle.body,
        "citations": [asdict(c) for c in bundle.citations],
        "selected_node_ids": list(bundle.selected_nodes),
        "char_budget_used": bundle.char_budget_used,
        "synthesized": bundle.synthesized,
    }
```

- `compile_context` real signature (`context_compiler.py:143`):
  `compile_context(graph, project_root=None, query="", seeds=None, depth=2,
  budget=32_000, synthesize=False, backend=None, multi_pool=False) -> ContextBundle`.
  We pass `backend=None` (auto-resolves; hash stub always available, no embeddings —
  matches "no embeddings" MVP constraint since synthesize defaults off and the hash
  lane needs no deps) and leave `multi_pool=False` (byte-identical path).
- `synthesize=False` by default keeps output deterministic (Finding: deterministic
  without timestamp when `synthesize=False`).

---

## 5. MODIFIED — `tesserae/cli.py`

### 5.1 `_build_top_level_ask_parser` (line 729–746)
- Change `--scope` choices: `["current", "all-registered"]` →
  `["current", "all-registered", "federated"]`.
- Update `--scope` help to add: `'federated' assembles a merged graph across the
  projects named in --scope-aliases and runs compile_context over it`.
- Update `--scope-aliases` help: drop "When --scope=all-registered" → "When
  --scope=all-registered (filter) or --scope=federated (selection, required)".

### 5.2 `_top_level_ask_handler` (line 290–292)
Add a branch alongside the existing one:
```python
scope = getattr(args, "scope", "current") or "current"
if scope == "all-registered":
    return _top_level_ask_scope_all_registered(args)
if scope == "federated":
    return _top_level_ask_scope_federated(args)   # NEW
```

### 5.3 NEW `_top_level_ask_scope_federated(args) -> int` (after `_top_level_ask_scope_all_registered`, ~line 468)
```python
def _top_level_ask_scope_federated(args) -> int:
    from .federation import federated_recall
    aliases = list(getattr(args, "scope_aliases", None) or [])
    if not aliases:
        print(
            "--scope federated requires --scope-aliases <name> [<name> ...]. "
            "Use `tesserae projects list` to see registered projects.",
            file=sys.stderr,
        )
        return 2
    try:
        envelope = federated_recall(
            aliases,
            args.question,
            synthesize=bool(getattr(args, "llm", False)),
        )
    except ValueError as exc:        # unknown alias / empty selection
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"federated ask failed: {exc}", file=sys.stderr)
        return 2

    if bool(args.json_output):
        print(json.dumps(envelope, ensure_ascii=False, indent=2))
        return 0
    # Human render: banner + bundle body (compile_context already cites).
    print(f"Federated scope · projects: {', '.join(envelope['projects'])}")
    print(f"question: {args.question!r}\n")
    print(envelope["body"])
    return 0
```
- Note federated returns a **single cited bundle** (not a `by_project` map like
  all-registered) — that's the design: one merged graph → one answer. So it does NOT
  reuse `_emit_ask_envelope` (which expects an `ask_project` envelope shape).

---

## 6. MODIFIED — `tesserae/mcp_server.py`

### 6.1 `list_tools` ask schema (line 660–665)
- `scope.enum`: `["current","all-registered"]` →
  `["current","all-registered","federated"]`.
- Append to `scope.description`: `"; 'federated' merges the graphs named in
  scope_aliases into one graph and runs compile_context, returning a single cited
  bundle."`
- `scope_aliases.description`: note it is **required** when `scope='federated'`.

### 6.2 `call_tool` ask dispatch (line 1546–1559)
```python
scope = str(args.get("scope") or "current")
if scope not in {"current", "all-registered", "federated"}:   # add federated
    raise ValueError(f"ask: unknown scope {scope!r}")
...
with _claude_config_dir_override(claude_config_dir):
    if scope == "all-registered":
        return self._mcp_ask_all_registered(...)
    if scope == "federated":                                  # NEW
        aliases = _coerce_str_list(args.get("scope_aliases"))
        if not aliases:
            raise ValueError("ask: scope='federated' requires scope_aliases")
        return self._mcp_ask_federated(question=question, scope_aliases=aliases)
    return self._mcp_ask(args, question=question, backend=backend, top_k=top_k)
```

### 6.3 NEW `LLMWikiMCPServer._mcp_ask_federated` (after `_mcp_ask_all_registered`, ~line 2565)
```python
def _mcp_ask_federated(self, *, question: str, scope_aliases: List[str]) -> JSONDict:
    """Federated recall over the registry subset named by scope_aliases.
    Returns a single cited bundle envelope (scope='federated')."""
    from .federation import federated_recall
    return federated_recall(scope_aliases, question, registry=self.registry)
```
- Reuses the server's `self.registry` (a `ProjectRegistry`) — passed through so it
  honors any custom registry path (Finding: registry reloads from disk each call,
  fine for determinism).
- `backend`/`top_k` are not meaningful for federated (no per-project `ask_project`
  fan-out); ignored, as `compile_context` is the retriever. The MCP `ask` validates
  `backend` upstream (line 1543) which is harmless.

---

## 7. Tests to write — `tests/test_federation.py` (NEW)

Unit (pure, no registry):
1. `test_namespace_graph_prefixes_ids_and_edges` — ids become `alias::id`, edges
   re-pointed, `federation_alias` set, original graph unmutated.
2. `test_identity_key_paper_arxiv` / `_repository` / `_source_document_content_hash`
   / `_code_symbol` / `_returns_none_for_concept` — table coverage incl. None cases.
3. `test_identity_key_repo_url_normalization` — trailing slash / scheme / case fold.
4. `test_federate_merges_same_arxiv_across_two_projects` — same paper in p1+p2 →
   one node, `federation_members == ["p1","p2"]`, aliases unioned.
5. `test_federate_keeps_distinct_concepts_separate` — same-named Concept in two
   projects stays TWO nodes (no fuzzy merge — guards against Contradiction #2).
6. `test_edge_repoint_to_representative` — edge from p1's paper-dup lands on the
   merged representative; self-loops dropped; duplicate edges collapsed.
7. `test_representative_id_is_lexicographically_smallest` — independent of input
   project order (run `federate_graphs` with both orderings → identical output).
8. `test_federate_determinism_byte_identical` — `federate_graphs(...).to_json()`
   equal across two runs AND across reversed input order (the determinism invariant).
9. `test_per_project_graph_untouched` — load a graph.json, federate, assert the
   file's bytes on disk are unchanged (read-only invariant).

Integration (tmp registry with 2 fixture projects):
10. `test_load_federated_graph_unknown_alias_raises` — `ValueError` lists missing.
11. `test_load_federated_graph_empty_aliases_raises`.
12. `test_federated_recall_envelope_shape` — keys: `scope=="federated"`, `projects`,
    `stats`, `body`, `citations`, `selected_node_ids`, `synthesized==False`.
13. `test_federated_recall_body_cites_both_projects` — a query hitting nodes from
    both projects yields citations referencing merged + namespaced nodes.

CLI (`tests/test_cli_ask_scope.py`, extend existing):
14. `test_cli_ask_scope_federated_requires_aliases` — exit 2 + stderr message.
15. `test_cli_ask_scope_federated_unknown_alias` — exit 2.
16. `test_cli_ask_scope_federated_json_envelope` — `--json` prints
    `scope=="federated"` bundle.
17. `test_parser_scope_choices_include_federated`.

MCP (`tests/test_mcp_ask.py`, extend existing):
18. `test_mcp_ask_scope_federated_requires_aliases` — `ValueError`.
19. `test_mcp_ask_scope_federated_unknown_scope_rejected` still rejects typos.
20. `test_mcp_ask_schema_enum_has_federated` — schema enum matches validation set
    (guards Finding: enum/branch drift).
21. `test_mcp_ask_federated_returns_bundle` — envelope shape from `_mcp_ask_federated`.

---

## 8. Determinism checklist (CI gate)

- `federate_graphs` output sorted (nodes by id, edges by `(source,type,target)`);
  `ResearchGraph.model_dump()` re-sorts → byte-idempotent.
- No wall-clock / random / set-iteration leakage: union representative = min id;
  cluster fold order = sorted member ids; `federation_members` sorted; aliases
  sorted (via `prefer_research_node`).
- `synthesize=False` default → no LLM, no timestamp in body.
- `project_root=None` → no filesystem dependence on per-project wiki dirs.
- Test #8 asserts byte-identical across reversed input order — the strongest guard.

## 9. Out of scope (explicitly NOT in MVP)

- Embeddings / semantic cross-project matching (design: identity links only).
- Federated wiki page bodies / `wiki://` cross-project URI resolution
  (`tesserae/cross_project.py` untouched).
- Writing a merged graph to disk / a federated `graph.json`.
- Per-project `ask_project` fan-out under federated (that's `all-registered`).
