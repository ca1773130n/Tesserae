# Workflow compagnon Understand Anything

<!-- translations:start -->
<p align="center"><a href="../../integrations/understand-anything.md">English</a> · <a href="understand-anything.ko.md">한국어</a> · <a href="understand-anything.zh.md">中文</a> · <a href="understand-anything.ja.md">日本語</a> · <a href="understand-anything.ru.md">Русский</a> · <a href="understand-anything.es.md">Español</a> · <a href="understand-anything.de.md">Deutsch</a></p>
<!-- translations:end -->
[Understand Anything](https://github.com/Lum1104/Understand-Anything) et Tesserae sont des projets complémentaires.

- Understand Anything excelle à produire un graphe de connaissances de codebase et un tableau de bord interactif.
- Tesserae se concentre sur la mémoire d’agent de longue durée : docs, compilation markdown/wiki, publication statique, historique de sessions et exports orientés agents.

Tesserae ne devrait ni vendorer ni absorber Understand Anything. Traitez-le comme un compagnon indépendant qui peut produire des artefacts de graphe utiles.

## Pourquoi utiliser les deux ?

Understand Anything peut écrire :

```text
.understand-anything/knowledge-graph.json
```

Ce graphe capture la structure du code : fichiers, fonctions, classes, modules, concepts, dépendances, couches et visites guidées.

Tesserae peut alors préserver cet artefact aux côtés du reste de la mémoire du projet :

- docs sources et pages markdown ;
- fichiers du dépôt ;
- notes de recherche ;
- historique local des sessions Claude Code / Codex ;
- pages de wiki statique générées ;
- vues 2D / 3D du site de graphe ;
- `llms.txt`, `llms-full.txt`, `search-index.json`, `graph.json`, et les siblings d’agent par page.

## Workflow actuel à faible friction

Le chemin recommandé est l’assistant de configuration :

```bash
tesserae init
```

Choisissez Understand Anything à l’étape des outils compagnons (il est **désactivé par défaut** — son rafraîchissement exécute un script d’installation distant). Tesserae écrit une commande de rafraîchissement gérée dans `.tesserae/config.json` sous `external_tools`. L’auto-refresh à la compilation est aussi désactivé par défaut (`auto_refresh: false`) ; passez-le à `true` si vous voulez que `tesserae compile` lance le wrapper automatiquement quand le graphe UA est manquant ou périmé.

Pour l’automatisation non interactive, lancez `tesserae init --yes` (intégrations OFF), activez Understand Anything dans `.tesserae/config.json`, puis :

```bash
tesserae integrations refresh understand-anything --platform codex
tesserae compile
```

La commande stockée appartient à Tesserae, ce n’est pas quelque chose que l’utilisateur doit inventer :

```bash
tesserae integrations refresh understand-anything --platform codex
```

Pendant la compilation, Tesserae :

1. vérifie si `.understand-anything/knowledge-graph.json` existe et correspond au commit git courant quand les métadonnées sont disponibles ;
2. lance la plateforme d’agent configurée (`codex`, `opencode` ou `claude`) uniquement quand son entrée `external_tools` a `auto_refresh: true` et que le graphe est manquant/périmé, ou que le rafraîchissement est forcé ;
3. vérifie que le graphe a été écrit ;
4. matérialise `.tesserae/external/understand-anything.md` ;
5. poursuit la compilation mémoire normale.

Vous pouvez forcer toutes les commandes de rafraîchissement externes configurées avant une compilation :

```bash
tesserae compile --refresh-integrations
```

Besoin de Cognee aussi ? Cognee est également opt-in : installez-le avec `pip install tesserae[cognee]` et définissez `memory_backends.cognee.enabled: true` dans `.tesserae/config.json` (interrogez-le explicitement avec `tesserae query --backend cognee`).

## Équivalent manuel

Le chemin de configuration géré est préféré. Si vous voulez intentionnellement utiliser UA hors de Tesserae, lancez d’abord Understand Anything dans votre environnement d’agent :

```bash
/understand
```

Puis lancez l’assistant de configuration et **activez Understand Anything quand demandé** pour
que Tesserae enregistre la source de projection markdown. Les fichiers JSON directs sont
gardés comme artefacts compagnons bruts, pas comme des chemins sources saisis à la main.

```bash
tesserae init
# enable Understand Anything when the wizard prompts
tesserae compile
tesserae export site
```

Pour l’automatisation non interactive, lancez `tesserae init --yes` (intégrations OFF),
activez Understand Anything dans `.tesserae/config.json` (l’assistant écrit
l’intégration sous la clé `external_tools`), puis `tesserae integrations
refresh understand-anything` avant de compiler.

Si vous voulez aussi la mémoire locale des sessions d’agent :

```bash
tesserae sessions discover --import
tesserae export site
```

## Synchronisation de graphe native

Tesserae garde désormais la projection markdown pour la lisibilité et importe aussi le graphe UA nativement pendant la compilation quand l’outil configuré utilise `sync_mode: native_graph`.

L’adaptateur natif lit `.understand-anything/knowledge-graph.json`, mappe les nœuds/arêtes UA vers l’ontologie contrôlée de Tesserae, et écrit un manifest de sync :

```text
.tesserae/external/understand-anything-sync.json
```

Correspondance actuelle :

| Understand Anything | Direction Tesserae |
|---|---|
| `project` | métadonnées de dépôt/projet |
| `nodes[type=file]` | nœuds `SourceFile` |
| `nodes[type=function]` / `method` | nœuds `CodeFunction` |
| `nodes[type=class]` / `component` | nœuds `CodeClass` |
| `nodes[type=module]` / `package` | nœuds `CodeModule` |
| `nodes[type=concept]` / `topic` | nœuds `Concept` canoniques |
| `nodes[type=feature]` / `capability` | nœuds `Capability` |
| `edges[type=imports]` | arêtes `imports` |
| `edges[type=contains]` | arêtes `contains` |
| `edges[type=calls]` | arêtes `calls` |
| types d’arêtes inconnus | `shares_concept_with` avec métadonnées `ua_edge_type` |

La synchronisation des concepts est canonicalisée au lieu d’être aveuglément dupliquée. Si UA émet `Mermaid Rendering` et que Tesserae a déjà `Mermaid rendering`, la compilation garde un seul nœud de concept et ajoute la provenance UA sous `metadata.external_refs`.

Tesserae reste le compilateur de mémoire ; UA reste un générateur de graphe compagnon indépendant.

## Principe de collaboration

Ne présentez pas Tesserae comme un remplaçant d’Understand Anything.

Un meilleur cadrage :

- Understand Anything aide un développeur à comprendre une codebase maintenant.
- Tesserae aide les agents à se souvenir, chercher, citer, mettre à jour et publier la connaissance du projet dans la durée.
