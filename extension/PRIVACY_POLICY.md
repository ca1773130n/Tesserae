# Privacy Policy — Clip to Tesserae

**Effective date:** 2026-06-15
**Extension:** Clip to Tesserae (Chrome Web Store)
**Developer:** ca1773130n
**Contact:** https://github.com/ca1773130n/Tesserae/issues

---

## Summary (plain English)

Clip to Tesserae is a browser extension that, **only when you ask it to**, copies
the readable content (or your highlighted selection) of the page you are looking
at and sends it to a **Tesserae server running on your own computer**.

- We do **not** run any server, and we do **not** receive your data.
- Your clips go **only** to the server address you configure (by default
  `http://localhost:8765` on your own machine).
- We do **not** collect, sell, share, or transmit your data to the developer or
  to any third party.
- We include **no** analytics, no tracking, and no remote code.

This extension is open source. You can read every line it runs at
https://github.com/ca1773130n/Tesserae.

---

## Single purpose

The extension has one purpose: to let you send the content of a web page you
choose to your own local Tesserae knowledge base for later search and reference.

---

## What data the extension handles, and why

The extension only acts in response to an explicit action by you: clicking the
toolbar button, choosing the "Clip to Tesserae" right-click menu item, or
pressing the keyboard shortcut. It does nothing in the background and reads
nothing from pages you do not clip.

| Data | When it is accessed | Why | Where it goes |
|------|--------------------|-----|---------------|
| The current page's readable content, or your text selection | Only when you trigger a clip | It is the content you are clipping | Only to your configured Tesserae server |
| Page metadata: title, URL, and author/published date if present in the page | Only when you trigger a clip | To label and attribute the clip | Only to your configured Tesserae server |
| An optional note and tags you type | Only when you add them in the popup | To organize the clip | Only to your configured Tesserae server |
| Your settings: server URL, default tags, capture mode, TL;DR toggle | When you change them in Options | To remember your preferences | Stored in the browser via `chrome.storage.sync` (see below) |

The extension does **not** read, collect, or store:

- your browsing history or the pages you visit but do not clip,
- form inputs, passwords, cookies, or authentication tokens,
- your location, contacts, or any device identifiers,
- any data for advertising, profiling, or analytics.

---

## Where your data goes

When you clip a page, the extension sends the clip to the server URL configured
in its options — and **nowhere else**. By default this is
`http://localhost:8765`, i.e. a Tesserae instance on your own computer. The
extension requests host access only to `http://localhost/*` and
`http://127.0.0.1/*`; it cannot send your clips to an arbitrary remote site.

If you change the configured endpoint to a non-local address, your clips will be
sent to that address. That is your choice and under your control; the developer
still never receives your data.

The clipped data is transmitted directly from your browser to that server. It is
not routed through, copied to, or seen by the developer or any third party.

## A note on `chrome.storage.sync`

Your **settings** (not your clips) are saved with the browser's
`chrome.storage.sync` API. If you are signed into Chrome with Sync enabled,
Chrome may sync these small preference values across your own devices through
your Google account, the same way it syncs other extension settings. This is a
browser feature operated by Google under Google's terms; the extension neither
controls nor receives that sync. Your clipped page content is **not** stored in
`chrome.storage.sync` and is never synced this way.

---

## Permissions and why each is needed

| Permission | Why the extension needs it |
|------------|----------------------------|
| `activeTab` | To read the content of the tab you are actively clipping, only at the moment you trigger a clip |
| `scripting` | To inject the content-extraction script into the page you are clipping |
| `storage` | To save your preferences (server URL, tags, capture mode, TL;DR toggle) |
| `contextMenus` | To add the "Clip to Tesserae" right-click menu item |
| `notifications` | To show a success/failure message after a clip |
| Host access to `localhost` / `127.0.0.1` | To deliver the clip to your local Tesserae server |

---

## Data retention and deletion

- The extension itself keeps **no** server-side records — it has no server.
- Clipped content lives only inside the local Tesserae project you sent it to,
  which is on your own machine and under your control.
- Your settings remain in browser storage until you change them, clear them from
  the Options page, or remove the extension.
- Removing the extension deletes its stored settings from your browser. To remove
  clipped content, delete it from your local Tesserae project.

---

## Third parties

The extension uses **no** third-party services, SDKs, analytics, advertising
networks, or remote code. No data is sold or shared with anyone.

---

## Children

The extension is a general-purpose developer/productivity tool and is not
directed to children. It collects no personal information from anyone.

---

## Changes to this policy

If this policy changes, the updated version will be published in the extension's
source repository with a new effective date at the top of this document.

---

## Contact

Questions or concerns: please open an issue at
https://github.com/ca1773130n/Tesserae/issues
