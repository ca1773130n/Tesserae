# Historique des sessions de harness

<!-- translations:start -->
<p align="center"><a href="../session-history.md">English</a> · <a href="session-history.ko.md">한국어</a> · <a href="session-history.zh.md">中文</a> · <a href="session-history.ja.md">日本語</a> · <a href="session-history.ru.md">Русский</a> · <a href="session-history.es.md">Español</a> · <a href="session-history.fr.md">Français</a> · <a href="session-history.de.md">Deutsch</a></p>
<!-- translations:end -->
Tesserae peut importer les transcriptions locales d’agents IA et les rendre comme mémoire de projet sous la section `sessions/` du site statique.

Cette fonctionnalité est volontairement séparée d’`export harness` :

- `export harness` est du contexte sortant pour des outils comme Claude Code, Codex, Gemini, Cursor, Kiro et OpenCode.
- `sessions ...` est de l’historique entrant : il normalise les sessions Claude Code/Codex passées du projet courant, les stocke sous `.tesserae/harness_sessions/` et laisse `export site` publier des pages d’index/de détail de session.

## Deux voies d’entrée : import par lot et surveillance en direct

L’ingestion de sessions n’est plus seulement par lot. Il existe deux chemins
vers le même magasin normalisé :

- **Import par lot** — `sessions discover/import` scanne les racines de
  transcriptions à la demande et écrit en un coup. Cette page documente ce flux
  ci-dessous.
- **Surveillance en direct** — le daemon superviseur (`tesserae engine`) exécute
  un `SessionTailer` qui surveille les transcriptions Claude Code et Codex
  *de ce projet même* et ingère les nouveaux tours à mesure qu’ils arrivent.
  Chaque tick se positionne sur un offset d’octets par fichier persisté, ne lit
  que les nouveaux octets, et stocke les tours complets dans la
  `HarnessSessionsDB` SQLite (`.tesserae/sqlite.db`) **avant** de mettre en
  file une recompilation avec debounce, si bien que la compilation lit toujours
  un état cohérent. Le tailer est limité aux sessions propres au projet
  (Claude `projects/<slug>/*.jsonl` ; Codex filtré par cwd) et reprend depuis
  les offsets stockés après un redémarrage sans rejouer les tours.

Lancez la boucle en direct avec :

```bash
tesserae engine        # watch sources, coalesce bursts, auto-recompile
tesserae engine --once # single drain cycle then exit (deterministic)
```

`tesserae refresh` exécute le même pipeline ingest → compile → project
une fois, en processus, sans démarrer le watcher de longue durée (passez
`--no-sessions` pour sauter le scan de découverte des sessions de harness).

## Modèle de confidentialité

Les deux chemins d’ingestion sont explicites : le tailer en direct ne tourne
que tant que vous gardez `tesserae engine` en vie, et la découverte par lot
n’écrit qu’avec `--import`. Un `tesserae compile` ou `tesserae export site`
normal lit les sessions déjà normalisées depuis `.tesserae/harness_sessions/`
et les enregistrements en direct dans `.tesserae/sqlite.db`, mais il ne
racle pas par surprise les répertoires privés de transcriptions de harness de
son propre chef.

Les enregistrements de session importés sont des artefacts locaux du projet. Passez-les en revue avant de publier un site public, surtout si vos transcriptions peuvent contenir des secrets, des chemins privés, des données clients ou du code non publié.

## Découvrir et importer les sessions locales

Depuis la racine du projet :

```bash
tesserae sessions discover --import
```

La découverte scanne les racines locales de transcriptions Claude Code et Codex qui appartiennent au répertoire de travail du projet courant. Utilisez `--root` pour scanner un répertoire de config spécifique, et répétez `--harness` pour limiter la découverte :

```bash
tesserae sessions discover \
  --root ~/.claude \
  --root ~/.codex \
  --harness claude-code \
  --harness codex \
  --import
```

Sans `--import`, la découverte affiche ce qu’elle a trouvé sans écrire d’enregistrements de session normalisés.

## Importer du JSON normalisé directement

