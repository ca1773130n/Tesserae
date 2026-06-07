# Obsidian — open the compiled wiki as a real vault

<!-- translations:start -->
<p align="center"><a href="../i18n/integrations/obsidian.ko.md">한국어</a> · <a href="../i18n/integrations/obsidian.zh.md">中文</a> · <a href="../i18n/integrations/obsidian.ja.md">日本語</a> · <a href="../i18n/integrations/obsidian.ru.md">Русский</a> · <a href="../i18n/integrations/obsidian.es.md">Español</a> · <a href="../i18n/integrations/obsidian.fr.md">Français</a> · <a href="../i18n/integrations/obsidian.de.md">Deutsch</a></p>
<!-- translations:end -->

Tesserae's Obsidian export turns your compiled typed graph into a real, opinionated [Obsidian](https://obsidian.md) vault. Not a directory of markdown — a vault with `.obsidian/` config, type-aware [callouts](https://help.obsidian.md/Editing+and+formatting/Callouts), [Dataview](https://blacksmithgu.github.io/obsidian-dataview/)-queryable frontmatter, a vault dashboard, and an index of cross-vault `wiki://` references.

## Prerequisites

Compile the project first:

```bash
cd /path/to/your-project
tesserae init
tesserae compile
```

The compile produces `.tesserae/graph.json` (the source of truth) and a plain markdown projection at `.tesserae/markdown_projection/`. The Obsidian export is built on top of that projection but layers Obsidian-native enrichments on every page.

## 1) Export the vault

```bash
tesserae vault export --vault ~/Documents/tesserae-vault
```

The directory is created if it doesn't exist. Re-running overwrites it idempotently — the markdown projection is deterministic given the same graph.

What lands on disk:

```text
tesserae-vault/
  .obsidian/                  # Obsidian config (app.json, graph.json, plugins)
  README.md                   # Vault entry point
  index.md                    # All nodes grouped by section
  _bridges.md                 # Cross-vault wiki:// references, grouped by alias
  _meta/
    dashboard.md              # Dataview overview tables
  papers/                     # Paper / Repository / SourceDocument pages
  concepts/                   # Concept / Topic / Field / Method / Algorithm pages
  claims/                     # Claim / OpenQuestion / Evidence pages
  raw/                        # Optional raw-source attachments (created lazily)
```

## 2) Open the directory in Obsidian

`File → Open vault... → Open folder as vault → ~/Documents/tesserae-vault`.

Obsidian will detect `.obsidian/`, recognize it as a real vault, and load. The community-plugins list includes Dataview, so Obsidian will prompt to enable it (recommended — without it the dataview blocks render as code fences).

`Settings → Community plugins → Browse → "Dataview" → Install → Enable`.

## 3) Tour the vault

### Entry points

- `README.md` — what this vault is and how to refresh it
- `index.md` — every node by section (papers, concepts, claims) with wikilinks
- `_meta/dashboard.md` — dataview overview: recent pages, papers, concepts/claims

### Per-page enrichments

Every node page now ships with:

**Type-aware callouts.** A semantic callout at the top of each page makes the node type visible at a glance:

```markdown
> [!quote] Paper
> The paper triggered a wave of follow-on work: SuGaR aligns Gaussians...

> [!warning] Limitation
> No current method can achieve real-time display rates at 1080p...

> [!question] Open question
> How does dynamic-scene reconstruction scale...
```

Mapping (highlights): `Paper → quote`, `Repository → info`, `Contribution → success`, `Performance → info`, `Limitation → warning`, `Causal → important`, `OpenQuestion → question`, `Evidence → example`.

**Dataview-queryable edges.** Frontmatter now carries the typed edges as nested maps:

```yaml
edges_out:
  uses: [gaussian-splatting, volumetric-rendering]
  part_of: [3d-4d-vision-and-reconstruction]
  supports_claim: [performance-claim-..., comparison-...]
edges_in:
  mentioned_in: [project-pulse, topic-visual-slam]
```

You can write queries like:

````markdown
```dataview
LIST FROM "papers" WHERE contains(edges_out.uses, "nerf")
```

```dataview
TABLE edges_out.supports_claim AS "Claims"
FROM "papers"
WHERE length(edges_out.supports_claim) > 3
SORT length(edges_out.supports_claim) DESC
LIMIT 10
```
````

**Cross-vault bridges.** Any `wiki://<alias>/<kind>/<slug>` URI mentioned in a node's description or metadata is surfaced both as a frontmatter field:

```yaml
cross_vault: [wiki://research/concepts/rlhf, wiki://notes/papers/arxiv-2510-12323]
```

and as a `Cross-vault references` body section. The vault-level `_bridges.md` index aggregates every outbound reference grouped by destination alias, so you can audit cross-vault links from a single page.

