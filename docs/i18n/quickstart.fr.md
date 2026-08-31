# Démarrage rapide

<!-- translations:start -->
<p align="center"><a href="../quickstart.md">English</a> · <a href="quickstart.ko.md">한국어</a> · <a href="quickstart.zh.md">中文</a> · <a href="quickstart.ja.md">日本語</a> · <a href="quickstart.ru.md">Русский</a> · <a href="quickstart.es.md">Español</a> · <a href="quickstart.fr.md">Français</a> · <a href="quickstart.de.md">Deutsch</a></p>
<!-- translations:end -->
Cette page montre le chemin le plus court d’un répertoire de projet existant vers un Tesserae navigable.

## Aperçu des commandes

La CLI est groupée : une poignée de verbes quotidiens au niveau supérieur, plus
des groupes (`sessions`, `vault`, `export`, `code`, `config`, `projects`, `agents`, `domains`,
`integrations`, `lab`) pour le reste. Lancez `tesserae --help` pour voir l’arbre
complet :

```text
tesserae 0.31.0 — a context engine

usage: tesserae <command> [options]

EVERYDAY
  init          Set up .tesserae (wizard by default; --yes non-interactive)
  compile       Rebuild the knowledge graph (compile [paths] = ad-hoc ingest)
  ingest        Ingest a document file or URL into the knowledge base
  context       Compile agent-ready context for a query
  ask           LLM answer over the knowledge graph (planned retrieval)
  serve         Browse the compiled site (auto-builds if missing)
  status        Node/edge counts, last compile, vault state

AUTOMATION
  engine        Refresh daemon: watch sessions/sources, coalesced recompiles, idle 'sleep' consolidation
  refresh       One-shot: import sessions + compile + sync vault
  research      Autonomous research mode: investigate a query
  distill       Per-agent L1 expertise artifacts (opt-in: TESSERAE_AGENT_DISTILL)

ANALYSIS
  query         raw retrieval: BM25/semantic + explicit backends
  graph-map     Budgeted Descent navigation (the graph_map tool as a CLI verb; JSON out)
  verify-claim  Does the graph license this triple? Deterministic verdict, JSON out
  schema-drift  Propose ResearchNodeType sub-types from clustered nodes (proposals only; promotion is a human edit)
  lint          Graph lint report (--fix-trivial, --severity, --json)
  doctor        Health checks: init/graph/registry/staleness/locks (--fix = safe repairs only)
  summary       Daily/weekly activity digest (sessions, findings, commits, PRs, docs)
  decisions     Decisions across projects + time (human AskUserQuestion + agent)

GROUPS
  sessions      import | discover | list — agent session history
  vault         sync | sync-all | set-root | export | prune — Obsidian projection
  export        harness | graphiti | site | okf | kuzu — artifact exports
  code          ingest | sync — CodeGraph ⇄ project graph (hook-invoked)
  setup         Machine-wide setup: LLM defaults + optional deps (interactive by default)
  config        llm | deps | show | status | clip-token — LLM backend defaults + resolved view & liveness ping
  projects      register | list | unregister | mcp-config — registry
  agents        init | list | tree | show | drill | set-parent | rename — role-grade agent org registry
  domains       status — chartered domain tree (divisions/departments/teams)
  sources       add | list | remove — manage compile source dirs (local & global)
  federation    status | explain — inspect cross-project federation
  integrations  refresh raganything
  extract       Low-level: extract a typed graph from markdown paths

LAB
  lab           evolve — experimental LLM ops

Run `tesserae <command> --help` for command details.
```

Lancez `tesserae <command> --help` (p. ex. `tesserae compile --help`) pour les
drapeaux de n’importe quelle commande individuelle.

## 1. Lancer l’assistant de configuration

Depuis le projet que vous voulez indexer :

```bash
cd /path/to/my-project
tesserae init
```

