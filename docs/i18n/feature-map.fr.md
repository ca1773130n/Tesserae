# Carte des fonctionnalités

<!-- translations:start -->
<p align="center"><a href="../feature-map.md">English</a> · <a href="feature-map.ko.md">한국어</a> · <a href="feature-map.zh.md">中文</a> · <a href="feature-map.ja.md">日本語</a> · <a href="feature-map.ru.md">Русский</a> · <a href="feature-map.es.md">Español</a> · <a href="feature-map.fr.md">Français</a> · <a href="feature-map.de.md">Deutsch</a></p>
<!-- translations:end -->
Ce document résume les fonctionnalités actuellement implémentées dans Tesserae, avec leur état, leurs fichiers source et l’endroit où elles sont documentées.

Tesserae est un **moteur de contexte** reposant sur trois piliers : (1) surveillance des sessions, (2) ingestion de connaissances autonome et proactive, et (3) docs/contexte à la demande. Le graphe typé, le coffre (vault) et le site statique sont des projections de la base de connaissances. Les fonctionnalités ci-dessous sont regroupées selon le pilier qu’elles servent ; le jalon **v0.5.0** (juin 2026) a livré la colonne vertébrale du moteur et la fonctionnalité phare du Pilier 3, le compilateur de contexte à la demande.

Légende d’état : ✅ livré · ⚠ en cours / partiel.

## Moteur de contexte — v0.5.0 (juin 2026)

La colonne vertébrale du moteur qui anime les trois piliers. Voir [`docs/architecture.md`](architecture.fr.md) pour la carte des modules de la colonne vertébrale du moteur, le sidecar de mémoire d’auto-amélioration et le flux de données du compilateur de contexte.

### Colonne vertébrale du moteur (piliers 1 & 2)

| Fonctionnalité | État | Source | Notes |
|---|---|---|---|
| `Pipeline` — chaîne de rafraîchissement réutilisable renvoyant `List[StepResult]` | ✅ | [`tesserae/engine/pipeline.py`](../../tesserae/engine/pipeline.py) | Un seul exécuteur d’étapes appelé par la CLI, le démon et MCP. Attrape `Exception` par étape ; s’arrête au premier échec. |
| `Daemon` — superviseur asyncio à propriétaire unique | ✅ | [`tesserae/engine/daemon.py`](../../tesserae/engine/daemon.py) | Surveille sources + coffre + répertoire des sessions du harness ; un debounce annuler-et-replanifier fusionne une rafale en un `Pipeline.run()`. pidfile ; survit aux exceptions en vol. |
| `engine` | ✅ | [`tesserae/cli.py`](../../tesserae/cli.py) | `--interval`, `--debounce`, `--once`. `daemon` est un alias de `engine`. |
| `project refresh` — chaîne en prose (ingestion → compilation → projection) | ✅ | `cli.py` + [`tesserae/project.py`](../../tesserae/project.py) | `--changed-only` (incrémental optionnel), `--skip-sessions`. |
| Surveillance des sessions en direct → découvertes | ✅ | `harness_sessions.py` + modules du graphe de session | Les sessions importées alimentent le graphe ; `fresh_insights` / `find_session_findings` les font remonter. |

### Mémoire d’auto-amélioration (pilier 2)

