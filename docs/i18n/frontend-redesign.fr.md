# Refonte du frontend — parcours annoté des routes

<!-- translations:start -->
<p align="center"><a href="../frontend-redesign.md">English</a> · <a href="frontend-redesign.ko.md">한국어</a> · <a href="frontend-redesign.zh.md">中文</a> · <a href="frontend-redesign.ja.md">日本語</a> · <a href="frontend-redesign.ru.md">Русский</a> · <a href="frontend-redesign.es.md">Español</a> · <a href="frontend-redesign.fr.md">Français</a> · <a href="frontend-redesign.de.md">Deutsch</a></p>
<!-- translations:end -->
Ce document propose une visite guidée de toutes les routes visibles du site statique Tesserae refondu. Il complète le modèle de haut niveau dans [`architecture.md`](architecture.fr.md) et le tableau d’état dans [`feature-map.md`](feature-map.fr.md).

Après `tesserae project compile`, le site se trouve dans `.tesserae/site/`. Pour l’explorer localement :

```bash
tesserae project serve --port 8765
# open http://127.0.0.1:8765/
```

Chaque route est un fichier HTML statique accompagné de deux fichiers voisins (`<page>.txt`, `<page>.json`) pour les consommateurs IA. Les exports à l’échelle du site (`llms.txt`, `llms-full.txt`, `graph.jsonld`, `sitemap.xml`, `rss.xml`, `robots.txt`, `ai-readme.md`, `manifest.json`) sont décrits à la fin de ce document.

Légende des statuts : ✅ livré · ⚠ en cours.

## Conventions sur toutes les pages

Chaque page feuille suit la même anatomie (§3.3 de la spécification de design) :

```
breadcrumbs
eyebrow (type · last updated · ≈ reading time)
TITLE
right-rail TOC (sticky on desktop, drawer on mobile)
lead paragraph
rendered markdown body
Mentions in the corpus  — bullets with badges + counts
Related (4-signal ranked) — card grid
Source provenance       — file path, line excerpt
Activity                — sparkline, weekly mentions
AI siblings footer      — links to the .txt and .json
```

Chrome global du site :

- **Barre supérieure.** Logo + nom du projet à gauche, déclencheur de recherche + bascule de thème à droite.
- **Rail gauche** (desktop ≥ 1024 px) : arbre hiérarchique — Home, Recent activity, Sources, Concepts, Entities, Papers, Repos, Topics, Syntheses, Open questions, Sessions, Timeline, Graph view, About. Les compteurs viennent de `manifest.json`.
- **Navigation basse** (mobile) : le rail drawer se replie ; la navigation basse expose les types les plus utilisés.
- **Palette de recherche.** `cmd+k` / `ctrl+k` / `/` — correspondance floue sur `search-index.json`, limitée aux types wiki. Les pages récentes sont conservées dans `localStorage`.
- **Thème.** Clair par défaut ; la bascule persiste `data-theme="dark"` dans `localStorage`. Appliqué avant le rendu initial pour éviter le flash.

## Home

### `/` ✅

> _Screenshot: home.png_

La page d’accueil est le pouls du projet. Elle est pilotée par la synthèse globale `pulse` (`wiki/syntheses/pulse.md`), régénérée à chaque compilation. Le hero est une rangée de statistiques — sources, concepts, papers, open questions — suivie de 1 à 3 cartes "what's new this week" tirées du corps `pulse` le plus récent. Sous le hero, des points d’entrée éditorialisés renvoient vers la page d’index de chaque type, afin qu’un nouveau visiteur puisse explorer sans devoir lire la navigation.

C’est la première page sur laquelle poser un agent LLM ; elle fournit le résumé du corpus avec le meilleur rapport signal/bruit. Les cartes mènent à des pages feuilles, pas vers les index.

**Interactions notables.** Les clics sur la rangée de statistiques font défiler vers l’index du type correspondant ou y naviguent. Le texte du hero est éditable : si vous écrivez `wiki/overview.md` à la main, la page d’accueil le récupère à la compilation suivante.

