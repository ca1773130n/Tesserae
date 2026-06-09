# `tesserae ingest`

<!-- translations:start -->
<p align="center"><a href="../ingest.md">English</a> · <a href="ingest.ko.md">한국어</a> · <a href="ingest.zh.md">中文</a> · <a href="ingest.ja.md">日本語</a> · <a href="ingest.ru.md">Русский</a> · <a href="ingest.es.md">Español</a> · <a href="ingest.fr.md">Français</a> · <a href="ingest.de.md">Deutsch</a></p>
<!-- translations:end -->
Fusionne un seul fichier de document ou une URL dans la base de connaissances.

## Utilisation

    tesserae ingest <input>...  [--title T] [--source-kind K] [--exact] [--dry-run]

`<input>` correspond à un ou plusieurs chemins de fichiers locaux ou URL `http(s)`. Les URL sont
récupérées, converties en markdown et enregistrées sous `data/ingested/<slug>.md` avec un
front-matter de provenance (`source_url`, `fetched_at`, `content_sha256`, ainsi que `arxiv_id`
lorsqu'il est détecté), puis fusionnées. Les fichiers locaux extérieurs au projet sont copiés dans
`data/ingested/` afin de devenir des sources suivies (une compilation complète ultérieure les
reproduit à l'identique).

L'ingestion par URL nécessite l'extra optionnel :

    pip install tesserae[ingest-url]

## Fonctionnement

Par défaut, `ingest` fusionne la nouvelle source via une compilation incrémentale — il ne réextrait
pas l'ensemble du corpus — et le résultat est identique octet pour octet à une compilation complète
(un repli automatique vers une recompilation complète garantit l'exactitude pour tout cas que le
chemin incrémental ne peut pas traiter). Passez `--exact` pour forcer une recompilation complète de
l'ensemble du corpus.

## Options

- `--exact` — force une recompilation complète de l'ensemble du corpus.
- `--dry-run` — récupère et signale ce qui serait ingéré ; n'écrit aucun graphe.
- `--title` — remplacement du titre, utile pour les URL nues.
- `--source-kind` — remplace la classification de la source.

## Commandes associées

- `tesserae compile` (sans argument) réextrait l'ensemble du corpus suivi.
- `tesserae ingest <x>` ajoute une source de manière incrémentale.
- `tesserae code ingest` génère un graphe de code à partir du code source Python (c'est une commande différente).
