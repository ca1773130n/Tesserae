/*
 * popup.js — drives the Clip to Tesserae popup.
 *
 * On open: ask the worker to inject content.js and extract the current page,
 * render a preview, let the user add a note + tags + toggle TL;DR, then POST.
 */

const $ = (id) => document.getElementById(id);

const els = {
  title: $("title"),
  meta: $("meta"),
  preview: $("preview"),
  selBadge: $("selBadge"),
  note: $("note"),
  tags: $("tags"),
  tldr: $("tldr"),
  clipBtn: $("clipBtn"),
  optionsBtn: $("optionsBtn"),
  status: $("status"),
  tldrBox: $("tldrBox"),
  tldrText: $("tldrText")
};

let state = {
  tabId: null,
  extracted: null,
  settings: null
};

function setStatus(msg, kind) {
  els.status.textContent = msg || "";
  els.status.className = "status" + (kind ? " " + kind : "");
}

function activeTab() {
  return new Promise((resolve) => {
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) =>
      resolve(tabs && tabs[0])
    );
  });
}

function send(message) {
  return new Promise((resolve, reject) => {
    chrome.runtime.sendMessage(message, (resp) => {
      const err = chrome.runtime.lastError;
      if (err) reject(new Error(err.message));
      else resolve(resp);
    });
  });
}

async function init() {
  const tab = await activeTab();
  if (!tab || tab.id == null) {
    els.title.textContent = "No active tab";
    setStatus("Cannot clip this tab.", "err");
    return;
  }
  state.tabId = tab.id;
  els.title.textContent = tab.title || tab.url || "Untitled";

  try {
    const resp = await send({ type: "POPUP_EXTRACT", tabId: tab.id });
    if (!resp || !resp.ok) {
      throw new Error((resp && resp.error) || "Extraction failed");
    }
    state.extracted = resp.extracted;
    state.settings = resp.settings;
    render();
  } catch (err) {
    setStatus(
      "Could not read this page (try reloading it): " + err.message,
      "err"
    );
    // Still allow clipping the title/url as a fallback.
    state.extracted = {
      url: tab.url,
      title: tab.title || tab.url,
      meta: {},
      content: "",
      selection: false
    };
    state.settings = state.settings || { defaultTags: "", tldr: true };
    render();
  }
}

function render() {
  const ex = state.extracted;
  const s = state.settings || {};

  els.title.textContent = ex.title || ex.url || "Untitled";

  const metaBits = [];
  if (ex.meta && ex.meta.siteName) metaBits.push(ex.meta.siteName);
  if (ex.meta && ex.meta.byline) metaBits.push(ex.meta.byline);
  els.meta.textContent = metaBits.join(" · ") || ex.url;
  els.meta.title = ex.url;

  if (ex.selection) els.selBadge.style.display = "inline-block";

  const preview = (ex.content || "").trim();
  if (preview) {
    els.preview.textContent =
      preview.slice(0, 360) + (preview.length > 360 ? "…" : "");
    els.preview.classList.remove("empty");
  } else {
    els.preview.textContent = "No readable content found — title + URL only.";
    els.preview.classList.add("empty");
  }

  els.tags.value = s.defaultTags || "";
  els.tldr.checked = s.tldr !== false;

  els.clipBtn.disabled = false;
}

function parseTags(raw) {
  return String(raw || "")
    .split(",")
    .map((t) => t.trim())
    .filter(Boolean);
}

async function doClip() {
  if (!state.extracted) return;
  els.clipBtn.disabled = true;
  setStatus("Clipping…");
  els.tldrBox.style.display = "none";

  const payload = {
    url: state.extracted.url,
    title: state.extracted.title,
    meta: state.extracted.meta || {},
    content: state.extracted.content || "",
    selection: !!state.extracted.selection,
    note: els.note.value.trim(),
    tags: parseTags(els.tags.value),
    tldr: els.tldr.checked
  };

  try {
    const resp = await send({ type: "POPUP_CLIP", payload });
    if (resp && resp.ok) {
      setStatus("Saved ✓", "ok");
      const tldr = resp.data && resp.data.tldr;
      if (tldr) {
        els.tldrText.textContent = String(tldr);
        els.tldrBox.style.display = "block";
      }
      els.clipBtn.textContent = "Clipped";
    } else if (resp && resp.offline) {
      setStatus("Tesserae not running — start `tesserae serve`", "err");
      els.clipBtn.disabled = false;
    } else {
      setStatus((resp && resp.error) || "Clip failed", "err");
      els.clipBtn.disabled = false;
    }
  } catch (err) {
    setStatus("Tesserae not running — start `tesserae serve`", "err");
    els.clipBtn.disabled = false;
  }
}

els.clipBtn.addEventListener("click", doClip);
els.optionsBtn.addEventListener("click", () => chrome.runtime.openOptionsPage());

// Cmd/Ctrl+Enter clips from the popup.
document.addEventListener("keydown", (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
    e.preventDefault();
    if (!els.clipBtn.disabled) doClip();
  }
});

init();
