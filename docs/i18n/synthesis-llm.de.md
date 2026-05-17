# LLM-gestützte Synthese-Prosa

<!-- translations:start -->
<p align="center"><a href="../synthesis-llm.md">English</a> · <a href="synthesis-llm.ko.md">한국어</a> · <a href="synthesis-llm.zh.md">中文</a> · <a href="synthesis-llm.ja.md">日本語</a> · <a href="synthesis-llm.ru.md">Русский</a> · <a href="synthesis-llm.es.md">Español</a> · <a href="synthesis-llm.fr.md">Français</a></p>
<!-- translations:end -->
LLM-Wiki kommt mit zwei Synthese-Pfaden. Der Default ist eine deterministische
Heuristik, die nie das Netzwerk aufruft: sie produziert vorhersagbare, idempotente
Markdown-Templates aus dem Research-Graph. Der optionale **LLM-Upgrade-Pfad**
ersetzt diese Templates durch Prosa, die Claude bei jedem Compile schreibt,
während jede andere Invariante (Idempotenz, Citation-Tracking,
hash-stabile Bodies) intakt bleibt.

Diese Seite behandelt, wann du ihn aktivierst, was er kostet, welche Daten deine
Maschine verlassen und wie du den Output inspizierst.

## Was er tut

Beide Pfade konsumieren dieselben `_PagePlan`-Inputs (Node-IDs, Namen, Typen,
Descriptions, Source-Paths). Der Unterschied ist der Body.

**Heuristik (`generator: heuristic-v1`)**

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

Liest sich wie ein Datenbank-Dump. Nützlich, deterministisch und heute ausgeliefert.

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

Liest sich wie ein redaktioneller Digest. Das Modell ist darauf beschränkt, Fakten,
die in den Inputs vorliegen, *neu auszudrücken* — jeder Absatz, der einen Knoten benennt,
endet mit einer `[node_id]`-Citation, und Bodies, die Citations weglassen (oder kürzer als
80 Zeichen sind), werden abgelehnt und fallen auf die Heuristik zurück.

## Prompt-Form

Zwei Blöcke: ein langer, stabiler System-Block, in
`cache_control: ephemeral` gewickelt, und eine Per-Page-User-Message, die je nach Kind variiert.

### System-Block (gecacht, identisch über Seiten hinweg)

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

Der volle Block sind ~500 Tokens. Siehe
[`llm_wiki/llm_synthesis.py`](../llm_wiki/llm_synthesis.py) für den
kanonischen Text. Jede Byte-Änderung dort invalidiert den Prompt-Cache für
jede nachfolgende Seite in einem Lauf, weshalb der Regel-Text bewusst eingefroren ist.

### User-Message (per Page, NICHT gecacht)

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

Der EDITORIAL-ANGLE-Block ist der deterministische Heuristik-Body — dem Modell
wird gesagt, diese exakten Fakten umzuformulieren / neu zu organisieren statt nach
neuen zu greifen. INPUTS sind auf 25 Knoten gedeckelt und nach Intra-Page-Degree gerankt,
sodass die signalstärksten Contributors im Prompt landen, wenn ein Plan mehr hat.

## Wie du ihn aktivierst

```sh
pip install llm-research-wiki[synthesis-llm]
export LLM_WIKI_SYNTHESIS_LLM=1
export ANTHROPIC_API_KEY=sk-...
python -m llm_wiki.cli project compile
```

Überschreibe das Modell mit `LLM_WIKI_SYNTHESIS_MODEL` (Default
`claude-sonnet-4-6`). Anthropic SDK ≥ 0.40 ist erforderlich.

Der Pfad aktiviert sich nur, wenn **alle drei** wahr sind:

1. `LLM_WIKI_SYNTHESIS_LLM` ist `1`/`true`/`yes`/`on`.
2. `ANTHROPIC_API_KEY` ist nicht leer (oder du setzt `synthesis.api_key` in der
   `.llm-wiki/config.json` deines Projekts).
3. Das `anthropic`-Package kann importiert werden.

Wenn etwas davon fehlt, wird eine informative Zeile nach stderr geloggt
(`[llm-wiki] LLM synthesis disabled (...)`) und auf die Heuristik zurückgefallen.

