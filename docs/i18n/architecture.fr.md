# Architecture

<!-- translations:start -->
<p align="center"><a href="../architecture.md">English</a> · <a href="architecture.ko.md">한국어</a> · <a href="architecture.zh.md">中文</a> · <a href="architecture.ja.md">日本語</a> · <a href="architecture.ru.md">Русский</a> · <a href="architecture.es.md">Español</a> · <a href="architecture.fr.md">Français</a> · <a href="architecture.de.md">Deutsch</a></p>
<!-- translations:end -->
Tesserae est un **moteur de contexte**. Il reconstruit une base de connaissances auto-améliorante à partir de votre projet et la remet aux agents comme contexte prêt à l’emploi. Il repose sur trois piliers : (1) **surveillance de sessions** — observer les sessions d’agents/de travail en direct et capturer les constats à mesure qu’ils surviennent ; (2) **ingestion de connaissances autonome et proactive** — un pipeline + une boucle superviseur qui tirent et ré-extraient les connaissances en continu, améliorant la base plutôt que d’attendre qu’on le leur demande ; (3) **docs/contexte à la demande** — des artefacts demandés par l’utilisateur, compilés depuis cette même base. Le graphe typé, le vault markdown et le site statique sont des *projections* de la base de connaissances ; le moteur est la boucle qui les garde frais et alimente les agents.

En dessous, Tesserae transforme un répertoire de matériau source en un graphe de connaissances typé et contrôlé, et projette ce graphe à travers une couche wiki markdown durable vers un site web statique adapté aux IA. La refonte d’avril 2026 a réorganisé le côté projection autour d’un modèle à trois couches façon Karpathy : la preuve brute reste brute, un graphe typé gouverne l’ontologie, et une couche wiki markdown se tient entre le graphe et toute sortie rendue. Le site statique est un *moteur de rendu* de cette couche wiki plutôt qu’un déversement direct du graphe, avec l’ontologie contrôlée de [`tesserae/research_graph.py`](../../tesserae/research_graph.py) comme schéma. Le jalon **v0.5.0** (juin 2026) a ajouté la colonne vertébrale du moteur qui anime les trois piliers — voir *Colonne vertébrale du moteur* et *Compilateur de contexte à la demande* ci-dessous.

## Le modèle à trois couches de Karpathy

Le cadrage d’Andrej Karpathy pour les bases de connaissances adaptées aux LLM distingue trois couches, chacune avec sa propre garantie de durabilité :

| Couche | Préoccupation | Emplacement dans le dépôt | Propriétaire |
|---|---|---|---|
| L1 — Sources brutes | Les octets littéraux que l’utilisateur a écrits ou collectés. Append-only. | `data/`, `docs/`, arbres de projet référencés dans `.tesserae/config.json` | l’utilisateur |
| L2 — Wiki | Pages markdown typées (sources, concepts, entités, papers, repos, topics, synthèses, questions) avec frontmatter YAML. Idempotentes : régénérées à chaque compilation, mais réécrites seulement quand les hashes de contenu changent. | `.tesserae/wiki/` | `WikiPageStore`, `WikiLayerProjector`, `SynthesisProjector` |
| L3 — Rendu | Le site HTML statique, les exports siblings IA, l’index de recherche, les sitemaps, le JSON-LD. Effacé et réécrit à chaque compilation, mais octet-stable entre exécutions. | `.tesserae/site/` | `StaticSiteBuilder` (`tesserae/site/`) |

Le schéma traverse les trois couches comme un axe séparé : `ResearchGraph` dans `graph.json` est l’ontologie contrôlée contre laquelle les pages L2 se lient, et la liste blanche `ResearchNodeType` / arêtes dans [`tesserae/research_graph.py`](../../tesserae/research_graph.py) est la source de vérité pour les types qui existent tout court.

La refonte a ajouté L2 explicitement. Avant avril 2026, le site statique était projeté directement depuis `graph.json` ; la couche wiki n’existait qu’à l’intérieur de l’export du vault Obsidian. La détacher nous a donné :

- Une seule surface éditable par l’humain (ouvrez `.tesserae/wiki/` dans Obsidian ou n’importe quel éditeur markdown).
- Des reconstructions idempotentes : relancer `project compile` produit zéro diff de fichiers sauf si le contenu source a changé.
- Un journal d’évolution : les pages de synthèse s’accumulent au fil du temps et laissent le projet se raconter lui-même.

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

