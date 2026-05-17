# LLM による合成 prose

<!-- translations:start -->
<p align="center"><a href="../synthesis-llm.md">English</a> · <a href="synthesis-llm.ko.md">한국어</a> · <a href="synthesis-llm.zh.md">中文</a> · <a href="synthesis-llm.ja.md">日本語</a> · <a href="synthesis-llm.ru.md">Русский</a> · <a href="synthesis-llm.es.md">Español</a> · <a href="synthesis-llm.fr.md">Français</a> · <a href="synthesis-llm.de.md">Deutsch</a></p>
<!-- translations:end -->
LLM-Wiki には 2 つの合成経路があります。デフォルトはネットワークを一切呼び出さない決定的なヒューリスティックです。研究グラフから、予測可能で冪等な Markdown テンプレートを生成します。任意の **LLM アップグレード経路** は、他のすべての不変条件（冪等性、citation tracking、hash-stable な本文）を保ったまま、compile のたびにそれらのテンプレートを Claude が書いた prose に置き換えます。

このページでは、いつ有効にするべきか、どれくらいコストがかかるか、どのデータがマシンの外へ出るか、そして出力をどう検査するかを説明します。

## 何をするか

両方の経路は同じ `_PagePlan` 入力（node id、名前、type、description、source path）を消費します。違いは本文です。

**ヒューリスティック (`generator: heuristic-v1`)**

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

データベースのダンプのように読めます。有用で、決定的で、現在提供されています。

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

編集ダイジェストのように読めます。モデルは入力に存在する事実を*言い換える*ことに制約されます。node を名指しするすべての段落は `[node_id]` citation で終わり、citation を省略した本文（または 80 文字未満の本文）は拒否され、ヒューリスティックへ fallback します。

## Prompt の形

2 つのブロックです。`cache_control: ephemeral` で包まれた長く安定した system block と、kind ごとに変わるページ単位の user message です。

### System block（cached、全ページで同一）

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

ブロック全体は約 500 tokens です。正規のテキストは [`llm_wiki/llm_synthesis.py`](../../llm_wiki/llm_synthesis.py) を参照してください。そこで byte が少しでも変わると、その実行中の以降すべてのページで prompt cache が無効化されるため、rule text は意図的に固定されています。

### User message（ページ単位、NOT cached）

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

EDITORIAL ANGLE ブロックは決定的なヒューリスティック本文です。モデルは新しい事実を取りに行くのではなく、これらの正確な事実を言い換えたり再構成したりするよう指示されます。INPUTS は 25 nodes に capped され、ページ内 degree で ranked されるため、plan にそれ以上の項目がある場合も、最も signal の強い contributors が prompt に入ります。

## 有効化方法

```sh
pip install llm-research-wiki[synthesis-llm]
export LLM_WIKI_SYNTHESIS_LLM=1
export ANTHROPIC_API_KEY=sk-...
python -m llm_wiki.cli project compile
```

`LLM_WIKI_SYNTHESIS_MODEL` で model を override できます（デフォルトは `claude-sonnet-4-6`）。Anthropic SDK ≥ 0.40 が必要です。

この経路は**次の 3 つがすべて**真の場合にのみ有効になります。

1. `LLM_WIKI_SYNTHESIS_LLM` が `1`/`true`/`yes`/`on` である。
2. `ANTHROPIC_API_KEY` が空でない（またはプロジェクトの `.llm-wiki/config.json` で `synthesis.api_key` を設定している）。
3. `anthropic` package を import できる。

いずれかが欠けている場合は stderr に情報行を 1 行出力し（`[llm-wiki] LLM synthesis disabled (...)`）、ヒューリスティックへ fallback します。

LLM 経路が有効でも単一ページが失敗した場合（network blip、401、429 など）、そのページはヒューリスティックへ fallback し、compile ごとに error class ごとの stderr log は 1 回だけ出ます。compile は継続します。

## コスト

各 synthesis page は `messages.create` call を 1 回行います。system block（style rules + ontology recap、約 500 tokens）は `cache_control: ephemeral` で包まれていますが、Sonnet 4.6 では最小 cacheable prefix が 2048 tokens です。そのため現在のサイズでは cache marker は設定されるものの、実際には機能しません。すべてのページで full input pricing を見込んでください。cache reads が重要なら、preamble を拡張するか、cache floor の低い model（例: 1024 tokens の Sonnet 4.5）に切り替えてください。

ページごとの token コスト（典型値、inputs は 25 件 cap）：

| | System | User message | Output | Total in / out |
|---|---:|---:|---:|---:|
| pulse | ~500 | ~600 | ~250 | ~1100 / ~250 |
| daily_digest | ~500 | ~700 | ~200 | ~1200 / ~200 |
| weekly | ~500 | ~700 | ~250 | ~1200 / ~250 |
| topic | ~500 | ~900 | ~300 | ~1400 / ~300 |
| comparison | ~500 | ~900 | ~250 | ~1400 / ~250 |
| field_overview | ~500 | ~900 | ~300 | ~1400 / ~300 |

現在この repository の典型的な compile は 5〜10 個の synthesis pages を生成します（pulse と、いくつかの daily/weekly/topic/comparison/field overview）。Sonnet 4.6 の list pricing（`$3/M` input、`$15/M` output、この preamble サイズでは cache hit なし）では:

```
per page (uncached): ~1300 * $3/1M + ~270 * $15/1M ≈ $0.0080
                     × 10 pages              total ≈ $0.080
```

Haiku 4.5（`$1/M` input、`$5/M` output）へ切り替えると、同じ compile はおよそ `~$0.027` です。tokens を使う前に prompt の形を確認したい場合は、先に `LLM_WIKI_SYNTHESIS_DRY_RUN=1` で実行してください。

## Privacy

送信されるのは graph metadata のみです。node id、node name、type、description の先頭約 280 文字、そして contributing source paths の一覧です。**Source-document bodies は送信されません。** 論文の完全な markdown が `data/research/...` にある場合でも、その text はマシンの外へ出ません。モデルが見るのは、その paper が存在すること、type、そして他の nodes とどう接続しているかだけです。

それでも用途に対して多すぎる場合は、env var を未設定のままにしてください。ヒューリスティック経路は完全に offline で動作し、デフォルトです。

## オフにする / fallback

env var を unset（または `0` に設定）して再実行します。

```sh
unset LLM_WIKI_SYNTHESIS_LLM
python -m llm_wiki.cli project compile
```

以降の compile は、影響を受けた synthesis pages をヒューリスティック generator で再生成します。page rewrite は `content_hash` によって gated されるため、実際に本文が変わったページだけが書き換えられます。

## 出力の検査

LLM 生成ページは on-disk frontmatter にタグ付けされます。

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

2 回の compile の出力を比較する最も簡単な方法は diff です。

```sh
git diff --no-index \
  baseline/.llm-wiki/wiki/syntheses/pulse.md \
  upgraded/.llm-wiki/wiki/syntheses/pulse.md
```

`.llm-wiki/wiki/syntheses/` にある append-only の `.history.jsonl` ledger は、すべての rewrite について generator label を記録するため、ページがいつヒューリスティックから LLM へ（またはその逆へ）移行したかを audit できます。