`tesserae init` est l’unique étape d’intégration. L’assistant détecte les sources courantes comme `README.md`, `docs`, `src`, `lib`, `app`, `packages` et `data`, sonde quelles CLI LLM sont installées **et connectées**, vous laisse choisir le fournisseur LLM, et écrit `.tesserae/config.json`. Le backend de mémoire optionnel RAG-Anything est **désactivé par défaut** ; activez-le plus tard dans `memory_backends` dans la config, et interrogez-le explicitement avec `tesserae query --backend raganything`.

Pour une configuration non interactive (CI, scripts), passez `--yes` pour
accepter les valeurs détectées sans invite (toutes les intégrations
optionnelles OFF) :

```bash
tesserae init --yes
```

### Configuration du fournisseur LLM

Le choix de fournisseur de l’assistant (ou les drapeaux équivalents) persiste ces clés de config :

| Clé de config | Drapeau | Ce que c’est |
|---|---|---|
| `llm_provider` | `--llm-provider {claude,codex,anthropic,openai,custom}` | Backend du client LLM : `claude`/`codex` utilisent la CLI connectée via OAuth ; `anthropic` et `openai` utilisent ces APIs directement ; `custom` cible un endpoint que vous nommez. |
| `llm_model` | `--llm-model` | Modèle pour le client LLM de synthèse/insights. |
| `llm_base_url` | `--llm-base-url` | URL de base de l’endpoint pour `anthropic`/`openai`/`custom`. |
| `llm_api_key` | `--llm-api-key` | Clé API pour `anthropic`/`openai`/`custom`. |

> **Avertissement texte en clair.** `llm_api_key` est stockée en **texte clair**
> dans `.tesserae/config.json`. Préférez plutôt les variables d’environnement :
> `TESSERAE_LLM_API_KEY` (clé), `TESSERAE_LLM_BASE_URL` (endpoint) et
> `TESSERAE_LLM_MODEL` (modèle). L’ordre de résolution est env → config projet →
> config machine (`~/.tesserae/config.json`, écrite par `tesserae setup`)
> → valeur par défaut intégrée. Les noms plus anciens `ANTHROPIC_API_KEY` /
> `ANTHROPIC_BASE_URL` fonctionnent toujours, un degré en dessous des noms
> possédés par Tesserae.

Relancer `init` sur un projet existant **fusionne** — vos `sources` et
`memory_backends` configurés sont préservés, pas écrasés.

Exemples de configurations de fournisseur non interactives :

```bash
tesserae init --yes --llm-provider codex

# Un endpoint compatible OpenAI (vLLM, LiteLLM, OpenRouter, Ollama, LM Studio).
# Le protocole est un paramètre séparé du fournisseur, et init ne le persiste
# pas — définissez-le dans l’environnement, ou avec `tesserae config llm`.
tesserae init --yes --llm-provider custom \
  --llm-base-url http://localhost:8000/v1 \
  --llm-model qwen2.5-coder-32b-instruct
export TESSERAE_LLM_API_STYLE=openai      # POST {base_url}/chat/completions
export TESSERAE_LLM_AUTH_TOKEN=sk-...     # omettez entièrement pour un serveur local sans clé

# Une passerelle compatible Anthropic. PAS de /v1 : le SDK ajoute /v1/messages lui-même,
# et une URL de base finissant par /v1 est rognée pour éviter qu’elle ne double.
tesserae init --yes --llm-provider custom \
  --llm-base-url https://gateway.internal.example \
  --llm-model claude-sonnet-4-6           # clé via TESSERAE_LLM_API_KEY
```

Ensuite confirmez ce qui s’est réellement résolu — protocole, modèle, URL, type
d’accréditation, et la couche d’où chacun provient — avant de passer une
compilation :

```bash
tesserae config status --project .
```

