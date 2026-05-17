# Prose de synthèse appuyée par un LLM

<!-- translations:start -->
<p align="center"><a href="../synthesis-llm.md">English</a> · <a href="synthesis-llm.ko.md">한국어</a> · <a href="synthesis-llm.zh.md">中文</a> · <a href="synthesis-llm.ja.md">日本語</a> · <a href="synthesis-llm.ru.md">Русский</a> · <a href="synthesis-llm.es.md">Español</a> · <a href="synthesis-llm.fr.md">Français</a> · <a href="synthesis-llm.de.md">Deutsch</a></p>
<!-- translations:end -->
LLM-Wiki fournit deux chemins de synthèse. Le chemin par défaut est une heuristique déterministe qui n’appelle jamais le réseau : elle produit des modèles Markdown prévisibles et idempotents à partir du graphe de recherche. Le **chemin optionnel de mise à niveau LLM** remplace ces modèles par une prose écrite par Claude à chaque compile, tout en conservant tous les autres invariants (idempotence, suivi des citation, corps hash-stable).

Cette page explique quand l’activer, combien cela coûte, quelles données quittent votre machine et comment inspecter la sortie.

## Ce que cela fait

Les deux chemins consomment les mêmes entrées `_PagePlan` (node ids, noms, types, descriptions, source paths). La différence se trouve dans le corps.

**Heuristique (`generator: heuristic-v1`)**

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

Cela se lit comme un dump de base de données. Utile, déterministe et disponible aujourd’hui.

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

Cela se lit comme un digest éditorial. Le modèle est contraint à *reformuler* les faits présents dans les entrées : chaque paragraphe qui nomme un node se termine par une citation `[node_id]`, et les corps qui omettent les citations (ou font moins de 80 caractères) sont rejetés et fallback vers l’heuristique.

## Forme du prompt

Deux blocs : un long system block stable enveloppé dans `cache_control: ephemeral`, et un user message par page qui varie selon le kind.

### System block (cached, identique entre les pages)

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

Le bloc complet fait environ 500 tokens. Voir [`llm_wiki/llm_synthesis.py`](../../llm_wiki/llm_synthesis.py) pour le texte canonique. Toute modification de byte à cet endroit invalide le prompt cache pour chaque page suivante d’une exécution ; le rule text est donc intentionnellement figé.

### User message (par page, NOT cached)

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

Le bloc EDITORIAL ANGLE est le corps heuristique déterministe : le modèle reçoit l’instruction de reformuler / réorganiser ces faits exacts plutôt que d’en chercher de nouveaux. INPUTS est limité à 25 nodes et classé par degree intra-page, afin que les contributors les plus signalés arrivent dans le prompt lorsqu’un plan en contient davantage.

## Comment l’activer

```sh
pip install llm-research-wiki[synthesis-llm]
export LLM_WIKI_SYNTHESIS_LLM=1
export ANTHROPIC_API_KEY=sk-...
python -m llm_wiki.cli project compile
```

Remplacez le model avec `LLM_WIKI_SYNTHESIS_MODEL` (par défaut `claude-sonnet-4-6`). Anthropic SDK ≥ 0.40 est requis.

Le chemin ne s’active que lorsque **les trois** conditions sont vraies :

1. `LLM_WIKI_SYNTHESIS_LLM` vaut `1`/`true`/`yes`/`on`.
2. `ANTHROPIC_API_KEY` n’est pas vide (ou vous définissez `synthesis.api_key` dans le `.llm-wiki/config.json` de votre projet).
3. Le package `anthropic` peut être importé.

Si l’un de ces éléments manque, une ligne informative est écrite sur stderr (`[llm-wiki] LLM synthesis disabled (...)`) et le système fallback vers l’heuristique.

Si le chemin LLM est actif mais qu’une seule page échoue — network blip, 401, 429 — cette page fallback vers l’heuristique avec un unique log stderr par error class et par compile. Le compile continue.

## Coût

Chaque synthesis page effectue un appel `messages.create`. Le system block (style rules + ontology recap, ~500 tokens) est enveloppé dans `cache_control: ephemeral`, mais avec Sonnet 4.6 le préfixe minimal cacheable est de 2048 tokens : à la taille actuelle, le cache marker est donc défini mais ne s’enclenche pas réellement. Prévoyez le full input pricing sur chaque page ; étendez le preamble ou passez à un model avec un cache floor plus bas (par exemple Sonnet 4.5 à 1024 tokens) si les cache reads comptent.

Coûts en tokens par page (typiques, avec un cap de 25 inputs) :

| | System | User message | Output | Total in / out |
|---|---:|---:|---:|---:|
| pulse | ~500 | ~600 | ~250 | ~1100 / ~250 |
| daily_digest | ~500 | ~700 | ~200 | ~1200 / ~200 |
| weekly | ~500 | ~700 | ~250 | ~1200 / ~250 |
| topic | ~500 | ~900 | ~300 | ~1400 / ~300 |
| comparison | ~500 | ~900 | ~250 | ~1400 / ~250 |
| field_overview | ~500 | ~900 | ~300 | ~1400 / ~300 |

Un compile typique de ce repository produit aujourd’hui 5 à 10 synthesis pages (pulse + une poignée de daily/weekly/topic/comparison/field overviews). Avec le list pricing de Sonnet 4.6 (`$3/M` input, `$15/M` output, sans cache hit à cette taille de preamble) :

```
per page (uncached): ~1300 * $3/1M + ~270 * $15/1M ≈ $0.0080
                     × 10 pages              total ≈ $0.080
```

Si vous passez à Haiku 4.5 (`$1/M` input, `$5/M` output), le même compile coûte environ `~$0.027`. Lancez d’abord avec `LLM_WIKI_SYNTHESIS_DRY_RUN=1` si vous voulez confirmer la forme du prompt sans dépenser de tokens.

## Privacy

Seules les graph metadata sont envoyées : node ids, node names, types, les ~280 premiers caractères des descriptions et la liste des contributing source paths. **Les source-document bodies ne sont pas envoyés.** Si le markdown complet d’un paper se trouve dans `data/research/...`, aucun de ce text ne quitte votre machine ; le modèle voit seulement que le paper existe, son type et comment il se connecte aux autres nodes.

Si c’est encore trop pour votre cas d’usage, laissez la env var non définie : le chemin heuristique fonctionne entièrement offline et c’est le comportement par défaut.

## Désactivation / fallback

Unset la env var (ou mettez-la à `0`) puis relancez :

```sh
unset LLM_WIKI_SYNTHESIS_LLM
python -m llm_wiki.cli project compile
```

Les compiles suivants régénèrent les synthesis pages affectées avec le generator heuristique. Comme les page rewrites sont gated par `content_hash`, seules les pages dont le body a effectivement changé seront réécrites.

## Inspecter la sortie

Les pages générées par LLM sont étiquetées dans le frontmatter sur disque :

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

Le moyen le plus simple de comparer les sorties entre deux compiles est un diff :

```sh
git diff --no-index \
  baseline/.llm-wiki/wiki/syntheses/pulse.md \
  upgraded/.llm-wiki/wiki/syntheses/pulse.md
```

Le ledger append-only `.history.jsonl` dans `.llm-wiki/wiki/syntheses/` enregistre le generator label de chaque rewrite, ce qui permet d’audit quand une page est passée de l’heuristique au LLM (ou inversement).
