# Remove the active-project concept — design

Date: 2026-06-28
Status: proposed

> Goal: delete the registry's `active` field so "all registered projects are
> active". Replace the single active-project fallback with a deterministic
> per-project resolver (cwd path-ancestor → explicit `--project` → error) and
> let query ops default to the existing all-registered / federated scopes.

---

## 1. How ingest is handled across multiple registered projects today

There is **no concept of "the right project" for a path**. Ingest picks ONE
target wiki and dumps the content into it, regardless of where the path lives:

- **CLI `tesserae ingest`** (`tesserae/cli.py:2718,2728`): target is purely
  `--project` with `default="."` (cwd). It calls `ProjectWiki.load(args.project)`
  directly — no registry, no active project. Multi-project only works if you
  `cd` into (or pass `--project` for) each project's root.
- **MCP `ingest` tool** (`tesserae/mcp_server.py:1704`): target is resolved via
  `_resolve_project_root_for_ask(args)`, whose fallback chain is
  `graph_path` → `project` (registry alias) → `active_graph_path()` →
  `default_graph_path` (`mcp_server.py:2469-2505`). So an MCP clip with no
  explicit project lands in **whatever happens to be active**.
- **Where the bytes go** (`tesserae/ingest/orchestrator.py:16-33`,
  `tesserae/clip.py:151-156`): once a wiki is chosen, `_ensure_in_corpus` does
  `path.resolve()`, and if the path is **under** `wiki.project_root` it's used in
  place; otherwise it is `shutil.copy2`'d into `<target>/data/ingested/`. Web
  clips always write to `<wiki.project_root>/data/ingested/`.

### Sharp edges

1. **Wrong-project ingest (MCP).** With several projects registered and no
   explicit `project`, the clip silently lands in the active one — which may not
   be the project the user is looking at. There is no cwd/ancestor check.
2. **External paths get copied, not routed.** A path outside every project root
   is copied into the chosen project's `data/ingested/`. The system never asks
   "which registered project does this path belong to?" — `iter_registered_projects`
   exists (`mcp_server.py:279-285`) but is not consulted on the ingest path.
3. **CLI vs MCP divergence.** CLI ingest = cwd; MCP ingest = active fallback.
   Same logical operation, two different target-selection rules.
4. **`compile`/`status`/`serve`/`context` already use cwd** (`--project="."`,
   `cli.py:1915,1936,1958,2026`) — they never read `active`. So ingest (MCP) is
   the *only* per-project write op that depends on `active`, making it the odd
   one out.

---

## 2. Full blast radius of removing active-project

### Registry storage / `ProjectRegistry` (`tesserae/mcp_server.py`)
- `:166,172` — `load()` seeds `{"version":1,"active":None,"projects":{}}` and
  `setdefault("active", None)`. Drop the `active` key.
- `:193-200` — `activate()` method. **Delete.**
- `:202-210` — `unregister()` clears `active` if matched. Remove the cleanup branch.
- `:212-220` — `list_projects()` returns `{"active": ..., "projects": [...]}`.
  Drop `active` from the payload.
- `:227-233` — `active_graph_path()` method. **Delete** (callers rewired in §3).

### MCP resolution chains (`tesserae/mcp_server.py`)
- `:2493-2497` — `_resolve_project_root_for_ask()` active fallback. Remove;
  rewrite per §3 (ask → all-registered default; ingest → cwd-ancestor).
- `:2727-2735` — `_load_requested_graph_with_root()` active fallback. Remove;
  read tools must take explicit `project`/`graph_path` or hit `default_graph_path`.
- `:2502-2505, 2740-2743` — error strings that say "activate a project". Reword.

### MCP tool surface (`tesserae/mcp_server.py`)
- `:708-715` — `activate_project` tool schema. **Delete.**
- `:1578-1582` — `activate_project` call handler. **Delete.**
- `:448,449,691,709,735,976` — tool/`graph_path` descriptions referencing
  "active project". Reword.
- `:1157-1183,1213-1231,1238-1273` — resource templates / `read_resource` for
  `tesserae://graph/summary` and `tesserae://lint-report` described as "Active
  project — …" and wired by calling tools with empty args (relying on active).
  Decide: drop these resources, or bind them to `default_graph_path` only.
- `:1298-1369` — prompts ("the active Tesserae project"). Reword.

