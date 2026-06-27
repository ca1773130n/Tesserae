# Clip to Tesserae

A self-contained Manifest V3 Chrome extension for one-click **"Clip to Tesserae"** —
capture the current page's readable content (or the active text selection), add an
optional note + tags, and POST it to a local Tesserae server.

No build step. No runtime network fetch of libraries. The readable-content
extractor and the HTML→Markdown converter are implemented inline and CSP-safe.

## What it does

- **Article extraction** — clones the DOM, strips `script/style/nav/aside/footer/
  header/iframe/ads/forms`, prefers `<article>` / `<main>` / the densest text block,
  then converts that subtree to Markdown (headings, paragraphs, lists, links,
  bold/italic, inline code, code blocks, blockquotes, images, line breaks).
- **Selection capture** — if there's a non-empty selection, that HTML is converted
  instead and the clip is marked `selection: true`.
- **Size cap** — content is capped at ~200KB (byte-accurate, code-point safe).
- **Three ways to clip**:
  - Toolbar popup (add a note + tags, toggle TL;DR, then **Clip**)
  - Right-click context menu → **Clip to Tesserae** (page or selection)
  - Keyboard shortcut **Ctrl+Shift+S** (macOS: **Cmd+Shift+S**)

Clips are POSTed as JSON to `<endpoint>/api/clip`:

```json
{
  "url": "…", "title": "…",
  "meta": { "byline": "…", "siteName": "…", "publishedTime": "…" },
  "content": "# markdown…", "selection": false,
  "note": "", "tags": ["research"], "tldr": true
}
```

On success the popup shows the returned `tldr` (if any) and **Saved ✓**; on a
network error it shows **Tesserae not running — start `tesserae serve`**.

## Requirements

You need a running Tesserae server that accepts `POST /api/clip`:

```bash
tesserae serve
```

By default the extension posts to `http://127.0.0.1:8765`. Change the endpoint in
the extension's **Settings** (options) page if your server listens elsewhere.

## Install (load unpacked)

1. Open `chrome://extensions`.
2. Enable **Developer mode** (top-right toggle).
3. Click **Load unpacked**.
4. Select this `extension/` directory.

The mosaic-tile icon appears in the toolbar. Pin it for quick access.

## Configure

Right-click the toolbar icon → **Options** (or click **Settings** in the popup):

- **Tesserae endpoint** — base URL of your server (default `http://127.0.0.1:8765`).
- **Default tags** — comma-separated, pre-filled in the popup.
- **Default capture mode** — *Selection first* (fall back to article) or *Article only*.
- **Generate TL;DR by default** — on/off.

Settings persist in `chrome.storage.sync`.

## Rebind the keyboard shortcut

1. Open `chrome://extensions/shortcuts`.
2. Find **Clip to Tesserae → Clip current page to Tesserae**.
3. Click the pencil and press your preferred combo.

(The default suggestion is **Ctrl+Shift+S** / **Cmd+Shift+S**; Chrome may leave it
unset if another extension claims it — set it here.)

## Files

```
extension/
├── manifest.json        MV3 manifest (permissions, action, background, command)
├── README.md            this file
├── icons/               16/48/128 px mosaic-tile PNG icons
└── src/
    ├── background.js     service worker: context menu, command, inject + POST
    ├── content.js        readable-content extractor + HTML→Markdown converter
    ├── popup.html/.js    toolbar UI: preview, note, tags, TL;DR, Clip
    └── options.html/.js  settings page (endpoint, tags, mode, TL;DR)
```

## Notes

- The extension only requests `host_permissions` for `localhost` / `127.0.0.1`,
  so it can talk to your local Tesserae server without broad host access.
- The content script is idempotent — injecting it multiple times (popup +
  command + menu) won't double-bind listeners.
