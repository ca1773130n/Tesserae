# Frontend-Redesign — annotierter Route-Walkthrough

<!-- translations:start -->
<p align="center"><a href="../frontend-redesign.md">English</a> · <a href="frontend-redesign.ko.md">한국어</a> · <a href="frontend-redesign.zh.md">中文</a> · <a href="frontend-redesign.ja.md">日本語</a> · <a href="frontend-redesign.ru.md">Русский</a> · <a href="frontend-redesign.es.md">Español</a> · <a href="frontend-redesign.fr.md">Français</a></p>
<!-- translations:end -->
Dieses Dokument ist eine geführte Tour durch jede sichtbare Route der neu gestalteten statischen Tesserae-Site. Es ergänzt das High-Level-Modell in [`architecture.md`](architecture.de.md) und die Status-Tabelle in [`feature-map.md`](feature-map.de.md).

Die Site liegt nach `tesserae project compile` unter `.tesserae/site/`. So erkundest du sie lokal:

```bash
tesserae project serve --port 8765
# open http://127.0.0.1:8765/
```

Jede Route ist eine statische HTML-Datei mit zwei Siblings (`<page>.txt`, `<page>.json`) für KI-Consumer. Site-weite KI-Exporte (`llms.txt`, `llms-full.txt`, `graph.jsonld`, `sitemap.xml`, `rss.xml`, `robots.txt`, `ai-readme.md`, `manifest.json`) sind am Ende dieses Dokuments beschrieben.

Status-Legende: ✅ ausgeliefert · ⚠ in Arbeit.

## Konventionen über alle Seiten

Jede Leaf-Seite folgt derselben Anatomie (§3.3 der Design-Spec):

```
breadcrumbs
eyebrow (type · last updated · ≈ reading time)
TITLE
right-rail TOC (sticky on desktop, drawer on mobile)
lead paragraph
rendered markdown body
Mentions in the corpus  — bullets with badges + counts
Related (4-signal ranked) — card grid
Source provenance       — file path, line excerpt
Activity                — sparkline, weekly mentions
AI siblings footer      — links to the .txt and .json
```

Site-weite Chrome:

- **Top-Bar.** Logo + Projektname links, Such-Trigger + Theme-Toggle rechts.
- **Left-Rail** (Desktop ≥ 1024 px): hierarchischer Baum — Home, Recent activity, Sources, Concepts, Entities, Papers, Repos, Topics, Syntheses, Open questions, Sessions, Timeline, Graph view, About. Counts kommen aus `manifest.json`.
- **Bottom-Nav** (Mobile): Drawer-Rail kollabiert; Bottom-Nav bringt die meistgenutzten Kinds nach vorn.
- **Search-Palette.** `cmd+k` / `ctrl+k` / `/` — Fuzzy-Match über `search-index.json`, gescopet auf Wiki-Kinds. Recent Pages werden in `localStorage` persistiert.
- **Theme.** Default Light; der Toggle persistiert `data-theme="dark"` in `localStorage`. Vor dem Paint angewendet, um Flash zu vermeiden.

## Home

### `/` ✅

> _Screenshot: home.png_

Die Home-Seite ist ein Project-Pulse. Sie wird von der globalen `pulse`-Synthese (`wiki/syntheses/pulse.md`) getrieben, die bei jedem Compile neu erzeugt wird. Der Hero ist eine Stat-Row — Sources, Concepts, Papers, Open Questions — gefolgt von 1–3 „What's new this week“-Cards aus dem aktuellsten `pulse`-Body. Unter dem Hero verlinken kuratierte Einstiegspunkte auf die Index-Seite jeder Kind, damit ein Erstbesucher reindrillen kann, ohne die Nav lesen zu müssen.

Das ist die Seite, auf der du einen LLM-Agent zuerst landen lässt; sie liefert die signalstärkste Korpus-Zusammenfassung. Cards verlinken auf Leaf-Pages, nicht zurück auf Indizes.

**Bemerkenswerte Interaktionen.** Klicks auf die Stat-Row scrollen zum passenden Kind-Index oder navigieren dorthin. Der Hero-Copy ist editierbar — wenn du `wiki/overview.md` selbst schreibst, übernimmt die Home-Seite das beim nächsten Compile.