### CLI (`tesserae/cli.py`)
- `:321-344` — `_top_level_ask_handler` active fallback branch. Replace with the
  new default (all-registered) per §3.
- `:325-330` — error message naming `projects activate`. Reword.
- `:519-533` — `projects list` prints `Active:` line and `*` marker. Drop.
- `:542-549` — `register --activate` branch in `_wiki_command_handler`. Delete.
- `:551-558` — `activate` branch in `_wiki_command_handler`. **Delete.**
- `:3276-3278` — `_handle_projects_activate` dispatcher. **Delete.**
- `:3317` — `register --activate` flag. **Delete.**
- `:3324-3326` — `projects activate` subparser. **Delete.**
- `:3294,3300,3320` — `projects` help/epilog mentioning activate. Reword.
- `:726-727,775` — top-level `ask` parser description/scope help mentioning
  "active project". Reword.

### MCP client surface (`mcp__plugin_tesserae_tesserae__activate_project`)
- The exposed MCP tool `activate_project` disappears. Breaking change for any
  client that calls it.

### Tests
- `tests/test_mcp_registry.py:71-74` (`active` is None for empty),
  `:165-174` (`test_activate_project_sets_active_in_registry`),
  `:177-180` (`activate_project_unknown_name_raises`),
  `:198-207` (`unregister_active_project_clears_active`),
  `:225-233` (`tool_call_falls_back_to_active_project`).
- `tests/test_cli_top_level_ask.py:95-114` (`uses_active_project_when_no_args`),
  `:117-127` (error mentions active), `:169-184` (list shows `Active:`/`*`),
  `:187-202` (`payload['active']` is None), `:226-240` (`wiki_activate_command`).
- `tests/test_mcp_ingest.py:1-5,120-126` (docstring "active project"; asserts
  ValueError when no project resolvable — keep but update message/behaviour).
- `tests/conftest.py:63-79` — test isolation fixtures for the registry/active.

