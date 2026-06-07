# Architecture

<!-- translations:start -->
<p align="center"><a href="../architecture.md">English</a> · <a href="architecture.ko.md">한국어</a> · <a href="architecture.zh.md">中文</a> · <a href="architecture.ja.md">日本語</a> · <a href="architecture.ru.md">Русский</a> · <a href="architecture.es.md">Español</a> · <a href="architecture.fr.md">Français</a> · <a href="architecture.de.md">Deutsch</a></p>
<!-- translations:end -->
Tesserae est un **moteur de contexte**. Il reconstruit une base de connaissances auto-améliorante à partir de votre projet et la remet aux agents sous forme de contexte prêt à l’emploi. Il repose sur trois piliers : (1) **surveillance des sessions** — observer les sessions d’agents/de travail en direct et capturer les découvertes au fil de l’eau ; (2) **ingestion de connaissances autonome et proactive** — un pipeline + une boucle superviseur tirent et réextraient le savoir en continu, améliorant la base au lieu d’attendre des instructions ; (3) **docs/contexte à la demande** — des artefacts demandés par l’utilisateur, compilés depuis cette même base. Le graphe typé, le coffre (vault) markdown et le site statique sont des *projections* de la base de connaissances ; le moteur est la boucle qui les garde à jour et alimente les agents.

En dessous, Tesserae transforme un répertoire de matériaux sources en graphe de connaissances contrôlé et typé, puis projette ce graphe à travers une couche wiki markdown durable vers un site web statique adapté à l’IA. La refonte d’avril 2026 a réorganisé le côté projections autour d’un modèle Karpathy à trois couches : les preuves brutes restent brutes, un graphe typé gouverne l’ontologie, et une couche wiki markdown se place entre le graphe et toute sortie rendue. Le site statique est un *moteur de rendu* de cette couche wiki, plutôt qu’un dump direct du graphe, avec l’ontologie contrôlée dans [`tesserae/research_graph.py`](../../tesserae/research_graph.py) comme schéma. Le jalon **v0.5.0** (juin 2026) a ajouté la colonne vertébrale du moteur qui anime les trois piliers — voir *Colonne vertébrale du moteur* et *Compilateur de contexte à la demande* ci-dessous.

## Le modèle Karpathy à trois couches

Le cadrage d’Andrej Karpathy pour les bases de connaissances adaptées aux LLM distingue trois couches, chacune avec sa propre garantie de durabilité :

| Couche | Préoccupation | Emplacement dans le dépôt | Propriétaire |
|---|---|---|---|
| L1 — Sources brutes | Les octets littéraux que l’utilisateur a rédigés ou collectés. Append-only. | `data/`, `docs/`, arbres de projet référencés dans `.tesserae/config.json` | l’utilisateur |
| L2 — Wiki | Pages markdown typées (sources, concepts, entities, papers, repos, topics, syntheses, questions) avec YAML frontmatter. Idempotent : régénéré à chaque compilation, mais réécrit uniquement lorsque les hashes de contenu changent. | `.tesserae/wiki/` | `WikiPageStore`, `WikiLayerProjector`, `SynthesisProjector` |
| L3 — Rendu | Le site HTML statique, les exports AI-sibling, l’index de recherche, les sitemaps, JSON-LD. Effacé et réécrit à chaque compilation, mais stable octet pour octet entre les relances. | `.tesserae/site/` | `StaticSiteBuilder` (`tesserae/site/`) |

Le schéma traverse les trois couches comme un axe séparé : `ResearchGraph` dans `graph.json` est l’ontologie contrôlée vers laquelle pointent les pages L2, et `ResearchNodeType` / la whitelist des arêtes dans [`tesserae/research_graph.py`](../../tesserae/research_graph.py) est la source de vérité pour les types qui existent.

La refonte a ajouté explicitement L2. Avant avril 2026, le site statique était projeté directement depuis `graph.json`; la couche wiki n’existait qu’à l’intérieur de l’export Obsidian vault. La séparer nous a donné :

- Une surface unique modifiable par un humain (ouvrez `.tesserae/wiki/` dans Obsidian ou n’importe quel éditeur markdown).
- Des reconstructions idempotentes : relancer `project compile` produit zéro diff de fichier sauf si le contenu source a changé.
- Un journal d’évolution : les pages de synthèse s’accumulent au fil du temps et permettent au projet de se raconter lui-même.

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

