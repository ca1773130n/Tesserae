# Architektur

<!-- translations:start -->
<p align="center"><a href="../architecture.md">English</a> · <a href="architecture.ko.md">한국어</a> · <a href="architecture.zh.md">中文</a> · <a href="architecture.ja.md">日本語</a> · <a href="architecture.ru.md">Русский</a> · <a href="architecture.es.md">Español</a> · <a href="architecture.fr.md">Français</a></p>
<!-- translations:end -->
Tesserae ist eine **Kontext-Engine**. Sie rekonstruiert eine sich selbst verbessernde Wissensbasis aus deinem Projekt und übergibt sie Agenten als sofort nutzbaren Kontext. Sie ruht auf drei Säulen: (1) **Sitzungsüberwachung** — Live-Agenten-/Arbeitssitzungen beobachten und Erkenntnisse erfassen, sobald sie entstehen; (2) **autonome, proaktive Wissensaufnahme** — eine Pipeline + Supervisor-Schleife ziehen und re-extrahieren Wissen kontinuierlich und verbessern die Basis, statt auf Anweisungen zu warten; (3) **Dokumente/Kontext auf Abruf** — vom Nutzer angeforderte Artefakte, kompiliert aus derselben Basis. Der typisierte Graph, der Markdown-Vault und die statische Site sind *Projektionen* der Wissensbasis; die Engine ist die Schleife, die sie frisch hält und Agenten speist.

Darunter verwandelt Tesserae ein Verzeichnis mit Quellmaterial in einen kontrollierten, typisierten Knowledge Graph und projiziert diesen Graph über eine langlebige Markdown-Wiki-Schicht in eine statische, KI-freundliche Website. Das Redesign vom April 2026 hat die Projektionsseite um ein dreischichtiges Modell nach Karpathy reorganisiert: Rohbelege bleiben roh, ein typisierter Graph regiert die Ontologie, und eine Markdown-Wiki-Schicht sitzt zwischen Graph und gerendertem Output. Die statische Site ist ein *Renderer* dieser Wiki-Schicht statt einer direkten Ausgabe des Graphen, mit der kontrollierten Ontologie in [`tesserae/research_graph.py`](../../tesserae/research_graph.py) als Schema. Der Meilenstein **v0.5.0** (Juni 2026) ergänzte das Engine-Rückgrat, das alle drei Säulen antreibt — siehe *Engine-Rückgrat* und *On-Demand-Kontext-Compiler* unten.

## Das dreischichtige Karpathy-Modell

Andrej Karpathys Framing für LLM-freundliche Wissensdatenbanken unterscheidet drei Schichten, jede mit eigener Beständigkeitsgarantie:

| Schicht | Inhalt | Repo-Ort | Owner |
|---|---|---|---|
| L1 — Rohquellen | Die literalen Bytes, die der Nutzer geschrieben oder gesammelt hat. Append-only. | `data/`, `docs/`, in `.tesserae/config.json` referenzierte Projektbäume | der Nutzer |
| L2 — Wiki | Typisierte Markdown-Seiten (sources, concepts, entities, papers, repos, topics, syntheses, questions) mit YAML-Frontmatter. Idempotent: bei jedem Compile neu erzeugt, aber nur überschrieben, wenn sich Content-Hashes ändern. | `.tesserae/wiki/` | `WikiPageStore`, `WikiLayerProjector`, `SynthesisProjector` |
| L3 — Rendered | Die statische HTML-Site, AI-Sibling-Exporte, Suchindex, Sitemaps, JSON-LD. Wird bei jedem Compile gelöscht und neu geschrieben, aber Byte-stabil über Reruns. | `.tesserae/site/` | `StaticSiteBuilder` (`tesserae/site/`) |

Das Schema spannt sich über alle drei Schichten als separate Achse: `ResearchGraph` in `graph.json` ist die kontrollierte Ontologie, gegen die L2-Seiten verlinken, und die `ResearchNodeType`-/Edge-Whitelist in [`tesserae/research_graph.py`](../../tesserae/research_graph.py) ist die Source of Truth dafür, welche Typen überhaupt existieren.

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

Der On-Demand-Kontext-Compiler ([`tesserae/context_compiler.py`](../../tesserae/context_compiler.py)) ist der Vorzeigepfad von Säule 3. Bei einer Abfrage und/oder expliziten Seed-Knoten-IDs baut `compile_context` ein maßgeschneidertes, **zitiertes** Markdown-Bundle direkt aus dem Graphen und gibt es im Speicher zurück — er schreibt nichts unter `.tesserae/`.

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
     ▼  2b. Prozedurale Reservierung (verdient, nicht gewährt)
        ein Platz je Pool, in der Reihenfolge von PROCEDURAL_POOL_ORDER: Runbook, Gotcha,
        Event, DistilledNote, ExpertiseProfile. Der Platz geht an den höchstplatzierten
        Knoten dieses Typs mit PRODUZENTEN-Provenienz — nicht bloß an den Typnamen
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

**Warum der prozedurale Platz durch Provenienz verdient wird.** Die fünf
prozeduralen Typen benennen, was ein Agent getan, gelernt und worin er gut ist —
doch auch die Dokumentextraktion darf sie erzeugen, sodass ein LLM beim Lesen
eines Call for Papers völlig legitim ein typisiertes `Event` namens „CVPR 2026"
erzeugt. Die Reservierung ist *additiv*: Sie hebt einen Knoten von irgendwo aus
der Nachbarschaft an den Anfang des Budgetdurchlaufs. Eine Reservierung allein
nach Typ ließe deshalb eine Konferenzdeadline jenen Sitzungsbefund verdrängen,
der den Platz tatsächlich verdient hat. Getrennt werden beide durch
`has_producer_provenance`, und eine Reservierung ist ein Anspruch auf einen
Platz, nicht der Beweis dafür: `delivered` entscheidet sich erst nach dem
Budgetdurchlauf, sodass die aufrufende Seite „prozedurales Gedächtnis wurde
reserviert" von „prozedurales Gedächtnis kam an" unterscheiden kann. Der
Lint-Code `PROCEDURAL_POOLS` meldet die Lücke.


