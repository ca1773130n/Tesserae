# Understand-Anything : mode code-graph uniquement

<!-- translations:start -->
<p align="center"><a href="../../integrations/understand-anything-code-only.md">English</a> · <a href="understand-anything-code-only.ko.md">한국어</a> · <a href="understand-anything-code-only.zh.md">中文</a> · <a href="understand-anything-code-only.ja.md">日本語</a> · <a href="understand-anything-code-only.ru.md">Русский</a> · <a href="understand-anything-code-only.es.md">Español</a> · <a href="understand-anything-code-only.de.md">Deutsch</a></p>
<!-- translations:end -->

Ce document fait suite à [understand-anything.md](../../integrations/understand-anything.md). Le document de base explique comment installer et activer [Understand-Anything](https://github.com/Lum1104/Understand-Anything) (UA) en tant que compagnon produisant un code-graph dans `.understand-anything/knowledge-graph.json`. **Le présent document explique comment faire en sorte qu'UA ne contribue QU'à un graphe de code et ne pollue jamais la couche Concept du research-graph de Tesserae avec des titres de sections extraits de vos docs.**

Si vous avez déjà ouvert le graphe typé après avoir activé UA et trouvé la couche Concept remplie d'éléments tels que `'Quickstart'`, `'2) Paste it into your MCP client'`, ou le même titre dans sept langues, vous avez rencontré le problème que ce document résout.

## Pourquoi cela se produit

Deux couches de la même erreur se combinent :

1. **UA parcourt vos docs par défaut.** À l'installation, le source loader d'UA parcourt chaque fichier lisible sous la racine de votre projet — y compris `docs/`, `docs/i18n/`, les READMEs dans toutes les langues, etc. Pour chaque titre markdown rencontré, il enregistre un nœud dans `.understand-anything/knowledge-graph.json` avec le texte du titre comme nom d'entité.
2. **Tesserae fusionne nativement l'intégralité du graphe d'UA.** Lorsque `external_tools` liste UA avec `sync_mode: "native_graph"`, `ProjectWiki._merge_configured_understand_anything_graph()` lit l'artefact et importe chaque nœud UA dans le research graph en tant que `Concept`. L'intention « ceci est un symbole de code » d'UA est aplatie en « ceci est un concept de recherche », et vos nœuds de titres de docs sont embarqués au passage.

Effet net : chaque titre traduit apparaît comme un Concept dupliqué (`'Setup'`, `'설정'`, `'安装'`, `'インストール'`, `'Установка'`, `'Configuración'`, `'Configuration'`, `'Einrichtung'`), créant des collisions de slug que le projector renomme en `setup-2.md`, `setup-3.md`, …, `setup-7.md`.

> [!warning] Vous le reconnaîtrez quand vous le verrez
> Vérification de symptômes sur un projet où cela s'est produit :
> ```bash
> .venv/bin/python -c "
> import json
> from collections import Counter
> nodes = json.load(open('.tesserae/graph.json'))['nodes']
> srcs = Counter(n.get('source_path','') for n in nodes if n['type']=='Concept')
> print(srcs.most_common(3))
> "
> ```
> Si la source de tête est `.understand-anything/knowledge-graph.json` avec des centaines de nœuds Concept, chaque titre traduit que vous avez est importé en tant que concept distinct.

## Correctif en trois étapes

### Étape 1 — empêcher Tesserae d'importer UA en tant que Concepts

Modifiez `.tesserae/config.json` et définissez à la fois `enabled: false` et `sync_mode: "disabled"` sur l'entrée de l'outil UA. Les deux drapeaux de ceinture-et-bretelles sont vérifiés par le chemin de code de la fusion :

```jsonc
{
  "external_tools": [
    {
      "id": "understand-anything",
      "enabled": false,            // ← était true
      "sync_mode": "disabled",     // ← était "native_graph"
      "auto_refresh": false,       // optionnel : ne plus rafraîchir UA à chaque compile
      // ...le reste de l'entrée reste tel quel
    }
  ]
}
```

`enabled: false` fait que `_merge_configured_understand_anything_graph()` ignore entièrement l'outil. `sync_mode: "disabled"` est un garde-fou secondaire au cas où un futur bug ignorerait le drapeau `enabled`.

