# Architektur

<!-- translations:start -->
<p align="center"><a href="../architecture.md">English</a> · <a href="architecture.ko.md">한국어</a> · <a href="architecture.zh.md">中文</a> · <a href="architecture.ja.md">日本語</a> · <a href="architecture.ru.md">Русский</a> · <a href="architecture.es.md">Español</a> · <a href="architecture.fr.md">Français</a></p>
<!-- translations:end -->
Tesserae ist eine **Kontext-Engine**. Sie rekonstruiert eine sich selbst verbessernde Wissensbasis aus deinem Projekt und übergibt sie Agenten als sofort nutzbaren Kontext. Sie ruht auf drei Säulen: (1) **Sitzungsüberwachung** — Live-Agenten-/Arbeitssitzungen beobachten und Erkenntnisse erfassen, sobald sie entstehen; (2) **autonome, proaktive Wissensaufnahme** — eine Pipeline + Supervisor-Schleife ziehen und re-extrahieren Wissen kontinuierlich und verbessern die Basis, statt auf Anweisungen zu warten; (3) **Dokumente/Kontext auf Abruf** — vom Nutzer angeforderte Artefakte, kompiliert aus derselben Basis. Der typisierte Graph, der Markdown-Vault und die statische Site sind *Projektionen* der Wissensbasis; die Engine ist die Schleife, die sie frisch hält und Agenten speist.

Darunter verwandelt Tesserae ein Verzeichnis mit Quellmaterial in einen kontrollierten, typisierten Knowledge Graph und projiziert diesen Graph über eine langlebige Markdown-Wiki-Schicht in eine statische, KI-freundliche Website. Das Redesign vom April 2026 hat die Projektionsseite um ein dreischichtiges Modell nach Karpathy reorganisiert: Rohbelege bleiben roh, ein typisierter Graph regiert die Ontologie, und eine Markdown-Wiki-Schicht sitzt zwischen Graph und gerendertem Output. Die statische Site ist ein *Renderer* dieser Wiki-Schicht statt einer direkten Ausgabe des Graphen, mit der kontrollierten Ontologie in [`tesserae/research_graph.py`](../tesserae/research_graph.py) als Schema. Der Meilenstein **v0.5.0** (Juni 2026) ergänzte das Engine-Rückgrat, das alle drei Säulen antreibt — siehe *Engine-Rückgrat* und *On-Demand-Kontext-Compiler* unten.

## Das dreischichtige Karpathy-Modell

Andrej Karpathys Framing für LLM-freundliche Wissensdatenbanken unterscheidet drei Schichten, jede mit eigener Beständigkeitsgarantie:

| Schicht | Inhalt | Repo-Ort | Owner |
|---|---|---|---|
| L1 — Rohquellen | Die literalen Bytes, die der Nutzer geschrieben oder gesammelt hat. Append-only. | `data/`, `docs/`, in `.tesserae/config.json` referenzierte Projektbäume | der Nutzer |
| L2 — Wiki | Typisierte Markdown-Seiten (sources, concepts, entities, papers, repos, topics, syntheses, questions) mit YAML-Frontmatter. Idempotent: bei jedem Compile neu erzeugt, aber nur überschrieben, wenn sich Content-Hashes ändern. | `.tesserae/wiki/` | `WikiPageStore`, `WikiLayerProjector`, `SynthesisProjector` |
| L3 — Rendered | Die statische HTML-Site, AI-Sibling-Exporte, Suchindex, Sitemaps, JSON-LD. Wird bei jedem Compile gelöscht und neu geschrieben, aber Byte-stabil über Reruns. | `.tesserae/site/` | `StaticSiteBuilder` (`tesserae/site/`) |

Das Schema spannt sich über alle drei Schichten als separate Achse: `ResearchGraph` in `graph.json` ist die kontrollierte Ontologie, gegen die L2-Seiten verlinken, und die `ResearchNodeType`-/Edge-Whitelist in [`tesserae/research_graph.py`](../tesserae/research_graph.py) ist die Source of Truth dafür, welche Typen überhaupt existieren.

Das Redesign hat L2 explizit hinzugefügt. Vor April 2026 wurde die statische Site direkt aus `graph.json` projiziert; die Wiki-Schicht existierte nur innerhalb des Obsidian-Vault-Exports. Sie herauszulösen brachte uns:

- Eine einzige menschenbearbeitbare Fläche (`.tesserae/wiki/` in Obsidian oder jedem Markdown-Editor öffnen).
- Idempotente Rebuilds: ein erneutes `project compile` erzeugt null File-Diffs, solange sich der Source-Content nicht geändert hat.
- Ein Evolutions-Log: Synthese-Seiten sammeln sich über die Zeit an und lassen das Projekt sich selbst erzählen.

