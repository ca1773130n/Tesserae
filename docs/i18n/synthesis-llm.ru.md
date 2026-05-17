# Синтез prose с опорой на LLM

<!-- translations:start -->
<p align="center"><a href="../synthesis-llm.md">English</a> · <a href="synthesis-llm.ko.md">한국어</a> · <a href="synthesis-llm.zh.md">中文</a> · <a href="synthesis-llm.ja.md">日本語</a> · <a href="synthesis-llm.ru.md">Русский</a> · <a href="synthesis-llm.es.md">Español</a> · <a href="synthesis-llm.fr.md">Français</a> · <a href="synthesis-llm.de.md">Deutsch</a></p>
<!-- translations:end -->
LLM-Wiki поставляется с двумя путями синтеза. По умолчанию используется детерминированная эвристика, которая никогда не обращается к сети: она создает предсказуемые, идемпотентные Markdown-шаблоны из исследовательского графа. Опциональный **путь LLM-апгрейда** заменяет эти шаблоны prose, написанной Claude при каждом compile, сохраняя все остальные инварианты (идемпотентность, отслеживание citation, hash-stable тела) неизменными.

Эта страница объясняет, когда это включать, сколько это стоит, какие данные покидают вашу машину и как проверять вывод.

## Что он делает

Оба пути используют одни и те же входы `_PagePlan` (node ids, names, types, descriptions, source paths). Отличается body.

**Эвристика (`generator: heuristic-v1`)**

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

Читается как дамп базы данных. Полезно, детерминированно и доступно уже сейчас.

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

Читается как редакторский дайджест. Модель ограничена задачей *пересказывать* факты, присутствующие во входах: каждый абзац, называющий node, заканчивается citation вида `[node_id]`, а body без citation (или короче 80 символов) отклоняется и fallback-ится к эвристике.

## Форма prompt

Два блока: длинный стабильный system block, обернутый в `cache_control: ephemeral`, и page-specific user message, который меняется по kind.

### System block (cached, одинаковый для всех страниц)

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

Полный блок занимает около 500 tokens. Канонический текст см. в [`llm_wiki/llm_synthesis.py`](../../llm_wiki/llm_synthesis.py). Любое изменение byte там инвалидирует prompt cache для каждой последующей страницы в запуске, поэтому rule text намеренно заморожен.

### User message (для каждой страницы, NOT cached)

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

Блок EDITORIAL ANGLE — это детерминированное эвристическое body. Модели говорится перефразировать/реорганизовать именно эти факты, а не искать новые. INPUTS ограничены 25 nodes и ранжируются по intra-page degree, так что самые значимые contributors попадают в prompt, когда в plan их больше.

## Как включить

```sh
pip install llm-research-wiki[synthesis-llm]
export LLM_WIKI_SYNTHESIS_LLM=1
export ANTHROPIC_API_KEY=sk-...
python -m llm_wiki.cli project compile
```

Model можно переопределить через `LLM_WIKI_SYNTHESIS_MODEL` (по умолчанию `claude-sonnet-4-6`). Требуется Anthropic SDK ≥ 0.40.

Путь активируется только когда **все три** условия истинны:

1. `LLM_WIKI_SYNTHESIS_LLM` равен `1`/`true`/`yes`/`on`.
2. `ANTHROPIC_API_KEY` не пустой (или вы задали `synthesis.api_key` в `.llm-wiki/config.json` проекта).
3. Package `anthropic` можно импортировать.

Если чего-то не хватает, в stderr пишется одна информационная строка (`[llm-wiki] LLM synthesis disabled (...)`), и происходит fallback к эвристике.

Если LLM-путь активен, но одна страница падает — network blip, 401, 429 — эта страница fallback-ится к эвристике с одной stderr-записью на error class за compile. Compile продолжает выполняться.

## Стоимость

Каждая synthesis page делает один вызов `messages.create`. System block (style rules + ontology recap, около 500 tokens) обернут в `cache_control: ephemeral`, но у Sonnet 4.6 минимальный cacheable prefix равен 2048 tokens — поэтому при текущем размере cache marker выставляется, но фактически не срабатывает. Планируйте полную input pricing для каждой страницы; если cache reads важны, расширьте preamble или переключитесь на model с более низким cache floor (например, Sonnet 4.5 на 1024 tokens).

Token-затраты на страницу (типично, с cap в 25 inputs):

| | System | User message | Output | Total in / out |
|---|---:|---:|---:|---:|
| pulse | ~500 | ~600 | ~250 | ~1100 / ~250 |
| daily_digest | ~500 | ~700 | ~200 | ~1200 / ~200 |
| weekly | ~500 | ~700 | ~250 | ~1200 / ~250 |
| topic | ~500 | ~900 | ~300 | ~1400 / ~300 |
| comparison | ~500 | ~900 | ~250 | ~1400 / ~250 |
| field_overview | ~500 | ~900 | ~300 | ~1400 / ~300 |

Типичный compile этого repository сегодня создает 5–10 synthesis pages (pulse + несколько daily/weekly/topic/comparison/field overviews). По list pricing Sonnet 4.6 (`$3/M` input, `$15/M` output, без cache hit при таком размере preamble):

```
per page (uncached): ~1300 * $3/1M + ~270 * $15/1M ≈ $0.0080
                     × 10 pages              total ≈ $0.080
```

Если переключиться на Haiku 4.5 (`$1/M` input, `$5/M` output), тот же compile стоит примерно `~$0.027`. Сначала запустите с `LLM_WIKI_SYNTHESIS_DRY_RUN=1`, если хотите проверить форму prompt без расхода tokens.

## Privacy

Отправляется только graph metadata: node ids, node names, types, первые ~280 символов descriptions и список contributing source paths. **Source-document bodies не отправляются.** Если полный markdown статьи лежит в `data/research/...`, этот text не покидает вашу машину; модель видит только, что paper существует, его type и связи с другими nodes.

Если для вашего случая это все еще слишком много, оставьте env var незаданной — эвристический путь работает полностью offline и является default.

## Выключение / fallback

Unset env var (или установите `0`) и запустите снова:

```sh
unset LLM_WIKI_SYNTHESIS_LLM
python -m llm_wiki.cli project compile
```

Последующие compile заново сгенерируют затронутые synthesis pages с эвристическим generator. Поскольку page rewrites gated по `content_hash`, переписаны будут только страницы, у которых body действительно изменился.

## Проверка output

Страницы, сгенерированные LLM, помечаются в on-disk frontmatter:

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

Самый простой способ сравнить выводы двух compile — diff:

```sh
git diff --no-index \
  baseline/.llm-wiki/wiki/syntheses/pulse.md \
  upgraded/.llm-wiki/wiki/syntheses/pulse.md
```

Append-only ledger `.history.jsonl` в `.llm-wiki/wiki/syntheses/` записывает generator label для каждого rewrite, поэтому можно audit-ить, когда страница перешла с эвристики на LLM (или обратно).