| Fonctionnalité | État | Source | Notes |
|---|---|---|---|
| sidecar `node_memory` SQLite (déclin / confiance / remplacé) | ✅ | [`tesserae/memory/store.py`](../../tesserae/memory/store.py) | `NodeMemoryRow` + accesseurs indépendants du store ; état mutable uniquement. La première apparition vit dans le sidecar `node_provenance` séparé. |
| Score de déclin Ebbinghaus | ✅ | [`tesserae/memory/decay.py`](../../tesserae/memory/decay.py) | Classe les découvertes de session par plus récent + plus consulté (anime `fresh_insights`). |
| Passe de remplacement (**activée par défaut**) | ✅ | [`tesserae/memory/supersede.py`](../../tesserae/memory/supersede.py) | Verdict déterministe marque un insight quasi-doublon plus ancien comme remplacé par un plus récent ; ajoute une arête `supersedes`. |
| Lien insight → symbole de code | ✅ | [`tesserae/memory/insight_symbol_link.py`](../../tesserae/memory/insight_symbol_link.py) | Arêtes `discusses` des insights de session vers les symboles référencés. |
| Passes de renforcement + contradiction | ✅ | [`tesserae/memory/reinforce.py`](../../tesserae/memory/reinforce.py), [`tesserae/memory/contradiction.py`](../../tesserae/memory/contradiction.py) | Renforcement d’accès + détection de contradictions sur le même sidecar. |
| Confiance de récurrence numérique en sortie | ✅ | [`tesserae/temporal.py`](../../tesserae/temporal.py) | Les faits temporels tamponnent `confidence` depuis `NodeMemoryRow.confidence`, sinon repli sur `infer_confidence`. |

### Récupération + embeddings (piliers 2 & 3)

| Fonctionnalité | État | Source | Notes |
|---|---|---|---|
| Récupérateur hybride (BM25 + lexical + embeddings, RRF k=60) | ✅ | [`tesserae/retrieval/hybrid.py`](../../tesserae/retrieval/hybrid.py) | Local-first, entièrement déterministe. |
| PageRank personnalisé (HippoRAG-2) | ✅ | [`tesserae/retrieval/ppr.py`](../../tesserae/retrieval/ppr.py) | Expansion multi-saut des graines ; sous-graphe à profondeur bornée. |
| Vrais embeddings par défaut (Track B, Phase 6) | ✅ | `retrieval/hybrid.py` | Par défaut = pseudo-embedding déterministe par seaux de hachage (sans dépendances) ; `sentence-transformers` (`all-MiniLM-L6-v2`) préféré à l’installation, chargé paresseusement. L’outil MCP `embedding_status` indique le backend actif. |

### Compilateur de contexte à la demande (Pilier 3 — phare)

| Fonctionnalité | État | Source | Notes |
|---|---|---|---|
| `compile_context` — `ContextBundle` en mémoire avec citations | ✅ | [`tesserae/context_compiler.py`](../../tesserae/context_compiler.py) | Résolution des graines → expansion PPR → sélection par budget → markdown cité → synthèse LLM optionnelle. Déterministe sauf `synthesize=true`. N’écrit rien sur disque. |
| CLI `project context` | ✅ | `cli.py` | `[query]`, `--seeds`, `--depth` (2), `--budget` (32000 ; ≤0 = sans limite), `--synthesize`, `--output`. |
| Outil MCP `compile_context` | ✅ | [`tesserae/mcp_server.py`](../../tesserae/mcp_server.py) | Le même pipeline via MCP ; `budget=0` = sans limite. |
| Tranches d’export par sujet | ✅ | [`tesserae/site/exports.py`](../../tesserae/site/exports.py) `slice_export_context_for_topic` | `llms.txt` par sujet + `render_harness_context` via `compile_context`. |

### Compilation incrémentale (Phase 4 — expérimentale)

| Fonctionnalité | État | Source | Notes |
|---|---|---|---|
| sidecar de provenance (`node_provenance`, première apparition) | ✅ | [`tesserae/graph_stores/sqlite.py`](../../tesserae/graph_stores/sqlite.py) | Base des suppressions « changed-only » ; toujours enregistré. |
| Surface de suppression de `GraphStore` | ✅ | [`tesserae/ports/graph_store.py`](../../tesserae/ports/graph_store.py) | `delete_node`, `delete_nodes_by_source` (supprime les nœuds dont l’ensemble de provenance se vide ; les concepts inter-fichiers survivent). |
| Aiguillage de store à l’exécution `url_resolver` | ✅ | [`tesserae/graph_stores/url_resolver.py`](../../tesserae/graph_stores/url_resolver.py) | `sqlite:///…` / `hypepaper-postgres://…` → `GraphStore`. |
| Flag `incremental_compile` | ⚠ | [`tesserae/project.py`](../../tesserae/project.py) | **OFF par défaut / expérimental.** Parité d’octets prouvée pour plusieurs formes d’édition, mais des lacunes multi-propriétaires/cycle de vie des producteurs subsistent ; la compilation complète reste la valeur par défaut. |