## Pipeline

```
data/, docs/, src/                                    (L1 raw)
        │
        ▼  project compile  (tesserae/project.py)
┌───────────────────────────┐
│ ResearchGraphExtractor    │   deterministic + selective Claude
│ + canonicalization        │
└───────────┬───────────────┘
            │
            ▼
┌───────────────────────────┐
│ ResearchGraph (graph.json)│   schema: research_graph.py
└───────────┬───────────────┘
            │
            ├──▶ WikiLayerProjector   (one page per L1/L2 node)
            ├──▶ SynthesisProjector   (pulse, daily, weekly, topic, …)
            │
            ▼
┌───────────────────────────┐
│ .tesserae/wiki/  (L2 md)  │   sources/, concepts/, entities/,
│                            │   papers/, repos/, topics/,
│                            │   syntheses/, questions/
└───────────┬───────────────┘
            │
            ▼  StaticSiteBuilder.write_site
┌───────────────────────────┐
│ .tesserae/site/  (L3 html)│   index.html, <kind>/index.html,
│                            │   <kind>/<slug>.html,
│                            │   per-page .txt + .json siblings,
│                            │   llms.txt, llms-full.txt,
│                            │   graph.json, graph.jsonld,
│                            │   search-index.json,
│                            │   sitemap.xml, rss.xml,
│                            │   robots.txt, ai-readme.md,
│                            │   manifest.json
└───────────────────────────┘
```

Jeder Schritt ist inkrementell. Der Graph-Extraktor nutzt die Content-Hashes aus `manifest.json`, um unveränderte Quelldateien zu überspringen. `WikiPageStore.write_page` gibt `False` zurück (und überspringt den Write), wenn der Body-Hash mit dem auf der Festplatte übereinstimmt. `StaticSiteBuilder` löscht und überschreibt `.tesserae/site/`, aber sein Output ist deterministisch — siehe „Idempotenz-Story“ unten.

## Datenfluss des Kontext-Compilers

Der On-Demand-Kontext-Compiler ([`tesserae/context_compiler.py`](../tesserae/context_compiler.py)) ist der Vorzeigepfad von Säule 3. Bei einer Abfrage und/oder expliziten Seed-Knoten-IDs baut `compile_context` ein maßgeschneidertes, **zitiertes** Markdown-Bundle direkt aus dem Graphen und gibt es im Speicher zurück — er schreibt nichts unter `.tesserae/`.

```
query / seeds
     │
     ▼  1. Seed-Auflösung
        explizite Seeds (nur behalten, wenn im Graphen vorhanden) + hybrid_search()-Treffer, dedupliziert, stabile Reihenfolge
     │
     ▼  2. PPR-Expansion
        retrieval.ppr.personalized_pagerank rankt die tiefenbegrenzte k-Hop-Nachbarschaft;
        leeres Ergebnis (unverbundene Seeds) → Rückfall auf die Seed-Reihenfolge (Bundle nie leer)
     │
     ▼  3. Budgetbegrenzte Auswahl
        PPR-Reihenfolge durchlaufen, jeden zitierten Knoten-Body aufnehmen, bis der nächste Body
        `budget` Zeichen überschreiten würde (budget <= 0 = unbegrenzt; Überlaufmarker an Wortgrenze)
     │
     ▼  4. Zusammenbau des zitierten Markdowns
        ein Abschnitt pro ausgewähltem Knoten + ein abschließender `## Citations`-Block.
        Der Body bevorzugt die projizierte Wiki-Seite (wenn ein Store und ein öffentlicher Wiki-Typ existieren),
        sonst die Knotenbeschreibung, sonst einen Minimal-Stub. Der Body ohne LLM bettet keinen
        Wanduhr-Zeitstempel ein → bytegleich für dasselbe (graph, query, seeds, depth, budget).
     │
     ▼  5. Optionale LLM-Synthese  (nur wenn synthesize=true UND ANTHROPIC_API_KEY vorhanden ist)
     ▼
   ContextBundle { query, seeds_used, ranked_nodes, selected_nodes,
                   citations[ContextCitation], body, synthesized,
                   char_budget_used, char_budget_total }
