# Carte des fonctionnalités

<!-- translations:start -->
<p align="center"><a href="../feature-map.md">English</a> · <a href="feature-map.ko.md">한국어</a> · <a href="feature-map.zh.md">中文</a> · <a href="feature-map.ja.md">日本語</a> · <a href="feature-map.ru.md">Русский</a> · <a href="feature-map.es.md">Español</a> · <a href="feature-map.fr.md">Français</a> · <a href="feature-map.de.md">Deutsch</a></p>
<!-- translations:end -->
Ce document résume les fonctionnalités actuellement implémentées dans Tesserae, avec statut, fichiers sources et où elles sont documentées.

Tesserae est un **moteur de contexte** reposant sur trois piliers : (1) la surveillance de sessions, (2) l’ingestion de connaissances autonome et proactive, et (3) les docs/contexte à la demande. Le graphe typé, le vault et le site statique sont des projections de la base de connaissances. Les fonctionnalités ci-dessous sont groupées selon le pilier qu’elles servent ; le jalon **v0.5.0** (juin 2026) a livré la colonne vertébrale du moteur et la fonctionnalité vedette du pilier 3, le compilateur de contexte à la demande.

Légende de statut : ✅ livré · ⚠ en cours / partiel.

> **Ordre de lecture.** Les sections ci-dessous sont des jalons, du plus récent
> au plus ancien. Les versions entre la v0.12.0 et la v0.28.7 ne sont pas
> reprises ici : leur détail version par version vit dans
> [`docs/release-notes/`](../release-notes/), qui fait foi comme journal des
> changements. Cette carte décrit la forme du système, pas chaque commit.

## Mémoire cognitive et périmètre — v0.29.0 → v0.30.0 (août 2026)

Le cycle qui a fait que le graphe *sait ce qui s'est passé*, et pas seulement ce
qui a été écrit : les résultats survivent à l'ingestion, une arête causale en est
dérivée, et les dégradations autrefois silencieuses se signalent.

| Fonctionnalité | État | Sources | Notes |
|---|---|---|---|
| Couche code optionnelle | ✅ | `cli.py`, [`tesserae/code_graph.py`](../../tesserae/code_graph.py) | `compile` n'ingère plus les symboles de code par défaut. Sur un gros dépôt, ils écrasaient tout le reste en nombre et évinçaient la recherche ; `tesserae code ingest` branche toujours CodeGraph délibérément. Voir [ingest](ingest.fr.md). |
| Surface de recherche dévoilée | ✅ | [`tesserae/mcp_server.py`](../../tesserae/mcp_server.py) | Les paramètres bitemporels et de sélection de vue étaient écrits et testés, mais inatteignables via MCP. `search_facts` accepte désormais `as_of` (répondre à une date passée) aux côtés de `current_only` — **refusés ensemble**, ce sont deux horloges — et signale `undated_included` pour dire combien des lignes renvoyées n'ont pas de date. |
| Dégradations sonores | ✅ | [`tesserae/lint.py`](../../tesserae/lint.py), [`tesserae/ingest/fetch.py`](../../tesserae/ingest/fetch.py), [`tesserae/ingest/orchestrator.py`](../../tesserae/ingest/orchestrator.py) | Trois échecs silencieux rendus explicites : une ingestion binaire qui n'a rien produit, la couverture d'intervalles non datée (`INTERVAL_COVERAGE`) et le contenu non textuel abandonné. Le silence se lisait comme un succès ; ce n'est plus le cas. |
| `first_seen_at` dérivé de la source | ✅ | [`tesserae/temporal.py`](../../tesserae/temporal.py), [`tesserae/session_graph.py`](../../tesserae/session_graph.py) | Un nœud est daté par le chemin sous lequel sa source a été ingérée, et non par l'horloge au moment de compiler — une réexécution le date donc à l'identique et l'idempotence à l'octet survit. |
| Pool procédural de recherche | ✅ | [`tesserae/context_compiler.py`](../../tesserae/context_compiler.py), [`tesserae/research_graph.py`](../../tesserae/research_graph.py) | `context` réserve une place à la mémoire procédurale — ce qui a été exécuté et ce qu'il en est advenu — **méritée par la provenance**, pas accordée par défaut. Le code de lint `PROCEDURAL_POOLS` signale quand la place ne peut être remplie honnêtement. |
| Un résultat d'outil est un tour | ✅ | [`tesserae/session_event.py`](../../tesserae/session_event.py), [`tesserae/harness_sessions.py`](../../tesserae/harness_sessions.py) | Les codes de sortie et indicateurs d'erreur survivent à l'ingestion et se posent sur les nœuds `Event`. Le graphe distingue une commande qui a échoué d'une commande qui a seulement tourné. Les répertoires personnels sont caviardés à l'entrée. |
| L'arête `recovers` | ✅ | [`tesserae/session_recovery.py`](../../tesserae/session_recovery.py) | La seule arête causale : « ceci a réussi après que cela a échoué », dérivée de deux résultats **observés** dans une même session, concordants sur l'outil, la famille de programme, le répertoire de travail et l'opérande. `CAUSAL_EDGE_TYPES` ne compte délibérément qu'un élément. Voir [historique des sessions](session-history.fr.md). |
| Structure de domaines par charte | ⚠ | [`tesserae/charter.py`](../../tesserae/charter.py), `cli.py` | La détection de communautés *propose* un vocabulaire de domaines ; la charte le *possède* entre deux réorganisations explicites, car la détection est déterministe mais pas stable (un seul document de 15 nœuds déplace ~29 % des membres). `tesserae domains status` la lit. **`compile` ne la produit pas encore** — d'ici là, la commande renvoie « no charter yet ». |
| Multi-hôtes sur disque partagé | ✅ | [`tesserae/harness_sessions.py`](../../tesserae/harness_sessions.py) | `TESSERAE_HOST_ID` restreint l'élagage et l'écrasement selon *qui a écrit un enregistrement*, de sorte que N serveurs sur un même disque cessent d'effacer mutuellement leur historique de sessions. Voir [historique des sessions](session-history.fr.md). |

