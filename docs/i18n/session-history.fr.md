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

Le texte d'un tour est copié dans les noms et descriptions de nœuds, lesquels
sont sérialisés dans `graph.json` et dans chacune de ses projections — les
**répertoires personnels sont donc caviardés à l'entrée**. `/Users/<nom>` et
`/home/<nom>` n'atteignent jamais le graphe, ce qui compte parce qu'un chemin est
la seule donnée personnelle qui apparaît dans presque toutes les transcriptions
sans que personne l'ait voulu.

## Ce que devient un tour de session

Pour chaque transition *significative* d'une session — un appel d'outil ou une
action substantielle de l'assistant, pas du bavardage — la passe `Event`, qui
n'utilise aucun LLM, crée exactement un nœud portant `{turn_id, actor, action,
bref changement d'état}` et relie les événements consécutifs par des arêtes
`precedes`, si bien que l'état dynamique d'une session peut être rejoué dans
l'ordre. Cette passe n'appelle jamais de modèle, ne lève jamais d'exception sur
une entrée malformée, et est idempotente à l'octet près : chaque id, corps et
`first_seen_at` produit dérive du contenu, donc une réexécution donne des nœuds
et des arêtes identiques.

**Un résultat d'outil est un tour.** Les codes de sortie et les indicateurs
d'erreur survivent à l'ingestion et se posent sur le nœud `Event` : le graphe
distingue donc une commande qui a *échoué* d'une commande qui a seulement été
lancée. Avant cela, un agent relisant son propre historique voyait qu'il avait
lancé `pytest` sans savoir si la suite était passée — c'est toute la différence
entre un journal et une mémoire.

### L'arête `recovers`

À partir de deux résultats **observés** dans une même session, Tesserae dérive
la seule arête causale de son vocabulaire : un appel d'outil ayant signalé un
échec, puis un appel ultérieur — même outil, même famille de programme, même
répertoire de travail, même opérande, sans aucun succès observé sur cet opérande
entre les deux — ayant signalé un succès. L'`Event` qui réussit est la source,
celui qui a échoué la cible ; les deux ids de tour sont nommés dans la preuve, et
`metadata["basis"]` nomme chaque dimension sur laquelle les deux appels devaient
concorder.

`CAUSAL_EDGE_TYPES` compte exactement un membre, et c'est délibéré. Une revue de
quatre systèmes de mémoire d'agents parmi les plus avancés a montré qu'aucun ne
dérive d'arête causale : deux déduisent leur lien le plus fort de la
co-occurrence, un prend pour argent comptant un vocabulaire ouvert d'étiquettes
de relation fourni par un LLM sans vérification, et un n'a aucune arête. L'échec
que cette étroitesse vise à éviter, c'est de livrer un `caused_by` qui n'est en
réalité qu'un `happened_near` : dans un graphe, les deux sont indiscernables, et
le mauvais est lu comme une preuve.

L'ancre est l'**opérande**, pas la commande, car les commandes varient sur ce
qui n'a pas d'importance (options, ordre) tandis que la chose sur laquelle on
agit est ce qu'une nouvelle tentative rejoue réellement.

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

## Comment le magasin est écrit

Chaque enregistrement dans `.tesserae/harness_sessions/` porte un **producteur (`producer`)** — l’importateur qui l’a écrit. `sessions discover --import` appose `tesserae:discover` ; `sessions import <path>` appose `tesserae:import`. **Un écrivain ne peut toucher que les enregistrements qu’il a produits** : il n’élagle que les siens, et il ne surchargera pas l’enregistrement d’un autre producteur pour la même session — l’écriture entrante est ignorée et signalée comme `Left alone (written by another producer)`.

