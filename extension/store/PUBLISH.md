# Publishing "Clip to Tesserae" to the Chrome Web Store

The package is store-ready. Two things only **you** can do gate the actual
publish: a Chrome Web Store **developer account** and the **upload** itself
(dashboard click-through, or API with your OAuth credentials). Steps below.

## 0. Prerequisites (one-time, you)

1. A Google account registered as a **Chrome Web Store developer**
   (https://chrome.google.com/webstore/devconsole) — one-time **$5** fee.
2. A **privacy policy URL** — host `extension/store/PRIVACY.md` somewhere public
   (GitHub Pages, or link the file on GitHub) and note the URL.
3. At least **one screenshot** at 1280×800 or 640×400 (see step 1).

## 1. Build the package

```bash
cd extension && zip -rq ../dist/tesserae-clip-v0.1.0.zip . -x '*.DS_Store'
```
(Already built at `dist/tesserae-clip-v0.1.0.zip`.)

### Screenshot (required by the store)
Load the extension unpacked, start `tesserae serve`, open the popup on a real
article, and capture the popup at 1280×800. Save under `extension/store/screenshots/`.

## 2a. Publish via the Dashboard (simplest, recommended for v1)

1. Go to the Developer Dashboard → **Add new item**.
2. Upload `dist/tesserae-clip-v0.1.0.zip`.
3. Fill the listing from `extension/store/listing.md` (name, summary,
   description, category, single-purpose, permission justifications).
4. Fill the **Privacy practices** tab from the "Data usage disclosures" section
   of `listing.md`, and paste your privacy-policy URL.
5. Upload the 128×128 icon (already in the zip) and your screenshot.
6. Choose visibility — **Public**, **Unlisted**, or **Private** — and **Submit
   for review**. Google review typically takes hours to a few days.

## 2b. Publish via the API (for repeat releases / CI)

The first item is easiest to create in the dashboard (2a) to obtain its
**extension ID**. After that, automate with `chrome-webstore-upload-cli`.

Set these secrets (never commit them):
```bash
export CWS_EXTENSION_ID=...        # from the dashboard URL after first create
export CWS_CLIENT_ID=...           # Google Cloud OAuth client (Chrome Web Store API enabled)
export CWS_CLIENT_SECRET=...
export CWS_REFRESH_TOKEN=...       # obtained once via the OAuth consent flow
```
Then run `extension/scripts/publish.sh` (uploads the zip and submits for review).

To mint a refresh token: create an OAuth client (type "Desktop"/"Web") in Google
Cloud, enable the **Chrome Web Store API**, and exchange an auth code for a
refresh token (see the chrome-webstore-upload-keys helper). Provide those four
values to me and I can run the publish for you.

## Notes / known gaps before a polished public launch
- The generated mosaic icon is clean but basic — swap in a designed icon if you
  want a stronger store presence.
- This extension only works alongside a local `tesserae serve`; the listing
  description states that requirement so reviewers/users aren't surprised.
