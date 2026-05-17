# LLM-Backed Synthesis Prose

<!-- translations:start -->
<p align="center"><a href="i18n/synthesis-llm.ko.md">한국어</a> · <a href="i18n/synthesis-llm.zh.md">中文</a> · <a href="i18n/synthesis-llm.ja.md">日本語</a> · <a href="i18n/synthesis-llm.ru.md">Русский</a> · <a href="i18n/synthesis-llm.es.md">Español</a> · <a href="i18n/synthesis-llm.fr.md">Français</a> · <a href="../i18n/synthesis-llm.de.md">Deutsch</a></p>
<!-- translations:end -->
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
## Recent activity

The wiki tightened around 3D reconstruction this week. Two papers landed
under the Splatting Family [ApproachFamily:splatting:a86ed11b9524], both
foregrounding photometric and depth supervision for stable splat geometry
[Paper:geometry-grounded-gaussian-splatting:f188522141a2]. The dominant
through-line is volumetric rendering refinements
[Concept:volumetric-rendering:b05846130d24].
```

Reads like an editorial digest. The model is constrained to *restate* facts
present in the inputs — every paragraph that names a node ends with a
`[node_id]` citation, and bodies that omit citations (or are shorter than
80 chars) are rejected and fall back to the heuristic.

## Prompt shape

Two blocks: a long, stable system block wrapped in
`cache_control: ephemeral` and a per-page user message that varies by kind.

### System block (cached, identical across pages)

```
You are an LLM-Wiki synthesis writer. Your job is to summarize a controlled
knowledge graph into a single Markdown page. Rules you follow ABSOLUTELY:

  RULE 1 — DO NOT INVENT FACTS. Restate or summarize ONLY material you find
  in the inputs. ...

  RULE 2 — CITE EVERY CLAIM. Every paragraph that names a node MUST end
  with one or more citation markers in square brackets, where the bracket
  body is the node's id (e.g. ``[Paper:arxiv-2604.20329:abcd1234]``).
  ...

  RULE 3 — STAY ON TOPIC. The synthesis kind decides the shape:
    * pulse        : project-wide weekly snapshot. 5-9 sentences max.
    * daily_digest : one paragraph per noteworthy paper that day.
    * weekly       : 3 themes from the week, 1 paragraph each.
    * topic        : narrative about a research topic / approach family.
    * comparison   : one paragraph per family with shared task/benchmark.
    * field_overview: 1-2 paragraphs per linked sub-topic.

  RULE 4 — TONE. Direct, terse, technical. ...
  RULE 5 — FORMAT. Output is pure Markdown. No frontmatter. ...
  RULE 6 — LANGUAGE. Match the dominant language of the input materials.
  If 80%+ of input titles/descriptions are in Korean, write in Korean.
  Otherwise English.

The current ontology is:
  Paper, Repository, Concept, Algorithm, Model, Dataset, Benchmark, Metric,
  Person, Organization, ResearchTopic, ApproachFamily, Synthesis, ...
A node id has the shape ``Type:slug:hash``.
```

The full block is ~500 tokens. See
[`llm_wiki/llm_synthesis.py`](../llm_wiki/llm_synthesis.py) for the
canonical text. Any byte change there invalidates the prompt cache for
every subsequent page in a run, so the rule text is intentionally frozen.

### User message (per page, NOT cached)

```
SYNTHESIS_KIND: topic
SHAPE: narrative about the named topic / approach family
TITLE: Topic — Gaussian Splatting
SOURCE_FILES: []

INPUTS:
  - id: Paper:geometry-grounded-gaussian-splatting:f188522141a2
    name: Geometry-Grounded Gaussian Splatting
    type: Paper
    description: Photometric and depth supervision for stable splat geometry.
    metadata: {"arxiv_id":"2604.20329","title_quality":"paper_file"}
  - id: ApproachFamily:splatting:a86ed11b9524
    name: Splatting Family
    type: ApproachFamily
  - id: Concept:volumetric-rendering:b05846130d24
    name: Volumetric Rendering
    type: Concept

CONTEXT:
  total nodes in graph: 2932
  total edges: 4394
  field name: 3D Reconstruction
  contributing days/weeks: 2026-04-25, 2026-04-26
  site title: LLM-Wiki
  page summary: Topic synthesis for Gaussian Splatting.

EDITORIAL ANGLE (HEURISTIC FALLBACK BODY for the model to consult):
  | # Topic — Gaussian Splatting
  | 
  | ## Contributing papers
  | - Geometry-Grounded Gaussian Splatting (arXiv:2604.20329)
  |
  | ## Related concepts
  | - Volumetric Rendering (Concept)

Write the synthesis page now. Remember Rule 2 — every claim must be
cited with the relevant node id in square brackets at the end of the
sentence or paragraph.
```

The EDITORIAL ANGLE block is the deterministic heuristic body — the model
is told to rephrase / re-organize those exact facts rather than reach for
new ones. INPUTS are capped at 25 nodes and ranked by intra-page degree so
the highest-signal contributors land in the prompt when a plan has more.

## How to enable it

```sh
pip install llm-research-wiki[synthesis-llm]
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
(style rules + ontology recap, ~500 tokens) is wrapped in
`cache_control: ephemeral`, but on Sonnet 4.6 the minimum cacheable prefix
is 2048 tokens — so at the current size the cache marker is set but does
not actually engage. Plan for full input pricing on every page; expand the
preamble or switch to a model with a lower cache floor (e.g. Sonnet 4.5 at
1024 tokens) if cache reads matter.

Per-page token costs (typical, with a 25-input cap on inputs):

| | System | User message | Output | Total in / out |
|---|---:|---:|---:|---:|
| pulse | ~500 | ~600 | ~250 | ~1100 / ~250 |
| daily_digest | ~500 | ~700 | ~200 | ~1200 / ~200 |
| weekly | ~500 | ~700 | ~250 | ~1200 / ~250 |
| topic | ~500 | ~900 | ~300 | ~1400 / ~300 |
| comparison | ~500 | ~900 | ~250 | ~1400 / ~250 |
| field_overview | ~500 | ~900 | ~300 | ~1400 / ~300 |

A typical compile of this repository today produces 5–10 synthesis pages
(pulse + a handful of daily/weekly/topic/comparison/field overviews). At
Sonnet 4.6 list pricing (`$3/M` input, `$15/M` output, no cache hit at
this preamble size):

```
per page (uncached): ~1300 * $3/1M + ~270 * $15/1M ≈ $0.0080
                     × 10 pages              total ≈ $0.080
```

If you switch to Haiku 4.5 (`$1/M` input, `$5/M` output) the same compile
costs roughly `~$0.027`. Run with `LLM_WIKI_SYNTHESIS_DRY_RUN=1` first if
you want to confirm prompt shape without spending tokens.

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