## Modul-Karte

### Wiki + Synthese (L2)

| Modul | Verantwortung |
|---|---|
| [`tesserae/wiki_store.py`](../../tesserae/wiki_store.py) | `WikiPage`-Dataclass, `WikiPageStore` für Filesystem-I/O. Stdlib-only YAML-Subset-Frontmatter-Parser. Body-Hash-Idempotenz. |
| [`tesserae/wiki_projector.py`](../../tesserae/wiki_projector.py) | `WikiLayerProjector`: bildet jeden `ResearchGraph`-Knoten eines Wiki-Layer-Typs auf eine Markdown-Seite im richtigen `kind/`-Ordner ab. |
| [`tesserae/synthesis.py`](../../tesserae/synthesis.py) | `SynthesisProjector`: deterministische Templates für pulse, daily_digest, weekly, topic, comparison, field_overview. Fügt `Synthesis`-Knoten und `synthesizes`-/`summarizes`-Kanten zurück in den Graph. |

### Graph + Ontologie

| Modul | Verantwortung |
|---|---|
| [`tesserae/research_graph.py`](../../tesserae/research_graph.py) | `ResearchNodeType`-Enum (inkl. `SYNTHESIS`), Edge-Type-Whitelist (inkl. `synthesizes`, `summarizes`), Validierung. |
| [`tesserae/canonicalization.py`](../../tesserae/canonicalization.py) | Alias-Kanonisierung + Near-Duplicate-Review-Queue. |
| [`tesserae/merge_ledger.py`](../../tesserae/merge_ledger.py) | `.tesserae/merge-ledger.json`: der Loser→Gewinner-Grabstein für jedes Duplikat, das eine Kompilation zusammenführt, sodass eine ID aus einer vorherigen Kompilation aufgelöst wird, statt „nicht gefunden" zurückzugeben. **Abgeleiteter Zustand, keine Historie** — beim Publish wird mit dem vorhandenen Zustand vereinigt, ein Datensatz wird nur geführt, solange der Verlierer aus dem gerade publizierten Graph fehlt *und* die Kette daraus auf einen vorhandenen Knoten führt. Ein Verlierer, der wieder auftaucht, wird verworfen. Wird nur nach einem Graph-Miss gelesen, was garantiert, dass eine Live-ID niemals umgeleitet wird. |
| [`tesserae/candidate_ledger.py`](../../tesserae/candidate_ledger.py) | `.tesserae/candidate-same-as.json`: ausstehend / bestätigt / abgelehnt pro Kandidaten-Merge-Paar, Schlüssel nur das sortierte Node-ID-Paar — Score, Grund und Backend liegen bewusst außerhalb des Schlüssels, es ist genau die Volatilität, die ein Urteil überdauern muss. **Akkumuliert, das Gegenteil des Merge-Ledgers**: ein Merge ist abgeleiteter Zustand, ein Mensch-Urteil ist das einzige, das eine Maschine in der Pipeline nicht erneut ableiten kann, deshalb wird hier nichts je gelöscht. Beide dürfen sich Code nicht teilen. |
| [`tesserae/code_graph.py`](../../tesserae/code_graph.py) | Deterministischer Python-AST-Extraktor für den Development-Slice. |
| [`tesserae/llm_extractor.py`](../../tesserae/llm_extractor.py) | Selektiver Extraktor via Claude CLI/OAuth. |

### Site-Renderer (L3)

| Modul | Verantwortung |
|---|---|
| [`tesserae/site/__init__.py`](../../tesserae/site/__init__.py) | `StaticSiteBuilder.write_site`: löscht und baut die Site neu, läuft jede Route durch, gibt Exporte + AI-Siblings + Manifest aus. |
| [`tesserae/site/pages.py`](../../tesserae/site/pages.py) | Ein Renderer pro Route (home, indexes, detail pages, timeline, graph, about). `SiteContext` trägt vorberechnete Indizes, damit Renderer pur bleiben. |
| [`tesserae/site/components.py`](../../tesserae/site/components.py) | HTML-Primitive: `breadcrumbs`, `card`, `badge`, `node_table`, `edge_list`, `sparkline_svg`, `heatmap_svg`, `toc`, `page_shell`, `ai_siblings_footer`. |
| [`tesserae/site/tokens.py`](../../tesserae/site/tokens.py) | Design Tokens — CSS-Variablen, Light- + Dark-Themes, Layout, Typografie, hier werden alle Komponenten gestylt. |
| [`tesserae/site/js.py`](../../tesserae/site/js.py) | Client-JS-Bundle: Search-Palette, Theme-Toggle, Sigma + 3D-Force-Graph-Ansicht. |
| [`tesserae/site/markdown.py`](../../tesserae/site/markdown.py) | Stdlib-only Markdown-Renderer (Links, Autolinks, Code, Hervorhebungen, Überschriften). Keine externe Abhängigkeit. |
| [`tesserae/site/relevance.py`](../../tesserae/site/relevance.py) | Vier-Signal-Relevanz-Scoring (direkter Link, Source-Overlap, Adamic-Adar, Typ-Affinität), das von jedem `Related`-Abschnitt benutzt wird. |
| [`tesserae/site/search.py`](../../tesserae/site/search.py) | Builder für `search-index.json`. Nur Wiki-Layer-Kinds. |
| [`tesserae/site/sessions.py`](../../tesserae/site/sessions.py) | Session-Index/Detail-Renderer für importierte Harness-Historie: Project-Memory-Summary-Sections, Conversation-Turn-Rail, Markdown-Transcript-Rendering und eingeklappte Tool-Use-Blöcke. |
| [`tesserae/site/exports.py`](../../tesserae/site/exports.py) | `llms.txt`, `llms-full.txt`, `graph.jsonld`, `sitemap.xml`, `rss.xml`, `robots.txt`, `ai-readme.md`, Per-Page-`.txt`/`.json`-Siblings. |

