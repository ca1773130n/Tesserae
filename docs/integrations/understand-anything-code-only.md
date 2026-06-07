# Understand-Anything: code-graph-only mode

<!-- translations:start -->
<p align="center"><a href="../i18n/integrations/understand-anything-code-only.ko.md">한국어</a> · <a href="../i18n/integrations/understand-anything-code-only.zh.md">中文</a> · <a href="../i18n/integrations/understand-anything-code-only.ja.md">日本語</a> · <a href="../i18n/integrations/understand-anything-code-only.ru.md">Русский</a> · <a href="../i18n/integrations/understand-anything-code-only.es.md">Español</a> · <a href="../i18n/integrations/understand-anything-code-only.fr.md">Français</a> · <a href="../i18n/integrations/understand-anything-code-only.de.md">Deutsch</a></p>
<!-- translations:end -->

This is a follow-up to [understand-anything.md](understand-anything.md). The base doc explains how to install and enable [Understand-Anything](https://github.com/Lum1104/Understand-Anything) (UA) as a companion that produces a code-graph in `.understand-anything/knowledge-graph.json`. **This doc explains how to make UA contribute ONLY a code graph and never pollute Tesserae's research-graph Concept layer with section headings extracted from your docs.**

If you've ever opened the typed graph after enabling UA and found the Concept layer filled with stuff like `'Quickstart'`, `'2) Paste it into your MCP client'`, or the same heading in seven languages, you hit the problem this doc fixes.

## Why this happens

Two layers of the same mistake compound:

1. **UA walks your docs by default.** Out of the box, UA's source loader walks every readable file under your project root — including `docs/`, `docs/i18n/`, READMEs in every language, etc. For each markdown heading it sees, it records a node in `.understand-anything/knowledge-graph.json` with the heading text as the entity name.
2. **Tesserae merges UA's entire graph natively.** When `external_tools` lists UA with `sync_mode: "native_graph"`, `ProjectWiki._merge_configured_understand_anything_graph()` reads the artifact and imports every UA node into the research graph as a `Concept`. UA's "this is a code symbol" intent gets flattened to "this is a research concept", and your doc-heading nodes ride along.

Net effect: every translated heading shows up as a duplicate Concept (`'Setup'`, `'설정'`, `'安装'`, `'インストール'`, `'Установка'`, `'Configuración'`, `'Configuration'`, `'Einrichtung'`), creating slug collisions that the projector renames as `setup-2.md`, `setup-3.md`, …, `setup-7.md`.

> [!warning] You'll know it when you see it
> A symptom check on a project where this has happened:
> ```bash
> .venv/bin/python -c "
> import json
> from collections import Counter
> nodes = json.load(open('.tesserae/graph.json'))['nodes']
> srcs = Counter(n.get('source_path','') for n in nodes if n['type']=='Concept')
> print(srcs.most_common(3))
> "
> ```
> If the top source is `.understand-anything/knowledge-graph.json` with hundreds of Concept nodes, every translated heading you have is being imported as a separate concept.

## Fix in three steps

### Step 1 — stop the Tesserae side from importing UA as Concepts

Edit `.tesserae/config.json` and set both `enabled: false` and `sync_mode: "disabled"` on the UA tool entry. Both belt-and-suspenders flags are checked by the merge code path:

```jsonc
{
  "external_tools": [
    {
      "id": "understand-anything",
      "enabled": false,            // ← was true
      "sync_mode": "disabled",     // ← was "native_graph"
      "auto_refresh": false,       // optional: stop refreshing UA on every compile
      // ...the rest of the entry stays as-is
    }
  ]
}
```

`enabled: false` makes `_merge_configured_understand_anything_graph()` skip the tool entirely. `sync_mode: "disabled"` is a secondary guard in case a future bug ignores the `enabled` flag.

### Step 2 — delete the stale artifacts so nothing's left behind

If you previously ran a compile with UA enabled, the polluted artifacts are still on disk:

```bash
rm -f .understand-anything/knowledge-graph.json
rm -f .tesserae/external/understand-anything.md
```

Tesserae regenerates `.tesserae/external/understand-anything.md` only when the tool is enabled, so deleting it is safe once Step 1 is in place.

### Step 3 — recompile + prune the Obsidian vault

```bash
tesserae compile
tesserae vault sync --prune-orphans
```

The compile will skip the UA merge, leaving the research graph free of UA-sourced Concepts. The prune step deletes any orphan pages in the Obsidian vault that pointed at node_ids the merge had created.

## Verification

After the recompile, the audit script above should report zero (or close to zero) Concept nodes sourced from `.understand-anything/knowledge-graph.json`. A second useful check:

```bash
.venv/bin/python -c "
import json, re
from collections import defaultdict
nodes = json.load(open('.tesserae/graph.json'))['nodes']
concepts = [n for n in nodes if n['type']=='Concept']
def slug(s): return re.sub(r'[^a-z0-9가-힣]+','-',s.lower()).strip('-')
buckets = defaultdict(list)
for n in concepts: buckets[slug(n['name'])].append(n)
collisions = {s: ns for s, ns in buckets.items() if len(ns)>1}
print(f'{len(collisions)} Concept slug collision(s), {sum(len(ns)-1 for ns in collisions.values())} duplicate page(s)')
"
```

Should print `0 Concept slug collision(s), 0 duplicate page(s)` if the fix took effect.

## When you actually want code-graph navigation back

UA's code graph is genuinely useful — call/import edges, class hierarchies, etc. — when it isn't drowning in doc-heading noise. To re-enable it properly:

1. **Scope UA itself to code, not docs.** UA accepts include/exclude patterns; configure it to walk only `src/`, `lib/`, `tesserae/`, etc. and explicitly exclude `docs/`, `README*.md`, and `docs/i18n/`. The exact config knob lives in UA's own docs at [Lum1104/Understand-Anything](https://github.com/Lum1104/Understand-Anything).
2. **Re-enable in `.tesserae/config.json`**: flip `enabled` back to `true`, `sync_mode` back to `"native_graph"`, `auto_refresh` back to `true`.
3. **Recompile** and re-run the audit. A clean UA run should produce Concepts that map to real code symbols (function names, class names, modules) rather than English-language section headings.

The asymmetry stings — disabling is one config flip, re-enabling cleanly requires understanding UA's source-scoping — but it's the right boundary. UA's job is code graphs, Tesserae's job is research graphs, and the seam between them should never let doc headings cross from one side to the other.

## Where this fits

| Layer | Concern | Configured via |
|---|---|---|
| UA's own walker | What files UA reads in the first place | UA's config (out of scope for Tesserae) |
| `auto_refresh` on UA tool | Whether `tesserae compile` re-runs UA | `.tesserae/config.json` external_tools entry |
| `enabled` on UA tool | Whether Tesserae considers UA at all | `.tesserae/config.json` external_tools entry |
| `sync_mode` on UA tool | Whether UA's nodes get merged into the research graph | `.tesserae/config.json` external_tools entry |

The `enabled` + `sync_mode` knobs are the seam between the two projects. The walker + `auto_refresh` knobs are UA's internal concerns.