```

Standardwerte: `depth=2`, `budget=32000`. Der deterministische Zusammenbau (Schritte 1–4) ist der Vertrag; die LLM-Synthese ist rein additiv. Dieselbe Pipeline trägt den CLI-Befehl `project context`, das MCP-Werkzeug `compile_context` und die themenbezogenen Export-Slices (`slice_export_context_for_topic`, themenbezogene `llms.txt`).

## Modul-Karte

### Wiki + Synthese (L2)

| Modul | Verantwortung |
|---|---|
| [`tesserae/wiki_store.py`](../tesserae/wiki_store.py) | `WikiPage`-Dataclass, `WikiPageStore` für Filesystem-I/O. Stdlib-only YAML-Subset-Frontmatter-Parser. Body-Hash-Idempotenz. |
| [`tesserae/wiki_projector.py`](../tesserae/wiki_projector.py) | `WikiLayerProjector`: bildet jeden `ResearchGraph`-Knoten eines Wiki-Layer-Typs auf eine Markdown-Seite im richtigen `kind/`-Ordner ab. |
| [`tesserae/synthesis.py`](../tesserae/synthesis.py) | `SynthesisProjector`: deterministische Templates für pulse, daily_digest, weekly, topic, comparison, field_overview. Fügt `Synthesis`-Knoten und `synthesizes`-/`summarizes`-Kanten zurück in den Graph. |

### Graph + Ontologie

| Modul | Verantwortung |
|---|---|
| [`tesserae/research_graph.py`](../tesserae/research_graph.py) | `ResearchNodeType`-Enum (inkl. `SYNTHESIS`), Edge-Type-Whitelist (inkl. `synthesizes`, `summarizes`), Validierung. |
| [`tesserae/canonicalization.py`](../tesserae/canonicalization.py) | Alias-Kanonisierung + Near-Duplicate-Review-Queue. |
| [`tesserae/code_graph.py`](../tesserae/code_graph.py) | Deterministischer Python-AST-Extraktor für den Development-Slice. |
| [`tesserae/llm_extractor.py`](../tesserae/llm_extractor.py) | Selektiver Extraktor via Claude CLI/OAuth. |

### Site-Renderer (L3)

| Modul | Verantwortung |
|---|---|
| [`tesserae/site/__init__.py`](../tesserae/site/__init__.py) | `StaticSiteBuilder.write_site`: löscht und baut die Site neu, läuft jede Route durch, gibt Exporte + AI-Siblings + Manifest aus. |
| [`tesserae/site/pages.py`](../tesserae/site/pages.py) | Ein Renderer pro Route (home, indexes, detail pages, timeline, graph, about). `SiteContext` trägt vorberechnete Indizes, damit Renderer pur bleiben. |
| [`tesserae/site/components.py`](../tesserae/site/components.py) | HTML-Primitive: `breadcrumbs`, `card`, `badge`, `node_table`, `edge_list`, `sparkline_svg`, `heatmap_svg`, `toc`, `page_shell`, `ai_siblings_footer`. |
| [`tesserae/site/tokens.py`](../tesserae/site/tokens.py) | Design Tokens — CSS-Variablen, Light- + Dark-Themes, Layout, Typografie, hier werden alle Komponenten gestylt. |
| [`tesserae/site/js.py`](../tesserae/site/js.py) | Client-JS-Bundle: Search-Palette, Theme-Toggle, Sigma + 3D-Force-Graph-Ansicht. |
| [`tesserae/site/markdown.py`](../tesserae/site/markdown.py) | Stdlib-only Markdown-Renderer (Links, Autolinks, Code, Hervorhebungen, Überschriften). Keine externe Abhängigkeit. |
| [`tesserae/site/relevance.py`](../tesserae/site/relevance.py) | Vier-Signal-Relevanz-Scoring (direkter Link, Source-Overlap, Adamic-Adar, Typ-Affinität), das von jedem `Related`-Abschnitt benutzt wird. |
| [`tesserae/site/search.py`](../tesserae/site/search.py) | Builder für `search-index.json`. Nur Wiki-Layer-Kinds. |
| [`tesserae/site/sessions.py`](../tesserae/site/sessions.py) | Session-Index/Detail-Renderer für importierte Harness-Historie: Project-Memory-Summary-Sections, Conversation-Turn-Rail, Markdown-Transcript-Rendering und eingeklappte Tool-Use-Blöcke. |
| [`tesserae/site/exports.py`](../tesserae/site/exports.py) | `llms.txt`, `llms-full.txt`, `graph.jsonld`, `sitemap.xml`, `rss.xml`, `robots.txt`, `ai-readme.md`, Per-Page-`.txt`/`.json`-Siblings. |

### Pipeline-Orchestrierung

| Modul | Verantwortung |
|---|---|
| [`tesserae/project.py`](../tesserae/project.py) | `ProjectWiki.compile`: treibt Extraktion → Graph → Memory-Pässe → Wiki-Layer → Site. Besitzt `ProjectPaths` (`config`, `graph`, `manifest`, `wiki`, `site`, etc.). Entscheidet vorab, ob eine herkunftsbasierte (provenance) inkrementelle Kompilierung infrage kommt (durch `incremental_compile` gesteuert, Standard OFF). |
| [`tesserae/cli.py`](../tesserae/cli.py) | Flache verb-basierte CLI-Dispatch (~2.732 Zeilen nach dem Löschen der veralteten `project`/`wiki`-Subcommand-Gruppen). Die Verben – `init`, `compile`, `context`, `ask`, `refresh`, `serve`, `engine`, `export`, `vault`, `code`, `lab`, `config`, `projects`, `integrations` – werden als Metadaten in [`tesserae/cli_tree.py`](../tesserae/cli_tree.py) deklariert und aus diesem Baum verdrahtet, statt von Hand registriert zu werden. |
| [`tesserae/deploy.py`](../tesserae/deploy.py) | `export site --deploy`: pusht `.tesserae/site/` auf einen `gh-pages`-Branch via Worktree, aktiviert optional Pages via `gh`. |

### Engine-Rückgrat (v0.5.0 — Säulen 1 & 2)

Das Engine-Rückgrat ist die In-Process-Schleife, die Sitzungsüberwachung und autonome Re-Ingestion antreibt. Dasselbe `Pipeline.run()` ist der einzige Aktualisierungspfad, den CLI, der Supervisor-Daemon und (später) der MCP-Server alle aufrufen.

| Modul | Verantwortung |
|---|---|
| [`tesserae/engine/pipeline.py`](../tesserae/engine/pipeline.py) | `Pipeline`: sequenzieller Schrittausführer. Kodifiziert die prosaische Aktualisierungskette (Aufnahme → Kompilierung → Projektion/Veröffentlichung) als importierbares Objekt, das eine strukturierte `List[StepResult]` zurückgibt statt drucken-und-beenden, sodass jeder Aufrufer selbst entscheidet, wie er Ergebnisse darstellt. `run()` fängt `Exception` pro Schritt (lässt `KeyboardInterrupt`/`SystemExit` durch) und stoppt beim ersten Fehler. |
| [`tesserae/engine/daemon.py`](../tesserae/engine/daemon.py) | `Daemon`: Asyncio-Supervisor mit alleinigem Besitzer. Überwacht Quellverzeichnisse, den Obsidian-Vault und das Harness-Sitzungsverzeichnis; über ein Abbrechen-und-Neuplanen-Debounce fasst er eine Serie von `TriggerEvent`s zu genau einem `Pipeline.run()` zusammen. Verwendet die vorhandenen Watcher `watch.py` / `vault_watch.py` wieder (schreibt sie nicht neu), schreibt eine Pidfile und überlebt Ausnahmen im laufenden Betrieb. Über `engine` (`--interval`, `--debounce`, `--once`) verfügbar. |
| [`tesserae/watch.py`](../tesserae/watch.py), [`tesserae/vault_watch.py`](../tesserae/vault_watch.py) | Polling-Watcher, die sowohl vom eigenständigen Befehl `export site --watch` als auch von den Quell-/Vault-Spuren des Daemons wiederverwendet werden. |

### Selbstverbesserungs-Speicher (v0.5.0 — Säule 2)

Phase 5 aktivierte die persistente Selbstverbesserung. Der veränderliche Zustand pro Knoten liegt in einem `node_memory`-SQLite-Sidecar (innerhalb von `.tesserae/sqlite.db`), getrennt vom unveränderlichen Erstsichtungs-Stempel `node_provenance.first_seen_at` (Sidecar aus Phase 4). Die Kompilierung treibt eine Reihe deterministischer Pässe über den Graphen.

| Modul | Verantwortung |
|---|---|
| [`tesserae/memory/store.py`](../tesserae/memory/store.py) | `NodeMemoryRow` + store-unabhängige Accessoren (`read_memory`, `write_memory`, `bump_access`) über die `node_memory`-Tabelle — `decay_score`, `last_accessed_at`, `confidence`, `superseded`. Keine Aufrufstelle bettet rohes SQL ein. |
| [`tesserae/memory/decay.py`](../tesserae/memory/decay.py) | `compute_decay_score`: Ebbinghaus-artige Frischescore (neueste + am häufigsten aufgerufene zuerst) zum Ranking von Sitzungsbefunden. |
| [`tesserae/memory/supersede.py`](../tesserae/memory/supersede.py) | `run_supersede_pass` (**standardmäßig EIN**): deterministisches Urteil, das ein älteres Beinahe-Duplikat-Insight als durch ein neueres ersetzt markiert und eine `supersedes`-Kante hinzufügt. |
| [`tesserae/memory/insight_symbol_link.py`](../tesserae/memory/insight_symbol_link.py) | `run_insight_symbol_link_pass`: verknüpft Sitzungs-Insights über `discusses`-Kanten mit den Code-Symbolen, die sie behandeln. |
| [`tesserae/memory/reinforce.py`](../tesserae/memory/reinforce.py), [`tesserae/memory/contradiction.py`](../tesserae/memory/contradiction.py) | Helfer für Zugriffsverstärkung und Widerspruchserkennung über demselben Sidecar. |

Die Wiederkehr-Konfidenz ist in der Ausgabe numerisch: Die zeitliche Projektion stempelt die `confidence` jedes Fakts aus `NodeMemoryRow.confidence` (Text in SQLite, über `temporal.py` bereitgestellt) und greift nur dann auf `infer_confidence` zurück, wenn kein gespeicherter Wert existiert.

### Retrieval (v0.5.0 — Säulen 2 & 3)

| Modul | Verantwortung |
|---|---|
| [`tesserae/retrieval/hybrid.py`](../tesserae/retrieval/hybrid.py) | `hybrid_search`: local-first Hybrid-Retriever, der drei Spuren — Okapi BM25 (k1=1.5, b=0.75), case-folding lexikalische/FTS-artige Teilstring-Übereinstimmung und eine einsteckbare Embedding-Spur — per Reciprocal-Rank-Fusion (RRF, k=60) verschmilzt. Vollständig deterministisch. |
| [`tesserae/retrieval/ppr.py`](../tesserae/retrieval/ppr.py) | `personalized_pagerank`: Personalized PageRank im HippoRAG-2-Stil (arXiv:2502.14802) über den Graphen zur Multi-Hop-Seed-Expansion — bringt gut verbundene Knoten mehrere Hops von der Seed entfernt an die Oberfläche, nicht nur die 1-Hop-Nachbarschaft. |
| Embedding-Backend (Phase 6, Track B) | Das Standard-Backend der Embedding-Spur des Hybrids ist ein deterministisches Hash-Bucket-Pseudo-Embedding, das keine zusätzlichen Abhängigkeiten braucht; `sentence-transformers` (`all-MiniLM-L6-v2`) wird bevorzugt und lazy geladen, wenn die optionale Abhängigkeit installiert ist. Das MCP-Werkzeug `embedding_status` meldet das aktive Backend. |

### On-Demand-Kontext-Compiler (v0.5.0 — Vorzeigefeature von Säule 3)

| Modul | Verantwortung |
|---|---|
| [`tesserae/context_compiler.py`](../tesserae/context_compiler.py) | `compile_context`: das Vorzeigefeature von Säule 3. Kompiliert ein maßgeschneidertes, **zitiertes** Kontext-Bundle für ein Abfrage-/Seed-Set direkt aus dem Graphen — siehe *Datenfluss des Kontext-Compilers* unten. Gibt ein `ContextBundle` im Speicher (mit `ContextCitation`s) zurück; schreibt nichts auf die Festplatte. Über den CLI-Befehl `project context` und das MCP-Werkzeug `compile_context` verfügbar. |

### Persistenz-Ports + Graph-Stores

| Modul | Verantwortung |
|---|---|
| [`tesserae/ports/graph_store.py`](../tesserae/ports/graph_store.py) | `GraphStore`-Protokoll: `upsert_node`/`upsert_edge`, `get_node`, `iterate_nodes`, `query_subgraph`, `find_canonical` und die Löschfläche aus Phase 4 — `delete_node` und `delete_nodes_by_source` (löscht Knoten, deren Herkunftsmenge nach Entfernen der angegebenen Quellpfade leer wird, sodass dateiübergreifende Konzepte überleben). |
| [`tesserae/graph_stores/sqlite.py`](../tesserae/graph_stores/sqlite.py) | `SqliteGraphStore`: eigenständiger Backing-Store; besitzt die Sidecar-Tabellen `node_provenance` und `node_memory`. |
| [`tesserae/graph_stores/url_resolver.py`](../tesserae/graph_stores/url_resolver.py) | Löst eine Store-URL (`sqlite:///…`, `hypepaper-postgres://…`) zum passenden `GraphStore` auf, sodass der MCP-Server zur Laufzeit auf einen beliebigen Backing-Store zeigen kann. |

