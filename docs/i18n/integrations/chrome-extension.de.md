# Web-Clipper (Chrome-Erweiterung)

<!-- translations:start -->
<p align="center"><a href="../../integrations/chrome-extension.md">English</a> · <a href="chrome-extension.ko.md">한국어</a> · <a href="chrome-extension.zh.md">中文</a> · <a href="chrome-extension.ja.md">日本語</a> · <a href="chrome-extension.ru.md">Русский</a> · <a href="chrome-extension.es.md">Español</a> · <a href="chrome-extension.fr.md">Français</a></p>
<!-- translations:end -->

Schneiden Sie eine beliebige Webseite aus — oder nur den von Ihnen markierten Text — direkt in Ihre Tesserae-Wissensdatenbank. Der Clipper sendet die Seite mit POST an eine lokale `tesserae serve`-Instanz, die eine Markdown-Datei mit Herkunftsstempel in das Projektkorpus schreibt und eine inkrementelle Kompilierung durchführt, damit der Clip als typisierte Knoten in Ihrem Graphen, Tresor und der Website angezeigt wird.

Dies ist die „autonome, proaktive Wissensaufnahme" als Ein-Klick-Funktion: Wenn Sie etwas Wertvolles sehen, schneiden Sie es aus, und es wird zu kontextbereitem Inhalt für Agenten.

---

## Was macht das Programm?

1. Sie navigieren zu einer Seite und drücken den Clipper (Symbolleistenschaltfläche oder Tastaturkürzel).
2. Die Erweiterung erfasst die Seiten-`url`, `title`, Seitenlmetadaten und entweder den **vollständigen lesbaren Inhalt** oder, wenn Sie Text markiert haben, nur Ihre **Auswahl**. Sie können eine optionale **Notiz** und **Tags** hinzufügen und die **TL;DR**-Generierung umschalten.
3. Sie sendet diese Nutzlast mit POST an `http://localhost:<port>/api/clip` auf Ihrem laufenden `tesserae serve`.
4. Der Server löst das bereitgestellte Projekt auf, schreibt `data/ingested/<slug>.md`, stellt optional eine TL;DR-Zusammenfassung eines Aufrufs voran und ruft denselben Aufnahmepfad auf, den die CLI verwendet (`ingest_sources`), was die neue Quelle inkrementell in den Graphen kompiliert.
5. Sie erhalten einen JSON-Bericht (`status`, `path`, `tldr`, `node_count`, `edge_count`) zurück.

Das zugeschnittene Markdown sieht wie folgt aus:

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

Die TL;DR ist **Best-Effort**: Sie verwendet die CLI-gestützte Claude-Schicht (kein API-Schlüssel erforderlich). Wenn die `claude`-CLI nicht verfügbar ist oder der Aufruf fehlschlägt, wird der Clip trotzdem aufgenommen — nur ohne den Abschnitt `## TL;DR`.

---

## Installation (Entpackt laden)

> Die Erweiterung wird im Repo unter `extension/` bereitgestellt (laden Sie sie während der Entwicklung entpackt; eine Chrome Web Store-Angebot ist in Überprüfung).

1. Öffnen Sie `chrome://extensions`.
2. Schalten Sie den **Entwicklermodus** (oben rechts) ein.
3. Klicken Sie auf **Entpackt laden** und wählen Sie das Verzeichnis `extension/` aus.
4. Heften Sie den Tesserae-Clipper an Ihre Symbolleiste an.

Die Erweiterung kommuniziert standardmäßig mit `http://localhost:8765`. Stellen Sie den Port in den Optionen der Erweiterung ein, damit er mit dem Port übereinstimmt, den Sie an `tesserae serve` übergeben.

---

## Server ausführen

Kompilieren Sie Ihr Projekt und stellen Sie es bereit:

```bash
python3 -m tesserae serve --project /path/to/project --port 8765
```

`tesserae serve` stellt die statische Website **plus** zwei JSON-Routen auf denselben Ursprung bereit:

- `POST /api/ask`  — Beantwortung von Fragen (siehe [mcp.md](mcp.de.md))
- `POST /api/clip` — Web-Clip-Aufnahme (diese Funktion)

Lassen Sie es laufen, während Sie surfen; jeder Clip trifft `/api/clip`.

---

## Der `/api/clip`-Vertrag

`POST /api/clip` mit einem JSON-Text:

