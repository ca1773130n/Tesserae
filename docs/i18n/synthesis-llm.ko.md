# LLM 기반 합성 prose

<!-- translations:start -->
<p align="center"><a href="../synthesis-llm.md">English</a> · <a href="synthesis-llm.ko.md">한국어</a> · <a href="synthesis-llm.zh.md">中文</a> · <a href="synthesis-llm.ja.md">日本語</a> · <a href="synthesis-llm.ru.md">Русский</a> · <a href="synthesis-llm.es.md">Español</a> · <a href="synthesis-llm.fr.md">Français</a> · <a href="synthesis-llm.de.md">Deutsch</a></p>
<!-- translations:end -->
LLM-Wiki에는 두 가지 합성 경로가 있습니다. 기본값은 네트워크를 전혀 호출하지 않는 결정적 휴리스틱입니다. 연구 그래프에서 예측 가능하고 멱등적인 Markdown 템플릿을 생성합니다. 선택 사항인 **LLM 업그레이드 경로**는 다른 모든 불변성(멱등성, citation 추적, hash-stable 본문)을 그대로 유지하면서, 매 compile마다 Claude가 작성한 prose로 해당 템플릿을 대체합니다.

이 페이지는 이를 언제 활성화해야 하는지, 비용은 얼마인지, 어떤 데이터가 사용자의 머신을 떠나는지, 출력을 어떻게 검사하는지를 설명합니다.

## 수행하는 일

두 경로 모두 동일한 `_PagePlan` 입력(node id, 이름, type, description, source path)을 사용합니다. 차이는 본문입니다.

**휴리스틱 (`generator: heuristic-v1`)**

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

데이터베이스 덤프처럼 읽힙니다. 유용하고, 결정적이며, 현재 제공되는 방식입니다.

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

편집자가 쓴 digest처럼 읽힙니다. 모델은 입력에 있는 사실을 *다시 서술*하도록 제한됩니다. node를 언급하는 모든 문단은 `[node_id]` citation으로 끝나며, citation을 누락한 본문(또는 80자보다 짧은 본문)은 거부되고 휴리스틱으로 fallback됩니다.

## Prompt 형태

두 블록으로 구성됩니다. `cache_control: ephemeral`로 감싼 길고 안정적인 system block과, kind마다 달라지는 page별 user message입니다.

### System block (cached, 모든 page에서 동일)

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

전체 블록은 약 500 token입니다. canonical text는 [`llm_wiki/llm_synthesis.py`](../../llm_wiki/llm_synthesis.py)를 참고하세요. 그곳의 byte가 하나라도 바뀌면 실행 중 이후 모든 page의 prompt cache가 무효화되므로, rule text는 의도적으로 고정되어 있습니다.

### User message (page별, cached 아님)

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

EDITORIAL ANGLE 블록은 결정적 휴리스틱 본문입니다. 모델은 새로운 사실을 찾는 대신, 정확히 그 사실들을 다시 표현하거나 재구성하라는 지시를 받습니다. INPUTS는 25개 node로 제한되며 page 내부 degree로 ranking되므로, plan에 더 많은 항목이 있더라도 신호가 가장 강한 기여자가 prompt에 들어갑니다.

## 활성화 방법

```sh
pip install llm-research-wiki[synthesis-llm]
export LLM_WIKI_SYNTHESIS_LLM=1
export ANTHROPIC_API_KEY=sk-...
python -m llm_wiki.cli project compile
```

`LLM_WIKI_SYNTHESIS_MODEL`로 model을 override할 수 있습니다(기본값 `claude-sonnet-4-6`). Anthropic SDK ≥ 0.40이 필요합니다.

이 경로는 **세 조건 모두** 참일 때만 활성화됩니다.

1. `LLM_WIKI_SYNTHESIS_LLM`이 `1`/`true`/`yes`/`on`입니다.
2. `ANTHROPIC_API_KEY`가 비어 있지 않습니다(또는 프로젝트의 `.llm-wiki/config.json`에서 `synthesis.api_key`를 설정했습니다).
3. `anthropic` package를 import할 수 있습니다.

