# Chrome Web Store listing — Clip to Tesserae

Copy/paste these fields into the Web Store Developer Dashboard listing.

## Product name
Clip to Tesserae

## Summary (≤132 chars)
One-click clip of the current page's readable content or selection into your local Tesserae knowledge graph.

## Category
Developer Tools

## Language
English (United States)

## Detailed description
Clip to Tesserae sends the page you're reading straight into your local
Tesserae context engine.

Tesserae (https://github.com/ca1773130n/Tesserae) keeps a self-improving,
typed knowledge graph of your project and compiles agent-ready context on
demand. This extension lets you feed that graph from the browser:

• One click (or Ctrl/Cmd+Shift+S) captures the page's main readable content —
  not the nav, ads, or footers — and converts it to clean Markdown.
• Highlight text first to clip just the selection.
• Add an optional note and tags before saving.
• An optional TL;DR is generated locally and stored with the clip.

Everything stays on your machine. The clip is POSTed to a Tesserae server you
run locally (default http://localhost:8765) via `tesserae serve`. The
extension talks ONLY to localhost — it never sends your data to any remote or
third-party server.

Requires a local Tesserae install with `tesserae serve` running. See the
project README for setup.

## Single purpose (required)
Capture the current web page's readable content (or the user's text selection)
and send it to the user's own locally-running Tesserae server for ingestion
into their knowledge graph.

## Permission justifications (required)
- activeTab: Read the content of the current tab only when the user explicitly
  clicks the toolbar button, the context-menu item, or the keyboard shortcut.
- scripting: Inject the readable-content extractor into the active tab on that
  explicit user action.
- storage: Persist user settings (local server URL, default tags, default TL;DR
  toggle) via chrome.storage.sync.
- contextMenus: Provide the right-click "Clip to Tesserae" action.
- notifications: Show whether a clip succeeded or failed.
- host_permissions http://localhost/* and http://127.0.0.1/*: POST the clip to
  the user's own local Tesserae server. No other hosts are requested.
- Remote code: none. All scripts are bundled in the package; nothing is fetched
  or eval'd at runtime.

## Data usage disclosures (Privacy practices tab)
- Does this item collect/use personal data? It reads the content of pages the
  user explicitly chooses to clip and sends it only to the user's own local
  server. No data is transmitted to the developer or any third party.
- Sold to third parties: No.
- Used/transferred for purposes unrelated to core functionality: No.
- Used/transferred to determine creditworthiness / lending: No.

## Privacy policy URL
Host extension/store/PRIVACY.md and paste its public URL here. Easiest:
enable GitHub Pages, or link the raw file, e.g.
https://github.com/ca1773130n/Tesserae/blob/main/extension/store/PRIVACY.md

## Assets checklist
- [x] Store icon 128×128 — extension/icons/icon128.png (also 512 master at icon512.png)
- [ ] At least 1 screenshot, 1280×800 or 640×400 PNG/JPG — capture the popup
      clipping a real page (load unpacked, then screenshot). REQUIRED.
- [ ] (optional) Small promo tile 440×280, Marquee 1400×560.
