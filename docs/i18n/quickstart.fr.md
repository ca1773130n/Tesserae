# Démarrage rapide

<!-- translations:start -->
<p align="center"><a href="../quickstart.md">English</a> · <a href="quickstart.ko.md">한국어</a> · <a href="quickstart.zh.md">中文</a> · <a href="quickstart.ja.md">日本語</a> · <a href="quickstart.ru.md">Русский</a> · <a href="quickstart.es.md">Español</a> · <a href="quickstart.fr.md">Français</a> · <a href="quickstart.de.md">Deutsch</a></p>
<!-- translations:end -->
Cette page montre le chemin le plus court depuis un répertoire de projet existant jusqu'à un Tesserae navigable.

## Aperçu des commandes

La CLI est groupée : une poignée de verbes du quotidien au niveau supérieur, plus des
groupes (`sessions`, `vault`, `export`, `code`, `config`, `projects`,
`integrations`, `lab`) pour le reste. Exécutez `tesserae --help` pour voir tout l'arbre :

```text
usage: tesserae <command> [options]

EVERYDAY
  init          Set up .tesserae (wizard by default; --yes non-interactive)
  compile       Rebuild the knowledge graph (compile [paths] = ad-hoc ingest)
  context       Compile agent-ready context for a query
  ask           Ask the project memory a question
  serve         Browse the compiled site (auto-builds if missing)
  status        Node/edge counts, last compile, vault state

AUTOMATION
  engine        Refresh daemon: watch sessions/sources, coalesced recompiles
  refresh       One-shot: import sessions + compile + sync vault
  research      Autonomous research mode: investigate a query

ANALYSIS
  query         Raw retrieval over the graph (top-k, kind filters)
  lint          Graph lint report (--fix-trivial, --severity, --json)

GROUPS
  sessions      import | discover | list — agent session history
  vault         sync | sync-all | set-root | export | prune — Obsidian projection
  export        harness | graphiti | site — artifact exports
  code          ingest | sync — CodeGraph ⇄ project graph (hook-invoked)
  config        llm | show — machine-wide defaults (~/.tesserae/config.json)
  projects      register | list | activate | unregister | mcp-config — registry
  integrations  refresh raganything|understand-anything
  extract       Low-level: extract a typed graph from markdown paths

LAB
  lab           evolve | schema-drift — experimental LLM ops

Run `tesserae <command> --help` for command details.
```

Pour voir les flags d'une commande précise, exécutez `tesserae <command> --help` (par exemple `tesserae compile --help`).

## 1. Lancer l'assistant de configuration

Depuis le projet que vous voulez indexer :

```bash
cd /path/to/my-project
tesserae init
```

L'assistant détecte des source courants tels que `README.md`, `docs`, `src`, `lib`, `app`, `packages` et `data`, puis écrit `.tesserae/config.json`. Il configure aussi le Cognee backend par défaut pour que `tesserae ask` puisse essayer Cognee et se rabattre sur la recherche wiki compilée.

Pour une configuration non interactive (CI, scripts), passez `--yes` afin d'accepter les valeurs par défaut détectées sans invite :

```bash
tesserae init --yes
```

Pour une configuration entièrement automatique avec Understand Anything et Cognee runtime memory activés :

```bash
tesserae init \
  --yes \
  --with-understand-anything \
  --install-understand-anything \
  --understand-anything-platform codex \
  --with-raganything \
  --install-raganything \
  --raganything-parser mineru \
  --run-raganything \
  --run-cognee \
  --install-cognee
```

Ce que cela fait :

| Flag | Effect |
|---|---|
| `--with-understand-anything` | Ajoute la UA graph projection comme source. |
| `--install-understand-anything` | Installe/met à jour les UA companion skills. |
| `--understand-anything-platform codex` | Utilise Codex pour exécuter le UA refresh wrapper géré par Tesserae. |
| `--with-raganything` | Active l'ingestion multimodal via RAG-Anything. |
| `--install-raganything` | Installe raganything[all] pendant la configuration. |
| `--raganything-parser` | Choix du parser : mineru (par défaut), docling, paddleocr. |
| `--run-raganything` | Rafraîchit automatiquement RAG-Anything à chaque compile. |
| `--run-cognee` | Exécute un Cognee runtime cognify best-effort pendant le compile. |
| `--install-cognee` | Installe Cognee avec le Python courant s'il est absent. |

