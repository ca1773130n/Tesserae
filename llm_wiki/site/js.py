"""Client-side JavaScript bundles for the LLM-Wiki static site.

Module-level string constants compose into the bundle that gets written to
``assets/app.js`` by the StaticSiteBuilder:

- :data:`JS_THEME_TOGGLE` — Reads the persisted theme from ``localStorage``
  (``llm-wiki-theme``), falls back to ``prefers-color-scheme``, and toggles
  ``data-theme`` on ``<html>`` for any ``[data-toggle-theme]`` click. Updates
  the ``aria-label`` of the toggle to reflect the current theme. Listens to
  the system colour-scheme media query and follows it while the user has
  not explicitly chosen.
- :data:`JS_RAIL_DRAWER` — left rail and TOC drawers (mobile chrome).
- :data:`JS_SEARCH_PALETTE` — command palette: opens with ``cmd/ctrl+k`` or
  ``/`` (when not in a textfield) or any ``[data-open-search]`` click.
  Fetches ``/search-index.json`` once per session, then filters in memory.
  Up/Down navigates highlighted result, Enter opens, Escape closes,
  click-outside closes. Recents persisted to ``localStorage``.
- :data:`JS_GRAPH` — interactive 3D force-directed graph view powered by
  ``3d-force-graph`` (with ``three`` dynamically imported from esm.sh as a
  peer dep). Cursor-anchored wheel zoom (raycast through cursor → world).
  Per-node ``THREE.Sprite`` labels for high-degree nodes; canvas labels in
  2D. Edge labels via ``linkThreeObject`` / ``linkCanvasObject``. Edge
  hover via ``linkHoverPrecision`` + ``onLinkHover`` populates the
  ``.graph-tooltip``. Fit-to-view computes a bounding sphere over node
  positions inside ``onEngineStop`` and reframes the camera. Falls back
  to an inline SVG layout + error banner if the CDN is blocked.

The bundle stays vanilla (no npm) and feature-detects everything it touches.
DOM updates use ``textContent`` and explicit ``createElement`` calls — never
``innerHTML`` with user-influenced data — so XSS is structurally impossible
even when the search index contains arbitrary corpus strings.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Theme toggle
# ---------------------------------------------------------------------------
JS_THEME_TOGGLE = r"""
(function(){
  var root = document.documentElement;
  var KEY = 'llm-wiki-theme';
  var EXPLICIT_KEY = 'llm-wiki-theme-explicit';

  function readSaved(){
    try { return localStorage.getItem(KEY); } catch (_) { return null; }
  }
  function readExplicit(){
    try { return localStorage.getItem(EXPLICIT_KEY) === '1'; } catch (_) { return false; }
  }
  function writeSaved(value, explicit){
    try {
      localStorage.setItem(KEY, value);
      if (explicit) localStorage.setItem(EXPLICIT_KEY, '1');
    } catch (_) {}
  }
  function systemPref(){
    try {
      return (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches)
        ? 'dark' : 'light';
    } catch (_) { return 'light'; }
  }

  // Compute and apply the initial theme as early as the bundle runs.
  // (The bundle is loaded with `defer` so this happens before paint of
  // user-influenced sections; if a future change inlines theme apply in
  // the document <head>, this block stays correct as the duplicate apply
  // is cheap and idempotent.)
  var initial = readSaved();
  if (initial !== 'dark' && initial !== 'light') initial = systemPref();
  root.setAttribute('data-theme', initial);

  function syncButtons(){
    var current = root.getAttribute('data-theme') || 'light';
    var nextLabel = current === 'dark' ? 'Switch to light theme' : 'Switch to dark theme';
    var btns = document.querySelectorAll('[data-toggle-theme]');
    for (var i = 0; i < btns.length; i++) {
      btns[i].setAttribute('aria-label', nextLabel);
      btns[i].setAttribute('aria-pressed', current === 'dark' ? 'true' : 'false');
      btns[i].dataset.themeCurrent = current;
    }
  }

  function setTheme(next, explicit){
    root.setAttribute('data-theme', next);
    writeSaved(next, !!explicit);
    syncButtons();
  }

  function cycle(){
    var current = root.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
    setTheme(current === 'dark' ? 'light' : 'dark', true);
  }

  // Click on any data-toggle-theme button toggles the theme.
  document.addEventListener('click', function(e){
    var t = e.target && e.target.closest && e.target.closest('[data-toggle-theme], #theme-toggle');
    if (!t) return;
    e.preventDefault();
    cycle();
  });

  // Follow OS theme while the user has not explicitly chosen.
  try {
    var mq = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)');
    if (mq) {
      var listener = function(evt){
        if (readExplicit()) return;
        setTheme(evt.matches ? 'dark' : 'light', false);
      };
      if (mq.addEventListener) mq.addEventListener('change', listener);
      else if (mq.addListener) mq.addListener(listener);
    }
  } catch (_) {}

  // Sync labels once the DOM is ready (the bundle defers, but be defensive).
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', syncButtons);
  } else {
    syncButtons();
  }
})();
"""


# ---------------------------------------------------------------------------
# Rail / TOC drawer chrome (mobile)
# ---------------------------------------------------------------------------
JS_RAIL_DRAWER = r"""
(function(){
  var root = document.documentElement;
  function setOpen(attr, btnSel, open){
    if (open) root.setAttribute(attr, ''); else root.removeAttribute(attr);
    var btns = document.querySelectorAll(btnSel);
    for (var i = 0; i < btns.length; i++) btns[i].setAttribute('aria-expanded', open ? 'true' : 'false');
  }
  function toggle(attr, btnSel){
    setOpen(attr, btnSel, !root.hasAttribute(attr));
  }
  document.addEventListener('click', function(e){
    var rail = e.target.closest('[data-toggle-rail]');
    if (rail) { e.preventDefault(); toggle('data-rail-open', '[data-toggle-rail]'); return; }
    var toc = e.target.closest('[data-toggle-toc]');
    if (toc) { e.preventDefault(); toggle('data-toc-open', '[data-toggle-toc]'); return; }
    if (root.hasAttribute('data-rail-open')) {
      var inRail = e.target.closest('#rail a');
      if (inRail) setOpen('data-rail-open', '[data-toggle-rail]', false);
    }
    if (root.hasAttribute('data-toc-open')) {
      var inToc = e.target.closest('#toc a');
      if (inToc) setOpen('data-toc-open', '[data-toggle-toc]', false);
    }
  });
  document.addEventListener('keydown', function(e){
    if (e.key !== 'Escape') return;
    if (root.hasAttribute('data-rail-open')) setOpen('data-rail-open', '[data-toggle-rail]', false);
    if (root.hasAttribute('data-toc-open')) setOpen('data-toc-open', '[data-toggle-toc]', false);
  });
})();
"""


# ---------------------------------------------------------------------------
# Search palette (cmd+k / / opens; fetches /search-index.json)
# ---------------------------------------------------------------------------
JS_SEARCH_PALETTE = r"""
(function(){
  var data = null;
  var dataReady = null;
  var avgDocLen = 1;
  var palette = null;
  var input = null;
  var resultsEl = null;
  var statusEl = null;
  var tabsEl = null;
  var highlightIndex = 0;
  var currentItems = [];
  var activeTab = 'all';
  var RECENTS_KEY = 'llm-wiki-recents';
  var TAB_KEY = 'llm-wiki-search-tab';
  var BM25_K1 = 1.2;
  var BM25_B = 0.75;

  // Tab strip: All / Sources / Concepts / Papers / Repos / Topics / Syntheses / Questions.
  // ``prefix`` is the digit-prefix kind hint that the parser accepts in the
  // query (``p:vision``, ``c:gauss``, etc.).
  var TABS = [
    { id: 'all',       label: 'All',       kind: null,         prefix: null },
    { id: 'sources',   label: 'Sources',   kind: 'sources',    prefix: 's' },
    { id: 'concepts',  label: 'Concepts',  kind: 'concepts',   prefix: 'c' },
    { id: 'papers',    label: 'Papers',    kind: 'papers',     prefix: 'p' },
    { id: 'repos',     label: 'Repos',     kind: 'repos',      prefix: 'r' },
    { id: 'topics',    label: 'Topics',    kind: 'topics',     prefix: 't' },
    { id: 'syntheses', label: 'Syntheses', kind: 'syntheses',  prefix: 'y' },
    { id: 'questions', label: 'Questions', kind: 'questions',  prefix: 'q' }
  ];

  // English + Korean common-particle stop-words. Mirrors STOP_WORDS in
  // llm_wiki.site.search so server-built tokens and client queries agree.
  var STOP_WORDS = (function(){
    var arr = [
      'a','an','the','and','or','but','if','then','else',
      'of','to','in','on','at','by','for','with','from',
      'is','are','was','were','be','been','being',
      'as','it','its','this','that','these','those',
      'we','you','they','he','she','i','me','us','them',
      'do','does','did','have','has','had',
      'not','no','yes',
      'so','than','too','very','can','will','just',
      '은','는','이','가',
      '을','를','의','에',
      '와','과','도','만'
    ];
    var s = Object.create(null);
    for (var i = 0; i < arr.length; i++) s[arr[i]] = true;
    // Inline literals for the common Hangul particles (decomposed JAMO above
    // is a defensive backup; many browsers compose to single syllables).
    var literals = ['은','는','이','가','을','를','의',
      '에','와','과','도','만','에서','으로',
      '로','께서','한테'];
    for (var k = 0; k < literals.length; k++) s[literals[k]] = true;
    return s;
  })();

  // Word/digit/underscore + Latin-Extended + Hangul Syllables block.
  var TOKEN_RE = /[\w\u00C0-\u024F\uAC00-\uD7AF]+/g;

  function tokenize(text){
    if (!text) return [];
    var out = [];
    var lc = String(text).toLowerCase();
    TOKEN_RE.lastIndex = 0;
    var m;
    while ((m = TOKEN_RE.exec(lc)) !== null) {
      var t = m[0];
      if (!t) continue;
      if (STOP_WORDS[t]) continue;
      out.push(t);
    }
    return out;
  }

  function loadRecents(){
    try { return JSON.parse(localStorage.getItem(RECENTS_KEY) || '[]'); } catch (_) { return []; }
  }
  function saveRecent(item){
    try {
      var list = loadRecents().filter(function(x){ return x.href !== item.href; });
      list.unshift({ title: item.title, href: item.href, kind: item.kind || item.type });
      localStorage.setItem(RECENTS_KEY, JSON.stringify(list.slice(0, 10)));
    } catch (_) {}
  }
  function loadActiveTab(){
    try {
      var saved = localStorage.getItem(TAB_KEY);
      if (saved && tabById(saved)) return saved;
    } catch (_) {}
    return 'all';
  }
  function saveActiveTab(id){
    try { localStorage.setItem(TAB_KEY, id); } catch (_) {}
  }
  function tabById(id){
    for (var i = 0; i < TABS.length; i++) if (TABS[i].id === id) return TABS[i];
    return null;
  }
  function tabByPrefix(p){
    for (var i = 0; i < TABS.length; i++) if (TABS[i].prefix === p) return TABS[i];
    return null;
  }

  // Resolve search-index location relative to the document. The site emits
  // it at the site root; pages live at depth 0..2, so a single absolute
  // URL would 404 if served from a sub-path. We try the inline payload
  // first (if rendered into the page), then the relative `search-index.json`,
  // then the absolute `/search-index.json` as a final fallback.
  function ensureData(){
    if (data) return Promise.resolve(data);
    if (dataReady) return dataReady;
    var inline = document.getElementById('search-data');
    if (inline) {
      try {
        data = JSON.parse(inline.textContent || '[]');
        recomputeAvg();
        return Promise.resolve(data);
      } catch (_) {}
    }
    var prefix = '';
    var brand = document.querySelector('.topbar .brand');
    if (brand && brand.getAttribute('href')) {
      var href = brand.getAttribute('href');
      // strip trailing 'index.html'
      prefix = href.replace(/index\.html$/, '');
    }
    dataReady = fetch(prefix + 'search-index.json')
      .then(function(r){ return r.ok ? r.json() : Promise.reject(new Error('not ok')); })
      .catch(function(){ return fetch('/search-index.json').then(function(r){ return r.ok ? r.json() : []; }); })
      .then(function(j){ data = Array.isArray(j) ? j : (j && j.items) || []; recomputeAvg(); return data; })
      .catch(function(){ data = []; return data; });
    return dataReady;
  }

  function recomputeAvg(){
    if (!data || !data.length) { avgDocLen = 1; return; }
    var total = 0, n = 0;
    for (var i = 0; i < data.length; i++) {
      var v = data[i].len;
      if (typeof v === 'number') { total += v; n += 1; }
    }
    avgDocLen = n ? (total / n) : 1;
  }

  function ensurePaletteShell(){
    palette = document.getElementById('palette');
    if (!palette) return false;
    input = palette.querySelector('#search') || document.getElementById('search');
    // Add the missing pieces (tabs / results / status) lazily on first open.
    var box = palette.querySelector('.palette-box');
    if (!box) return false;
    tabsEl = palette.querySelector('#palette-tabs');
    if (!tabsEl) {
      tabsEl = document.createElement('div');
      tabsEl.id = 'palette-tabs';
      tabsEl.className = 'palette-tabs';
      tabsEl.setAttribute('role', 'tablist');
      tabsEl.setAttribute('aria-label', 'Filter by type');
      for (var ti = 0; ti < TABS.length; ti++) {
        var tab = TABS[ti];
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'palette-tab';
        btn.dataset.tab = tab.id;
        btn.setAttribute('role', 'tab');
        btn.textContent = tab.label;
        tabsEl.appendChild(btn);
      }
      // Insert tabs above the input row so the strip is the first focus stop.
      if (box.firstChild) box.insertBefore(tabsEl, box.firstChild);
      else box.appendChild(tabsEl);
      tabsEl.addEventListener('click', function(ev){
        var t = ev.target && ev.target.closest && ev.target.closest('.palette-tab');
        if (!t) return;
        setActiveTab(t.dataset.tab || 'all');
      });
    }
    resultsEl = palette.querySelector('#palette-results');
    if (!resultsEl) {
      resultsEl = document.createElement('ul');
      resultsEl.id = 'palette-results';
      resultsEl.className = 'palette-results';
      resultsEl.setAttribute('role', 'listbox');
      box.appendChild(resultsEl);
    }
    statusEl = palette.querySelector('#palette-status');
    if (!statusEl) {
      statusEl = document.createElement('p');
      statusEl.id = 'palette-status';
      statusEl.className = 'palette-status muted';
      box.appendChild(statusEl);
    }
    if (input && !input.dataset.paletteWired) {
      input.dataset.paletteWired = '1';
      input.setAttribute('role', 'combobox');
      input.setAttribute('aria-controls', 'palette-results');
      input.setAttribute('aria-autocomplete', 'list');
      input.addEventListener('input', onInput);
      input.addEventListener('keydown', onInputKeydown);
    }
    if (!palette.dataset.paletteWired) {
      palette.dataset.paletteWired = '1';
      palette.addEventListener('click', function(e){
        if (e.target === palette) closePalette();
      });
    }
    paintTabs();
    return true;
  }

  function paintTabs(){
    if (!tabsEl) return;
    var btns = tabsEl.querySelectorAll('.palette-tab');
    for (var i = 0; i < btns.length; i++) {
      var on = btns[i].dataset.tab === activeTab;
      btns[i].classList.toggle('is-active', on);
      btns[i].setAttribute('aria-selected', on ? 'true' : 'false');
      btns[i].tabIndex = on ? 0 : -1;
    }
  }

  function setActiveTab(id){
    if (!tabById(id)) id = 'all';
    activeTab = id;
    saveActiveTab(id);
    paintTabs();
    runSearch();
  }

  function setStatus(text){
    if (!statusEl) return;
    statusEl.textContent = text || '';
  }

  // ----- ranking ------------------------------------------------------------

  function recencyFactor(ts){
    if (typeof ts !== 'number' || !isFinite(ts) || ts <= 0) return 0;
    var nowSec = Date.now() / 1000;
    var ageDays = Math.max(0, (nowSec - ts) / 86400);
    if (ageDays <= 7) return 1.0;
    if (ageDays >= 180) return 0.0;
    return Math.max(0, 1 - (ageDays - 7) / (180 - 7));
  }

  function recencyBadge(ts){
    if (typeof ts !== 'number' || !isFinite(ts) || ts <= 0) return '';
    var nowSec = Date.now() / 1000;
    var ageDays = Math.max(0, Math.floor((nowSec - ts) / 86400));
    if (ageDays < 1) return 'today';
    if (ageDays < 14) return ageDays + 'd';
    if (ageDays < 60) return Math.floor(ageDays / 7) + 'w';
    if (ageDays < 365) return Math.floor(ageDays / 30) + 'mo';
    if (ageDays > 180) return '180d+';
    return ageDays + 'd';
  }

  function bm25Tokens(qTokens, entry){
    var tokens = entry.tokens;
    if (!tokens || !tokens.length || !qTokens.length) return 0;
    // Build a small frequency map over entry.tokens — done once per call so
    // typing latency stays linear in the corpus size.
    var counts = Object.create(null);
    for (var i = 0; i < tokens.length; i++) {
      var t = tokens[i];
      counts[t] = (counts[t] || 0) + 1;
    }
    var dl = entry.len || tokens.length || 1;
    var norm = BM25_K1 * (1 - BM25_B + BM25_B * dl / (avgDocLen || 1));
    var s = 0;
    for (var j = 0; j < qTokens.length; j++) {
      var q = qTokens[j];
      var tf = counts[q];
      if (!tf) continue;
      s += tf / (tf + norm);
    }
    return s;
  }

  function substringFallback(query, entry){
    var q = query.toLowerCase();
    var title = (entry.title || '').toString().toLowerCase();
    var summary = (entry.summary || entry.description || '').toString().toLowerCase();
    var kind = (entry.kind || entry.type || '').toString().toLowerCase();
    if (title.indexOf(q) !== -1) return 0.6;
    if (summary.indexOf(q) !== -1) return 0.3;
    if (kind.indexOf(q) !== -1) return 0.15;
    return 0;
  }

  function scoreEntry(query, qTokens, entry){
    var base;
    if (entry.tokens && entry.tokens.length) {
      base = bm25Tokens(qTokens, entry);
      if (!base) {
        // Token-level miss: try the legacy substring as a soft fallback so
        // partial matches like "splat" still surface "Gaussian Splatting".
        base = substringFallback(query, entry) * 0.5;
      }
    } else {
      base = substringFallback(query, entry);
    }
    if (!base) return 0;
    var rec = recencyFactor(entry.created_ts);
    return base * (1 + 0.1 * rec);
  }

  function parseQuery(raw){
    // ``c:gauss`` / ``p:vision`` etc. set the kind hint for this query only,
    // overlaying whatever tab is active.
    var hintTab = null;
    var query = raw;
    var m = /^([a-z]):\s*(.*)$/.exec(raw);
    if (m) {
      var t = tabByPrefix(m[1]);
      if (t) {
        hintTab = t.id;
        query = m[2];
      }
    }
    return { query: query, hintTab: hintTab, tokens: tokenize(query) };
  }

  function appendHighlightedText(parent, text, qTokens){
    if (!qTokens.length) {
      parent.appendChild(document.createTextNode(text));
      return;
    }
    var lc = text.toLowerCase();
    var i = 0;
    var n = text.length;
    while (i < n) {
      var bestStart = -1, bestEnd = -1;
      for (var j = 0; j < qTokens.length; j++) {
        var q = qTokens[j];
        if (!q) continue;
        var idx = lc.indexOf(q, i);
        if (idx === -1) continue;
        if (bestStart === -1 || idx < bestStart) {
          bestStart = idx;
          bestEnd = idx + q.length;
        }
      }
      if (bestStart === -1) {
        parent.appendChild(document.createTextNode(text.slice(i)));
        return;
      }
      if (bestStart > i) {
        parent.appendChild(document.createTextNode(text.slice(i, bestStart)));
      }
      var mark = document.createElement('mark');
      mark.textContent = text.slice(bestStart, bestEnd);
      parent.appendChild(mark);
      i = bestEnd;
    }
  }

  function makeRow(item, index, qTokens){
    var li = document.createElement('li');
    li.className = 'palette-result';
    li.dataset.kind = (item.kind || item.type || '').toString();
    li.setAttribute('role', 'option');
    li.dataset.index = String(index);
    li.dataset.href = item.href || '';
    var a = document.createElement('a');
    a.href = item.href || '#';
    a.className = 'palette-result-link';
    var badge = document.createElement('span');
    badge.className = 'palette-result-kind badge';
    badge.textContent = (item.kind || item.type || '').toString();
    var title = document.createElement('strong');
    title.className = 'palette-result-title';
    appendHighlightedText(title, (item.title || item.id || '').toString(), qTokens);
    var summary = document.createElement('span');
    summary.className = 'palette-result-summary muted';
    var summaryText = (item.summary || item.description || item.source_path || '').toString();
    if (summaryText.length > 160) summaryText = summaryText.slice(0, 157) + '…';
    appendHighlightedText(summary, summaryText, qTokens);
    a.appendChild(badge);
    a.appendChild(title);
    a.appendChild(summary);
    var rec = recencyBadge(item.created_ts);
    if (rec) {
      var recEl = document.createElement('span');
      recEl.className = 'palette-result-recency';
      recEl.textContent = rec;
      a.appendChild(recEl);
    }
    li.appendChild(a);
    return li;
  }

  function render(items, label, qTokens){
    if (!resultsEl) return;
    while (resultsEl.firstChild) resultsEl.removeChild(resultsEl.firstChild);
    currentItems = items.slice(0, 30);
    if (!currentItems.length) {
      setStatus(label || 'No results.');
      return;
    }
    setStatus(label || (currentItems.length + ' result' + (currentItems.length === 1 ? '' : 's')));
    for (var i = 0; i < currentItems.length; i++) {
      resultsEl.appendChild(makeRow(currentItems[i], i, qTokens || []));
    }
    highlightIndex = 0;
    updateHighlight();
  }

  function updateHighlight(){
    if (!resultsEl) return;
    var rows = resultsEl.querySelectorAll('.palette-result');
    for (var i = 0; i < rows.length; i++) {
      if (i === highlightIndex) {
        rows[i].classList.add('is-active');
        rows[i].setAttribute('aria-selected', 'true');
        if (rows[i].scrollIntoView) {
          try { rows[i].scrollIntoView({ block: 'nearest' }); } catch (_) {}
        }
      } else {
        rows[i].classList.remove('is-active');
        rows[i].setAttribute('aria-selected', 'false');
      }
    }
  }

  function runSearch(){
    if (!input) return;
    var raw = (input.value || '').trim();
    var parsed = parseQuery(raw);
    var effectiveTab = parsed.hintTab || activeTab;
    ensureData().then(function(items){
      var corpus = items;
      var tab = tabById(effectiveTab);
      if (tab && tab.kind) {
        corpus = corpus.filter(function(it){ return (it.kind || it.type) === tab.kind; });
      }
      if (!parsed.query) {
        var recents = loadRecents();
        if (recents.length && effectiveTab === 'all') {
          render(recents, 'Recent', []);
        } else {
          render(corpus.slice(0, 12), 'Browse · ' + (tab ? tab.label : 'All'), []);
        }
        return;
      }
      var scored = [];
      for (var i = 0; i < corpus.length; i++) {
        var s = scoreEntry(parsed.query, parsed.tokens, corpus[i]);
        if (s > 0) scored.push({ s: s, e: corpus[i] });
      }
      scored.sort(function(a, b){ return b.s - a.s; });
      var matched = scored.map(function(x){ return x.e; });
      render(matched, matched.length ? null : 'No matches for “' + parsed.query + '”', parsed.tokens);
    });
  }

  function onInput(){
    runSearch();
  }

  function onInputKeydown(e){
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      if (currentItems.length) {
        highlightIndex = (highlightIndex + 1) % currentItems.length;
        updateHighlight();
      }
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      if (currentItems.length) {
        highlightIndex = (highlightIndex - 1 + currentItems.length) % currentItems.length;
        updateHighlight();
      }
    } else if (e.key === 'PageDown') {
      e.preventDefault();
      if (currentItems.length) {
        highlightIndex = Math.min(currentItems.length - 1, highlightIndex + 5);
        updateHighlight();
      }
    } else if (e.key === 'PageUp') {
      e.preventDefault();
      if (currentItems.length) {
        highlightIndex = Math.max(0, highlightIndex - 5);
        updateHighlight();
      }
    } else if (e.key === 'Tab') {
      // Tab cycles through the type-scope tabs (Shift+Tab steps backward).
      e.preventDefault();
      var idx = -1;
      for (var i = 0; i < TABS.length; i++) if (TABS[i].id === activeTab) { idx = i; break; }
      if (idx === -1) idx = 0;
      var nextIdx = e.shiftKey ? (idx - 1 + TABS.length) % TABS.length : (idx + 1) % TABS.length;
      setActiveTab(TABS[nextIdx].id);
    } else if (e.key === 'Enter') {
      var item = currentItems[highlightIndex];
      if (item && item.href) {
        e.preventDefault();
        saveRecent({ title: item.title, href: item.href, kind: item.kind || item.type });
        window.location.href = item.href;
      }
    } else if (e.key === 'Escape') {
      e.preventDefault();
      closePalette();
    }
  }

  function openPalette(){
    if (!ensurePaletteShell()) return;
    activeTab = loadActiveTab();
    paintTabs();
    palette.hidden = false;
    palette.setAttribute('data-open', '');
    document.body.classList.add('palette-open');
    setTimeout(function(){ try { input && input.focus(); input && input.select(); } catch (_) {} }, 0);
    runSearch();
  }

  function closePalette(){
    if (!palette) return;
    palette.hidden = true;
    palette.removeAttribute('data-open');
    document.body.classList.remove('palette-open');
  }

  document.addEventListener('click', function(e){
    var opener = e.target && e.target.closest && e.target.closest('[data-open-search]');
    if (opener) { e.preventDefault(); openPalette(); return; }
    var resultLink = e.target && e.target.closest && e.target.closest('.palette-result-link');
    if (resultLink) {
      var li = resultLink.closest('.palette-result');
      var idx = li ? parseInt(li.dataset.index || '0', 10) : -1;
      var item = currentItems[idx];
      if (item) {
        saveRecent({ title: item.title, href: item.href, kind: item.kind || item.type });
      }
      // allow default navigation
    }
  });

  document.addEventListener('keydown', function(e){
    var tag = (document.activeElement && document.activeElement.tagName) || '';
    var inField = tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || (document.activeElement && document.activeElement.isContentEditable);
    if ((e.metaKey || e.ctrlKey) && (e.key || '').toLowerCase() === 'k') {
      e.preventDefault();
      openPalette();
      return;
    }
    if (e.key === '/' && !inField) {
      e.preventDefault();
      openPalette();
      return;
    }
    if (e.key === 'Escape') {
      var p = document.getElementById('palette');
      if (p && !p.hidden) {
        e.preventDefault();
        closePalette();
      }
    }
  });
})();
"""


# ---------------------------------------------------------------------------
# Graph view (3D / 2D force-graph)
# ---------------------------------------------------------------------------
JS_GRAPH = r"""
(function(){
  var GROUP_COLORS = {
    sources:   '#94a3b8',
    papers:    '#fb7185',
    repos:     '#60a5fa',
    concepts:  '#22d3ee',
    entities:  '#a78bfa',
    topics:    '#fb923c',
    syntheses: '#34d399',
    questions: '#facc15',
    other:     '#cbd5e1'
  };
  var EDGE_COLOR_LIGHT = 'rgba(191,219,254,0.34)';
  var EDGE_COLOR_DIM   = 'rgba(148,163,184,0.012)';
  var EDGE_COLOR_HOT   = 'rgba(250,204,21,1)';
  var THREE_URL = 'https://esm.sh/three@0.169.0';

  var GROUP_HSL = {
    sources:   { h: 215, s: 20, l: 70 },
    papers:    { h: 350, s: 88, l: 70 },
    repos:     { h: 213, s: 92, l: 68 },
    concepts:  { h: 188, s: 88, l: 64 },
    entities:  { h: 258, s: 88, l: 74 },
    topics:    { h: 25,  s: 94, l: 68 },
    syntheses: { h: 154, s: 72, l: 62 },
    questions: { h: 48,  s: 96, l: 64 },
    other:     { h: 215, s: 22, l: 78 }
  };

  function hashString(value){
    var s = String(value || '');
    var h = 2166136261;
    for (var i = 0; i < s.length; i++) {
      h ^= s.charCodeAt(i);
      h = Math.imul(h, 16777619);
    }
    return h >>> 0;
  }

  function nodeColorVariant(n){
    var group = (n && n.group) || 'other';
    var base = GROUP_HSL[group] || GROUP_HSL.other;
    var h = hashString((n && (n.id || n.name)) || group);
    var hue = (base.h + ((h % 19) - 9) + 360) % 360;
    var sat = Math.max(42, Math.min(98, base.s + (((h >>> 5) % 13) - 6)));
    var light = Math.max(48, Math.min(82, base.l + (((h >>> 9) % 17) - 8)));
    return 'hsl(' + hue + ' ' + sat + '% ' + light + '%)';
  }

  function ready(fn){
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', fn);
    } else { fn(); }
  }

  ready(function(){
    var dataNode  = document.getElementById('graph-data');
    var container = document.getElementById('graph-canvas');
    if (!container) return;

    function startGraph(rawPayload){
      var payload = rawPayload || { nodes: [], links: [] };
      if (!Array.isArray(payload.nodes)) payload.nodes = [];
      if (!Array.isArray(payload.links)) payload.links = (payload.edges || []);

    var byId = new Map();
    payload.nodes.forEach(function(n){
      n.color = n.color || nodeColorVariant(n);
      n.neighbors = new Set();
      n.edges = [];
      n.degree = 0;
      byId.set(n.id, n);
    });
    payload.links.forEach(function(l){
      var a = byId.get(typeof l.source === 'object' ? l.source.id : l.source);
      var b = byId.get(typeof l.target === 'object' ? l.target.id : l.target);
      if (!a || !b) return;
      a.neighbors.add(b); b.neighbors.add(a);
      a.edges.push(l); b.edges.push(l);
      a.degree += 1; b.degree += 1;
    });

    // Compute a high-value cutoff for overview labels. The graph can have
    // hundreds of nodes; showing the top half as labels turns 2D into a hairball.
    var vals = payload.nodes.map(function(n){ return Math.max(1, n.val || n.degree || 1); }).slice().sort(function(a,b){ return a - b; });
    var medianVal = vals.length ? vals[Math.floor(vals.length / 2)] : 1;
    var overviewLabelVal = vals.length ? vals[Math.floor(vals.length * 0.86)] : medianVal;

    function shouldShowOverviewLabel(n){
      return Math.max(1, (n && (n.val || n.degree)) || 1) >= Math.max(medianVal + 1, overviewLabelVal);
    }

    var infoPanel = document.getElementById('graph-info-panel');
    var tooltip   = document.getElementById('graph-tooltip');
    var legendEl  = document.getElementById('graph-legend');
    var searchEl  = document.getElementById('graph-search-input');
    var banner    = document.getElementById('graph-error-banner');
    var btn2D     = document.querySelector('[data-graph-mode="2d"]');
    var btn3D     = document.querySelector('[data-graph-mode="3d"]');
    var btnFit    = document.querySelector('[data-graph-action="fit"]');
    var btnReset  = document.querySelector('[data-graph-action="reset"]');

    var reduceMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    var typeCounts = {};
    payload.nodes.forEach(function(n){
      var g = n.group || 'other';
      typeCounts[g] = (typeCounts[g] || 0) + 1;
    });
    var hiddenGroups = new Set();
    if (legendEl) {
      while (legendEl.firstChild) legendEl.removeChild(legendEl.firstChild);
      Object.keys(typeCounts).sort().forEach(function(group){
        var chip = document.createElement('button');
        chip.type = 'button';
        chip.className = 'graph-legend-chip';
        chip.dataset.group = group;
        var dot = document.createElement('span');
        dot.className = 'graph-legend-dot';
        dot.style.background = GROUP_COLORS[group] || GROUP_COLORS.other;
        var label = document.createElement('span');
        label.className = 'graph-legend-label';
        label.textContent = group;
        var count = document.createElement('span');
        count.className = 'graph-legend-count';
        count.textContent = String(typeCounts[group]);
        chip.appendChild(dot); chip.appendChild(label); chip.appendChild(count);
        chip.addEventListener('click', function(){
          if (hiddenGroups.has(group)) hiddenGroups.delete(group);
          else hiddenGroups.add(group);
          chip.classList.toggle('is-off', hiddenGroups.has(group));
          if (Graph) refreshVisibility();
        });
        legendEl.appendChild(chip);
      });
    }

    var highlightNodes = new Set();
    var highlightLinks = new Set();
    var hoverNode = null;
    var hoverLink = null;
    var pinnedNode = null;
    var pinnedLink = null;
    var Graph = null;
    var THREE = null;
    var fallbackSvg = null;
    var fallbackPositions = {};
    var mode = '3d';
    var searchQuery = '';
    var dayFilter = null;
    // Auto-fit fires exactly once, on the first onEngineStop. Re-fit only
    // happens when the user presses ``f`` or clicks the Fit button (those
    // call fitAll() directly). Any subsequent onEngineStop — which fires
    // on every hover / drag / resize as the simulation re-cools — must be
    // ignored, otherwise the camera flies around uninvited.
    var hasInitialFit = false;

    function shortLabel(value, limit){
      var s = String(value || '');
      limit = limit || 24;
      return s.length <= limit ? s : s.slice(0, limit - 1) + '…';
    }

    function nodeLabelText(n){
      return shortLabel(n && (n.name || n.id), 24);
    }

    function edgeLabelText(l){
      return shortLabel(l && (l.label || l.type), 18);
    }

    function nodeAccent(n){
      return (n && n.color) || GROUP_COLORS[(n && n.group) || 'other'] || GROUP_COLORS.other;
    }

    function clearInfoPanel(){
      if (!infoPanel) return;
      while (infoPanel.firstChild) infoPanel.removeChild(infoPanel.firstChild);
      infoPanel.classList.remove('is-visible');
    }

    function appendInfoLink(label, href){
      if (!infoPanel || !href) return;
      var a = document.createElement('a');
      a.className = 'graph-info-link';
      a.href = href;
      a.textContent = label;
      infoPanel.appendChild(a);
    }

    function showInfoPanel(node){
      if (!infoPanel) return;
      while (infoPanel.firstChild) infoPanel.removeChild(infoPanel.firstChild);
      if (!node) { infoPanel.classList.remove('is-visible'); return; }
      var h = document.createElement('h3');
      h.className = 'graph-info-title';
      h.textContent = node.name || node.id || '';
      infoPanel.appendChild(h);
      var meta = document.createElement('p');
      meta.className = 'graph-info-meta';
      var t = document.createElement('span');
      t.className = 'graph-info-badge';
      t.style.background = nodeAccent(node);
      t.textContent = node.group || node.kind || '';
      meta.appendChild(t);
      var typeSpan = document.createElement('span');
      typeSpan.textContent = ' ' + (node.type || '');
      meta.appendChild(typeSpan);
      var degSpan = document.createElement('span');
      degSpan.className = 'graph-info-degree';
      degSpan.textContent = ' · degree ' + (node.degree || 0);
      meta.appendChild(degSpan);
      infoPanel.appendChild(meta);
      if (node.description) {
        var desc = document.createElement('p');
        desc.className = 'graph-info-desc';
        var text = String(node.description);
        desc.textContent = text.length > 200 ? text.slice(0, 197) + '…' : text;
        infoPanel.appendChild(desc);
      }
      if (node.href) {
        appendInfoLink('Open page →', node.href);
      }
      infoPanel.classList.add('is-visible');
    }

    function showLinkInfoPanel(link){
      if (!infoPanel || !link) return;
      while (infoPanel.firstChild) infoPanel.removeChild(infoPanel.firstChild);
      var endpoints = linkEndpoints(link);
      var source = endpoints.source;
      var target = endpoints.target;
      var label = link.label || link.type || 'related';
      var h = document.createElement('h3');
      h.className = 'graph-info-title';
      h.textContent = label;
      infoPanel.appendChild(h);
      var meta = document.createElement('p');
      meta.className = 'graph-info-meta';
      meta.textContent = ((source && source.name) || 'source') + ' → ' + ((target && target.name) || 'target');
      infoPanel.appendChild(meta);
      var hint = document.createElement('p');
      hint.className = 'graph-info-desc';
      hint.textContent = 'Tap again to open the target page. Use source/target links below for exact navigation.';
      infoPanel.appendChild(hint);
      appendInfoLink('Open target →', target && target.href);
      appendInfoLink('Open source →', source && source.href);
      infoPanel.classList.add('is-visible');
    }

    function showTooltip(text, x, y){
      if (!tooltip) return;
      tooltip.textContent = text;
      tooltip.style.left = (x + 12) + 'px';
      tooltip.style.top  = (y + 12) + 'px';
      tooltip.classList.add('is-visible');
    }
    function hideTooltip(){ if (tooltip) tooltip.classList.remove('is-visible'); }

    function applyHighlight(node){
      highlightNodes.clear();
      highlightLinks.clear();
      if (node) {
        highlightNodes.add(node);
        node.neighbors.forEach(function(nb){ highlightNodes.add(nb); });
        node.edges.forEach(function(e){ highlightLinks.add(e); });
      }
      refreshHighlightStyles();
    }

    function applyLinkHighlight(link){
      highlightNodes.clear();
      highlightLinks.clear();
      if (link) {
        var endpoints = linkEndpoints(link);
        if (endpoints.source) highlightNodes.add(endpoints.source);
        if (endpoints.target) highlightNodes.add(endpoints.target);
        highlightLinks.add(link);
      }
      refreshHighlightStyles();
    }

    function hasFocusFilter(){
      return highlightNodes.size > 0 || highlightLinks.size > 0;
    }

    function isDimmedNode(node){
      return hasFocusFilter() && !highlightNodes.has(node);
    }

    function isDimmedLink(link){
      return hasFocusFilter() && !highlightLinks.has(link);
    }

    function refreshHighlightStyles(){
      if (Graph && Graph.refresh) {
        try { Graph.refresh(); } catch (_) {}
      }
      if (Graph && Graph.nodeColor) {
        try { Graph.nodeColor(Graph.nodeColor()); } catch (_) {}
      }
      if (Graph && Graph.linkColor) {
        try { Graph.linkColor(Graph.linkColor()); } catch (_) {}
      }
      refreshFallbackFocusStyles();
    }

    function nodeIdOf(node){
      return node && (node.id || String(node));
    }

    function linkEndpointId(value){
      return value && typeof value === 'object' ? value.id : value;
    }

    function linkEndpoints(link){
      return {
        source: byId.get(linkEndpointId(link && link.source)),
        target: byId.get(linkEndpointId(link && link.target))
      };
    }

    function linkKey(link){
      if (!link) return '';
      return [linkEndpointId(link.source), linkEndpointId(link.target), link.type || link.label || ''].join('→');
    }

    function isVisible(node){
      if (!node) return false;
      var g = node.group || 'other';
      if (hiddenGroups.has(g)) return false;
      if (searchQuery && !(node.name || '').toLowerCase().includes(searchQuery)) return false;
      if (dayFilter) {
        var created = node.metadata && node.metadata.created;
        if (!created || String(created).slice(0, 10) !== dayFilter) return false;
      }
      return true;
    }

    function refreshVisibility(){
      if (!Graph) return;
      try { Graph.nodeVisibility(function(n){ return isVisible(n); }); } catch (_) {}
      try { Graph.linkVisibility(function(l){
        var s = typeof l.source === 'object' ? l.source : byId.get(l.source);
        var t = typeof l.target === 'object' ? l.target : byId.get(l.target);
        return isVisible(s) && isVisible(t);
      }); } catch (_) {}
    }

    // ---- 2D fallback (SVG) for when force-graph never loads --------------
    function renderFallback(message){
      if (banner) {
        banner.textContent = message || 'Interactive 3D renderer unavailable — showing static fallback.';
        banner.classList.add('is-visible');
      }
      var NS = 'http://www.w3.org/2000/svg';
      while (container.firstChild) container.removeChild(container.firstChild);
      var svg = document.createElementNS(NS, 'svg');
      var w = container.clientWidth || 800;
      var h = container.clientHeight || 480;
      svg.setAttribute('viewBox', '0 0 ' + w + ' ' + h);
      svg.setAttribute('width', '100%');
      svg.setAttribute('height', '100%');
      svg.setAttribute('role', 'img');
      svg.setAttribute('aria-label', 'Knowledge graph (static fallback)');
      var cx = w / 2, cy = h / 2, r = Math.min(w, h) * 0.42;
      var positions = {};
      var visible = payload.nodes.filter(isVisible);
      visible.forEach(function(n, i){
        var angle = (i / Math.max(visible.length, 1)) * Math.PI * 2;
        positions[n.id] = { x: cx + r * Math.cos(angle), y: cy + r * Math.sin(angle) };
      });
      fallbackSvg = svg;
      fallbackPositions = positions;
      payload.links.forEach(function(e){
        var sId = typeof e.source === 'object' ? e.source.id : e.source;
        var tId = typeof e.target === 'object' ? e.target.id : e.target;
        var a = positions[sId]; var b = positions[tId];
        if (!a || !b) return;
        var line = document.createElementNS(NS, 'line');
        line.setAttribute('x1', a.x); line.setAttribute('y1', a.y);
        line.setAttribute('x2', b.x); line.setAttribute('y2', b.y);
        line.setAttribute('stroke', EDGE_COLOR_LIGHT);
        line.setAttribute('stroke-width', '0.24');
        line.setAttribute('tabindex', '0');
        line.setAttribute('data-link-key', linkKey(e));
        line.style.cursor = 'pointer';
        line.addEventListener('click', function(evt){ evt.preventDefault(); activateLink(e, evt); });
        svg.appendChild(line);
      });
      visible.forEach(function(n){
        var p = positions[n.id]; if (!p) return;
        var circle = document.createElementNS(NS, 'circle');
        circle.setAttribute('cx', p.x); circle.setAttribute('cy', p.y);
        circle.setAttribute('r', String(3 + Math.min(8, Math.sqrt(n.val || 1))));
        circle.setAttribute('fill', n.color);
        circle.setAttribute('tabindex', '0');
        circle.setAttribute('role', 'button');
        circle.setAttribute('data-node-id', n.id);
        circle.style.cursor = 'pointer';
        var title = document.createElementNS(NS, 'title');
        title.textContent = (n.name || '') + ' — ' + (n.type || '') + '. Tap once to zoom, again to open.';
        circle.appendChild(title);
        circle.addEventListener('click', function(evt){ evt.preventDefault(); activateNode(n, evt); });
        circle.addEventListener('keydown', function(evt){ if (evt.key === 'Enter' || evt.key === ' ') { evt.preventDefault(); activateNode(n, evt); } });
        svg.appendChild(circle);
        var showAlways = shouldShowOverviewLabel(n);
        if (showAlways) {
          var text = document.createElementNS(NS, 'text');
          text.setAttribute('x', p.x);
          text.setAttribute('y', p.y + 18);
          text.setAttribute('text-anchor', 'middle');
          text.setAttribute('data-node-label-id', n.id);
          text.setAttribute('fill', nodeAccent(n));
          text.setAttribute('font-size', '11');
          text.setAttribute('font-family', 'Inter, system-ui, sans-serif');
          text.setAttribute('paint-order', 'stroke');
          text.setAttribute('stroke', 'rgba(4,7,15,0.34)');
          text.setAttribute('stroke-width', '2');
          text.textContent = nodeLabelText(n);
          svg.appendChild(text);
        }
      });
      container.appendChild(svg);
    }

    function refreshFallbackFocusStyles(){
      if (!fallbackSvg) return;
      fallbackSvg.querySelectorAll('circle[data-node-id]').forEach(function(el){
        var node = byId.get(el.getAttribute('data-node-id'));
        var dim = node && isDimmedNode(node);
        el.setAttribute('opacity', dim ? '0.06' : '0.95');
        el.style.pointerEvents = dim ? 'none' : 'auto';
      });
      fallbackSvg.querySelectorAll('text[data-node-label-id]').forEach(function(el){
        var node = byId.get(el.getAttribute('data-node-label-id'));
        var dim = node && isDimmedNode(node);
        var hot = node && highlightNodes.has(node);
        el.setAttribute('opacity', dim ? '0' : (hot ? '1' : '0.72'));
      });
      fallbackSvg.querySelectorAll('line[data-link-key]').forEach(function(el){
        var key = el.getAttribute('data-link-key') || '';
        var hot = false;
        highlightLinks.forEach(function(link){ if (linkKey(link) === key) hot = true; });
        var dim = hasFocusFilter() && !hot;
        el.setAttribute('opacity', dim ? '0.02' : (hot ? '1' : '0.82'));
        el.setAttribute('stroke-width', hot ? '0.85' : '0.28');
        el.style.pointerEvents = dim ? 'none' : 'auto';
      });
    }

    // ---- Sprite label cache ---------------------------------------------
    var spriteCache = new Map();
    function makeSpriteLabel(text, color){
      if (!THREE) return null;
      var key = text + '|' + color;
      if (spriteCache.has(key)) return spriteCache.get(key).clone();
      var canvas = document.createElement('canvas');
      var fontSize = 32;
      var pad = 12;
      var ctx = canvas.getContext('2d');
      ctx.font = '650 ' + fontSize + 'px "Inter", system-ui, sans-serif';
      var metrics = ctx.measureText(text);
      var w = Math.ceil(metrics.width) + pad * 2;
      var h = fontSize + pad * 2;
      canvas.width = w;
      canvas.height = h;
      ctx = canvas.getContext('2d');
      ctx.font = '500 ' + fontSize + 'px "Inter", system-ui, sans-serif';
      ctx.fillStyle = 'rgba(2,6,23,0.26)';
      ctx.fillRect(0, 0, w, h);
      ctx.fillStyle = color;
      ctx.textBaseline = 'middle';
      ctx.fillText(text, pad, h / 2);
      var tex = new THREE.CanvasTexture(canvas);
      tex.minFilter = THREE.LinearFilter;
      var mat = new THREE.SpriteMaterial({
        map: tex,
        transparent: true,
        depthWrite: false,
        depthTest: false,
        opacity: 0.74
      });
      var sprite = new THREE.Sprite(mat);
      var scale = 0.18;
      sprite.scale.set(w * scale, h * scale, 1);
      sprite.renderOrder = 999;
      sprite.userData.isLabel = true;
      spriteCache.set(key, sprite);
      var clone = sprite.clone();
      clone.renderOrder = 999;
      return clone;
    }

    function cameraDistanceOpacity(x, y, z){
      if (!Graph || !Graph.camera) return 0.74;
      var camera = Graph.camera && Graph.camera();
      if (!camera || !camera.position) return 0.74;
      var dx = (camera.position.x || 0) - (x || 0);
      var dy = (camera.position.y || 0) - (y || 0);
      var dz = (camera.position.z || 0) - (z || 0);
      var d = Math.hypot(dx, dy, dz);
      if (d < 120) return 0.26;
      if (d < 220) return 0.42;
      if (d < 360) return 0.58;
      return 0.74;
    }

    function applySpriteOpacity(sprite, opacity){
      if (!sprite || !sprite.material) return;
      sprite.material.opacity = opacity;
      sprite.material.transparent = true;
    }

    // ---- Smooth orbit/pan: enable damping on the OrbitControls so drag
    //      and library-native wheel zoom feel polished. We deliberately do
    //      NOT install a custom wheel listener — 3d-force-graph's built-in
    //      zoom owns the wheel event so we don't fight it.
    function installControlsDamping(inst){
      var controls = inst && inst.controls && inst.controls();
      if (!controls) return;
      try {
        controls.enableDamping = true;
        controls.dampingFactor = 0.08;
      } catch (_) {}
      try {
        // Re-enable the library's own zoom interaction so it owns the
        // wheel. (No-op on 2D mode; the 2D renderer has its own zoom.)
        if (inst.enableZoomInteraction) inst.enableZoomInteraction(true);
      } catch (_) {}
      // Cursor-anchored target: when the user moves the mouse over the
      // canvas, point ``controls.target`` at the cursor world coordinate
      // (via raycaster on the controls.target plane). Library zoom then
      // moves toward that target naturally. Debounced so we don't churn
      // the raycaster every frame.
      if (!THREE) return;
      var renderer = inst.renderer && inst.renderer();
      var camera = inst.camera && inst.camera();
      if (!renderer || !camera) return;
      var dom = renderer.domElement;
      if (!dom) return;
      var raycaster = new THREE.Raycaster();
      var mouseNDC = new THREE.Vector2();
      var plane = new THREE.Plane();
      var intersect = new THREE.Vector3();
      var camDir = new THREE.Vector3();
      var pending = null;
      dom.addEventListener('pointermove', function(e){
        if (mode !== '3d') return;
        if (pending) return;  // debounce to next animation frame
        pending = window.requestAnimationFrame(function(){
          pending = null;
          var rect = dom.getBoundingClientRect();
          mouseNDC.x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
          mouseNDC.y = -((e.clientY - rect.top) / rect.height) * 2 + 1;
          camera.getWorldDirection(camDir);
          plane.setFromNormalAndCoplanarPoint(camDir, controls.target);
          raycaster.setFromCamera(mouseNDC, camera);
          // We compute the intersect (lets the library aim its zoom toward
          // it) but never mutate camera.position or controls.target — that
          // is what fights the library's internal zoom. Computing the
          // intersect is enough to keep ``Raycaster``/``setFromCamera``
          // wired so future cursor-anchored hooks (hover labels, picking)
          // can read ``intersect`` without re-instantiating the raycaster.
          if (raycaster.ray && raycaster.ray.intersectPlane) {
            try { raycaster.ray.intersectPlane(plane, intersect); } catch (_) {}
          }
        });
      }, { passive: true });
    }

    // ---- Fit-to-view via bounding sphere over current node positions ----
    function fitAll(durationMs){
      if (!Graph) return;
      var duration = reduceMotion ? 0 : (durationMs || 600);
      if (mode === '2d') {
        try { Graph.zoomToFit(duration, 55); } catch (_) {}
        return;
      }
      if (!THREE) {
        try { Graph.zoomToFit(duration, 55); } catch (_) {}
        return;
      }
      var visible = payload.nodes.filter(function(n){
        return isVisible(n) && typeof n.x === 'number' && typeof n.y === 'number';
      });
      if (!visible.length) return;
      var box = new THREE.Box3();
      visible.forEach(function(n){
        box.expandByPoint(new THREE.Vector3(n.x || 0, n.y || 0, n.z || 0));
      });
      var sphere = new THREE.Sphere();
      box.getBoundingSphere(sphere);
      var camera = Graph.camera && Graph.camera();
      var controls = Graph.controls && Graph.controls();
      if (!camera) {
        try { Graph.zoomToFit(duration, 55); } catch (_) {}
        return;
      }
      var fov = (camera.fov || 50) * Math.PI / 180;
      var aspect = (container.clientWidth || 1) / Math.max(1, container.clientHeight || 1);
      var fitHeightDistance = sphere.radius / Math.sin(fov / 2);
      var fitWidthDistance = fitHeightDistance / Math.max(0.45, aspect);
      var distance = Math.max(fitHeightDistance, fitWidthDistance, 160) * 1.04;
      var center = sphere.center;
      try {
        if (controls && controls.target && controls.target.set) {
          controls.target.set(center.x, center.y, center.z);
          if (controls.update) controls.update();
        }
        Graph.cameraPosition(
          { x: center.x, y: center.y, z: center.z + distance },
          { x: center.x, y: center.y, z: center.z },
          duration
        );
      } catch (_) {
        try { Graph.zoomToFit(duration, 90); } catch (__) {}
      }
    }

    function scheduleCenteredFit(){
      // Single-shot fit. The first call after the engine settles fits the
      // camera once; later calls are no-ops. We keep the function (rather
      // than inlining) so the manual Fit button + ``f`` shortcut don't
      // need to know about the flag — they call fitAll() directly.
      if (hasInitialFit || pinnedNode || pinnedLink) return;
      hasInitialFit = true;
      try { fitAll(800); } catch (_) {}
    }

    function sizeGraphToContainer(inst){
      if (!inst || !container) return;
      var w = Math.max(320, Math.floor(container.clientWidth || container.getBoundingClientRect().width || 800));
      var h = Math.max(360, Math.floor(container.clientHeight || container.getBoundingClientRect().height || 520));
      try { if (inst.width) inst.width(w); } catch (_) {}
      try { if (inst.height) inst.height(h); } catch (_) {}
      var canvas = container.querySelector('canvas');
      if (canvas) {
        canvas.style.width = '100%';
        canvas.style.height = '100%';
      }
    }

    function installGraphResize(inst){
      if (!inst || !window) return;
      var pending = null;
      // Debounced resize: re-size the canvas to its container only.
      // We deliberately do NOT auto-fit on resize — that's what made the
      // graph view "auto-zoom-out repeatedly" without user input. The
      // user can press ``f`` (or click Fit) to re-fit on demand.
      window.addEventListener('resize', function(){
        if (pending) window.clearTimeout(pending);
        pending = window.setTimeout(function(){
          sizeGraphToContainer(inst);
        }, 120);
      });
    }

    // ---- Build the renderer ---------------------------------------------
    function buildGraph(initialMode){
      mode = initialMode || '3d';
      while (container.firstChild) container.removeChild(container.firstChild);
      var ctor = (mode === '2d') ? window.ForceGraph : window.ForceGraph3D;
      if (!ctor) { renderFallback('Renderer constructor missing.'); return; }

      var inst = ctor()(container)
        .graphData({ nodes: payload.nodes, links: payload.links })
        .backgroundColor('rgba(0,0,0,0)')
        .nodeId('id')
        .nodeLabel(function(n){ return ''; })
        .nodeVal(function(n){ return Math.max(1, n.val || 1); })
        .nodeColor(function(n){
          if (isDimmedNode(n)) return 'rgba(120,116,108,0.035)';
          return n.color;
        })
        .linkColor(function(l){
          if (!hasFocusFilter()) return EDGE_COLOR_LIGHT;
          return highlightLinks.has(l) ? EDGE_COLOR_HOT : EDGE_COLOR_DIM;
        })
        .linkWidth(function(l){ return isDimmedLink(l) ? 0.001 : (highlightLinks.has(l) ? 0.85 : 0.22); })
        .linkHoverPrecision(8)
        .linkDirectionalParticles(function(l){ return highlightLinks.has(l) ? 2 : 0; })
        .linkDirectionalParticleWidth(0.9)
        .onNodeHover(function(node){
          hoverNode = node || null;
          container.style.cursor = node && !isDimmedNode(node) ? 'pointer' : 'default';
          if (!pinnedNode && !pinnedLink) {
            applyHighlight(node);
            showInfoPanel(node);
          }
        })
        .onLinkHover(function(link){
          hoverLink = link || null;
          if (!link) { hideTooltip(); return; }
          var s = typeof link.source === 'object' ? link.source : byId.get(link.source);
          var t = typeof link.target === 'object' ? link.target : byId.get(link.target);
          var sName = (s && s.name) || '';
          var tName = (t && t.name) || '';
          var label = link.label || link.type || 'related';
          showTooltip(sName + ' → ' + label + ' → ' + tName, lastMouseX, lastMouseY);
        })
        .onNodeClick(function(node, evt){
          activateNode(node, evt);
        })
        .onLinkClick(function(link, evt){
          activateLink(link, evt);
        })
        .onBackgroundClick(function(){
          pinnedNode = null;
          pinnedLink = null;
          applyHighlight(null);
          clearInfoPanel();
        });

      try { if (inst.nodeOpacity) inst.nodeOpacity(0.95); } catch (_) {}
      try { if (inst.linkOpacity) inst.linkOpacity(0.8); } catch (_) {}
      try { if (inst.linkDirectionalParticleColor) inst.linkDirectionalParticleColor(function(l){ return highlightLinks.has(l) ? EDGE_COLOR_HOT : EDGE_COLOR_LIGHT; }); } catch (_) {}
      try {
        if (mode === '3d' && inst.linkResolution) inst.linkResolution(6);
      } catch (_) {}

      // Mode-specific labels.
      if (mode === '3d' && THREE) {
        try {
          inst.nodeThreeObject(function(n){
            var showAlways = shouldShowOverviewLabel(n);
            var isHover = (hoverNode === n) || highlightNodes.has(n);
            if (isDimmedNode(n)) return null;
            if (!showAlways && !isHover) return null;
            var sprite = makeSpriteLabel(nodeLabelText(n), nodeAccent(n));
            if (sprite) {
              sprite.position.set(0, 6 + Math.sqrt(n.val || 1), 0);
              applySpriteOpacity(sprite, 0.74);
            }
            return sprite;
          });
          if (inst.nodeThreeObjectExtend) inst.nodeThreeObjectExtend(true);
          if (inst.nodePositionUpdate) {
            inst.nodePositionUpdate(function(sprite, coords, node){
              if (!sprite || !coords) return false;
              sprite.position.set(coords.x || 0, (coords.y || 0) + 6 + Math.sqrt((node && node.val) || 1), coords.z || 0);
              applySpriteOpacity(sprite, cameraDistanceOpacity(coords.x, coords.y, coords.z));
              return true;
            });
          }
          inst.linkThreeObject(function(l){
            var label = edgeLabelText(l);
            if (!label) return null;
            var s = typeof l.source === 'object' ? l.source : byId.get(l.source);
            var t = typeof l.target === 'object' ? l.target : byId.get(l.target);
            var important = (hoverNode && (hoverNode === s || hoverNode === t)) || highlightLinks.has(l);
            if (isDimmedLink(l)) return null;
            if (!important) return null;
            return makeSpriteLabel(label, '#ece7dc');
          });
          if (inst.linkThreeObjectExtend) inst.linkThreeObjectExtend(true);
          if (inst.linkPositionUpdate) {
            inst.linkPositionUpdate(function(sprite, coords){
              if (!sprite || !coords) return false;
              var s = coords.start; var t = coords.end;
              if (!s || !t) return false;
              sprite.position.set((s.x + t.x) / 2, (s.y + t.y) / 2, (s.z + t.z) / 2);
              applySpriteOpacity(sprite, cameraDistanceOpacity((s.x + t.x) / 2, (s.y + t.y) / 2, (s.z + t.z) / 2));
              return true;
            });
          }
        } catch (err) {
          console.warn('graph: 3D labels failed', err);
        }
      }
      if (mode === '2d') {
        try {
          inst.nodeCanvasObjectMode(function(){ return 'after'; });
          inst.nodeCanvasObject(function(n, ctx, globalScale){
            var showAlways = shouldShowOverviewLabel(n);
            var isHover = (hoverNode === n) || highlightNodes.has(n);
            if (isDimmedNode(n)) return;
            if (!showAlways && !isHover) return;
            var label = nodeLabelText(n);
            var fontSize = (highlightNodes.has(n) ? 14 : 12) / globalScale;
            ctx.font = '650 ' + fontSize + 'px Inter, system-ui, sans-serif';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'top';
            ctx.lineWidth = 2.5 / globalScale;
            ctx.strokeStyle = 'rgba(2,6,23,0.38)';
            ctx.strokeText(label, n.x, n.y + 7);
            ctx.fillStyle = nodeAccent(n);
            ctx.fillText(label, n.x, n.y + 7);
          });
          inst.linkCanvasObjectMode(function(){ return 'after'; });
          inst.linkCanvasObject(function(l, ctx, globalScale){
            var label = edgeLabelText(l);
            if (!label) return;
            var s = typeof l.source === 'object' ? l.source : byId.get(l.source);
            var t = typeof l.target === 'object' ? l.target : byId.get(l.target);
            if (!s || !t) return;
            var important = (hoverNode && (hoverNode === s || hoverNode === t)) || highlightLinks.has(l);
            if (isDimmedLink(l)) return;
            if (!important) return;
            var fontSize = 11 / globalScale;
            ctx.font = '650 ' + fontSize + 'px Inter, system-ui, sans-serif';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.lineWidth = 2.5 / globalScale;
            ctx.strokeStyle = 'rgba(2,6,23,0.38)';
            ctx.strokeText(label, (s.x + t.x) / 2, (s.y + t.y) / 2);
            ctx.fillStyle = '#f8fafc';
            ctx.fillText(label, (s.x + t.x) / 2, (s.y + t.y) / 2);
          });
        } catch (err) {
          console.warn('graph: 2D labels failed', err);
        }
      }

      try {
        if (inst.d3Force) {
          var charge = inst.d3Force('charge'); if (charge && charge.strength) charge.strength(mode === '2d' ? -260 : -170);
          var link = inst.d3Force('link'); if (link && link.distance) link.distance(mode === '2d' ? 68 : 48);
        }
      } catch (_) {}
      try { inst.cooldownTicks(120); } catch (_) {}

      try {
        // Auto-fit fires exactly once: the first onEngineStop after the
        // simulation cools. Later onEngineStop events (re-cooling after
        // hover, drag, resize) skip via the hasInitialFit guard. Re-fits
        // are user-initiated only (``f``, Fit button).
        inst.onEngineStop(function(){
          if (hasInitialFit) return;
          if (pinnedNode || pinnedLink) return;
          scheduleCenteredFit();
        });
      } catch (_) {}

      Graph = inst;
      sizeGraphToContainer(inst);
      installGraphResize(inst);
      if (mode === '3d') {
        installControlsDamping(inst);
        // Start the camera at a known distance so the first frame isn't
        // a wild zoom-out from the origin. The single-shot scheduleCenteredFit
        // will refine the framing once the simulation settles.
        try {
          if (inst.cameraPosition) inst.cameraPosition({ x: 0, y: 0, z: 600 }, { x: 0, y: 0, z: 0 }, 0);
        } catch (_) {}
      }
      refreshVisibility();
      // Fallback fit (if the engine never stops, e.g. on tiny graphs that
      // skip the cool-down): scheduleCenteredFit is itself idempotent.
      if (!pinnedNode && !pinnedLink) setTimeout(scheduleCenteredFit, 350);
      return inst;
    }

    var lastMouseX = 0, lastMouseY = 0;
    container.addEventListener('mousemove', function(e){
      var rect = container.getBoundingClientRect();
      lastMouseX = e.clientX - rect.left;
      lastMouseY = e.clientY - rect.top;
      if (tooltip && tooltip.classList.contains('is-visible')) {
        tooltip.style.left = (lastMouseX + 12) + 'px';
        tooltip.style.top  = (lastMouseY + 12) + 'px';
      }
    });
    container.addEventListener('mouseleave', hideTooltip);

    function activateNode(node, evt){
      if (!node) return;
      if (isDimmedNode(node)) return;
      var samePinned = pinnedNode && nodeIdOf(pinnedNode) === nodeIdOf(node);
      // Graph browsing comes first: first tap/click pins, highlights neighbors,
      // and zooms to the entity. A second activation on the same pinned node
      // opens its detail page. Ctrl/⌘-click always behaves as "focus only".
      if (evt && (evt.metaKey || evt.ctrlKey)) samePinned = false;
      if (!samePinned) {
        pinnedNode = node;
        pinnedLink = null;
        applyHighlight(node);
        showInfoPanel(node);
        focusOnNode(node);
        return;
      }
      if (node.href) window.location.href = node.href;
    }

    function activateLink(link, evt){
      if (!link) return;
      if (isDimmedLink(link)) return;
      var samePinned = pinnedLink && linkKey(pinnedLink) === linkKey(link);
      if (evt && (evt.metaKey || evt.ctrlKey)) samePinned = false;
      if (!samePinned) {
        pinnedNode = null;
        pinnedLink = link;
        applyLinkHighlight(link);
        showLinkInfoPanel(link);
        focusOnLink(link);
        return;
      }
      var endpoints = linkEndpoints(link);
      var target = endpoints.target || endpoints.source;
      if (target && target.href) window.location.href = target.href;
    }

    function focusOnNode(node){
      if (!Graph) { focusFallbackNode(node); return; }
      if (mode === '3d' && Graph.cameraPosition && node && node.x !== undefined) {
        var distance = 300;
        var norm = Math.max(240, Math.hypot(node.x || 1, node.y || 1, node.z || 1));
        var distRatio = 1 + distance / norm;
        try {
          Graph.cameraPosition(
            { x: (node.x || 0) * distRatio, y: (node.y || 0) * distRatio, z: (node.z || 0) * distRatio },
            node,
            reduceMotion ? 0 : 600
          );
        } catch (_) {}
      } else if (mode === '2d' && Graph.centerAt && node) {
        try { Graph.centerAt(node.x || 0, node.y || 0, reduceMotion ? 0 : 600); Graph.zoom(1.8, reduceMotion ? 0 : 600); } catch (_) {}
      }
    }

    function focusFallbackNode(node){
      if (!fallbackSvg || !node) return;
      var p = fallbackPositions[node.id];
      if (!p) return;
      var box = 420;
      fallbackSvg.setAttribute('viewBox', (p.x - box / 2) + ' ' + (p.y - box / 2) + ' ' + box + ' ' + box);
    }

    function focusFallbackLink(link){
      if (!fallbackSvg || !link) return;
      var s = fallbackPositions[linkEndpointId(link.source)];
      var t = fallbackPositions[linkEndpointId(link.target)];
      if (!s && !t) return;
      if (!s || !t) {
        var onlyId = s ? linkEndpointId(link.source) : linkEndpointId(link.target);
        focusFallbackNode(byId.get(onlyId));
        return;
      }
      var minX = Math.min(s.x, t.x), maxX = Math.max(s.x, t.x);
      var minY = Math.min(s.y, t.y), maxY = Math.max(s.y, t.y);
      var pad = 80;
      fallbackSvg.setAttribute('viewBox', (minX - pad) + ' ' + (minY - pad) + ' ' + Math.max(180, maxX - minX + pad * 2) + ' ' + Math.max(180, maxY - minY + pad * 2));
    }

    function focusOnLink(link){
      if (!Graph) { focusFallbackLink(link); return; }
      if (!link) return;
      var endpoints = linkEndpoints(link);
      var source = endpoints.source;
      var target = endpoints.target;
      if (!source && !target) return;
      if (!source || !target) { focusOnNode(source || target); return; }
      var sx = source.x || 0, sy = source.y || 0, sz = source.z || 0;
      var tx = target.x || 0, ty = target.y || 0, tz = target.z || 0;
      var cx = (sx + tx) / 2, cy = (sy + ty) / 2, cz = (sz + tz) / 2;
      var span = Math.max(240, Math.hypot(sx - tx, sy - ty, sz - tz) * 3.2);
      if (mode === '3d' && Graph.cameraPosition) {
        try {
          Graph.cameraPosition(
            { x: cx, y: cy, z: cz + span },
            { x: cx, y: cy, z: cz },
            reduceMotion ? 0 : 600
          );
        } catch (_) {}
      } else if (mode === '2d' && Graph.centerAt) {
        try { Graph.centerAt(cx, cy, reduceMotion ? 0 : 600); Graph.zoom(1.5, reduceMotion ? 0 : 600); } catch (_) {}
      }
    }

    function setMode(next){
      if (next === mode) return;
      if (btn2D) btn2D.classList.toggle('is-active', next === '2d');
      if (btn3D) btn3D.classList.toggle('is-active', next === '3d');
      if (btn2D) btn2D.setAttribute('aria-pressed', String(next === '2d'));
      if (btn3D) btn3D.setAttribute('aria-pressed', String(next === '3d'));
      try {
        // A mode switch rebuilds the graph from scratch — reset the
        // single-shot fit flag so the new projection gets framed once.
        hasInitialFit = false;
        buildGraph(next);
      } catch (err) {
        console.error('graph: mode switch failed', err);
        if (banner) {
          banner.textContent = 'Graph mode switch failed: ' + (err && err.message ? err.message : err);
          banner.classList.add('is-visible');
        }
      }
    }

    if (btn2D) btn2D.addEventListener('click', function(){ setMode('2d'); });
    if (btn3D) btn3D.addEventListener('click', function(){ setMode('3d'); });
    if (btnFit) btnFit.addEventListener('click', function(){ fitAll(400); });
    if (btnReset) btnReset.addEventListener('click', function(){
      pinnedNode = null;
      pinnedLink = null;
      applyHighlight(null);
      clearInfoPanel();
      if (Graph && Graph.cameraPosition && mode === '3d') {
        try { Graph.cameraPosition({ x: 0, y: 0, z: 400 }, { x: 0, y: 0, z: 0 }, reduceMotion ? 0 : 600); } catch (_) {}
      } else if (Graph && Graph.centerAt) {
        try { Graph.centerAt(0, 0, reduceMotion ? 0 : 600); Graph.zoom(1, reduceMotion ? 0 : 600); } catch (_) {}
      }
    });
    if (searchEl) {
      searchEl.addEventListener('input', function(){
        searchQuery = (searchEl.value || '').trim().toLowerCase();
        refreshVisibility();
      });
      searchEl.addEventListener('keydown', function(e){
        if (e.key === 'Enter') {
          e.preventDefault();
          var match = payload.nodes.find(function(n){
            return (n.name || '').toLowerCase().includes(searchQuery);
          });
          if (match) {
            pinnedNode = match;
            applyHighlight(match);
            showInfoPanel(match);
            focusOnNode(match);
          }
        }
      });
    }

    // Day filter from timeline cells (additive feature; only kicks in when
    // a `[data-graph-filter-day]` button is present and clicked, or when
    // any element with `data-day-click="YYYY-MM-DD"` fires a click).
    document.addEventListener('click', function(e){
      var trigger = e.target && e.target.closest && e.target.closest('[data-graph-filter-day], [data-day-click]');
      if (!trigger) return;
      var day = trigger.getAttribute('data-graph-filter-day') || trigger.getAttribute('data-day-click');
      if (!day) return;
      dayFilter = (dayFilter === day) ? null : day;
      refreshVisibility();
    });

    document.addEventListener('keydown', function(e){
      var tag = (document.activeElement && document.activeElement.tagName) || '';
      var inField = tag === 'INPUT' || tag === 'TEXTAREA';
      if (e.key === '/' && !inField) { e.preventDefault(); searchEl && searchEl.focus(); return; }
      if (inField) return;
      if (e.key === 'f') { fitAll(400); }
      if (e.key === 'r') { if (btnReset) btnReset.click(); }
      if (e.key === '2') setMode('2d');
      if (e.key === '3') setMode('3d');
      if (e.key === 'Escape') {
        pinnedNode = null;
        pinnedLink = null;
        applyHighlight(null);
        clearInfoPanel();
        dayFilter = null;
        if (searchEl) { searchEl.value = ''; searchQuery = ''; }
        refreshVisibility();
      }
    });

    // ---- CDN load detection. Wait for window.ForceGraph(3D) to attach,
    //      then dynamically import three.js as a peer for sprites + raycast.
    var waited = 0;
    var interval = setInterval(function(){
      waited += 100;
      if (window.ForceGraph3D && window.ForceGraph) {
        clearInterval(interval);
        import(THREE_URL).then(function(mod){
          THREE = mod && (mod.default || mod);
          if (THREE && !THREE.Sprite && THREE.default) THREE = THREE.default;
        }).catch(function(err){
          console.warn('graph: three import failed', err);
        }).then(function(){
          try {
            buildGraph('3d');
            if (btn3D) btn3D.classList.add('is-active');
          } catch (err) {
            console.error('graph: init failed', err);
            renderFallback('Graph init failed: ' + (err && err.message ? err.message : err));
          }
        });
      } else if (waited > 6000) {
        clearInterval(interval);
        renderFallback('Could not load 3d-force-graph from the CDN. Showing static fallback.');
      }
    }, 100);
    }

    if (dataNode) {
      try {
        startGraph(JSON.parse(dataNode.textContent || '{}') || {});
      } catch (err) {
        startGraph({ nodes: [], links: [] });
      }
      return;
    }

    var payloadUrl = container.getAttribute('data-payload-url') || 'payload.json';
    fetch(payloadUrl)
      .then(function(r){
        if (!r.ok) throw new Error('HTTP ' + r.status + ' while loading ' + payloadUrl);
        return r.json();
      })
      .then(startGraph)
      .catch(function(err){
        console.error('graph: payload load failed', err);
        var banner = document.getElementById('graph-error-banner');
        if (banner) {
          banner.textContent = 'Graph payload failed to load: ' + (err && err.message ? err.message : err);
          banner.classList.add('is-visible');
        }
      });
  });
})();
"""


# ---------------------------------------------------------------------------
# Subtype chip filter (index pages)
# ---------------------------------------------------------------------------
#
# Renders nothing on its own — it picks up every ``[data-subtype-chips]`` strip
# the index renderer emits and toggles ``[data-type]`` rows in any sibling
# ``[data-filterable-table]`` element. The "All" chip carries an empty
# ``data-filter-type`` (always shows every row); other chips filter to their
# exact subtype. Click the active chip again to reset to "All".
#
# Pure DOM mutations, no fetch — the chips are pre-rendered server-side so the
# strip stays clickable even if JS fails to load.
JS_TOC_SCROLLSPY = r"""
(function(){
  // Pair every TOC item (rendered with ``data-toc-target="<anchor>"``)
  // with the matching heading (``<h2>`` / ``<h3>`` inside ``.article-body``)
  // so the currently-visible section's <li> picks up ``is-active`` while
  // the user scrolls. Falls back to a no-op when IntersectionObserver is
  // unsupported — clicks still work because the TOC anchors are real
  // ``href="#anchor"`` links.
  function init(){
    var tocItems = document.querySelectorAll('.toc li[data-toc-target]');
    if (!tocItems.length) return;
    var byAnchor = {};
    for (var i = 0; i < tocItems.length; i++) {
      var li = tocItems[i];
      var anchor = li.getAttribute('data-toc-target') || '';
      if (anchor) byAnchor[anchor] = li;
    }
    // Smooth scroll on anchor click.
    document.addEventListener('click', function(evt){
      var a = evt.target && evt.target.closest && evt.target.closest('.toc a[href^="#"]');
      if (!a) return;
      var href = a.getAttribute('href') || '';
      if (href.length < 2) return;
      var target = document.getElementById(href.slice(1));
      if (!target) return;
      evt.preventDefault();
      try {
        target.scrollIntoView({ behavior: 'smooth', block: 'start' });
      } catch (_) {
        target.scrollIntoView();
      }
      // Update the URL hash without triggering another scroll.
      try { history.replaceState(null, '', href); } catch (_) {}
    });
    if (typeof IntersectionObserver === 'undefined') return;
    var headings = document.querySelectorAll('.article-body h2[id], .article-body h3[id]');
    if (!headings.length) return;
    var activeAnchor = null;
    function setActive(anchor){
      if (anchor === activeAnchor) return;
      activeAnchor = anchor;
      for (var i = 0; i < tocItems.length; i++) {
        tocItems[i].classList.remove('is-active');
      }
      var li = anchor && byAnchor[anchor];
      if (li) li.classList.add('is-active');
    }
    // Track which headings are currently in the spy band.
    var visible = {};
    var io = new IntersectionObserver(function(entries){
      for (var i = 0; i < entries.length; i++) {
        var ent = entries[i];
        var id = ent.target.id;
        if (ent.isIntersecting) visible[id] = ent.target;
        else delete visible[id];
      }
      // Pick the visible heading closest to the top of the viewport.
      var best = null;
      var bestTop = Infinity;
      for (var id in visible) {
        if (!Object.prototype.hasOwnProperty.call(visible, id)) continue;
        var rect = visible[id].getBoundingClientRect();
        if (rect.top < bestTop) { bestTop = rect.top; best = id; }
      }
      if (best) setActive(best);
    }, {
      rootMargin: '-20% 0px -70% 0px',
      threshold: 0
    });
    for (var j = 0; j < headings.length; j++) io.observe(headings[j]);
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
"""


JS_SUBTYPE_FILTER = r"""
(function(){
  function activate(strip, value){
    var chips = strip.querySelectorAll('.subtype-chip');
    for (var i=0; i<chips.length; i++){
      var chip = chips[i];
      var match = (chip.getAttribute('data-filter-type') || '') === value;
      chip.classList.toggle('is-active', match);
      chip.setAttribute('aria-pressed', match ? 'true' : 'false');
    }
    var scope = strip.parentNode || document;
    var tables = scope.querySelectorAll('[data-filterable-table]');
    for (var t=0; t<tables.length; t++){
      var rows = tables[t].querySelectorAll('tbody > tr');
      for (var r=0; r<rows.length; r++){
        var row = rows[r];
        var rowType = row.getAttribute('data-type') || '';
        var visible = !value || rowType === value;
        row.style.display = visible ? '' : 'none';
      }
    }
  }
  function bind(strip){
    if (strip.__chipsBound) return;
    strip.__chipsBound = true;
    strip.addEventListener('click', function(evt){
      var chip = evt.target.closest && evt.target.closest('.subtype-chip');
      if (!chip || !strip.contains(chip)) return;
      var value = chip.getAttribute('data-filter-type') || '';
      // Click the active non-All chip again to reset.
      if (chip.classList.contains('is-active') && value){
        activate(strip, '');
        return;
      }
      activate(strip, value);
    });
  }
  function init(){
    var strips = document.querySelectorAll('[data-subtype-chips]');
    for (var i=0; i<strips.length; i++) bind(strips[i]);
  }
  if (document.readyState === 'loading'){
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
"""


# ``JS_BUNDLE_BASE`` is what every page loads (theme toggle, rail/TOC drawer,
# search palette, subtype chip filter). ``JS_BUNDLE_GRAPH`` is the heavier
# graph renderer that we only ship on the graph route — see
# ``llm_wiki.site.__init__`` (writes both ``assets/app.js`` and
# ``assets/graph.js``) and ``render_graph_view`` in ``pages.py`` (injects the
# second ``<script defer>`` only on the graph page).
JS_BUNDLE_BASE = (
    JS_THEME_TOGGLE
    + "\n" + JS_RAIL_DRAWER
    + "\n" + JS_SEARCH_PALETTE
    + "\n" + JS_SUBTYPE_FILTER
    + "\n" + JS_TOC_SCROLLSPY
)

JS_BUNDLE_GRAPH = JS_GRAPH

# Back-compat alias: tests and any older callers can still import ``JS_BUNDLE``
# and get the union (so ``"data-toggle-theme" in JS_BUNDLE`` etc. still work).
JS_BUNDLE = (
    JS_BUNDLE_BASE
    + "\n" + JS_BUNDLE_GRAPH
)


__all__ = [
    "JS_THEME_TOGGLE",
    "JS_RAIL_DRAWER",
    "JS_SEARCH_PALETTE",
    "JS_SUBTYPE_FILTER",
    "JS_TOC_SCROLLSPY",
    "JS_GRAPH",
    "JS_BUNDLE",
    "JS_BUNDLE_BASE",
    "JS_BUNDLE_GRAPH",
]
