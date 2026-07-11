# Démo self-dogfood

<!-- translations:start -->
<p align="center"><a href="../self-dogfood.md">English</a> · <a href="self-dogfood.ko.md">한국어</a> · <a href="self-dogfood.zh.md">中文</a> · <a href="self-dogfood.ja.md">日本語</a> · <a href="self-dogfood.ru.md">Русский</a> · <a href="self-dogfood.es.md">Español</a> · <a href="self-dogfood.fr.md">Français</a> · <a href="self-dogfood.de.md">Deutsch</a></p>
<!-- translations:end -->
Ce projet peut s’indexer lui-même. Le flux self-dogfood prouve que Tesserae peut être installé, configuré à l’intérieur de son propre dépôt, ingérer ses propres docs/sources/tests/scripts, éventuellement rafraîchir RAG-Anything, compiler les artefacts de graphe et construire le frontend web statique.

Le même flux sert aussi de test de fumée multimodal. Avec RAG-Anything installé (`tesserae setup --install raganything`) et activé dans `.tesserae/config.json` (`memory_backends.raganything.enabled: true`), la compilation dogfood pointe RAG-Anything vers le markdown de `docs/` de Tesserae lui-même plus les images de `docs/assets/` et d’`assets/` au niveau projet. Cela valide le pipeline multimodal contre un vrai corpus non-code appartenant au projet — couvrant les captures d’écran et diagrammes que les chargeurs de sources orientés texte sautent — sans inventer un jeu de fixtures séparé.

Il exerce aussi la boucle d’auto-amélioration. Chaque compilation re-dérive
l’état de mémoire mutable — `decay_score`, `access_count`, `confidence` et le
drapeau `superseded` — dans une table sidecar **`node_memory`** à l’intérieur de
`.tesserae/sqlite.db`. Ces scalaires vivent dans le sidecar *uniquement* et
jamais dans `graph.json`, si bien qu’une compilation dogfood fraîche est
octet-pour-octet identique côté graphe pendant que le sidecar suit la décrue et
la récurrence. Les insights qui reviennent dans `>= 3` sessions distinctes sont
renforcés avec une confiance numérique dans `(0, 1]`
(3 sessions → `0.5`, 4 → `0.75`, 5+ → `1.0`, plafonné), écrite dans le sidecar
et exposée par l’outil MCP `fresh_insights`, qui masque par défaut les constats
supplantés par un quasi-doublon plus récent.

## Commandes

Depuis la racine du dépôt :

```bash
# Ensure the shell command is installed.
./scripts/install.sh --dir "$PWD"
export PATH="$HOME/.local/bin:$PATH"

# (optional) install the default semantic embedding backend.
pip install 'tesserae[semantic]'

# Set up this repository as an Tesserae project.
tesserae init \
  --yes \
  --name tesserae_self \
  --source README.md \
  --source docs \
  --source tesserae \
  --source tests \
  --source scripts

# (optional) install + enable the heavier companions afterwards:
#   tesserae setup --install raganything
#   then flip memory_backends.*.enabled / external_tools in .tesserae/config.json

# Compile the configured sources.
tesserae compile

# Rebuild the static frontend explicitly.
tesserae export site

# Serve locally (auto-builds the site first if needed).
tesserae serve --port 8765
```

Ouvrez :

```text
http://127.0.0.1:8765/
```

## Espace de travail généré

La démo self écrit les artefacts générés sous :

```text
.tesserae/
```

Artefacts clés :

```text
.tesserae/config.json
.tesserae/graph.json
.tesserae/manifest.json
.tesserae/sqlite.db          # typed graph + node_memory sidecar + live HarnessSessionsDB
.tesserae/report.md
.tesserae/competitive_report.md
.tesserae/temporal_facts.jsonl
.tesserae/graphiti_episodes.jsonl
.tesserae/markdown_projection/
.tesserae/obsidian_vault/
.tesserae/agent_harness/
.tesserae/site/
```

L’espace de travail généré n’est volontairement pas commité par défaut. Il est reproductible depuis les sources du dépôt avec les commandes ci-dessus.

## Dernière exécution vérifiée

Vérifiée le `2026-04-27 11:11:23 KST` depuis le dépôt Tesserae lui-même.

Les opt-ins d’intégration (RAG-Anything) sont désormais des
**invites interactives de l’assistant**, pas des drapeaux CLI. L’équivalent
non interactif ci-dessous lance `tesserae init --yes` (intégrations OFF),
active les intégrations dans `.tesserae/config.json` (l’assistant les écrit
sous les clés `memory_backends` et `external_tools` — voir les docs
d’intégration pour les clés exactes), puis rafraîchit chacune avant de
compiler.

```text
install command: ./scripts/install.sh --dir /Users/neo/Developer/Projects/Tesserae --skip-shell-config
setup command:   tesserae init --yes --name tesserae_self --source README.md --source docs --source tesserae --source tests --source scripts
                 # then enable the optional integrations in .tesserae/config.json and run:
                 #   tesserae integrations refresh raganythingingest command:  tesserae compile README.md docs --changed-only
compile command: tesserae compile
site command:    tesserae export site
serve command:   tesserae serve --host 0.0.0.0 --port 56821
local URL:       http://127.0.0.1:56821/
LAN URL:         http://192.168.45.130:56821/
```

Comptes d’artefacts finaux :

```text
nodes:               667
edges:               1020
markdown notes:      684
obsidian notes:      686
agent harness files: 14
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

Vérification navigateur :

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

- Le chemin d’installation public fonctionne.
- La commande shell `tesserae` fonctionne.
- Un dépôt peut s’attacher un espace de travail `.tesserae` local au projet.
- Le markdown de recherche/documentation et les nœuds de graphe de code de développement peuvent coexister.
- Les projections Markdown, Obsidian, frontend, Graphiti, SQLite, rapport et harness d’agent sont produites depuis un seul pipeline de graphe.
- Le frontend HTML statique peut parcourir le graphe du projet sans étape de build JavaScript.
- La boucle d’auto-amélioration tourne et persiste : décrue, compteurs d’accès, confiance de récurrence et drapeaux de supplantation atterrissent dans le sidecar `node_memory` sans perturber `graph.json`.
- La récupération hybride résout un vrai backend sémantique quand `tesserae[semantic]` est installé (ordre `auto` par défaut : model2vec → sentence-transformers → stub hash-bucket) ; sans lui, la récupération par embeddings se dégrade vers le stub hash-bucket non sémantique et émet un avertissement bruyant.