Les utilisateurs n'ont pas besoin de connaître le chemin d'installation de UA ni de taper `/understand` ; quand le UA graph est absent ou obsolète, `tesserae compile` exécute `tesserae integrations refresh understand-anything`.

> **Sauter l'assistant.** `tesserae init --bare` écrit un `.tesserae/config.json` minimal sans détection de source ni sondage de backend — pratique quand vous voulez éditer le config à la main avant le premier compile.

## 2. Compiler le graphe et les projections

```bash
tesserae compile
```

`compile` écrit les artefacts durables :

```text
.tesserae/
  config.json
  graph.json
  manifest.json
  sqlite.db
  temporal_facts.jsonl
  graphiti_episodes.jsonl
  report.md
  competitive_report.md
  markdown_projection/
  obsidian_vault/
  agent_harness/
  harness_sessions/
  site/
  cognee_bundle/
```

Après la première exécution, utilisez `--changed-only` pour ignorer les fichiers markdown inchangés tout en préservant le graphe précédent lorsque aucun fichier n'a changé. Si Understand Anything est activé, compile commence par refresh/materialize `.tesserae/external/understand-anything.md` ; si Cognee runtime est activé, il met aussi à jour Cognee en best-effort après avoir écrit `.tesserae/cognee_bundle/`.

Pour faire un ingest ad-hoc de chemins supplémentaires sans toucher aux source configurés, passez-les en positionnel : `tesserae compile path/to/extra.md docs/`.

### Les commutateurs d'intégration vivent désormais dans config

`tesserae compile` est volontairement limité aux flags du quotidien (paths
positionnels plus `--project`, `--changed-only`, `--limit`,
`--refresh-integrations`, `--sessions`/`--no-sessions` et les trois flags LLM). Tous
les autres anciens flags de compile ont été déplacés dans un bloc `compile_options`
de `.tesserae/config.json` ; l'ancienne valeur par défaut d'argparse reste le
fallback. Définissez une clé là pour changer le comportement :

| Clé `compile_options` | Ancien flag | Par défaut | Ce que ça fait |
|---|---|---|---|
| `source_kind` | `--source-kind` | (aucun) | Remplace le source kind configuré. |
| `trends` | `--trends` | `false` | Ajoute des nœuds Trend au niveau du corpus. |
| `min_trend_sources` | `--min-trend-sources` | `2` | Minimum de source pour un nœud Trend. |
| `exclude_data` | `--exclude-data` | `false` | Ignore l'auto-inclusion implicite de `project_root/data`. |
| `no_vault_pull` | `--no-vault-pull` | `false` | Ne refait pas le pull des éditions existantes du vault avant le compile. |
| `use_extraction_feedback` | `--use-extraction-feedback` | `false` | Réinjecte les résultats d'extraction précédents dans l'exécution. |
| `sessions_llm` | `--sessions-llm` | (auto) | Mode d'extraction de sessions par LLM (`auto`/`true`/`false`). |
| `sessions_model` | `--sessions-model` | (aucun) | Remplace le modèle LLM utilisé pour l'extraction de sessions. |
| `cognee_add` | `--cognee-add` | `false` | Ajoute le Cognee bundle au dataset (sans cognify). |
| `cognee_cognify` | `--cognee-cognify` | `false` | Ajoute le bundle et lance Cognee cognify. |
| `cognee_codex_cognify` | `--cognee-codex-cognify` | `false` | Lance cognify avec le LLM client de Cognee patché vers Codex. |
| `cognee_codex_model` | `--cognee-codex-model` | `gpt-5.4` | Modèle Codex CLI pour `cognee_codex_cognify`. |
| `cognee_codex_timeout` | `--cognee-codex-timeout` | `300` | Timeout par appel Codex CLI (secondes). |
| `cognee_dataset` | `--cognee-dataset` | `tesserae_research_graph` | Nom du dataset Cognee. |
| `cognee_embedding_provider` | `--cognee-embedding-provider` | `deterministic` | Embedding provider pour la lane Cognee. |
| `cognee_ollama_embedding_model` | `--cognee-ollama-embedding-model` | `qwen3-embedding:0.6b` | Modèle d'embedding Ollama. |
| `cognee_ollama_embedding_endpoint` | `--cognee-ollama-embedding-endpoint` | `http://127.0.0.1:11434/api/embed` | Endpoint `/api/embed` d'Ollama. |
| `cognee_ollama_embedding_timeout` | `--cognee-ollama-embedding-timeout` | `120` | Timeout de la requête d'embedding Ollama (secondes). |
| `cognee_local_embedding_dimensions` | `--cognee-local-embedding-dimensions` | `128` | Dimensionnalité de l'embedding local. |
| `cognee_system_root` | `--cognee-system-root` | (aucun) | Répertoire system root isolé de Cognee. |
| `cognee_data_root` | `--cognee-data-root` | (aucun) | Répertoire data root isolé de Cognee. |

