# Installation

<!-- translations:start -->
<p align="center"><a href="../installation.md">English</a> · <a href="installation.ko.md">한국어</a> · <a href="installation.zh.md">中文</a> · <a href="installation.ja.md">日本語</a> · <a href="installation.ru.md">Русский</a> · <a href="installation.es.md">Español</a> · <a href="installation.fr.md">Français</a> · <a href="installation.de.md">Deutsch</a></p>
<!-- translations:end -->
Tesserae est publié sur PyPI et expose des commandes shell pour que les utilisateurs n’aient pas à lancer `python3 -m tesserae.cli` manuellement.

## Installer depuis PyPI (recommandé)

```bash
pip install tesserae
```

C’est tout. `pip` enregistre deux scripts console sur votre `PATH` :

```bash
tesserae --help
tesserae_mcp --help
```

La commande canonique dans les docs est `tesserae`. `tesserae_mcp` démarre le serveur MCP (qui expose désormais l’outil à la demande `compile_context` — voir le Démarrage rapide).

> **pipx convient aussi.** Si vous préférez garder les outils CLI dans leurs propres venvs isolés :
> ```bash
> pipx install tesserae
> ```

## Mise à niveau

```bash
pip install --upgrade tesserae
```

## Configuration machine (une fois, tous les projets)

Configurez Tesserae une seule fois au lieu de par projet, et installez les
dépendances optionnelles en une commande :

```bash
# Interactive machine-wide setup — pick LLM provider/effort + which deps to install:
tesserae setup
# …or non-interactively (written to ~/.tesserae/config.json) + install everything:
tesserae setup --llm-provider codex --reasoning-effort medium --install all

# Just see / manage optional dependencies
tesserae config deps                     # show what's installed
tesserae config deps --install memex     # fast transcript search (needs cargo)
```

Dépendances optionnelles connues : **memex** (recherche rapide de transcriptions) et
**raganything**. Un `.tesserae/config.json` par projet
prime toujours sur ces valeurs globales (ordre de résolution : env → projet → global →
défaut intégré). `tesserae init` propose aussi d’installer memex lors d’une configuration interactive.

## Intégrations optionnelles (par projet)

La wheel par défaut est volontairement légère, et les backends de mémoire
optionnels sont **désactivés par défaut**. `tesserae init` est l’unique étape
d’intégration par projet — son assistant choisit le fournisseur LLM et les
sources détectées ; les pièces compagnons/runtime plus lourdes s’installent au
niveau machine via `tesserae setup --install …` (ou `tesserae config deps
--install …`) et s’activent par projet dans `.tesserae/config.json` :

```bash
# Machine-wide installs of the optional pieces:
tesserae setup --install raganything

# Then per project: enable what you want in .tesserae/config.json
#   memory_backends.raganything.enabled: true   (query via `tesserae query --backend raganything`)
```

Les installations manuelles de paquets restent disponibles pour les workflows avancés :

```bash
pip install kuzu graphiti-core
```

- `kuzu` — persistance de graphe Kuzu.
- RAG-Anything — installé via `pip install 'raganything[all]'` (`tesserae setup --install raganything`) ; Tesserae stocke un wrapper de rafraîchissement géré pour les exécutions du parseur multimodal.
- `graphiti-core` — synchronisation Graphiti/Neo4j en direct. `export graphiti` et `export graphiti --sync --dry-run` fonctionnent sans lui.

Le chemin de synthèse adossé à Anthropic utilise un marqueur extras :

```bash
pip install "tesserae[synthesis-llm]"
```

Les vrais embeddings sémantiques (la voie de récupération par défaut depuis la v0.5.0) sont livrés derrière l’extra `semantic` :

```bash
pip install "tesserae[semantic]"
```

Cela installe `model2vec` et télécharge un modèle statique léger, hors-ligne et sans torch (~8 Mo `potion-base-8M`, récupéré une fois au premier usage). Sans lui, la récupération hybride/par embeddings retombe sur un stub hash-bucket non sémantique et émet un avertissement bruyant, donc installer cet extra est recommandé pour quiconque utilise `tesserae ask`, `tesserae context` ou l’outil MCP `compile_context`.

Pour la pile multimodale RAG-Anything avec tous les parseurs préinstallés :

```bash
pip install 'tesserae[raganything-all]'
```

> **Prérequis système :** le parsing de `.doc/.docx/.ppt/.pptx/.xls/.xlsx` requiert LibreOffice sur l’hôte. Installez-le via le gestionnaire de paquets de votre plateforme (p. ex. `brew install --cask libreoffice`, `apt-get install libreoffice`) ; RAG-Anything saute les documents Office avec un avertissement quand LibreOffice est absent.

## Installer depuis les sources (pour les contributeurs)

Si vous voulez bidouiller le code, installez plutôt le checkout éditable :

```bash
git clone https://github.com/ca1773130n/Tesserae.git
cd Tesserae
pip install -e .
```

Un installeur de commodité est également fourni — il clone, crée un `.venv` local au projet, lance `pip install -e .`, et dépose les wrappers dans `~/.local/bin` :

```bash
# Quick: clone + install in one shot
curl -fsSL https://raw.githubusercontent.com/ca1773130n/Tesserae/main/scripts/install.sh | bash

# From an existing checkout
./scripts/install.sh --dir "$PWD"
```

Drapeaux utiles (`./scripts/install.sh --help`) :

| Option | Objet |
| --- | --- |
| `--dir PATH` | Installe ou met à jour le checkout à `PATH`. |
| `--branch NAME` | Installe une branche spécifique. |
| `--repo URL` | Remplace l’URL du dépôt Git. Utile pour les forks ou les tests locaux. |
| `--bin-dir PATH` | Écrit les wrappers de commandes ailleurs que dans `~/.local/bin`. |
| `--no-venv` | Installe dans l’environnement Python courant au lieu de créer `.venv`. |
| `--skip-shell-config` | Évite d’éditer `.zshrc` / `.bashrc`. |

Si `--skip-shell-config` a été utilisé, redémarrez le shell ou lancez :

```bash
export PATH="$HOME/.local/bin:$PATH"
```

## Vérifier l’installation

```bash
tesserae init --help
tesserae compile --help
tesserae export site --help
```
