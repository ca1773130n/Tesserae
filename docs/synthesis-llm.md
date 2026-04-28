# LLM-Backed Synthesis Prose

LLM-Wiki ships with two synthesis paths. The default is a deterministic
heuristic that never calls a network: it produces predictable, idempotent
markdown templates from the research graph. The optional **LLM upgrade
path** replaces those templates with prose written by Claude on every
compile, while keeping every other invariant (idempotence, citation tracking,
hash-stable bodies) intact.

This page covers when to enable it, what it costs, what data leaves your
machine, and how to inspect the output.

## What it does

Both paths consume the same `_PagePlan` inputs (node ids, names, types,
descriptions, source paths). The difference is the body.

**Heuristic (`generator: heuristic-v1`)**

```markdown
# Project Pulse

## Counts
- Paper: 14
- Repository: 4
...

## Recently added
- Geometry-Grounded Gaussian Splatting (Paper)
- Volumetric Rendering Revisited (Paper)
...

## Tagline
LLM-Wiki — a self-evolving research notebook.
```

Reads like a database dump. Useful, deterministic, and shipped today.

**LLM (`generator: llm-claude-sonnet-4-6`)**

```markdown
The wiki tightened around 3D reconstruction this week. Three new papers
landed under the Geometry-Grounded Gaussian Splatting family
[Paper:geometry-grounded] [Paper:stochastic-solid] [Paper:volumetric].
The dominant thread continues to be how to anchor splat geometry in
photometric and depth supervision...
```

Reads like an editorial digest. The model is constrained to *restate* facts
present in the inputs — every paragraph that names a node ends with a
`[node_id]` citation, and bodies that omit citations are rejected and fall
back to the heuristic.

## How to enable it

```sh
pip install llm-wiki[synthesis-llm]
export LLM_WIKI_SYNTHESIS_LLM=1
export ANTHROPIC_API_KEY=sk-...
python -m llm_wiki.cli project compile
```

Override the model with `LLM_WIKI_SYNTHESIS_MODEL` (default
`claude-sonnet-4-6`). Anthropic SDK ≥ 0.40 is required.

The path activates only when **all three** are true:

1. `LLM_WIKI_SYNTHESIS_LLM` is `1`/`true`/`yes`/`on`.
2. `ANTHROPIC_API_KEY` is non-empty (or you set `synthesis.api_key` in your
   project's `.llm-wiki/config.json`).
3. The `anthropic` package can be imported.

Any of those missing logs one informational line to stderr
(`[llm-wiki] LLM synthesis disabled (...)`) and falls back to the heuristic.

If the LLM path is active but a single page fails — network blip, 401, 429
— that page falls back to the heuristic with a single stderr log per error
class per compile. The compile keeps running.

## Cost

Each synthesis page makes one `messages.create` call. The system block
(style rules + ontology recap, ~1.5K tokens) is wrapped in
`cache_control: ephemeral`, so the first page in a compile pays the cache
write and every subsequent page reads it. Per-page token costs are roughly:

| | Cache read | Uncached input | Output |
|---|---:|---:|---:|
| First page in a run | 0 | ~1500 + ~600 | ~250 |
| Every page after | ~1500 | ~600 | ~250 |

A typical compile of this repository today produces 5–10 synthesis pages
(pulse + a handful of daily/weekly/topic/comparison/field overviews). At
Sonnet 4.6 list pricing (`$3/M` input, `$15/M` output) that is roughly:

```
1 first-page  : (1500 * 1.25 + 600) * $3/1M + 250 * $15/1M ≈ $0.0107
9 cached pages: (1500 * 0.10 + 600) * $3/1M + 250 * $15/1M ≈ $0.0060 each
                                                     total ≈ $0.065
```

Numbers are approximate — actual token counts vary with the size of your
graph and how many input nodes each page references. Run with
`LLM_WIKI_SYNTHESIS_DRY_RUN=1` first if you want to confirm prompt shape
without spending tokens.

## Privacy

Only graph metadata is sent: node ids, node names, types, the first ~280
characters of descriptions, and the list of contributing source paths.
**Source-document bodies are not sent.** If a paper's full markdown lives
in `data/research/...`, none of that text leaves your machine; the model
only sees that the paper exists, what type it is, and how it connects to
other nodes.

If that's still too much for your use case, leave the env var unset — the
heuristic path runs fully offline and is the default.

## Switching off / falling back

Unset the env var (or set it to `0`) and re-run:

```sh
unset LLM_WIKI_SYNTHESIS_LLM
python -m llm_wiki.cli project compile
```

Subsequent compiles regenerate the affected synthesis pages with the
heuristic generator. Because page rewrites are gated on `content_hash`,
only pages whose body actually shifted will be rewritten.

## Inspecting output

LLM-generated pages are tagged in the on-disk frontmatter:

```yaml
---
synthesis_kind: pulse
slug: pulse
title: Project Pulse
generator: llm-claude-sonnet-4-6
llm_model: claude-sonnet-4-6
llm_cache_id: sha256-...
content_hash: sha256-...
---
```

Diffing across two compiles is the simplest way to compare outputs:

```sh
git diff --no-index \
  baseline/.llm-wiki/wiki/syntheses/pulse.md \
  upgraded/.llm-wiki/wiki/syntheses/pulse.md
```

The append-only `.history.jsonl` ledger in `.llm-wiki/wiki/syntheses/`
records the generator label for every rewrite, so you can audit when a
page transitioned from heuristic to LLM (or back).