### Pipeline-Orchestrierung

| Modul | Verantwortung |
|---|---|
| [`tesserae/project.py`](../../tesserae/project.py) | `ProjectWiki.compile`: treibt Extraktion → Graph → Memory-Pässe → Wiki-Layer → Site. Besitzt `ProjectPaths` (`config`, `graph`, `manifest`, `wiki`, `site`, etc.). Entscheidet vorab, ob eine herkunftsbasierte (provenance) inkrementelle Kompilierung infrage kommt (durch `incremental_compile` gesteuert, Standard OFF). |
| [`tesserae/cli.py`](../../tesserae/cli.py) | Flache verb-basierte CLI-Dispatch (~2.732 Zeilen nach dem Löschen der veralteten `project`/`wiki`-Subcommand-Gruppen). Die Verben – `init`, `compile`, `ingest`, `context`, `ask`, `query`, `doctor`, `summary`, `decisions`, `refresh`, `serve`, `engine`, `export`, `vault`, `code`, `lab`, `setup`, `config`, `projects`, `sources`, `federation`, `integrations` – werden als Metadaten in [`tesserae/cli_tree.py`](../../tesserae/cli_tree.py) deklariert und aus diesem Baum verdrahtet, statt von Hand registriert zu werden. |
| [`tesserae/deploy.py`](../../tesserae/deploy.py) | `export site --deploy`: pusht `.tesserae/site/` auf einen `gh-pages`-Branch via Worktree, aktiviert optional Pages via `gh`. |

### Engine-Rückgrat (v0.5.0 — Säulen 1 & 2)

Das Engine-Rückgrat ist die In-Process-Schleife, die Sitzungsüberwachung und autonome Re-Ingestion antreibt. Dasselbe `Pipeline.run()` ist der einzige Aktualisierungspfad, den CLI, der Supervisor-Daemon und (später) der MCP-Server alle aufrufen.

| Modul | Verantwortung |
|---|---|
| [`tesserae/engine/pipeline.py`](../../tesserae/engine/pipeline.py) | `Pipeline`: sequenzieller Schrittausführer. Kodifiziert die prosaische Aktualisierungskette (Aufnahme → Kompilierung → Projektion/Veröffentlichung) als importierbares Objekt, das eine strukturierte `List[StepResult]` zurückgibt statt drucken-und-beenden, sodass jeder Aufrufer selbst entscheidet, wie er Ergebnisse darstellt. `run()` fängt `Exception` pro Schritt (lässt `KeyboardInterrupt`/`SystemExit` durch) und stoppt beim ersten Fehler. |
| [`tesserae/engine/daemon.py`](../../tesserae/engine/daemon.py) | `Daemon`: Asyncio-Supervisor mit alleinigem Besitzer. Überwacht Quellverzeichnisse, den Obsidian-Vault und das Harness-Sitzungsverzeichnis; über ein Abbrechen-und-Neuplanen-Debounce fasst er eine Serie von `TriggerEvent`s zu genau einem `Pipeline.run()` zusammen. Verwendet die vorhandenen Watcher `watch.py` / `vault_watch.py` wieder (schreibt sie nicht neu), schreibt eine Pidfile und überlebt Ausnahmen im laufenden Betrieb. Über `engine` (`--interval`, `--debounce`, `--once`) verfügbar. |
| [`tesserae/watch.py`](../../tesserae/watch.py), [`tesserae/vault_watch.py`](../../tesserae/vault_watch.py) | Polling-Watcher, die sowohl vom eigenständigen Befehl `export site --watch` als auch von den Quell-/Vault-Spuren des Daemons wiederverwendet werden. |

### Selbstverbesserungs-Speicher (v0.5.0 — Säule 2)

Phase 5 aktivierte die persistente Selbstverbesserung. Der veränderliche Zustand pro Knoten liegt in einem `node_memory`-SQLite-Sidecar (innerhalb von `.tesserae/sqlite.db`), getrennt vom unveränderlichen Erstsichtungs-Stempel `node_provenance.first_seen_at` (Sidecar aus Phase 4). Die Kompilierung treibt eine Reihe deterministischer Pässe über den Graphen.

| Modul | Verantwortung |
|---|---|
| [`tesserae/memory/store.py`](../../tesserae/memory/store.py) | `NodeMemoryRow` + store-unabhängige Accessoren (`read_memory`, `write_memory`, `bump_access`) über die `node_memory`-Tabelle — `decay_score`, `last_accessed_at`, `confidence`, `superseded`. Keine Aufrufstelle bettet rohes SQL ein. |
| [`tesserae/memory/decay.py`](../../tesserae/memory/decay.py) | `compute_decay_score`: Ebbinghaus-artige Frischescore (neueste + am häufigsten aufgerufene zuerst) zum Ranking von Sitzungsbefunden. |
| [`tesserae/memory/supersede.py`](../../tesserae/memory/supersede.py) | `run_supersede_pass` (**standardmäßig EIN**): deterministisches Urteil, das ein älteres Beinahe-Duplikat-Insight als durch ein neueres ersetzt markiert und eine `supersedes`-Kante hinzufügt. |
| [`tesserae/memory/insight_symbol_link.py`](../../tesserae/memory/insight_symbol_link.py) | `run_insight_symbol_link_pass`: verknüpft Sitzungs-Insights über `discusses`-Kanten mit den Code-Symbolen, die sie behandeln. |
| [`tesserae/memory/reinforce.py`](../../tesserae/memory/reinforce.py), [`tesserae/memory/contradiction.py`](../../tesserae/memory/contradiction.py) | Helfer für Zugriffsverstärkung und Widerspruchserkennung über demselben Sidecar. |

