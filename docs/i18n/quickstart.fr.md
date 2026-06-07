# Démarrage rapide

<!-- translations:start -->
<p align="center"><a href="../quickstart.md">English</a> · <a href="quickstart.ko.md">한국어</a> · <a href="quickstart.zh.md">中文</a> · <a href="quickstart.ja.md">日本語</a> · <a href="quickstart.ru.md">Русский</a> · <a href="quickstart.es.md">Español</a> · <a href="quickstart.fr.md">Français</a> · <a href="quickstart.de.md">Deutsch</a></p>
<!-- translations:end -->
Cette page montre le chemin le plus court depuis un répertoire de projet existant jusqu'à un Tesserae navigable.

## 1. Lancez l'assistant de configuration

Depuis le projet que vous voulez indexer :

```bash
cd /path/to/my-project
tesserae project setup
```

L'assistant détecte les sources courantes comme `README.md`, `docs`, `src`, `lib`, `app`, `packages` et `data`, puis écrit `.tesserae/config.json`. Il configure aussi le backend Cognee par défaut afin que `project ask` puisse essayer Cognee puis fallback vers la recherche du wiki compilé.

Pour une configuration entièrement automatisée avec Understand Anything et Cognee runtime memory activés :

```bash
tesserae project setup \
  --yes \
  --with-understand-anything \
  --install-understand-anything \
  --understand-anything-platform codex \
  --run-cognee \
  --install-cognee
```

Ce que cela fait :

| Flag | Effect |
|---|---|
| `--with-understand-anything` | Ajoute la UA graph projection comme source. |
| `--install-understand-anything` | Installe/met à jour les UA companion skills. |
| `--understand-anything-platform codex` | Utilise Codex pour exécuter le managed UA refresh wrapper de Tesserae. |
| `--run-cognee` | Lance best-effort Cognee runtime cognify pendant compile. |
| `--install-cognee` | Installe Cognee avec le Python courant s'il manque. |

Les utilisateurs n'ont pas besoin de connaître le UA install path ni de taper `/understand` ; `project compile` exécute `project refresh-understand-anything` lorsque le UA graph manque ou est obsolète.

## 2. Compilez le graphe et les projections

```bash
tesserae project compile
```

`project compile` écrit les durable artifacts :

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

Utilisez `--changed-only` après le premier run pour ignorer les fichiers markdown inchangés tout en conservant le graph précédent quand aucun fichier n'a changé. Si Understand Anything est activé, compile refresh/materialize d'abord `.tesserae/external/understand-anything.md` ; si Cognee runtime est activé, il met aussi à jour Cognee en best-effort après l'écriture de `.tesserae/cognee_bundle/`.

> **Pipeline en une commande.** `tesserae project refresh` exécute toute la boucle in-process : il importe les nouvelles agent sessions, compile et synchronise le vault en une seule commande. Passez `--changed-only` pour le compile incrémental optionnel et `--skip-sessions` pour ignorer le scan plus lent de découverte des harness-sessions.

## 3. Construisez et servez le frontend statique

```bash
tesserae project build-site
tesserae project serve --port 8765
```

Ouvrez :

```text
http://127.0.0.1:8765/
```

<!-- BEGIN: subagent-r-watch -->
### Auto-rebuild à l'enregistrement

Associez le dev server à un polling watcher pour que les modifications sous `data/` et `docs/` déclenchent une incremental recompile :

```bash
# terminal 1
python3 -m http.server 56821 --directory .tesserae/site

# terminal 2
tesserae project watch
```

`project watch` poll toutes les 2 s, debounce 1 s, puis exécute `compile --changed-only`. Utilisez `--once` pour des rebuilds façon cron (snapshots vs `.tesserae/.watch-cache.json`), `--paths <dir>` pour ajouter des custom watch dirs, et `--interval` / `--debounce` pour ajuster la cadence.
<!-- END: subagent-r-watch -->

### Lancer le daemon de refresh

Si vous voulez un moteur toujours actif qui surveille vos sources de lui-même, regroupe les rafales d'éditions et recompile automatiquement pour garder la base de connaissances à jour, démarrez le daemon supervisé :

```bash
tesserae project engine
```

`project engine` (alias `project daemon`) est le superviseur de longue durée : il poll toutes les 2 s et attend une fenêtre de silence de 1 s avant chaque rebuild. Réglez la cadence avec `--interval` et `--debounce`, ciblez un autre projet avec `--project`, ou passez `--once` pour exécuter un unique cycle de drain déterministe puis quitter (pratique pour cron ou CI). C'est la contrepartie autonome de `project watch` : laissez-le tourner et le graph, le vault et le site restent à jour pendant que vous et vos agents travaillez.

Pour un tour annoté de chaque route visible — home, sources, concepts, entities, papers, repos, topics, syntheses, questions, timeline, graph, plus les AI siblings — voir [`docs/frontend-redesign.md`](frontend-redesign.fr.md).

Le frontend a peu de dépendances et écrit :

```text
.tesserae/site/index.html
.tesserae/site/sessions/index.html
.tesserae/site/graph.json
.tesserae/site/search-index.json
.tesserae/site/llms.txt
```

## 4. Importez l'historique local des sessions agent

