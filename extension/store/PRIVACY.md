# Privacy Policy — Clip to Tesserae

_Last updated: 2026-06-13_

**Clip to Tesserae** is a browser extension that captures content from a web
page you choose and sends it to a Tesserae server running on your own computer.

## What the extension accesses

- **Page content you explicitly clip.** When — and only when — you click the
  toolbar button, choose the right-click "Clip to Tesserae" item, or press the
  keyboard shortcut, the extension reads the current tab's main readable content
  (or your text selection) plus basic page metadata (title, URL, author/date if
  present).
- **Your settings.** The local server URL, default tags, and the TL;DR toggle
  are stored using the browser's extension storage (`chrome.storage.sync`).

## Where your data goes

- The clipped content is sent **only** to the Tesserae server address you
  configure, which defaults to your own machine (`http://localhost:8765`).
- The extension does **not** send your data to the developer, to Tesserae's
  authors, or to any third-party or remote service. It requests host access to
  `localhost`/`127.0.0.1` only.

## What we do NOT do

- We do not collect, store, or transmit your browsing history.
- We do not sell or share any data with third parties.
- We do not use your data for advertising, profiling, or any purpose unrelated
  to delivering a clip to your local server.
- We include no remote code, analytics, or tracking.

## Data retention

The extension keeps no server-side records — it has no server. Clipped content
lives only in the local Tesserae project you sent it to, under your control. You
can clear extension settings at any time from the extension's options page or by
removing the extension.

## Contact

Questions: open an issue at https://github.com/ca1773130n/Tesserae/issues
