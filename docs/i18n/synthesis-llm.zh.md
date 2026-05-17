# LLM 支持的合成 prose

<!-- translations:start -->
<p align="center"><a href="../synthesis-llm.md">English</a> · <a href="synthesis-llm.ko.md">한국어</a> · <a href="synthesis-llm.zh.md">中文</a> · <a href="synthesis-llm.ja.md">日本語</a> · <a href="synthesis-llm.ru.md">Русский</a> · <a href="synthesis-llm.es.md">Español</a> · <a href="synthesis-llm.fr.md">Français</a> · <a href="synthesis-llm.de.md">Deutsch</a></p>
<!-- translations:end -->
LLM-Wiki 提供两条合成路径。默认路径是确定性的启发式方法，完全不调用网络：它从研究图生成可预测、幂等的 Markdown 模板。可选的 **LLM 升级路径** 会在每次 compile 时用 Claude 编写的 prose 替换这些模板，同时保持其他所有不变量（幂等性、citation 跟踪、hash-stable 正文）不变。

本页说明何时启用它、成本如何、哪些数据会离开你的机器，以及如何检查输出。

## 它做什么

两条路径都消费相同的 `_PagePlan` 输入（node id、名称、type、description、source path）。区别在正文。

**启发式 (`generator: heuristic-v1`)**

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

读起来像数据库转储。有用、确定性，并且已经随项目提供。

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

读起来像编辑摘要。模型被约束为只*重述*输入中已有的事实：每个命名 node 的段落都必须以 `[node_id]` citation 结尾；省略 citation（或短于 80 个字符）的正文会被拒绝并 fallback 到启发式。

## Prompt 形状

由两个块组成：一个长且稳定的 system block，用 `cache_control: ephemeral` 包裹；以及一个按页面、按 kind 变化的 user message。

### System block（cached，所有页面相同）

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

完整块约 500 tokens。规范文本见 [`llm_wiki/llm_synthesis.py`](../../llm_wiki/llm_synthesis.py)。那里任何 byte 变化都会让一次运行中后续每个页面的 prompt cache 失效，因此 rule text 被有意冻结。

### User message（按页面，NOT cached）

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

EDITORIAL ANGLE 块是确定性的启发式正文。模型被要求改写/重组这些确切事实，而不是寻找新事实。INPUTS 最多 25 个 node，并按页面内 degree 排名，因此当一个 plan 含有更多节点时，信号最高的贡献项会进入 prompt。

## 如何启用

```sh
pip install llm-research-wiki[synthesis-llm]
export LLM_WIKI_SYNTHESIS_LLM=1
export ANTHROPIC_API_KEY=sk-...
python -m llm_wiki.cli project compile
```

可用 `LLM_WIKI_SYNTHESIS_MODEL` 覆盖 model（默认 `claude-sonnet-4-6`）。需要 Anthropic SDK ≥ 0.40。

仅当**全部三个**条件为真时，该路径才会激活：

1. `LLM_WIKI_SYNTHESIS_LLM` 为 `1`/`true`/`yes`/`on`。
2. `ANTHROPIC_API_KEY` 非空（或你在项目的 `.llm-wiki/config.json` 中设置了 `synthesis.api_key`）。
3. 可以 import `anthropic` package。

缺少任何一项都会向 stderr 记录一行信息（`[llm-wiki] LLM synthesis disabled (...)`），然后 fallback 到启发式。

如果 LLM 路径已激活但单个页面失败——网络抖动、401、429——该页面会 fallback 到启发式；每次 compile 中每个 error class 只向 stderr 记录一次。compile 会继续运行。

## 成本

每个 synthesis page 会进行一次 `messages.create` call。system block（style rules + ontology recap，约 500 tokens）被 `cache_control: ephemeral` 包裹，但在 Sonnet 4.6 上，最小 cacheable prefix 是 2048 tokens，因此当前大小只会设置 cache marker，实际不会生效。应按每个页面完整 input pricing 估算；如果 cache reads 很重要，可以扩展 preamble，或切换到 cache floor 更低的 model（例如 1024 tokens 的 Sonnet 4.5）。

按页面的 token 成本（典型情况，inputs 上限为 25）：

| | System | User message | Output | Total in / out |
|---|---:|---:|---:|---:|
| pulse | ~500 | ~600 | ~250 | ~1100 / ~250 |
| daily_digest | ~500 | ~700 | ~200 | ~1200 / ~200 |
| weekly | ~500 | ~700 | ~250 | ~1200 / ~250 |
| topic | ~500 | ~900 | ~300 | ~1400 / ~300 |
| comparison | ~500 | ~900 | ~250 | ~1400 / ~250 |
| field_overview | ~500 | ~900 | ~300 | ~1400 / ~300 |

如今本 repository 的一次典型 compile 会生成 5–10 个 synthesis pages（pulse 加少量 daily/weekly/topic/comparison/field overview）。按 Sonnet 4.6 list pricing（`$3/M` input、`$15/M` output，此 preamble 大小无 cache hit）：

```
per page (uncached): ~1300 * $3/1M + ~270 * $15/1M ≈ $0.0080
                     × 10 pages              total ≈ $0.080
```

如果切换到 Haiku 4.5（`$1/M` input、`$5/M` output），同一次 compile 大约花费 `~$0.027`。如果想在花费 tokens 前确认 prompt 形状，请先用 `LLM_WIKI_SYNTHESIS_DRY_RUN=1` 运行。

## 隐私

只会发送 graph metadata：node id、node name、type、description 的前约 280 个字符，以及 contributing source paths 列表。**不会发送 source-document bodies。** 如果论文完整 markdown 位于 `data/research/...`，其中内容不会离开你的机器；模型只会看到这篇论文存在、它的 type，以及它如何连接到其他 nodes。

如果这对你的用例仍然过多，请不要设置该 env var——启发式路径完全离线运行，并且是默认值。

## 关闭 / fallback

取消设置 env var（或设为 `0`）并重新运行：

```sh
unset LLM_WIKI_SYNTHESIS_LLM
python -m llm_wiki.cli project compile
```

后续 compile 会用启发式 generator 重新生成受影响的 synthesis pages。因为 page rewrite 由 `content_hash` gated，只有正文确实变化的页面才会被重写。

## 检查输出

LLM 生成的页面会在磁盘上的 frontmatter 中标记：

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

比较两次 compile 输出的最简单方式是 diff：

```sh
git diff --no-index \
  baseline/.llm-wiki/wiki/syntheses/pulse.md \
  upgraded/.llm-wiki/wiki/syntheses/pulse.md
```

`.llm-wiki/wiki/syntheses/` 中 append-only 的 `.history.jsonl` ledger 会记录每次 rewrite 的 generator label，因此你可以 audit 页面何时从启发式切换到 LLM（或切回）。
