# `tesserae ingest`

<!-- translations:start -->
<p align="center"><a href="../ingest.md">English</a> · <a href="ingest.ko.md">한국어</a> · <a href="ingest.zh.md">中文</a> · <a href="ingest.ja.md">日本語</a> · <a href="ingest.ru.md">Русский</a> · <a href="ingest.es.md">Español</a> · <a href="ingest.fr.md">Français</a> · <a href="ingest.de.md">Deutsch</a></p>
<!-- translations:end -->
Fusionne un fichier de document ou une URL dans la base de connaissances.

## Usage

    tesserae ingest <input>...  [--title T] [--source-kind K] [--full] [--dry-run]

`<input>` est un ou plusieurs chemins de fichiers locaux ou des URL `http(s)`. Les URL sont récupérées, converties en
markdown et persistées sous `data/ingested/<slug>.md` avec un front-matter de provenance
(`source_url`, `fetched_at`, `content_sha256`, et `arxiv_id` quand détecté), puis fusionnées.
Les fichiers locaux venant de l’extérieur du projet sont copiés dans `data/ingested/` pour devenir des
sources suivies (une compilation complète ultérieure les reproduit à l’identique).

L’ingestion d’URL requiert l’extra optionnel :

    pip install tesserae[ingest-url]

## Comment ça marche

Par défaut, `ingest` fusionne la nouvelle source via une compilation incrémentale — il ne re-extrait pas
tout le corpus — et le résultat est octet-pour-octet identique à une compilation complète (un repli
automatique en recompilation complète garantit la correction pour tout cas que le chemin incrémental ne sait pas gérer).
Passez `--full` pour forcer une recompilation complète de tout le corpus.

## Drapeaux

- `--full` — force une recompilation complète de tout le corpus.
- `--dry-run` — récupère et rapporte ce qui serait ingéré ; n’écrit aucun graphe.
- `--title` — remplacement du titre, utile pour les URL nues.
- `--source-kind` — remplace la classification de la source.

## La couche de concepts (`--extractor`)

Tesserae est un wiki LLM, donc `compile` construit la **couche concepts/claims par
défaut** (`--extractor llm`) : il lit chaque document à travers votre fournisseur LLM
configuré — **codex / claude / API Anthropic**, selon `llm_provider` — et frappe des
concepts, claims, capacités, termes techniques, extraits de preuve, et les arêtes
typées entre eux. C’est la couche qui permet au graphe de répondre à *« quelle est
cette idée, et comment se relie-t-elle »*, pas seulement à *« quel fichier l’a dit »*.

    tesserae compile                        # LLM concept layer, configured provider
    tesserae compile --llm-provider codex   # force a provider for this run

Si aucun backend LLM n’est configuré/authentifié, compile se dégrade vers l’extracteur
**déterministe** (structurel seulement — sources, sections, liens explicites) et avertit. Vous pouvez
aussi le demander explicitement — il est rapide, sans clé et octet-stable, le mode CI /
reproductible :

    tesserae compile --extractor deterministic

### Choisir les comptes à dépenser (`llm_claude_config_dirs`)

Avec le fournisseur `claude`, Tesserae passe en revue vos comptes Claude CLI
connectés : un compte qui atteint sa limite laisse la place au suivant, plutôt que de
faire basculer tout le reste de l'exécution en extraction déterministe. Par défaut,
tous les répertoires `~/.claude*` sont découverts automatiquement.