**Routes liées.** [Timeline](#timeline) pour le journal d’activité, [Syntheses](#syntheses) pour le format long, [Graph](#graph-view) pour la vue spatiale.

## Sources

Ce sont les documents bruts L1 — fichiers dans `data/`, `docs/` et dans l’arborescence du projet référencée par `.tesserae/config.json`. Chaque source devient un nœud `SourceDocument` (ou `Paper` / `Repository`) et reçoit une page wiki projetée par `WikiLayerProjector`.

### `/sources/` ✅

> _Screenshot: sources-index.png_

Un tableau de tous les documents bruts connus du corpus. Colonnes : badge de type (Document / Paper / Repository / Project), titre, nombre de mentions, dernière mise à jour. Le tableau est zébré, le survol affiche un aperçu d’une ligne, et le badge de type est filtrable via la palette de recherche (`/ kind:papers`).

C’est l’index de l’agent lorsqu’il doit énumérer les preuves littérales sur lesquelles la wiki est construite.

**Routes liées.** [Papers](#papers) pour la tranche limitée aux articles, [Repos](#repos) pour les dépôts seulement, [Concepts](#concepts) pour la vue extraite.

### `/sources/<slug>.html` ✅

> _Screenshot: source-detail.png_

Un document source unique, rendu par le pipeline markdown stdlib (`tesserae/site/markdown.py`). Le corps est le markdown original avec rendu sûr des liens et images. Sous le corps :

- **Mentions** — chaque concept / entity / paper extrait de cette source, avec badges de type d’arête.
- **Backlinks** — toutes les autres pages wiki qui pointent ici.
- **Related** — classement à quatre signaux (direct link 3.0 + source overlap 4.0 + Adamic-Adar 1.5 + type affinity 1.0).
- **Source provenance** — chemin du fichier original + les 12 premières lignes du fichier brut comme preuve.
- **Activity** — sparkline des mentions hebdomadaires sur les 12 dernières semaines.
- **AI siblings footer** — vue texte brut `<page>.txt`, enregistrement structuré `<page>.json`.

**Interactions notables.** URL et IDs arXiv auto-liés dans le corps ; click-to-copy sur les blocs de code ; le TOC du rail droit suit le défilement.

## Concepts

Concepts désigne les idées, termes, algorithmes, architectures et méthodologies récurrents extraits dans tout le corpus. Ils couvrent `Concept`, `TechnicalTerm`, `Algorithm`, `MathematicalConcept`, `MethodologicalConcept`, `ArchitecturePattern`, `TrainingParadigm`, `InferenceStrategy`, `EvaluationProtocol`, `Task`, `Capability`, `ObjectiveFunction`.

### `/concepts/` ✅

> _Screenshot: concepts-index.png_

Une grille de cartes filtrable par facettes. Chaque carte porte le badge de type, le titre, une définition d’une ligne, le nombre de mentions et la date de dernière mise à jour. La page prend en charge les filtres par type via des tag chips (Algorithm, Architecture, Training paradigm, …) et trie par nombre de mentions par défaut.

C’est ici qu’on va pour demander « de quoi parle ce corpus ? »

**Routes liées.** [Papers](#papers) — les concepts sont généralement introduits dans des articles, [Topics](#topics) — les concepts se regroupent en thèmes.

### `/concepts/<slug>.html` ✅

> _Screenshot: concept-detail.png_

Une page de concept riche avec une définition synthétisée (ou le premier paragraphe de la source la plus autorisée si aucune synthèse n’est disponible), une liste de toutes les mentions dans le corpus, des voisins liés classés et la sparkline d’activité.

La section "Mentions in the corpus" est essentielle pour les agents : elle liste chaque article / source / dépôt qui référence le concept, avec un extrait d’une ligne pour le contexte.

**Interactions notables.** Le TOC du rail droit suit les `<h2>` / `<h3>` du corps ; la grille de cartes liées respecte le score à quatre signaux afin que les voisins paraissent pertinents plutôt que simplement adjacents.

## Entities

Entities sont les choses nommées et identifiables du corpus : `Model`, `Dataset`, `Benchmark`, `Metric`, `Organization`, `Person`. Elles sont projetées depuis les nœuds du graphe, et leurs pages mettent l’accent sur les affirmations et résultats plutôt que sur la prose.

### `/entities/` ✅

> _Screenshot: entities-index.png_

Un tableau facetté par type. Colonnes : badge de type, nom, summary (première phrase du frontmatter `description` si présente, sinon premier paragraphe du corps), nombre de mentions. Filtrable par type chip.

### `/entities/<slug>.html` ✅

> _Screenshot: entity-detail.png_

La page de détail met trois sections en avant :

- **Claims** — arêtes `ContributionClaim`, `PerformanceClaim`, `ComparisonClaim`, `LimitationClaim`, `CausalClaim` qui touchent cette entité, avec extraits de preuves inline. (Les nœuds Claim eux-mêmes n’ont pas leur propre URL : ils apparaissent ici sous forme de puces.)
- **Reported results** — chaque `Result` / `evaluated_on` / `reports_result` lié à cette entité, listé avec metric + score + provenance de l’article.
- **Mentions in the corpus** — même forme que sur les pages de concepts.

C’est la bonne page lorsqu’un agent doit répondre « que savons-nous du model X ? » ou « sur quels datasets le benchmark Y est-il rapporté ? »

## Papers

Papers correspond à la littérature de recherche traitée comme preuve de première classe. La refonte les a sortis du pool générique des sources et leur a donné un type dédié afin de rendre des affordances propres aux articles.

### `/papers/` ✅

> _Screenshot: papers-index.png_

Une grille de cartes filtrable par facettes avec des chips year, topic et family. Chaque carte : titre, auteurs (les trois premiers + "et al."), extrait d’abstract d’une ligne, badge d’année, nombre de mentions. Tri par année décroissante par défaut.

### `/papers/<slug>.html` ✅

> _Screenshot: paper-detail.png_

Une mise en page de carte d’article : titre, auteurs, année, extrait d’abstract, puis sections pour :

- **Contributions** — arêtes `ContributionClaim` depuis l’article.
- **Results** — arêtes `reports_result` avec metric + score.
- **Comparisons** — arêtes `compares_against`.
- **Related concepts** — arêtes `introduces` / `extends` / `uses`.
- **Open questions** — `OpenQuestion` liées via l’article.

Les liens arXiv s’auto-lient via `tesserae/site/markdown.py` ; le TOC du rail droit reflète la liste de sections ci-dessus.

## Repos

Repos désigne les projets logiciels : `Repository`, `Project`, `CodeProject`. La refonte ne rend explicitement pas de pages HTML par classe / fonction ; les pages de dépôts résument la surface du projet et pointent vers l’arborescence source.

### `/repos/` ✅

> _Screenshot: repos-index.png_

Une grille de cartes avec badges de langage. Chaque carte : nom, extrait README d’une ligne, langage(s) principal(aux), nombre d’étoiles si connu, dernière mise à jour.

### `/repos/<slug>.html` ✅

> _Screenshot: repo-detail.png_

La page de dépôt affiche :

- **README excerpt** — les ~600 premiers caractères du `README.md` du dépôt s’il est dans le corpus.
- **Dependencies** — arêtes sortantes de type `depends_on` / `imports` / `uses` vers d’autres repos / models / concepts.
- **Implements** — arêtes `implemented_in` depuis les articles (c.-à-d. « ce repo implémente paper X »).
- **Module overview** — compteurs de modules / classes / fonctions, mais aucun lien par fonction — par design.
- **Related** — classement à quatre signaux.

C’est la bonne page pour un agent qui doit choisir un dépôt dans une famille d’approches.

## Topics

Topics regroupe les concepts en domaines plus larges : `ResearchField`, `ResearchTopic`, `ProblemArea`, `ApproachFamily`, `Trend`. Les pages de thèmes sont en partie projetées depuis les nœuds du graphe, en partie synthétisées — voir [Syntheses](#syntheses) pour les pages d’aperçu de champ qui alimentent l’introduction d’un thème.

### `/topics/` ✅

> _Screenshot: topics-index.png_

Une grille de cartes indexée par type chip. Chaque carte expose le nom du thème, le champ parent et les stats "X papers · Y concepts · Z repos".

### `/topics/<slug>.html` ✅

> _Screenshot: topic-detail.png_

Une page de thème commence par un paragraphe de synthèse (lorsqu’il existe dans `wiki/syntheses/topic-<slug>.md`) et liste :

- **Papers in this topic** — tableau.
- **Approach families** — sous-thèmes de type `ApproachFamily`.
- **Concepts in scope** — nuage de chips.
- **Open questions** — nœuds `OpenQuestion` liés.
- **Related fields** — voisins `belongs_to` / `rising_in`.

**Routes liées.** [Syntheses → topic-…](#syntheses) pour le récit long, [Concepts](#concepts) pour les atomes constitutifs.

## Syntheses

Syntheses sont les pages de niveau supérieur produites par `SynthesisProjector`. Sept types (pulse, daily_digest, weekly, topic, comparison, field_overview) couvrent les découpages temporels et structurels du corpus. Aujourd’hui, les corps de synthèse sont des templates déterministes ; `TESSERAE_SYNTHESIS_LLM=1` est le hook d’upgrade LLM (stub).

### `/syntheses/` ✅

> _Screenshot: syntheses-index.png_

L’index liste chaque synthèse regroupée par type, triée par `generated_at` décroissant dans chaque groupe. Chaque ligne : kind badge, titre, lead d’une ligne, timestamp generated-at.

### `/syntheses/<slug>.html` ✅

> _Screenshot: synthesis-detail.png_

Une page de synthèse rend le corps markdown tel quel. Le frontmatter porte `synthesis_kind`, `slug`, `sources`, `inputs`, `generated_at`, `generator`, `content_hash` — la page expose `synthesis_kind` et `generated_at` dans l’eyebrow. Sous le corps :

- **Sources consumed** — les cibles d’arêtes `summarizes` — une par source utilisée par la synthèse.
- **Inputs (graph nodes)** — les cibles d’arêtes `synthesizes` — chaque nœud qui a servi d’entrée.
- **Related syntheses** — même type / inputs chevauchants.

La synthèse `pulse` est la page d’accueil ; les synthèses daily / weekly ancrent le rail [Timeline](#timeline).

## Questions

Open questions sont extraites du corpus sous forme de nœuds `OpenQuestion` — endroits où un article, une source ou une synthèse signale explicitement un problème non résolu.

### `/questions/` ✅

> _Screenshot: questions-index.png_

Vue en liste, une ligne par question ouverte. Colonnes : texte de la question, sources qui l’ont soulevée, concepts liés, âge (depuis la première apparition). Tri par récence par défaut.

### `/questions/<slug>.html` ✅

> _Screenshot: question-detail.png_

Une page centrée sur une seule question ouverte avec :

- Le texte exact de la question.
- **Raised in** — sources / papers / syntheses où la question apparaît.
- **Related concepts** — ce dont parle la question.
- **Adjacent questions** — même source ou concepts partagés.

C’est la page sur laquelle arriver lorsqu’on demande à un agent « qu’est-ce qui reste sans réponse dans ce domaine ? »

## Sessions

Sessions sont des transcripts locaux AI-harness importés, normalisés dans `.tesserae/harness_sessions/`, puis rendus comme mémoire de projet consultable. L’import est explicite via `tesserae project sessions discover --import` ou `tesserae project sessions import ...` ; les builds de site normales ne consomment que des enregistrements déjà normalisés.

### `/sessions/` ✅

> _Screenshot: sessions-index.png_

L’index sessions regroupe les sessions Claude Code et Codex de niveau supérieur pour le projet. Cartes/tableaux exposent title, harness, model, project path, start/end timestamps, message count, tool count, token counts when known, files touched, commands et preview text. La page est liée depuis le rail global, les Browse cards de l’accueil et les entrées de palette de recherche de type `session`.

### `/sessions/<project>/<session>.html` ✅

> _Screenshot: session-detail.png_

Une page de détail session utilise le shell partagé plutôt qu’un dump brut de transcript. La mise en page inclut un hero, une stat strip, High-Level Summary, Timeline & size, decisions/files/commands/tools/errors, un arbre subagent replié et un bloc de conversation tour par tour.

Le rail gauche propre à la session remplace le rail générique d’arborescence de fichiers par des ancres de tours user/assistant (`#turn-N`). Les tours utilisateur et assistant passent par le renderer markdown du site ; les surfaces sémantiques comme inline code, command/tag markup, paths, filenames et hashtags deviennent des chips compacts. Les tool calls sont repliés sous le tour assistant précédent, avec des arrière-plans code/tool sombres en thèmes clair comme sombre.

La typographie actuelle des détails garde la prose normale de conversation compacte à 8 px, les code fences génériques de conversation à 10 px, le contenu fenced bash/shell code à 11 px, tool details/summary à 10 px, tool headers à 8 px et tool payload text à 6 px. Voir [`session-history.md`](session-history.fr.md) pour la carte des sélecteurs et la checklist de confidentialité de publication.

## Timeline

La page timeline est le journal d’activité : quand le corpus a-t-il grandi, quels types de nœuds ont été ajoutés, à quoi cela ressemble-t-il à travers jours et semaines ?

### `/timeline/` ✅

> _Screenshot: timeline.png_

La page comporte trois rails :

- **Activity heatmap** — SVG de 26 semaines avec libellés de mois + jours de semaine, cellules colorées selon node-add-count. Chaque cellule pointe vers la page source `digest.md` du jour lorsqu’elle existe.
- **Days** — les 60 derniers jours datés, chaque ligne affichant `<date> · X activity · Y papers`. Lorsque la date possède un `digest.md`, la date est un lien.
- **Syntheses rail** — chaque synthèse triée par récence, kind badge + title + timestamp.

C’est la page à mettre en favori pour la question « qu’est-ce qui s’est passé récemment ? »

### `/timeline/<YYYY-MM-DD>.html` ✅

> _Screenshot: timeline-day.png_

Les pages de détail par jour — listant chaque paper / repo / concept / synthesis lié à ce jour calendaire — sont désormais livrées. `render_timeline_day` (`tesserae/site/pages.py`) est émis pour chaque jour daté via `StaticSiteBuilder` (`tesserae/site/__init__.py`), et chaque page est enregistrée dans `manifest.json` comme une route `timeline_day`. Les cellules heatmap pointent directement vers la page du jour (ne se rabattant sur la page source `digest.md` du jour que lorsqu’aucune route par jour n’existe).

## Graph view

### `/graph/` ✅

> _Screenshot: graph.png_

La vue graphe interactive est un 3D force layout (3d-force-graph + Three.js, vendored comme snapshot CDN dans `assets/`) avec fallback 2D. Les nœuds sont colorés par `ResearchNodeType`. Les arêtes affichent leur type comme libellé au survol.

**Interactions notables.**

- Survoler un nœud → tooltip avec nom, type, nombre de mentions.
- Cliquer un nœud → naviguer vers sa page wiki.
- Survoler une arête → libellé montrant la relation (`uses` / `extends` / `compares_against` / …).
- Molette de souris → zoom ancré au curseur (zoome vers le curseur, pas vers le centre).
- Glisser → orbit (3D) ou pan (2D).
- Basculer 2D/3D en haut à droite.

Le payload intégré à la page est plafonné à `MAX_GRAPH_NODES = 1500` (voir [`pages.py`](../../tesserae/site/pages.py)). Le graphe complet est toujours disponible à `/graph.json` pour l’outillage. Les nœuds code-graph (`CodeClass`, `CodeFunction`, `Dependency`, …) sont filtrés de la visualisation par design.

**Routes liées.** Chaque page wiki renvoie vers une vue ciblée du sous-graphe.

## About

### `/about.html` ✅

> _Screenshot: about.png_

Une page statique qui explique le schéma (chaque `ResearchNodeType` et la whitelist d’arêtes, avec descriptions d’une ligne), les infos de build (commit SHA, generator version, timestamp generated-at) et l’inventaire des AI exports.

C’est la bonne page pour ancrer un nouveau contributeur : quels types existent et à quoi sert chacun.

## AI siblings — comment chaque page est aussi de la donnée

Tesserae livre chaque page sous trois formes : le HTML humain, un voisin texte brut et un voisin JSON structuré. Plus des exports à l’échelle du site pour crawlers et agents.

### Per-page `<page>.txt` ✅

Vue texte brut d’une page — pas de nav, pas de styling, pas de décoration markdown au-delà de ce que le corps utilise explicitement. Utile lorsqu’un agent veut ingérer une seule page comme contexte sans écrire de scraper HTML.

```bash
curl http://127.0.0.1:8765/concepts/diffusion-model.txt
```

### Per-page `<page>.json` ✅

Un enregistrement structuré :

```json
{
  "title": "...",
  "kind": "concepts",
  "body": "raw markdown body",
  "body_text": "plain-text body",
  "links": ["..."],
  "source_path": "data/...",
  "frontmatter": { ... }
}
```

Utile lorsqu’un outil a besoin d’un accès typé — la liste des liens, le frontmatter, le kind tag — sans parseur markdown.

### `/llms.txt` ✅

L’index court llmstxt.org. Une seule page listant chaque type avec les entrées les plus pertinentes par type. Adapté à la première requête qu’un agent LLM fait en découvrant le site.

### `/llms-full.txt` ✅

Le format long llmstxt.org : toutes les pages wiki concaténées. Plafonné à 5 MB ; si le plafond est atteint, un marqueur `[TRUNCATED — see graph.jsonld for the full set]` termine le fichier. Adapté lorsqu’un agent a le budget pour ingérer tout le corpus en une requête et un contexte de 5 MB.

### `/graph.json` ✅

Le payload complet `ResearchGraph` — y compris les nœuds code-graph qui n’ont pas de pages HTML. Adapté lorsqu’un outil veut le graphe complet pour sa propre analyse (les consommateurs MCP, Cognee, Graphiti lisent tous ceci).

### `/graph.jsonld` ✅

Un JSON-LD `Dataset` schema.org. Nœuds wiki-layer seulement (pas de code nodes). Adapté aux crawlers qui comprennent les données structurées — Google Knowledge Graph, search indexers, agrégateurs compatibles schema.org.

### `/search-index.json` ✅

L’index de palette + recherche de pages. Types wiki-layer seulement. Adapté à l’intégration d’une UI de recherche tierce ; le schéma est une liste d’entrées `{kind, title, slug, body, score_hints}`.

### `/sitemap.xml` ✅

Chaque route émise avec `lastmod` dérivé du frontmatter (`generated_at`, `updated_at`, `published_at`, `date`). Adapté aux moteurs de recherche.

### `/rss.xml` ✅

Les 30 dernières synthèses triées de la plus récente à la plus ancienne. Adapté à "subscribe to this wiki" — lecteurs RSS, IFTTT, listes de diffusion.

### `/robots.txt` ✅

Permissif — crawl + index everything. La wiki est faite pour être lue par des agents.

### `/ai-readme.md` ✅

Une carte du site lisible par machine pour les AI agents qui ne parlent pas bien HTML. Liste chaque artifact ci-dessus avec son purpose et une description d’une ligne de quand chaque format est approprié.

### `/manifest.json` ✅

Un enregistrement sha256 + size pour chaque emitted file du site. Utilisé par :

- Le test d’idempotence compile-twice (`tests/test_project_e2e_redesign.py`).
- Le downstream tooling qui veut détecter « ce site a-t-il changé depuis la dernière visite ? » sans diff complet.
- La commande deploy, pour court-circuiter les pushes quand rien n’a changé.

## Choisir le bon format

| Si vous êtes… | Lire |
|---|---|
| Un humain visitant pour la première fois | `/` puis approfondir dans [Concepts](#concepts) ou [Papers](#papers) |
| Un humain voulant "what's new" | [Timeline](#timeline) et [Syntheses](#syntheses) récentes |
| Un humain voulant la structure | [Graph view](#graph-view) |
| Un agent LLM faisant une requête | `<page>.json` pour l’accès typé |
| Un agent LLM faisant une requête avec budget contraint | `<page>.txt` |
| Un agent LLM ingérant tout | `/llms-full.txt` (≤ 5 MB) ou `/graph.jsonld` (sans plafond) |
| Un outil construisant une UI personnalisée | `/search-index.json` + `/graph.json` |
| Un moteur de recherche | `/sitemap.xml` + `/graph.jsonld` |
| Un abonné | `/rss.xml` |
| Un détecteur de changements | `/manifest.json` |

## Documents liés

- [Architecture](architecture.fr.md) — le modèle à trois couches, la carte des modules, l’histoire d’idempotence.
- [Feature map](feature-map.fr.md) — chaque fonctionnalité avec statut, fichiers sources et liens ici.
- [Quickstart](quickstart.fr.md) — chemin minimal de `project init` à un site navigable.
- [Self-dogfood demo](self-dogfood.fr.md) — exécuter Tesserae sur son propre repo, y compris ce site.
