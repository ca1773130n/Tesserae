/*
 * background.js — MV3 service worker for Clip to Tesserae.
 *
 * Responsibilities:
 *   - register a "Clip to Tesserae" context-menu item (page + selection)
 *   - handle the 'clip-page' keyboard command
 *   - inject content.js, gather the extracted payload, POST it to the
 *     configured Tesserae endpoint, and surface success/failure via badge
 *     + notification
 *   - answer popup messages so the popup can clip with a user note
 */

const DEFAULTS = {
  // 127.0.0.1, not "localhost": `tesserae serve` binds IPv4-only, but Chrome
  // resolves "localhost" to IPv6 ::1 first, so a localhost POST is refused and
  // never reaches the server (no logs, no response). 127.0.0.1 forces IPv4.
  endpoint: "http://127.0.0.1:8765",
  defaultTags: "",
  captureMode: "selection-first", // "article" | "selection-first"
  tldr: true,
  // Optional shared secret. When the server runs with TESSERAE_CLIP_TOKEN set
  // (e.g. exposed on a LAN/public IP), it requires a matching X-Tesserae-Token
  // header; set the same value here. Empty = no token sent.
  token: ""
};

const MENU_ID = "tesserae-clip";

// ---- settings ---------------------------------------------------------------
async function getSettings() {
  const stored = await chrome.storage.sync.get(DEFAULTS);
  return { ...DEFAULTS, ...stored };
}

function parseTags(raw) {
  if (!raw) return [];
  return String(raw)
    .split(",")
    .map((t) => t.trim())
    .filter(Boolean);
}

// ---- lifecycle --------------------------------------------------------------
chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create(
    {
      id: MENU_ID,
      title: "Clip to Tesserae",
      contexts: ["page", "selection"]
    },
    () => void chrome.runtime.lastError // ignore "duplicate id" on reload.
  );
});

// ---- injection + extraction -------------------------------------------------
async function extractFromTab(tabId, mode) {
  // Ensure the extractor is present (idempotent — guarded in content.js).
  await chrome.scripting.executeScript({
    target: { tabId },
    files: ["src/content.js"]
  });

  const preferSelection = mode !== "article";
  const response = await chrome.tabs.sendMessage(tabId, {
    type: "EXTRACT",
    mode,
    preferSelection
  });
  if (!response) throw new Error("No response from page extractor.");
  if (response.error) throw new Error(response.error);
  return response;
}

// ---- POST to Tesserae -------------------------------------------------------
async function postClip(payload, endpoint, token) {
  const base = String(endpoint || DEFAULTS.endpoint).replace(/\/+$/, "");
  const headers = { "Content-Type": "application/json" };
  if (token) headers["X-Tesserae-Token"] = String(token);
  const res = await fetch(`${base}/api/clip`, {
    method: "POST",
    headers,
    body: JSON.stringify(payload)
  });
  if (!res.ok) {
    throw new Error(`Tesserae responded ${res.status}`);
  }
  let data = {};
  try {
    data = await res.json();
  } catch (_) {
    data = {};
  }
  return data;
}

// ---- feedback ---------------------------------------------------------------
function flashBadge(text, color) {
  chrome.action.setBadgeBackgroundColor({ color });
  chrome.action.setBadgeText({ text });
  setTimeout(() => chrome.action.setBadgeText({ text: "" }), 3500);
}

function notify(title, message) {
  chrome.notifications.create({
    type: "basic",
    iconUrl: chrome.runtime.getURL("icons/icon128.png"),
    title,
    message
  });
}

// ---- the core clip flow (used by menu + command) ---------------------------
async function clipTab(tabId, { note = "" } = {}) {
  const settings = await getSettings();
  try {
    const extracted = await extractFromTab(tabId, settings.captureMode);
    const payload = {
      url: extracted.url,
      title: extracted.title,
      meta: extracted.meta,
      content: extracted.content,
      selection: extracted.selection,
      note,
      tags: parseTags(settings.defaultTags),
      tldr: settings.tldr
    };
    const data = await postClip(payload, settings.endpoint, settings.token);
    flashBadge("✓", "#2e7d5b");
    notify(
      "Clipped to Tesserae",
      data && data.tldr ? String(data.tldr) : extracted.title
    );
    return { ok: true, data };
  } catch (err) {
    const offline = /Failed to fetch|NetworkError|ERR_CONNECTION/i.test(
      String(err && err.message)
    );
    flashBadge("!", "#b3261e");
    notify(
      offline ? "Tesserae not running" : "Clip failed",
      offline
        ? "Start `tesserae serve` and try again."
        : String(err && err.message ? err.message : err)
    );
    return { ok: false, error: String(err && err.message ? err.message : err), offline };
  }
}

// ---- context menu -----------------------------------------------------------
chrome.contextMenus.onClicked.addListener((info, tab) => {
  if (info.menuItemId === MENU_ID && tab && tab.id != null) {
    clipTab(tab.id);
  }
});

// ---- keyboard command -------------------------------------------------------
chrome.commands.onCommand.addListener(async (command) => {
  if (command !== "clip-page") return;
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (tab && tab.id != null) clipTab(tab.id);
});

// ---- popup messages ---------------------------------------------------------
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (!msg || !msg.type) return false;

  if (msg.type === "POPUP_EXTRACT") {
    (async () => {
      try {
        const settings = await getSettings();
        const extracted = await extractFromTab(msg.tabId, settings.captureMode);
        sendResponse({ ok: true, extracted, settings });
      } catch (err) {
        sendResponse({ ok: false, error: String(err && err.message ? err.message : err) });
      }
    })();
    return true;
  }

  if (msg.type === "POPUP_CLIP") {
    (async () => {
      const settings = await getSettings();
      try {
        const payload = {
          url: msg.payload.url,
          title: msg.payload.title,
          meta: msg.payload.meta,
          content: msg.payload.content,
          selection: msg.payload.selection,
          note: msg.payload.note || "",
          tags: msg.payload.tags || [],
          tldr: msg.payload.tldr
        };
        const data = await postClip(payload, settings.endpoint, settings.token);
        flashBadge("✓", "#2e7d5b");
        sendResponse({ ok: true, data });
      } catch (err) {
        const offline = /Failed to fetch|NetworkError|ERR_CONNECTION/i.test(
          String(err && err.message)
        );
        sendResponse({
          ok: false,
          offline,
          error: String(err && err.message ? err.message : err)
        });
      }
    })();
    return true;
  }

  return false;
});