### Registry migration
- Existing `~/.tesserae/registry.json` files contain an `active` key. `load()`
  should simply ignore an unknown `active` key (don't crash); no rewrite needed
  unless `save()` is called, which naturally drops it. Backward-compatible read.

---

## 3. Resolution model (replacement for active-project)

Two operation classes, two rules.

### A. Per-project ops — `compile`, `ingest`, `status`, `serve`, `context`
These act on exactly one project. Resolution order:

1. **Explicit `--project <path>` / `project` arg** (registry alias or path) —
   highest priority, unchanged.
2. **cwd path-ancestor match** — walk `Path.cwd()` upward; the nearest ancestor
   that is a registered project root (or simply contains `.tesserae/graph.json`)
   wins. This is the new default and replaces both the CLI `default="."` literal
   and the MCP active fallback. A new helper:

   ```
   def resolve_project_by_cwd(registry, start=None) -> Path | None:
       """Nearest ancestor of `start` (default cwd) that is a registered
       project root; fall back to nearest ancestor containing
       .tesserae/graph.json. Return None if none found."""
   ```

   Matching against **registered roots** (via `iter_registered_projects`,
   `mcp_server.py:279-285`) is what makes "all registered projects are active":
   you're always operating on the registered project you're standing in.
3. **Ambiguity / none → clear error.** If cwd is under *two* registered roots
   (nested projects), error and require `--project`. If under none and no
   `--project`, error: "Not inside a registered Tesserae project. Pass --project
   <path> or cd into one. Registered: a, b, c."

For **ingest specifically**, also use the *path being ingested* as a tiebreaker:
when an input path is under exactly one registered root, route the ingest to that
project (closing sharp-edge #2). cwd is the fallback when the path is external.

Today `compile/status/serve/context` use `default="."` which already means "cwd
as a literal path" — upgrading them to `resolve_project_by_cwd` is mostly a
no-op for the common case (you're in the project root) but adds ancestor-walk and
a real error instead of a confusing `FileNotFoundError`.

### B. Query ops — `ask`, `context` (read), MCP read tools
These can legitimately span projects, and the machinery already exists:
`--scope current|all-registered|federated` in the CLI
(`cli.py:415-508,376-412`) and the `scope` arg in MCP `ask`
(`mcp_server.py:1543-1591`).

- **`ask` with no `--project`/`--wiki`/`--scope`** → default to
  **`all-registered`** (fan-out over every registered project). This *is* the
  literal meaning of "all registered projects are active" and reuses
  `_top_level_ask_scope_all_registered` / `_mcp_ask_all_registered` unchanged.
- `--scope current` keeps single-project semantics but now resolves the single
  project via the §3A rule (cwd-ancestor → `--project`) instead of `active`.
- `federated` is unchanged (requires explicit `--scope-aliases`).
- MCP read tools (`search_nodes`, `graph_summary`, …) that call
  `_load_requested_graph_with_root` with no `project`: since they each return one
  graph, default them to the cwd-ancestor project (server-side cwd is the
  process cwd) and fall back to `default_graph_path`; if neither resolves, raise
  the reworded "pass project/graph_path" error. (They cannot fan out without a
  response-shape change, so they stay single-graph.)

### Reconciliation with existing `--scope`
Nothing new is invented for query ops — we only **change the default** from
`current`(+active) to `all-registered`, and re-point `current`'s single-project
lookup at the cwd-ancestor resolver. `all-registered` and `federated` already
ignore `active` entirely, so they are untouched.

---

## 4. The one design decision that needs the user

**What should bare `tesserae ask "q"` (no flags, no scope) default to?**

- Option A — **all-registered fan-out** (recommended): matches "all registered
  projects are active" literally; reuses existing code; but a bare question now
  queries every project (slower, noisier) and changes long-standing single-answer
  behaviour.
- Option B — **cwd-ancestor single project**, error if not inside one: keeps
  `ask` cheap and local, symmetric with compile/ingest/status; but then "all
  projects active" is only true for explicit `--scope all-registered`.

This is the only genuine fork — everything else (deleting `activate`, the cwd
resolver for per-project ops, federated) is mechanical.

---

## 5. Staged implementation plan

1. **Add the resolver.** Implement `resolve_project_by_cwd(registry, start)` and
   an ingest path-tiebreaker helper in `tesserae/mcp_server.py` (next to
   `ProjectRegistry`), with unit tests for: in-root, ancestor, nested-ambiguous
   (error), and not-found (None). No behaviour change yet.
2. **Rewire per-project ops to the resolver.** Replace `--project default="."`
   handling in `compile/status/serve/context/ingest` CLI handlers
   (`cli.py:1135,1584,2041,2115,2728`) to resolve via cwd-ancestor when
   `--project` is omitted, with the explicit-ambiguity error. Update MCP `ingest`
   (`mcp_server.py:1704`) to use the new resolver + path tiebreaker instead of
   `_resolve_project_root_for_ask`.
3. **Change query defaults.** Make bare `ask` use the §4-chosen default (CLI
   `_top_level_ask_handler` `cli.py:321-344`; MCP ask `mcp_server.py:1543-1591`).
   Re-point `--scope current` single-project lookup at the new resolver. Update
   `_load_requested_graph_with_root` (`:2727-2735`) and
   `_resolve_project_root_for_ask` (`:2493-2497`) to drop the active fallback.
4. **Delete the active machinery.** Remove `activate()`/`active_graph_path()`
   (`mcp_server.py:193-200,227-233`), the `active` key from `load()`/
   `list_projects()` (`:166,172,212-220`), the unregister cleanup (`:207-208`),
   the `activate_project` MCP tool+handler (`:708-715,1578-1582`), and the CLI
   `projects activate` subparser/handler + `register --activate`
   (`cli.py:551-558,3276-3278,3317,3324-3326,542-549`).
5. **Reword copy.** All "active project" strings in tool descriptions, resource
   templates, prompts, `projects list` output, and `ask` help
   (`mcp_server.py:448,691,735,976,1157-1273,1298-1369`;
   `cli.py:519-533,726-727,775,3294-3320`).
6. **Migration safety.** Confirm `load()` ignores a stray `active` key in old
   registries; `save()` naturally drops it. No destructive migration.
7. **Tests.** Delete/rewrite the active-coupled tests (§2), add tests for the
   cwd-ancestor resolver, ambiguity error, ingest path-routing, and the new bare
   `ask` default. Update `tests/conftest.py:63-79` fixtures to stop seeding
   `active`.
8. **Docs.** Update AGENTS.md / any user docs that mention `projects activate`.
