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

Tesserae est un **moteur de contexte**. Pointez-le vers un répertoire contenant du Markdown, des fichiers source et, en option, des PDF/documents Office/images : il reconstruit depuis votre projet une base de connaissances *qui s’améliore d’elle-même* — un graphe de connaissances typé — et tend aux agents le contexte dont ils ont besoin. Il repose sur trois piliers :

1. **Surveillance des sessions** — observe les sessions d’agent/de travail en direct et capture décisions, insights et questions ouvertes comme des nœuds de première classe du graphe au moment où ils surviennent.
2. **Ingestion de connaissances autonome et proactive** — un daemon de refresh supervisé fusionne les éditions et recompile, et un sidecar d’auto-amélioration renforce les trouvailles récurrentes et remplace (supersede) celles qui sont périmées, si bien que la base continue de s’améliorer toute seule.
3. **Contexte à la demande** — la fonctionnalité phare, le **Compilateur de Contexte à la Demande**, assemble pour toute requête ou nœud-graine un document de contexte sur mesure et cité (expansion Personalized PageRank dans un budget de caractères), plus des artefacts demandés par l’utilisateur.

Le graphe typé, le vault Obsidian et le site statique sont des *projections* de cette base de connaissances. Tesserae produit aussi des artefacts portables — projection Markdown, bundle prêt pour Cognee, agent harness, et un serveur MCP que vous pouvez brancher sur Claude Code, Codex ou n’importe quel client MCP. C’est une étape de build et un moteur vivant pour le contexte de projet, pas un service hébergé.

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

Projet de recherche / agent-tooling en évolution (actuellement v0.5.0). Limitations connues :

- Le temps de compilation croît à peu près linéairement avec la taille du corpus. La première compilation sur de gros arbres Markdown (milliers de fichiers) peut prendre plusieurs minutes.
- La recherche native utilise par défaut une vraie voie sémantique : installez l’extra `semantic` (`pip install "tesserae[semantic]"`) pour tirer `model2vec` (vecteurs statiques sans torch, ~8 Mo de `potion-base-8M` au premier usage). Sans lui, la recherche hybride/embedding se dégrade en un stub non sémantique de hash-bucket et émet un avertissement bien visible. Pour les backends Cognee/RAG-Anything, le provider d’embedding reste `deterministic` par défaut ; passez à `ollama` (par exemple `qwen3-embedding:0.6b`) ou à un endpoint compatible OpenAI pour un meilleur recall — voir [docs/integrations/rag-anything.md](docs/integrations/rag-anything.md).
- La compilation incrémentale (`--changed-only`) est livrée mais reste expérimentale et DÉSactivée par défaut ; les recompilations complètes restent le chemin pris en charge.
- Le support vision pour RAG-Anything (extraction du contenu des images) n’est pas encore connecté de bout en bout. Les fichiers image sont parsés structurellement mais pas décrits.
- Le runtime cognify de Cognee est best-effort : providers manquants, clés API payantes ou pannes réseau sont journalisés et ignorés plutôt que d’interrompre le build.
- Le serveur MCP expose un ensemble stable d’outils, mais le schéma sous-jacent du graphe peut encore être enrichi.

## Démarrage rapide

Nécessite Python 3.9 ou plus. RAG-Anything nécessite Python 3.10 ou plus si vous l’activez.