Die Wiederkehr-Konfidenz ist in der Ausgabe numerisch: Die zeitliche Projektion stempelt die `confidence` jedes Fakts aus `NodeMemoryRow.confidence` (Text in SQLite, über `temporal.py` bereitgestellt) und greift nur dann auf `infer_confidence` zurück, wenn kein gespeicherter Wert existiert.

### Retrieval (v0.5.0 — Säulen 2 & 3)

| Modul | Verantwortung |
|---|---|
| [`tesserae/retrieval/hybrid.py`](../../tesserae/retrieval/hybrid.py) | `hybrid_search`: local-first Hybrid-Retriever, der drei Spuren — Okapi BM25 (k1=1.5, b=0.75), case-folding lexikalische/FTS-artige Teilstring-Übereinstimmung und eine einsteckbare Embedding-Spur — per Reciprocal-Rank-Fusion (RRF, k=60) verschmilzt. Vollständig deterministisch. |
| [`tesserae/retrieval/ppr.py`](../../tesserae/retrieval/ppr.py) | `personalized_pagerank`: Personalized PageRank im HippoRAG-2-Stil (arXiv:2502.14802) über den Graphen zur Multi-Hop-Seed-Expansion — bringt gut verbundene Knoten mehrere Hops von der Seed entfernt an die Oberfläche, nicht nur die 1-Hop-Nachbarschaft. |
| Embedding-Backend (Phase 6, Track B) | Das Standard-Backend der Embedding-Spur des Hybrids ist ein deterministisches Hash-Bucket-Pseudo-Embedding, das keine zusätzlichen Abhängigkeiten braucht; `sentence-transformers` (`all-MiniLM-L6-v2`) wird bevorzugt und lazy geladen, wenn die optionale Abhängigkeit installiert ist. Das MCP-Werkzeug `embedding_status` meldet das aktive Backend. |
| [`tesserae/retrieval/vector_cache.py`](../../tesserae/retrieval/vector_cache.py) | Die `node_vectors`-Tabelle im SQLite-Sidecar und der eine Accessor, durch den alle drei `.embed(`-Stellen laufen. Mit Schlüssel `(backend_name, backend_dim, sha256(embedded_text))` — Identität hier ist der eingebettete **Text**, nicht die Node-ID, sodass ein unveränderter Knoten nach einer vollständigen Neukompilierung, einem Projektumzug oder einer Kanonisierungs-Neuschreibung seiner ID trifft, während eine neu beschriebene ihn verfehlt und erneut einbettet. Vektoren zweier Modelle treffen sich nie: ihre Räume sind nicht vergleichbar und ein stummer Mix würde Cosine korrumpieren statt zu scheitern. |
| [`tesserae/retrieval/views.py`](../../tesserae/retrieval/views.py) | Die Sicht-Registry: `semantic` / `temporal` / `causal` / `entity`, jede eine benannte Untermenge von `ALLOWED_EDGE_TYPES`, aufgelöst durch `weights_for()` in explizite Null-Gewichte für jeden Out-of-View-Typ. Zwei Partitionierungsentscheidungen sind tragend: `summarizes` + `evidenced_by` (~50% aller Kanten — Abstraktion und Herkunft) gehören zu **keiner** Sicht, sonst wird die semantische Sicht erneut der ganze Graph; und die kausale Sicht ist breiter als `CAUSAL_EDGE_TYPES`, da `{recovers}` allein eine Sicht ohne Live-Kanten wäre. |
| [`tesserae/blocking.py`](../../tesserae/blocking.py) | Die einzige Blocking-Schicht für beide paarweisen Durchgänge (Canonicalization's Review-Builder und `memory.supersede`). Die Obergrenze wird nach **sortierter ID** gekürzt, sodass ein gekappter Lauf nicht von der Ankunftsreihenfolge der Knoten abhängt und ein eingeengter Compile reproduzierbar bleibt; der Aufrufer versorgt seinen eigenen Tokenizer, weil ein Blocker grober als sein Scorer stille wahre Treffer löscht. Jeder Durchgang meldet eine Obergrenze, die er traf, statt stille eine kürzere Warteschlange zurückzugeben. |

### On-Demand-Kontext-Compiler (v0.5.0 — Vorzeigefeature von Säule 3)

| Modul | Verantwortung |
|---|---|
| [`tesserae/context_compiler.py`](../../tesserae/context_compiler.py) | `compile_context`: das Vorzeigefeature von Säule 3. Kompiliert ein maßgeschneidertes, **zitiertes** Kontext-Bundle für ein Abfrage-/Seed-Set direkt aus dem Graphen — siehe *Datenfluss des Kontext-Compilers* unten. Gibt ein `ContextBundle` im Speicher (mit `ContextCitation`s) zurück; schreibt nichts auf die Festplatte. Über den CLI-Befehl `project context` und das MCP-Werkzeug `compile_context` verfügbar. |

### Persistenz-Ports + Graph-Stores

| Modul | Verantwortung |
|---|---|
| [`tesserae/ports/graph_store.py`](../../tesserae/ports/graph_store.py) | `GraphStore`-Protokoll: `upsert_node`/`upsert_edge`, `get_node`, `iterate_nodes`, `query_subgraph`, `find_canonical` und die Löschfläche aus Phase 4 — `delete_node` und `delete_nodes_by_source` (löscht Knoten, deren Herkunftsmenge nach Entfernen der angegebenen Quellpfade leer wird, sodass dateiübergreifende Konzepte überleben). |
| [`tesserae/graph_stores/sqlite.py`](../../tesserae/graph_stores/sqlite.py) | `SqliteGraphStore`: eigenständiger Backing-Store; besitzt die Sidecar-Tabellen `node_provenance` und `node_memory`. |
| [`tesserae/graph_stores/url_resolver.py`](../../tesserae/graph_stores/url_resolver.py) | Löst eine Store-URL (`sqlite:///…`, `hypepaper-postgres://…`) zum passenden `GraphStore` auf, sodass der MCP-Server zur Laufzeit auf einen beliebigen Backing-Store zeigen kann. |

### Externe Adapter (in dieser Runde unverändert)

| Modul | Verantwortung |
|---|---|
| [`tesserae/obsidian_adapter.py`](../../tesserae/obsidian_adapter.py) | Obsidian-Vault-Projektion (Graph-Coloring, Dataview-Dashboard, Roh-Assets). |
| [`tesserae/agent_harness.py`](../../tesserae/agent_harness.py) | Harness-Exporte für Claude Code / Codex / Gemini / Kiro / Cursor / OpenCode. |
| [`tesserae/harness_sessions.py`](../../tesserae/harness_sessions.py) | Inbound Discovery, Normalisierung und Storage von Claude-Code-/Codex-Sessions unter `.tesserae/harness_sessions/` plus redigierte Markdown-Zusammenfassungen. |
| [`tesserae/graphiti_adapter.py`](../../tesserae/graphiti_adapter.py) | Temporal-Fact-JSONL + optionaler Live-Sync mit Graphiti. |
| [`tesserae/kuzu_adapter.py`](../../tesserae/kuzu_adapter.py) | Einweg-Export in eine Kuzu-Datenbank (`tesserae export kuzu`). Kein Store — siehe [Kuzu-Export](#kuzu-export). |
| [`tesserae/mcp_server.py`](../../tesserae/mcp_server.py) | MCP-Stdio-Server. Retrieval/Graph: `schema`, `graph_summary`, `search_nodes`, `node_context` (mit `use_ppr`), `search_facts`, `timeline`, `graph_ppr`, `wiki_page`, `raw_source`, `lint_report`, `doctor_report`. Kontext-Engine (v0.5.0): `compile_context` (der On-Demand-Kontext-Compiler), `embedding_status`, `fresh_insights` (nach Decay gerankte Sitzungsbefunde), `list_communities`, `find_session_findings`, `find_code_symbol_mentions`. Dazu `ask`, die Multi-Projekt-Registry-Werkzeuge (`list_projects`, `register_project`, `unregister_project`, `list_sessions`) sowie `tesserae_setup_plan` / `tesserae_setup_apply`. |

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

## Die Charta

Die Community-Erkennung **schlägt** ein Domänenvokabular vor; die Charta
([`tesserae/charter.py`](../../tesserae/charter.py)) **besitzt** es zwischen
ausdrücklichen Reorganisationen. Diese Trennung existiert, weil die Erkennung
zwar deterministisch, aber nicht stabil ist: Identische Eingaben reproduzieren
alle 1.649 Communities exakt, doch ein einziges Dokument mit 15 Knoten verschiebt
rund 29 % der Mitglieder zwischen Communities und drückt große Communities auf
einen Jaccard von 0,39–0,60. Alles, was auf Community-Zugehörigkeit schlüsselt,
erleidet damit pro Aufnahme einen nahezu vollständigen Cache-Miss — und dieses
Korpus nimmt täglich auf.

Also fixiert die Charta die Institution: Abschnitte werden erkannt, zu einem
Quotientengraphen zusammengefaltet (ein Knoten je Abschnitt, eine `part_of`-Kante
je abschnittsübergreifender L0-Kante) und **nach Sub-Community, nie nach Größe**
in Bereiche → Abteilungen → Teams zerlegt. Der Anker jeder Domäne ist ihr
Mitglied mit dem höchsten Grad unter den Typen, die ein Thema benennen können —
`SourceDocument`, `TechnicalTerm`, `EvidenceSpan`, `Session`, `Event` und `Agent`
werden zurückgestuft, denn eine Abschnittsüberschrift, ein Zitat, die erste Zeile
eines Transkripts oder eine Kontokennung ist kein Name, an den jemand einen
Agenten heften kann. Gewählt wird gierig, sodass keine zwei Domänen denselben
Anker teilen; der für Menschen sichtbare Slug wird einmal aus diesem Anker
geprägt und festgehalten. Über eine Reorganisation hinweg trägt `succeed` die
Slugs weiter, indem es über den Anker zuordnet — ein stabiler Name überlebt also
das Durchmischen der Mitglieder darunter. Jeder Knoten landet in genau einer
Domäne: `intake_members` fängt die verworfenen Singletons und kantenisolierten
Abschnitte auf, die die Erkennung sonst stillschweigend verlöre.

`tesserae domains status [--json]` gibt den Baum aus. **Status:** Jede
Kompilierung leitet die Charta nach `.tesserae/charter/charter.json` ab, aus
demselben kanonisierten Graphen, aus dem auch das Hierarchie-Sidecar gebaut
wird. Eine Neukompilierung, die nichts reorganisiert, schreibt nicht — die
Datei bleibt Byte für Byte gleich, denn `reorg_seq` zählt Reorganisationen,
nicht Kompilierungen. Ein Projekt, dessen Forschungsschicht in einen einzigen
Lesevorgang passt, liegt unter der Schwelle und bekommt gar keine Charta; dort
meldet der Befehl weiterhin „no charter yet" und endet mit 0, was die ehrliche
Antwort ist.

## Was bewusst ausgeschlossen ist

Das Redesign hat eine klare Linie gezogen: Code-Class- und Code-Function-Knoten bleiben in `graph.json` (damit MCP- und Graphiti-Consumer sie weiterhin sehen), bekommen aber nie HTML-Seiten, tauchen nie in `search-index.json` auf und erscheinen nie in der Navigation. Das ist der Vertrag nach außen — das Wiki ist eine dokument-zentrierte Wissensdatenbank, kein Function-Browser.

Konkret überspringt `StaticSiteBuilder` jeden Knoten, dessen Typ nicht in der L2-Wiki-Kind-Map (`tesserae/wiki_projector.py::_KIND_FOR_TYPE`) steht:

- Ausgeschlossen aus L2 + L3: `CodeClass`, `CodeFunction`, `CodeModule`, `Dependency`, `EvidenceSpan`, `SourceFile`, alle `Claim`-Varianten (`Claim`, `ContributionClaim`, `PerformanceClaim`, `ComparisonClaim`, `LimitationClaim`, `CausalClaim`).
- Flächen, wo sie weiterhin auftauchen: als Bullets, Badges, Neighbour-Counts oder Evidence-Excerpts inline auf verwandten Wiki-Seiten, und in `graph.json` für Downstream-Tooling.

Wenn du Code-Level-Browsing brauchst, richte ein LSP- / Call-Graph-Tool direkt auf den Source-Tree — das ist ein anderes Problem als „Wiki von dem, was dieses Projekt weiß“.

## OKF-v0.2-Export/-Import

[`tesserae/okf.py`](../../tesserae/okf.py) projiziert den Graphen in ein [Google-**OKF-v0.2**](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md)-Bundle — einen Verzeichnisbaum aus Markdown-Dateien mit YAML-Frontmatter, dessen einzige Pflichtangabe ein nicht leerer `type` ist. `tesserae export okf` **schreibt v0.2**; `tesserae export okf --import DIR` **liest v0.1 und v0.2**. Das Bundle ist eine reine Projektion von `graph.json`: keine Wanduhr, kein `os.stat()`, keine Umgebung — zwei Exporte desselben Graphen sind daher byteidentisch.

Was Tesserae ausgibt, und woher jeder Wert ehrlicherweise stammt:

| Frontmatter | §  | Abgeleitet aus |
|---|---|---|
| `type` | §4.1 | Der Knotentyp oder ein fremder Typ, bewahrt in `metadata.okf_type` |
| `title` | §4.1 | `node.name` — v0.1 schrieb ein spezifikationsfremdes `name`; siehe die brechenden Änderungen weiter unten |
| `description` | §4.1 | Erster Satz der Knotenbeschreibung, gedeckelt |
| `resource` | §4.1 | `arxiv_id` → `https://arxiv.org/abs/<id>`, sonst `repo_url` / `github_repo` |
| `generated: {by, at}` | §5.2 | `by` aus `agent_key` → `<key>/tesserae-agent-write`, sonst `extractor` → `process:tesserae-<extractor>`, sonst `process:tesserae-compile`; `at` aus der gemeinsamen Quell-Zeitstempelleiter in [`temporal.py`](../../tesserae/temporal.py) |
| `sources[]` | §5.1 | `source_path`, relativ zur Projektwurzel gemacht, plus `author` (ein einzelnes `authored_by`), `last_modified` (`frontmatter_date` / `analysis_date`), `usage_count` (verschiedene `discussed_in`-Sitzungen) |
| `usage_window` | §5.1 | Min/Max von `started_at` / `ended_at` der oben gezählten Sitzungen |
| `status: deprecated`, `stale_after` | §5.4, §5.5 | Knoten, auf die eine `supersedes`-Kante zeigt; `stale_after` ist das Datum des ablösenden Knotens und entfällt, wenn es vor dem Datum des abgelösten läge |
| `x_tesserae` | Erweiterung | Echte Knoten-ID, Aliase, `source_path`, Metadaten, typisierte Kanten — der verlustfreie Rückweg-Kanal |

`index.md` folgt §8 (das Frontmatter ist exakt `okf_version: '0.2'`, die einzige Stelle, an der §12 es erlaubt) und `log.md` folgt §9 (kein Frontmatter, `## YYYY-MM-DD`-Gruppen, neueste zuerst). Auf dem Projektgraphen von Tesserae (5197 Knoten / 15284 Kanten) sind das 5195 Dateien, mit `generated` auf allen 5193 Konzepten, `sources` auf 3934, `usage_window` auf 1264, `description` auf 1749, `resource` auf 822 und `status`/`stale_after` auf 25.

**Bewusst nicht ausgegeben.** Kein `verified`-Schlüssel (§5.2) und damit keine Vertrauensstufe oberhalb von `unverified` (§5.3): Nichts im kompilierten Graphen ist ein aufgezeichnetes Verifikations-*Ereignis* mit Akteur und Zeitstempel. `verify_claim` und das Neu-Erden sind Funktionen zur Abfragezeit über dem Graphen, und `lint --verify-claims` ist ein LLM-Richter, von dem [`verify.py`](../../tesserae/verify.py) selbst sagt, er sei kein Beleg. Kanten-Provenienzklassen beschreiben, wie stark der Graph ein *Tripel* zulässt; OKFs Vertrauensfamilie ist eine Bestätigung pro *Konzept*. Das eine auf das andere abzubilden setzte eine maschinell bestätigte Stufe auf Inhalte, die niemand bestätigt hat — deshalb darf `generated.by` nie mit `human:` beginnen; ein Test hält das fest. Ebenso keine Attested-Computation-Familie (§10): Tesserae hat keine sanktionierten Berechnungen, keinen Executor, keine Quittung und keine Attester-ABI, und §10.5 weist Konsumenten an, auf Attestierung zu *gaten* — leeres Gerüst würde also einen Vertrag bewerben, der nicht eingehalten werden kann. Mangels ehrlicher Quelle fehlen ebenfalls: `tags` (es gibt kein Tag-Feld auf Knotenebene — `aliases` sind Alternativnamen, keine Kategorien), `[^id]`-Fußnoten je Behauptung, `status: draft` (`metadata.confidence` ist Extraktionskonfidenz, kein Review-Status) und jeder gespeicherte Glaubwürdigkeitswert (§5.1 hält Signale fest, keine Urteile). `last_modified` stammt aus Dokumentdaten im Graphen, **nie** aus der mtime der Datei — die naheliegend wirkende `os.stat()`-Abkürzung ist genau jenes Umgebungsleck, das hier schon einmal die byteweise Idempotenz zerstört hat.

**Lesen.** Gemäß §11 weist der Importer nichts zurück: unbekannte `type`-Werte, unbekannte Frontmatter-Schlüssel, fehlende optionale Familien, kaputte Querverweise und eine fehlende `index.md` werden alle toleriert; übersprungen wird nur eine Datei ohne nicht leeren `type`. Tesserae-eigene Bundles laufen über `x_tesserae` verlustfrei hin und zurück. Fremde Bundles bilden `type` → die passende Knotenart oder `Concept` ab, Links im Text → `references`-Kanten, und jeden unbekannten Frontmatter-Schlüssel nach `metadata.okf` (das Round-Trip-SHOULD aus §4.1), wobei ein nacktes `verified` zu einer einelementigen Liste normalisiert wird (MUST aus §11). v0.1-Rückfälle (§13.1): Ein altes `timestamp` landet in `metadata["updated_at"]` (eine Sprosse, die die Zeitstempelleiter ohnehin liest), und eine alte `# Citations`-Liste im Text wird zu `metadata["okf"]["sources"]` und aus der Beschreibung herausgelöst, statt als Prosa verschluckt zu werden. Beim Re-Export wird der bewahrte Eimer *über* alles gemischt, was Tesserae abgeleitet hat — der Re-Export eines fremden Bundles überschreibt deren Provenienz oder Vertrauensaussagen also nie mit unseren; `--import` gibt ein Histogramm der Vertrauensstufen aus, sodass ein gemischtes Bundle sichtbar statt stumm ist. Die Stufen werden beim Lesen von `okf_trust_tier` abgeleitet und nie gespeichert.

**Brechende Änderungen gegenüber Tesseraes v0.1-Ausgabe.** Aus `name:` wird `title:` (`name` war in keiner der beiden Versionen je ein OKF-Schlüssel; der Reader akzeptiert es weiterhin, hinter `title`). `index.md` und `log.md` verlieren ihr `type:`/`name:`-Frontmatter (§8, §9), sodass ein Konsument, der sie als typisierte Konzepte behandelte, zwei Phantomeinträge verliert — was genau der Punkt ist; damit zusammenhängend sind sie nun auf *jeder* Ebene der Hierarchie reserviert (§3.1), nicht nur in der Bundle-Wurzel. Die Bytes jeder Konzeptdatei ändern sich, der erste v0.2-Export schreibt das ganze Bundle also neu.

**Bekannte Grenzen.** `usage_count` zählt verschiedene Agenten-/Arbeitssitzungen, deren Transkript das Dokument berührt hat, nicht menschliche Seitenaufrufe — §5.1 warnt bereits, dass das Signal grob ist; lesen Sie es als Lebendigkeit, nicht als Beliebtheit. Die Lebenszyklus-Familie greift nur bei Knoten, auf die eine `supersedes`-Kante zeigt (hier 25 von 5197); echte Abdeckung bräuchte die zeitlichen Gültigkeitsintervalle, die `TemporalFactProjector` zur Abfragezeit ableitet, und das im Exporter über 15k Kanten laufen zu lassen wurde als Scope-Überschreitung verworfen. `generated.by` verwendet absichtlich `process:tesserae-<extractor>` statt des `<producer>/<version>` aus §7: Ein versionstragender Akteur würde alle ~5200 Konzeptdateien bei jedem Release neu schreiben, ohne dass sich semantisch irgendetwas ändert. Kein pfadwertiges OKF-Feld (`resource`, `sources[].resource`) trägt je einen absoluten Pfad — einer, der sich nicht relativ zur Projektwurzel machen lässt, wird ausgelassen statt roh ausgegeben, da §6.2 einen Konsumenten dazu brächte, ihn als bundle-relativ zu lesen — auch wenn absolute Pfade weiterhin innerhalb von `x_tesserae.source_path` (die echte Identität des Knotens, die ein fremder Konsument ignoriert) und in Knoteninhalten auftauchen können, die zufällig einen zitieren.

## Kuzu-Export

[`tesserae/kuzu_adapter.py`](../../tesserae/kuzu_adapter.py) projiziert `graph.json` in eine eingebettete [Kuzu](https://kuzudb.com)-Datenbank, damit ein anderes Werkzeug Cypher über dem Graphen ausführen kann. `tesserae export kuzu` schreibt sie; `--graph PATH` exportiert einen nackten extrahierten Graphen statt des kompilierten Projektgraphen. Es ist ein **Einweg-Export**, der dritte von dreien neben [OKF](#okf-v02-export-import) und [Graphiti](../../tesserae/graphiti_adapter.py), und `write_graph(replace=True)` löscht die Datenbank und legt sie neu an, sodass die Ausgabe eine reine Funktion des übergebenen Graphen ist.

**Kuzu ist bewusst kein Store, und die Unterscheidung ist tragend.** Ein `KuzuResearchGraphStore` saß früher in [`tesserae/persistence.py`](../../tesserae/persistence.py) neben dem echten SQLite-Store, erreichbar nur über ein `extract --kuzu-output`-Flag, dessen Abhängigkeit als dev-only deklariert war — ein halb verdrahtetes zweites Backend, und genau das ließ „sollte Tesserae eine Graphdatenbank übernehmen?" wie eine offene Frage aussehen. Sie ist nicht offen, und der Grund ist architektonisch, nicht rechtlich (Kuzu ist MIT-lizenziert, eingebettet und braucht keinen Server):

- **Ein zweiter autoritativer Store kann `graph.json` über denselben Fakt widersprechen**, und es gibt keinen Schiedsrichter. `graph.json` ist die Quelle der Wahrheit; alles, was ihr widersprechen kann, ist eine Fehlerfläche.
- **Byte-Idempotenz würde von einer reinen Funktion auf die Schreibreihenfolge einer Datenbank übergehen.** Die von `tests/test_byte_idempotence_phase5.py` festgehaltene Eigenschaft — zwei Kompilationen erzeugen byte-identische `graph.json` — gilt, weil Kompilieren eine reine Funktion mit sortierten Schlüsseln über ihren Eingaben ist. Kein verglichenes Graph-Memory-System versucht das überhaupt, und Schreibvorgänge durch eine Engine zu leiten ist der Weg, es zu verlieren.

Auf einen Export trifft keiner der beiden Einwände zu: Die Datenbank ist abgeleitete Ausgabe, wird aus dem Graphen gelöscht und neu geschrieben, und kein Kompilier- oder Abfragepfad liest sie zurück. `read_graph` existiert aus demselben Grund wie `okf.read_okf_bundle` — ein Export, den man nicht zurücklesen kann, ist ein Export, den man nicht verifizieren kann — nicht weil irgendetwas in der Engine aus Kuzu lädt. `tests/test_kuzu_adapter.py` stellt sicher, dass `tesserae.persistence` kein Kuzu-Symbol exportiert, sodass ein wiedereingesetzter Store an der Suite scheitert statt am Review.

Dasselbe Urteil schließt Neo4j als Substrat aus: siehe [`docs/superpowers/specs/2026-08-14-neo4j-agent-memory-roadmap.md`](../superpowers/specs/2026-08-14-neo4j-agent-memory-roadmap.md), das die Fähigkeiten (ein persistierter Vektorindex, ein Soft-Merge-Grabstein, eine Transaktionszeit-Uhr) als Datei- und SQLite-Sidecars übernimmt statt als Engine.

## Idempotenz-Story

Das Redesign zielt auf **byte-identischen Output über zwei aufeinanderfolgende `project compile`-Läufe bei unveränderten Inputs**. Die Bausteine:

1. **Source-Extraktion** nutzt die Content-Hashes aus `manifest.json`; unveränderte Dateien werden übersprungen, der Graph bleibt stabil.
2. **Wiki-Layer-Writes** sind auf Body-Ebene idempotent. `WikiPageStore.write_page` liest die existierende Datei, entfernt Frontmatter, sha256t den Body und kurzschließt, wenn der neue Body denselben Hash ergibt — auch wenn das neue Frontmatter einen anderen `generated_at`-Timestamp hat. Das ist der Schlüsseltrick, der git-Diffs beim Rebuild eng hält.
3. **Synthesis-Output** trägt einen `content_hash: sha256-…` im Frontmatter. Der Body-Hash wird ohne `generated_at` berechnet, sodass wiederholte Compiles auf demselben Graph denselben Hash erzeugen, und `Synthesis`-Knoten tragen denselben `content_hash` in den Graph-Metadaten.
4. **Site-Rendering** löscht `site/` zu Beginn von `write_site` und schreibt dann deterministisch: Routen sind sortiert, Dicts werden mit `sort_keys=True` gedumpt, `manifest.json` läuft über `sorted(rglob("*"))`. Zwei Läufe erzeugen byte-identische Dateien inklusive Manifest.
5. **Knotendaten sind quellenabgeleitet.** Das `first_seen_at` eines Knotens stammt aus dem Pfad, unter dem seine Quelle aufgenommen wurde, nicht aus der Wanduhr zur Kompilierzeit. Eine Uhrzeit zu lesen machte jeden erneuten Lauf zu einem Diff — genau deshalb zerstört die naive Variante dieses Punkts Punkt 1. Dieselbe Regel hält den `Event`-Lauf byteweise idempotent: Jede erzeugte ID, jeder Text und jedes Datum ist inhaltsabgeleitet, verifiziert über ein Korpus von 481 Sitzungen.

Das wird durch `tests/test_site_pages.py` und den End-to-End-Smoke in `tests/test_project_e2e_redesign.py` verifiziert (zweimal compilen, Sites diffen, null Deltas erwarten).

## Skalierungsnotizen

- **Graph-View-Knoten-Cap.** [`MAX_GRAPH_NODES = 1500`](../../tesserae/site/pages.py) begrenzt das in die Seite eingebettete Payload für das interaktive Force-Layout. Jenseits von ~1500 Knoten wird die Browser-Simulation auf Mid-Range-Hardware träge, daher droppt die Seite zuerst die Wiki-Layer-Knoten mit dem niedrigsten Grad, sobald der Count das Cap überschreitet. Die exportierte `graph.json` ist davon unberührt — sie enthält immer den vollen Graph. Code-Knoten werden vor dem Cap herausgefiltert.
- **`llms-full.txt`-Cap.** Ein Safety-Cap von 5 MB greift in [`tesserae/site/exports.py`](../../tesserae/site/exports.py); die Datei endet mit einem `[TRUNCATED — see graph.jsonld for the full set]`-Marker, wenn der Cap erreicht wird. `graph.jsonld` ist uncapped, weil JSON-LD-Consumer das volle Set erwarten.
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