Chaque étape est incrémentale. L’extracteur de graphe utilise les hashes de contenu de `manifest.json` pour ignorer les fichiers sources inchangés. `WikiPageStore.write_page` renvoie `False` (et saute l’écriture) lorsque le hash du corps correspond à ce qui est déjà sur disque. `StaticSiteBuilder` efface et réécrit `.tesserae/site/`, mais sa sortie est déterministe — voir « Récit d’idempotence » ci-dessous.

## Flux de données du compilateur de contexte

Le compilateur de contexte à la demande ([`tesserae/context_compiler.py`](../../tesserae/context_compiler.py)) est le chemin phare du Pilier 3. À partir d’une requête et/ou d’ids de nœuds-graines explicites, `compile_context` construit un paquet markdown sur mesure et **cité** directement depuis le graphe et le renvoie en mémoire — il n’écrit rien sous `.tesserae/`.

```
query / seeds
     │
     ▼  1. Résolution des graines
        graines explicites (conservées seulement si présentes dans le graphe) + résultats de hybrid_search(), dédoublonnés, ordre stable
     │
     ▼  2. Expansion PPR
        retrieval.ppr.personalized_pagerank classe le voisinage à k sauts à profondeur bornée ;
        résultat vide (graines déconnectées) → repli sur l’ordre des graines (le paquet n’est jamais vide)
     │
     ▼  3. Sélection bornée par le budget
        parcourt l’ordre PPR, en incluant le corps cité de chaque nœud jusqu’à ce que le corps
        suivant dépasse `budget` caractères (budget <= 0 = sans limite ; marqueur de dépassement sur une frontière de mot)
     │
     ▼  4. Assemblage du markdown cité
        une section par nœud sélectionné + un bloc final `## Citations`.
        Le corps privilégie la page wiki projetée (quand un store et un type wiki public existent),
        sinon la description du nœud, sinon un stub minimal. Le corps sans LLM n’intègre aucun
        horodatage d’horloge → identique octet pour octet pour le même (graph, query, seeds, depth, budget).
     │
     ▼  5. Synthèse LLM optionnelle  (seulement si synthesize=true ET ANTHROPIC_API_KEY est présent)
     ▼
   ContextBundle { query, seeds_used, ranked_nodes, selected_nodes,
                   citations[ContextCitation], body, synthesized,
                   char_budget_used, char_budget_total }
