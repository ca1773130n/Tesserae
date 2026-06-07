# Installation

<!-- translations:start -->
<p align="center"><a href="../installation.md">English</a> · <a href="installation.ko.md">한국어</a> · <a href="installation.zh.md">中文</a> · <a href="installation.ja.md">日本語</a> · <a href="installation.ru.md">Русский</a> · <a href="installation.es.md">Español</a> · <a href="installation.fr.md">Français</a> · <a href="installation.de.md">Deutsch</a></p>
<!-- translations:end -->
Tesserae est publié sur PyPI et expose des commandes shell afin que les utilisateurs n'aient pas à lancer `python3 -m tesserae.cli` manuellement.

## Installer depuis PyPI (recommandé)

```bash
pip install tesserae
```

C'est tout. `pip` enregistre deux scripts console dans votre `PATH` :

```bash
tesserae --help
tesserae_mcp --help
```

La commande canonique dans la documentation est `tesserae`. `tesserae_mcp` démarre le serveur MCP (qui expose désormais l'outil `compile_context` à la demande — voir le Démarrage rapide).

> **pipx convient aussi.** Si vous préférez garder les outils CLI dans leurs propres venv isolés :
> ```bash
> pipx install tesserae
> ```

## Mettre à niveau

```bash
pip install --upgrade tesserae
```

## Intégrations facultatives

La wheel par défaut est volontairement légère. L'assistant de configuration peut installer les composants companion/runtime plus lourds uniquement lorsque vous le demandez :

```bash
# Understand Anything companion graph + Cognee runtime memory
tesserae project setup \
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
- Understand Anything — installé via l'installateur upstream lorsque `--install-understand-anything` est sélectionné ; Tesserae stocke un refresh wrapper géré au lieu de demander aux utilisateurs d'inventer une commande shell.
- `graphiti-core` — synchronisation live Graphiti/Neo4j. `export-graphiti` et `sync-graphiti --dry-run` fonctionnent sans lui.

Le chemin de synthèse basé sur Anthropic utilise un marqueur extras :

```bash
pip install "tesserae[synthesis-llm]"
```

Les véritables embeddings sémantiques (la voie de récupération par défaut depuis la v0.5.0) sont fournis via l'extra `semantic` :

```bash
pip install "tesserae[semantic]"
```

Cela installe `model2vec` et télécharge un modèle statique léger, hors ligne et sans torch (environ 8 Mo de `potion-base-8M`, téléchargé une seule fois lors de la première utilisation). Sans lui, la récupération hybride/par embeddings retombe sur un stub non sémantique à seaux de hachage et émet un avertissement bien visible ; l'installation de cet extra est donc recommandée à quiconque utilise `project ask`, `project context` ou l'outil MCP `compile_context`.

## Installer depuis la source (pour les contributeurs)

Si vous voulez modifier la base de code, installez plutôt le checkout editable :

```bash
git clone https://github.com/ca1773130n/Tesserae.git
cd Tesserae
pip install -e .
```

Un installateur pratique est également inclus : il clone, crée un `.venv` local au projet, exécute `pip install -e .` et dépose les wrappers dans `~/.local/bin` :

```bash
# Quick: clone + install in one shot
curl -fsSL https://raw.githubusercontent.com/ca1773130n/Tesserae/main/scripts/install.sh | bash

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
tesserae project init --help
tesserae project compile --help
tesserae project build-site --help
```
