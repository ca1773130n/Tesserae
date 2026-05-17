# Prosa de síntesis respaldada por LLM

<!-- translations:start -->
<p align="center"><a href="../synthesis-llm.md">English</a> · <a href="synthesis-llm.ko.md">한국어</a> · <a href="synthesis-llm.zh.md">中文</a> · <a href="synthesis-llm.ja.md">日本語</a> · <a href="synthesis-llm.ru.md">Русский</a> · <a href="synthesis-llm.es.md">Español</a> · <a href="synthesis-llm.fr.md">Français</a> · <a href="synthesis-llm.de.md">Deutsch</a></p>
<!-- translations:end -->
LLM-Wiki incluye dos rutas de síntesis. La predeterminada es una heurística determinista que nunca llama a la red: produce plantillas Markdown predecibles e idempotentes a partir del grafo de investigación. La **ruta opcional de actualización con LLM** reemplaza esas plantillas con prosa escrita por Claude en cada compile, manteniendo intactas todas las demás invariantes (idempotencia, seguimiento de citation, cuerpos hash-stable).

Esta página explica cuándo habilitarla, cuánto cuesta, qué datos salen de tu máquina y cómo inspeccionar el resultado.

## Qué hace

Ambas rutas consumen las mismas entradas `_PagePlan` (node ids, nombres, types, descriptions, source paths). La diferencia está en el cuerpo.

**Heurística (`generator: heuristic-v1`)**

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

Se lee como un volcado de base de datos. Útil, determinista y disponible hoy.

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

Se lee como un digest editorial. El modelo está restringido a *reformular* hechos presentes en las entradas: cada párrafo que nombra un node termina con una citation `[node_id]`, y los cuerpos que omiten citations (o tienen menos de 80 caracteres) se rechazan y hacen fallback a la heurística.

## Forma del prompt

Dos bloques: un system block largo y estable envuelto en `cache_control: ephemeral`, y un user message por página que varía según el kind.

### System block (cached, idéntico en todas las páginas)

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

El bloque completo tiene unos 500 tokens. Consulta [`llm_wiki/llm_synthesis.py`](../../llm_wiki/llm_synthesis.py) para el texto canónico. Cualquier cambio de byte allí invalida el prompt cache para cada página posterior en una ejecución, por lo que el rule text está congelado intencionalmente.

### User message (por página, NOT cached)

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

El bloque EDITORIAL ANGLE es el cuerpo heurístico determinista: al modelo se le indica que reformule / reorganice esos hechos exactos en lugar de buscar otros nuevos. INPUTS está limitado a 25 nodes y se ordena por degree dentro de la página, de modo que los contributors con mayor señal llegan al prompt cuando un plan contiene más.

## Cómo habilitarlo

```sh
pip install llm-research-wiki[synthesis-llm]
export LLM_WIKI_SYNTHESIS_LLM=1
export ANTHROPIC_API_KEY=sk-...
python -m llm_wiki.cli project compile
```

Puedes sobrescribir el model con `LLM_WIKI_SYNTHESIS_MODEL` (predeterminado `claude-sonnet-4-6`). Se requiere Anthropic SDK ≥ 0.40.

La ruta se activa solo cuando **las tres** condiciones son verdaderas:

1. `LLM_WIKI_SYNTHESIS_LLM` es `1`/`true`/`yes`/`on`.
2. `ANTHROPIC_API_KEY` no está vacío (o configuraste `synthesis.api_key` en `.llm-wiki/config.json` de tu proyecto).
3. El package `anthropic` puede importarse.

Si falta cualquiera de ellas, se registra una línea informativa en stderr (`[llm-wiki] LLM synthesis disabled (...)`) y se hace fallback a la heurística.

Si la ruta LLM está activa pero una sola página falla — network blip, 401, 429 — esa página hace fallback a la heurística con un único log a stderr por error class y por compile. El compile sigue ejecutándose.

## Coste

Cada synthesis page hace una llamada `messages.create`. El system block (style rules + ontology recap, ~500 tokens) está envuelto en `cache_control: ephemeral`, pero en Sonnet 4.6 el prefijo mínimo cacheable es de 2048 tokens, así que con el tamaño actual el cache marker se establece pero no se activa realmente. Planifica full input pricing en cada página; amplía el preamble o cambia a un model con un cache floor más bajo (por ejemplo, Sonnet 4.5 con 1024 tokens) si las cache reads importan.

Costes de tokens por página (típicos, con cap de 25 entradas):

| | System | User message | Output | Total in / out |
|---|---:|---:|---:|---:|
| pulse | ~500 | ~600 | ~250 | ~1100 / ~250 |
| daily_digest | ~500 | ~700 | ~200 | ~1200 / ~200 |
| weekly | ~500 | ~700 | ~250 | ~1200 / ~250 |
| topic | ~500 | ~900 | ~300 | ~1400 / ~300 |
| comparison | ~500 | ~900 | ~250 | ~1400 / ~250 |
| field_overview | ~500 | ~900 | ~300 | ~1400 / ~300 |

Un compile típico de este repository hoy produce 5–10 synthesis pages (pulse + un puñado de daily/weekly/topic/comparison/field overviews). Con el list pricing de Sonnet 4.6 (`$3/M` input, `$15/M` output, sin cache hit con este tamaño de preamble):

```
per page (uncached): ~1300 * $3/1M + ~270 * $15/1M ≈ $0.0080
                     × 10 pages              total ≈ $0.080
```

Si cambias a Haiku 4.5 (`$1/M` input, `$5/M` output), el mismo compile cuesta aproximadamente `~$0.027`. Ejecuta primero con `LLM_WIKI_SYNTHESIS_DRY_RUN=1` si quieres confirmar la forma del prompt sin gastar tokens.

## Privacy

Solo se envía graph metadata: node ids, node names, types, los primeros ~280 caracteres de descriptions y la lista de contributing source paths. **No se envían source-document bodies.** Si el markdown completo de un paper vive en `data/research/...`, nada de ese text sale de tu máquina; el modelo solo ve que el paper existe, qué type tiene y cómo se conecta con otros nodes.

Si eso sigue siendo demasiado para tu caso de uso, deja la env var sin configurar: la ruta heurística se ejecuta totalmente offline y es la predeterminada.

## Desactivar / fallback

Unset la env var (o ponla en `0`) y vuelve a ejecutar:

```sh
unset LLM_WIKI_SYNTHESIS_LLM
python -m llm_wiki.cli project compile
```

Los compiles posteriores regeneran las synthesis pages afectadas con el generator heurístico. Como las page rewrites están gated por `content_hash`, solo se reescriben las páginas cuyo body realmente cambió.

## Inspeccionar el resultado

Las páginas generadas por LLM se etiquetan en el frontmatter en disco:

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

La forma más simple de comparar resultados entre dos compiles es hacer diff:

```sh
git diff --no-index \
  baseline/.llm-wiki/wiki/syntheses/pulse.md \
  upgraded/.llm-wiki/wiki/syntheses/pulse.md
```

El ledger append-only `.history.jsonl` en `.llm-wiki/wiki/syntheses/` registra el generator label de cada rewrite, para que puedas audit cuándo una página pasó de heurística a LLM (o volvió atrás).