> **Pipeline en une fois.** `tesserae refresh` exécute toute la boucle en cours de processus — il importe toute nouvelle session d'agent, compile et synchronise le vault en une seule commande. Passez `--changed-only` pour le compile incrémental optionnel.

## 3. Construire et servir le frontend statique

`serve` construit automatiquement le site s'il est absent, donc une seule commande vous donne un Tesserae navigable :

```bash
tesserae serve --port 8765
```

Ouvrez :

```text
http://127.0.0.1:8765/
```

Pour construire le site explicitement (par exemple pour un deploy sans servir), utilisez `export site` ; passez `--no-build` à `serve` lorsque vous voulez parcourir un site déjà construit sans le reconstruire :

```bash
tesserae export site
tesserae serve --no-build --port 8765
```

<!-- BEGIN: subagent-r-watch -->
### Reconstruction automatique à l'enregistrement

Associez le serveur de développement au watcher intégré pour que les éditions sous `data/` et `docs/` déclenchent un recompile incrémental :

```bash
# terminal 1
python3 -m http.server 56821 --directory .tesserae/site

# terminal 2
tesserae export site --watch
```

`export site --watch` sonde toutes les 2 s, fait un debounce de 1 s et exécute `compile --changed-only`. Utilisez `--once` pour des reconstructions façon cron (instantanés comparés à `.tesserae/.watch-cache.json`), `--paths <dir>` pour ajouter des répertoires de surveillance personnalisés et `--interval` / `--debounce` pour ajuster la cadence.
<!-- END: subagent-r-watch -->

### Lancer le daemon de refresh

Si vous voulez un moteur toujours actif qui maintient la base de connaissances à jour de lui-même — surveillant vos source, fusionnant les rafales d'éditions et recompilant automatiquement — démarrez le daemon supervisé :

```bash
tesserae engine
```

`engine` est le superviseur de longue durée : il sonde toutes les 2 s et attend une fenêtre de silence de 1 s avant chaque reconstruction. Réglez la cadence avec `--interval` et `--debounce`, pointez-le vers un autre projet avec `--project`, ou passez `--once` pour exécuter un unique cycle de drain déterministe et sortir (utile pour cron ou CI). C'est la contrepartie sans intervention de `export site --watch` : laissez-le tourner et le graphe, le vault et le site restent à jour pendant que vous et vos agents travaillez.

Pour une visite annotée de chaque route visible — home, sources, concepts, entities, papers, repos, topics, syntheses, questions, timeline, graph, ainsi que les AI siblings — voir [`docs/frontend-redesign.md`](frontend-redesign.fr.md).

Le frontend est léger en dépendances et écrit :

```text
.tesserae/site/index.html
.tesserae/site/sessions/index.html
.tesserae/site/graph.json
.tesserae/site/search-index.json
.tesserae/site/llms.txt
```

## 4. Importer l'historique local des sessions d'agent

L'import de l'historique des sessions est explicite : le compile/build normal lit les sessions déjà normalisées mais ne scanne pas de lui-même les stores privés de transcriptions de Claude Code ou Codex.

```bash
# Preview matching Claude Code/Codex sessions for this project:
tesserae sessions discover

# Normalize and store them under .tesserae/harness_sessions/:
tesserae sessions discover --import

# Confirm the imported set:
tesserae sessions list

# Rebuild so sessions/index.html and session detail pages are emitted:
tesserae export site
```

Les sessions importées apparaissent dans la section globale Sessions, la recherche du site et les cartes Browse de la home. Les pages de détail de session rendent les tours user/assistant en markdown lisible, attachent les blocs tool-use sous le tour assistant précédent et exposent un rail de tours à gauche pour la navigation `#turn-N`. Pour les notes de confidentialité, les formats d'import et la carte typographique actuelle des transcriptions, voir [`docs/session-history.md`](session-history.fr.md).

## 5. Lint du wiki

```bash
tesserae lint
```