이 중 하나라도 누락되면 stderr에 정보성 line 하나를 기록하고(`[llm-wiki] LLM synthesis disabled (...)`) 휴리스틱으로 fallback합니다.

LLM 경로가 활성 상태이지만 단일 page가 실패하는 경우(네트워크 일시 장애, 401, 429 등) 해당 page는 휴리스틱으로 fallback되며, compile당 error class별로 stderr log가 한 번만 출력됩니다. compile은 계속 실행됩니다.

## 비용

각 synthesis page는 `messages.create` call을 한 번 수행합니다. system block(style rules + ontology recap, 약 500 token)은 `cache_control: ephemeral`로 감싸져 있지만, Sonnet 4.6에서 최소 cacheable prefix는 2048 token입니다. 따라서 현재 크기에서는 cache marker가 설정되지만 실제로 작동하지 않습니다. 모든 page에 대해 전체 input pricing을 예상하세요. cache read가 중요하다면 preamble을 확장하거나 cache floor가 더 낮은 model(예: Sonnet 4.5의 1024 token)로 전환하세요.

Page별 token 비용(일반적인 경우, inputs 25개 cap 기준):

| | System | User message | Output | Total in / out |
|---|---:|---:|---:|---:|
| pulse | ~500 | ~600 | ~250 | ~1100 / ~250 |
| daily_digest | ~500 | ~700 | ~200 | ~1200 / ~200 |
| weekly | ~500 | ~700 | ~250 | ~1200 / ~250 |
| topic | ~500 | ~900 | ~300 | ~1400 / ~300 |
| comparison | ~500 | ~900 | ~250 | ~1400 / ~250 |
| field_overview | ~500 | ~900 | ~300 | ~1400 / ~300 |

현재 이 repository의 일반적인 compile은 5~10개의 synthesis page를 생성합니다(pulse와 몇 개의 daily/weekly/topic/comparison/field overview). Sonnet 4.6 list pricing(`$3/M` input, `$15/M` output, 이 preamble 크기에서는 cache hit 없음) 기준:

```
per page (uncached): ~1300 * $3/1M + ~270 * $15/1M ≈ $0.0080
                     × 10 pages              total ≈ $0.080
```

Haiku 4.5(`$1/M` input, `$5/M` output)로 전환하면 동일한 compile 비용은 대략 `~$0.027`입니다. token을 쓰기 전에 prompt 형태를 확인하려면 먼저 `LLM_WIKI_SYNTHESIS_DRY_RUN=1`로 실행하세요.

## Privacy

전송되는 것은 graph metadata뿐입니다. node id, node name, type, description의 처음 약 280자, 그리고 contributing source path 목록입니다. **Source-document body는 전송되지 않습니다.** paper의 전체 markdown이 `data/research/...`에 있더라도 그 text는 사용자의 머신을 떠나지 않습니다. 모델은 해당 paper가 존재한다는 점, type, 그리고 다른 node와 어떻게 연결되는지만 봅니다.

그래도 사용 사례에 너무 많다면 env var를 설정하지 마세요. 휴리스틱 경로는 완전히 offline으로 실행되며 기본값입니다.

## 끄기 / fallback

env var를 unset하거나 `0`으로 설정한 뒤 다시 실행하세요.

```sh
unset LLM_WIKI_SYNTHESIS_LLM
python -m llm_wiki.cli project compile
```

이후 compile은 영향을 받은 synthesis page를 휴리스틱 generator로 다시 생성합니다. page rewrite는 `content_hash`에 의해 gated되므로 실제로 본문이 바뀐 page만 다시 작성됩니다.

## 출력 검사

LLM이 생성한 page는 on-disk frontmatter에 tag됩니다.

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

두 compile의 출력을 비교하는 가장 간단한 방법은 diff입니다.

```sh
git diff --no-index \
  baseline/.llm-wiki/wiki/syntheses/pulse.md \
  upgraded/.llm-wiki/wiki/syntheses/pulse.md
```

`.llm-wiki/wiki/syntheses/`의 append-only `.history.jsonl` ledger는 모든 rewrite의 generator label을 기록하므로, page가 언제 휴리스틱에서 LLM으로(또는 다시) 전환되었는지 audit할 수 있습니다.