### Externe Adapter (in dieser Runde unverändert)

| Modul | Verantwortung |
|---|---|
| [`tesserae/obsidian_adapter.py`](../tesserae/obsidian_adapter.py) | Obsidian-Vault-Projektion (Graph-Coloring, Dataview-Dashboard, Roh-Assets). |
| [`tesserae/agent_harness.py`](../tesserae/agent_harness.py) | Harness-Exporte für Claude Code / Codex / Gemini / Kiro / Cursor / OpenCode. |
| [`tesserae/harness_sessions.py`](../tesserae/harness_sessions.py) | Inbound Discovery, Normalisierung und Storage von Claude-Code-/Codex-Sessions unter `.tesserae/harness_sessions/` plus redigierte Markdown-Zusammenfassungen. |
| [`tesserae/graphiti_adapter.py`](../tesserae/graphiti_adapter.py) | Temporal-Fact-JSONL + optionaler Live-Sync mit Graphiti. |
| [`tesserae/cognee_adapter.py`](../tesserae/cognee_adapter.py) | Cognee-Nodes/Edges-JSONL-Bundle und direkter Add-/Cognify-Pfad. |
| [`tesserae/mcp_server.py`](../tesserae/mcp_server.py) | MCP-Stdio-Server. Retrieval/Graph: `schema`, `graph_summary`, `search_nodes`, `node_context` (mit `use_ppr`), `search_facts`, `timeline`, `graph_ppr`, `wiki_page`, `raw_source`, `lint_report`. Kontext-Engine (v0.5.0): `compile_context` (der On-Demand-Kontext-Compiler), `embedding_status`, `fresh_insights` (nach Decay gerankte Sitzungsbefunde), `list_communities`, `find_session_findings`, `find_code_symbol_mentions`. Dazu `ask`, die Multi-Projekt-Registry-Werkzeuge (`list_projects`, `register_project`, `activate_project`, `unregister_project`, `list_sessions`) sowie `tesserae_setup_plan` / `tesserae_setup_apply`. |