[Tuning → Serveur LLM](tuning.md#serveur-llm) est la référence complète : chaque
clé `llm_*`, les deux recettes d’endpoint personnalisé, et ce qu’un endpoint
configuré fait quand il ne peut pas être construit.

> **Sauter l’assistant.** `tesserae init --bare` écrit un `.tesserae/config.json`
> minimal sans détection de sources ni sondage de backends — pratique quand vous
> voulez éditer la config à la main avant la première compilation.

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
  markdown_projection/
  obsidian_vault/
  agent_harness/
  harness_sessions/
  site/
```

Utilisez `--changed-only` après la première exécution pour sauter les fichiers markdown inchangés tout en préservant le graphe précédent quand aucun fichier n’a changé.

Pour ingérer des chemins supplémentaires ad hoc sans toucher aux sources
configurées, passez-les en positionnel : `tesserae compile path/to/extra.md docs/`.

### Les réglages d’intégration vivent désormais dans la config

`tesserae compile` est délibérément limité aux drapeaux quotidiens (chemins en
positionnel plus `--project`, `--changed-only`, `--limit`, `--refresh-integrations`,
`--sessions`/`--no-sessions`, et les trois drapeaux LLM). Tous les autres anciens
drapeaux de compile ont migré dans un bloc `compile_options` de
`.tesserae/config.json` ; l’ancienne valeur par défaut argparse reste le repli.
Définissez une clé là-bas pour changer le comportement :

| Clé `compile_options` | Ancien drapeau | Défaut | Ce qu’elle fait |
|---|---|---|---|
| `source_kind` | `--source-kind` | (aucun) | Remplace le type de source configuré. |
| `trends` | `--trends` | `false` | Ajoute des nœuds Trend au niveau du corpus. |
| `min_trend_sources` | `--min-trend-sources` | `2` | Nombre minimal de sources pour un nœud Trend. |
| `exclude_data` | `--exclude-data` | `false` | Saute l’auto-inclusion implicite de `project_root/data`. |
| `no_vault_pull` | `--no-vault-pull` | `false` | Ne pas rapatrier les éditions du vault existant avant la compilation. |
| `use_extraction_feedback` | `--use-extraction-feedback` | `false` | Réinjecte les résultats d’extraction antérieurs dans l’exécution. |
| `sessions_llm` | `--sessions-llm` | (auto) | Mode d’extraction de sessions par LLM (`auto`/`true`/`false`). |
| `sessions_model` | `--sessions-model` | (aucun) | Remplace le modèle LLM utilisé pour l’extraction de sessions. |

> **Cognee a été supprimé en 0.19.** Le backend cognee avait été rétrogradé en
> 0.18 et n’a jamais alimenté le graphe. Les configs portant encore une section
> `memory_backends.cognee` (ou des options de compile `cognee_*`) continuent de
> se charger — la section est ignorée avec une note d’une ligne.

> **Pipeline en un coup.** `tesserae refresh` exécute toute la boucle en
> processus — il importe les nouvelles sessions d’agent, compile et synchronise
> le vault en une seule commande. Passez `--changed-only` pour la compilation
> incrémentale opt-in.

## 3. Construire et servir le frontend statique

`serve` construit automatiquement le site s’il est manquant, si bien qu’une
seule commande vous donne un Tesserae navigable. **Un `serve` nu sert chaque
projet enregistré** sous un même serveur — une page d’accueil des projets à `/`,
chaque projet à `/<alias>/`, et un sélecteur Projects dans l’en-tête pour passer
de l’un à l’autre. Le **widget ask** intégré à la page **fonctionne en direct
dans les deux modes**, routé vers le projet de la page où vous êtes :

```bash
tesserae serve --port 8765                 # all registered projects
tesserae serve --project . --port 8765     # just this one
```

Ouvrez :

```text
http://127.0.0.1:8765/
```

Pour construire le site explicitement (p. ex. pour un déploiement sans le
servir), utilisez `export site` ; passez `--no-build` à `serve` quand vous
voulez naviguer dans un site déjà construit sans le reconstruire :

```bash
tesserae export site
tesserae serve --no-build --port 8765
```

<!-- BEGIN: subagent-r-watch -->
### Reconstruction automatique à la sauvegarde

Appariez le serveur de dev avec le watcher intégré pour que les éditions sous `data/` et `docs/` déclenchent une recompilation incrémentale :

```bash
# terminal 1
python3 -m http.server 56821 --directory .tesserae/site

# terminal 2
tesserae export site --watch
```

`export site --watch` sonde toutes les 2 s, applique un debounce de 1 s, et lance `compile --changed-only`. Utilisez `--once` pour des reconstructions façon cron (snapshots contre `.tesserae/.watch-cache.json`), `--paths <dir>` pour ajouter des répertoires surveillés personnalisés, et `--interval` / `--debounce` pour régler la cadence.
<!-- END: subagent-r-watch -->

### Lancer le daemon de rafraîchissement

Pour un moteur toujours actif qui garde de lui-même la base de connaissances fraîche — surveillant vos sources, coalescant les rafales d’éditions et recompilant automatiquement — démarrez le daemon supervisé :

```bash
tesserae engine
```

`engine` est le superviseur de longue durée : il sonde toutes les 2 s et attend une fenêtre de calme de 1 s avant chaque reconstruction. Réglez la cadence avec `--interval` et `--debounce`, pointez-le vers un autre projet avec `--project`, ou passez `--once` pour exécuter un unique cycle de drainage déterministe puis sortir (utile pour cron ou la CI). C’est le pendant mains-libres d’`export site --watch` : laissez-le tourner et le graphe, le vault et le site restent à jour pendant que vous et vos agents travaillez.

Pour une visite annotée de chaque route visible — home, sources, concepts, entités, papers, repos, topics, synthèses, questions, timeline, graphe, plus les siblings IA — voir [`docs/frontend-redesign.md`](frontend-redesign.fr.md).

Le frontend est léger en dépendances et écrit :

```text
.tesserae/site/index.html
.tesserae/site/sessions/index.html
.tesserae/site/graph.json
.tesserae/site/search-index.json
.tesserae/site/llms.txt
```

## 4. Importer l’historique local des sessions d’agent

L’import de l’historique de sessions est explicite : la compilation/construction normale lit les sessions déjà normalisées mais ne scanne pas d’elle-même les magasins privés de transcriptions Claude Code ou Codex.

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

Les sessions importées apparaissent dans la section globale Sessions, la recherche du site et les cartes Browse de l’accueil. Les pages de détail de session rendent les tours utilisateur/assistant en markdown lisible, attachent les blocs d’usage d’outil sous le tour assistant précédent, et exposent un rail de tours à gauche pour la navigation `#turn-N`. Voir [`docs/session-history.md`](session-history.fr.md) pour les notes de confidentialité, les formats d’import et la carte typographique actuelle des transcriptions.

## 5. Linter le wiki

```bash
tesserae lint
```

Parcourt le graphe compilé + le wiki + le site et signale les papers orphelins, les citations périmées, la dérive entre graphe et wiki/, les entrées de synthèse fantômes, et plus encore. Écrit `.tesserae/lint-report.md` et `.tesserae/lint-report.json`. Passez `--fix-trivial` pour appliquer les auto-corrections sûres (arêtes `implemented_in` manquantes, élagage des entrées fantômes) et `--severity error` pour ne faire échouer le code de sortie que sur les erreurs.

Pour la santé de l’espace de travail au-delà du graphe lui-même — cohérence du registre, staleness, verrous, connexion LLM, hygiène — lancez `tesserae doctor` (`--fix` n’applique que les réparations sûres). Voir [`docs/doctor.md`](doctor.fr.md).

## 6. Interroger le wiki avec ask et query

```bash
# LLM-planned, cited answer over the compiled graph (the default):
tesserae ask "What is Gaussian Splatting?"

# Raw retrieval — ranked search hits, no LLM:
tesserae query "What is Gaussian Splatting?"
```

`ask` est la surface de réponse : le modèle planifie la récupération sur le graphe compilé, puis synthétise une réponse citée. Il fonctionne avec une CLI `claude`/`codex` connectée (OAuth) ou `ANTHROPIC_API_KEY` ; passez `--no-llm` pour n’obtenir que des résultats de recherche classés (ce forçage à off l’emporte sur `TESSERAE_QUERY_LLM=1`). `TESSERAE_QUERY_DRY_RUN=1` exerce le prompt sans appel API.

`query` est la surface de récupération : recherche BM25/sémantique sur `.tesserae/site/search-index.json`, avec un extrait de 200 caractères tiré du `wiki/<kind>/<slug>.md` correspondant. Passez `--kind papers` (ou `concepts`, `repos`, etc.) pour restreindre, `--top-k N` pour élargir, et `--json` pour une sortie structurée ; `--interactive` ouvre un REPL readline — ligne vide ou EOF pour sortir. Le backend mémoire explicite vit ici aussi : `--backend raganything` court-circuite vers ce backend et fait remonter ses erreurs. Il n’y a pas de synthèse LLM sur `query` — c’est le rôle d’`ask`.

## 7. Compiler du contexte prêt pour agent à la demande

La tête d’affiche de la v0.5.0 est le compilateur de contexte à la demande : demandez au graphe compilé un unique document de contexte cité, ciblé sur une requête, dimensionné pour tenir dans la fenêtre d’un agent.

```bash
tesserae context "How does session import work?"
```

Il amorce un Personalized PageRank depuis les nœuds correspondant à votre requête (utilisez `--seeds <node_id>` pour amorcer explicitement), étend le voisinage (`--depth`, défaut 2), et assemble un doc cité plafonné à un `--budget` en caractères (défaut 32000 ; passez `<= 0` pour sans plafond). Ajoutez `--llm` pour un résumé écrit par LLM par-dessus (requiert un backend LLM) et `-o/--output <file>` pour écrire le doc sur disque au lieu de stdout.

Le même compilateur est exposé aux agents via MCP comme l’outil `compile_context`, si bien qu’un agent de codage peut tirer un contexte projet juste-suffisant, borné en budget, en pleine conversation sans export manuel.

## 8. Exporter les fichiers de harness d’agent

```bash
tesserae export harness
```

Cibles prises en charge :

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
tesserae vault export --output "$OBSIDIAN_VAULT_PATH"
```

Le vault inclut les projections markdown, les valeurs par défaut `.obsidian`, la coloration du graphe, `raw/assets/`, et un tableau de bord Dataview. Utilisez `tesserae vault sync` pour réconcilier un vault existant avec la dernière compilation (ajoutez `--prune` pour supprimer les notes orphelines).

## 10. Configurer MCP

```bash
tesserae projects mcp-config --server-name my_project_wiki
```

Collez la sortie sous `mcp_servers` dans `~/.hermes/config.yaml`, puis redémarrez Hermes/gateway.

## 11. Export / sync Graphiti

Export d’épisodes sans dépendance :

```bash
tesserae export graphiti
```

Test de sync à blanc sans Graphiti installé :

```bash
tesserae export graphiti --sync --dry-run
```

La synchronisation en direct requiert `graphiti_core` et un backend Neo4j joignable :

```bash
tesserae export graphiti --sync \
  --neo4j-uri bolt://localhost:7687 \
  --neo4j-user neo4j \
  --neo4j-password '<password>'
```

## 12. Déployer sur GitHub Pages

Poussez le site compilé de `.tesserae/site/` vers la branche `gh-pages` de l’origin git du projet :

```bash
tesserae export site --deploy --build --enable-pages
```

`--build` lance d’abord `compile` pour que le site soit frais. `--enable-pages` active Pages via la CLI `gh` (idempotent ; sauté avec une indication si `gh` est absent). Utilisez `--dry-run` pour préparer et committer sans pousser, `--branch` / `--remote` pour remplacer les valeurs par défaut, et `--force` pour autoriser un déploiement avec un arbre de travail sale.

Le site devient accessible à `https://<owner>.github.io/<repo>/`.
