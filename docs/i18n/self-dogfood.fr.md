# Démo Self-dogfood

<!-- translations:start -->
<p align="center"><a href="../self-dogfood.md">English</a> · <a href="self-dogfood.ko.md">한국어</a> · <a href="self-dogfood.zh.md">中文</a> · <a href="self-dogfood.ja.md">日本語</a> · <a href="self-dogfood.ru.md">Русский</a> · <a href="self-dogfood.es.md">Español</a> · <a href="self-dogfood.fr.md">Français</a> · <a href="self-dogfood.de.md">Deutsch</a></p>
<!-- translations:end -->
Ce projet peut s'indexer lui-même. Le flux self-dogfood prouve que Tesserae peut être installé, configuré dans son propre dépôt, ingérer ses propres docs/source/tests/scripts, rafraîchir optionnellement Understand Anything et Cognee, compiler des artefacts de graphe et construire le frontend web statique.

Il exerce aussi la boucle d'auto-amélioration. Chaque compilation re-dérive l'état de mémoire mutable — `decay_score`, `access_count`, `confidence` et le flag `superseded` — vers une **table sidecar `node_memory`** à l'intérieur de `.tesserae/sqlite.db`. Ces scalaires vivent *uniquement* dans le sidecar et jamais dans `graph.json`, de sorte qu'une recompilation dogfood est byte-identical sur le graphe tandis que le sidecar suit le decay et la recurrence. Les insights qui reviennent dans `>= 3` sessions distinctes sont renforcés avec un confidence numérique dans `(0, 1]` (3 sessions → `0.5`, 4 → `0.75`, 5+ → `1.0`, plafonné), écrits dans le sidecar et exposés par l'outil MCP `fresh_insights`, qui masque par défaut les findings remplacés (superseded) par un near-duplicate plus récent.

## Commandes

Depuis la racine du dépôt :

```bash
# Vérifiez que la commande shell est installée.
./scripts/install.sh --dir "$PWD"
export PATH="$HOME/.local/bin:$PATH"

# (optionnel) installez le backend d'embeddings sémantiques par défaut.
pip install 'tesserae[semantic]'

# Configurez ce dépôt comme projet Tesserae.
tesserae project setup \
  --yes \
  --name tesserae_self \
  --source README.md \
  --source docs \
  --source tesserae \
  --source tests \
  --source scripts \
  --with-understand-anything \
  --install-understand-anything \
  --understand-anything-platform codex \
  --run-cognee \
  --install-cognee

# Compilez les sources configurées.
tesserae project compile

# Reconstruisez explicitement le frontend statique.
tesserae project build-site

# Servez localement.
tesserae project serve --port 8765
```

Ouvrir :

```text
http://127.0.0.1:8765/
```

## Workspace généré

La self-demo écrit les artefacts générés sous :

```text
.tesserae/
```

Artefacts clés :

```text
.tesserae/config.json
.tesserae/graph.json
.tesserae/manifest.json
.tesserae/sqlite.db          # graphe typé + sidecar node_memory + HarnessSessionsDB en direct
.tesserae/report.md
.tesserae/competitive_report.md
.tesserae/temporal_facts.jsonl
.tesserae/graphiti_episodes.jsonl
.tesserae/markdown_projection/
.tesserae/obsidian_vault/
.tesserae/agent_harness/
.tesserae/site/
.tesserae/cognee_bundle/
```

Le workspace généré n'est intentionnellement pas committé par défaut. Il est reproductible depuis les sources du dépôt avec les commandes ci-dessus.

## Dernière exécution vérifiée

Vérifié le `2026-04-27 11:11:23 KST` depuis le dépôt Tesserae lui-même.

```text
install command: ./scripts/install.sh --dir /Users/neo/Developer/Projects/Tesserae --skip-shell-config
setup command:   tesserae project setup --yes --name tesserae_self --source README.md --source docs --source tesserae --source tests --source scripts --with-understand-anything --install-understand-anything --understand-anything-platform codex --run-cognee --install-cognee
ingest command:  tesserae project ingest README.md docs --changed-only
compile command: tesserae project compile
site command:    tesserae project build-site
serve command:   tesserae project serve --host 0.0.0.0 --port 56821
local URL:       http://127.0.0.1:56821/
LAN URL:         http://192.168.45.130:56821/
```

Décompte final des artefacts :

```text
nodes:               667
edges:               1020
markdown notes:      684
obsidian notes:      686
agent harness files: 14
cognee nodes:        667
cognee edges:        1020
graphiti episodes:  1020
temporal facts:      1020
site files:          index.html, nodes/index.html, sources/index.html, graph/index.html, graph.json, search-index.json, llms.txt, llms-full.txt, manifest.json, assets/style.css, assets/app.js
node pages:          687
source pages:        56
```

Principaux types de nœuds :

```text
CodeFunction:    452
Dependency:       55
CodeClass:        54
Concept:          51
SourceFile:       47
SourceDocument:    7
CodeProject:       1
```

Vérification dans le navigateur :

```text
loaded title: Home · tesserae_self
visible stats: 667 nodes / 1020 edges / 55 sources / 7 types
sources page: source evidence table links to per-source pages
source detail: tesserae/frontend.py shows 41 nodes, 54 related edges, type mix, node links, and edge table
search smoke: StaticSiteBuilder returned CodeClass and StaticSiteBuilder.write_site results
console: no JavaScript errors on home, sources, source detail, or graph pages
server: TCP *:56821 LISTEN, serving via --host 0.0.0.0
```

## Ce que cela démontre

- Le chemin d'installation public fonctionne.
- La commande shell `tesserae` fonctionne.
- Un dépôt peut attacher un workspace `.tesserae` local au projet.
- Le markdown de recherche/documentation et les nœuds de graphe du code de développement peuvent coexister.
- Les projections Markdown, Obsidian, frontend, Graphiti, Cognee, SQLite, report et agent-harness sont produites depuis un seul pipeline de graphe.
- Le frontend HTML statique peut parcourir le graphe du projet sans étape de build JavaScript.
- La boucle d'auto-amélioration tourne et persiste : decay, compteurs d'accès, recurrence confidence et flags supersede atterrissent dans le sidecar `node_memory` sans perturber `graph.json`.
- La recherche hybride résout un vrai backend sémantique lorsque `tesserae[semantic]` est installé (ordre `auto` par défaut : model2vec → sentence-transformers → stub hash-bucket) ; sans lui, la recherche par embeddings se dégrade vers le stub hash-bucket non sémantique et émet un avertissement bruyant.