## Projekt-Workspace-Layout

```text
.tesserae/
  config.json                 project name, source kind, source list
  graph.json                  validated ResearchGraph (incl. Synthesis nodes)
  manifest.json               per-source content hashes (input dedup)
  sqlite.db                   SQLite graph store; besitzt außerdem die Sidecar-Tabellen node_provenance
                              (Erstsichtung, Phase 4) und node_memory (Decay / Konfidenz /
                              ersetzt, Phase 5)
  temporal_facts.jsonl        Graphiti-style temporal projection (numerische Wiederkehr-Konfidenz)
  graphiti_episodes.jsonl     dependency-free Graphiti episode export
  report.md                   graph quality / summary
  competitive_report.md       comparison vs. MegaMem / Graphiti / others
  markdown_projection/        flat human-readable markdown
  obsidian_vault/             Obsidian projection w/ .obsidian/, raw/assets/
  agent_harness/              Claude Code / Codex / etc. harness files
  harness_sessions/           imported local Claude Code/Codex sessions
  cognee_bundle/              Cognee nodes/edges/manifest JSONL
  wiki/                       L2 markdown wiki — see below
  site/                       L3 static site — see below
```

### `.tesserae/wiki/` (L2)

```text
wiki/
  sources/<slug>.md           raw documents from data/ + docs/, with frontmatter
  concepts/<slug>.md          Concept / TechnicalTerm / Algorithm / etc.
  entities/<slug>.md          Model / Dataset / Benchmark / Metric / Org / Person
  papers/<slug>.md            Paper hub
  repos/<slug>.md             Repository / Project / CodeProject
  topics/<slug>.md            ResearchField / ResearchTopic / ApproachFamily / Trend
  syntheses/<slug>.md         pulse, daily_digest, weekly, topic, comparison, field_overview
  questions/<slug>.md         OpenQuestion
```