Chaque étape est incrémentale. L’extracteur de graphe utilise les hashes de contenu de `manifest.json` pour sauter les fichiers sources inchangés. `WikiPageStore.write_page` retourne `False` (et saute l’écriture) quand le hash du corps correspond à ce qui est déjà sur disque. `StaticSiteBuilder` efface et réécrit `.tesserae/site/`, mais sa sortie est déterministe — voir « Histoire d’idempotence » ci-dessous.

## Flux de données du compilateur de contexte

Le compilateur de contexte à la demande ([`tesserae/context_compiler.py`](../../tesserae/context_compiler.py)) est le chemin vedette du pilier 3. Étant donné une requête et/ou des ids de nœuds graines explicites, `compile_context` construit un bundle markdown cité et sur mesure directement depuis le graphe et le retourne en mémoire — il n’écrit rien sous `.tesserae/`.

```
query / seeds
     │
     ▼  1. Seed resolution
        explicit seeds (kept iff they exist) + hybrid_search() hits, deduped, stable order
     │
     ▼  2. PPR expansion
        retrieval.ppr.personalized_pagerank ranks the depth-bounded k-hop neighbourhood;
        empty result (disconnected seeds) → fall back to seed order (bundle is never empty)
     │
     ▼  2b. Réservation procédurale (méritée, non octroyée)
        une place par pool, dans l'ordre de PROCEDURAL_POOL_ORDER : Runbook, Gotcha, Event,
        DistilledNote, ExpertiseProfile. La place revient au nœud le mieux classé de ce type
        qui porte une provenance de PRODUCTEUR — pas au simple nom de type
     │
     ▼  3. Budget-bound selection
        walk PPR order, include each node's cited body until the next would overflow
        `budget` chars (budget <= 0 = uncapped; over-budget marker on a word boundary)
     │
     ▼  4. Cited markdown assembly
        one section per selected node + a trailing `## Citations` block.
        Body text prefers the projected wiki page (when a store + public wiki kind exist),
        else the node description, else a minimal stub. The no-LLM body embeds NO
        wall-clock timestamp → byte-identical for the same (graph, query, seeds, depth, budget).
     │
     ▼  5. Optional LLM synthesis  (only when synthesize=true AND ANTHROPIC_API_KEY is set)
     ▼
   ContextBundle { query, seeds_used, ranked_nodes, selected_nodes,
                   citations[ContextCitation], body, synthesized,
                   char_budget_used, char_budget_total }
