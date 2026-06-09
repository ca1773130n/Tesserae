# `tesserae ingest`

<!-- translations:start -->
<p align="center"><a href="../ingest.md">English</a> · <a href="ingest.ko.md">한국어</a> · <a href="ingest.zh.md">中文</a> · <a href="ingest.ja.md">日本語</a> · <a href="ingest.ru.md">Русский</a> · <a href="ingest.es.md">Español</a> · <a href="ingest.fr.md">Français</a> · <a href="ingest.de.md">Deutsch</a></p>
<!-- translations:end -->
Fusiona un único archivo de documento o URL en la base de conocimiento.

## Uso

    tesserae ingest <input>...  [--title T] [--source-kind K] [--exact] [--dry-run]

`<input>` es una o más rutas de archivos locales o URL `http(s)`. Las URL se obtienen, se
convierten a markdown y se guardan en `data/ingested/<slug>.md` con front-matter de procedencia
(`source_url`, `fetched_at`, `content_sha256` y `arxiv_id` cuando se detecta) y luego se fusionan.
Los archivos locales externos al proyecto se copian a `data/ingested/` para que se conviertan en
fuentes rastreadas (una compilación completa posterior los reproduce de forma idéntica).

La ingesta por URL requiere el extra opcional:

    pip install tesserae[ingest-url]

## Cómo funciona

De forma predeterminada, `ingest` fusiona la nueva fuente mediante una compilación incremental —no
vuelve a extraer todo el corpus— y el resultado es idéntico byte a byte al de una compilación
completa (un mecanismo automático de recompilación completa garantiza la corrección en cualquier
caso que la ruta incremental no pueda manejar). Pasa `--exact` para forzar una recompilación
completa de todo el corpus.

## Opciones

- `--exact` — fuerza una recompilación completa de todo el corpus.
- `--dry-run` — obtiene e informa de lo que se ingeriría; no escribe ningún grafo.
- `--title` — anulación del título, útil para URL sin más.
- `--source-kind` — anula la clasificación de la fuente.

## Comandos relacionados

- `tesserae compile` (sin argumentos) vuelve a extraer todo el corpus rastreado.
- `tesserae ingest <x>` añade una fuente de forma incremental.
- `tesserae code ingest` genera un grafo de código a partir de código fuente de Python (es un comando distinto).