Jede Datei ist von Hand editierbar; der nächste Compile respektiert Nutzer-Edits, solange der Body-Hash von dem abweicht, was der Projector schreiben würde. (Nur den Body zu bearbeiten gewinnt; das Frontmatter zu bearbeiten verliert beim nächsten Compile, weil das Frontmatter neu erzeugt wird.) Obsidian-Nutzer können `.tesserae/wiki/` direkt öffnen; der bestehende `obsidian_vault/`-Adapter ist eine separate Projektion, kein Ersatz.

### `.tesserae/site/` (L3)

```text
site/
  index.html                  home + project pulse
  about.html                  schema, build info
  assets/{style.css,app.js}   single CSS bundle + single JS bundle
  sources/index.html
  sources/<slug>.html
  sources/<slug>.txt          AI sibling — plain text
  sources/<slug>.json         AI sibling — structured record
  concepts/…  entities/…  papers/…  repos/…  topics/…  syntheses/…  questions/…
  sessions/index.html          imported harness-session index
  sessions/<project>/<id>.html session detail: summary, metadata, turn rail, markdown turns, collapsed tools
  timeline/index.html
  graph/index.html            interactive 2D + 3D force layout
  graph.json                  full graph payload (incl. code nodes, for tooling)
  graph.jsonld                schema.org Dataset, wiki-layer nodes only
  search-index.json           palette + page search; wiki-layer kinds only
  llms.txt                    llmstxt.org — short index
  llms-full.txt               llmstxt.org — every page body, capped 5MB
  sitemap.xml                 every emitted route
  rss.xml                     last 30 syntheses
  robots.txt                  permissive (crawl + index)
  ai-readme.md                machine-readable site map
  manifest.json               sha256 + size for every emitted file
```