### Étape 2 — supprimer les artefacts obsolètes pour ne rien laisser derrière

Si vous avez précédemment exécuté un compile avec UA activé, les artefacts pollués sont toujours sur le disque :

```bash
rm -f .understand-anything/knowledge-graph.json
rm -f .tesserae/external/understand-anything.md
```

Tesserae ne régénère `.tesserae/external/understand-anything.md` que lorsque l'outil est activé, donc le supprimer est sans risque une fois l'étape 1 en place.

### Étape 3 — recompiler + nettoyer le vault Obsidian

```bash
tesserae compile
tesserae vault sync --prune-orphans
```

Le compile sautera la fusion UA, laissant le research graph exempt de Concepts issus d'UA. L'étape de nettoyage supprime toutes les pages orphelines du vault Obsidian qui pointaient vers des node_ids créés par la fusion.

## Vérification

Après la recompilation, le script d'audit ci-dessus devrait signaler zéro (ou presque zéro) nœud Concept provenant de `.understand-anything/knowledge-graph.json`. Une seconde vérification utile :

```bash
.venv/bin/python -c "
import json, re
from collections import defaultdict
nodes = json.load(open('.tesserae/graph.json'))['nodes']
concepts = [n for n in nodes if n['type']=='Concept']
def slug(s): return re.sub(r'[^a-z0-9가-힣]+','-',s.lower()).strip('-')
buckets = defaultdict(list)
for n in concepts: buckets[slug(n['name'])].append(n)
collisions = {s: ns for s, ns in buckets.items() if len(ns)>1}
print(f'{len(collisions)} Concept slug collision(s), {sum(len(ns)-1 for ns in collisions.values())} duplicate page(s)')
"
```

Devrait afficher `0 Concept slug collision(s), 0 duplicate page(s)` si le correctif a pris effet.

## Quand vous voulez réellement retrouver la navigation par code-graph

Le code graph d'UA est véritablement utile — arêtes d'appels/imports, hiérarchies de classes, etc. — quand il n'est pas noyé dans le bruit des titres de docs. Pour le réactiver proprement :

1. **Limitez UA lui-même au code, pas aux docs.** UA accepte des patterns d'inclusion/exclusion ; configurez-le pour ne parcourir que `src/`, `lib/`, `tesserae/`, etc. et excluez explicitement `docs/`, `README*.md` et `docs/i18n/`. Le bouton de configuration exact se trouve dans la documentation propre d'UA sur [Lum1104/Understand-Anything](https://github.com/Lum1104/Understand-Anything).
2. **Réactivez dans `.tesserae/config.json`** : remettez `enabled` à `true`, `sync_mode` à `"native_graph"`, `auto_refresh` à `true`.
3. **Recompilez** puis relancez l'audit. Une exécution UA propre devrait produire des Concepts qui correspondent à de vrais symboles de code (noms de fonctions, noms de classes, modules) plutôt qu'à des titres de sections en anglais.

L'asymétrie est frustrante — désactiver est un simple changement de config, réactiver proprement exige de comprendre le source-scoping d'UA — mais c'est la bonne frontière. Le rôle d'UA est de produire des code graphs, celui de Tesserae des research graphs, et la couture entre les deux ne devrait jamais laisser passer des titres de docs d'un côté à l'autre.

## Où cela s'inscrit

| Couche | Préoccupation | Configurée via |
|---|---|---|
| Le walker propre d'UA | Quels fichiers UA lit en premier lieu | Config d'UA (hors du périmètre Tesserae) |
| `auto_refresh` sur l'outil UA | Si `tesserae compile` ré-exécute UA | Entrée external_tools de `.tesserae/config.json` |
| `enabled` sur l'outil UA | Si Tesserae tient compte d'UA du tout | Entrée external_tools de `.tesserae/config.json` |
| `sync_mode` sur l'outil UA | Si les nœuds d'UA sont fusionnés dans le research graph | Entrée external_tools de `.tesserae/config.json` |

Les boutons `enabled` + `sync_mode` constituent la couture entre les deux projets. Les boutons walker + `auto_refresh` sont des préoccupations internes à UA.
