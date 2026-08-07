# Tesserae

<p align="center">
  <img src="docs/assets/tesserae-graph-view.png" alt="Vue du graphe Tesserae : concepts, articles, dépôts, synthèses et entités regroupés autour d'un nœud focal" width="100%" />
</p>

<p align="center">
  <a href="./README.md">English</a> ·
  <a href="./README.ko.md">한국어</a> ·
  <a href="./README.zh.md">中文</a> ·
  <a href="./README.ja.md">日本語</a> ·
  <a href="./README.ru.md">Русский</a> ·
  <a href="./README.es.md">Español</a> ·
  <a href="./README.de.md">Deutsch</a>
</p>

> Un moteur de contexte qui maintient une base de connaissances auto-améliorante pour votre projet et compile à la demande du contexte prêt pour les agents.

<p align="center">
  <img src="docs/screencasts/showcase.gif" alt="Screencast en trois étapes : tesserae init -> compile -> ask, enregistré sur le corpus de démonstration de 135 documents" width="100%" />
</p>

<p align="center">
  <a href="https://ca1773130n.github.io/Tesserae">Démo en ligne</a> ·
  <a href="docs/">Documentation</a> ·
  <a href="docs/release-notes/">Notes de version</a> ·
  <a href="docs/integrations/mcp.md">Configuration MCP</a> ·
  <a href="docs/tuning.md">Réglages</a> ·
  <a href="docs/integrations/obsidian.md">Export Obsidian</a>
</p>

## Présentation

Pointez Tesserae vers un répertoire de fichiers Markdown, de code source et, optionnellement,
de PDF/documents Office/images. Il reconstruit un **graphe de connaissances typé** du projet
et le maintient à jour, de sorte que les agents disposent toujours d'un contexte fondé et cité.
Trois piliers :

1. **Surveillance des sessions** — vos conversations Claude Code / Codex sur le projet
   deviennent des nœuds de premier ordre dans le graphe (décisions, insights, questions,
   TODO) au fur et à mesure qu'elles se déroulent.
2. **Ingestion autonome** — un moteur supervisé surveille les sources et les sessions,
   regroupe les rafales de modifications, recompile, et un sidecar d'auto-amélioration
   renforce les découvertes récurrentes tout en remplaçant les données obsolètes.
3. **Contexte à la demande** — le compilateur de contexte assemble un document de contexte
   personnalisé et cité pour toute requête ou nœud de départ (PageRank personnalisé sous
   un budget de caractères), prêt à coller dans n'importe quel agent.

Le graphe, le vault Obsidian et le site statique sont des *projections* d'une seule base
de connaissances. Tout fonctionne localement : c'est une étape de compilation plus un
moteur en direct, pas un service hébergé.

## Démarrage rapide

Requiert **Python 3.10+**.

```bash
pip install tesserae          # ajoutez [semantic] pour de vrais embeddings
# ou : pipx install tesserae   # installation la plus sûre pour le PATH
# ou : npx @jokerized/tesserae # wrapper Node autour du même CLI

cd /path/to/my-project
tesserae init --yes           # assistant ; --yes accepte les valeurs détectées par défaut
tesserae compile              # construire le graphe de connaissances
tesserae ask "Where is Mermaid rendering implemented?"

# Compiler un document de contexte personnalisé et cité :
tesserae context "How does the parser handle arXiv IDs?" --budget 32000 -o context.md

tesserae serve --port 8765    # parcourir le graphe et le wiki localement
```

Les fonctionnalités basées sur LLM utilisent par défaut les CLI `codex` / `claude` via OAuth —
**aucune clé API requise** pour le parcours habituel. Voir
[docs/quickstart.md](docs/quickstart.md) et
[docs/installation.md](docs/installation.md).

<details>
<summary><strong><code>tesserae: command not found</code> après l'installation ? Problèmes Linux ?</strong></summary>