## Was bewusst ausgeschlossen ist

Das Redesign hat eine klare Linie gezogen: Code-Class- und Code-Function-Knoten bleiben in `graph.json` (damit MCP-, Cognee- und Graphiti-Consumer sie weiterhin sehen), bekommen aber nie HTML-Seiten, tauchen nie in `search-index.json` auf und erscheinen nie in der Navigation. Das ist der Vertrag nach außen — das Wiki ist eine dokument-zentrierte Wissensdatenbank, kein Function-Browser.

Konkret überspringt `StaticSiteBuilder` jeden Knoten, dessen Typ nicht in der L2-Wiki-Kind-Map (`tesserae/wiki_projector.py::_KIND_FOR_TYPE`) steht:

- Ausgeschlossen aus L2 + L3: `CodeClass`, `CodeFunction`, `CodeModule`, `Dependency`, `EvidenceSpan`, `SourceFile`, alle `Claim`-Varianten (`Claim`, `ContributionClaim`, `PerformanceClaim`, `ComparisonClaim`, `LimitationClaim`, `CausalClaim`).
- Flächen, wo sie weiterhin auftauchen: als Bullets, Badges, Neighbour-Counts oder Evidence-Excerpts inline auf verwandten Wiki-Seiten, und in `graph.json` für Downstream-Tooling.

Wenn du Code-Level-Browsing brauchst, richte ein LSP- / Call-Graph-Tool direkt auf den Source-Tree — das ist ein anderes Problem als „Wiki von dem, was dieses Projekt weiß“.

## Idempotenz-Story

Das Redesign zielt auf **byte-identischen Output über zwei aufeinanderfolgende `project compile`-Läufe bei unveränderten Inputs**. Die Bausteine:

1. **Source-Extraktion** nutzt die Content-Hashes aus `manifest.json`; unveränderte Dateien werden übersprungen, der Graph bleibt stabil.
2. **Wiki-Layer-Writes** sind auf Body-Ebene idempotent. `WikiPageStore.write_page` liest die existierende Datei, entfernt Frontmatter, sha256t den Body und kurzschließt, wenn der neue Body denselben Hash ergibt — auch wenn das neue Frontmatter einen anderen `generated_at`-Timestamp hat. Das ist der Schlüsseltrick, der git-Diffs beim Rebuild eng hält.
3. **Synthesis-Output** trägt einen `content_hash: sha256-…` im Frontmatter. Der Body-Hash wird ohne `generated_at` berechnet, sodass wiederholte Compiles auf demselben Graph denselben Hash erzeugen, und `Synthesis`-Knoten tragen denselben `content_hash` in den Graph-Metadaten.
4. **Site-Rendering** löscht `site/` zu Beginn von `write_site` und schreibt dann deterministisch: Routen sind sortiert, Dicts werden mit `sort_keys=True` gedumpt, `manifest.json` läuft über `sorted(rglob("*"))`. Zwei Läufe erzeugen byte-identische Dateien inklusive Manifest.

Das wird durch `tests/test_site_pages.py` und den End-to-End-Smoke in `tests/test_project_e2e_redesign.py` verifiziert (zweimal compilen, Sites diffen, null Deltas erwarten).

## Skalierungsnotizen

- **Graph-View-Knoten-Cap.** [`MAX_GRAPH_NODES = 1500`](../tesserae/site/pages.py) begrenzt das in die Seite eingebettete Payload für das interaktive Force-Layout. Jenseits von ~1500 Knoten wird die Browser-Simulation auf Mid-Range-Hardware träge, daher droppt die Seite zuerst die Wiki-Layer-Knoten mit dem niedrigsten Grad, sobald der Count das Cap überschreitet. Die exportierte `graph.json` ist davon unberührt — sie enthält immer den vollen Graph. Code-Knoten werden vor dem Cap herausgefiltert.
- **`llms-full.txt`-Cap.** Ein Safety-Cap von 5 MB greift in [`tesserae/site/exports.py`](../tesserae/site/exports.py); die Datei endet mit einem `[TRUNCATED — see graph.jsonld for the full set]`-Marker, wenn der Cap erreicht wird. `graph.jsonld` ist uncapped, weil JSON-LD-Consumer das volle Set erwarten.
- **Search-Index.** Nur Wiki-Layer-Kinds. Code-Graph-Knoten landen nie in `search-index.json`; das Redesign-Ziel ist < 500 KB für den Dogfood-Korpus und wir liegen heute deutlich darunter.
- **Per-Page-Byte-Budget (Faustregel).** Jede Detailseite < 60 KB gz HTML, shared CSS < 30 KB, shared JS < 25 KB, Sigma-Vendor nur auf der Graph-Seite (~60 KB). Die Graph-Ansicht nutzt 3D-force-graph + Three.js, einmal geladen; alle anderen Seiten bleiben vanilla.
- **Compile-Zeit auf Dogfood.** ~300 Markdown-Dateien extrahieren in unter 5 s auf einer aktuellen Dev-Maschine; das Site-Rendering fügt weitere ~2 s hinzu. Die Idempotenz der Wiki-Schicht sorgt dafür, dass nachfolgende Compiles nur die geänderten Pfade berühren.

