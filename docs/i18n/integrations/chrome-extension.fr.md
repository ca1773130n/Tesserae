# Web Clipper (Extension Chrome)

<!-- translations:start -->
<p align="center"><a href="../../integrations/chrome-extension.md">English</a> · <a href="chrome-extension.ko.md">한국어</a> · <a href="chrome-extension.zh.md">中文</a> · <a href="chrome-extension.ja.md">日本語</a> · <a href="chrome-extension.ru.md">Русский</a> · <a href="chrome-extension.es.md">Español</a> · <a href="chrome-extension.de.md">Deutsch</a></p>
<!-- translations:end -->

Capturez n'importe quelle page web — ou simplement le texte que vous avez sélectionné — directement dans votre
base de connaissances Tesserae. Le clipper envoie la page via POST à une instance locale `tesserae
serve`, qui écrit un fichier markdown horodaté de provenance dans le corpus du projet et exécute une compilation incrémentale pour que la capture apparaisse sous forme de nœuds typés dans votre graphe, voûte et site.

C'est le pilier « ingestion autonome et proactive des connaissances » rendu
en un seul clic : vous voyez quelque chose qui mérite d'être conservé, vous le capturez, et il devient
un contexte prêt pour les agents.

---

## Ce qu'il fait

1. Vous naviguez vers une page et appuyez sur le clipper (bouton de barre d'outils ou raccourci clavier).
2. L'extension récupère l'URL de la page, le titre, les métadonnées de la page, et soit
   le **contenu lisible complet**, soit, si vous avez du texte en surbrillance, juste votre
   **sélection**. Vous pouvez ajouter une **note** optionnelle et des **tags**, et
   basculer la génération du **TL;DR**.
3. Il envoie ce payload via POST à `http://localhost:<port>/api/clip` sur votre
   instance `tesserae serve` en cours d'exécution.
4. Le serveur résout le projet servi, écrit
   `data/ingested/<slug>.md`, ajoute optionnellement un TL;DR LLM en un seul appel,
   et appelle le même chemin d'ingestion que celui utilisé par la CLI (`ingest_sources`),
   qui compile de manière incrémentale la nouvelle source dans le graphe.
5. Vous récupérez un rapport JSON (`status`, `path`, `tldr`, `node_count`,
   `edge_count`).

Le markdown capturé ressemble à ceci :

```markdown
---
clipped_at: 2026-06-13T00:00:00Z
note: read later
source: web-clip
tags: python, web
title: An Article
url: https://example.com/article
---

## TL;DR

A two-sentence summary (only present when TL;DR is enabled and succeeds).

## Note

read later

## Content

The clipped page text (or your selection).
```

Le TL;DR est **meilleur effort** : il utilise la couche Claude soutenue par la CLI (aucune clé API nécessaire). Si la CLI `claude` est indisponible ou l'appel échoue, la
capture est toujours ingérée — juste sans la section `## TL;DR`.

---

## Installer (charger en déballé)

> L'extension est fournie dans le repo sous `extension/` (chargement en déballé pendant
> le développement ; une annonce sur le Chrome Web Store est en révision).

1. Ouvrez `chrome://extensions`.
2. Basculez le **Mode développeur** (en haut à droite) sur activé.
3. Cliquez sur **Charger l'extension non emballée** et sélectionnez le répertoire `extension/`.
4. Épinglez le clipper Tesserae à votre barre d'outils.

L'extension communique avec `http://localhost:8765` par défaut ; définissez le port dans
les options d'extension pour qu'il corresponde au port que vous passez à `tesserae serve`.

---

## Exécuter le serveur

Compilez votre projet, puis servez-le :

```bash
python3 -m tesserae serve --project /path/to/project --port 8765
```

`tesserae serve` expose le site statique **plus** deux routes JSON sur la
même origine :

- `POST /api/ask`  — réponse aux questions (voir [mcp.md](mcp.md))
- `POST /api/clip` — ingestion de capture web (cette fonctionnalité)

Laissez-le en cours d'exécution pendant que vous naviguez ; chaque capture accède à `/api/clip`.

---

## Le contrat `/api/clip`

`POST /api/clip` avec un corps JSON :