**Verwandte Routen.** [Timeline](#timeline) für das Activity-Log, [Syntheses](#syntheses) für die Langform, [Graph](#graph-view) für die räumliche Sicht.

## Sources

Das sind die L1-Rohdokumente — Dateien in `data/`, `docs/` und dem in `.tesserae/config.json` referenzierten Projektbaum. Jede Quelle wird zu einem `SourceDocument`-Knoten (oder `Paper` / `Repository`) und bekommt eine Wiki-Seite, die der `WikiLayerProjector` projiziert.

### `/sources/` ✅

> _Screenshot: sources-index.png_

Eine Tabelle aller Rohdokumente, die der Korpus kennt. Spalten: Typ-Badge (Document / Paper / Repository / Project), Titel, Mention-Count, Last-Updated. Die Tabelle ist zebrastreifen-formatiert, Hover zeigt eine 1-zeilige Preview, und das Typ-Badge ist über die Search-Palette filterbar (`/ kind:papers`).

Das ist der Index des Agenten, wenn er die literale Evidenz aufzählen muss, auf der das Wiki gebaut ist.

**Verwandte Routen.** [Papers](#papers) für die Paper-only-Sicht, [Repos](#repos) für Repos-only, [Concepts](#concepts) für die Extracted-From-Sicht.

### `/sources/<slug>.html` ✅

> _Screenshot: source-detail.png_

Ein einzelnes Source-Dokument, gerendert durch die Stdlib-Markdown-Pipeline (`tesserae/site/markdown.py`). Der Body ist das originale Markdown mit sicherem Link/Image-Rendering. Unter dem Body:

- **Mentions** — jedes Concept / jede Entity / jedes Paper, das aus dieser Quelle extrahiert wurde, mit Edge-Type-Badges.
- **Backlinks** — jede andere Wiki-Seite, die hierher verlinkt.
- **Related** — vier-Signal-gerankt (direkter Link 3.0 + Source-Overlap 4.0 + Adamic-Adar 1.5 + Typ-Affinität 1.0).
- **Source-Provenance** — originaler File-Path + erste 12 Zeilen der Rohdatei als Evidenz.
- **Activity** — Sparkline wöchentlicher Mentions über die letzten 12 Wochen.
- **AI-Siblings-Footer** — `<page>.txt`-Plain-Text-View, `<page>.json`-strukturierter-Record.

**Bemerkenswerte Interaktionen.** Auto-verlinkte URLs und arXiv-IDs im Body; Click-to-Copy auf Code-Blöcken; das Right-Rail-TOC trackt Scroll.

## Concepts

Concepts sind die wiederkehrenden Ideen, Begriffe, Algorithmen, Architekturen und Methodiken, die über den Korpus hinweg extrahiert werden. Sie decken `Concept`, `TechnicalTerm`, `Algorithm`, `MathematicalConcept`, `MethodologicalConcept`, `ArchitecturePattern`, `TrainingParadigm`, `InferenceStrategy`, `EvaluationProtocol`, `Task`, `Capability`, `ObjectiveFunction` ab.

### `/concepts/` ✅

> _Screenshot: concepts-index.png_

Ein facetten-filterbares Card-Grid. Jede Card trägt das Typ-Badge, Titel, 1-zeilige Definition, Mention-Count und Last-Updated-Datum. Die Seite unterstützt Typ-Filter über Tag-Chips (Algorithm, Architecture, Training paradigm, …) und sortiert standardmäßig nach Mention-Count.

Das ist der Ort, an den du gehst, um zu fragen „Worüber spricht dieser Korpus?“.

**Verwandte Routen.** [Papers](#papers) — Concepts werden typischerweise in Papers eingeführt, [Topics](#topics) — Concepts clustern zu Topics.

### `/concepts/<slug>.html` ✅

> _Screenshot: concept-detail.png_

Eine reiche Concept-Seite mit einer synthetisierten Definition (oder dem ersten Absatz der maßgeblichsten Quelle, falls keine Synthese verfügbar ist), einer Liste aller Mentions über den Korpus, gerankten verwandten Nachbarn und der Activity-Sparkline.

Der „Mentions in the corpus“-Abschnitt ist der tragende für Agenten — er listet jedes Paper / jede Source / jedes Repo, das das Concept referenziert, mit einem 1-zeiligen Excerpt zum Kontext.

**Bemerkenswerte Interaktionen.** Das Right-Rail-TOC trackt `<h2>` / `<h3>` im Body; das Related-Card-Grid ehrt den Vier-Signal-Score, sodass Nachbarn relevant statt nur benachbart wirken.

## Entities

Entities sind die benannten, identifizierbaren Dinge im Korpus: `Model`, `Dataset`, `Benchmark`, `Metric`, `Organization`, `Person`. Sie werden aus Graph-Knoten projiziert, und ihre Seiten betonen Claims und Ergebnisse statt Prosa.

### `/entities/` ✅

> _Screenshot: entities-index.png_

Eine typ-facettierte Tabelle. Spalten: Typ-Badge, Name, Summary (erster Satz des Frontmatter-`description`, falls vorhanden, sonst erster Absatz des Bodys), Mention-Count. Filterbar per Typ-Chip.

### `/entities/<slug>.html` ✅

> _Screenshot: entity-detail.png_

Die Detail-Seite stellt drei Abschnitte in den Vordergrund:

- **Claims** — `ContributionClaim`-, `PerformanceClaim`-, `ComparisonClaim`-, `LimitationClaim`-, `CausalClaim`-Kanten, die diese Entity berühren, mit Evidence-Excerpts inline. (Claim-Knoten selbst bekommen keine eigene URL — sie tauchen hier als Bullets auf.)
- **Reported results** — jedes `Result` / `evaluated_on` / `reports_result`, das mit dieser Entity verknüpft ist, gelistet mit Metric + Score + Paper-Provenance.
- **Mentions in the corpus** — gleiche Form wie Concept-Seiten.

Das ist die richtige Seite, wenn ein Agent „Was wissen wir über Modell X?“ oder „Auf welchen Datasets wird Benchmark Y berichtet?“ beantworten muss.

## Papers

Papers sind Forschungsliteratur, die als First-Class-Evidenz behandelt wird. Das Redesign hat sie aus dem generischen Source-Pool herausgenommen und ihnen eine dedizierte Kind gegeben, damit wir paper-spezifische Affordances rendern können.

### `/papers/` ✅

> _Screenshot: papers-index.png_

Ein facetten-filterbares Card-Grid mit Year-, Topic- und Family-Chips. Jede Card: Titel, Autoren (erste drei + „et al.“), 1-zeiliger Abstract-Excerpt, Year-Badge, Mention-Count. Sortiert standardmäßig absteigend nach Jahr.

### `/papers/<slug>.html` ✅

> _Screenshot: paper-detail.png_

Ein Paper-Card-Layout: Titel, Autoren, Jahr, Abstract-Excerpt, dann Abschnitte für:

- **Contributions** — `ContributionClaim`-Kanten vom Paper.
- **Results** — `reports_result`-Kanten mit Metric + Score.
- **Comparisons** — `compares_against`-Kanten.
- **Related concepts** — `introduces` / `extends` / `uses`-Kanten.
- **Open questions** — `OpenQuestion` über das Paper verknüpft.

ArXiv-Links werden via `tesserae/site/markdown.py` auto-verlinkt; das Right-Rail-TOC spiegelt die obige Section-Liste.

## Repos

Repos sind Software-Projekte — `Repository`, `Project`, `CodeProject`. Das Redesign rendert explizit keine Per-Class-/Per-Function-HTML-Seiten; Repo-Seiten fassen die Projektfläche zusammen und verlinken raus auf den Source-Tree.

### `/repos/` ✅

> _Screenshot: repos-index.png_

Ein Card-Grid mit Sprach-Badges. Jede Card: Name, 1-zeiliger README-Excerpt, Primärsprache(n), Star-Count falls bekannt, Last-Updated.

### `/repos/<slug>.html` ✅

> _Screenshot: repo-detail.png_

Die Repo-Seite zeigt:

- **README-Excerpt** — die ersten ~600 Zeichen der `README.md` des Repos, falls eine im Korpus liegt.
- **Dependencies** — Out-Edges vom Typ `depends_on` / `imports` / `uses` zu anderen Repos / Models / Concepts.
- **Implements** — `implemented_in`-Kanten von Papers (also „dieses Repo implementiert Paper X“).
- **Module-Overview** — Counts von Modulen / Klassen / Funktionen, aber keine Per-Function-Links — by design.
- **Related** — vier-Signal-gerankt.

Das ist die richtige Seite für einen Agent, der ein Repo aus einer Familie von Ansätzen wählen muss.

## Topics

Topics gruppieren Concepts in breitere Felder: `ResearchField`, `ResearchTopic`, `ProblemArea`, `ApproachFamily`, `Trend`. Topic-Seiten sind teils aus Graph-Knoten projiziert, teils synthetisiert — siehe [Syntheses](#syntheses) für die Field-Overview-Seiten, die die Topic-Intro treiben.

### `/topics/` ✅

> _Screenshot: topics-index.png_

Ein Card-Grid, das per Typ-Chip aufgeschlüsselt ist. Jede Card zeigt den Topic-Namen, das Parent-Field und die Stats „X papers · Y concepts · Z repos“.

### `/topics/<slug>.html` ✅

> _Screenshot: topic-detail.png_

Eine Topic-Seite führt mit einem Synthese-Absatz (wenn unter `wiki/syntheses/topic-<slug>.md` einer existiert) und listet:

- **Papers in this topic** — Tabelle.
- **Approach families** — Sub-Topics vom Typ `ApproachFamily`.
- **Concepts in scope** — Chip-Cloud.
- **Open questions** — verlinkte `OpenQuestion`-Knoten.
- **Related fields** — `belongs_to` / `rising_in`-Nachbarn.

**Verwandte Routen.** [Syntheses → topic-…](#syntheses) für die Langform-Narrative, [Concepts](#concepts) für die einzelnen Atome.

## Syntheses

Syntheses sind die übergeordneten Seiten, die der `SynthesisProjector` produziert. Sieben Kinds (pulse, daily_digest, weekly, topic, comparison, field_overview) decken die temporalen und strukturellen Schnitte des Korpus ab. Synthese-Bodies sind heute deterministische Templates; `TESSERAE_SYNTHESIS_LLM=1` ist der LLM-Upgrade-Hook (Stub).

### `/syntheses/` ✅

> _Screenshot: syntheses-index.png_

Der Index listet jede Synthese gruppiert nach Kind, innerhalb jeder Gruppe absteigend nach `generated_at` sortiert. Jede Reihe: Kind-Badge, Titel, 1-zeiliger Lead, Generated-At-Timestamp.

### `/syntheses/<slug>.html` ✅

> _Screenshot: synthesis-detail.png_

Eine Synthese-Seite rendert den Markdown-Body as-is. Frontmatter trägt `synthesis_kind`, `slug`, `sources`, `inputs`, `generated_at`, `generator`, `content_hash` — die Seite exponiert `synthesis_kind` und `generated_at` im Eyebrow. Unter dem Body:

- **Sources consumed** — die Ziele der `summarizes`-Kante — eines pro Quelle, aus der die Synthese geschöpft hat.
- **Inputs (graph nodes)** — die Ziele der `synthesizes`-Kante — jeder Knoten, der einfloss.
- **Related syntheses** — gleicher Kind / überlappende Inputs.

Die `pulse`-Synthese ist die Home-Seite; die daily/weekly-Syntheses ankern die [Timeline](#timeline)-Rail.

## Questions

Open Questions werden aus dem Korpus als `OpenQuestion`-Knoten extrahiert — Stellen, an denen ein Paper, eine Quelle oder eine Synthese explizit ein ungelöstes Problem markiert.

### `/questions/` ✅

> _Screenshot: questions-index.png_

Eine Listenansicht, eine Reihe pro Open Question. Spalten: Fragetext, Quellen, die sie aufgeworfen haben, verwandte Concepts, Alter (seit erstem Auftreten). Standardmäßig nach Aktualität sortiert.

### `/questions/<slug>.html` ✅

> _Screenshot: question-detail.png_

Eine fokussierte Seite zu einer einzigen Open Question mit:

- Dem wortgetreuen Fragetext.
- **Raised in** — Quellen / Papers / Syntheses, in denen die Frage auftaucht.
- **Related concepts** — worum es bei der Frage geht.
- **Adjacent questions** — gleiche Quelle oder geteilte Concepts.

Das ist die Seite, auf der man landet, wenn ein Agent gefragt wird „Was ist in diesem Bereich noch unbeantwortet?“.

## Sessions

Sessions sind importierte lokale AI-Harness-Transkripte, normalisiert nach `.tesserae/harness_sessions/` und dann als durchsuchbares Projektgedächtnis gerendert. Der Import ist explizit über `tesserae project sessions discover --import` oder `tesserae project sessions import ...`; normale Site-Builds konsumieren nur bereits normalisierte Records.

### `/sessions/` ✅

> _Screenshot: sessions-index.png_

Der Sessions-Index gruppiert Top-Level-Claude-Code- und -Codex-Sessions für das Projekt. Cards/Tabellen zeigen Title, Harness, Model, Project-Path, Start-/Ende-Timestamps, Message-Count, Tool-Count, Token-Counts (falls bekannt), berührte Dateien, Commands und Preview-Text. Die Seite ist von der globalen Rail, den Home-Browse-Cards und Search-Palette-Einträgen vom Kind `session` aus verlinkt.

### `/sessions/<project>/<session>.html` ✅

> _Screenshot: session-detail.png_

Eine Session-Detail-Seite nutzt die geteilte Shell statt eines rohen Transkript-Dumps. Das Layout enthält einen Hero, einen Stat-Strip, ein High-Level-Summary, Timeline & Size, Decisions/Files/Commands/Tools/Errors, einen eingeklappten Subagent-Baum und einen Turn-by-Turn-Conversation-Block.

Die session-spezifische Left-Rail ersetzt die generische File-Tree-Rail durch User-/Assistant-Turn-Anker (`#turn-N`). User- und Assistant-Turns werden durch den Site-Markdown-Renderer gerendert; semantische Flächen wie Inline-Code, Command-/Tag-Markup, Pfade, Filenames und Hashtags werden zu kompakten Chips. Tool-Calls werden unter dem vorhergehenden Assistant-Turn eingeklappt, mit dunklen Code-/Tool-Backgrounds in Light- wie Dark-Theme.

Die aktuelle Detail-Typografie hält normale Conversation-Prosa kompakt bei 8 px, generische Conversation-Code-Fences bei 10 px, Bash-/Shell-Fenced-Code-Content bei 11 px, Tool-Details/Summary bei 10 px, Tool-Headers bei 8 px und Tool-Payload-Text bei 6 px. Siehe [`session-history.md`](session-history.de.md) für die Selector-Map und Publishing-Privacy-Checkliste.

## Timeline

Die Timeline-Seite ist das Activity-Log: Wann ist der Korpus gewachsen, welche Arten von Knoten wurden hinzugefügt, wie sieht es über Tage und Wochen aus?

### `/timeline/` ✅

> _Screenshot: timeline.png_

Die Seite hat drei Rails:

- **Activity-Heatmap** — 26-Wochen-SVG mit Monats- + Wochentag-Labels, Zellen nach Node-Add-Count koloriert. Jede Zelle verlinkt auf die Source-Seite `digest.md` des Tages, falls eine existiert.
- **Days** — letzte 60 datierte Tage, jede Reihe zeigt `<date> · X activity · Y papers`. Wenn das Datum eine `digest.md` hat, ist das Datum ein Link.
- **Syntheses-Rail** — jede Synthese nach Aktualität sortiert, Kind-Badge + Title + Timestamp.

Das ist die Seite, die man für die „Was ist passiert?“-Frage bookmarkt.

### `/timeline/<YYYY-MM-DD>.html` ✅

> _Screenshot: timeline-day.png_

Per-Day-Detailseiten — mit jedem Paper / Repo / Concept / jeder Synthese zu diesem Kalendertag — sind jetzt ausgeliefert. `render_timeline_day` (`tesserae/site/pages.py`) wird pro datiertem Tag über `StaticSiteBuilder` (`tesserae/site/__init__.py`) emittiert, und jede Seite wird in `manifest.json` als `timeline_day`-Route erfasst. Heatmap-Zellen verlinken direkt auf die Tagesseite (und fallen nur dann auf die Source-Seite `digest.md` des Tages zurück, wenn keine Per-Day-Route existiert).

## Graph-View

### `/graph/` ✅

> _Screenshot: graph.png_

Die interaktive Graph-Ansicht ist ein 3D-Force-Layout (3d-force-graph + Three.js, als CDN-Snapshot in `assets/` vendored) mit einer 2D-Fallback. Knoten sind nach `ResearchNodeType` eingefärbt. Edges zeigen ihren Typ als Label on Hover.

**Bemerkenswerte Interaktionen.**

- Hover auf Knoten → Tooltip mit Name, Typ, Mention-Count.
- Klick auf Knoten → Navigation zur Wiki-Seite.
- Hover auf Edge → Label mit der Relation (`uses` / `extends` / `compares_against` / …).
- Mausrad → Cursor-verankerter Zoom (zoomt Richtung Cursor, nicht zur Mitte).
- Drag → Orbit (3D) oder Pan (2D).
- 2D/3D-Toggle oben rechts.

Das in die Seite eingebettete Payload ist auf `MAX_GRAPH_NODES = 1500` gedeckelt (siehe [`pages.py`](../tesserae/site/pages.py)). Der volle Graph ist immer unter `/graph.json` für Tooling verfügbar. Code-Graph-Knoten (`CodeClass`, `CodeFunction`, `Dependency`, …) werden by design aus der Visualisierung gefiltert.

**Verwandte Routen.** Jede Wiki-Seite verlinkt in eine fokussierte Subgraph-View.

## About

### `/about.html` ✅

> _Screenshot: about.png_

Eine statische Seite, die das Schema erklärt (jeder `ResearchNodeType` und die Edge-Whitelist mit 1-Zeilen-Beschreibungen), die Build-Info (Commit-SHA, Generator-Version, Generated-At-Timestamp) und das KI-Export-Inventar.

Das ist die richtige Seite, um einen neuen Contributor darauf zu erden, welche Typen existieren und wofür jeder ist.

## AI-Siblings — wie jede Seite auch Daten ist

Tesserae liefert jede Seite in drei Formen: das menschliche HTML, ein Plain-Text-Sibling und ein strukturiertes JSON-Sibling. Plus site-weite Exporte für Crawler und Agenten.

### Per-Page `<page>.txt` ✅

Plain-Text-View einer Seite — keine Nav, kein Styling, keine Markdown-Dekoration über das hinaus, was der Body explizit nutzt. Richtig, wenn ein Agent eine einzelne Seite als Kontext ingestieren will, ohne einen HTML-Scraper zu schreiben.

```bash
curl http://127.0.0.1:8765/concepts/diffusion-model.txt
```

### Per-Page `<page>.json` ✅

Ein strukturierter Record:

```json
{
  "title": "...",
  "kind": "concepts",
  "body": "raw markdown body",
  "body_text": "plain-text body",
  "links": ["..."],
  "source_path": "data/...",
  "frontmatter": { ... }
}
```

Richtig, wenn ein Tool typisierten Zugriff braucht — die Link-Liste, das Frontmatter, das Kind-Tag — ohne Markdown-Parser.

### `/llms.txt` ✅

Der llmstxt.org-Kurzindex. Eine einzige Seite, die jede Kind mit den relevantesten Einträgen pro Kind listet. Richtig für den ersten Request, den ein LLM-Agent macht, wenn er die Site entdeckt.

### `/llms-full.txt` ✅

Die llmstxt.org-Langform: jede Wiki-Seite aneinandergehängt. Gedeckelt bei 5 MB; wird der Cap erreicht, beendet ein `[TRUNCATED — see graph.jsonld for the full set]`-Marker die Datei. Richtig, wenn ein Agent das Budget hat, den ganzen Korpus in einem Request zu ingestieren und einen 5-MB-Kontext mitbringt.

### `/graph.json` ✅

Das volle `ResearchGraph`-Payload — inklusive Code-Graph-Knoten, die keine HTML-Seiten haben. Richtig, wenn ein Tool den vollständigen Graphen für eigene Analyse will (MCP-, Cognee-, Graphiti-Consumer lesen das alle).

### `/graph.jsonld` ✅

Ein schema.org-`Dataset`-JSON-LD. Nur Wiki-Layer-Knoten (keine Code-Knoten). Richtig für Crawler, die strukturierte Daten verstehen — Google Knowledge Graph, Search-Indexer, schema.org-aware Aggregatoren.

### `/search-index.json` ✅

Der Palette- + Page-Search-Index. Nur Wiki-Layer-Kinds. Richtig, wenn man eine Drittanbieter-Search-UI integriert; das Schema ist eine Liste von `{kind, title, slug, body, score_hints}`-Einträgen.

### `/sitemap.xml` ✅

Jede emittierte Route mit `lastmod` aus dem Frontmatter (`generated_at`, `updated_at`, `published_at`, `date`). Richtig für Suchmaschinen.

### `/rss.xml` ✅

Letzte 30 Syntheses, neueste zuerst. Richtig für „Subscribe to this wiki“ — RSS-Reader, IFTTT, Mailing-Listen.

### `/robots.txt` ✅

Permissiv — crawl + index everything. Das Wiki ist dafür gedacht, von Agenten gelesen zu werden.

### `/ai-readme.md` ✅

Eine maschinenlesbare Site-Map für KI-Agenten, die HTML nicht gut sprechen. Listet jedes obige Artefakt mit Zweck und einer 1-zeiligen Beschreibung, wann jedes Format richtig ist.

### `/manifest.json` ✅

Ein sha256-+-Size-Record für jede emittierte Datei in der Site. Wird genutzt von:

- Dem Compile-twice-Idempotenz-Test (`tests/test_project_e2e_redesign.py`).
- Downstream-Tooling, das erkennen will „hat sich diese Site seit dem letzten Besuch geändert?“ ohne vollen Diff.
- Dem Deploy-Command, um Pushes kurzzuschließen, wenn sich nichts geändert hat.

## Das richtige Format wählen

| Wenn du … bist | Lies |
|---|---|
| Menschlicher Erstbesucher | `/` dann reindrillen in [Concepts](#concepts) oder [Papers](#papers) |
| Mensch, der „what's new“ will | [Timeline](#timeline) und aktuelle [Syntheses](#syntheses) |
| Mensch, der Struktur will | [Graph-View](#graph-view) |
| LLM-Agent mit einer Query | `<page>.json` für typisierten Zugriff |
| LLM-Agent mit einer Query, budgetbegrenzt | `<page>.txt` |
| LLM-Agent, der alles ingestiert | `/llms-full.txt` (≤ 5 MB) oder `/graph.jsonld` (uncapped) |
| Tool, das eine Custom-UI baut | `/search-index.json` + `/graph.json` |
| Suchmaschine | `/sitemap.xml` + `/graph.jsonld` |
| Abonnent | `/rss.xml` |
| Change-Detector | `/manifest.json` |

## Verwandte Dokumentation

- [Architektur](architecture.de.md) — das Drei-Schichten-Modell, Modul-Karte, Idempotenz-Story.
- [Feature-Map](feature-map.de.md) — jedes Feature mit Status, Source-Dateien und Links hierhin.
- [Quickstart](quickstart.de.md) — minimaler Pfad von `project init` zu einer browserbaren Site.
- [Self-Dogfood-Demo](self-dogfood.de.md) — Tesserae gegen das eigene Repo laufen lassen, inklusive dieser Site.
