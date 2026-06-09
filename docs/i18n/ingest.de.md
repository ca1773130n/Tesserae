# `tesserae ingest`

<!-- translations:start -->
<p align="center"><a href="../ingest.md">English</a> · <a href="ingest.ko.md">한국어</a> · <a href="ingest.zh.md">中文</a> · <a href="ingest.ja.md">日本語</a> · <a href="ingest.ru.md">Русский</a> · <a href="ingest.es.md">Español</a> · <a href="ingest.fr.md">Français</a> · <a href="ingest.de.md">Deutsch</a></p>
<!-- translations:end -->
Führt eine einzelne Dokumentdatei oder URL in die Wissensbasis zusammen.

## Verwendung

    tesserae ingest <input>...  [--title T] [--source-kind K] [--exact] [--dry-run]

`<input>` ist ein oder mehrere lokale Dateipfade oder `http(s)`-URLs. URLs werden abgerufen, in
Markdown konvertiert und mit Herkunfts-Front-Matter (`source_url`, `fetched_at`, `content_sha256`
sowie `arxiv_id`, falls erkannt) unter `data/ingested/<slug>.md` gespeichert und anschließend
zusammengeführt. Lokale Dateien außerhalb des Projekts werden nach `data/ingested/` kopiert, sodass
sie zu nachverfolgten Quellen werden (eine spätere vollständige Kompilierung reproduziert sie
identisch).

Die URL-Aufnahme erfordert das optionale Extra:

    pip install tesserae[ingest-url]

## Funktionsweise

Standardmäßig führt `ingest` die neue Quelle über eine inkrementelle Kompilierung zusammen — es
extrahiert nicht den gesamten Korpus erneut — und das Ergebnis ist Byte für Byte identisch mit einer
vollständigen Kompilierung (ein automatischer Rückfall auf eine vollständige Neukompilierung
garantiert die Korrektheit für jeden Fall, den der inkrementelle Pfad nicht verarbeiten kann).
Übergib `--exact`, um eine vollständige Neukompilierung des gesamten Korpus zu erzwingen.

## Flags

- `--exact` — erzwingt eine vollständige Neukompilierung des gesamten Korpus.
- `--dry-run` — ruft ab und meldet, was aufgenommen würde; schreibt keinen Graphen.
- `--title` — Titelüberschreibung, nützlich für nackte URLs.
- `--source-kind` — überschreibt die Quellenklassifizierung.

## Verwandte Befehle

- `tesserae compile` (ohne Argumente) extrahiert den gesamten nachverfolgten Korpus erneut.
- `tesserae ingest <x>` fügt eine Quelle inkrementell hinzu.
- `tesserae code ingest` erzeugt einen Codegraphen aus Python-Quellcode (ein anderer Befehl).