```

Défauts : `depth=2`, `budget=32000`. L’assemblage déterministe (étapes 1–4) est le contrat ; la synthèse LLM est purement additive. Le même pipeline sous-tend la commande CLI `project context`, l’outil MCP `compile_context` et les tranches d’export ciblées par topic (`slice_export_context_for_topic`, `llms.txt` ciblé par topic).

**Pourquoi la place procédurale se mérite par la provenance.** Les cinq types
procéduraux nomment ce qu'un agent a fait, a appris à faire et sait bien faire —
mais l'extraction documentaire a elle aussi le droit de les créer, si bien qu'un
LLM lisant un appel à communications crée légitimement un `Event` typé nommé
« CVPR 2026 ». La réservation est *additive* : elle promeut un nœud depuis
n'importe où dans le voisinage jusqu'en tête du parcours budgété. Réserver sur le
seul type laisserait donc une date limite de conférence évincer le constat de
session qui a réellement mérité la place. Ce qui sépare les deux, c'est
`has_producer_provenance` ; et une réservation est une revendication de place, pas
la preuve qu'elle a été honorée : `delivered` n'est tranché qu'après le parcours
budgété, de sorte que l'appelant distingue « de la mémoire procédurale a été
réservée » de « de la mémoire procédurale est arrivée ». Le code de lint
`PROCEDURAL_POOLS` signale l'écart.


## Carte des modules

### Wiki + synthèse (L2)

| Module | Responsabilité |
|---|---|
| [`tesserae/wiki_store.py`](../../tesserae/wiki_store.py) | Dataclass `WikiPage`, `WikiPageStore` pour l’E/S du système de fichiers. Parseur de frontmatter sous-ensemble YAML en stdlib seulement. Idempotence par hash de corps. |
| [`tesserae/wiki_projector.py`](../../tesserae/wiki_projector.py) | `WikiLayerProjector` : mappe chaque nœud `ResearchGraph` d’un type de la couche wiki vers une page markdown dans le bon dossier `kind/`. |
| [`tesserae/synthesis.py`](../../tesserae/synthesis.py) | `SynthesisProjector` : modèles déterministes pour pulse, daily_digest, weekly, topic, comparison, field_overview. Ajoute des nœuds `Synthesis` et des arêtes `synthesizes` / `summarizes` en retour dans le graphe. |

### Graphe + ontologie

| Module | Responsabilité |
|---|---|
| [`tesserae/research_graph.py`](../../tesserae/research_graph.py) | Enum `ResearchNodeType` (y compris `SYNTHESIS`), liste blanche des types d’arêtes (y compris `synthesizes`, `summarizes`), validation. |
| [`tesserae/canonicalization.py`](../../tesserae/canonicalization.py) | Canonicalisation des alias + file de revue des quasi-doublons. |
| [`tesserae/code_graph.py`](../../tesserae/code_graph.py) | Extracteur AST Python déterministe pour la tranche développement. |
| [`tesserae/llm_extractor.py`](../../tesserae/llm_extractor.py) | Extracteur sélectif Claude CLI/OAuth. |

### Moteur de rendu du site (L3)

| Module | Responsabilité |
|---|---|
| [`tesserae/site/__init__.py`](../../tesserae/site/__init__.py) | `StaticSiteBuilder.write_site` : efface + reconstruit le site, parcourt chaque route, émet exports + siblings IA + manifest. |
| [`tesserae/site/pages.py`](../../tesserae/site/pages.py) | Un moteur de rendu par route (accueil, index, pages de détail, timeline, graphe, about). `SiteContext` transporte des indices précalculés pour que les moteurs de rendu restent purs. |
| [`tesserae/site/components.py`](../../tesserae/site/components.py) | Primitives HTML : `breadcrumbs`, `card`, `badge`, `node_table`, `edge_list`, `sparkline_svg`, `heatmap_svg`, `toc`, `page_shell`, `ai_siblings_footer`. |
| [`tesserae/site/tokens.py`](../../tesserae/site/tokens.py) | Design tokens — variables CSS, thèmes clair + sombre, mise en page, typographie, tous les composants stylés ici. |
| [`tesserae/site/js.py`](../../tesserae/site/js.py) | Bundle JS client : palette de recherche, bascule de thème, vue graphe sigma + 3D-force. |
| [`tesserae/site/markdown.py`](../../tesserae/site/markdown.py) | Moteur de rendu markdown en stdlib seulement (liens, autoliens, code, emphase, titres). Aucune dépendance externe. |
| [`tesserae/site/relevance.py`](../../tesserae/site/relevance.py) | Score de pertinence à quatre signaux (lien direct, recouvrement de sources, Adamic-Adar, affinité de type) utilisé par chaque section `Related`. |
| [`tesserae/site/search.py`](../../tesserae/site/search.py) | Constructeur de `search-index.json`. Types de la couche wiki uniquement. |
| [`tesserae/site/sessions.py`](../../tesserae/site/sessions.py) | Moteurs de rendu index/détail de session pour l’historique de harness importé : sections de résumé de mémoire projet, rail de tours de conversation, rendu markdown des transcriptions et blocs d’usage d’outil repliés. |
| [`tesserae/site/exports.py`](../../tesserae/site/exports.py) | `llms.txt`, `llms-full.txt`, `graph.jsonld`, `sitemap.xml`, `rss.xml`, `robots.txt`, `ai-readme.md`, siblings `.txt`/`.json` par page. |

### Orchestration du pipeline

| Module | Responsabilité |
|---|---|
| [`tesserae/project.py`](../../tesserae/project.py) | `ProjectWiki.compile` : pilote extraction → graphe → passes mémoire → couche wiki → site. Possède `ProjectPaths` (`config`, `graph`, `manifest`, `wiki`, `site`, etc.). Décide en amont si une compilation incrémentale pilotée par la provenance est éligible (barrée par `incremental_compile`, OFF par défaut). |
| [`tesserae/cli.py`](../../tesserae/cli.py) | Dispatch CLI à verbes plats (~2 732 lignes après la suppression des groupes de sous-commandes hérités `project`/`wiki`). Les verbes — `init`, `compile`, `ingest`, `context`, `ask`, `query`, `doctor`, `summary`, `decisions`, `refresh`, `serve`, `engine`, `export`, `vault`, `code`, `lab`, `setup`, `config`, `projects`, `sources`, `federation`, `integrations` — sont déclarés comme métadonnées dans [`tesserae/cli_tree.py`](../../tesserae/cli_tree.py) et câblés depuis cet arbre plutôt qu’enregistrés à la main. |
| [`tesserae/deploy.py`](../../tesserae/deploy.py) | `export site --deploy` : pousse `.tesserae/site/` vers une branche `gh-pages` via worktree, active éventuellement Pages via `gh`. |

### Colonne vertébrale du moteur (v0.5.0 — piliers 1 & 2)

La colonne vertébrale du moteur est la boucle en processus qui anime la surveillance de sessions et la ré-ingestion autonome. Le même `Pipeline.run()` est l’unique chemin de rafraîchissement que la CLI, le daemon superviseur et (plus tard) le serveur MCP appellent tous.

| Module | Responsabilité |
|---|---|
| [`tesserae/engine/pipeline.py`](../../tesserae/engine/pipeline.py) | `Pipeline` : un exécuteur d’étapes séquentiel. Codifie la chaîne de refresh en prose (ingest → compile → project/publish) comme un objet importable qui retourne une `List[StepResult]` structurée au lieu d’imprimer-et-sortir, si bien que chaque appelant décide comment faire remonter les résultats. `run()` attrape `Exception` par étape (laisse passer `KeyboardInterrupt`/`SystemExit`) et s’arrête au premier échec. |
| [`tesserae/engine/daemon.py`](../../tesserae/engine/daemon.py) | `Daemon` : superviseur asyncio à propriétaire unique. Surveille les répertoires sources, le vault Obsidian et le répertoire des sessions de harness ; coalesce une rafale de `TriggerEvent` en exactement un `Pipeline.run()` via un debounce cancel-and-reschedule. Réutilise les watchers existants `watch.py` / `vault_watch.py` (il ne les réécrit pas), écrit un pidfile et survit aux exceptions en vol. Exposé comme `engine` (`--interval`, `--debounce`, `--once`). |
| [`tesserae/watch.py`](../../tesserae/watch.py), [`tesserae/vault_watch.py`](../../tesserae/vault_watch.py) | Watchers par sondage réutilisés à la fois par la commande autonome `export site --watch` et par les voies source/vault du daemon. |

### Mémoire d’auto-amélioration (v0.5.0 — pilier 2)

La Phase 5 a activé l’auto-amélioration persistante. L’état mutable par nœud vit dans un sidecar SQLite `node_memory` (à l’intérieur de `.tesserae/sqlite.db`), séparé de l’estampille first-seen immuable `node_provenance.first_seen_at` (un sidecar de Phase 4). La compilation pilote un ensemble de passes déterministes sur le graphe.

| Module | Responsabilité |
|---|---|
| [`tesserae/memory/store.py`](../../tesserae/memory/store.py) | `NodeMemoryRow` + accesseurs agnostiques du store (`read_memory`, `write_memory`, `bump_access`) sur la table `node_memory` — `decay_score`, `last_accessed_at`, `confidence`, `superseded`. Aucun site d’appel n’embarque du SQL brut. |
| [`tesserae/memory/decay.py`](../../tesserae/memory/decay.py) | `compute_decay_score` : score de fraîcheur façon Ebbinghaus (le plus récent + le plus accédé d’abord) utilisé pour classer les constats de session. |
| [`tesserae/memory/supersede.py`](../../tesserae/memory/supersede.py) | `run_supersede_pass` (**activée par défaut**) : verdict déterministe qui marque un insight quasi-doublon plus ancien comme supplanté par un plus récent, en ajoutant une arête `supersedes`. |
| [`tesserae/memory/insight_symbol_link.py`](../../tesserae/memory/insight_symbol_link.py) | `run_insight_symbol_link_pass` : lie les insights de session aux symboles de code dont ils discutent via des arêtes `discusses`. |
| [`tesserae/memory/reinforce.py`](../../tesserae/memory/reinforce.py), [`tesserae/memory/contradiction.py`](../../tesserae/memory/contradiction.py) | Helpers de renforcement d’accès et de détection de contradictions sur le même sidecar. |

La confiance de récurrence est numérique en sortie : la projection temporelle estampille la `confidence` de chaque fait depuis `NodeMemoryRow.confidence` (texte dans SQLite, exposé via `temporal.py`), avec repli sur `infer_confidence` uniquement quand aucune valeur stockée n’existe.

### Récupération (v0.5.0 — piliers 2 & 3)

| Module | Responsabilité |
|---|---|
| [`tesserae/retrieval/hybrid.py`](../../tesserae/retrieval/hybrid.py) | `hybrid_search` : récupérateur hybride local d’abord fusionnant trois voies — Okapi BM25 (k1=1.5, b=0.75), sous-chaîne lexicale/style FTS insensible à la casse, et une voie d’embeddings enfichable — via la fusion de rangs réciproques (RRF, k=60). Entièrement déterministe. |
| [`tesserae/retrieval/ppr.py`](../../tesserae/retrieval/ppr.py) | `personalized_pagerank` : Personalized PageRank façon HippoRAG-2 (arXiv:2502.14802) sur le graphe pour l’expansion multi-sauts des graines — fait remonter des nœuds bien connectés à plusieurs sauts de la graine, pas seulement le voisinage à 1 saut. |
| Backend d’embeddings (Phase 6, Track B) | Le backend par défaut de la voie d’embeddings hybride est un pseudo-embedding hash-bucket déterministe qui ne requiert aucune dep supplémentaire ; `sentence-transformers` (`all-MiniLM-L6-v2`) est préféré et chargé paresseusement quand la dépendance optionnelle est installée. L’outil MCP `embedding_status` rapporte quel backend est actif. |

### Compilateur de contexte à la demande (v0.5.0 — vedette du pilier 3)

| Module | Responsabilité |
|---|---|
| [`tesserae/context_compiler.py`](../../tesserae/context_compiler.py) | `compile_context` : la fonctionnalité vedette du pilier 3. Compile un bundle de contexte sur mesure et **cité** pour un ensemble requête/graines directement depuis le graphe — voir *Flux de données du compilateur de contexte* ci-dessus. Retourne un `ContextBundle` en mémoire (avec des `ContextCitation`) ; n’écrit rien sur disque. Exposé comme la commande CLI `project context` et l’outil MCP `compile_context`. |

### Ports de persistance + stores de graphe

| Module | Responsabilité |
|---|---|
| [`tesserae/ports/graph_store.py`](../../tesserae/ports/graph_store.py) | Protocole `GraphStore` : `upsert_node`/`upsert_edge`, `get_node`, `iterate_nodes`, `query_subgraph`, `find_canonical`, et la surface de suppression de Phase 4 — `delete_node` et `delete_nodes_by_source` (supprime les nœuds dont l’ensemble de provenance devient vide après retrait des chemins sources donnés, si bien que les concepts multi-fichiers survivent). |
| [`tesserae/graph_stores/sqlite.py`](../../tesserae/graph_stores/sqlite.py) | `SqliteGraphStore` : store de persistance autonome ; possède les tables sidecar `node_provenance` et `node_memory`. |
| [`tesserae/graph_stores/url_resolver.py`](../../tesserae/graph_stores/url_resolver.py) | Résout une URL de store (`sqlite:///…`, `hypepaper-postgres://…`) vers le bon `GraphStore`, permettant au serveur MCP de pointer vers n’importe quel store au runtime. |