## Refonte du frontend — avril 2026

Un wiki hiérarchique centré sur les documents remplace l’ancien dump de graphe. Voir [`docs/frontend-redesign.md`](frontend-redesign.fr.md) pour le parcours route par route et [`docs/architecture.md`](architecture.fr.md) pour le modèle en trois couches.

### Couche wiki (L2 markdown)

| Fonctionnalité | État | Source | Ancre de documentation |
|---|---|---|---|
| `WikiPageStore` (écritures body-hash idempotentes, parseur frontmatter) | ✅ | [`tesserae/wiki_store.py`](../../tesserae/wiki_store.py) | [architecture.md § Carte des modules](architecture.fr.md#wiki--synthesis-l2) |
| `WikiLayerProjector` — une page md par nœud de couche wiki | ✅ | [`tesserae/wiki_projector.py`](../../tesserae/wiki_projector.py) | [architecture.md § Pipeline](architecture.fr.md#pipeline) |
| Pages `sources/` | ✅ | `wiki_projector.py` | [frontend-redesign.md § Sources](frontend-redesign.fr.md#sources) |
| Pages `concepts/` | ✅ | `wiki_projector.py` | [frontend-redesign.md § Concepts](frontend-redesign.fr.md#concepts) |
| Pages `entities/` | ✅ | `wiki_projector.py` | [frontend-redesign.md § Entities](frontend-redesign.fr.md#entities) |
| Pages `papers/` | ✅ | `wiki_projector.py` | [frontend-redesign.md § Papers](frontend-redesign.fr.md#papers) |
| Pages `repos/` | ✅ | `wiki_projector.py` | [frontend-redesign.md § Repos](frontend-redesign.fr.md#repos) |
| Pages `topics/` | ✅ | `wiki_projector.py` | [frontend-redesign.md § Topics](frontend-redesign.fr.md#topics) |
| Pages `questions/` (questions ouvertes) | ✅ | `wiki_projector.py` | [frontend-redesign.md § Questions](frontend-redesign.fr.md#questions) |
| Pages `syntheses/` | ✅ | [`tesserae/synthesis.py`](../../tesserae/synthesis.py) | [frontend-redesign.md § Syntheses](frontend-redesign.fr.md#syntheses) |

### Types de synthèse (L2 → dérivé)

`SynthesisProjector` produit sept modèles déterministes et ajoute des nœuds `Synthesis` ainsi que des arêtes `synthesizes` / `summarizes` dans le graphe.

| Type | État | Source | Notes |
|---|---|---|---|
| `pulse` (un global, alimente `/`) | ✅ | `synthesis.py` | Reconstruit à chaque compile. |
| `daily_digest` | ✅ | `synthesis.py` | Un par `data/research/daily/<date>/`. |
| `weekly` | ✅ | `synthesis.py` | Un par `data/research/weekly/<iso-week>/`. |
| `topic` | ✅ | `synthesis.py` | Un par cluster `ResearchTopic` / `ApproachFamily` contenant ≥ 3 papers. |
| `comparison` | ✅ | `synthesis.py` | Un par paire d’`ApproachFamily` en concurrence sur la même tâche. |
| `field_overview` | ✅ | `synthesis.py` | Un par `ResearchField`. |
| Résumés améliorés par LLM (activés par variable d’environnement) | ⚠ | hook uniquement | La base heuristique est livrée ; le hook `TESSERAE_SYNTHESIS_LLM=1` reste un stub. |

### Routes du site statique

| Route | État | Source | Notes |
|---|---|---|---|
| `/` (accueil, hero pulse) | ✅ | [`tesserae/site/pages.py`](../../tesserae/site/pages.py) `render_home` | Ligne de statistiques + points d’entrée sélectionnés + activité récente. |
| `/sources/`, `/sources/<slug>.html` | ✅ | `pages.py::render_sources_index`, `render_source_detail` | |
| `/concepts/`, `/concepts/<slug>.html` | ✅ | `pages.py::render_concepts_index`, `render_concept_detail` | |
| `/entities/`, `/entities/<slug>.html` | ✅ | `pages.py::render_entities_index`, `render_entity_detail` | |
| `/papers/`, `/papers/<slug>.html` | ✅ | `pages.py::render_papers_index`, `render_paper_detail` | |
| `/repos/`, `/repos/<slug>.html` | ✅ | `pages.py::render_repos_index`, `render_repo_detail` | |
| `/topics/`, `/topics/<slug>.html` | ✅ | `pages.py::render_topics_index`, `render_topic_detail` | |
| `/syntheses/`, `/syntheses/<slug>.html` | ✅ | `pages.py::render_syntheses_index`, `render_synthesis_detail` | |
| `/questions/`, `/questions/<slug>.html` | ✅ | `pages.py::render_questions_index`, `render_question_detail` | |
| `/timeline/` | ✅ | `pages.py::render_timeline` | Carte thermique + liste de jours + rail de synthèse. |
| `/timeline/<YYYY-MM-DD>.html` (détail par jour) | ⚠ | n/a pour l’instant | Les cellules de la carte thermique pointent provisoirement vers la page source `digest.md` du jour. Subagent P connecte les pages de détail quotidiennes via `StaticSiteBuilder`. |
| `/graph/` (2D + 3D interactif) | ✅ | `pages.py::render_graph_view` + `js.py` | 3d-force-graph + Three.js, infobulles au survol, libellés d’arêtes, zoom ancré au curseur. |
| `/about.html` | ✅ | `pages.py::render_about` | Schéma, informations de build. |

### Exports adaptés à l’IA

| Artefact | État | Source | Objectif |
|---|---|---|---|
| Fichier frère `<page>.txt` par page | ✅ | [`tesserae/site/exports.py`](../../tesserae/site/exports.py) `write_siblings` | Vue texte brut d’une page (sans navigation ni style). |
| Fichier frère `<page>.json` par page | ✅ | `exports.py::write_siblings` | `{title, kind, body, body_text, links, source_path, frontmatter}`. |
| `llms.txt` | ✅ | `exports.py::render_llms_txt` | Index court llmstxt.org. |
| `llms-full.txt` | ✅ | `exports.py::render_llms_full_txt` | Corps de toutes les pages, plafonné à 5 MB. |
| `graph.jsonld` | ✅ | `exports.py::render_graph_jsonld` | `Dataset` schema.org, nœuds de couche wiki uniquement. |
| `graph.json` | ✅ | `__init__.py::write_site` | Payload complet du graphe (incl. nœuds de code pour l’outillage). |
| `search-index.json` | ✅ | [`tesserae/site/search.py`](../../tesserae/site/search.py) | Palette + recherche de pages ; types de couche wiki uniquement. |
| `sitemap.xml` | ✅ | `exports.py::render_sitemap_xml` | Toutes les routes émises, `lastmod` depuis le frontmatter. |
| `rss.xml` | ✅ | `exports.py::render_rss_xml` | Les 30 dernières syntheses. |
| `robots.txt` | ✅ | `exports.py::render_robots_txt` | Permissif — crawl + indexation. |
| `ai-readme.md` | ✅ | `exports.py::render_ai_readme` | Carte du site lisible par machine. |
| `manifest.json` | ✅ | `__init__.py::_manifest` | sha256 + taille de chaque fichier émis (harnais d’idempotence). |

### Design visuel + UX

| Fonctionnalité | État | Source | Notes |
|---|---|---|---|
| Tokens de design (thèmes clair + sombre, accent terre cuite) | ✅ | [`tesserae/site/tokens.py`](../../tesserae/site/tokens.py) | Un bundle CSS dans `assets/style.css`. |
| Bascule de thème (persistée, sans flash) | ✅ | [`tesserae/site/js.py`](../../tesserae/site/js.py) | `data-theme="dark"` dans `localStorage`, appliqué avant le rendu. |
| Palette de recherche (`cmd+k` / `ctrl+k` / `/`) | ✅ | `js.py` | Correspondance floue sur `search-index.json` ; liste des pages récentes. |
| TOC droit fixe | ✅ | `pages.py` + `tokens.py` | Bureau uniquement ; tiroir mobile via `<details>`. |
| Carte thermique d’activité avec libellés mois + jours de semaine | ✅ | `components.py::heatmap_svg` | SVG 26 semaines, cellules liées au `digest.md` du jour. |
| Sparkline (par concept/entité) | ✅ | `components.py::sparkline_svg` | Comptes de mentions hebdomadaires, 12 dernières semaines. |
| Shell mobile (rail de tiroir, navigation basse, type fluide) | ✅ | `tokens.py` + `pages.py` | Cibles tactiles ≥ 44 px. |
| Transitions de page (opacité 120 ms, prefers-reduced-motion) | ✅ | `tokens.py` | |
| Vue graphe 3D + 2D (survol, libellés d’arêtes, zoom ancré au curseur) | ✅ | `pages.py::render_graph_view` + `js.py` | 3d-force-graph + Three.js, vendorié comme snapshot CDN. |
| Pied de page des fichiers frères IA | ✅ | `components.py::ai_siblings_footer` | Liens inline vers le `.txt` et le `.json` de la page courante. |
| Pages d’historique de sessions du harnais | ✅ | [`tesserae/harness_sessions.py`](../../tesserae/harness_sessions.py) + [`tesserae/site/sessions.py`](../../tesserae/site/sessions.py) | Import explicite Claude Code/Codex ; index `/sessions/` et pages détail avec tours markdown, rail de tours à gauche, utilisation d’outils repliée et entrées de recherche. |

### Pipeline + CLI

| Fonctionnalité | État | Source | Notes |
|---|---|---|---|
| `project compile` appelle synthesis + wiki + site dans l’ordre | ✅ | [`tesserae/project.py`](../../tesserae/project.py) | Phase 3 du plan de refonte. |
| `project build-site` autonome | ✅ | `project.py` + [`tesserae/cli.py`](../../tesserae/cli.py) | Lit `wiki/` + `graph.json`, écrit `site/`. |
| `project serve` HTTP local | ✅ | `cli.py` | Serveur stdlib simple. |
| `export site --deploy` → GitHub Pages | ✅ | [`tesserae/deploy.py`](../../tesserae/deploy.py) | Push de worktree vers `gh-pages` ; `--enable-pages` optionnel via CLI `gh`. `--build`, `--dry-run`, `--branch`, `--remote`, `--force`. |
| `project sessions discover/import/list` | ✅ | [`tesserae/harness_sessions.py`](../../tesserae/harness_sessions.py) + `cli.py` | Historique de sessions entrant pour Claude Code/Codex ; découverte explicite et bornée au répertoire de travail du projet. |
| `export site --watch` rebuild-on-change | ✅ | [`tesserae/cli.py`](../../tesserae/cli.py) + [`tesserae/watch.py`](../../tesserae/watch.py) | Watcher par polling autonome : `--interval`, `--debounce`, `--once`, `--paths`, `--quiet`. Le superviseur multi-sources vit dans `project engine`/`daemon` (voir Moteur de contexte). |
| `project context` — compile un document de contexte cité | ✅ | `cli.py` + [`tesserae/context_compiler.py`](../../tesserae/context_compiler.py) | Phare du Pilier 3 ; voir la section Moteur de contexte. |
| `project refresh` / `engine` | ✅ | `cli.py` + [`tesserae/engine/`](../../tesserae/engine/) | Chaîne de rafraîchissement en prose + boucle superviseur ; voir la section Moteur de contexte. |

## Fonctionnalités préexistantes (conservées inchangées)

### CLI et installation

- ✅ Package Python installable via `pyproject.toml`.
- ✅ Commandes console : `tesserae`, `tesserae`, `tesserae_mcp`.
- ✅ `scripts/install.sh` pour installation `curl | bash`.
- ✅ Installations éditables par défaut pour un développement local rapide.

### Extraction

- ✅ Extracteur déterministe de notes de recherche avec vocabulaires contrôlés de nœuds/arêtes.
- ✅ Extracteur Claude CLI/OAuth pour une extraction structurée de meilleure qualité sans clés API.
- ✅ Routage Claude sélectif par glob et limite de budget.
- ✅ Extracteur déterministe de code de développement pour projets Python.
- ✅ Ingestion batch avec hachage de contenu et prise en charge de `--changed-only`.
- ✅ Lecture de sources tolérante à l’UTF-8 malformé.

### Gouvernance du graphe

- ✅ Liste `ResearchNodeType` contrôlée — inclut maintenant `SYNTHESIS`.
- ✅ Liste blanche contrôlée des types d’arêtes — inclut maintenant `synthesizes`, `summarizes`.
- ✅ Validation pour rejeter la dérive de schéma.
- ✅ Canonicalisation des alias.
- ✅ File de revue pour nœuds quasi dupliqués ambigus.
- ✅ Modèle de décisions de revue et workflow fusionner/garder séparé.
- ✅ Résumé des tendances du corpus depuis les graphes par fichier.

### Persistance et rapports

- ✅ Export Graph JSON.
- ✅ Magasin de graphe SQLite.
- ✅ Magasin de graphe Kuzu optionnel.
- ✅ Rapport de graphe avec comptes, couverture de preuves, nœuds orphelins, buckets de dates, nœuds riches en alias.
- ✅ Rapport concurrentiel décrivant les idées absorbées depuis MegaMem, Graphiti/Zep, MCP graph servers, agentic RAG.

### Workflow local de projet

- ✅ `tesserae init --bare`
- ✅ `tesserae compile <paths>`
- ✅ `tesserae compile`
- ✅ `tesserae projects mcp-config`
- ✅ `tesserae export site`
- ✅ `tesserae serve`
- ✅ `tesserae export site --deploy` (GitHub Pages)
- ✅ `tesserae sessions discover/import/list` (import explicite d’historique d’agent local)
- ✅ `tesserae export site --watch` (watcher par polling autonome)
- ✅ `tesserae engine` (boucle superviseur — v0.5.0)
- ✅ `tesserae refresh` (chaîne en prose ingestion → compilation → projection — v0.5.0)
- ✅ `tesserae context` (compilateur de contexte à la demande — v0.5.0)
- ✅ `tesserae export harness`
- ✅ `tesserae vault export`
- ✅ `tesserae export graphiti`
- ✅ `tesserae export graphiti --sync`

### Obsidian

- ✅ Export de vault prêt à ouvrir.
- ✅ `.obsidian/app.json` et paramètres de graphe.
- ✅ Projection Markdown.
- ✅ Structure `raw/assets/`.
- ✅ `_meta/dashboard.md` avec requête Dataview.

### Harnais d’agents

Fichiers cibles générés pour :

- ✅ Claude Code: `CLAUDE.md`, `.claude/settings.json`
- ✅ Codex: `AGENTS.md`, `mcp.toml`
- ✅ Gemini: `GEMINI.md`, `.gemini/settings.json`
- ✅ Kiro: steering et paramètres MCP
- ✅ Cursor: règles de projet et config MCP
- ✅ OpenCode: `AGENTS.md`, `opencode.json`

### Graphiti / faits temporels

- ✅ Projection de faits temporels avec champs provenance, currentness, confidence et invalidation.
- ✅ Export JSONL d’épisodes Graphiti sans dépendance.
- ✅ Smoke `sync-graphiti --dry-run` sans Graphiti installé.
- ✅ Sync live optionnelle avec `graphiti_core` et Neo4j.

### Cognee

- ✅ Bundle Cognee JSONL (`nodes.jsonl`, `edges.jsonl`, `manifest.json`).
- ✅ Import direct add-only optionnel.
- ✅ Adaptateur Cognee cognify optionnel basé sur Codex CLI/OAuth.
- ✅ Chemins d’adaptateurs d’embedding déterministe et Ollama pour workflows smoke/qualité sans clé API.

### Serveur MCP

- ✅ `tesserae_mcp` / `python3 -m tesserae.mcp_server` via stdio JSON-RPC.
- ✅ Outils de récupération/graphe : `schema`, `graph_summary`, `search_nodes`, `node_context` (avec `use_ppr`), `search_facts`, `timeline`, `graph_ppr`, `wiki_page`, `raw_source`, `lint_report`.
- ✅ Outils du moteur de contexte (v0.5.0) : `compile_context`, `embedding_status`, `fresh_insights` (classement par déclin), `list_communities`, `find_session_findings`, `find_code_symbol_mentions`, `ask`.
- ✅ Outils de configuration : `tesserae_setup_plan`, `tesserae_setup_apply`.
- ✅ Registre multiprojet : `list_projects`, `register_project`, `activate_project`, `unregister_project`, `list_sessions`. Aiguillage d’URL de store via `url_resolver`.

## Tests

La suite actuelle couvre :

- ✅ garde-fous d’ontologie (incl. nouveau nœud `Synthesis` + arêtes `synthesizes` / `summarizes`) ;
- ✅ extraction déterministe ;
- ✅ parsing/validation du wrapper Claude CLI ;
- ✅ routage Claude sélectif ;
- ✅ workflow canonicalisation/revue ;
- ✅ ingestion batch ;
- ✅ rapports ;
- ✅ persistance SQLite/Kuzu ;
- ✅ bundles/import patches Cognee ;
- ✅ export/sync Graphiti dry-run ;
- ✅ workflow CLI projet ;
- ✅ export de harnais d’agent ;
- ✅ export Obsidian ;
- ✅ génération frontend + intégrité des liens (pas de `nodes/codeclass-*.html`) ;
- ✅ idempotence du wiki store ;
- ✅ golden + idempotence du synthesis projector ;
- ✅ composants, pages, exports et pertinence du site ;
- ✅ forme des fichiers frères IA (`.txt` + `.json` par page) ;
- ✅ idempotence end-to-end de deux compilations ;
- ✅ colonne vertébrale du moteur : pipeline, chaîne de rafraîchissement, cœur du démon + sources, CLI `project engine` ;
- ✅ mémoire d’auto-amélioration : sidecar, déclin/remplacement, suppression du remplacement (y compris MCP), renforcement/contradiction ;
- ✅ récupération + embeddings : recherche hybride, PPR, vrais embeddings par défaut (Phase 6) ;
- ✅ compilateur de contexte : forme/intégrité des citations/déterminisme/budget/repli PPR, CLI `project context`, MCP `compile_context` ;
- ✅ compilation incrémentale (expérimentale) : différenciateur, portes de parité, préparation de provenance, provenance SQLite ;
- ✅ installation du package et contrat de l’installateur.