Cette règle existe parce que la provenance est la seule chose qui sépare véritablement les importateurs. Deux d’entre eux décrivent régulièrement la *même* session : l’analyse locale de Tesserae crée un enregistrement simple à partir d’une transcription sous `~/.claude`, tandis qu’un orchestrateur exporte cette même session portant l’identité de l’agent que seul il connaît. Les deux dérivent le même nom de fichier à partir de l’id de session, de sorte qu’ils entrent en collision. Ni l’emplacement de la transcription ni le nom du harness ne peuvent les distinguer — c’est pourquoi les corrections antérieures avec portée root pour [#104](https://github.com/ca1773130n/Tesserae/issues/104) n’ont pas fonctionné, et pourquoi 0.28.6 perdait toujours de tels enregistrements de deux façons : supprimés lorsque l’analyse ne trouvait plus la transcription, silencieusement surchargés lorsqu’elle l’était.

Si vous écrivez dans ce magasin à partir de votre propre outil, utilisez `tesserae sessions import <file>` et vos enregistrements sont protégés à partir de ce moment. Rien d’autre n’est requis.

La portée se réduit davantage, en tant que deuxième barrière : un enregistrement n’est élagué que si sa transcription se trouve également sous une racine que cette exécution a analysée et que son harness en était un qu’elle a analysé. Ainsi `--harness codex` laisse les enregistrements claude-code intacts même si `~/.claude` a été parcouru.

### Plusieurs machines partageant un même répertoire de projet

Chaque enregistrement porte aussi un **hôte (`host`)** — la machine qui l’a moissonné. **Un hôte n’élague que ce qu’il a moissonné lui-même.**

C’est un axe réellement distinct de `producer`, et les barrières ci-dessus ne peuvent pas en tenir lieu. Quand plusieurs serveurs exécutent chacun Claude Code et partagent un disque, ils partagent aussi `.tesserae` — mais chacun ne voit que ses propres transcriptions locales. L’analyse de chaque hôte appose le même `tesserae:discover`, et le `~/.claude` de chaque hôte se résout vers la même chaîne de chemin : la barrière de producteur et la barrière de portée passent donc *toutes les deux* sur une machine qui n’a jamais vu la transcription. Elle supprime alors l’enregistrement d’une autre machine et annonce un succès. Les enregistrements portent désormais l’hôte qui les a moissonnés, et l’élagage exige qu’il corresponde.

L’id d’hôte vit dans `~/.tesserae/host_id` — par machine, pas dans le répertoire de projet partagé — et il est généré une seule fois à la première utilisation. Forcez-le avec `TESSERAE_HOST_ID`. C’est délibérément un id persisté plutôt que le nom d’hôte : une flotte construite à partir d’une même image réutilise les noms d’hôte, et une collision de noms d’hôte livrerait silencieusement les enregistrements d’une machine à une autre.

Le chemin d’**écriture**, lui, est délibérément aveugle à l’hôte. Deux hôtes ne peuvent écrire la même session que si tous deux voient la transcription ; l’écriture est donc idempotente et se contente de réapposer la propriété sur le dernier hôte ayant prouvé qu’il la voyait. Filtrer aussi les écritures par hôte gèlerait à jamais les enregistrements d’une machine mise hors service, sans aucun moyen de les récupérer.

Les enregistrements écrits avant ce champ ne portent pas d’hôte. Ils sont sans propriétaire sur cet axe et survivent à l’élagage de n’importe quel hôte jusqu’à ce que `--adopt-unowned` les réclame — la même règle que `producer` applique déjà, et si elle compte ici, c’est que *tout* enregistrement écrit par 0.28.7 porte un producteur et pas d’hôte : la barrière de producteur s’abstiendrait donc et rien d’autre ne les protégerait.

Trois comportements à connaître :

- **Les enregistrements écrits avant 0.28.7 ne portent pas de producteur.** Ils sont sans propriétaire, donc aucun importateur ne les élague ni les surcharge — sûr, mais la découverte ne les rafraîchira pas non plus. `sessions discover --import --adopt-unowned` les réclame pour la découverte. Exécutez-le une fois si l’analyse propre de Tesserae est la seule chose écrivant dans ce magasin ; ne l’exécutez *pas* si un autre outil écrit aussi ici, car cela remet vos enregistrements à la découverte.
- Une découverte vide ne supprime jamais. Une analyse qui ne trouve rien — `HOME` incorrect, racines harness détachées — fusionne au lieu d’effacer.
- Une découverte qui supprime ou préserve des enregistrements imprime les deux nombres à côté du nombre d’imports, de sorte que le magasin ne peut pas changer de taille dans une ligne qui ne signale que la croissance.

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