## Inter-projets & UX — v0.11.0 (juin 2026)

| Fonctionnalité | Statut | Source | Notes |
|---|---|---|---|
| Fédération inter-projets | ✅ | [`tesserae/federation.py`](../../tesserae/federation.py) | `ask --scope federated` assemble UN graphe depuis plusieurs projets enregistrés — fusion d’identité (même arxiv/repo/hash/symbole) + liens `shares_concept_with` adossés aux embeddings avec opt-out — et retourne une seule réponse citée et croisée sur l’union (PPR + `compile_context`). Le `graph.json` par projet est en lecture seule ; déterministe en mode identité seule. |
| Routeur `ask` intelligent (pas de projet actif) | ✅ | [`tesserae/ask_router.py`](../../tesserae/ask_router.py) | Le concept de « projet actif » est supprimé — tous les projets enregistrés sont égaux. Un `ask` nu se route lui-même (nomme un projet → celui-là ; comparatif → fédéré ; relance → garde la route ; sinon repli fédéré), avec un départage LLM optionnel et une continuité par conversation. Les opérations par projet résolvent le projet depuis le cwd. |
| Inspection de la fédération | ✅ | `tesserae/federation.py`, `cli.py` | `tesserae federation status` (comptes de nœuds par projet, fusions d’identité, liens sémantiques) et `federation explain <node>` (pourquoi un nœud fait pont entre projets). |
| Serve multi-projets | ✅ | [`tesserae/serve.py`](../../tesserae/serve.py), `cli.py` | Un `tesserae serve` nu sert CHAQUE projet enregistré sous un même serveur (accueil à `/`, chacun à `/<alias>/`, un sélecteur Projects dans l’en-tête, chemins confinés) ; `--project X` en sert un seul avec le widget ask en direct. |
| Couche de concepts LLM dans `compile` | ✅ | `cli.py`, [`tesserae/llm_extractor.py`](../../tesserae/llm_extractor.py), [`tesserae/selective_extractor.py`](../../tesserae/selective_extractor.py) | `tesserae compile` construit la couche concepts/claims **par défaut** (`--extractor llm`) via le fournisseur configuré (codex/claude/api selon `llm_provider`) ; `--extractor deterministic` est l’opt-out structurel octet-stable ; `selective-llm --llm-include … --llm-limit N` est sensible au coût. |
| `tesserae setup` (interactif) | ✅ | `cli.py`, [`tesserae/deps.py`](../../tesserae/deps.py) | `tesserae setup` de niveau supérieur — interactif par défaut (fournisseur/effort LLM + quelles deps optionnelles) ; les drapeaux sautent les invites. Les installations fonctionnent dans les envs uv-tool sans pip (repli uv-pip). |

## Interop, recherche & setup — v0.10.0 (juin 2026)

