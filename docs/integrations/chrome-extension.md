# Web Clipper (Chrome extension)

<!-- translations:start -->
<p align="center"><a href="../i18n/integrations/chrome-extension.ko.md">한국어</a> · <a href="../i18n/integrations/chrome-extension.zh.md">中文</a> · <a href="../i18n/integrations/chrome-extension.ja.md">日本語</a> · <a href="../i18n/integrations/chrome-extension.ru.md">Русский</a> · <a href="../i18n/integrations/chrome-extension.es.md">Español</a> · <a href="../i18n/integrations/chrome-extension.fr.md">Français</a> · <a href="../i18n/integrations/chrome-extension.de.md">Deutsch</a></p>
<!-- translations:end -->

Clip any web page — or just the text you selected — straight into your
Tesserae knowledge base. The clipper POSTs the page to a local `tesserae
serve` instance, which writes a provenance-stamped markdown file into the
project corpus and runs an incremental compile so the clip shows up as
typed nodes in your graph, vault, and site.

This is the "autonomous, proactive knowledge ingestion" pillar made
one-click: see something worth keeping, clip it, and it becomes
agent-ready context.

---

## What it does

1. You browse to a page and hit the clipper (toolbar button or keyboard
   shortcut).
2. The extension grabs the page `url`, `title`, page metadata, and either
   the **full readable content** or, if you have text highlighted, just
   your **selection**. You can add an optional **note** and **tags**, and
   toggle **TL;DR** generation.
3. It POSTs that payload to `http://localhost:<port>/api/clip` on your
   running `tesserae serve`.
4. The server resolves the project being served, writes
   `data/ingested/<slug>.md`, optionally prepends a one-call LLM TL;DR,
   and calls the same ingestion path the CLI uses (`ingest_sources`),
   which incrementally compiles the new source into the graph.
5. You get back a JSON report (`status`, `path`, `tldr`, `node_count`,
   `edge_count`).

The clipped markdown looks like:

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

The TL;DR is **best-effort**: it uses the CLI-backed Claude layer (no API
key needed). If the `claude` CLI is unavailable or the call fails, the
clip is still ingested — just without the `## TL;DR` section.

---

## Install (load unpacked)

> The extension ships in the repo under `extension/` (load-unpacked during
> development; a Chrome Web Store listing is in review).

1. Open `chrome://extensions`.
2. Toggle **Developer mode** (top-right) on.
3. Click **Load unpacked** and select the `extension/` directory.
4. Pin the Tesserae clipper to your toolbar.

The extension talks to `http://localhost:8765` by default; set the port in
the extension options to match the port you pass to `tesserae serve`.

---

## Run the server

Compile your project, then serve it:

```bash
python3 -m tesserae serve --project /path/to/project --port 8765
```

`tesserae serve` exposes the static site **plus** two JSON routes on the
same origin:

- `POST /api/ask`  — question answering (see [mcp.md](mcp.md))
- `POST /api/clip` — web-clip ingestion (this feature)

Leave it running while you browse; each clip hits `/api/clip`.

---

## The `/api/clip` contract

`POST /api/clip` with a JSON body:

| Field       | Type      | Required | Notes |
|-------------|-----------|----------|-------|
| `url`       | string    | yes      | Source page URL (provenance + filename slug). |
| `title`     | string    | no       | Page title; falls back to a derived title. |
| `content`   | string    | yes\*    | Full page text. |
| `selection` | string    | no       | If present, **overrides** `content` — clips just the highlighted text. |
| `meta`      | object    | no       | Extra page metadata passed through. |
| `note`      | string    | no       | Your free-text annotation → `## Note`. |
| `tags`      | string[]  | no       | Front-matter tags. |
| `tldr`      | boolean   | no       | Default `true`. Set `false` to skip TL;DR generation. |

\* Either `content` or `selection` must be non-empty.

**Response** `200 OK`:

```json
{
  "status": "ok",
  "path": "/path/to/project/data/ingested/example-com-article.md",
  "tldr": "A two-sentence summary…",
  "node_count": 142,
  "edge_count": 287
}
```

Errors return `400` (bad request / empty body) or `500` (ingestion
failure) with `{"error": "..."}`.

### CORS

Because the clipper is a browser extension hitting `localhost`, the
endpoint speaks CORS — but only for trusted callers, so an arbitrary
website you visit cannot POST into your graph:

- `OPTIONS /api/clip` returns the preflight headers.
- The server validates the request `Origin` and **reflects only**
  browser-extension (`chrome-extension://…`) and loopback
  (`http://localhost`, `http://127.0.0.1`) origins. A foreign website
  origin is rejected with `403` and never reaches the ingest path.
- Allowed responses send `Access-Control-Allow-Origin: <that origin>`,
  `Access-Control-Allow-Methods: POST, OPTIONS`, and
  `Access-Control-Allow-Headers: Content-Type`.
- Chrome's **Private Network Access** preflight is honored: when the
  request carries `Access-Control-Request-Private-Network: true`, the
  server replies `Access-Control-Allow-Private-Network: true` so a
  Web-Store extension can reach `localhost`.
- The request body is capped (5 MB) before it is read.

---

## The MCP `ingest` tool

The same ingestion path is exposed to agents through the Tesserae MCP
server as the `ingest` tool, so an agent can clip content it found without
a browser:

| Input     | Required | Notes |
|-----------|----------|-------|
| `content` | yes      | The text to ingest. |
| `url`     | no       | Source URL (provenance + slug). |
| `title`   | no       | Document title. |
| `note`    | no       | Annotation → `## Note`. |
| `tags`    | no       | Front-matter tags. |
| `tldr`    | no       | Default `true`. |

It ingests into the project the server resolves from its working directory
(or pass `project` to target a registered alias) and returns the same
`{status, path, tldr, node_count, edge_count}` report. See [mcp.md](mcp.md) for MCP setup.

---

## TL;DR toggle

TL;DR is on by default. Turn it off per-clip in the extension popup (or
send `"tldr": false`) when you want a fast, deterministic clip with no LLM
call — e.g. clipping into an air-gapped project or when `claude` isn't on
PATH. With it on, a failed/missing summarizer never blocks the clip; you
simply get no `## TL;DR` section.

---

## Keyboard shortcut

The clipper registers a command you can bind under
`chrome://extensions/shortcuts`. The default is:

- **Clip current page / selection:** `Ctrl+Shift+S` (macOS:
  `Cmd+Shift+S`)

Rebind it there if it collides with another extension.
