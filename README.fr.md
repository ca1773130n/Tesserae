# Tesserae

<p align="center">
  <img src="docs/assets/tesserae-graph-view.png" alt="Vue graphe de Tesserae" width="100%" />
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

[Démo en direct](https://ca1773130n.github.io/Tesserae) · [Documentation](docs/) · [Configuration MCP](docs/i18n/integrations/mcp.fr.md) · [Export Obsidian](docs/i18n/integrations/obsidian.fr.md)

Tesserae est un compilateur de mémoire de projet. Pointez-le vers un répertoire contenant du Markdown, des fichiers source et, en option, des PDF/documents Office/images : il extrait un graphe de connaissances typé, écrit un wiki interrogeable et produit des artefacts portables — projection Markdown, bundle prêt pour Cognee, agent harness, et un serveur MCP que vous pouvez brancher sur Claude Code, Codex ou n’importe quel client MCP. C’est une étape de build pour le contexte de projet, pas un service hébergé.

## Quand l’utiliser (et quand ne pas l’utiliser)

À utiliser si :

- Vous voulez un graphe de connaissances durable et inspectable sur les sources majoritairement textuelles d’un seul projet (documentation, code, notes de recherche).
- Vous voulez un serveur MCP local qui répond à partir de vos propres fichiers.
- Vous voulez alimenter Cognee avec un bundle propre, ou déposer une projection Markdown dans Obsidian, sans écrire vous-même le code de liaison.

À éviter si :

- Vous voulez seulement une recherche vectorielle sur un petit répertoire — `ripgrep` plus une bibliothèque d’embeddings est plus simple.
- Vous voulez un wiki hébergé avec une UI d’édition. Le site statique généré ici est en lecture seule.
- Vous attendez des embeddings sémantiques précis prêts à l’emploi. L’embedding par défaut de RAG-Anything est déterministe (voir [Statut](#statut)).
- Vous attendez un agent « demande n’importe quoi » clé en main. Ce projet construit le socle ; le branchement à l’agent de votre choix reste à votre charge.

## Statut

Projet de recherche / agent-tooling en évolution. Limitations connues :

- Le temps de compilation croît à peu près linéairement avec la taille du corpus. La première compilation sur de gros arbres Markdown (milliers de fichiers) peut prendre plusieurs minutes.
- Le provider d’embedding par défaut de RAG-Anything est `deterministic`. Il est reproductible et sans dépendance, mais son rappel sémantique est limité. Passez à `ollama` (par exemple `qwen3-embedding:0.6b`) ou à un endpoint compatible OpenAI pour un meilleur recall — voir [docs/integrations/rag-anything.md](docs/integrations/rag-anything.md).
- Le support vision pour RAG-Anything (extraction du contenu des images) n’est pas encore connecté de bout en bout. Les fichiers image sont parsés structurellement mais pas décrits.
- Le runtime cognify de Cognee est best-effort : providers manquants, clés API payantes ou pannes réseau sont journalisés et ignorés plutôt que d’interrompre le build.
- Le serveur MCP expose un ensemble stable d’outils, mais le schéma sous-jacent du graphe peut encore être enrichi.

## Démarrage rapide

Nécessite Python 3.9 ou plus. RAG-Anything nécessite Python 3.10 ou plus si vous l’activez.

```bash
pip install tesserae

cd /path/to/my-project
tesserae project setup
tesserae project compile
tesserae project ask "Where is Mermaid rendering implemented?"
tesserae project build-site && tesserae project serve --port 8765
```

L’assistant de setup détecte les sources courantes (`README.md`, `docs/`, `src/`, `data/`) et écrit `.tesserae/config.json`. Les fonctions appelant un LLM utilisent par défaut la CLI `codex` via OAuth, donc aucune clé API n’est nécessaire dans le chemin courant. Versions plus complètes dans [docs/quickstart.md](docs/quickstart.md) et [docs/installation.md](docs/installation.md).

> [!tip]
> **`tesserae: command not found` après l'installation ?** `pip` a placé le binaire à un endroit que votre shell ne cherche pas. La solution la plus fiable sur **toute plateforme** est [`pipx`](https://pipx.pypa.io/) — il installe les outils CLI dans des venvs isolés et gère votre `PATH` automatiquement :
>
> ```bash
> # macOS — `brew install pipx`
> # Ubuntu / Debian — `sudo apt install pipx`
> # autres — `python3 -m pip install --user pipx`
> pipx ensurepath          # ajoute ~/.local/bin au PATH ; ouvrez un nouveau shell ensuite
> pipx install tesserae
> ```
>
> **Ubuntu 23.04+** problèmes courants avec un simple `pip install tesserae` :
>
> | Erreur | Cause | Solution |
> |---|---|---|
> | `error: externally-managed-environment` | PEP 668 — le Python du système est verrouillé | Utilisez `pipx` (ci-dessus), ou `pip install --user --break-system-packages tesserae` (moche), ou un venv |
> | `tesserae: command not found` après `pip install --user …` | `~/.local/bin` n'est pas dans `PATH` | `echo 'export PATH=$HOME/.local/bin:$PATH' >> ~/.bashrc && source ~/.bashrc` |
> | `ModuleNotFoundError: pydantic` sur Ubuntu 20.04 | `python3` système est 3.8, tesserae nécessite ≥3.9 | `sudo apt install python3.11 python3.11-venv` puis `python3.11 -m pip install --user tesserae` |


## Ce que vous obtenez après compile

```text
.tesserae/
  config.json
  graph.json              # nœuds / arêtes typés
  manifest.json           # empreintes des sources (utilisé par --changed-only)
  sqlite.db               # store de graphe interrogeable
  temporal_facts.jsonl
  graphiti_episodes.jsonl
  report.md
  markdown_projection/    # pages wiki lisibles par un humain
  obsidian_vault/         # prêt à être déposé dans Obsidian
  agent_harness/          # configuration par agent (Claude/Codex/Gemini/Cursor/...)
  harness_sessions/       # mémoire des sessions Claude/Codex importées
  cognee_bundle/          # JSONL prêt pour ingest dans Cognee
  site/                   # site statique construit par build-site
  external/               # sorties des outils complémentaires (UA, RAG-Anything)
```

Après `project compile`, faites `ls .tesserae/` pour vérifier ce qui a été écrit.

## Vue d’ensemble de la CLI

Commandes au quotidien. Lancez `tesserae <subcommand> --help` pour la liste complète des flags.

| Commande | Rôle |
|---|---|
| `tesserae project setup` | Assistant interactif. Écrit `.tesserae/config.json`. Accepte `--with-understand-anything`, `--with-raganything`, `--run-cognee`, etc. |
| `tesserae project compile` | Lit les sources configurées, déclenche les refresh des outils complémentaires et écrit tous les artefacts sous `.tesserae/`. Utilisez `--changed-only` pour des rebuilds incrémentaux. |
| `tesserae project build-site` | Construit le frontend statique dans `.tesserae/site/`. |
| `tesserae project serve --port 8765` | Sert le site statique en local. |
| `tesserae project refresh-understand-anything` | Exécute le wrapper de refresh géré par Tesserae pour Understand Anything. |
| `tesserae project refresh-raganything --parser mineru` | Re-parse les sources non-code (PDF, Office, images) via RAG-Anything. |
| `tesserae project ask "<question>"` | Interroge le backend configuré (`auto`/`raganything`/`cognee`/`wiki`). |
| `tesserae project mcp-config` | Affiche un fragment de configuration de serveur MCP à coller dans Claude Code, Codex ou Hermes. |
| `tesserae wiki register <path> --name <alias>` | Enregistre un projet dans le registry partagé. |
| `tesserae wiki list` / `tesserae wiki activate <name>` | Liste les projets enregistrés ; fixe l’actif. |
| `tesserae ask "<question>" [--wiki <name>]` | Commande ask de premier niveau, qui résout via le registry. |

## Intégrations

Toutes les intégrations sont opt-in. Aucune n’est requise pour utiliser Tesserae sur un projet Markdown/code classique.

- **Understand Anything** — projet séparé ([Lum1104/Understand-Anything](https://github.com/Lum1104/Understand-Anything)) qui produit un graphe de connaissances du code dans `.understand-anything/knowledge-graph.json`. Activé par `--with-understand-anything`. Tesserae stocke un wrapper de refresh géré, donc `project compile` maintient le graphe à jour. Voir [docs/integrations/understand-anything.md](docs/integrations/understand-anything.md).
- **RAG-Anything** — ingestion multimodale ([HKUDS/RAG-Anything](https://github.com/HKUDS/RAG-Anything)) pour PDF, documents Office et images via MinerU/Docling/PaddleOCR. Activé par `--with-raganything`. Sert aussi de backend de questions runtime (LightRAG). Nécessite Python 3.10+. Voir [docs/integrations/rag-anything.md](docs/integrations/rag-anything.md).
- **Cognee** — backend mémoire graphe + vecteur. Activé par `--run-cognee --install-cognee`. Le compile normal écrit toujours `.tesserae/cognee_bundle/` ; la passe runtime `cognify` est best-effort et ne s’exécute que si on l’active explicitement.

## Registry multi-projets

Un registry persistant à `~/.tesserae/registry.json` permet à la CLI `ask` de premier niveau et au serveur MCP de résoudre les noms de projet vers leurs racines sans passer `--project` à chaque appel.

```bash
tesserae wiki register /path/to/my-project --name myproj
tesserae wiki activate myproj
tesserae ask "Where is the parser entry point?"
```

Le serveur MCP lit le même registry, donc les clients MCP peuvent appeler `list_projects`, `activate_project` et `ask` sur n’importe quel wiki enregistré.

## MCP

`tesserae project mcp-config` affiche une entrée de serveur à coller dans Claude Code, Codex ou n’importe quel client compatible MCP. Le serveur expose des outils dont `schema`, `graph_summary`, `search_nodes`, `node_context`, `search_facts`, `timeline`, `wiki_page`, `raw_source`, `lint_report`, `ask`, ainsi que les outils de registry `list_projects` / `register_project` / `activate_project` / `unregister_project`. Les outils qui nécessitent un projet précis résolvent via le même registry que la CLI.

## Authentification et providers LLM

Le chemin courant ne demande pas de clé API :

- **Codex CLI** (par défaut) via OAuth. `--raganything-llm-provider codex` est la valeur par défaut ; le mode `codex_cognify` de Cognee patche le client LLM de Cognee vers la CLI Codex.
- **Claude Code CLI** via OAuth. Pour les requêtes runtime de RAG-Anything, positionnez `--raganything-llm-provider claude`. Les configurations multi-comptes utilisent `--raganything-claude-config-dir ~/.claude` (Tesserae exporte `CLAUDE_CONFIG_DIR` avant chaque appel).
- **Embeddings** : provider déterministe en-process par défaut. Passez à Ollama via `--cognee-embedding-provider ollama --cognee-ollama-embedding-model qwen3-embedding:0.6b`, ou branchez des endpoints compatibles OpenAI — les deux sont documentés dans les pages d’intégration.

Si vous définissez `ANTHROPIC_API_KEY` ou `OPENAI_API_KEY`, les chemins correspondants les utilisent, mais ils ne sont pas requis.

## Structure du projet

```text
tesserae/        # le package (CLI, compilateur, serveur MCP, adapters)
docs/            # documentation anglaise + docs/i18n/ pour les six autres langues
ontology/        # schémas de nœud/arête validés par le compilateur
prompts/         # prompts d’extraction et de synthèse
scripts/         # scripts de maintenance
tests/           # suite pytest
evals/           # harnesses d’évaluation de qualité du graphe
data/            # notes de recherche d’exemple pour le self-dogfooding
```

## Documentation localisée

[English](./README.md) ·
[한국어](./README.ko.md) ·
[中文](./README.zh.md) ·
[日本語](./README.ja.md) ·
[Русский](./README.ru.md) ·
[Español](./README.es.md)

La documentation longue est dupliquée sous `docs/i18n/` et `docs/i18n/integrations/`.

## Licence

MIT. Voir [LICENSE](LICENSE).