Ist der LLM-Pfad aktiv, aber eine einzelne Seite schlägt fehl — Network-Blip, 401, 429 —,
fällt diese Seite auf die Heuristik zurück, mit einem einzelnen stderr-Log pro
Error-Class pro Compile. Der Compile läuft weiter.

## Kosten

Jede Synthese-Seite macht einen `messages.create`-Call. Der System-Block
(Style-Regeln + Ontology-Recap, ~500 Tokens) ist in
`cache_control: ephemeral` gewickelt, aber auf Sonnet 4.6 ist das minimale cachefähige Prefix
2048 Tokens — sodass bei der aktuellen Größe der Cache-Marker zwar gesetzt ist, aber
nicht tatsächlich greift. Plane mit voller Input-Bepreisung pro Seite; erweitere die
Präambel oder wechsle zu einem Modell mit niedrigerem Cache-Floor (z. B. Sonnet 4.5 bei
1024 Tokens), wenn Cache-Reads zählen.

Per-Page-Token-Kosten (typisch, mit einem 25-Input-Cap auf Inputs):

| | System | User-Message | Output | Total in / out |
|---|---:|---:|---:|---:|
| pulse | ~500 | ~600 | ~250 | ~1100 / ~250 |
| daily_digest | ~500 | ~700 | ~200 | ~1200 / ~200 |
| weekly | ~500 | ~700 | ~250 | ~1200 / ~250 |
| topic | ~500 | ~900 | ~300 | ~1400 / ~300 |
| comparison | ~500 | ~900 | ~250 | ~1400 / ~250 |
| field_overview | ~500 | ~900 | ~300 | ~1400 / ~300 |

Ein typischer Compile dieses Repositories produziert heute 5–10 Synthese-Seiten
(pulse + eine Handvoll daily/weekly/topic/comparison/field overviews). Zu
Sonnet 4.6 Listenpreisen (`$3/M` Input, `$15/M` Output, kein Cache-Hit bei
dieser Präambel-Größe):

```
per page (uncached): ~1300 * $3/1M + ~270 * $15/1M ≈ $0.0080
                     × 10 pages              total ≈ $0.080
```

Wechselst du zu Haiku 4.5 (`$1/M` Input, `$5/M` Output), kostet derselbe Compile
ungefähr `~$0.027`. Starte vorher mit `LLM_WIKI_SYNTHESIS_DRY_RUN=1`, wenn du
die Prompt-Form ohne Token-Verbrauch bestätigen willst.

## Privacy

Es werden nur Graph-Metadaten gesendet: Node-IDs, Node-Namen, Typen, die ersten ~280
Zeichen der Descriptions und die Liste der beitragenden Source-Paths.
**Source-Document-Bodies werden nicht gesendet.** Wenn ein vollständiges Paper-Markdown
unter `data/research/...` liegt, verlässt nichts davon deine Maschine; das Modell
sieht nur, dass das Paper existiert, welchen Typ es hat und wie es sich zu anderen Knoten verbindet.

Wenn das für deinen Use-Case immer noch zu viel ist, lass die Env-Var ungesetzt — der
Heuristik-Pfad läuft vollständig offline und ist der Default.

## Ausschalten / Fallback

Unset die Env-Var (oder setze sie auf `0`) und führe erneut aus:

```sh
unset LLM_WIKI_SYNTHESIS_LLM
python -m llm_wiki.cli project compile
```

Nachfolgende Compiles erzeugen die betroffenen Synthese-Seiten mit dem
Heuristik-Generator neu. Da Page-Rewrites über `content_hash` gegated sind,
werden nur Seiten überschrieben, deren Body sich tatsächlich verschoben hat.

## Output inspizieren

LLM-generierte Seiten sind im On-Disk-Frontmatter getaggt:

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

Der einfachste Weg, Outputs zu vergleichen, ist ein Diff zwischen zwei Compiles:

```sh
git diff --no-index \
  baseline/.llm-wiki/wiki/syntheses/pulse.md \
  upgraded/.llm-wiki/wiki/syntheses/pulse.md
```

Das append-only `.history.jsonl`-Ledger unter `.llm-wiki/wiki/syntheses/`
hält das Generator-Label für jede Rewrite fest, sodass du auditieren kannst, wann eine
Seite von Heuristik zu LLM (oder zurück) wechselte.