| Fonctionnalité | Statut | Source | Notes |
|---|---|---|---|
| Import/export Google **OKF v0.1** | ✅ | [`tesserae/okf.py`](../../tesserae/okf.py) | `tesserae export okf [--import DIR]`. Bundle markdown + frontmatter YAML ; fait l’aller-retour sans perte des bundles propres de Tesserae via un espace de noms `x_tesserae`, les bundles étrangers au mieux. |
| Recherche rapide de transcriptions (memex) | ✅ | [`tesserae/memex_search.py`](../../tesserae/memex_search.py) | Index BM25 `nicosuave/memex` sur les transcriptions Claude/Codex, câblé au tableau de bord des sessions de `tesserae serve` via `GET /api/transcript-search`. Optionnel + gracieux en son absence. |
| Handles de discipline de lecture | ✅ | [`tesserae/mcp_server.py`](../../tesserae/mcp_server.py) | `compile_context` `preview=N` retourne un aperçu borné + un handle clé par contenu ; `get_handle` pagine le reste. Garde les énormes payloads hors du contexte de l’agent. |
| Signaux de qualité d’extraction | ✅ | [`tesserae/session_graph_llm.py`](../../tesserae/session_graph_llm.py) | `confidence` + `confidence_rationale` + `revisit_signals` par constat (octet-stable ; exposés dans `fresh_insights`). |
| Setup machine + deps | ✅ | [`tesserae/deps.py`](../../tesserae/deps.py), `cli.py` | `tesserae setup` écrit les défauts LLM globaux + installe les deps optionnelles (memex, raganything) ; `tesserae config deps` liste/installe ; `tesserae init` propose memex. La config par projet prime toujours. |

## Moteur de contexte — v0.5.0 (juin 2026)

La colonne vertébrale du moteur qui anime les trois piliers. Voir [`docs/architecture.md`](architecture.fr.md) pour la carte des modules du moteur, le sidecar de mémoire d’auto-amélioration et le flux de données du compilateur de contexte.

### Colonne vertébrale du moteur (piliers 1 & 2)

| Fonctionnalité | Statut | Source | Notes |
|---|---|---|---|
| `Pipeline` — chaîne de refresh réutilisable retournant `List[StepResult]` | ✅ | [`tesserae/engine/pipeline.py`](../../tesserae/engine/pipeline.py) | Un seul exécuteur d’étapes que la CLI, le daemon et MCP appellent tous. Attrape `Exception` par étape ; s’arrête au premier échec. |
| `Daemon` — superviseur asyncio à propriétaire unique | ✅ | [`tesserae/engine/daemon.py`](../../tesserae/engine/daemon.py) | Surveille sources + vault + répertoire de sessions de harness ; un debounce cancel-and-reschedule coalesce une rafale en un seul `Pipeline.run()`. Pidfile ; survit aux exceptions en vol. |
| `project engine` / `project daemon` | ✅ | [`tesserae/cli.py`](../../tesserae/cli.py) | `--interval`, `--debounce`, `--once`. `daemon` est un alias d’`engine`. |
| `project refresh` — chaîne en prose (ingest → compile → project) | ✅ | `cli.py` + [`tesserae/project.py`](../../tesserae/project.py) | `--changed-only` (incrémental opt-in), `--no-sessions`. |
| Moniteur de sessions en direct → constats | ✅ | `harness_sessions.py` + modules session-graph | Les sessions importées alimentent le graphe ; `fresh_insights` / `find_session_findings` les font remonter. |

### Mémoire d’auto-amélioration (pilier 2)

| Fonctionnalité | Statut | Source | Notes |
|---|---|---|---|
| Sidecar SQLite `node_memory` (décrue / confiance / supplanté) | ✅ | [`tesserae/memory/store.py`](../../tesserae/memory/store.py) | `NodeMemoryRow` + accesseurs agnostiques du store ; état mutable uniquement. Le first-seen vit dans le sidecar séparé `node_provenance`. |
| Score de décrue d’Ebbinghaus | ✅ | [`tesserae/memory/decay.py`](../../tesserae/memory/decay.py) | Classe les constats de session du plus récent + plus accédé d’abord (anime `fresh_insights`). |
| Passe de supplantation (**activée par défaut**) | ✅ | [`tesserae/memory/supersede.py`](../../tesserae/memory/supersede.py) | Un verdict déterministe marque un insight quasi-doublon plus ancien comme supplanté par un plus récent ; ajoute une arête `supersedes`. |
| Liaison insight → symbole de code | ✅ | [`tesserae/memory/insight_symbol_link.py`](../../tesserae/memory/insight_symbol_link.py) | Arêtes `discusses` des insights de session vers les symboles qu’ils référencent. |
| Passes de renforcement + contradiction | ✅ | [`tesserae/memory/reinforce.py`](../../tesserae/memory/reinforce.py), [`tesserae/memory/contradiction.py`](../../tesserae/memory/contradiction.py) | Renforcement d’accès + détection de contradictions sur le même sidecar. |
| Confiance de récurrence numérique en sortie | ✅ | [`tesserae/temporal.py`](../../tesserae/temporal.py) | Les faits temporels estampillent `confidence` depuis `NodeMemoryRow.confidence`, avec repli sur `infer_confidence`. |

