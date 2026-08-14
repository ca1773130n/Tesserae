# Compagnon multimodal RAG-Anything

<!-- translations:start -->
<p align="center"><a href="../../integrations/rag-anything.md">English</a> · <a href="rag-anything.ko.md">한국어</a> · <a href="rag-anything.zh.md">中文</a> · <a href="rag-anything.ja.md">日本語</a> · <a href="rag-anything.ru.md">Русский</a> · <a href="rag-anything.es.md">Español</a> · <a href="rag-anything.de.md">Deutsch</a></p>
<!-- translations:end -->

[RAG-Anything](https://github.com/HKUDS/RAG-Anything) est un framework RAG multimodal (construit sur LightRAG) qui parse les PDF, documents Office, images et équations via MinerU/Docling/PaddleOCR. Tesserae l’intègre à la fois comme pipeline d’ingestion multimodale (projection de graphe native façon UA) et comme backend mémoire runtime optionnel.

## Pourquoi utiliser les deux ?

- Tesserae — mémoire d’agent de longue durée, compilation de wiki, projection de graphe.
- RAG-Anything — ingestion multimodale + récupération runtime LightRAG.

Les deux se complètent : RAG-Anything apporte la compréhension PDF/Office/image que les chargeurs de sources orientés texte de Tesserae n’offrent pas ; Tesserae garde la mémoire durable et interrogeable qui survit d’une session à l’autre.

## Workflow actuel à faible friction

Le chemin recommandé est l’assistant de configuration :

```bash
tesserae init
```

RAG-Anything est désormais une **invite interactive de l’assistant** plutôt qu’un
ensemble de drapeaux CLI. Quand l’assistant tourne, répondez aux invites
d’intégration :

- activez RAG-Anything quand demandé ;
- installez-le quand proposé (installe `raganything` + `docling`) ;
- choisissez le parseur `mineru` ;
- activez l’exécution de rafraîchissement post-installation quand offerte.

Puis compilez :

```bash
tesserae compile
```

Pour l’automatisation non interactive (CI), lancez l’assistant avec les défauts
(toutes les intégrations optionnelles OFF), puis activez RAG-Anything dans
`.tesserae/config.json` — l’assistant écrit la config d’intégration sous les
clés `external_tools` / `memory_backends` (voir les clés que cette doc
référence ci-dessous) — et lancez le rafraîchissement géré :

```bash
tesserae init --yes
# enable raganything in .tesserae/config.json (external_tools key)
tesserae integrations refresh raganything --parser mineru
tesserae compile
```

L’assistant de configuration installe `raganything` et `docling` ensemble. MinerU reste opt-in : installez-le avec `pip install 'mineru[core]'` seulement si vous avez des PDF ou des images à ingérer.

Tesserae stocke une commande de rafraîchissement gérée plutôt que de demander aux utilisateurs d’en inventer une :

```bash
tesserae integrations refresh raganything --parser mineru
```

Pendant la compilation, Tesserae :

1. vérifie si `.tesserae/external/raganything/manifest.json` existe et correspond au commit git courant (via le `meta.json#gitCommitHash` stocké) ;
2. lance le wrapper de rafraîchissement géré si manquant/périmé ou si `--refresh-external-tools` est passé ;
3. découvre les sources non-code (PDF, docs Office, images, markdown) et les parse via le parseur configuré ;
4. écrit `manifest.json` + `meta.json` ;
5. poursuit la compilation mémoire normale.

Vous pouvez forcer toutes les commandes de rafraîchissement externes configurées avant une compilation :

```bash
tesserae compile --refresh-integrations
```

## Équivalent manuel

```bash
pip install 'raganything[all]'
python -m tesserae.raganything_refresh --project . --parser mineru
tesserae compile
```

## Compile-time vs runtime

Tesserae scinde l’intégration proprement :

- **Parsing à la compilation** (`refresh-raganything` et `compile`) : lance les parseurs directement — lecture native pour `.md/.txt/.rst`, `docling.DocumentConverter` pour tout le reste. Le pipeline complet de RAG-Anything n’est *pas* invoqué ici, donc aucune clé LLM/embedding/vision n’est requise pour que la compilation réussisse.
- **Requêtes runtime** (`project ask`) : `raganything_query.py` instancie `RAGAnything` avec les fonctions LLM/embedding/vision configurées du projet et lance `aquery` contre le store de LightRAG. Ce chemin requiert des clés API.

Cette scission signifie que `compile` est rapide, déterministe et sans clé ; seules les opérations au moment de la récupération coûtent des tokens LLM.

## Synchronisation de graphe native

Tesserae importe le manifest parsé nativement pendant la compilation quand l’outil configuré utilise `sync_mode: native_graph`.

L’adaptateur natif lit `.tesserae/external/raganything/manifest.json`, projette chaque document parsé en un nœud `SourceDocument` avec des métadonnées de blocs multimodaux — et, pour chaque figure/tableau/équation avec contenu résolvable, un nœud `Artifact` comme preuve de première classe (id issu du hachage du contenu, `part_of` son document, adressable par `evidenced_by`) — puis écrit un manifest de sync :

```text
.tesserae/external/raganything-sync.json
```

Correspondance actuelle :

| RAG-Anything | Direction Tesserae |
|---|---|
| `documents[*]` | nœud `SourceDocument`, `metadata.parser="raganything"`, `metadata.content_hash` = sha256 de la source |
| `content_list[type=text]` | replié dans `SourceDocument.description` ; concepts via l’extracteur existant |
| `content_list[type=image]` | nœud `Artifact` (id issu du sha256 des **octets** de l’asset, légende comme description) + `SourceDocument.metadata.multimodal_blocks[]` (`img_path`, `caption`, `content_hash` clé de jointure) ; les assets non-résolubles sautent le nœud bruyamment (`skipped_blocks` dans le manifest de sync) |
| `content_list[type=table]` | nœud `Artifact` (id issu du sha256 du `table_body`, corps comme description) + `multimodal_blocks[]` (`table_body`, `caption`, `content_hash`) |
| `content_list[type=equation]` | nœud `Artifact` (id issu du sha256 du `latex`, LaTeX comme description) + `multimodal_blocks[]` et `metadata.equations[]` (LaTeX préservé) |

### Les faits par propriétaire chevauchent l'arête `part_of`

L'id d'un `Artifact` est amorcé à partir de son hash de contenu et rien d'autre, donc
le nœud est délibérément **doc-agnostique**: la même figure imprimée dans deux
documents est un nœud avec une arête `part_of` par propriétaire. Mais `kind`, `page`,
`caption` et le `ordinal` 1-basé par kind sont des faits sur la paire *(artifact,
document)* — conservés uniquement sur le nœud, un artifact partagé conserverait le
document qui a fusionné en premier et perdrait silencieusement la page de chaque
propriétaire ultérieur. Ils chevauchent l'arête, qui est par-propriétaire par
construction. Le nœud en garde ses propres copies pour la compatibilité rétroactive ;
ce qui s'ajoute, ne se déplace pas. Où les mêmes bytes apparaissent deux fois dans
un document, la position antérieure gagne, déterministement.

`evidence` sur cette arête reste nulle à dessein : chaque `edge.evidence` dans ce
code est un extrait littéral qui a autorisé une affirmation, et une caption
n'affirme rien.

### Atteindre les bytes

Un `Artifact` de **figure** affirme que les bytes d'une image existent — le nœud
n'existe que parce qu'ils ont été hachés à l'importation — donc le site les
sert. `tesserae export site` lit `metadata['asset_path']` comme source à part
entière, donnant à cette figure une page brute, une entrée sitemap, et ses bytes
sous `raw-assets/` sous un nom de fichier adressé au contenu dérivé du digest
que le graphe a déjà déclaré, jamais un re-hash. Un nom qui est une fonction pure
des bytes est ce qui fait `asset_site_path` ci-dessous un fait plutôt qu'une
prédiction.

Les tableaux et équations ne portent pas d'`asset_path` — leur contenu *est* la
description du nœud — et un asset hors-tree supprime la clé à l'importation.
Les deux sont correctement non-servables plutôt que des erreurs.

Via MCP, `raw_source` ne retourne jamais les bytes ; `drill_down` rapporte
l'adresse à la place — `asset_path` (sur disque), `asset_sha256`, et
`asset_site_path` (récupérable depuis un `tesserae serve` en cours
d'exécution). Un hash déclaré malformé supprime `asset_site_path` plutôt
que d'en inventer un.

### Les artifacts restent hors du canvas du graphe

`Artifact` est regroupé avec `EvidenceSpan` et chaque variante de Claim dans la
couche d'assertion, et toute la couche d'assertion est exclue de la vue graphe
interactive — délibérément et définitivement, pas en attente. C'est l'evidence
*pour* les nœuds du canvas plutôt qu'un pair d'eux, et deux raisons mécaniques
disent la même chose : l'evidence surpasse ce qu'elle soutient (l'inondation qui
a déjà mis `SourceDocument` derrière `show_sources`), et la seule arête d'un
`Artifact` est `part_of` vers un `SourceDocument`, qui est masqué par défaut —
donc l'admettre seul dessinerait des points orphelins inaccessibles. Lisez l'evidence
via `drill_down` et la page d'asset brute, c'est là qu'elle est adressable.

La provenance est préservée sur chaque nœud :

```json
{"system": "rag-anything", "id": "doc-<sha256>", "type": "document", "artifact": ".tesserae/external/raganything/manifest.json"}
```

Note : la vue graphe interactive masque par défaut les nœuds du groupe `sources` pour se concentrer sur les concepts et entités — les SourceDocuments raganything projetés restent dans `graph.json` (MCP, la recherche et les vues wiki par page les voient toujours), ils n’inondent simplement pas le canevas. Définissez `graph_view.show_sources = true` dans `.tesserae/config.json` pour restaurer la vue dense.

## Backend mémoire runtime

`memory_backends.raganything` (défaut produit par `default_raganything_backend_config`) est le seul backend mémoire optionnel. RAG-Anything est opt-in (défaut `enabled: false`) ; le drapeau de setup `--with-raganything` l’active.

### Fournisseur LLM (aucune clé API requise)

Le backend runtime de RAG-Anything a besoin d’un LLM pour répondre aux requêtes. Tesserae utilise par défaut ses intégrations CLI existantes basées sur OAuth — aucune clé API requise :

| Fournisseur | Comment il s’authentifie | Drapeau de setup |
|---|---|---|
| `codex` (défaut) | OAuth de la CLI `codex` (vous vous êtes connecté une fois avec `codex login`) | `--raganything-llm-provider codex` |
| `claude` | CLI `claude -p` ; respecte `CLAUDE_CONFIG_DIR` pour les configurations multi-comptes | `--raganything-llm-provider claude --raganything-claude-config-dir ~/.claude-personal2` |

Pour les configurations Claude multi-comptes (p. ex. `~/.claude-personal1`, `~/.claude-personal2`), passez `--raganything-claude-config-dir <path>` au setup. Le backend runtime exportera `CLAUDE_CONFIG_DIR=<path>` avant chaque invocation pour que l’auth du compte choisi soit utilisée sans toucher à votre `~/.claude` par défaut.

### Embeddings

| Fournisseur | Quand l’utiliser |
|---|---|
| `deterministic` (défaut) | Aucune dep externe. Basé sur du hachage ; qualité sémantique faible mais suffisante pour que LightRAG construise un index. Bonne base de référence pour prouver que l’intégration fonctionne. |
| `ollama` | Ollama local en marche avec un modèle d’embeddings (p. ex. `nomic-embed-text`). Passez `--raganything-embedding ollama` ; le backend utilise `http://localhost:11434` par défaut. |

Le support direct des embeddings OpenAI n’est pas câblé à travers ces drapeaux en v1 — les utilisateurs avec des clés OpenAI peuvent définir `OPENAI_API_KEY` et surcharger `memory_backends.raganything.embedding.provider` directement dans `.tesserae/config.json` (RAGAnything récupérera la variable d’env via les défauts de LightRAG).

### Invocation depuis la CLI

```bash
# `ask` never enters memory backends: it plans retrieval over the compiled
# graph and synthesizes a cited LLM answer (--no-llm = ranked hits only).
tesserae ask "What does the integration spec say about parser routing?"

# Explicit backends live on `query` — raw retrieval, no LLM synthesis.
tesserae query "..." --backend raganything
tesserae query "..." --backend wiki
```

`tesserae query --backend raganything` appelle `tesserae.raganything_query.query` directement. Un `working_dir` relatif dans `memory_backends.raganything` est résolu contre la racine du projet avant l’appel.

### `ask` de niveau supérieur (utilise le registre multi-projets)

Pour les workflows où vous voulez interroger plusieurs projets Tesserae enregistrés sans faire `cd` dans chacun, la commande de niveau supérieur `tesserae ask` résout le projet via le registre persistant partagé avec le serveur MCP :

```bash
# One-time: register your projects (saved to ~/.tesserae/registry.json).
tesserae projects register ~/Developer/Projects/Tesserae --name tesserae
tesserae projects register ~/Developer/Projects/Other --name other

# List registered projects.
tesserae projects list

# Ask from inside a project (the smart router picks the target otherwise).
tesserae ask "How does the parser routing work?"

# Ask a specific registered project by name.
tesserae ask "What is the architecture?" --name other

# Pass a direct path, or hit a memory backend explicitly via `query`.
tesserae ask "..." --project /tmp/somewhere
tesserae query "..." --backend raganything --json
```

La logique de dispatch — `--project > --name > routeur` — est implémentée dans le handler ask de niveau supérieur et le formatage des réponses est partagé avec l’outil MCP `ask` via `tesserae.query.ask_project` (les backends mémoire ne sont joignables qu’à travers `tesserae query --backend …`). Le registre est adossé à un fichier (`~/.tesserae/registry.json` par défaut), donc il persiste entre les sessions et reste en phase avec la liste de projets du serveur MCP.

#### Interroger plusieurs vaults à la fois (`--scope all-registered`)

Pari B2 — quand vous avez plusieurs projets enregistrés (vault de recherche, vault de travail, vault de projet perso) et que vous voulez poser la même question à tous, utilisez `--scope all-registered` :

```bash
# Fan out across every registered project. The aggregated envelope is
# {"scope": "all-registered", "question": "...", "by_project": {"<alias>": <envelope>}}.
tesserae ask "What did I write about RLHF?" --scope all-registered --json

# Restrict to a hand-picked subset of aliases.
tesserae ask "..." --scope all-registered --scope-aliases research side-projects
```

Le handler itère les projets enregistrés par ordre alphabétique, appelle `ask_project` sur chacun et agrège les enveloppes par projet. Un seul projet en échec — config manquante, RAG-Anything non activé — est capturé comme `{"error": "..."}` dans l’emplacement de cet alias et n’avorte jamais le reste du fan-out. Le même argument `scope` est accepté par l’outil MCP `ask`, donc les agents de codage pilotés par MCP obtiennent le même fan-out sans plomberie supplémentaire.

### Registre multi-projets (`tesserae projects`)

| Commande | Objet |
| --- | --- |
| `tesserae projects list [--json]` | Affiche les projets enregistrés (tous sont égaux — il n’y a pas de projet « actif »). |
| `tesserae projects register <path> [--name <alias>]` | Ajoute un projet au registre ; l’alias prend par défaut le nom de répertoire assaini. |
| `tesserae projects unregister <name>` | Retire une entrée du registre. |

Ces commandes opèrent directement sur `tesserae.mcp_server.ProjectRegistry` — sans aller-retour MCP — donc elles peuvent être scriptées sans lancer le serveur MCP.

### Invocation depuis MCP

Le serveur MCP stdio expose un outil `ask` avec le même sélecteur de backend :

```json
{
  "name": "ask",
  "arguments": {
    "question": "What does the integration spec say about parser routing?",
    "backend": "auto",
    "project": "tesserae"
  }
}
```

L’ordre de dispatch (`raganything` → recherche du wiki compilé) et la résolution de `working_dir` reflètent exactement le handler CLI, si bien que les agents de codage et les opérateurs humains convergent vers les mêmes réponses.

## Prérequis système

- **Python 3.10+** est requis pour RAG-Anything (le paquet amont `raganything` ≥1.3.0 dépend transitivement de `mineru[core]`, qui est Python 3.10+). Sur des Pythons plus anciens, Tesserae désactive l’intégration avec un avertissement clair plutôt que d’installer silencieusement un placeholder cassé.
- **LibreOffice** pour le parsing `.doc/.docx/.ppt/.pptx/.xls/.xlsx` — à installer séparément via le gestionnaire de paquets de votre plateforme. RAG-Anything saute les documents Office avec un avertissement quand LibreOffice est absent.
- **Les poids de modèle MinerU** sont téléchargés au premier parse et mis en cache (~Go). Les exécutions suivantes réutilisent le cache.
- **Des clés LLM/embedding/vision compatibles OpenAI** pour le backend mémoire runtime (`OPENAI_API_KEY`, `OPENAI_BASE_URL`). Le mode parseur seul ne requiert pas de clés.

## Routage des parseurs

Tesserae route automatiquement les sources vers le bon parseur par extension de fichier :

| Extension | Parseur | Raison |
|---|---|---|
| `.md`, `.markdown`, `.txt`, `.rst` | `docling` | Léger ; pas de téléchargement de modèle MinerU. |
| `.doc`, `.docx`, `.ppt`, `.pptx`, `.xls`, `.xlsx` | `docling` | Meilleure préservation de la structure Office selon l’amont. |
| `.pdf`, `.png`, `.jpg`, `.jpeg`, `.gif`, `.bmp`, `.tiff`, `.webp` | défaut configuré (`--raganything-parser`, défaut `mineru`) | OCR + extraction de tableaux. |

Le wrapper géré `tesserae integrations refresh raganything` expose `--parser` (le défaut configuré pour PDF/images), `--parse-method {auto,ocr,txt}`, `--root` (répétable, restreint à un sous-arbre), `--force` et `--full`. Le routage texte/office par bucket est fixe (les deux utilisent `docling` par défaut). Pour surcharger explicitement le parseur texte ou office, appelez le module sous-jacent directement — `python -m tesserae.raganything_refresh --text-parser <p> --office-parser <p>` — qui expose ces deux drapeaux supplémentaires. Le défaut configuré s’applique toujours aux PDF et images.

Avant que la boucle de parsing ne tourne, Tesserae sonde si le paquet Python de chaque parseur requis est importable (`importlib.import_module(...)`) et échoue vite avec une seule erreur agrégée listant chaque parseur manquant et sa commande d’installation. Nous n’utilisons délibérément pas l’amont `RAGAnything.check_parser_installation()` parce qu’il n’inspecte que le parseur configuré sur l’instance et intègre des vérifications de disponibilité des poids de modèle qui ne conviennent pas à une étape de pré-vol.

Tesserae choisit aussi le parseur au moment de la construction de `RAGAnything` depuis la distribution de routage réelle (le parseur le plus souvent choisi gagne) plutôt que depuis `--raganything-parser` directement. Cela évite le mode de défaillance où `RAGAnything.__init__` tente d’initialiser un parseur lourd (p. ex. `mineru`) dont les poids de modèle ne sont pas encore sur disque et bousille toute l’exécution avant que les surcharges `parser=` par appel puissent prendre effet. Le drapeau `--raganything-parser` contrôle toujours le défaut pour les sources non-texte et non-Office (PDF, images).

### Paquets de parseurs

Le chemin de parse à la compilation utilise `docling.DocumentConverter` directement pour chaque source non textuelle ; installez-le une fois et vous êtes couvert :

| Parseur | Commande d’installation |
|---|---|
| `docling` (défaut compile-time pour tout sauf le texte natif) | fourni quand vous lancez `--with-raganything --install-raganything` (ou `pip install docling` en autonome) |
| `paddleocr` (alternative OCR optionnelle) | `pip install 'raganything[paddleocr]>=1.3.0'` et `pip install paddlepaddle` (wheel spécifique à la plateforme) |

> Note : `mineru` n’est actuellement **pas invoqué à la compilation**. Le chemin de compilation contourne le pipeline complet de RAG-Anything (qui exigerait des callables LLM/embedding/vision) et route chaque source non textuelle à travers docling directement. Le support MinerU est réservé à un futur chemin d’import direct qui ingérera un `content_list.json` produit à l’extérieur.

Quand un parseur configuré est manquant, `refresh-raganything` échoue vite — listant chaque parseur manquant dans une seule erreur avec la bonne commande d’installation — au lieu de cascader des échecs par fichier.

### Widget ask par page

Chaque page de détail (concept, paper, repo, synthèse, entité, topic, question, source) inclut un widget inline « ask about this page ». Il fait un POST vers `/api/ask` sur l’instance locale `tesserae serve`, qui appelle `tesserae.query.ask_project` et rend la réponse inline. Contrairement à la CLI (où `tesserae ask` est LLM-par-défaut), `/api/ask` fait défaut à la **récupération non-LLM** pour la latence du widget ; envoyez `{"llm": true}` dans le payload pour opter pour la réponse planifiée/synthétisée. Le widget préfixe le nom de nœud de la page courante à la question de l’utilisateur comme indice de contexte en langage naturel (p. ex. `` About `<NodeName>`: <question> ``) ; une future PR pourra câbler un vrai scoping de sous-graphe dans `ask_project` lui-même.

Le widget détecte la disponibilité du backend via `/api/ask/health` au chargement. Quand le wiki est servi statiquement (GitHub Pages, `file://`, S3, n’importe quel hébergeur statique), le widget se replie en une note d’une ligne pointant les lecteurs vers `tesserae serve` pour l’usage interactif local. Aucune requête n’échoue et rien ne bloque le rendu de la page — le widget est un îlot JS différé, séparé du bundle de graphe plus lourd.

Appariez cela avec le registre multi-projets (`tesserae projects register`) et vous pouvez interroger le wiki de n’importe quel projet enregistré depuis n’importe laquelle de ses pages de détail.

## Principe de collaboration

Tesserae reste le compilateur de mémoire. RAG-Anything reste un compagnon indépendant : un parseur multimodal + un moteur de récupération LightRAG.