La solution la plus fiable sur toutes les plateformes est [`pipx`](https://pipx.pypa.io/) :

```bash
# macOS : brew install pipx · Ubuntu/Debian : sudo apt install pipx
pipx ensurepath          # ajoute ~/.local/bin au PATH ; ouvrez un nouveau terminal ensuite
pipx install tesserae
```

Problèmes courants sur Ubuntu avec `pip install tesserae` :

| Erreur | Cause | Solution |
|---|---|---|
| `error: externally-managed-environment` | PEP 668 — Python système verrouillé | Utilisez `pipx` (ci-dessus) ou un venv |
| `command not found` après `pip install --user …` | `~/.local/bin` absent du `PATH` | `echo 'export PATH=$HOME/.local/bin:$PATH' >> ~/.bashrc && source ~/.bashrc` |
| `ModuleNotFoundError` sur anciennes distros | `python3` système < 3.10 | `sudo apt install python3.11 python3.11-venv`, puis installez avec `python3.11 -m pip` |

</details>

<details>
<summary><strong>GIFs de démonstration</strong> — chaque étape du démarrage rapide sur le corpus de démo de 135 documents inclus</summary>

<details>
<summary>1. Configuration — pointer vers un répertoire de recherche, obtenir un scaffold de wiki de projet</summary>
<br/>
<img src="docs/screencasts/setup.gif" alt="tesserae init --source ./research s'exécutant en mode non interactif et créant .tesserae/" width="100%" />
</details>

<details>
<summary>2. Compilation + construction du site — déterministe, sans appels LLM</summary>
<br/>
<img src="docs/screencasts/compile.gif" alt="tesserae compile suivi de tesserae export site, émettant graph.json et l'arborescence du site statique" width="100%" />
</details>

<details>
<summary>3. Ask — interroger le wiki compilé depuis le CLI</summary>
<br/>
<img src="docs/screencasts/ask.gif" alt="tesserae ask --backend wiki retournant les 3 premiers résultats avec score, type et relations sortantes" width="100%" />
</details>

Reconstruisez n'importe quel GIF avec `vhs docs/screencasts/<name>.tape`.

</details>

## Commandes du quotidien

Exécutez `tesserae --help` pour la liste complète groupée, `tesserae <cmd> --help` pour les options.

| Commande | Ce qu'elle fait |
|---|---|
| `tesserae init` | Assistant de configuration → `.tesserae/config.json`. `--yes` non interactif, `--bare` minimal. |
| `tesserae compile` | Reconstruire le graphe de connaissances et tous les artefacts. `compile <paths>` ingère des fichiers supplémentaires ponctuellement. |
| `tesserae ingest <file\|url>` | Intégrer un seul document ou une page web dans la base de connaissances sans recompilation complète (chemin rapide incrémental). |
| `tesserae context "<query>"` | **Compilateur de contexte à la demande** : document de contexte cité via expansion PPR sous `--budget` ; `--synthesize` ajoute un résumé LLM. |
| `tesserae ask "<question>"` | Interroger la base de connaissances compilée (`--scope all-registered` couvre tous les projets). |
| `tesserae engine` | Démon de rafraîchissement supervisé pour le projet courant : surveillance, debounce, recompilation. |
| `tesserae engine --all` | **Mode flotte** : un processus maintient à jour *tous* les projets enregistrés — rechargement à chaud du registre, limitation avec `--compile-slots`. |
| `tesserae refresh` | Pipeline ponctuel : importer de nouvelles sessions → compiler → synchroniser le vault. |
| `tesserae sessions discover --import` | Trouver et importer l'historique des sessions locales Claude Code / Codex pour ce projet. |
| `tesserae export site` | Construire le site statique (`--deploy`, `--watch`). |
| `tesserae serve` | Servir le site localement avec le widget de requête intégré (`/api/ask`). |
| `tesserae projects …` | Registre multi-projets : `register`, `list`, `activate`, `mcp-config`. |
| `tesserae integrations refresh …` | Relancer les outils complémentaires (Understand-Anything, RAG-Anything). |

## Maintien automatique à jour

Le moteur est ce qui rend la base de connaissances *auto-améliorante* plutôt qu'une
compilation ponctuelle :

```bash
# Un projet : surveiller les sources + sessions en direct, recompiler à chaque changement.
tesserae engine

# Tous les projets enregistrés, un seul processus (v0.8.0) :
tesserae engine --all --compile-slots 1
```

Le mode flotte se réconcilie avec `~/.tesserae/registry.json` toutes les 10 s —
l'enregistrement ou la suppression d'un projet prend effet sans redémarrage — et
sérialise les compilations entre projets pour que l'extraction LLM concurrente ne
sature jamais les limites de débit du compte. La première exécution balaie l'historique
des sessions une seule fois (indiqué dans le journal) ; les redémarrages reprennent
depuis un point de départ persisté.

## Ce que vous obtenez après la compilation

```text
.tesserae/
  graph.json              # nœuds/arêtes typés (la base de connaissances)
  sqlite.db               # entrepôt de graphe interrogeable
  markdown_projection/    # pages wiki lisibles par les humains
  obsidian_vault/         # prêt à déposer dans Obsidian
  site/                   # site statique (vue graphe + wiki + recherche)
  harness_sessions/       # mémoire de sessions Claude/Codex importée
  agent_harness/          # configuration de contexte par agent (Claude/Codex/Gemini/...)
  cognee_bundle/          # JSONL prêt pour l'ingestion Cognee
  config.json · manifest.json · report.md · …
```

## Serveur MCP

`tesserae projects mcp-config` affiche une entrée de serveur pour Claude Code, Codex ou
tout client MCP. Outils principaux :

- **`compile_context`** — document de contexte personnalisé et cité pour une requête ou des nœuds de départ
  (déterministe sauf si `synthesize=true`), s'appuyant sur `graph_ppr`.
- **Graphe + wiki** : `search_nodes`, `node_context`, `graph_summary`,
  `wiki_page`, `raw_source`, `timeline`, `search_facts`, `lint_report`, `ask`.
- **Mémoire de sessions** : `list_sessions`, `find_session_findings`,
  `fresh_insights` (classé par décroissance, dédupliqué).
- **Registre** : `list_projects`, `register_project`, `activate_project`.

## Multi-projets

Un registre dans `~/.tesserae/registry.json` résout les noms de projets partout —
CLI, MCP et moteur de flotte :

```bash
tesserae projects register /path/to/my-project --name myproj
tesserae projects activate myproj
tesserae ask "..." --scope all-registered        # couvrir tous les projets
```

Le Markdown d'un projet peut établir un lien profond vers un nœud d'un autre via
`wiki://<alias>/<kind>/<slug>` ; à la compilation, ces liens deviennent des nœuds
passerelles dans la vue graphe. Voir la [documentation](docs/) pour les détails.

## Intégrations (toutes optionnelles)

- **Plugin Claude Code** — slash commands, hooks de session, skill et auto-enregistrement MCP
  en un seul `/plugin install`.
  [docs/integrations/claude-code-plugin.md](docs/integrations/claude-code-plugin.md)
- **Graphe de sessions** — conversations Claude Code / Codex → nœuds Insight / Decision /
  Question / TODO, reliés aux documents qu'ils ont touchés. Aucune clé API requise.
  [docs/integrations/sessions.md](docs/integrations/sessions.md)
- **Understand-Anything** — ingestion du graphe de connaissances du code.
  [docs/integrations/understand-anything.md](docs/integrations/understand-anything.md)
- **RAG-Anything** — ingestion multimodale (PDF/Office/images via
  MinerU/Docling) et backend de questions LightRAG.
  [docs/integrations/rag-anything.md](docs/integrations/rag-anything.md)
- **Cognee** — backend mémoire graphe+vecteur ; la compilation écrit toujours un
  bundle prêt pour Cognee ; le cognify en temps d'exécution est de type best-effort.
- **Obsidian** — synchronisation bidirectionnelle du vault avec overlay des modifications utilisateur.
  [docs/integrations/obsidian.md](docs/integrations/obsidian.md)

## Comparaison

<details>
<summary>Matrice de fonctionnalités par rapport à Quartz, Logseq, Cognee, Foam</summary>

| Fonctionnalité | Tesserae | Quartz | Logseq | Cognee | Foam |
|---|---|---|---|---|---|
| Sortie HTML statique | oui | oui | partiel (export) | non | partiel (publish) |
| Vue graphe intégrée | oui | oui | oui | oui (UI séparée) | oui (VSCode) |
| Schéma de nœuds typé | oui (41 types) | non | partiel (tags) | oui | non |
| Extraction de concepts depuis les sources | oui (LLM) | non | non | oui | non |
| Ingestion multimodale (PDF/image) | oui (via RAG-Anything) | non | partiel (embeds) | oui | non |
| Ingestion de graphe de code | oui | non | non | partiel | non |
| Serveur MCP | oui | non | non | oui | non |
| Compilateur de contexte cité à la demande | oui (PPR + budget) | non | non | non | non |
| Surveillance des sessions en direct → graphe | oui | non | non | non | non |
| Registre multi-projets | oui | non | oui (graphes) | partiel | non |
| Démon de flotte multi-projets | oui | non | non | non | non |
| Fonctionne sans clé API (OAuth) | oui | n/a | n/a | non | n/a |
| Compilation déterministe octet par octet | oui | oui | n/a | non | n/a |
| Édition en direct | non | partiel | oui | n/a | oui |
| Collaboration en temps réel | non | non | oui (DB beta) | non | non |

</details>

Tesserae privilégie la compilation depuis les sources plutôt que l'édition en direct. Si vous
souhaitez éditer des notes dans une interface graphique, utilisez Logseq ou Obsidian. Si vous
voulez un outil de compilation *et un moteur en direct* pour votre graphe de connaissances,
c'est ce projet.

**Utilisez-le si** vous souhaitez un graphe de connaissances durable et inspectable sur les
sources textuelles d'un projet, un serveur MCP local ancré dans vos propres fichiers, ou des
bundles propres pour Cognee/Obsidian sans écrire de code de liaison.

**Passez votre chemin si** vous n'avez besoin que de recherche vectorielle sur un petit
répertoire, souhaitez un wiki hébergé avec interface d'édition, ou attendez un agent
«posez n'importe quelle question» clé en main — Tesserae construit le substrat ; vous le
câblez à l'agent de votre choix.

## Authentification et fournisseurs LLM

Le parcours habituel n'utilise **aucune clé API** :

- **Codex CLI** (par défaut) et **Claude Code CLI** via OAuth, avec
  rotation multi-comptes.
- **Embeddings** : la récupération hybride native utilise une voie sémantique hors ligne sans
  torch via `pip install "tesserae[semantic]"` (`model2vec`). Les backends Cognee/RAG-Anything
  utilisent par défaut un fournisseur déterministe ; passez à Ollama ou tout endpoint compatible
  OpenAI pour un meilleur rappel.

`ANTHROPIC_API_KEY` / `OPENAI_API_KEY` sont utilisées si elles sont présentes, jamais obligatoires.

## État et limitations

Version actuelle : voir les [notes de version](docs/release-notes/). Limitations connues :

- Les premières compilations sur de grands corpus (des milliers de fichiers) prennent quelques
  minutes ; le temps de compilation évolue de manière approximativement linéaire. La compilation
  incrémentale (`--changed-only`) existe mais est expérimentale et désactivée par défaut.
- Sans l'extra `semantic`, la récupération hybride se dégrade en un stub non sémantique
  (avec un avertissement visible).
- La vision RAG-Anything (description d'images) n'est pas encore connectée de bout en bout.
- Le cognify en temps d'exécution de Cognee est de type best-effort : les fournisseurs manquants
  sont journalisés et ignorés, jamais fatals.
- L'ensemble d'outils MCP est stable ; le schéma du graphe peut encore gagner de nouveaux types de nœuds.

## Structure du projet

```text
tesserae/        # le package (CLI, compilateur, moteur, serveur MCP, adaptateurs)
docs/            # documentation anglaise + docs/i18n/ pour les sept autres langues
ontology/        # schémas de nœuds/arêtes contre lesquels le compilateur valide
prompts/         # prompts d'extraction et de synthèse
tests/           # suite pytest
evals/           # harnais d'évaluation de la qualité du graphe
examples/        # corpus de démo utilisé par les screencasts
```

## Documentation localisée

[한국어](./README.ko.md) ·
[中文](./README.zh.md) ·
[日本語](./README.ja.md) ·
[Русский](./README.ru.md) ·
[Español](./README.es.md) ·
[Français](./README.fr.md) ·
[Deutsch](./README.de.md)

La documentation longue est disponible dans `docs/i18n/`.

## Licence

MIT. Voir [LICENSE](LICENSE).