```

Valeurs par défaut : `depth=2`, `budget=32000`. L’assemblage déterministe (étapes 1–4) est le contrat ; la synthèse LLM est purement additive. Le même pipeline soutient la commande CLI `project context`, l’outil MCP `compile_context` et les tranches d’export par sujet (`slice_export_context_for_topic`, `llms.txt` par sujet).

## Carte des modules

### Wiki + synthèse (L2)

| Module | Responsabilité |
|---|---|
| [`tesserae/wiki_store.py`](../../tesserae/wiki_store.py) | Dataclass `WikiPage`, `WikiPageStore` pour les I/O du système de fichiers. Parseur YAML-subset frontmatter uniquement stdlib. Idempotence par hash du corps. |
| [`tesserae/wiki_projector.py`](../../tesserae/wiki_projector.py) | `WikiLayerProjector` : mappe chaque nœud `ResearchGraph` de type wiki-layer vers une page markdown dans le bon dossier `kind/`. |
| [`tesserae/synthesis.py`](../../tesserae/synthesis.py) | `SynthesisProjector` : modèles déterministes pour pulse, daily_digest, weekly, topic, comparison, field_overview. Ajoute les nœuds `Synthesis` et les arêtes `synthesizes` / `summarizes` dans le graphe. |

### Graphe + ontologie

| Module | Responsabilité |
|---|---|
| [`tesserae/research_graph.py`](../../tesserae/research_graph.py) | Enum `ResearchNodeType` (incl. `SYNTHESIS`), whitelist des types d’arêtes (incl. `synthesizes`, `summarizes`), validation. |
| [`tesserae/canonicalization.py`](../../tesserae/canonicalization.py) | Canonicalisation des alias + file de revue des quasi-doublons. |
| [`tesserae/code_graph.py`](../../tesserae/code_graph.py) | Extracteur Python AST déterministe pour la tranche développement. |
| [`tesserae/llm_extractor.py`](../../tesserae/llm_extractor.py) | Extracteur sélectif Claude CLI/OAuth. |

### Rendu du site (L3)

| Module | Responsabilité |
|---|---|
| [`tesserae/site/__init__.py`](../../tesserae/site/__init__.py) | `StaticSiteBuilder.write_site` : efface + reconstruit le site, parcourt toutes les routes, émet les exports + AI siblings + manifest. |
| [`tesserae/site/pages.py`](../../tesserae/site/pages.py) | Un moteur de rendu par route (home, indexes, detail pages, timeline, graph, about). `SiteContext` transporte des index précalculés pour que les renderers restent purs. |
| [`tesserae/site/components.py`](../../tesserae/site/components.py) | Primitives HTML : `breadcrumbs`, `card`, `badge`, `node_table`, `edge_list`, `sparkline_svg`, `heatmap_svg`, `toc`, `page_shell`, `ai_siblings_footer`. |
| [`tesserae/site/tokens.py`](../../tesserae/site/tokens.py) | Design tokens — variables CSS, thèmes clair + sombre, layout, typographie; tous les composants sont stylés ici. |
| [`tesserae/site/js.py`](../../tesserae/site/js.py) | Bundle JS client : search palette, theme toggle, sigma + 3D-force graph view. |
| [`tesserae/site/markdown.py`](../../tesserae/site/markdown.py) | Renderer markdown uniquement stdlib (links, autolinks, code, emphasis, headings). Aucune dépendance externe. |
| [`tesserae/site/relevance.py`](../../tesserae/site/relevance.py) | Scoring de pertinence à quatre signaux (direct link, source overlap, Adamic-Adar, type affinity), utilisé par chaque section `Related`. |
| [`tesserae/site/search.py`](../../tesserae/site/search.py) | Constructeur de `search-index.json`. Wiki-layer kinds uniquement. |
| [`tesserae/site/sessions.py`](../../tesserae/site/sessions.py) | Renderers d’index/détail de sessions pour l’historique harness importé : sections project-memory summary, rail des tours de conversation, rendu de markdown transcript et blocs tool-use repliés. |
| [`tesserae/site/exports.py`](../../tesserae/site/exports.py) | `llms.txt`, `llms-full.txt`, `graph.jsonld`, `sitemap.xml`, `rss.xml`, `robots.txt`, `ai-readme.md`, siblings `.txt`/`.json` par page. |

### Orchestration du pipeline

| Module | Responsabilité |
|---|---|
| [`tesserae/project.py`](../../tesserae/project.py) | `ProjectWiki.compile` : pilote extraction → graph → passes mémoire → wiki layer → site. Possède `ProjectPaths` (`config`, `graph`, `manifest`, `wiki`, `site`, etc.). Décide en amont si une compilation incrémentale fondée sur la provenance est éligible (contrôlée par `incremental_compile`, OFF par défaut). |
| [`tesserae/cli.py`](../../tesserae/cli.py) | Dispatch CLI à verbes plats (~2 732 lignes après la suppression des groupes de sous-commandes hérités `project`/`wiki`). Les verbes — `init`, `compile`, `context`, `ask`, `refresh`, `serve`, `engine`, `export`, `vault`, `code`, `lab`, `config`, `projects`, `integrations` — sont déclarés comme métadonnées dans [`tesserae/cli_tree.py`](../../tesserae/cli_tree.py) et câblés à partir de cet arbre plutôt qu'enregistrés à la main. |
| [`tesserae/deploy.py`](../../tesserae/deploy.py) | `export site --deploy` : pousse `.tesserae/site/` vers une branche `gh-pages` via worktree, active éventuellement Pages via `gh`. |

### Colonne vertébrale du moteur (v0.5.0 — piliers 1 & 2)

La colonne vertébrale du moteur est la boucle en-processus qui anime la surveillance des sessions et la réingestion autonome. Le même `Pipeline.run()` est l’unique chemin de rafraîchissement qu’appellent la CLI, le démon superviseur et (plus tard) le serveur MCP.

| Module | Responsabilité |
|---|---|
| [`tesserae/engine/pipeline.py`](../../tesserae/engine/pipeline.py) | `Pipeline` : exécuteur d’étapes séquentiel. Codifie la chaîne de rafraîchissement en prose (ingestion → compilation → projection/publication) comme un objet importable qui renvoie un `List[StepResult]` structuré au lieu d’imprimer-et-quitter, afin que chaque appelant décide comment présenter les résultats. `run()` attrape `Exception` par étape (laisse passer `KeyboardInterrupt`/`SystemExit`) et s’arrête au premier échec. |
| [`tesserae/engine/daemon.py`](../../tesserae/engine/daemon.py) | `Daemon` : superviseur asyncio à propriétaire unique. Surveille les répertoires sources, le coffre Obsidian et le répertoire des sessions du harness ; via un debounce annuler-et-replanifier, fusionne une rafale de `TriggerEvent` en exactement un `Pipeline.run()`. Réutilise les observateurs existants `watch.py` / `vault_watch.py` (sans les réécrire), écrit un pidfile et survit aux exceptions en vol. Exposé via `engine` (`--interval`, `--debounce`, `--once`). |
| [`tesserae/watch.py`](../../tesserae/watch.py), [`tesserae/vault_watch.py`](../../tesserae/vault_watch.py) | Observateurs par sondage réutilisés par la commande autonome `export site --watch` et par les voies sources/coffre du démon. |

### Mémoire d’auto-amélioration (v0.5.0 — pilier 2)

La Phase 5 a activé l’auto-amélioration persistante. L’état mutable par nœud vit dans un sidecar `node_memory` SQLite (à l’intérieur de `.tesserae/sqlite.db`), séparé de l’horodatage immuable de première apparition `node_provenance.first_seen_at` (sidecar de la Phase 4). La compilation exécute un ensemble de passes déterministes sur le graphe.

| Module | Responsabilité |
|---|---|
| [`tesserae/memory/store.py`](../../tesserae/memory/store.py) | `NodeMemoryRow` + accesseurs indépendants du store (`read_memory`, `write_memory`, `bump_access`) sur la table `node_memory` — `decay_score`, `last_accessed_at`, `confidence`, `superseded`. Aucun site d’appel n’intègre de SQL brut. |
| [`tesserae/memory/decay.py`](../../tesserae/memory/decay.py) | `compute_decay_score` : score de fraîcheur à la Ebbinghaus (plus récent + plus consulté en premier) servant à classer les découvertes de session. |
| [`tesserae/memory/supersede.py`](../../tesserae/memory/supersede.py) | `run_supersede_pass` (**activé par défaut**) : verdict déterministe qui marque un insight quasi-doublon plus ancien comme remplacé par un plus récent, en ajoutant une arête `supersedes`. |
| [`tesserae/memory/insight_symbol_link.py`](../../tesserae/memory/insight_symbol_link.py) | `run_insight_symbol_link_pass` : relie les insights de session aux symboles de code qu’ils évoquent via des arêtes `discusses`. |
| [`tesserae/memory/reinforce.py`](../../tesserae/memory/reinforce.py), [`tesserae/memory/contradiction.py`](../../tesserae/memory/contradiction.py) | Aides de renforcement d’accès et de détection de contradictions sur le même sidecar. |

La confiance de récurrence est numérique en sortie : la projection temporelle tamponne la `confidence` de chaque fait depuis `NodeMemoryRow.confidence` (texte dans SQLite, exposé via `temporal.py`), ne se rabattant sur `infer_confidence` que lorsqu’aucune valeur stockée n’existe.

### Récupération (v0.5.0 — piliers 2 & 3)

| Module | Responsabilité |
|---|---|
| [`tesserae/retrieval/hybrid.py`](../../tesserae/retrieval/hybrid.py) | `hybrid_search` : récupérateur hybride local-first fusionnant trois voies — Okapi BM25 (k1=1.5, b=0.75), correspondance lexicale/style FTS de sous-chaînes insensible à la casse, et une voie d’embeddings enfichable — via la fusion par rangs réciproques (RRF, k=60). Entièrement déterministe. |
| [`tesserae/retrieval/ppr.py`](../../tesserae/retrieval/ppr.py) | `personalized_pagerank` : PageRank personnalisé à la HippoRAG-2 (arXiv:2502.14802) sur le graphe pour l’expansion multi-saut des graines — fait remonter des nœuds bien connectés à plusieurs sauts de la graine, pas seulement le voisinage à 1 saut. |
| Backend d’embeddings (Phase 6, Track B) | Le backend par défaut de la voie d’embeddings de l’hybride est un pseudo-embedding déterministe par seaux de hachage qui ne nécessite aucune dépendance supplémentaire ; `sentence-transformers` (`all-MiniLM-L6-v2`) est préféré et chargé paresseusement quand la dépendance optionnelle est installée. L’outil MCP `embedding_status` indique le backend actif. |

### Compilateur de contexte à la demande (v0.5.0 — phare du Pilier 3)

| Module | Responsabilité |
|---|---|
| [`tesserae/context_compiler.py`](../../tesserae/context_compiler.py) | `compile_context` : la fonctionnalité phare du Pilier 3. Compile un paquet de contexte **cité** sur mesure pour un ensemble de requêtes/graines directement depuis le graphe — voir *Flux de données du compilateur de contexte* ci-dessous. Renvoie un `ContextBundle` en mémoire (avec des `ContextCitation`) ; n’écrit rien sur disque. Exposé via la commande CLI `project context` et l’outil MCP `compile_context`. |

### Ports de persistance + stores de graphe

| Module | Responsabilité |
|---|---|
| [`tesserae/ports/graph_store.py`](../../tesserae/ports/graph_store.py) | Protocole `GraphStore` : `upsert_node`/`upsert_edge`, `get_node`, `iterate_nodes`, `query_subgraph`, `find_canonical` et la surface de suppression de la Phase 4 — `delete_node` et `delete_nodes_by_source` (supprime les nœuds dont l’ensemble de provenance devient vide après retrait des chemins sources donnés, de sorte que les concepts inter-fichiers survivent). |
| [`tesserae/graph_stores/sqlite.py`](../../tesserae/graph_stores/sqlite.py) | `SqliteGraphStore` : store de stockage autonome ; possède les tables sidecar `node_provenance` et `node_memory`. |
| [`tesserae/graph_stores/url_resolver.py`](../../tesserae/graph_stores/url_resolver.py) | Résout une URL de store (`sqlite:///…`, `hypepaper-postgres://…`) vers le bon `GraphStore`, permettant au serveur MCP de pointer vers n’importe quel store de stockage à l’exécution. |

