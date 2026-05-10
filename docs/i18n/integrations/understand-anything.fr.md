# Workflow compagnon Understand Anything

<!-- translations:start -->
<p align="center"><a href="../../integrations/understand-anything.md">English</a> · <a href="understand-anything.ko.md">한국어</a> · <a href="understand-anything.zh.md">中文</a> · <a href="understand-anything.ja.md">日本語</a> · <a href="understand-anything.ru.md">Русский</a> · <a href="understand-anything.es.md">Español</a> · <a href="understand-anything.fr.md">Français</a></p>
<!-- translations:end -->
[Understand Anything](https://github.com/Lum1104/Understand-Anything) et LLM-Wiki sont des projets complémentaires.

- Understand Anything est excellent pour produire un graphe de connaissance d'une base de code et un tableau de bord interactif.
- LLM-Wiki se concentre sur la mémoire d'agent durable : docs, compilation markdown/wiki, publication statique, historique de sessions et exports destinés aux agents.

LLM-Wiki ne doit pas intégrer ni absorber Understand Anything. Traitez-le comme un compagnon indépendant pouvant produire des artefacts de graphe utiles.

## Pourquoi utiliser les deux ?

Understand Anything peut écrire :

```text
.understand-anything/knowledge-graph.json
```

Ce graphe capture la structure du code, comme les fichiers, fonctions, classes, modules, concepts, dépendances, couches et visites guidées.

LLM-Wiki peut ensuite conserver cet artefact avec le reste de la mémoire du projet :

- documents sources et pages markdown ;
- fichiers du dépôt ;
- notes de recherche ;
- historique local des sessions Claude Code / Codex ;
- pages wiki statiques générées ;
- vues de site de graphe 2D / 3D ;
- `llms.txt`, `llms-full.txt`, `search-index.json`, `graph.json` et siblings d'agent par page.

## Workflow actuel à faible friction

Le chemin recommandé est l'assistant de configuration :

```bash
llm_wiki project setup
```

Choisissez Understand Anything à l'étape des outils compagnons. LLM-Wiki installe/met à jour les skills compagnons sur demande et écrit une commande de rafraîchissement gérée dans `.llm-wiki/config.json`. Les futurs appels à `llm_wiki project compile` exécutent automatiquement ce wrapper lorsque le graphe UA est manquant ou périmé.

Pour l'automatisation non interactive, utilisez :

```bash
llm_wiki project setup \
  --yes \
  --with-understand-anything \
  --install-understand-anything \
  --understand-anything-platform codex
llm_wiki project compile
```

La commande stockée appartient à LLM-Wiki ; ce n'est pas quelque chose que l'utilisateur doit inventer :

```bash
llm_wiki project refresh-understand-anything --platform codex
```

Pendant la compilation, LLM-Wiki :

1. vérifie si `.understand-anything/knowledge-graph.json` existe et correspond au commit git courant lorsque les métadonnées sont disponibles ;
2. exécute la plateforme d'agent configurée (`codex`, `opencode` ou `claude`) uniquement lorsque le graphe est manquant/périmé ou que le rafraîchissement est forcé ;
3. vérifie que le graphe a été écrit ;
4. matérialise `.llm-wiki/external/understand-anything.md` ;
5. poursuit la compilation normale de la mémoire.

Vous pouvez forcer toutes les commandes de rafraîchissement externes configurées avant une compilation :

```bash
llm_wiki project compile --refresh-external-tools
```

Besoin aussi de Cognee ? Ajoutez les flags de mémoire d'exécution dans la même commande setup :

```bash
llm_wiki project setup \
  --yes \
  --with-understand-anything \
  --install-understand-anything \
  --understand-anything-platform codex \
  --run-cognee \
  --install-cognee
```

## Équivalent manuel

Le chemin de configuration géré est préférable. Si vous voulez volontairement utiliser UA en dehors de LLM-Wiki, exécutez d'abord Understand Anything dans votre environnement d'agent :

```bash
/understand
```

Puis exécutez `llm_wiki project setup --with-understand-anything` afin que LLM-Wiki enregistre la source de projection markdown. Les fichiers JSON directs sont conservés comme artefacts compagnons bruts, et non comme chemins sources saisis à la main.

```bash
llm_wiki project setup --with-understand-anything
llm_wiki project compile
llm_wiki project build-site
```

Si vous voulez aussi la mémoire locale des sessions d'agent :

```bash
llm_wiki project sessions discover --import
llm_wiki project build-site
```

## Synchronisation native du graphe

LLM-Wiki conserve la markdown projection pour la lisibilité et importe aussi le graphe UA nativement pendant compile quand l’outil configuré utilise `sync_mode: native_graph`.

L’adaptateur natif lit `.understand-anything/knowledge-graph.json`, mappe les nœuds/arêtes UA vers l’ontology contrôlée de LLM-Wiki, puis écrit un sync manifest:

```text
.llm-wiki/external/understand-anything-sync.json
```

Mapping actuel:

| Understand Anything | Direction LLM-Wiki |
|---|---|
| `project` | repository/project metadata |
| `nodes[type=file]` | `SourceFile` nodes |
| `nodes[type=function]` / `method` | `CodeFunction` nodes |
| `nodes[type=class]` / `component` | `CodeClass` nodes |
| `nodes[type=module]` / `package` | `CodeModule` nodes |
| `nodes[type=concept]` / `topic` | canonical `Concept` nodes |
| `nodes[type=feature]` / `capability` | `Capability` nodes |
| `edges[type=imports]` | `imports` edges |
| `edges[type=contains]` | `contains` edges |
| `edges[type=calls]` | `calls` edges |
| unknown edge types | `shares_concept_with` avec metadata `ua_edge_type` |

Concept synchronization est canonicalisée au lieu de créer des doublons aveuglément. Si UA émet `Mermaid Rendering` et que LLM-Wiki possède déjà `Mermaid rendering`, compile conserve un seul concept node et ajoute la provenance UA sous `metadata.external_refs`.

LLM-Wiki reste le memory compiler; UA reste un companion graph generator indépendant.

## Principe de collaboration

Ne présentez pas LLM-Wiki comme remplaçant Understand Anything.

Meilleure formulation :

- Understand Anything aide un développeur à comprendre une base de code maintenant.
- LLM-Wiki aide les agents à mémoriser, rechercher, citer, mettre à jour et publier les connaissances du projet dans le temps.