### Récupération + embeddings (piliers 2 & 3)

| Fonctionnalité | Statut | Source | Notes |
|---|---|---|---|
| Récupérateur hybride (BM25 + lexical + embedding, RRF k=60) | ✅ | [`tesserae/retrieval/hybrid.py`](../../tesserae/retrieval/hybrid.py) | Local d’abord, entièrement déterministe. |
| Personalized PageRank (HippoRAG-2) | ✅ | [`tesserae/retrieval/ppr.py`](../../tesserae/retrieval/ppr.py) | Expansion multi-sauts des graines ; sous-graphe borné en profondeur. |
| Vrais embeddings par défaut (Track B, Phase 6) | ✅ | `retrieval/hybrid.py` | Défaut = pseudo-embedding hash-bucket déterministe (sans deps) ; `sentence-transformers` (`all-MiniLM-L6-v2`) préféré, chargé paresseusement quand installé. L’outil MCP `embedding_status` rapporte le backend actif. |

### Compilateur de contexte à la demande (pilier 3 — vedette)

| Fonctionnalité | Statut | Source | Notes |
|---|---|---|---|
| `compile_context` — `ContextBundle` cité en mémoire | ✅ | [`tesserae/context_compiler.py`](../../tesserae/context_compiler.py) | Résolution des graines → expansion PPR → sélection bornée par budget → markdown cité → synthèse LLM optionnelle. Déterministe sauf si `synthesize=true`. N’écrit rien sur disque. |
| CLI `project context` | ✅ | `cli.py` | `[query]`, `--seeds`, `--depth` (2), `--budget` (32000 ; ≤0 = sans plafond), `--llm`, `--output`. |
| Outil MCP `compile_context` | ✅ | [`tesserae/mcp_server.py`](../../tesserae/mcp_server.py) | Même pipeline via MCP ; `budget=0` est sans plafond. |
| Tranches d’export ciblées par topic | ✅ | [`tesserae/site/exports.py`](../../tesserae/site/exports.py) `slice_export_context_for_topic` | `llms.txt` ciblé par topic + `render_harness_context` via `compile_context`. |

### Compilation incrémentale (Phase 4 — expérimental)

| Fonctionnalité | Statut | Source | Notes |
|---|---|---|---|
| Sidecar de provenance (`node_provenance`, first-seen) | ✅ | [`tesserae/graph_stores/sqlite.py`](../../tesserae/graph_stores/sqlite.py) | Fondation des suppressions changed-only ; toujours enregistré. |
| Surface de suppression `GraphStore` | ✅ | [`tesserae/ports/graph_store.py`](../../tesserae/ports/graph_store.py) | `delete_node`, `delete_nodes_by_source` (supprime les nœuds dont l’ensemble de provenance se vide ; les concepts multi-fichiers survivent). |
| Dispatch de store runtime `url_resolver` | ✅ | [`tesserae/graph_stores/url_resolver.py`](../../tesserae/graph_stores/url_resolver.py) | `sqlite:///…` / `hypepaper-postgres://…` → `GraphStore`. |
| Drapeau `incremental_compile` | ⚠ | [`tesserae/project.py`](../../tesserae/project.py) | **OFF par défaut / expérimental.** Parité octet prouvée pour plusieurs formes d’édition mais des lacunes multi-propriétaires/cycle de vie producteur demeurent ; la compilation complète reste le défaut. |

## Refonte du frontend — avril 2026

Un wiki hiérarchique orienté documents remplace l’ancien déversement de graphe. Voir [`docs/frontend-redesign.md`](frontend-redesign.fr.md) pour la visite route par route et [`docs/architecture.md`](architecture.fr.md) pour le modèle à trois couches.

### Couche wiki (markdown L2)