## Frontend-Interaktionsfläche

- **Search-Palette** — `cmd+k` / `ctrl+k` / `/`. Fuzzy-Match über `search-index.json`, gescopet auf Wiki-Kinds. Recent Pages werden in `localStorage` persistiert.
- **Theme-Toggle** — Button oben rechts; `data-theme="dark"` wird in `localStorage` gespeichert und vor dem Paint angewendet, um Flash zu vermeiden.
- **Sticky-Right-TOC** — nur Desktop; kollabiert auf Mobile zu einem `<details>`-Drawer. Erzeugt aus `<h2>` / `<h3>` im Body.
- **Activity-Heatmap** — 26-Wochen-SVG mit Monats- + Wochentag-Labels. Zellen verlinken auf die Source-Seite `digest.md` des Tages, falls eine existiert. (Per-Day-Timeline-Detailseiten — `/timeline/<YYYY-MM-DD>.html` — sind ein expliziter Follow-up; der Inline-Hinweis in `render_timeline` markiert es. ⚠ in Arbeit.)
- **Graph-View** — `/graph/`. 3D-Force-Layout (3d-force-graph + Three.js) mit Hover-Tooltips, Edge-Labels, Cursor-verankertem Zoom und einer 2D-Fallback-View. Knotenfarben kommen aus `ResearchNodeType`.
- **Mobile-Shell** — Drawer-Rail, Bottom-Nav, fluide Schrift, touch-sichere Hit-Targets (≥ 44 px).

## Test-Strategie

- **Unit** — `tests/test_wiki_store.py`, `tests/test_synthesis.py`, `tests/test_site_components.py`, `tests/test_site_pages.py`, `tests/test_site_exports.py`, `tests/test_relevance.py`.
- **Engine-Rückgrat** — `tests/test_pipeline.py`, `tests/test_refresh_pipeline.py`, `tests/test_daemon_core.py`, `tests/test_daemon_sources.py`, `tests/test_cli_engine.py`.
- **Selbstverbesserungs-Speicher** — `tests/test_memory_sidecar.py`, `tests/test_decay_supersede.py`, `tests/test_supersede_suppression.py`, `tests/test_mcp_supersede_suppression.py`, `tests/test_memory_contradiction_reinforce.py`.
- **Retrieval + Embeddings** — `tests/test_hybrid_search.py`, `tests/test_ppr.py`, `tests/test_real_embeddings_phase6.py`.
- **Kontext-Compiler** — `tests/test_context_compiler.py` (Form, Zitat-Integrität, Determinismus, Budget, PPR-Rückfall), `tests/test_cli_context.py`, `tests/test_mcp_server_context.py`.
- **Inkrementelle Kompilierung (experimentell)** — `tests/test_incremental_compile.py`, `tests/test_incremental_parity.py`, `tests/test_provenance_readiness.py`, `tests/test_sqlite_provenance.py`.
- **Idempotenz** — `tests/test_project_e2e_redesign.py` compilet zweimal und prüft auf null Diffs in `wiki/` und `site/`.
- **Link-Integrität** — `tests/test_frontend.py` parst jedes emittierte HTML nach hrefs und prüft, dass jeder interne Link auf eine erzeugte Datei zeigt. Es wird kein `nodes/codeclass-*.html` produziert.
- **AI-Siblings** — für jedes `path/foo.html` prüft die Test-Suite, dass `path/foo.txt` und `path/foo.json` existieren; das JSON parst und enthält `{title, kind, body, links}`.
- **Kein Playwright** — vanilla pytest unter `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`.

## Verwandte Dokumentation

- [Quickstart](quickstart.de.md) — minimaler Pfad von `project init` zu einer browserbaren Site.
- [Frontend-Redesign-Walkthrough](frontend-redesign.de.md) — annotierte Tour durch jede Route.
- [Feature-Map](feature-map.de.md) — was geliefert ist, was in Arbeit ist, mit File-Pointern.
- [Self-Dogfood-Demo](self-dogfood.de.md) — Tesserae gegen das eigene Repo laufen lassen.
