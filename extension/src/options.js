/*
 * options.js — load/persist Clip to Tesserae settings in chrome.storage.sync.
 */

const DEFAULTS = {
  // 127.0.0.1, not "localhost": `tesserae serve` is IPv4-only and Chrome hits
  // IPv6 ::1 for "localhost", which is refused. 127.0.0.1 forces IPv4.
  endpoint: "http://127.0.0.1:8765",
  defaultTags: "",
  captureMode: "selection-first",
  tldr: true
};

const $ = (id) => document.getElementById(id);
const els = {
  endpoint: $("endpoint"),
  defaultTags: $("defaultTags"),
  captureMode: $("captureMode"),
  tldr: $("tldr"),
  saveBtn: $("saveBtn"),
  saved: $("saved")
};

async function load() {
  const s = await chrome.storage.sync.get(DEFAULTS);
  els.endpoint.value = s.endpoint || DEFAULTS.endpoint;
  els.defaultTags.value = s.defaultTags || "";
  els.captureMode.value = s.captureMode || DEFAULTS.captureMode;
  els.tldr.checked = s.tldr !== false;
}

function normalizeEndpoint(raw) {
  let v = String(raw || "").trim();
  if (!v) return DEFAULTS.endpoint;
  if (!/^https?:\/\//i.test(v)) v = "http://" + v;
  return v.replace(/\/+$/, "");
}

async function save() {
  const settings = {
    endpoint: normalizeEndpoint(els.endpoint.value),
    defaultTags: els.defaultTags.value.trim(),
    captureMode: els.captureMode.value,
    tldr: els.tldr.checked
  };
  await chrome.storage.sync.set(settings);
  els.endpoint.value = settings.endpoint;
  els.saved.classList.add("show");
  setTimeout(() => els.saved.classList.remove("show"), 1600);
}

els.saveBtn.addEventListener("click", save);
load();