Si un autre outil a déjà produit du JSON `HarnessSession` normalisé, importez un fichier ou une liste de fichiers :

```bash
tesserae sessions import path/to/session.json path/to/more-sessions.json
```

Chaque entrée peut contenir un objet session ou une liste d’objets session.

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

Les sessions surveillées en direct sont en plus suivies dans la
`HarnessSessionsDB` SQLite (`.tesserae/sqlite.db`), qui persiste aussi les
offsets de lecture par fichier depuis lesquels le tailer reprend.
`tesserae sessions list` rapporte la vue combinée.

## Construire les pages de session statiques

Après avoir importé des sessions, reconstruisez le site :

```bash
tesserae export site
```

Le site émet :

```text
.tesserae/site/sessions/index.html
.tesserae/site/sessions/<project>/<session>.html
```

Le site généré lie Sessions depuis le rail global, les cartes Browse de l’accueil, les entrées de recherche et le fil d’Ariane de chaque page de détail de session.

## Recherche rapide de transcriptions (memex)

Quand vous servez le site avec `tesserae serve`, le **tableau de bord des sessions**
gagne une boîte de recherche plein texte sur chaque transcription Claude/Codex
indexée, adossée à [`nicosuave/memex`](https://github.com/nicosuave/memex)
(BM25). Les résultats affichent `project · role · date · score` plus un extrait
correspondant.

```bash
cargo install --git https://github.com/nicosuave/memex --locked   # or: tesserae config deps --install memex
memex index                                                        # build the index once
tesserae serve                                                     # search box appears on /sessions
```

C’est **optionnel et gracieux** : sans binaire `memex` (ou sans index), la boîte
affiche un message clair et actionnable et le reste du tableau de bord n’est pas
affecté. L’endpoint de recherche (`GET /api/transcript-search`) est restreint
aux appelants same-origin/loopback pour qu’une page web visitée ne puisse pas
sonder votre historique local.

## Mise en page des pages de détail de session

Les pages de détail de session utilisent la coquille partagée du site statique plutôt qu’un déversement de transcription autonome. Elles incluent :

- un hero et un bandeau de stats ;
- un résumé de haut niveau ;
- des métadonnées de chronologie et de taille ;
- décisions, fichiers, commandes, outils et erreurs quand présents ;
- l’arbre des sous-agents replié ;
- la conversation utilisateur/assistant tour par tour ;
- des blocs d’usage d’outil repliés attachés sous le tour assistant précédent ;
- un rail de conversation à gauche qui lie vers les ancres `#turn-N`.

Le markdown de conversation est rendu via le moteur de rendu markdown du site. Les surfaces sémantiques comme le code inline, le marquage explicite de commandes/tags, les chemins, les noms de fichiers et les hashtags sont décorées en puces compactes ; les noms capitalisés aléatoires ne sont pas transformés automatiquement en puces.

Typographie actuelle des transcriptions :

| Surface | Sélecteur | Taille |
|---|---|---|
| Prose markdown de conversation | `.session-turn-text`, prose children | `8px` |
| Blocs de code de conversation génériques | `.session-turn-text pre` | `10px` |
| Contenu de code cloisonné Bash/shell | `.session-code-block code.language-bash`, `.language-sh`, `.language-shell`, `.language-zsh` | `11px` |
| details/summary d’outil | `.session-tool-details`, `.session-tool-details > summary` | `10px` |
| En-tête d’usage d’outil | `.session-tool-use-header` | `8px` |
| Texte de payload d’outil | `.session-tool-use-text` | `6px` |

## Checklist de publication pour les sessions

Avant de déployer un site public qui inclut des sessions :

1. Lancez `tesserae sessions list` et confirmez que le compte est celui attendu.
2. Inspectez `.tesserae/harness_sessions/` pour tout contenu sensible.
3. Reconstruisez avec `tesserae export site`.
4. Ouvrez `sessions/index.html` et au moins une page de détail de session en local.
5. Confirmez que les blocs d’outil sont repliés par défaut et que les payloads bruts d’outil sont acceptables à publier.
6. Déployez avec `tesserae export site --deploy` une fois l’arbre source commité.