### Adaptateurs externes (inchangés dans ce cycle)

| Module | Responsabilité |
|---|---|
| [`tesserae/obsidian_adapter.py`](../../tesserae/obsidian_adapter.py) | Projection Obsidian vault (coloration du graphe, Dataview dashboard, raw assets). |
| [`tesserae/agent_harness.py`](../../tesserae/agent_harness.py) | Exports harness Claude Code / Codex / Gemini / Kiro / Cursor / OpenCode. |
| [`tesserae/harness_sessions.py`](../../tesserae/harness_sessions.py) | Découverte des sessions entrantes Claude Code/Codex, normalisation, stockage sous `.tesserae/harness_sessions/`, et résumés markdown expurgés. |
| [`tesserae/graphiti_adapter.py`](../../tesserae/graphiti_adapter.py) | Temporal-fact JSONL + synchronisation live Graphiti optionnelle. |
| [`tesserae/cognee_adapter.py`](../../tesserae/cognee_adapter.py) | Bundle JSONL de nœuds/arêtes Cognee et chemin direct add/cognify. |
| [`tesserae/mcp_server.py`](../../tesserae/mcp_server.py) | Serveur MCP stdio. Récupération/graphe : `schema`, `graph_summary`, `search_nodes`, `node_context` (avec `use_ppr`), `search_facts`, `timeline`, `graph_ppr`, `wiki_page`, `raw_source`, `lint_report`. Moteur de contexte (v0.5.0) : `compile_context` (le compilateur de contexte à la demande), `embedding_status`, `fresh_insights` (découvertes de session classées par déclin), `list_communities`, `find_session_findings`, `find_code_symbol_mentions`. Plus `ask`, les outils du registre multi-projets (`list_projects`, `register_project`, `activate_project`, `unregister_project`, `list_sessions`) et `tesserae_setup_plan` / `tesserae_setup_apply`. |