Parcourt le graph + wiki + site compilés et signale les orphan papers, stale citations, le drift entre graph et wiki/, les ghost synthesis inputs et plus encore. Écrit `.tesserae/lint-report.md` et `.tesserae/lint-report.json`. Passez `--fix-trivial` pour appliquer des auto-corrections sûres (edges `implemented_in` manquants, élagage des ghost-input) et `--severity error` pour ne faire échouer le code de sortie que sur les erreurs.

## 6. Interroger le wiki

```bash
tesserae query "What is Gaussian Splatting?"
```

Recherche uniquement par défaut — BM25 sur `.tesserae/site/search-index.json`, avec un extrait de 200 caractères tiré du `wiki/<kind>/<slug>.md` correspondant. Passez `--kind papers` (ou `concepts`, `repos`, etc.) pour restreindre, `--top-k N` pour élargir et `--json` pour une sortie structurée. Ajoutez `--llm` (ou définissez `TESSERAE_QUERY_LLM=1`) pour demander à Claude une réponse synthétisée avec des citations `[node_id]` ; `--interactive` ouvre un REPL readline — ligne vide ou EOF pour quitter. `TESSERAE_QUERY_DRY_RUN=1` exerce le prompt sans appel API.

## 7. Compiler à la demande un context prêt pour l'agent

La vedette de la v0.5.0 est l'On-Demand Context Compiler : demandez au graphe compilé un unique document de context cité, cadré sur une requête et dimensionné pour tenir dans la fenêtre d'un agent.

```bash
tesserae context "How does session import work?"
```

Il amorce Personalized PageRank depuis les nœuds correspondant à votre requête (utilisez `--seeds <node_id>` pour amorcer explicitement), étend le voisinage (`--depth`, par défaut 2) et assemble un document cité plafonné par un `--budget` de caractères (par défaut 32000 ; passez `<= 0` pour sans plafond). Ajoutez `--synthesize` pour un résumé écrit par LLM au-dessus (nécessite un LLM backend) et `-o/--output <file>` pour écrire le document sur disque au lieu de stdout.

Le même compiler est exposé aux agents via MCP comme l'outil `compile_context`, de sorte qu'un agent de codage peut tirer juste ce qu'il faut de context de projet, borné par le budget, en cours de conversation, sans export manuel.

## 8. Exporter les fichiers d'agent harness

```bash
tesserae export harness
```

Targets pris en charge :

- Claude Code
- Codex
- Gemini
- Kiro
- Cursor
- OpenCode

Exemple de sous-ensemble :

```bash
tesserae export harness \
  --target claude-code \
  --target cursor \
  --target opencode
```

## 9. Exporter un vault Obsidian

```bash
tesserae vault export
```

Ou écrire dans un vault existant :

```bash
tesserae vault export --vault "$OBSIDIAN_VAULT_PATH"
```

Le vault inclut les markdown projections, les `.obsidian` defaults, la coloration du graphe, `raw/assets/` et un Dataview dashboard. Utilisez `tesserae vault sync` pour réconcilier un vault existant avec le dernier compile (ajoutez `--prune` pour supprimer les notes orphelines).

## 10. Configurer MCP

```bash
tesserae projects mcp-config --server-name my_project_wiki
```

Collez la sortie sous `mcp_servers` dans `~/.hermes/config.yaml`, puis redémarrez Hermes/gateway.

## 11. Export / sync Graphiti

Export d'épisodes sans dépendances :

```bash
tesserae export graphiti
```

Smoke de sync en dry-run sans Graphiti installé :

```bash
tesserae export graphiti --sync --dry-run
```

La sync en direct nécessite `graphiti_core` et un Neo4j backend accessible :

```bash
tesserae export graphiti --sync \
  --neo4j-uri bolt://localhost:7687 \
  --neo4j-user neo4j \
  --neo4j-password '<password>'
```

## 12. Déployer sur GitHub Pages

Poussez le site compilé dans `.tesserae/site/` vers la branche `gh-pages` du git origin du projet :

```bash
tesserae export site --deploy --build --enable-pages
```

`--build` exécute `compile` d'abord pour que le site soit frais. `--enable-pages` active Pages via la `gh` CLI (idempotent ; ignoré avec un indice si `gh` est absent). Utilisez `--dry-run` pour faire stage et commit sans push, `--branch` / `--remote` pour remplacer les valeurs par défaut et `--force` pour autoriser le deploy avec un arbre de travail sale.

Le site devient accessible à `https://<owner>.github.io/<repo>/`.