| Fonctionnalité | Statut | Source | Ancre doc |
|---|---|---|---|
| `WikiPageStore` (écritures idempotentes par hash de corps, parseur de frontmatter) | ✅ | [`tesserae/wiki_store.py`](../../tesserae/wiki_store.py) | [architecture.md § Module map](architecture.fr.md#wiki--synthesis-l2) |
| `WikiLayerProjector` — une page md par nœud de la couche wiki | ✅ | [`tesserae/wiki_projector.py`](../../tesserae/wiki_projector.py) | [architecture.md § Pipeline](architecture.fr.md#pipeline) |
| Pages `sources/` | ✅ | `wiki_projector.py` | [frontend-redesign.md § Sources](frontend-redesign.fr.md#sources) |
| Pages `concepts/` | ✅ | `wiki_projector.py` | [frontend-redesign.md § Concepts](frontend-redesign.fr.md#concepts) |
| Pages `entities/` | ✅ | `wiki_projector.py` | [frontend-redesign.md § Entities](frontend-redesign.fr.md#entities) |
| Pages `papers/` | ✅ | `wiki_projector.py` | [frontend-redesign.md § Papers](frontend-redesign.fr.md#papers) |
| Pages `repos/` | ✅ | `wiki_projector.py` | [frontend-redesign.md § Repos](frontend-redesign.fr.md#repos) |
| Pages `topics/` | ✅ | `wiki_projector.py` | [frontend-redesign.md § Topics](frontend-redesign.fr.md#topics) |
| Pages `questions/` (questions ouvertes) | ✅ | `wiki_projector.py` | [frontend-redesign.md § Questions](frontend-redesign.fr.md#questions) |
| Pages `syntheses/` | ✅ | [`tesserae/synthesis.py`](../../tesserae/synthesis.py) | [frontend-redesign.md § Syntheses](frontend-redesign.fr.md#syntheses) |

### Types de synthèse (L2 → dérivé)

`SynthesisProjector` produit sept modèles déterministes et ajoute des nœuds `Synthesis` + des arêtes `synthesizes` / `summarizes` en retour dans le graphe.

| Type | Statut | Source | Notes |
|---|---|---|---|
| `pulse` (un global, anime `/`) | ✅ | `synthesis.py` | Reconstruit à chaque compilation. |
| `daily_digest` | ✅ | `synthesis.py` | Un par `data/research/daily/<date>/`. |
| `weekly` | ✅ | `synthesis.py` | Un par `data/research/weekly/<iso-week>/`. |
| `topic` | ✅ | `synthesis.py` | Un par cluster `ResearchTopic` / `ApproachFamily` ≥ 3 papers. |
| `comparison` | ✅ | `synthesis.py` | Un par paire d’`ApproachFamily` en concurrence sur la même tâche. |
| `field_overview` | ✅ | `synthesis.py` | Un par `ResearchField`. |
| Résumés améliorés par LLM (drapeau env) | ⚠ | hook seulement | La ligne de base heuristique est livrée ; le hook `TESSERAE_SYNTHESIS_LLM=1` reste un stub. |

### Routes du site statique

| Route | Statut | Source | Notes |
|---|---|---|---|
| `/` (accueil, hero pulse) | ✅ | [`tesserae/site/pages.py`](../../tesserae/site/pages.py) `render_home` | Rangée de stats + points d’entrée choisis + activité récente. |
| `/sources/`, `/sources/<slug>.html` | ✅ | `pages.py::render_sources_index`, `render_source_detail` | |
| `/concepts/`, `/concepts/<slug>.html` | ✅ | `pages.py::render_concepts_index`, `render_concept_detail` | |
| `/entities/`, `/entities/<slug>.html` | ✅ | `pages.py::render_entities_index`, `render_entity_detail` | |
| `/papers/`, `/papers/<slug>.html` | ✅ | `pages.py::render_papers_index`, `render_paper_detail` | |
| `/repos/`, `/repos/<slug>.html` | ✅ | `pages.py::render_repos_index`, `render_repo_detail` | |
| `/topics/`, `/topics/<slug>.html` | ✅ | `pages.py::render_topics_index`, `render_topic_detail` | |
| `/syntheses/`, `/syntheses/<slug>.html` | ✅ | `pages.py::render_syntheses_index`, `render_synthesis_detail` | |
| `/questions/`, `/questions/<slug>.html` | ✅ | `pages.py::render_questions_index`, `render_question_detail` | |
| `/timeline/` | ✅ | `pages.py::render_timeline` | Heatmap + liste des jours + rail de synthèses. |
| `/timeline/<YYYY-MM-DD>.html` (détail par jour) | ⚠ | pas encore | Les cellules de la heatmap lient vers la page source `digest.md` du jour en intérim. Le sous-agent P câble les pages de détail par jour via `StaticSiteBuilder`. |
| `/graph/` (2D + 3D interactif) | ✅ | `pages.py::render_graph_view` + `js.py` | 3d-force-graph + Three.js, infobulles au survol, étiquettes d’arêtes, zoom ancré au curseur. |
| `/about.html` | ✅ | `pages.py::render_about` | Schéma, infos de build. |

### Exports adaptés aux IA

| Artefact | Statut | Source | Objet |
|---|---|---|---|
| Sibling `<page>.txt` par page | ✅ | [`tesserae/site/exports.py`](../../tesserae/site/exports.py) `write_siblings` | Vue texte brut d’une page (sans nav, sans style). |
| Sibling `<page>.json` par page | ✅ | `exports.py::write_siblings` | `{title, kind, body, body_text, links, source_path, frontmatter}`. |
| `llms.txt` | ✅ | `exports.py::render_llms_txt` | Index court llmstxt.org. |
| `llms-full.txt` | ✅ | `exports.py::render_llms_full_txt` | Le corps de chaque page, plafonné à 5 Mo. |
| `graph.jsonld` | ✅ | `exports.py::render_graph_jsonld` | `Dataset` schema.org, nœuds de la couche wiki uniquement. |
| `graph.json` | ✅ | `__init__.py::write_site` | Payload complet du graphe (y compris les nœuds de code pour l’outillage). |
| `search-index.json` | ✅ | [`tesserae/site/search.py`](../../tesserae/site/search.py) | Palette + recherche de pages ; types de la couche wiki uniquement. |
| `sitemap.xml` | ✅ | `exports.py::render_sitemap_xml` | Chaque route émise, `lastmod` depuis le frontmatter. |
| `rss.xml` | ✅ | `exports.py::render_rss_xml` | Les 30 dernières synthèses. |
| `robots.txt` | ✅ | `exports.py::render_robots_txt` | Permissif — crawl + index. |
| `ai-readme.md` | ✅ | `exports.py::render_ai_readme` | Plan du site lisible par machine. |
| `manifest.json` | ✅ | `__init__.py::_manifest` | sha256 + taille pour chaque fichier émis (harnais d’idempotence). |

### Design visuel + UX

| Fonctionnalité | Statut | Source | Notes |
|---|---|---|---|
| Design tokens (thèmes clair + sombre, accent terracotta) | ✅ | [`tesserae/site/tokens.py`](../../tesserae/site/tokens.py) | Un seul bundle CSS dans `assets/style.css`. |
| Bascule de thème (persistée, sans flash) | ✅ | [`tesserae/site/js.py`](../../tesserae/site/js.py) | `data-theme="dark"` dans `localStorage`, appliqué avant le paint. |
| Palette de recherche (`cmd+k` / `ctrl+k` / `/`) | ✅ | `js.py` | Correspondance floue sur `search-index.json` ; liste des pages récentes. |
| TOC droite collante | ✅ | `pages.py` + `tokens.py` | Desktop uniquement ; tiroir mobile via `<details>`. |
| Heatmap d’activité avec étiquettes de mois + jours de semaine | ✅ | `components.py::heatmap_svg` | SVG sur 26 semaines, les cellules lient vers le `digest.md` du jour. |
| Sparkline (par concept/entité) | ✅ | `components.py::sparkline_svg` | Comptes de mentions hebdomadaires, 12 dernières semaines. |
| Coquille mobile (rail en tiroir, nav basse, typographie fluide) | ✅ | `tokens.py` + `pages.py` | Cibles tactiles ≥ 44 px. |
| Transitions de page (opacité 120 ms, prefers-reduced-motion) | ✅ | `tokens.py` | |
| Vue graphe 3D + 2D (survol, étiquettes d’arêtes, zoom ancré au curseur) | ✅ | `pages.py::render_graph_view` + `js.py` | 3d-force-graph + Three.js, vendoré comme snapshot CDN. |
| Pied de page des siblings IA par page | ✅ | `components.py::ai_siblings_footer` | Liens inline vers le `.txt` et le `.json` de la page courante. |
| Pages d’historique des sessions de harness | ✅ | [`tesserae/harness_sessions.py`](../../tesserae/harness_sessions.py) + [`tesserae/site/sessions.py`](../../tesserae/site/sessions.py) | Import Claude Code/Codex explicite ; index `/sessions/` et pages de détail avec tours en markdown, rail de tours à gauche, usage d’outil replié et entrées de recherche. |

### Pipeline + CLI

| Fonctionnalité | Statut | Source | Notes |
|---|---|---|---|
| `project compile` appelle synthèse + wiki + site dans l’ordre | ✅ | [`tesserae/project.py`](../../tesserae/project.py) | Phase 3 du plan de refonte. |
| `project build-site` autonome | ✅ | `project.py` + [`tesserae/cli.py`](../../tesserae/cli.py) | Lit `wiki/` + `graph.json`, écrit `site/`. |
| `project serve` HTTP local | ✅ | `cli.py` | Serveur stdlib simple. |
| `project deploy` → GitHub Pages | ✅ | [`tesserae/deploy.py`](../../tesserae/deploy.py) | Push par worktree vers `gh-pages` ; `--enable-pages` optionnel via la CLI `gh`. `--build`, `--dry-run`, `--branch`, `--remote`, `--force`. |
| `project sessions discover/import/list` | ✅ | [`tesserae/harness_sessions.py`](../../tesserae/harness_sessions.py) + `cli.py` | Historique de sessions entrant pour Claude Code/Codex ; la découverte est explicite et limitée au répertoire de travail du projet. |
| `project watch` reconstruction au changement | ✅ | [`tesserae/cli.py`](../../tesserae/cli.py) + [`tesserae/watch.py`](../../tesserae/watch.py) | Watcher autonome par sondage : `--interval`, `--debounce`, `--once`, `--paths`, `--quiet`. Le superviseur multi-sources vit sous `project engine`/`daemon` (voir Moteur de contexte). |
| `project context` — compiler un doc de contexte cité | ✅ | `cli.py` + [`tesserae/context_compiler.py`](../../tesserae/context_compiler.py) | Vedette du pilier 3 ; voir la section Moteur de contexte. |
| `project refresh` / `project engine` / `project daemon` | ✅ | `cli.py` + [`tesserae/engine/`](../../tesserae/engine/) | Chaîne de refresh en prose + boucle superviseur ; voir la section Moteur de contexte. |

## Fonctionnalités préexistantes (reportées telles quelles)

### CLI et installation

- ✅ Paquet Python installable via `pyproject.toml`.
- ✅ Commandes console : `tesserae`, `tesserae`, `tesserae_mcp`.
- ✅ `scripts/install.sh` pour l’installation `curl | bash`.
- ✅ Installations éditables par défaut pour un développement local rapide.

### Extraction

- ✅ Extracteur déterministe de notes de recherche avec vocabulaires contrôlés de nœuds/arêtes.
- ✅ Extracteur Claude CLI/OAuth pour une extraction structurée de meilleure qualité sans clés API.
- ✅ Routage Claude sélectif par glob et limite de budget.
- ✅ Extracteur déterministe de code de développement pour les projets Python.
- ✅ Ingestion par lot avec hachage de contenu et prise en charge de `--changed-only`.
- ✅ Lecture de sources tolérante à l’UTF-8 malformé.

### Gouvernance du graphe

- ✅ Liste `ResearchNodeType` contrôlée — inclut désormais `SYNTHESIS`.
- ✅ Liste blanche de types d’arêtes contrôlée — inclut désormais `synthesizes`, `summarizes`.
- ✅ Validation pour rejeter la dérive de schéma.
- ✅ Canonicalisation des alias.
- ✅ File de revue pour les nœuds quasi-doublons ambigus.
- ✅ Modèle de décisions de revue et workflow fusionner/garder-séparé.
- ✅ Synthèse des tendances du corpus depuis les graphes par fichier.

### Persistance et rapports

- ✅ Export JSON du graphe.
- ✅ Store de graphe SQLite.
- ✅ Store de graphe Kuzu optionnel.
- ✅ Rapport de graphe avec comptes, couverture de preuve, nœuds orphelins, buckets de dates, nœuds riches en alias.
- ✅ Rapport concurrentiel décrivant les idées absorbées de MegaMem, Graphiti/Zep, serveurs de graphe MCP, RAG agentique.

### Workflow local au projet

- ✅ `tesserae init --bare`
- ✅ `tesserae compile <paths>`
- ✅ `tesserae compile`
- ✅ `tesserae projects mcp-config`
- ✅ `tesserae export site`
- ✅ `tesserae serve`
- ✅ `tesserae export site --deploy` (GitHub Pages)
- ✅ `tesserae sessions discover/import/list` (import explicite d’historique d’agent local)
- ✅ `tesserae export site --watch` (watcher autonome par sondage)
- ✅ `tesserae engine` (boucle superviseur — v0.5.0)
- ✅ `tesserae refresh` (chaîne en prose ingest → compile → project — v0.5.0)
- ✅ `tesserae context` (compilateur de contexte à la demande — v0.5.0)
- ✅ `tesserae export harness`
- ✅ `tesserae vault export`
- ✅ `tesserae export graphiti`
- ✅ `tesserae export graphiti --sync`

### Obsidian

- ✅ Export de vault prêt à ouvrir.
- ✅ `.obsidian/app.json` et réglages du graphe.
- ✅ Projection markdown.
- ✅ Structure `raw/assets/`.
- ✅ `_meta/dashboard.md` avec requête Dataview.

### Harness d’agents

Fichiers cibles générés pour :

- ✅ Claude Code : `CLAUDE.md`, `.claude/settings.json`
- ✅ Codex : `AGENTS.md`, `mcp.toml`
- ✅ Gemini : `GEMINI.md`, `.gemini/settings.json`
- ✅ Kiro : steering et réglages MCP
- ✅ Cursor : règles de projet et config MCP
- ✅ OpenCode : `AGENTS.md`, `opencode.json`

### Graphiti / faits temporels

- ✅ Projection de faits temporels avec provenance, actualité, confiance et champs d’invalidation.
- ✅ Export JSONL d’épisodes Graphiti sans dépendance.
- ✅ Test à blanc `sync-graphiti --dry-run` sans Graphiti installé.
- ✅ Sync en direct optionnelle avec `graphiti_core` et Neo4j.

### Serveur MCP

- ✅ `tesserae_mcp` / `python3 -m tesserae.mcp_server` en JSON-RPC sur stdio.
- ✅ Outils de récupération/graphe : `schema`, `graph_summary`, `search_nodes`, `node_context` (avec `use_ppr`), `search_facts`, `timeline`, `graph_ppr`, `wiki_page`, `raw_source`, `lint_report`, `doctor_report`.
- ✅ Outils du moteur de contexte (v0.5.0) : `compile_context`, `embedding_status`, `fresh_insights` (classés par décrue), `list_communities`, `find_session_findings`, `find_code_symbol_mentions`, `ask`.
- ✅ Outils de setup : `tesserae_setup_plan`, `tesserae_setup_apply`.
- ✅ Registre multi-projets : `list_projects`, `register_project`, `unregister_project`, `list_sessions`. Dispatch d’URL de store via `url_resolver`.

## Tests

La suite actuelle couvre :

- ✅ garde-fous d’ontologie (y compris le nouveau nœud `Synthesis` + les arêtes `synthesizes` / `summarizes`) ;
- ✅ extraction déterministe ;
- ✅ parsing/validation du wrapper Claude CLI ;
- ✅ routage Claude sélectif ;
- ✅ workflow canonicalisation/revue ;
- ✅ ingestion par lot ;
- ✅ rapports ;
- ✅ persistance SQLite/Kuzu ;
- ✅ export/sync Graphiti à blanc ;
- ✅ workflow CLI projet ;
- ✅ export de harness d’agent ;
- ✅ export Obsidian ;
- ✅ génération du frontend + intégrité des liens (pas de `nodes/codeclass-*.html`) ;
- ✅ idempotence du store wiki ;
- ✅ golden + idempotence du projecteur de synthèses ;
- ✅ composants, pages, exports, pertinence du site ;
- ✅ forme des siblings IA (`.txt` + `.json` par page) ;
- ✅ idempotence compile-deux-fois de bout en bout ;
- ✅ colonne vertébrale du moteur : pipeline, chaîne de refresh, cœur du daemon + sources, CLI `project engine` ;
- ✅ mémoire d’auto-amélioration : sidecar, décrue/supplantation, suppression de supplantés (y compris MCP), renforcement/contradiction ;
- ✅ récupération + embeddings : recherche hybride, PPR, vrais embeddings par défaut (Phase 6) ;
- ✅ compilateur de contexte : forme/intégrité des citations/déterminisme/budget/repli PPR, CLI `project context`, MCP `compile_context` ;
- ✅ compilation incrémentale (expérimental) : différenciateur, portes de parité, préparation de la provenance, provenance SQLite ;
- ✅ installation du paquet et contrat de l’installeur.
