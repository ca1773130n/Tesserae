/*
 * options.js — load/persist Clip to Tesserae settings in chrome.storage.sync.
 */

const DEFAULTS = {
  // 127.0.0.1, not "localhost": `tesserae serve` is IPv4-only and Chrome hits
  // IPv6 ::1 for "localhost", which is refused. 127.0.0.1 forces IPv4.
  endpoint: "http://127.0.0.1:8765",
  defaultTags: "",
  captureMode: "selection-first",
  tldr: true,
  token: "",
  // Target project alias for fleet servers (serving every registered project).
  // Empty = let the server pick (single-project serve, or a fleet server with
  // exactly one registered project).
  project: ""
};

const $ = (id) => document.getElementById(id);
const els = {
  endpoint: $("endpoint"),
  defaultTags: $("defaultTags"),
  captureMode: $("captureMode"),
  tldr: $("tldr"),
  token: $("token"),
  project: $("project"),
  saveBtn: $("saveBtn"),
  saved: $("saved")
};

async function load() {
  const s = await chrome.storage.sync.get(DEFAULTS);
  els.endpoint.value = s.endpoint || DEFAULTS.endpoint;
  els.defaultTags.value = s.defaultTags || "";
  els.captureMode.value = s.captureMode || DEFAULTS.captureMode;
  els.tldr.checked = s.tldr !== false;
  els.token.value = s.token || "";
  els.project.value = s.project || "";
}

function normalizeEndpoint(raw) {
  let v = String(raw || "").trim();
  if (!v) return DEFAULTS.endpoint;
  if (!/^https?:\/\//i.test(v)) v = "http://" + v;
  return v.replace(/\/+$/, "");
}

// localhost / 127.0.0.1 are already in host_permissions. Any other endpoint host
// (e.g. a LAN IP, to clip from another machine to this one's `tesserae serve`)
// needs an optional host permission granted at runtime, or the background
// worker's fetch is blocked by the browser.
async function ensureHostPermission(endpoint) {
  if (/^https?:\/\/(localhost|127\.0\.0\.1)([:/]|$)/i.test(endpoint)) return true;
  let origin;
  try {
    origin = new URL(endpoint).origin + "/*";
  } catch (_) {
    return true; // unparseable → let the save proceed; the clip will surface it
  }
  try {
    if (await chrome.permissions.contains({ origins: [origin] })) return true;
    return await chrome.permissions.request({ origins: [origin] });
  } catch (_) {
    return false;
  }
}

async function save() {
  const endpoint = normalizeEndpoint(els.endpoint.value);
  // Request host access FIRST so the call stays inside the Save click's user
  // gesture (chrome.permissions.request requires one).
  const granted = await ensureHostPermission(endpoint);
  await chrome.storage.sync.set({
    endpoint,
    defaultTags: els.defaultTags.value.trim(),
    captureMode: els.captureMode.value,
    tldr: els.tldr.checked,
    token: els.token.value.trim(),
    project: els.project.value.trim()
  });
  els.endpoint.value = endpoint;
  els.saved.textContent = granted
    ? "Saved ✓"
    : "Saved — but host access was denied; clips to this endpoint will fail.";
  els.saved.classList.add("show");
  setTimeout(() => els.saved.classList.remove("show"), granted ? 1600 : 4000);
}

els.saveBtn.addEventListener("click", save);
load();