## Layout du workspace projet

```text
.tesserae/
  config.json                 project name, source kind, source list
  graph.json                  validated ResearchGraph (incl. Synthesis nodes)
  manifest.json               per-source content hashes (input dedup)
  sqlite.db                   SQLite graph store; possède aussi les tables sidecar node_provenance
                              (première apparition, Phase 4) et node_memory (déclin / confiance /
                              remplacé, Phase 5)
  temporal_facts.jsonl        Graphiti-style temporal projection (confiance de récurrence numérique)
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

Chaque fichier est modifiable à la main ; la prochaine compilation respecte les modifications utilisateur tant que le hash du corps diffère de ce que le projector écrirait. (Modifier seulement le corps gagne ; modifier le frontmatter perd à la compilation suivante parce que le frontmatter est régénéré.) Les utilisateurs d’Obsidian peuvent ouvrir `.tesserae/wiki/` directement ; l’adaptateur `obsidian_vault/` existant est une projection séparée, pas un substitut.

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

## Ce qui est volontairement exclu

La refonte a tracé une ligne explicite : les nœuds code-class et code-function restent dans `graph.json` (donc les consommateurs MCP, Cognee et Graphiti les voient toujours), mais ils n’obtiennent jamais de pages HTML, n’apparaissent jamais dans `search-index.json` et n’apparaissent jamais dans la navigation. C’est le contrat côté utilisateur : le wiki est une base de connaissances orientée documents, pas un navigateur de fonctions.

Concrètement, `StaticSiteBuilder` ignore tout nœud dont le type n’est pas dans la map des types wiki L2 (`tesserae/wiki_projector.py::_KIND_FOR_TYPE`) :

- Exclus de L2 + L3 : `CodeClass`, `CodeFunction`, `CodeModule`, `Dependency`, `EvidenceSpan`, `SourceFile`, toutes les variantes `Claim` (`Claim`, `ContributionClaim`, `PerformanceClaim`, `ComparisonClaim`, `LimitationClaim`, `CausalClaim`).
- Surfaces où ils apparaissent encore : comme bullets, badges, neighbor counts ou evidence excerpts inline dans les pages wiki liées, et dans `graph.json` pour le downstream tooling.

Si vous avez besoin de navigation au niveau code, pointez un outil LSP / call-graph directement vers l’arbre source — c’est un problème différent de « wiki de ce que ce projet sait ».

## Récit d’idempotence

La refonte vise une **sortie identique octet pour octet sur deux exécutions consécutives de `project compile` avec des entrées inchangées**. Les éléments :

1. **L’extraction des sources** utilise les hashes de contenu de `manifest.json` ; les fichiers inchangés sont ignorés, donc le graphe reste stable.
2. **Les écritures de la couche wiki** sont idempotentes au niveau du corps. `WikiPageStore.write_page` lit le fichier existant, retire le frontmatter, calcule le sha256 du corps, et court-circuite si le nouveau corps a le même hash — même si le nouveau frontmatter a un timestamp `generated_at` différent. C’est l’astuce clé qui garde les git diffs serrés lors des reconstructions.
3. **La sortie de synthèse** porte `content_hash: sha256-…` dans son frontmatter. Le hash du corps est calculé sans `generated_at`, donc des compilations répétées sur le même graphe produisent le même hash, et les nœuds `Synthesis` portent le même `content_hash` dans les métadonnées du graphe.
4. **Le rendu du site** efface `site/` au début de `write_site`, puis écrit de façon déterministe : les routes sont triées, les dictionnaires dumpés avec `sort_keys=True`, `manifest.json` parcouru via `sorted(rglob("*"))`. Deux exécutions produisent des fichiers identiques octet pour octet, y compris le manifest.

Ceci est vérifié par `tests/test_site_pages.py` et le smoke end-to-end dans `tests/test_project_e2e_redesign.py` (compiler deux fois, diff des sites, attendre zéro delta de fichier).

## Notes de mise à l’échelle

- **Limite de nœuds de la vue graphe.** [`MAX_GRAPH_NODES = 1500`](../../tesserae/site/pages.py) borne le payload embarqué dans la page pour le layout de force interactif. Au-delà d’environ 1500 nœuds, la simulation côté navigateur devient lente sur du matériel moyen ; la page retire donc d’abord les nœuds wiki-layer de plus faible degré lorsque le nombre dépasse la limite. Le `graph.json` exporté n’est pas affecté — il contient toujours le graphe complet. Les code nodes sont filtrés avant l’application de la limite.
- **Limite de `llms-full.txt`.** Une limite de sécurité de 5 MB s’applique dans [`tesserae/site/exports.py`](../../tesserae/site/exports.py) ; si la limite est atteinte, le fichier se termine par le marqueur `[TRUNCATED — see graph.jsonld for the full set]`. `graph.jsonld` n’est pas limité parce que les consommateurs JSON-LD attendent l’ensemble complet.
- **Index de recherche.** Wiki-layer kinds uniquement. Les code-graph nodes n’entrent jamais dans `search-index.json` ; l’objectif de refonte est < 500 KB pour le corpus dogfood et nous sommes largement en dessous aujourd’hui.
- **Budget d’octets par page (règle empirique).** Chaque detail page < 60 KB gz HTML, shared CSS < 30 KB, shared JS < 25 KB, sigma vendor seulement sur la graph page (~60 KB). La graph view utilise 3D-force-graph + Three.js chargés une seule fois ; toutes les autres pages restent vanilla.
- **Temps de compilation sur dogfood.** ~300 fichiers markdown s’extraient en moins de 5 s sur une machine de dev récente ; le rendu du site ajoute encore ~2 s. L’idempotence de la couche wiki signifie que les compilations suivantes ne touchent que les chemins modifiés.

## Surface d’interaction frontend

- **Search palette** — `cmd+k` / `ctrl+k` / `/`. Fuzzy match sur `search-index.json`, limité aux wiki kinds. Les pages récentes sont persistées dans `localStorage`.
- **Theme toggle** — bouton en haut à droite ; `data-theme="dark"` est stocké dans `localStorage` et appliqué avant le paint pour éviter le flash.
- **Sticky right TOC** — desktop uniquement ; se replie en drawer `<details>` sur mobile. Généré depuis les `<h2>` / `<h3>` dans le corps de page.
- **Activity heatmap** — SVG de 26 semaines avec labels month + weekday. Les cellules pointent vers la source page `digest.md` du jour quand elle existe. (Per-day timeline detail pages — `/timeline/<YYYY-MM-DD>.html` — est un follow-up explicite ; la notice inline dans `render_timeline` le signale. ⚠ in-progress.)
- **Graph view** — `/graph/`. 3D force layout (3d-force-graph + Three.js) avec hover tooltips, edge labels, zoom ancré au curseur et 2D fallback view. Les couleurs de nœuds viennent de `ResearchNodeType`.
- **Mobile shell** — drawer rail, bottom nav, fluid type, touch-safe hit targets (≥ 44 px).

## Stratégie de test

- **Unit** — `tests/test_wiki_store.py`, `tests/test_synthesis.py`, `tests/test_site_components.py`, `tests/test_site_pages.py`, `tests/test_site_exports.py`, `tests/test_relevance.py`.
- **Colonne vertébrale du moteur** — `tests/test_pipeline.py`, `tests/test_refresh_pipeline.py`, `tests/test_daemon_core.py`, `tests/test_daemon_sources.py`, `tests/test_cli_engine.py`.
- **Mémoire d’auto-amélioration** — `tests/test_memory_sidecar.py`, `tests/test_decay_supersede.py`, `tests/test_supersede_suppression.py`, `tests/test_mcp_supersede_suppression.py`, `tests/test_memory_contradiction_reinforce.py`.
- **Récupération + embeddings** — `tests/test_hybrid_search.py`, `tests/test_ppr.py`, `tests/test_real_embeddings_phase6.py`.
- **Compilateur de contexte** — `tests/test_context_compiler.py` (forme, intégrité des citations, déterminisme, budget, repli PPR), `tests/test_cli_context.py`, `tests/test_mcp_server_context.py`.
- **Compilation incrémentale (expérimentale)** — `tests/test_incremental_compile.py`, `tests/test_incremental_parity.py`, `tests/test_provenance_readiness.py`, `tests/test_sqlite_provenance.py`.
- **Idempotence** — `tests/test_project_e2e_redesign.py` compile deux fois et affirme zéro diff dans `wiki/` et `site/`.
- **Intégrité des liens** — `tests/test_frontend.py` parse chaque HTML émis pour les hrefs et affirme que chaque lien interne se résout vers un fichier généré. Aucun `nodes/codeclass-*.html` n’est produit.
- **AI siblings** — pour chaque `path/foo.html`, la suite de tests affirme que `path/foo.txt` et `path/foo.json` existent ; le JSON se parse et contient `{title, kind, body, links}`.
- **Pas de Playwright** — pytest vanilla sous `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`.

## Documents liés

- [Démarrage rapide](quickstart.fr.md) — chemin minimal de `project init` à un site navigable.
- [Parcours de la refonte frontend](frontend-redesign.fr.md) — visite annotée de chaque route.
- [Carte des fonctionnalités](feature-map.fr.md) — ce qui est livré, ce qui est in-progress, avec pointeurs de fichiers.
- [Démo self-dogfood](self-dogfood.fr.md) — exécuter Tesserae sur son propre dépôt.