| Feld        | Typ       | Erforderlich | Hinweise |
|-------------|-----------|----------|-------|
| `url`       | string    | ja       | URL der Quellseite (Herkunft + Dateiname-Slug). |
| `title`     | string    | nein     | Seitentitel; fällt auf einen abgeleiteten Titel zurück. |
| `content`   | string    | ja\*     | Vollständiger Seitentext. |
| `selection` | string    | nein     | Falls vorhanden, **überschreibt** `content` — schneidet nur den markierten Text aus. |
| `meta`      | object    | nein     | Zusätzliche Seitenlmetadaten werden durchgeleitet. |
| `note`      | string    | nein     | Ihre freie Anmerkung → `## Note`. |
| `tags`      | string[]  | nein     | Metadaten-Tags. |
| `tldr`      | boolean   | nein     | Standard `true`. Setzen Sie auf `false`, um die TL;DR-Generierung zu überspringen. |

\* Entweder `content` oder `selection` muss nicht leer sein.

**Antwort** `200 OK`:

```json
{
  "status": "ok",
  "path": "/path/to/project/data/ingested/example-com-article.md",
  "tldr": "A two-sentence summary…",
  "node_count": 142,
  "edge_count": 287
}
```

Fehler geben `400` (ungültige Anfrage / leerer Text) oder `500` (Aufnahmefehler) mit `{"error": "..."}` zurück.

### CORS

Da der Clipper eine Browser-Erweiterung ist, die `localhost` trifft, spricht der Endpunkt CORS — aber nur für vertrauenswürdige Aufrufer, sodass eine beliebige Website, die Sie besuchen, nicht in Ihren Graphen POSTieren kann:

- `OPTIONS /api/clip` gibt die Preflight-Header zurück.
- Der Server validiert die `Origin` der Anfrage und **reflektiert nur** Browser-Erweiterungen (`chrome-extension://…`) und Loopback-Ursprünge (`http://localhost`, `http://127.0.0.1`). Ein ausländischer Website-Ursprung wird mit `403` abgelehnt und erreicht nie den Aufnahmepfad.
- Zulässige Antworten senden `Access-Control-Allow-Origin: <that origin>`, `Access-Control-Allow-Methods: POST, OPTIONS` und `Access-Control-Allow-Headers: Content-Type`.
- Die **Private Network Access** von Chrome wird berücksichtigt: Wenn die Anfrage `Access-Control-Request-Private-Network: true` trägt, antwortet der Server `Access-Control-Allow-Private-Network: true`, damit eine Web-Store-Erweiterung `localhost` erreichen kann.
- Der Anfragekörper wird begrenzt (5 MB), bevor er gelesen wird.

---

## Das MCP-Werkzeug `ingest`

Derselbe Aufnahmepfad wird den Agenten über den Tesserae MCP-Server als `ingest`-Werkzeug verfügbar gemacht, sodass ein Agent Inhalte, die es gefunden hat, ohne Browser ausschneiden kann:

| Eingabe   | Erforderlich | Hinweise |
|-----------|----------|-------|
| `content` | ja       | Der aufzunehmende Text. |
| `url`     | nein     | Quell-URL (Herkunft + Slug). |
| `title`   | nein     | Dokumenttitel. |
| `note`    | nein     | Anmerkung → `## Note`. |
| `tags`    | nein     | Metadaten-Tags. |
| `tldr`    | nein     | Standard `true`. |

Es wird in das **aktive Projekt** aufgenommen (aufgelöst mit `activate_project` oder Übergabe von `project`) und gibt denselben Bericht `{status, path, tldr, node_count, edge_count}` zurück. Siehe [mcp.md](mcp.de.md) für MCP-Setup.

---

## TL;DR-Umschalter

TL;DR ist standardmäßig aktiviert. Schalten Sie es pro Clip im Popup der Erweiterung aus (oder senden Sie `"tldr": false`), wenn Sie einen schnellen, deterministischen Clip ohne LLM-Aufruf möchten — z. B. wenn Sie in ein luftgetrenntes Projekt clippen oder wenn `claude` nicht im PATH ist. Wenn es aktiviert ist, blockiert ein fehlgeschlagener/fehlender Zusammenfasser nie den Clip; Sie erhalten einfach keinen Abschnitt `## TL;DR`.

---

## Tastaturkürzel

Der Clipper registriert einen Befehl, den Sie unter `chrome://extensions/shortcuts` binden können. Der Standard ist:

- **Aktuelle Seite / Auswahl clippen:** `Ctrl+Shift+S` (macOS: `Cmd+Shift+S`)

Binden Sie es dort erneut, wenn es mit einer anderen Erweiterung kollidiert.