| Field       | Type      | Required | Notes |
|-------------|-----------|----------|-------|
| `url`       | string    | yes      | URL de la page source (provenance + slug de nom de fichier). |
| `title`     | string    | no       | Titre de la page ; revient à un titre dérivé. |
| `content`   | string    | yes\*    | Texte complet de la page. |
| `selection` | string    | no       | S'il est présent, **remplace** `content` — capture uniquement le texte en surbrillance. |
| `meta`      | object    | no       | Métadonnées de page supplémentaires transmises. |
| `note`      | string    | no       | Votre annotation en texte libre → `## Note`. |
| `tags`      | string[]  | no       | Tags de front-matter. |
| `tldr`      | boolean   | no       | Par défaut `true`. Définissez sur `false` pour ignorer la génération du TL;DR. |

\* Soit `content` soit `selection` doit être non vide.

**Réponse** `200 OK`:

```json
{
  "status": "ok",
  "path": "/path/to/project/data/ingested/example-com-article.md",
  "tldr": "A two-sentence summary…",
  "node_count": 142,
  "edge_count": 287
}
```

Les erreurs retournent `400` (mauvaise requête / corps vide) ou `500` (échec d'ingestion)
avec `{"error": "..."}`.

### CORS

Parce que le clipper est une extension de navigateur accédant à `localhost`, l'
endpoint parle CORS — mais seulement pour les appelants de confiance, donc un
site Web arbitraire que vous visitez ne peut pas envoyer une requête POST dans votre graphe :

- `OPTIONS /api/clip` retourne les en-têtes de préflight.
- Le serveur valide l'`Origin` de la requête et **ne reflète que**
  les origines extension de navigateur (`chrome-extension://…`) et loopback
  (`http://localhost`, `http://127.0.0.1`). Une origine de site Web étrangère
  est rejetée avec `403` et n'atteint jamais le chemin d'ingestion.
- Les réponses autorisées envoient `Access-Control-Allow-Origin: <that origin>`,
  `Access-Control-Allow-Methods: POST, OPTIONS`, et
  `Access-Control-Allow-Headers: Content-Type`.
- Le préflight **Private Network Access** de Chrome est honoré : quand la
  requête porte `Access-Control-Request-Private-Network: true`, le
  serveur répond `Access-Control-Allow-Private-Network: true` pour qu'une
  extension Web Store puisse atteindre `localhost`.
- Le corps de la requête est limité (5 MB) avant d'être lu.

---

## L'outil MCP `ingest`

Le même chemin d'ingestion est exposé aux agents via le serveur MCP Tesserae
en tant que l'outil `ingest`, pour qu'un agent puisse capturer du contenu qu'il a trouvé sans
navigateur :

| Input     | Required | Notes |
|-----------|----------|-------|
| `content` | yes      | Le texte à ingérer. |
| `url`     | no       | URL source (provenance + slug). |
| `title`   | no       | Titre du document. |
| `note`    | no       | Annotation → `## Note`. |
| `tags`    | no       | Tags de front-matter. |
| `tldr`    | no       | Par défaut `true`. |

Il ingère dans le **projet actif** (résolu avec `activate_project`
ou en passant `project`) et retourne le même rapport `{status, path, tldr, node_count,
edge_count}`. Voir [mcp.md](mcp.md) pour la configuration MCP.

---

## Basculer TL;DR

Le TL;DR est activé par défaut. Désactivez-le par capture dans la popup d'extension (ou
envoyez `"tldr": false`) quand vous voulez une capture rapide et déterministe sans appel LLM — par exemple en capturant dans un projet isolé ou quand `claude` n'est pas sur
PATH. Quand il est activé, un résumé manquant/échoué ne bloque jamais la capture ; vous
obtenez simplement pas de section `## TL;DR`.

---

## Raccourci clavier

Le clipper enregistre une commande que vous pouvez lier sous
`chrome://extensions/shortcuts`. La valeur par défaut est :

- **Capturer la page actuelle / la sélection :** `Ctrl+Shift+S` (macOS :
  `Cmd+Shift+S`)

Reliez-la là si elle entre en collision avec une autre extension.