Le fournisseur **codex** fonctionne de la même façon : il parcourt les répertoires
`~/.codex*` authentifiés (un répertoire ne compte que s'il contient `auth.json`) et se
configure avec `llm_codex_homes`. Chaque fournisseur a sa propre clé parce que chacun a sa
propre organisation de comptes sur disque — les répertoires de configuration de Claude CLI
et les homes Codex ne sont pas interchangeables :

| fournisseur | clé de configuration | ce qu'elle liste |
|---|---|---|
| `claude` | `llm_claude_config_dirs` | répertoires de configuration Claude CLI (`~/.claude*`) |
| `codex`  | `llm_codex_homes`        | homes Codex (`~/.codex*`) |

Pour contrôler exactement quels comptes peuvent être dépensés, et dans quel ordre,
définissez `llm_claude_config_dirs` dans `.tesserae/config.json` (projet) ou
`~/.tesserae/config.json` (global) :

```json
{
  "llm_claude_config_dirs": [
    "/Users/you/.claude-work",
    "/Users/you/.claude-personal"
  ]
}
```

Cette liste fait autorité — rien en dehors n'est essayé. Elle **l'emporte aussi sur la
variable d'environnement ambiante `CLAUDE_CONFIG_DIR`**, héritée par tout processus
lancé depuis une session Claude Code et qui, sinon, lierait toute la compilation au
quota de cette seule session. Sans configuration, `CLAUDE_CONFIG_DIR` reste le premier
compte essayé.

Lorsque tous les comptes configurés signalent leur limite d'usage, la compilation
cesse d'appeler le LLM pour le reste de l'exécution au lieu de reposer la question
document par document, marque ces documents `fallback: true` et vous le signale.
Récupérez-les après réinitialisation de la limite, sans tout recompiler :

    tesserae compile --changed-only --retry-fallbacks


**Sensible au coût (`selective-llm`)** — ne router à travers le LLM que les docs correspondants, le
reste en déterministe :

    tesserae compile --extractor selective-llm \
      --llm-include "docs/**/*.md" --llm-limit 20

Les mêmes drapeaux fonctionnent sur `tesserae extract <paths>` (autonome) et
`tesserae compile <paths>` (ingestion de chemins ad hoc).

**Réglage :**

- `--llm-provider codex|claude|anthropic` — remplace le fournisseur (défaut :
  `llm_provider` dans la config).
- `--llm-model` — modèle pour l’extracteur (défaut : celui par défaut du fournisseur).
- `--llm-include <glob>` — pour `selective-llm`, quels fichiers passent par le LLM
  (répétez pour plusieurs ; les motifs correspondent n’importe où dans le chemin absolu, p. ex.
  `"*docs/superpowers*"`).
- `--llm-limit N` — plafonne combien de fichiers atteignent le LLM (le reste demeure déterministe).

**Pas de timeout par défaut.** Un gros document de conception génère beaucoup de JSON et peut prendre
des minutes ; l’extraction court jusqu’au bout plutôt que d’être coupée silencieusement (un
timeout est opt-in uniquement).

**Robuste sur des corpus réels.** Un seul document bruité ou lent n’avorte jamais toute la
compilation : un échec LLM sur un doc (auth, erreur, une génération imparsable) retombe
sur la ligne de base déterministe pour *ce* doc, une arête ou un type de nœud hors du
vocabulaire contrôlé est abandonné, et le cache clé par contenu fait qu’une recompilation de
docs inchangés réutilise l’extraction antérieure.

> Les noms d’extracteurs `claude-cli` / `selective-claude` (et les drapeaux `--claude-*`)
> sont des alias dépréciés de `llm` / `selective-llm` (et `--llm-*`) ; ils
> fonctionnent encore mais émettent une note de dépréciation.

## Gérer le périmètre de compilation (`sources`)

`tesserae compile` (sans arguments) compile les répertoires de la liste `sources`
du projet. Gérez cette liste — **locale ou globale** — avec les sous-commandes `sources` :

    tesserae sources add docs                 # local: inside the project (stored project-relative)
    tesserae sources add /data/shared-notes   # global: an absolute path outside the project
    tesserae sources add ../sibling-project   # global: a relative path that escapes the root
    tesserae sources list                     # shows each source tagged local/global, flags missing
    tesserae sources remove docs

Un chemin à l’intérieur du projet est stocké relatif au projet (portable) ; tout ce qui est à l’extérieur
est stocké en absolu. Les deux sont résolus au moment de la compilation, donc une source globale se compile
exactement comme une locale. (Les ajouts dédupliquent par emplacement résolu, si bien que les formes absolue et
relative en `../` du même répertoire ne comptent jamais double.)

## Commandes associées

- `tesserae compile` (sans arguments) re-extrait tout le corpus suivi.
- `tesserae ingest <x>` ajoute une source de façon incrémentale.
- `tesserae code ingest` frappe un graphe de code depuis des sources Python (une commande
  différente), pour les projets qui activent la couche de code via une entrée `external_tools`
  pour `codegraph`.

### Activer la couche de code

Le code source est **optionnel**. Ajoutez une entrée `external_tools` à `.tesserae/config.json` :

```json
{
  "external_tools": [{"id": "codegraph"}]
}
```

Sans cette entrée, il n'y a pas de couche de code : la compilation n'extrait rien, les hooks sync-code restent silencieux et `code-graph.json` est supprimé si une compilation antérieure en a laissé un. Le type de projet ne l'active pas — un projet `Repository` sans entrée ne compile aucun code.

Mettez `"enabled": false` pour la désactiver. Pour l'intelligence de code, envisagez CodeGraph ; Tesserae se concentre sur les documents et les transcriptions de session.
