# Historique des sessions Harness

<!-- translations:start -->
<p align="center"><a href="../session-history.md">English</a> · <a href="session-history.ko.md">한국어</a> · <a href="session-history.zh.md">中文</a> · <a href="session-history.ja.md">日本語</a> · <a href="session-history.ru.md">Русский</a> · <a href="session-history.es.md">Español</a> · <a href="session-history.fr.md">Français</a> · <a href="session-history.de.md">Deutsch</a></p>
<!-- translations:end -->
Tesserae peut importer des transcript locaux d'AI-agent et les rendre comme mémoire de projet dans la section `sessions/` du site statique.

Cette fonctionnalité est volontairement séparée de `export harness` :

- `export harness` est un contexte sortant pour des outils comme Claude Code, Codex, Gemini, Cursor, Kiro et OpenCode.
- `sessions ...` est un historique entrant : il normalise les sessions Claude Code/Codex précédentes pour le projet courant, les stocke dans `.tesserae/harness_sessions/`, et permet à `export site` de publier les pages index/detail des sessions.

## Deux entrées : import par lot et surveillance en direct

L'ingestion de sessions n'est plus uniquement par lot. Deux chemins mènent au même magasin normalisé :

- **Import par lot** — `sessions discover/import` scanne les racines de transcript à la demande et écrit en une fois. Cette page documente ce flux ci-dessous.
- **Surveillance en direct** — le supervisor daemon (`tesserae engine`) exécute un `SessionTailer` qui surveille les transcript *du projet lui-même* (Claude Code et Codex) et ingère les nouveaux tours dès qu'ils arrivent. À chaque tick il fait un seek vers un byte offset persisté par fichier, ne lit que les nouveaux octets, et écrit les tours complets dans le SQLite `HarnessSessionsDB` (`.tesserae/sqlite.db`) **avant** d'enfiler une recompilation avec debounce, de sorte que la compilation lit toujours un état cohérent. Le tailer est limité aux propres sessions du projet (Claude `projects/<slug>/*.jsonl` ; Codex filtré par cwd) et, après un redémarrage, reprend depuis les offset stockés sans rejouer les tours.

Lancez la boucle en direct avec :

```bash
tesserae engine        # surveiller les sources, fusionner les rafales, recompiler automatiquement
tesserae engine --once # un seul cycle de drain puis sortie (déterministe)
```

`tesserae refresh` exécute le même pipeline ingest → compile → project une fois, in-process, sans démarrer le watcher de longue durée (passez `--skip-sessions` pour sauter le scan de discovery des sessions harness).

## Modèle de confidentialité

Les deux chemins d'ingestion sont explicites : le tailer en direct ne tourne que tant que vous gardez `tesserae engine` actif, et le discovery par lot n'écrit qu'avec `--import`. Un `tesserae compile` ou `tesserae export site` normal lit les sessions déjà normalisées depuis `.tesserae/harness_sessions/` et les enregistrements en direct de `.tesserae/sqlite.db`, mais ne surprise-scrape pas de lui-même les répertoires privés de transcript harness.

Les enregistrements de session importés sont des artefacts locaux du projet. Relisez-les avant de publier un site public, surtout si vos transcript peuvent contenir des secrets, chemins privés, données client ou code non publié.

## Découvrir et importer les sessions locales

Depuis la racine du projet :

```bash
tesserae sessions discover --import
```

Discovery scanne les racines locales de transcript Claude Code et Codex qui appartiennent au répertoire de travail du projet courant. Utilisez `--root` pour scanner un répertoire de configuration précis, et répétez `--harness` pour limiter discovery :

```bash
tesserae sessions discover \
  --root ~/.claude \
  --root ~/.codex \
  --harness claude-code \
  --harness codex \
  --import
```

Sans `--import`, discovery affiche ce qu'il a trouvé sans écrire d'enregistrements de session normalisés.

## Importer directement du JSON normalisé

Si un autre outil a déjà produit du JSON `HarnessSession` normalisé, importez un fichier ou une liste de fichiers :

```bash
tesserae sessions import path/to/session.json path/to/more-sessions.json
```

Chaque entrée peut contenir un objet session ou une liste d'objets session.

## Lister les sessions importées

```bash
tesserae sessions list
```

Les sessions sont stockées sous :

```text
.tesserae/harness_sessions/
  manifest.json
  <harness>/
    <session>.json
    <session>.md
```

Les sessions surveillées en direct sont en plus suivies dans le SQLite `HarnessSessionsDB` (`.tesserae/sqlite.db`), qui persiste aussi les read offset par fichier depuis lesquels le tailer reprend. `sessions list` rapporte la vue combinée.

## Construire les pages statiques de sessions

Après avoir importé les sessions, reconstruisez le site :

```bash
tesserae export site
```

Le site émet :

```text
.tesserae/site/sessions/index.html
.tesserae/site/sessions/<project>/<session>.html
```

Le site généré relie Sessions depuis le global rail, les cartes Browse de l'accueil, les entrées de recherche et le breadcrumb trail de chaque page de détail de session.

## Mise en page de la page détail de session

Les pages détail de session utilisent le shell partagé du site statique plutôt qu'un transcript dump autonome. Elles incluent :

- hero et stat strip ;
- résumé de haut niveau ;
- timeline et size metadata ;
- decisions, files, commands, tools et errors lorsqu'ils existent ;
- subagent tree replié ;
- conversation user/assistant tour par tour ;
- tool-use blocks repliés attachés sous le tour assistant précédent ;
- un conversation rail gauche qui pointe vers les anchors `#turn-N`.

Le markdown de conversation est rendu via le renderer markdown du site. Les surfaces sémantiques comme inline code, command/tag markup explicite, paths, filenames et hashtags sont décorées en chips compactes ; les noms aléatoires capitalisés ne sont pas chipés automatiquement.

Typography actuelle des transcript :

| Surface | Selector | Size |
|---|---|---|
| Prose markdown de conversation | `.session-turn-text`, prose children | `8px` |
| Code fences génériques de conversation | `.session-turn-text pre` | `10px` |
| Contenu fenced code Bash/shell | `.session-code-block code.language-bash`, `.language-sh`, `.language-shell`, `.language-zsh` | `11px` |
| Tool details/summary | `.session-tool-details`, `.session-tool-details > summary` | `10px` |
| Tool-use header | `.session-tool-use-header` | `8px` |
| Tool payload text | `.session-tool-use-text` | `6px` |

## Checklist de publication des sessions

Avant de déployer un site public qui inclut des sessions :

1. Exécutez `tesserae sessions list` et confirmez que le nombre est attendu.
2. Inspectez `.tesserae/harness_sessions/` pour du contenu sensible.
3. Reconstruisez avec `tesserae export site`.
4. Ouvrez localement `sessions/index.html` et au moins une page détail de session.
5. Confirmez que les tool blocks sont repliés par défaut et que les raw tool payloads sont acceptables à publier.
6. Déployez avec `tesserae export site --deploy` une fois le source tree committed.