### Adaptateurs externes (inchangés cette fois-ci)

| Module | Responsabilité |
|---|---|
| [`tesserae/obsidian_adapter.py`](../../tesserae/obsidian_adapter.py) | Projection du vault Obsidian (coloration du graphe, tableau de bord Dataview, assets bruts). |
| [`tesserae/agent_harness.py`](../../tesserae/agent_harness.py) | Exports de harness Claude Code / Codex / Gemini / Kiro / Cursor / OpenCode. |
| [`tesserae/harness_sessions.py`](../../tesserae/harness_sessions.py) | Découverte, normalisation, stockage sous `.tesserae/harness_sessions/` des sessions Claude Code/Codex entrantes, et résumés markdown expurgés. |
| [`tesserae/graphiti_adapter.py`](../../tesserae/graphiti_adapter.py) | JSONL de faits temporels + sync Graphiti en direct optionnelle. |
| [`tesserae/mcp_server.py`](../../tesserae/mcp_server.py) | Serveur MCP stdio. Récupération/graphe : `schema`, `graph_summary`, `search_nodes`, `node_context` (avec `use_ppr`), `search_facts`, `timeline`, `graph_ppr`, `wiki_page`, `raw_source`, `lint_report`, `doctor_report`. Moteur de contexte (v0.5.0) : `compile_context` (le compilateur de contexte à la demande), `embedding_status`, `fresh_insights` (constats de session classés par décrue), `list_communities`, `find_session_findings`, `find_code_symbol_mentions`. Plus `ask`, les outils du registre multi-projets (`list_projects`, `register_project`, `unregister_project`, `list_sessions`), et `tesserae_setup_plan` / `tesserae_setup_apply`. |

