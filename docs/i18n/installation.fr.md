# Installation

<!-- translations:start -->
<p align="center"><a href="../installation.md">English</a> · <a href="installation.ko.md">한국어</a> · <a href="installation.zh.md">中文</a> · <a href="installation.ja.md">日本語</a> · <a href="installation.ru.md">Русский</a> · <a href="installation.es.md">Español</a> · <a href="installation.fr.md">Français</a> · <a href="installation.de.md">Deutsch</a></p>
<!-- translations:end -->
LLM-Wiki est publié sur PyPI et expose des commandes shell afin que les utilisateurs n'aient pas à lancer `python3 -m llm_wiki.cli` manuellement.

## Installer depuis PyPI (recommandé)

```bash
pip install llm-research-wiki
```

C'est tout. `pip` enregistre trois scripts console dans votre `PATH` :

```bash
llm_wiki --help
llm-wiki --help
llm_wiki_mcp --help
```

La commande canonique dans la documentation est `llm_wiki` ; `llm-wiki` (avec un tiret) est un alias. `llm_wiki_mcp` démarre le serveur MCP.

> **pipx convient aussi.** Si vous préférez garder les outils CLI dans leurs propres venv isolés :
> ```bash
> pipx install llm-research-wiki
> ```

## Mettre à niveau

```bash
pip install --upgrade llm-research-wiki
```

## Intégrations facultatives

La wheel par défaut est volontairement légère. L'assistant de configuration peut installer les composants companion/runtime plus lourds uniquement lorsque vous le demandez :

```bash
# Understand Anything companion graph + Cognee runtime memory
llm_wiki project setup \
  --with-understand-anything \
  --install-understand-anything \
  --understand-anything-platform codex \
  --run-cognee \
  --install-cognee
```

Les installations manuelles de paquets restent disponibles pour les flux avancés :

```bash
pip install kuzu cognee graphiti-core
```

- `kuzu` — persistance de graphe Kuzu.
- `cognee` — workflows runtime Cognee add/cognify ; la configuration stocke `{python} -m pip install cognee` et réessaie une fois si Cognee manque.
- Understand Anything — installé via l'installateur upstream lorsque `--install-understand-anything` est sélectionné ; LLM-Wiki stocke un refresh wrapper géré au lieu de demander aux utilisateurs d'inventer une commande shell.
- `graphiti-core` — synchronisation live Graphiti/Neo4j. `export-graphiti` et `sync-graphiti --dry-run` fonctionnent sans lui.

Le chemin de synthèse basé sur Anthropic utilise un marqueur extras :

```bash
pip install "llm-research-wiki[synthesis-llm]"
```

## Installer depuis la source (pour les contributeurs)

Si vous voulez modifier la base de code, installez plutôt le checkout editable :

```bash
git clone https://github.com/ca1773130n/LLM-Wiki.git
cd LLM-Wiki
pip install -e .
```

Un installateur pratique est également inclus : il clone, crée un `.venv` local au projet, exécute `pip install -e .` et dépose les wrappers dans `~/.local/bin` :

```bash
# Quick: clone + install in one shot
curl -fsSL https://raw.githubusercontent.com/ca1773130n/LLM-Wiki/main/scripts/install.sh | bash

# From an existing checkout
./scripts/install.sh --dir "$PWD"
```

Options utiles (`./scripts/install.sh --help`) :

| Option | Objectif |
| --- | --- |
| `--dir PATH` | Installer ou mettre à jour le checkout à `PATH`. |
| `--branch NAME` | Installer une branche spécifique. |
| `--repo URL` | Remplacer l'URL du dépôt Git. Utile pour les forks ou les smoke tests locaux. |
| `--bin-dir PATH` | Écrire les wrappers de commande ailleurs que dans `~/.local/bin`. |
| `--no-venv` | Installer dans l'environnement Python courant au lieu de créer `.venv`. |
| `--skip-shell-config` | Éviter de modifier `.zshrc` / `.bashrc`. |

Si `--skip-shell-config` a été utilisé, redémarrez le shell ou exécutez :

```bash
export PATH="$HOME/.local/bin:$PATH"
```

## Vérifier l'installation

```bash
llm_wiki project init --help
llm_wiki project compile --help
llm_wiki project build-site --help
```