L'import d'historique de session est explicite : compile/build normal lit les sessions déjà normalisées, mais ne scanne pas seul les transcript stores privés Claude Code ou Codex.

```bash
# Preview matching Claude Code/Codex sessions for this project:
tesserae project sessions discover

# Normalize and store them under .tesserae/harness_sessions/:
tesserae project sessions discover --import

# Confirm the imported set:
tesserae project sessions list

# Rebuild so sessions/index.html and session detail pages are emitted:
tesserae project build-site
```

Les sessions importées apparaissent dans la section globale Sessions, la recherche du site et les cartes Browse de l'accueil. Les pages détail de session rendent les tours user/assistant en markdown lisible, attachent les tool-use blocks sous le tour assistant précédent et exposent un turn rail gauche pour la navigation `#turn-N`. Voir [`docs/session-history.md`](session-history.fr.md) pour les notes de confidentialité, formats d'import et la transcript typography map courante.

## 5. Lintez le wiki

```bash
tesserae project lint
```

Parcourt le compiled graph + wiki + site et signale orphan papers, stale citations, drift entre graph et wiki/, ghost synthesis inputs, etc. Écrit `.tesserae/lint-report.md` et `.tesserae/lint-report.json`. Passez `--fix-trivial` pour appliquer des auto-fixes sûrs (missing `implemented_in` edges, ghost-input pruning) et `--severity error` pour ne faire échouer l'exit code que sur les errors.

## 6. Interrogez le wiki

```bash
tesserae project query "What is Gaussian Splatting?"
```

Par défaut, recherche seule — BM25 sur `.tesserae/site/search-index.json`, avec un excerpt de 200 caractères pris depuis le `wiki/<kind>/<slug>.md` correspondant. Passez `--kind papers` (ou `concepts`, `repos`, etc.) pour restreindre, `--top-k N` pour élargir, et `--json` pour une sortie structurée. Ajoutez `--llm` (ou définissez `TESSERAE_QUERY_LLM=1`) pour demander à Claude une réponse synthétisée avec citations `[node_id]` ; `--interactive` ouvre un REPL readline — ligne vide ou EOF quitte. `TESSERAE_QUERY_DRY_RUN=1` exerce le prompt sans appel API.

## 7. Compilez du context pour les agents à la demande

La nouveauté phare de la v0.5.0 est l'On-Demand Context Compiler : demandez au graph compilé un unique document de context cité, limité à une requête et dimensionné pour tenir dans la context window d'un agent.

```bash
tesserae project context "Comment fonctionne session import ?"
```

Il amorce un Personalized PageRank à partir des nœuds correspondant à votre requête (utilisez `--seeds <node_id>` pour amorcer explicitement), étend le voisinage (`--depth`, par défaut 2), puis assemble un document cité plafonné par un `--budget` de caractères (par défaut 32000 ; passez `<= 0` pour illimité). Ajoutez `--synthesize` pour un résumé rédigé par un LLM par-dessus (nécessite un backend LLM) et `-o/--output <file>` pour écrire le document dans un fichier plutôt que sur stdout.

Le même compiler est exposé aux agents via MCP sous la forme de l'outil `compile_context`, de sorte qu'un agent de programmation peut récupérer juste ce qu'il faut de context projet, borné par budget, en pleine conversation et sans export manuel.

## 8. Exportez les fichiers agent harness

```bash
tesserae project export-agent-harness
```

Targets supportés :

- Claude Code
- Codex
- Gemini
- Kiro
- Cursor
- OpenCode

Subset d'exemple :

```bash
tesserae project export-agent-harness \
  --target claude-code \
  --target cursor \
  --target opencode
```

## 9. Exportez un vault Obsidian

```bash
tesserae project export-obsidian
```

Ou écrivez dans un vault existant :

```bash
tesserae project export-obsidian --vault "$OBSIDIAN_VAULT_PATH"
```

Le vault inclut les markdown projections, les defaults `.obsidian`, le graph coloring, `raw/assets/` et un dashboard Dataview.

## 10. Configurez MCP

```bash
tesserae project mcp-config --server-name my_project_wiki
```

Collez la sortie sous `mcp_servers` dans `~/.hermes/config.yaml`, puis redémarrez Hermes/gateway.

## 11. Graphiti export / sync

Episode export sans dépendance :

```bash
tesserae project export-graphiti
```

Dry-run sync smoke sans Graphiti installé :

```bash
tesserae project sync-graphiti --dry-run
```

Live sync nécessite `graphiti_core` et un backend Neo4j joignable :

```bash
tesserae project sync-graphiti \
  --neo4j-uri bolt://localhost:7687 \
  --neo4j-user neo4j \
  --neo4j-password '<password>'
```

## 12. Déployez sur GitHub Pages

Poussez le compiled site dans `.tesserae/site/` vers la branche `gh-pages` du git origin du projet :

```bash
tesserae project deploy --build --enable-pages
```

`--build` exécute d'abord `project compile` pour que le site soit frais. `--enable-pages` active Pages via la CLI `gh` (idempotent ; ignoré avec une indication si `gh` manque). Utilisez `--dry-run` pour stage et commit sans push, `--branch` / `--remote` pour remplacer les defaults, et `--force` pour autoriser le déploiement avec un working tree dirty.

Le site devient accessible à `https://<owner>.github.io/<repo>/`.