## Disposition de l’espace de travail du projet

```text
.tesserae/
  config.json                 project name, source kind, source list
  graph.json                  validated ResearchGraph (incl. Synthesis nodes)
  manifest.json               per-source content hashes (input dedup)
  sqlite.db                   SQLite graph store; also owns the node_provenance
                              (first-seen, Phase 4) and node_memory (decay /
                              confidence / superseded, Phase 5) sidecar tables
  temporal_facts.jsonl        Graphiti-style temporal projection (numeric recurrence confidence)
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

Chaque fichier est éditable à la main ; la compilation suivante honore les éditions de l’utilisateur tant que le hash du corps diffère de ce que le projecteur écrirait. (Éditer seulement le corps gagne ; éditer le frontmatter perd à la compilation suivante parce que le frontmatter est régénéré.) Les utilisateurs d’Obsidian peuvent ouvrir `.tesserae/wiki/` directement ; l’adaptateur `obsidian_vault/` existant est une projection séparée, pas un substitut.

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

## La charte

La détection de communautés **propose** un vocabulaire de domaines ; la charte
([`tesserae/charter.py`](../../tesserae/charter.py)) le **possède** entre deux
réorganisations explicites. Cette séparation existe parce que la détection est
déterministe mais pas stable : une entrée identique reproduit exactement les
1 649 communautés, et pourtant un seul document de 15 nœuds déplace environ 29 %
des membres d'une communauté à l'autre et fait chuter les grandes communautés à
un Jaccard de 0,39–0,60. Tout ce qui s'indexe sur l'appartenance à une communauté
subit donc un défaut de cache quasi total à chaque ingestion — et ce corpus
ingère quotidiennement.

La charte fige donc l'institution : les sections sont détectées, repliées en un
graphe quotient (un nœud par section, une arête `part_of` par arête L0
inter-sections), puis découpées en divisions → départements → équipes **par
sous-communauté, jamais par taille**. L'ancre de chaque domaine est son membre de
plus haut degré, choisi gloutonnement pour que deux domaines n'en partagent
jamais une seule ; le slug destiné aux humains est frappé une fois à partir de
cette ancre, puis épinglé. Au fil d'une réorganisation, `succeed` reporte les
slugs en appariant les ancres, si bien qu'un nom stable survit au brassage des
membres qui se trouvent dessous. Chaque nœud atterrit dans exactement un domaine :
`intake_members` rattrape les singletons écartés et les sections isolées côté
arêtes que la détection perdrait sinon en silence.

`tesserae domains status [--json]` affiche l'arbre. **État :** le module et son
verbe CLI sont livrés et couverts par des tests, mais `compile` n'écrit pas
encore de charte ; d'ici là, la commande renvoie « no charter yet » et sort avec
0, ce qui est aussi la réponse honnête pour un projet sous le seuil d'une seule
lecture.

## Ce qui est délibérément exclu

La refonte a tracé une ligne explicite : les nœuds code-class et code-function restent dans `graph.json` (pour que les consommateurs MCP et Graphiti les voient toujours) mais n’obtiennent jamais de pages HTML, n’apparaissent jamais dans `search-index.json` et n’apparaissent jamais dans la navigation. C’est le contrat côté utilisateur — le wiki est une base de connaissances orientée documents, pas un navigateur de fonctions.

Concrètement, `StaticSiteBuilder` saute tout nœud dont le type n’est pas dans la carte des types de wiki L2 (`tesserae/wiki_projector.py::_KIND_FOR_TYPE`) :

- Exclus de L2 + L3 : `CodeClass`, `CodeFunction`, `CodeModule`, `Dependency`, `EvidenceSpan`, `SourceFile`, toutes les variantes de `Claim` (`Claim`, `ContributionClaim`, `PerformanceClaim`, `ComparisonClaim`, `LimitationClaim`, `CausalClaim`).
- Surface où ils apparaissent encore : en puces, badges, comptes de voisins ou extraits de preuve inline sur les pages wiki associées, et dans `graph.json` pour l’outillage aval.

Si vous avez besoin de navigation au niveau du code, pointez un outil LSP / de graphe d’appels vers l’arbre source directement — c’est un problème différent de « wiki de ce que ce projet sait ».

## Histoire d’idempotence

La refonte vise une **sortie octet-pour-octet identique sur deux exécutions consécutives de `project compile` avec des entrées inchangées**. Les pièces :

1. **L’extraction de sources** utilise les hashes de contenu de `manifest.json` ; les fichiers inchangés sont sautés, si bien que le graphe reste stable.
2. **Les écritures de la couche wiki** sont idempotentes au niveau du corps. `WikiPageStore.write_page` lit le fichier existant, retire le frontmatter, calcule le sha256 du corps et court-circuite si le nouveau corps hache pareil — même si le nouveau frontmatter a un horodatage `generated_at` différent. C’est l’astuce clé qui garde les diffs git serrés à la reconstruction.
3. **La sortie de synthèse** porte un `content_hash: sha256-…` dans son frontmatter. Le hash du corps est calculé sans `generated_at`, si bien que des compilations répétées sur le même graphe produisent le même hash, et les nœuds `Synthesis` portent le même `content_hash` dans les métadonnées du graphe.
4. **Le rendu du site** efface `site/` au début de `write_site`, puis écrit de façon déterministe : les routes sont triées, les dictionnaires sérialisés avec `sort_keys=True`, `manifest.json` parcouru via `sorted(rglob("*"))`. Deux exécutions produisent des fichiers octet-identiques, manifest compris.
5. **Les dates des nœuds dérivent de la source.** Le `first_seen_at` d'un nœud vient du chemin sous lequel sa source a été ingérée, et non de l'horloge au moment de la compilation. Lire l'horloge ferait de chaque réexécution un diff, et c'est précisément pourquoi la version naïve de ce point ruine le point 1. La même règle garde la passe `Event` idempotente à l'octet près : chaque id, corps et date produits dérivent du contenu, vérifié sur un corpus de 481 sessions.

C’est vérifié par `tests/test_site_pages.py` et le smoke de bout en bout de `tests/test_project_e2e_redesign.py` (compiler deux fois, différencier les sites, attendre zéro delta de fichiers).

## Notes de passage à l’échelle

- **Plafond de nœuds de la vue graphe.** [`MAX_GRAPH_NODES = 1500`](../../tesserae/site/pages.py) borne le payload embarqué dans la page pour la disposition en force interactive. Au-delà d’environ 1500 nœuds, la simulation côté navigateur devient poussive sur du matériel de milieu de gamme, donc la page abandonne d’abord les nœuds de la couche wiki de plus faible degré quand le compte dépasse le plafond. Le `graph.json` exporté n’est pas affecté — il contient toujours le graphe complet. Les nœuds de code sont filtrés avant l’application du plafond.
- **Plafond de `llms-full.txt`.** Un plafond de sécurité de 5 Mo s’applique dans [`tesserae/site/exports.py`](../../tesserae/site/exports.py) ; le fichier se termine par un marqueur `[TRUNCATED — see graph.jsonld for the full set]` si le plafond est atteint. `graph.jsonld` est sans plafond parce que les consommateurs JSON-LD attendent l’ensemble complet.
- **Index de recherche.** Types de la couche wiki uniquement. Les nœuds du graphe de code n’entrent jamais dans `search-index.json` ; la cible de la refonte est < 500 Ko pour le corpus dogfood et nous sommes bien en dessous aujourd’hui.
- **Budget d’octets par page (règle empirique).** Chaque page de détail < 60 Ko de HTML gz, CSS partagé < 30 Ko, JS partagé < 25 Ko, le vendor sigma sur la page graphe uniquement (~60 Ko). La vue graphe utilise 3D-force-graph + Three.js chargés une fois ; toutes les autres pages restent vanilla.
- **Temps de compilation sur le dogfood.** ~300 fichiers markdown s’extraient en moins de 5 s sur une machine de dev récente ; le rendu du site ajoute environ 2 s. L’idempotence de la couche wiki signifie que les compilations suivantes ne touchent que les chemins modifiés.

## Surface d’interaction du frontend

- **Palette de recherche** — `cmd+k` / `ctrl+k` / `/`. Correspondance floue sur `search-index.json`, restreinte aux types wiki. Pages récentes persistées dans `localStorage`.
- **Bascule de thème** — bouton en haut à droite ; `data-theme="dark"` est stocké dans `localStorage` et appliqué avant le paint pour éviter le flash.
- **TOC droite collante** — desktop uniquement ; se replie en tiroir `<details>` sur mobile. Générée depuis les `<h2>` / `<h3>` du corps de la page.
- **Heatmap d’activité** — SVG sur 26 semaines avec étiquettes de mois + jours de semaine. Les cellules lient vers la page source `digest.md` du jour quand elle existe. (Les pages de détail de timeline par jour — `/timeline/<YYYY-MM-DD>.html` — sont un suivi explicite ; l’avis inline dans `render_timeline` le signale. ⚠ en cours.)
- **Vue graphe** — `/graph/`. Disposition en force 3D (3d-force-graph + Three.js) avec infobulles au survol, étiquettes d’arêtes, zoom ancré au curseur, et une vue 2D de repli. Les couleurs des nœuds viennent de `ResearchNodeType`.
- **Coquille mobile** — rail en tiroir, nav basse, typographie fluide, cibles tactiles sûres (≥ 44 px).

## Stratégie de test

- **Unitaire** — `tests/test_wiki_store.py`, `tests/test_synthesis.py`, `tests/test_site_components.py`, `tests/test_site_pages.py`, `tests/test_site_exports.py`, `tests/test_relevance.py`.
- **Colonne vertébrale du moteur** — `tests/test_pipeline.py`, `tests/test_refresh_pipeline.py`, `tests/test_daemon_core.py`, `tests/test_daemon_sources.py`, `tests/test_cli_engine.py`.
- **Mémoire d’auto-amélioration** — `tests/test_memory_sidecar.py`, `tests/test_decay_supersede.py`, `tests/test_supersede_suppression.py`, `tests/test_mcp_supersede_suppression.py`, `tests/test_memory_contradiction_reinforce.py`.
- **Récupération + embeddings** — `tests/test_hybrid_search.py`, `tests/test_ppr.py`, `tests/test_real_embeddings_phase6.py`.
- **Compilateur de contexte** — `tests/test_context_compiler.py` (forme, intégrité des citations, déterminisme, budget, repli PPR), `tests/test_cli_context.py`, `tests/test_mcp_server_context.py`.
- **Compilation incrémentale (expérimental)** — `tests/test_incremental_compile.py`, `tests/test_incremental_parity.py`, `tests/test_provenance_readiness.py`, `tests/test_sqlite_provenance.py`.
- **Idempotence** — `tests/test_project_e2e_redesign.py` compile deux fois et vérifie zéro diff dans `wiki/` et `site/`.
- **Intégrité des liens** — `tests/test_frontend.py` parse chaque HTML émis pour les hrefs et vérifie que chaque lien interne se résout vers un fichier généré. Aucun `nodes/codeclass-*.html` n’est produit.
- **Siblings IA** — pour chaque `path/foo.html`, la suite de tests vérifie que `path/foo.txt` et `path/foo.json` existent ; le JSON parse et contient `{title, kind, body, links}`.
- **Pas de Playwright** — pytest vanilla sous `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`.

## Docs associées

- [Démarrage rapide](quickstart.fr.md) — chemin minimal de `project init` à un site navigable.
- [Visite guidée de la refonte du frontend](frontend-redesign.fr.md) — visite annotée de chaque route.
- [Carte des fonctionnalités](feature-map.fr.md) — ce qui est livré, ce qui est en cours, avec pointeurs de fichiers.
- [Démo self-dogfood](self-dogfood.fr.md) — faire tourner Tesserae contre son propre dépôt.