**Related (dataview) block.** Every page ends with a query that shows pages linking back, populated automatically:

````markdown
```dataview
LIST
FROM "papers" OR "concepts" OR "claims"
WHERE contains(file.outlinks, this.file.link) AND file.name != this.file.name
SORT file.name
LIMIT 25
```
````

### Vault dashboard

`_meta/dashboard.md` ships dataview blocks for the most useful aggregate views: recently-updated pages, all papers with metadata columns, all concepts and claims sorted by type. Edit it freely — it's a starting point, not a fixed contract.

### Vault graph view

Obsidian's built-in graph view (`Ctrl/Cmd+G`) already works against the wikilinks emitted in `## Outgoing` / `## Incoming` sections. The pre-shipped `.obsidian/graph.json` colour-codes `papers/`, `concepts/`, `claims/` paths for orientation. You can layer dataview-filtered views on top for richer slices.

## Cross-vault workflows

Register multiple Tesserae vaults so `wiki://` URIs resolve across them:

```bash
tesserae register-project /path/to/research --name research
tesserae register-project /path/to/notes    --name notes
```

Re-export each vault after registration. `_bridges.md` in each export will now show resolvable references between vaults grouped by alias.

Obsidian itself does not follow `wiki://` URIs natively — they render as inline text — but `_bridges.md` plus the per-page `Cross-vault references` section give you a manual index until a dedicated Obsidian plugin lands.

## Refresh workflow

To incorporate new sources or fixes from your source files:

```bash
# Edit source files under your project's source dirs, then:
tesserae compile
```

`compile` re-projects the vault automatically — you no longer have to run a separate export step. (`tesserae vault export --vault <path>` still exists for a one-off re-projection without a full recompile.) Obsidian hot-reloads the changed files on disk.

If you've added markdown notes inside the vault that aren't projected from the graph (e.g. your own personal annotations), they survive — the projector only overwrites files it owns under `papers/`, `concepts/`, `claims/`, plus `index.md`, `_bridges.md`, `_meta/dashboard.md`, and `README.md`. Hand-written pages (no `node_id:` frontmatter) and the dedicated user-notes block (`<!-- user-notes:start -->` … `<!-- user-notes:end -->`) on each projected page are preserved across recompiles.

### Editing in Obsidian flows back (bidirectional sync)

As of v0.5.0 the vault is **no longer a one-way export**. It is a *bidirectional projection*: the typed graph is still the source of truth, but `project compile` now reads your Obsidian edits back out of the vault and overlays them onto the graph **before** re-projecting. Edit a node's `title`, `aliases`, description callout, or any non-system frontmatter scalar in Obsidian, recompile, and the change survives — and propagates to the static site, MCP, and every other projection.

```bash
tesserae compile
# [tesserae] vault overlay: applying 3 field override(s) from obsidian_vault/
```

What the overlay harvests (the *vault-wins* fields):

- `title` → node `name`
- `aliases` → node aliases
- the body description callout (or first paragraph) → node `description`
- every non-reserved frontmatter scalar → `metadata.<key>` (reserved/system keys `node_id`, `title`, `type`, `aliases`, `source_path`, `edges_out`, `edges_in`, `cross_vault` are never treated as user overrides)

Every overlay run writes a `.tesserae/diverged-fields.md` report (`## Field overrides — N across M node(s)`) so you can audit exactly what was pulled back. Wikilinks you add inside the user-notes block become `user_link` edges. Pass `tesserae compile` (with `compile_options.no_vault_pull = true` in `.tesserae/config.json`) to bypass the overlay for one run — useful for recovery, or when you intentionally want the source markdown to win.

The first compile after enabling this feature gets a "free pass": with no `vault_snapshot.json` baseline yet, nothing is harvested; the snapshot written at the end becomes the baseline for the next compile's diff.

For a dedicated live workflow, `tesserae vault sync` re-applies the overlay and re-projects without a full recompile:

```bash
# Preview what a compile would pull back, without mutating the graph.
tesserae vault sync --dry-run

# Watch the vault and round-trip edits live (Ctrl-C to stop).
tesserae vault sync --watch

# After renaming/removing nodes, delete projected pages left orphaned.
tesserae vault sync --prune-orphans
```

See [obsidian-sync.md](obsidian-sync.md) for the full per-field ownership matrix and design rationale.

## When to use this vs. the static site

The compiled HTML site (`tesserae export site` → `.tesserae/site/`) is a one-way, read-only export for sharing — push to GitHub Pages, S3, any static host. The Obsidian vault is for **reading, querying, and editing** locally with Dataview and Obsidian's graph view: it is the one projection whose edits flow back into the graph (see the bidirectional sync section above). Both project from the same graph, so they never drift — and corrections you make in Obsidian propagate to the site on the next compile.