```bash
pip install tesserae          # pour de vrais embeddings, ajoutez [semantic] : pip install "tesserae[semantic]"

cd /path/to/my-project
tesserae init --yes
tesserae compile
tesserae ask "Where is Mermaid rendering implemented?"

# Contexte à la demande : compile un document de contexte sur mesure et cité pour une requête.
tesserae context "How does the parser handle arXiv IDs?" --budget 32000 -o context.md

tesserae serve --port 8765
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

Après `compile`, faites `ls .tesserae/` pour vérifier ce qui a été écrit.

## Vue d’ensemble de la CLI

Commandes au quotidien. Lancez `tesserae <subcommand> --help` pour la liste complète des flags.

| Commande | Rôle |
|---|---|
| `tesserae init` | Assistant interactif. Écrit `.tesserae/config.json`. Passez `--yes` pour une exécution non interactive qui accepte les valeurs par défaut détectées (toutes les intégrations optionnelles DÉSactivées), ou `--bare` pour sauter l’assistant et écrire un workspace minimal. |
| `tesserae compile` | Lit les sources configurées, déclenche les refresh des outils complémentaires et écrit tous les artefacts sous `.tesserae/`. `--changed-only` active le rebuild incrémental expérimental (DÉSactivé par défaut). `compile <paths>` fait une ingestion ad-hoc de chemins markdown supplémentaires. |
| `tesserae context "<requête>"` | **Compilateur de Contexte à la Demande.** Compile pour une requête (ou des `--seeds` explicites) un document de contexte sur mesure et cité via une expansion Personalized PageRank (`--depth`, défaut 2) dans un `--budget` (défaut 32000 caractères ; `<=0` = illimité). `--synthesize` ajoute un résumé LLM ; `-o` écrit dans un fichier. |
| `tesserae engine` (alias `daemon`) | Exécute le daemon de refresh supervisé : surveille les sources, fusionne les rafales d’éditions (`--debounce`) et recompile automatiquement. `--once` exécute un unique cycle de vidange déterministe. |
| `tesserae refresh` | Pipeline in-process en une passe : import des nouvelles sessions, compile, synchronisation du vault. |
| `tesserae export site` | Construit le frontend statique dans `.tesserae/site/`. `--deploy` publie ; `--watch` reconstruit à chaque changement. |
| `tesserae serve --port 8765` | Sert le site statique en local (le construit automatiquement s’il manque). |
| `tesserae integrations refresh understand-anything` | Exécute le wrapper de refresh géré par Tesserae pour Understand Anything. |
| `tesserae integrations refresh raganything --parser mineru` | Re-parse les sources non-code (PDF, Office, images) via RAG-Anything. |
| `tesserae ask "<question>"` | Interroge le backend configuré (`auto`/`raganything`/`cognee`/`wiki`). |
| `tesserae projects mcp-config` | Affiche un fragment de configuration de serveur MCP à coller dans Claude Code, Codex ou Hermes. |
| `tesserae projects register <path> --name <alias>` | Enregistre un projet dans le registry partagé. |
| `tesserae projects list` / `tesserae projects activate <name>` | Liste les projets enregistrés ; fixe l’actif. |
| `tesserae ask "<question>" [--wiki <name>]` | Commande ask de premier niveau, qui résout via le registry. |

## Intégrations

Toutes les intégrations sont opt-in. Aucune n’est requise pour utiliser Tesserae sur un projet Markdown/code classique.

- **Plugin Claude Code** — commandes slash (`/tesserae:compile`, `/tesserae:ask "<question>"`, `/tesserae:refresh`, `/tesserae:status`, …), quatre hooks (statut SessionStart / auto-compilation SessionEnd / recompilation incrémentielle PostToolUse opt-in / portail de confirmation PreToolUse pour grands graphes), une compétence `using-tesserae` et auto-enregistrement MCP — le tout en un seul `/plugin install`. Voir [docs/integrations/claude-code-plugin.md](docs/integrations/claude-code-plugin.md).
- **Graphe de sessions (Pilier 1)** — transforme vos conversations Claude Code / Codex à propos du projet en nœuds de première classe dans le graphe (Insight / Decision / Question / TODO / Hypothesis / Takeaway), liés aux documents qui sont apparus. Exécutez `tesserae sessions discover --import` une fois, puis chaque `tesserae compile` importe les nouvelles sessions ; `tesserae engine` les surveille en direct et les intègre en continu. La passe structurelle est gratuite ; la passe LLM s'exécute automatiquement lorsque la CLI `claude` est connectée — **aucune clé API requise**. Voir [docs/integrations/sessions.md](docs/integrations/sessions.md).
- **Understand Anything** — projet séparé ([Lum1104/Understand-Anything](https://github.com/Lum1104/Understand-Anything)) qui produit un graphe de connaissances du code dans `.understand-anything/knowledge-graph.json`. Activé par `--with-understand-anything`. Tesserae stocke un wrapper de refresh géré, donc `compile` maintient le graphe à jour. Voir [docs/integrations/understand-anything.md](docs/integrations/understand-anything.md).
- **RAG-Anything** — ingestion multimodale ([HKUDS/RAG-Anything](https://github.com/HKUDS/RAG-Anything)) pour PDF, documents Office et images via MinerU/Docling/PaddleOCR. Activé par `--with-raganything`. Sert aussi de backend de questions runtime (LightRAG). Nécessite Python 3.10+. Voir [docs/integrations/rag-anything.md](docs/integrations/rag-anything.md).
- **Cognee** — backend mémoire graphe + vecteur. Activé par `--run-cognee --install-cognee`. Le compile normal écrit toujours `.tesserae/cognee_bundle/` ; la passe runtime `cognify` est best-effort et ne s’exécute que si on l’active explicitement.

## Registry multi-projets

Un registry persistant à `~/.tesserae/registry.json` permet à la CLI `ask` de premier niveau et au serveur MCP de résoudre les noms de projet vers leurs racines sans passer `--project` à chaque appel.

```bash
tesserae projects register /path/to/my-project --name myproj
tesserae projects activate myproj
tesserae ask "Where is the parser entry point?"
```

Le serveur MCP lit le même registry, donc les clients MCP peuvent appeler `list_projects`, `activate_project` et `ask` sur n’importe quel wiki enregistré.

## MCP

`tesserae projects mcp-config` affiche une entrée de serveur à coller dans Claude Code, Codex ou n’importe quel client compatible MCP. Le serveur expose des outils dont `schema`, `graph_summary`, `search_nodes`, `node_context`, `search_facts`, `timeline`, `wiki_page`, `raw_source`, `lint_report`, `ask`, `embedding_status`. Le phare de la v0.5.0 est **`compile_context`** — il renvoie un document de contexte sur mesure et cité pour une requête ou des nœuds-graines (déterministe sauf si `synthesize=true`), adossé à **`graph_ppr`** (Personalized PageRank sur le graphe typé). Les outils de mémoire de sessions et d’auto-amélioration complètent l’ensemble : `list_sessions`, `find_session_findings`, `find_code_symbol_mentions`, `list_communities` et `fresh_insights` (trouvailles de session classées par décroissance à la Ebbinghaus, les quasi-doublons remplacés étant filtrés). Les outils de registry `list_projects` / `register_project` / `activate_project` / `unregister_project` résolvent les noms de projet via le même registry que la CLI.

## Authentification et providers LLM

Le chemin courant ne demande pas de clé API :

- **Codex CLI** (par défaut) via OAuth. `--raganything-llm-provider codex` est la valeur par défaut ; le mode `codex_cognify` de Cognee patche le client LLM de Cognee vers la CLI Codex.
- **Claude Code CLI** via OAuth. Pour les requêtes runtime de RAG-Anything, positionnez `--raganything-llm-provider claude`. Les configurations multi-comptes utilisent `--raganything-claude-config-dir ~/.claude` (Tesserae exporte `CLAUDE_CONFIG_DIR` avant chaque appel).
- **Embeddings.** La recherche hybride native utilise une vraie voie sémantique, hors-ligne et sans torch, via l’extra `semantic` (`model2vec` / `potion-base-8M`). Pour le backend Cognee, les embeddings utilisent par défaut un provider déterministe en-process ; passez à Ollama via `--cognee-embedding-provider ollama --cognee-ollama-embedding-model qwen3-embedding:0.6b`, ou branchez des endpoints compatibles OpenAI — les deux sont documentés dans les pages d’intégration.

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
[Español](./README.es.md) ·
[Deutsch](./README.de.md)

La documentation longue est dupliquée sous `docs/i18n/` et `docs/i18n/integrations/`.

## Licence

MIT. Voir [LICENSE](LICENSE).
