"""Client-side JavaScript bundles for the Tesserae static site.

Module-level string constants compose into the bundle that gets written to
``assets/app.js`` by the StaticSiteBuilder:

- :data:`JS_THEME_TOGGLE` — Reads the persisted theme from ``localStorage``
  (``tesserae-theme``), falls back to ``prefers-color-scheme``, and toggles
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
  peer dep). Library-default OrbitControls zoom (Issue 2 — three rounds
  of bespoke cursor-anchored zoom were reverted because every variant
  stuttered or inverted direction; the library zoom feels right).
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
  var KEY = 'tesserae-theme';
  var EXPLICIT_KEY = 'tesserae-theme-explicit';

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
    // Issue 1 — graph view registers ``window.__graphRefreshLabels`` so
    // its label sprites can be re-tinted when the user toggles theme.
    // Pure no-op on every other route.
    try {
      if (typeof window.__graphRefreshLabels === 'function') {
        window.__graphRefreshLabels();
      }
    } catch (_) {}
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
  var RECENTS_KEY = 'tesserae-recents';
  var TAB_KEY = 'tesserae-search-tab';
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
  // tesserae.site.search so server-built tokens and client queries agree.
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
    var closer = e.target && e.target.closest && e.target.closest('[data-close-search]');
    if (closer) { e.preventDefault(); closePalette(); return; }
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
  // ----------------------------------------------------------------
  // Interaction state machine (read this first when touching graph.js)
  //   focusedNode      — the currently selected sphere (or null). Set
  //                      by activateNode / focusOnNode; drives the
  //                      auto-orbit hook, the focused-label sprite, and
  //                      the floating ``#graph-focus-panel`` overlay.
  //   pinnedNode       — sticky highlight set by a click on a node when
  //                      the user is NOT focusing (Esc / contextmenu /
  //                      Reset all clear it).
  //   pinnedLink       — same as pinnedNode but for a clicked edge.
  //   hoverNode        — the node currently under the mouse (or last
  //                      touched on a touch device). Drives the cursor
  //                      tooltip and 1-hop highlight.
  //   userInteracted   — flips true on first user gesture (click, drag,
  //                      scroll, search, focus shortcut). The rest-
  //                      payload merge skips its post-merge auto-fit
  //                      when this is set so a click before the rest
  //                      payload arrives is not stolen back.
  //   orbitTarget      — ``{x,y,z}`` cluster centroid the auto-orbit
  //                      hook orbits around AND the camera looks at.
  //                      One target shared between the focus fly-to
  //                      and the per-tick orbit so they don't fight.
  // ----------------------------------------------------------------
  // Graph view is dark-only by design (matches HypePaper's CitationGraph).
  // Even when the site theme toggle flips to "light", the canvas, labels,
  // edges, and overlays all stay on the dark palette. The wiki chrome
  // (sidebar, header, body) keeps responding to the toggle — only the
  // graph widget is theme-locked. Background hex is HypePaper's #060A14.
  var GRAPH_FORCE_DARK = true;
  var GRAPH_BG_COLOR = '#060A14';
  // Palette ported from HypePaper's CitationGraph.vue category dots:
  // purple-500 / blue-500 / cyan-400 / amber-400 / emerald-400 / pink-400
  // / gray-400 / gray-500. Concepts are the "seed" purple (matches the
  // sprite text color #e9d5ff used over a #a855f7 sphere).
  var GROUP_COLORS = {
    sources:   '#9ca3af',
    papers:    '#3b82f6',
    repos:     '#22d3ee',
    concepts:  '#a855f7',
    entities:  '#a855f7',
    topics:    '#f472b6',
    syntheses: '#fbbf24',
    questions: '#34d399',
    // B2 — cross-project bridge nodes. Violet-400 (HypePaper-adjacent
    // accent) so they read as "outside this vault" at a glance without
    // clashing with the concept/entity purple.
    external:  '#a78bfa',
    other:     '#6b7280'
  };
  // Graph View v1 — 8-family colour scheme (spec §B). Nodes are coloured by
  // their precomputed ``family`` (taxonomy/sources/code/concepts/claims/
  // synthesis/sessions/actors) instead of one of 36 raw types. Cross-project
  // bridges keep their distinct violet. Any family not listed → neutral gray.
  // Tailwind-500/400 anchors, matched to HypePaper's category-dot palette.
  var FAMILY_COLORS = {
    taxonomy:  '#f472b6', // pink-400
    sources:   '#3b82f6', // blue-500
    code:      '#22d3ee', // cyan-400
    concepts:  '#8b5cf6', // violet-500
    claims:    '#f59e0b', // amber-500
    synthesis: '#10b981', // emerald-500
    sessions:  '#fb7185', // rose-400
    actors:    '#94a3b8', // slate-400
    external:  '#a78bfa', // bridge violet (B2)
    other:     '#6b7280'  // neutral gray fallback
  };
  // HSL anchors per family — drive the per-node lightness/sat wobble +
  // importance tier in ``nodeColorVariant`` (spec §B "brighten focused /
  // selected / CommunitySummary / high-importance; desaturate low-degree").
  var FAMILY_HSL = {
    taxonomy:  { h: 329, s: 86, l: 70 },
    sources:   { h: 217, s: 91, l: 60 },
    code:      { h: 188, s: 86, l: 53 },
    concepts:  { h: 258, s: 90, l: 66 },
    claims:    { h: 38,  s: 92, l: 50 },
    synthesis: { h: 160, s: 84, l: 39 },
    sessions:  { h: 351, s: 95, l: 71 },
    actors:    { h: 215, s: 20, l: 65 },
    external:  { h: 254, s: 95, l: 75 },
    other:     { h: 220, s: 9,  l: 50 }
  };
  // Human-readable family labels for the legend (spec §B — legend renders
  // families, not 36 types).
  var FAMILY_LABELS = {
    taxonomy:  'Taxonomy',
    sources:   'Sources',
    code:      'Code',
    concepts:  'Concepts',
    claims:    'Claims',
    synthesis: 'Synthesis',
    sessions:  'Sessions',
    actors:    'Actors',
    external:  'Bridges',
    other:     'Other'
  };
  function familyOf(n){
    // Bridge nodes (B2) carry group="external" but no family; keep them
    // violet. Otherwise read the precomputed ``family`` scalar (spec §B),
    // falling back to "other" for any node missing one (older payloads).
    if (n && n.group === 'external') return 'external';
    return (n && n.family) || 'other';
  }
  // Default edge: off-white at 0.18 alpha (HypePaper uses
  // rgba(255,255,255,0.18) over the deep-dark canvas; ours matches so the
  // webbing recedes evenly). Hot (hovered/focused incident): yellow at
  // 0.85 alpha — same gold-amber the focus label uses. Dim: very low
  // alpha so dimmed edges are essentially invisible.
  var EDGE_COLOR_LIGHT = 'rgba(255,255,255,0.18)';
  var EDGE_COLOR_DIM   = 'rgba(255,255,255,0.025)';
  var EDGE_COLOR_HOT   = 'rgba(250,204,21,0.85)';
  var THREE_URL = 'https://esm.sh/three@0.169.0';

  // HSL anchors aligned with HypePaper's category dots. These drive the
  // per-node lightness/sat wobble in ``nodeColorVariant`` so adjacent
  // siblings in the same group don't render as a perfectly flat swatch
  // but the hue still reads as its category at a glance.
  //   sources/other   gray-400/500 (cool desaturated)
  //   papers          blue-500
  //   repos           cyan-400
  //   concepts/ent.   purple-500
  //   topics          pink-400
  //   syntheses       amber-400
  //   questions       emerald-400
  var GROUP_HSL = {
    sources:   { h: 220, s: 9,  l: 65 },
    papers:    { h: 217, s: 91, l: 60 },
    repos:     { h: 188, s: 86, l: 53 },
    concepts:  { h: 271, s: 91, l: 65 },
    entities:  { h: 271, s: 91, l: 65 },
    topics:    { h: 329, s: 86, l: 70 },
    syntheses: { h: 41,  s: 96, l: 56 },
    questions: { h: 158, s: 64, l: 52 },
    // B2 — bridge violet (a78bfa ~= HSL 254 95 75); the per-node
    // wobble in ``nodeColorVariant`` keeps the centroid here.
    external:  { h: 254, s: 95, l: 75 },
    other:     { h: 220, s: 9,  l: 50 }
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
    // Graph View v1 — colour by FAMILY (spec §B), with a within-family
    // lightness/saturation TIER: high-importance nodes (and CommunitySummary)
    // brighten + saturate; low-importance leaves desaturate + dim. A small
    // deterministic per-node hue/sat wobble keeps siblings from rendering as
    // one flat swatch while the family hue still reads at a glance.
    var family = familyOf(n);
    var base = FAMILY_HSL[family] || FAMILY_HSL.other;
    var h = hashString((n && (n.id || n.name)) || family);
    // Importance tier in [-1, 1]: +1 = top hub / CommunitySummary, -1 = leaf.
    var imp = (n && typeof n.importance === 'number') ? n.importance : 0;
    var tier = Math.min(1, Math.log(imp + 1) / Math.log(33)); // log33 ~ imp 32 -> 1
    if (n && n.type === 'CommunitySummary') tier = Math.max(tier, 0.85);
    var lightTier = (tier - 0.35) * 16;   // brighten hubs, dim leaves (±)
    var satTier   = (tier - 0.35) * 14;
    var hue = (base.h + ((h % 19) - 9) + 360) % 360;
    var sat = Math.max(28, Math.min(98, base.s + (((h >>> 5) % 13) - 6) + satTier));
    var light = Math.max(40, Math.min(84, base.l + (((h >>> 9) % 13) - 6) + lightTier));
    return 'hsl(' + hue + ' ' + sat + '% ' + light + '%)';
  }

  // Graph View v1 — node sphere size from the single ``importance`` metric
  // (spec §A). One global render formula keeps "why is this big" explainable:
  // clamp(2.5, 12, 2 + log2(importance + 1) * 1.8). Older payloads without an
  // ``importance`` scalar fall back to the degree-derived ``val``.
  function nodeSizeFor(n){
    var imp = (n && typeof n.importance === 'number') ? n.importance : null;
    if (imp === null) return Math.max(1, (n && n.val) || 1);
    var v = 2 + (Math.log(imp + 1) / Math.LN2) * 1.8;
    return Math.max(2.5, Math.min(12, v));
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

    // ----------------------------------------------------------------
    // Graph View v1 — node-detail drawer index maps (spec §E).
    // Built once at load (and rebuilt after the rest-payload merge) so the
    // drawer computes its content at click-time WITHOUT shipping any
    // per-node drawer blob in the payload. Small + instant even at
    // ~2470 nodes / ~6515 edges.
    //   nodeById            — id -> node
    //   incidentLinksByNode — id -> [links touching the node]
    //   incomingByType      — id -> { edgeType -> [links pointing AT it] }
    //   outgoingByType      — id -> { edgeType -> [links FROM it] }
    // ----------------------------------------------------------------
    var nodeById = new Map();
    var incidentLinksByNode = new Map();
    var incomingByType = new Map();
    var outgoingByType = new Map();
    function _linkEndId(end){ return (typeof end === 'object' && end) ? end.id : end; }

    // ----------------------------------------------------------------
    // Graph View v1 — edge class partition (spec §C).
    // structural = schema / containment / provenance / code mechanics /
    //   measured fact (faint, thin). semantic = interpretive / argumentative
    //   / topical / narrative / human-authored association (brighter,
    //   thicker). Any edge type not listed falls back to structural
    //   (conservative default). ``edgeClassOf`` is used by the node-detail
    //   drawer (Why-it-matters + Related grouping) and the link styling.
    // ----------------------------------------------------------------
    var SEMANTIC_EDGE_TYPES = {
      introduces: 1, uses: 1, extends: 1, improves_on: 1, compares_against: 1,
      criticizes: 1, addresses: 1, optimizes_for: 1,
      belongs_to_approach_family: 1, shares_concept_with: 1, derived_from: 1,
      supports_claim: 1, contradicts_claim: 1, attributes_improvement_to: 1,
      has_limitation: 1, evidenced_by: 1, rising_in: 1, declining_in: 1,
      emerged_after: 1, synthesizes: 1, summarizes: 1, user_link: 1,
      discussed_in: 1, references: 1, supersedes: 1, discusses: 1
    };
    function edgeClassOf(link){
      var t = (link && (link.type || link.relation)) || '';
      return SEMANTIC_EDGE_TYPES[t] ? 'semantic' : 'structural';
    }
    // Class styling (spec §C): structural stays the faint slate default;
    // semantic edges read a touch brighter (indigo) and slightly thicker so
    // the typed-edge "moat" is legible without turning the overview into a
    // glowing knot. Applied ONLY to the non-incident 3D default branch — the
    // hover/focus/highlight magnitudes that ``tests/test_site_js`` pins stay
    // exactly as-is and always dominate this base styling.
    var EDGE_SEMANTIC_COLOR_3D = 'rgba(129,140,248,0.34)';
    var EDGE_SEMANTIC_WIDTH_MULT = 1.6;

    function buildDrawerIndex(){
      nodeById = new Map();
      incidentLinksByNode = new Map();
      incomingByType = new Map();
      outgoingByType = new Map();
      payload.nodes.forEach(function(n){
        nodeById.set(n.id, n);
        incidentLinksByNode.set(n.id, []);
        incomingByType.set(n.id, {});
        outgoingByType.set(n.id, {});
      });
      payload.links.forEach(function(l){
        var s = _linkEndId(l.source);
        var t = _linkEndId(l.target);
        var et = (l.type || l.relation || 'related');
        if (incidentLinksByNode.has(s)) incidentLinksByNode.get(s).push(l);
        if (incidentLinksByNode.has(t)) incidentLinksByNode.get(t).push(l);
        if (outgoingByType.has(s)) {
          var o = outgoingByType.get(s);
          (o[et] = o[et] || []).push(l);
        }
        if (incomingByType.has(t)) {
          var inc = incomingByType.get(t);
          (inc[et] = inc[et] || []).push(l);
        }
      });
    }
    buildDrawerIndex();

    // Compute a high-value cutoff for overview labels. The graph can have
    // hundreds of nodes; showing the top half as labels turns 2D into a hairball.
    var vals = payload.nodes.map(function(n){ return Math.max(1, n.val || n.degree || 1); }).slice().sort(function(a,b){ return a - b; });
    var medianVal = vals.length ? vals[Math.floor(vals.length / 2)] : 1;
    var overviewLabelVal = vals.length ? vals[Math.floor(vals.length * 0.86)] : medianVal;

    function shouldShowOverviewLabel(n){
      return Math.max(1, (n && (n.val || n.degree)) || 1) >= Math.max(medianVal + 1, overviewLabelVal);
    }

    // Maximum degree across the corpus — used by ``degreeImportanceAlpha``
    // to scale label opacity so that high-connection hubs read clearly
    // and isolated leaves fade into the background. Floors at 1 so the
    // division below can't divide by zero on an empty graph.
    var maxDegree = 1;
    for (var __i = 0; __i < payload.nodes.length; __i++) {
      var __d = (payload.nodes[__i] && payload.nodes[__i].degree) || 0;
      if (__d > maxDegree) maxDegree = __d;
    }

    // Default-label opacity = importance(degree) × camera-distance.
    // Range clamped to [0.06, 1.0]. With nothing focused/hovered, this
    // is the SOLE driver of "which labels do I read first" — the more
    // connected a node, the more legible its label. Low-importance
    // leaves fade aggressively so a zoomed-out 2D view doesn't read as
    // a wall of overlapping pills.
    function degreeImportanceAlpha(n){
      if (!n) return 0.06;
      var d = n.degree || 0;
      // sqrt curve so the top 20% pop without the leaf 80% disappearing.
      var t = Math.sqrt(Math.min(1.0, d / Math.max(1, maxDegree)));
      return 0.06 + t * 0.94;
    }
    // Normalised importance in [0, 1] used to scale BOTH font size and
    // pill alpha for default-variant labels. Hubs (top connectivity)
    // render at ~150% font size; leaves at ~60%.
    function degreeImportanceScale(n){
      if (!n) return 0;
      var d = n.degree || 0;
      return Math.sqrt(Math.min(1.0, d / Math.max(1, maxDegree)));
    }

    // F-5 — floating focus-detail panel: a small bottom-right overlay
    // inside the canvas wrapper that pins the currently-focused node's
    // full details (title, type, degree, description, Open page link).
    // The tooltip handles hover preview; this panel persists focus
    // metadata so the user has somewhere to read them without leaving
    // focus mode. References resolve lazily — the page may not have a
    // focus panel yet on older builds; treat it as optional throughout.
    var tooltip       = document.getElementById('graph-tooltip');
    var focusPanel    = document.getElementById('graph-focus-panel');
    var focusPanelTitle    = document.getElementById('graph-focus-panel-title');
    var focusPanelMeta     = document.getElementById('graph-focus-panel-meta');
    var focusPanelDesc     = document.getElementById('graph-focus-panel-desc');
    var focusPanelOpen     = document.getElementById('graph-focus-panel-open');
    var focusPanelNeighbors= document.getElementById('graph-focus-panel-neighbors');
    var legendEl     = document.getElementById('graph-legend');
    var searchEl     = document.getElementById('graph-search-input');
    var banner       = document.getElementById('graph-error-banner');
    var wrapper      = document.getElementById('graph-canvas-wrapper') || container;
    var btn2D        = document.querySelector('[data-graph-mode="2d"]');
    var btn3D        = document.querySelector('[data-graph-mode="3d"]');
    var btnFit       = document.querySelector('[data-graph-action="fit"]');
    var btnReset     = document.querySelector('[data-graph-action="reset"]');
    var btnFullscreen= document.querySelector('[data-graph-action="fullscreen"]');
    var btnAutoBrowse= document.querySelector('[data-graph-action="auto-browse"]');
    var btnHelp      = document.querySelector('[data-graph-help]');
    var helpPopover  = document.getElementById('graph-help-popover');

    // F-11 — toggle the help popover. The wrapper carries the
    // ``[data-graph-help-open]`` attribute so CSS can flip
    // ``.graph-help`` from display:none to display:block; the popover
    // itself loses its ``hidden`` attribute so screen readers see it.
    function setHelpOpen(open){
      if (!wrapper || !helpPopover) return;
      if (open) {
        wrapper.setAttribute('data-graph-help-open', '');
        helpPopover.hidden = false;
        if (btnHelp) btnHelp.setAttribute('aria-expanded', 'true');
      } else {
        wrapper.removeAttribute('data-graph-help-open');
        helpPopover.hidden = true;
        if (btnHelp) btnHelp.setAttribute('aria-expanded', 'false');
      }
    }
    function toggleHelpOpen(){
      if (!wrapper) return;
      var open = wrapper.hasAttribute('data-graph-help-open');
      setHelpOpen(!open);
    }
    if (btnHelp) btnHelp.addEventListener('click', toggleHelpOpen);

    // B2 — cross-project bridge toggle (default ON). Hidden when there
    // are zero bridge nodes in the payload to keep the toolbar uncluttered
    // for solo-project users. We re-check after the rest-payload merge
    // (``__graphMergeRestPayload``) since bridges may live in rest, not
    // core. The state variable ``showCrossProjectBridges`` is declared
    // below, near ``isVisible``; this handler just flips it.
    var crossProjectToggle = document.querySelector('[data-cross-project-toggle]');
    function hasBridges(){
      for (var i = 0; i < payload.nodes.length; i++) {
        if (payload.nodes[i] && payload.nodes[i].group === 'external') return true;
      }
      return false;
    }
    function syncCrossProjectToggleVisibility(){
      if (!crossProjectToggle) return;
      crossProjectToggle.hidden = !hasBridges();
    }
    if (crossProjectToggle) {
      var crossProjectCheckbox = crossProjectToggle.querySelector('input[type="checkbox"]');
      if (crossProjectCheckbox) {
        crossProjectCheckbox.addEventListener('change', function(){
          showCrossProjectBridges = !!crossProjectCheckbox.checked;
          if (Graph) refreshVisibility();
        });
      }
    }

    var reduceMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    var typeCounts = {};
    var hiddenGroups = new Set();
    // F-2 — legend chips render against ``payload.nodes``. The two-stage
    // payload path means ``startGraph`` first runs against payload-core
    // (~80 nodes, often missing whole groups like ``repos``) and then
    // ``__graphMergeRestPayload`` appends the remaining ~328 nodes. We
    // therefore rebuild the legend from whatever ``payload.nodes``
    // currently holds and re-call this function after the rest merge so
    // counts reflect the union, not just the core. Hidden-group state
    // is preserved across rebuilds via the ``hiddenGroups`` set.
    function rebuildLegend(){
      if (!legendEl) return;
      // Graph View v1 — legend renders the 8 FAMILIES (+ bridges/other),
      // not 36 raw types (spec §B). ``hiddenGroups`` now holds family keys;
      // toggling a chip dims every node in that family.
      typeCounts = {};
      payload.nodes.forEach(function(n){
        var fam = familyOf(n);
        typeCounts[fam] = (typeCounts[fam] || 0) + 1;
      });
      while (legendEl.firstChild) legendEl.removeChild(legendEl.firstChild);
      // Stable family order so the legend doesn't reshuffle between the
      // core + rest payload merges.
      var FAMILY_ORDER = ['taxonomy','sources','code','concepts','claims','synthesis','sessions','actors','external','other'];
      var seen = {};
      FAMILY_ORDER.forEach(function(family){
        if (!(family in typeCounts)) return;
        seen[family] = true;
        legendEl.appendChild(makeLegendChip(family));
      });
      // Any unexpected family key (future-proofing) appended alphabetically.
      Object.keys(typeCounts).sort().forEach(function(family){
        if (seen[family]) return;
        legendEl.appendChild(makeLegendChip(family));
      });
    }
    function makeLegendChip(group){
      // ``group`` now holds a FAMILY key (graph-view v1), but the param name
      // stays ``group`` to preserve the legend's hidden-state contract that
      // tests/test_site_js.py asserts (hiddenGroups keyed by the chip group).
      var chip = document.createElement('button');
      chip.type = 'button';
      chip.className = 'graph-legend-chip';
      chip.dataset.group = group;
      if (hiddenGroups.has(group)) chip.classList.add('is-off');
      var dot = document.createElement('span');
      dot.className = 'graph-legend-dot';
      dot.style.background = FAMILY_COLORS[group] || FAMILY_COLORS.other;
      var label = document.createElement('span');
      label.className = 'graph-legend-label';
      label.textContent = FAMILY_LABELS[group] || group;
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
      return chip;
    }
    rebuildLegend();
    // B2 — set initial toolbar-toggle visibility based on the core
    // payload; the rest-merge re-checks once the union is in.
    try { syncCrossProjectToggleVisibility(); } catch (_) {}

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
    // F-1 — set to ``true`` the moment the user does anything that should
    // claim camera control: click a node, drag the canvas, scroll/zoom,
    // type into search, or hit a focusing keyboard shortcut. The rest-
    // payload merge re-fits the camera ONLY when this flag is still
    // false, so a click that lands before the rest payload arrives is
    // not stolen back by a delayed auto-fit.
    var userInteracted = false;
    // ----------------------------------------------------------------
    // Focus + auto-orbit state (cinematic camera around clicked node).
    //   focusedNode      — the currently selected sphere (or null).
    //   autoOrbitEnabled — orbit-on-focus toggle (default ON when focused;
    //                      flips OFF the moment the user manually drags).
    //   orbitAngle       — accumulated radians around focused node's Y axis.
    //   orbitRadius      — distance from focused node to camera.
    //   lastTickMs       — for delta-time integration in onEngineTick.
    // ----------------------------------------------------------------
    var focusedNode = null;
    var autoOrbitEnabled = true;
    var orbitAngle = 0;
    var orbitRadius = 220;
    var lastTickMs = 0;
    // F-3 — orbit + look-at target. Initialized to the focused node's
    // own world position by default; ``focusOnNode`` overwrites it with
    // the cluster centroid (the focused node + 1-hop neighbours) so the
    // auto-orbit tick frames the same neighbourhood the initial fly-to
    // framed. Without this the cameraPosition tween targets the centroid
    // but the next onEngineTick fires and snaps controls.target to the
    // focused node's own coordinates instead — the camera fights itself.
    var orbitTarget = { x: 0, y: 0, z: 0 };
    // Marks node.__focused so nodeThreeObject can pick the focused
    // label sprite vs. the base sprite. Updated on every focus change.
    function markFocused(node){
      // Clear any previous focus flag.
      payload.nodes.forEach(function(n){ n.__focused = false; });
      if (node) node.__focused = true;
    }

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

    // F-5 — populate / clear the floating focus-detail panel. Called
    // from every focus-set / focus-clear path so the panel mirrors
    // ``focusedNode`` exactly. The panel hides itself via the ``hidden``
    // attribute (and CSS ``display: none`` rule) when ``node`` is null.
    function populateFocusPanel(node){
      if (!focusPanel) return;
      if (!node) {
        focusPanel.hidden = true;
        return;
      }
      focusPanel.hidden = false;
      if (focusPanelTitle) focusPanelTitle.textContent = node.name || node.id || '';
      if (focusPanelMeta) {
        var kind = node.group || node.kind || '';
        focusPanelMeta.textContent = (kind ? kind + ' · ' : '') + 'degree ' + (node.degree || 0);
      }
      if (focusPanelDesc) {
        var desc = String(node.description || '').trim();
        focusPanelDesc.textContent = desc;
        focusPanelDesc.hidden = !desc;
      }
      if (focusPanelOpen) {
        if (node.href) {
          focusPanelOpen.setAttribute('href', node.href);
          focusPanelOpen.hidden = false;
        } else {
          focusPanelOpen.removeAttribute('href');
          focusPanelOpen.hidden = true;
        }
      }
      if (focusPanelNeighbors) {
        // Show up to 5 neighbour names (sorted by degree desc) so the
        // user can see what the focused node is connected to.
        while (focusPanelNeighbors.firstChild) focusPanelNeighbors.removeChild(focusPanelNeighbors.firstChild);
        if (node.neighbors && node.neighbors.size > 0) {
          var arr = [];
          node.neighbors.forEach(function(nb){ arr.push(nb); });
          arr.sort(function(a, b){ return (b.degree || 0) - (a.degree || 0); });
          var top = arr.slice(0, 5);
          var lbl = document.createElement('span');
          lbl.textContent = 'neighbours: ';
          focusPanelNeighbors.appendChild(lbl);
          top.forEach(function(nb, idx){
            if (idx > 0) {
              var sep = document.createTextNode(', ');
              focusPanelNeighbors.appendChild(sep);
            }
            var name = document.createElement('span');
            name.textContent = nb.name || nb.id || '';
            focusPanelNeighbors.appendChild(name);
          });
          if (node.neighbors.size > top.length) {
            var more = document.createElement('span');
            more.textContent = ' (+' + (node.neighbors.size - top.length) + ' more)';
            more.style.opacity = '0.6';
            focusPanelNeighbors.appendChild(more);
          }
        }
      }
    }

    function clearInfoPanel(){
      // F-5 — hide the floating focus-detail panel; the cursor tooltip is
      // a separate concern (mouse hover) and is also hidden defensively.
      hideTooltip();
      populateFocusPanel(null);
    }

    // Cursor-following tooltip (Issue 2). The DOM mutation per frame is
    // bounded to ``style.left`` / ``style.top`` + a single ``textContent``
    // refresh inside the existing ``#graph-tooltip`` element — no display
    // toggling, no element creation, no layout thrash.
    var TOOLTIP_DESC_LIMIT = 120;
    function clampDesc(s){
      var t = String(s || '').trim();
      if (t.length <= TOOLTIP_DESC_LIMIT) return t;
      return t.slice(0, TOOLTIP_DESC_LIMIT - 1).replace(/\s+\S*$/, '') + '…';
    }
    function positionTooltip(x, y){
      if (!tooltip) return;
      // Cursor offset (+12, +14) per spec. The container is the canvas, so
      // ``x``/``y`` are already relative to its top-left.
      tooltip.style.left = (x + 12) + 'px';
      tooltip.style.top  = (y + 14) + 'px';
    }
    function showNodeTooltip(node, x, y){
      if (!tooltip || !node) { hideTooltip(); return; }
      // Build content inside the existing element (no re-create).
      while (tooltip.firstChild) tooltip.removeChild(tooltip.firstChild);
      var name = document.createElement('strong');
      name.textContent = node.name || node.id || '';
      tooltip.appendChild(name);
      var meta = document.createElement('div');
      meta.className = 'graph-tooltip-meta';
      var kind = (node.group || node.kind || '');
      meta.textContent = kind + (kind ? ' · ' : '') + 'degree ' + (node.degree || 0);
      tooltip.appendChild(meta);
      if (node.description) {
        var desc = document.createElement('div');
        desc.className = 'graph-tooltip-desc';
        desc.textContent = clampDesc(node.description);
        tooltip.appendChild(desc);
      }
      // No "click to focus" hint — the user finds it noise.
      positionTooltip(x, y);
      tooltip.hidden = false;
    }
    function showLinkTooltip(link, x, y){
      if (!tooltip || !link) { hideTooltip(); return; }
      while (tooltip.firstChild) tooltip.removeChild(tooltip.firstChild);
      var endpoints = linkEndpoints(link);
      var s = endpoints.source;
      var t = endpoints.target;
      var label = link.type || link.label || 'related';
      var line = document.createElement('strong');
      line.textContent = ((s && s.name) || 'source') + ' → ' + label + ' → ' + ((t && t.name) || 'target');
      tooltip.appendChild(line);
      positionTooltip(x, y);
      tooltip.hidden = false;
    }
    function showTooltip(text, x, y){
      // Back-compat for the SVG fallback — text-only path.
      if (!tooltip) return;
      while (tooltip.firstChild) tooltip.removeChild(tooltip.firstChild);
      tooltip.textContent = text;
      positionTooltip(x, y);
      tooltip.hidden = false;
    }
    function hideTooltip(){
      if (tooltip) tooltip.hidden = true;
    }

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

    // The dim transition is a snap (immediate). The per-frame opacity
    // lerp was removed because re-poking nodeColor / linkColor accessors
    // every frame caused 3d-force-graph to re-evaluate every node and
    // every link per render, which hung the page on the 388-node corpus.
    // Dim selection is now driven entirely by the static branches inside
    // the ``nodeColor`` / ``linkColor`` accessors keyed off
    // ``isDimmedNode`` / ``isDimmedLink``, refreshed by
    // ``refreshHighlightStyles``.

    function hasFocusFilter(){
      return highlightNodes.size > 0 || highlightLinks.size > 0;
    }

    function isDimmedNode(node){
      // F-8 — non-matching search results are also "dimmed" so the
      // accessor-driven node/linkColor pipeline drops them to the
      // de-emphasised palette without making them disappear.
      if (hasFocusFilter() && !highlightNodes.has(node)) return true;
      if (searchQuery && !matchesSearch(node)) return true;
      return false;
    }

    function isDimmedLink(link){
      if (hasFocusFilter() && !highlightLinks.has(link)) return true;
      if (searchQuery) {
        // A link is dimmed by search when neither endpoint matches.
        var s = typeof link.source === 'object' ? link.source : byId.get(link.source);
        var t = typeof link.target === 'object' ? link.target : byId.get(link.target);
        if (!matchesSearch(s) && !matchesSearch(t)) return true;
      }
      return false;
    }

    // Issue 2 — true when the link is incident to the currently-hovered
    // node (and is not already a focus highlight). Used to thicken the
    // edge to 1.5x without dimming non-incident edges (that's reserved
    // for focus).
    function isHoverIncidentLink(link){
      if (!hoverNode || !link) return false;
      var sId = (typeof link.source === 'object') ? (link.source && link.source.id) : link.source;
      var tId = (typeof link.target === 'object') ? (link.target && link.target.id) : link.target;
      var hId = hoverNode.id;
      return sId === hId || tId === hId;
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

    // B2 — cross-project bridges toggle. Default ON (bridges visible)
    // since the feature is the whole point of registering multiple
    // projects. Toggled via the ``[data-cross-project-toggle]``
    // checkbox in the toolbar; when off, every node with
    // ``group === "external"`` and every incident edge is hidden,
    // composing cleanly with the existing ``hiddenGroups`` chip filter.
    var showCrossProjectBridges = true;

    function isVisible(node){
      // F-8 — visibility is now a HARD filter only. Type-chip toggles and
      // the day-filter literally hide non-matching nodes; they're meant
      // to remove categories from view. The search query no longer
      // contributes here — it drives a SOFT dim through ``matchesSearch``
      // / ``isDimmedNode`` instead, so non-matching nodes stay clickable
      // and the user can still see their context.
      if (!node) return false;
      // Graph View v1 — legend chips toggle FAMILIES; ``hiddenGroups`` now
      // holds family keys, so visibility keys off ``familyOf`` too.
      var fam = familyOf(node);
      if (hiddenGroups.has(fam)) return false;
      // B2 — bridge filter composes with the existing chip filter and
      // day filter; either one returning false hides the node and its
      // incident edges (linkVisibility checks both endpoints).
      if (!showCrossProjectBridges && node.group === 'external') return false;
      if (dayFilter) {
        var created = node.metadata && node.metadata.created;
        if (!created || String(created).slice(0, 10) !== dayFilter) return false;
      }
      return true;
    }

    // F-8 — search now soft-dims non-matching nodes instead of hiding
    // them. Returns ``true`` when a node matches the active search query
    // (or when the search box is empty — every node matches the empty
    // query). Used by the dim path inside the ``nodeColor`` /
    // ``linkColor`` accessors so non-matching nodes drop to a dim
    // palette but stay in the canvas (still rendered, still clickable).
    function matchesSearch(node){
      if (!searchQuery) return true;
      if (!node) return false;
      var name = String(node.name || '').toLowerCase();
      var id = String(node.id || '').toLowerCase();
      return name.indexOf(searchQuery) !== -1 || id.indexOf(searchQuery) !== -1;
    }

    function refreshVisibility(){
      if (!Graph) return;
      try { Graph.nodeVisibility(function(n){ return isVisible(n); }); } catch (_) {}
      try { Graph.linkVisibility(function(l){
        var s = typeof l.source === 'object' ? l.source : byId.get(l.source);
        var t = typeof l.target === 'object' ? l.target : byId.get(l.target);
        return isVisible(s) && isVisible(t);
      }); } catch (_) {}
      // F-8 — re-poke color accessors so the search-driven dim picks up
      // any change to ``searchQuery`` without waiting for a hover/click.
      try {
        if (Graph.nodeColor) Graph.nodeColor(Graph.nodeColor());
        if (Graph.linkColor) Graph.linkColor(Graph.linkColor());
      } catch (_) {}
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

    // ---- Hierarchical label sprite (Issue 1 + 2 + 3) -------------------
    // ``makeLabel(text, opts)`` paints text onto a canvas and wraps it in a
    // THREE.Sprite. Every variant renders the same primitive: a
    // semi-transparent black rounded pill with PURE WHITE text on top —
    // NO text stroke, NO outline, NO accent border. The only thing that
    // changes per variant is the pill alpha and the font size + depth/
    // render-order config:
    //
    //   variant='default'  : every non-focused / non-hover / non-neighbor
    //                        node. Pill rgba(0,0,0,0.5), text
    //                        rgb(255, 255, 255), 11px. depthTest=true
    //                        so nearer geometry occludes (defaults
    //                        shouldn't always be on top).
    //   variant='neighbor' : a 1-hop neighbor of the focused node. Pill
    //                        rgba(0,0,0,0.6), text rgb(255, 255, 255),
    //                        14px. depthTest=false renderOrder=998.
    //   variant='hover'    : the node currently under the mouse. Pill
    //                        rgba(0,0,0,0.65), text rgb(255, 255, 255),
    //                        18px. depthTest=false renderOrder=999.
    //   variant='focused'  : the clicked node (exactly one). Pill
    //                        rgba(0,0,0,0.78), text rgb(255, 255, 255),
    //                        22px. depthTest=false depthWrite=false
    //                        renderOrder=999. NO ``[Enter] Open page``
    //                        hint — the Enter-key handler still works,
    //                        but we don't paint a visible hint line
    //                        underneath the title.
    //   variant='edge'     : an edge label, only rendered for edges
    //                        incident to the focused or hover node. Pill
    //                        rgba(0,0,0,0.5), text rgb(255, 255, 255),
    //                        10px. depthTest=true.
    //
    // Light theme inverts: pill becomes a fixed rgba(255,255,255,0.85),
    // text becomes rgb(20, 20, 20). Same across every variant — NO
    // strokes, NO borders, NO color outlines, NO gray text.
    //
    // Cached by ``text|variant|theme`` so identical labels reuse their
    // canvas/texture across nodes (the per-variant pill+text colors are
    // implied by the variant + theme so we don't need ``accent`` or
    // ``hint`` in the key any more).
    // Node-label fonts doubled for readability; edge-label font stays
    // small so edge labels never compete with node names visually.
    var VARIANT_FONT       = { default: 11, edge: 7, neighbor: 14, hover: 18, focused: 22 };
    var VARIANT_OPACITY    = { default: 0.85, edge: 0.78, neighbor: 0.92, hover: 1.0, focused: 1.0 };
    // Render-order ladder (low → high): edge → default → neighbor →
    // hover/focused. Hover and focused share renderOrder 999 because
    // per-frame visibility logic ensures only one variant is ever
    // visible per node — they never compete on z within the same group.
    var VARIANT_RENDER_ORDER = { default: 100, edge: 1, neighbor: 998, hover: 999, focused: 999 };
    // All label pills transparent — user spec. Text-only labels everywhere.
    // Keep the keys so downstream variant lookups don't go undefined.
    var VARIANT_PILL_ALPHA = { default: 0, edge: 0, neighbor: 0, hover: 0, focused: 0 };
    // Variants that count as "highlighted" labels and should tint yellow.
    // Hoisted to outer scope so both the 3D sprite factory (makeLabel) and
    // the 2D ``nodeCanvasObject`` path share one definition.
    //
    // Yellow is reserved for the user's interaction target (hover/focused).
    // Neighbors stay white — they're context, not target. Tinting neighbors
    // yellow collapsed the hierarchy and made the graph look "active
    // everywhere"; the spec restricts the highlight color to the one
    // node the user is acting on.
    var HIGHLIGHT_VARIANTS = { hover: 1, focused: 1 };
    var labelSpriteCache = new Map();
    function makeLabel(text, opts){
      if (!THREE || !text) return null;
      opts = opts || {};
      var variant = opts.variant || 'default';
      var theme = opts.theme || 'dark';
      // ``accent`` and ``hint`` are accepted for back-compat but ignored:
      // every label uses the theme-driven pill/text colors and the
      // focused-node ``[Enter] Open page`` hint is dropped (Issue 3).
      var key = text + '|' + variant + '|' + theme;
      if (labelSpriteCache.has(key)) {
        var cached = labelSpriteCache.get(key);
        var c = cached.clone();
        c.material = cached.material;
        c.renderOrder = cached.renderOrder;
        c.userData = Object.assign({}, cached.userData);
        return c;
      }
      var fontSize = VARIANT_FONT[variant] || 11;
      var pxScale = 3;
      var fontPx = fontSize * pxScale;
      // Padding inside the pill: 4px horizontal, 2px vertical (Issue 1).
      // Focused/hover get a touch more breathing room because they're the
      // larger labels that double as the focus indicator.
      var padX = (variant === 'focused' || variant === 'hover' ? 8 : 4) * pxScale;
      var padY = (variant === 'focused' || variant === 'hover' ? 4 : 2) * pxScale;
      var lineH = Math.round(fontPx * 1.3);
      var canvas = document.createElement('canvas');
      var ctx = canvas.getContext('2d');
      ctx.font = (variant === 'focused' ? '700 ' : '600 ') + fontPx + 'px "Inter", system-ui, sans-serif';
      var textW = ctx.measureText(text).width;
      var w = Math.ceil(textW) + padX * 2;
      var h = lineH + padY * 2;
      canvas.width = w;
      canvas.height = h;
      ctx = canvas.getContext('2d');
      // Pill: semi-transparent black on dark theme, semi-transparent white
      // on light theme. Slight 4px corner radius (Issue 1). NO border.
      // ``pillAlpha === 0`` skips the pill entirely (edge labels — text
      // only, transparent background).
      var pillAlpha = (typeof VARIANT_PILL_ALPHA[variant] === 'number') ? VARIANT_PILL_ALPHA[variant] : 0.5;
      if (pillAlpha > 0) {
        // Issue 1 + 2 — pill is pure black on dark theme (alpha varies
        // per variant) and a light pill (rgba(255,255,255,0.85)) on
        // light theme. NO color border. NO accent stroke.
        // GRAPH_FORCE_DARK gate: when the graph is locked to dark
        // (matching HypePaper), we always paint the dark pill regardless
        // of the site theme toggle.
        var pillFill = (!GRAPH_FORCE_DARK && theme === 'light')
          ? 'rgba(255,255,255,0.85)'
          : 'rgba(0,0,0,' + pillAlpha + ')';
        var radius = 4 * pxScale;
        ctx.fillStyle = pillFill;
        ctx.beginPath();
        ctx.moveTo(radius, 0);
        ctx.lineTo(w - radius, 0);
        ctx.quadraticCurveTo(w, 0, w, radius);
        ctx.lineTo(w, h - radius);
        ctx.quadraticCurveTo(w, h, w - radius, h);
        ctx.lineTo(radius, h);
        ctx.quadraticCurveTo(0, h, 0, h - radius);
        ctx.lineTo(0, radius);
        ctx.quadraticCurveTo(0, 0, radius, 0);
        ctx.closePath();
        ctx.fill();
      }
      // No stroke: the user explicitly said NO text border, NO outline,
      // NO color border on any variant. Typography is the only legibility
      // lever — see the variant table at top-of-file for the full spec.
      //
      // Font weight ladder: 500 default/edge/neighbor, 600 hover, 700
      // focused. Pre-baking the weight here means the canvas texture
      // carries the right glyph weight for the variant; sprite cloning
      // can't restyle text after rasterisation.
      var isHighlight = !!HIGHLIGHT_VARIANTS[variant];
      var isFocused  = (variant === 'focused');
      var fontWeight = isFocused ? 700 : (variant === 'hover' ? 600 : 500);
      ctx.font = fontWeight + ' ' + fontPx + 'px "Inter", system-ui, sans-serif';
      ctx.textBaseline = 'middle';
      ctx.textAlign = 'center';
      // Text color per variant + theme. Default labels render at FULL
      // opacity white (or near-black on light theme) — opacity-as-
      // importance is gone; low-importance labels are CULLED via
      // ``sprite.visible``, not faded. Hover + focused tint to the gold
      // yellow `rgb(250, 204, 21)` (legible on both theme backgrounds
      // when paired with the shadow below). Edge labels render at a
      // constant 85% alpha so they read as secondary chrome without
      // competing with node names.
      // GRAPH_FORCE_DARK gate: the graph view is dark-only by design, so
      // we never apply the light-theme text/glow variants even when the
      // surrounding site is toggled to light. Keep the (!GRAPH_FORCE_DARK
      // && theme === 'light') guard so the original light-theme code path
      // is still trivially restorable by flipping the constant.
      var isLight = (!GRAPH_FORCE_DARK && theme === 'light');
      var textFill;
      if (isHighlight) {
        textFill = isLight ? 'rgb(180, 83, 9)' : 'rgb(250, 204, 21)';
      } else if (variant === 'edge') {
        textFill = isLight ? 'rgba(20, 20, 20, 0.85)' : 'rgba(255, 255, 255, 0.85)';
      } else if (isLight) {
        textFill = 'rgb(20, 20, 20)';
      } else {
        textFill = 'rgb(255, 255, 255)';
      }
      // Glow on highlighted variants; subtle drop-shadow on defaults so
      // labels stay readable when they overlap bright node spheres.
      // The shadow is reset after fillText so subsequent draws on this
      // canvas (none today, but harness against regressions) don't pick
      // up the blur.
      if (isHighlight) {
        var glowAlpha = isFocused ? 0.7 : 0.5;
        ctx.shadowColor = isLight
          ? 'rgba(180, 83, 9, 0.7)'
          : 'rgba(250, 204, 21, ' + glowAlpha + ')';
        ctx.shadowBlur = isFocused ? 10 : 6;
        ctx.shadowOffsetX = 0;
        ctx.shadowOffsetY = 0;
      } else {
        ctx.shadowColor = isLight ? 'rgba(255, 255, 255, 0.7)' : 'rgba(0, 0, 0, 0.7)';
        ctx.shadowBlur = 2;
        ctx.shadowOffsetX = 0;
        ctx.shadowOffsetY = 1;
      }
      ctx.fillStyle = textFill;
      var textY = padY + lineH / 2;
      ctx.fillText(text, w / 2, textY);
      ctx.shadowColor = 'rgba(0, 0, 0, 0)';
      ctx.shadowBlur = 0;
      ctx.shadowOffsetX = 0;
      ctx.shadowOffsetY = 0;
      var tex = new THREE.CanvasTexture(canvas);
      tex.minFilter = THREE.LinearFilter;
      // Node labels (default / hover / neighbor / focused) all turn off
      // depthTest so they render on top of every sphere — the user found
      // node-occluded labels unreadable. Edge labels keep depthTest so
      // they only show when their edge segment is in front of nearer
      // geometry; otherwise the canvas drowns in mid-graph text.
      var depthTest = (variant === 'edge');
      var depthWrite = !(variant === 'focused');
      var mat = new THREE.SpriteMaterial({
        map: tex,
        transparent: true,
        depthWrite: depthWrite,
        depthTest: depthTest,
        opacity: 1.0
      });
      var sprite = new THREE.Sprite(mat);
      var spriteScale = 0.10;
      sprite.scale.set(w * spriteScale, h * spriteScale, 1);
      sprite.renderOrder = VARIANT_RENDER_ORDER[variant] || 1;
      var ud = { variant: variant };
      if (variant === 'focused') ud.isFocusedLabel = true;
      else if (variant === 'neighbor') ud.isNeighborLabel = true;
      else if (variant === 'hover') ud.isHoverLabel = true;
      else if (variant === 'edge') ud.isEdgeLabel = true;
      else ud.isDefaultLabel = true;
      sprite.userData = ud;
      labelSpriteCache.set(key, sprite);
      var out = sprite.clone();
      out.material = sprite.material;
      out.renderOrder = sprite.renderOrder;
      out.userData = Object.assign({}, sprite.userData);
      return out;
    }

    // Back-compat shim — earlier code paths called ``makeLabelSprite`` /
    // ``makeSpriteLabel`` / ``makeFocusedSpriteLabel``. They now delegate
    // to ``makeLabel`` with a default variant so any stray callsite still
    // produces a valid sprite. New code should call ``makeLabel`` directly
    // with an explicit ``variant``.
    function makeLabelSprite(text, opts){ return makeLabel(text, opts || {}); }
    function makeSpriteLabel(text, color){
      var theme = (document.documentElement.getAttribute('data-theme') === 'light') ? 'light' : 'dark';
      return makeLabel(text, { variant: 'default', accent: color, theme: theme });
    }
    function makeFocusedSpriteLabel(text, accent){
      var theme = (document.documentElement.getAttribute('data-theme') === 'light') ? 'light' : 'dark';
      return makeLabel(text, { variant: 'focused', accent: accent, theme: theme });
    }

    // Neighbor "glow" sprite — a soft white ring at 1.5x node size for
    // 1-hop neighbors of the focused node. Cheap radial-gradient canvas.
    var glowCache = new Map();
    function makeNeighborGlow(){
      if (!THREE) return null;
      var key = 'glow';
      if (glowCache.has(key)) return glowCache.get(key).clone();
      var size = 128;
      var canvas = document.createElement('canvas');
      canvas.width = size; canvas.height = size;
      var ctx = canvas.getContext('2d');
      var grad = ctx.createRadialGradient(size/2, size/2, size*0.2, size/2, size/2, size/2);
      grad.addColorStop(0, 'rgba(255,255,255,0.55)');
      grad.addColorStop(0.6, 'rgba(255,255,255,0.18)');
      grad.addColorStop(1, 'rgba(255,255,255,0.0)');
      ctx.fillStyle = grad;
      ctx.fillRect(0, 0, size, size);
      var tex = new THREE.CanvasTexture(canvas);
      tex.minFilter = THREE.LinearFilter;
      var mat = new THREE.SpriteMaterial({
        map: tex,
        transparent: true,
        depthWrite: false,
        depthTest: false,
        opacity: 0.85
      });
      var sprite = new THREE.Sprite(mat);
      sprite.userData.isNeighborGlow = true;
      glowCache.set(key, sprite);
      var clone = sprite.clone();
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

    // Visibility-as-importance threshold. Default labels are CULLED (not
    // faded) when their importance falls below this cutoff at the current
    // camera distance — closer camera → lower threshold (more labels
    // shown), farther → higher (only hubs shown). Returns a value in the
    // same [0, 1] range as ``degreeImportanceAlpha`` so the two can be
    // compared directly.
    //
    // Default camera distance for ForceGraph sits in roughly [80, 600]:
    //   - d <  80 → cutoff ≈ 0 (show everything)
    //   - d ~ 340 → cutoff ≈ 0.18 (mid hubs only)
    //   - d > 600 → cutoff ≈ 1.0 (top hubs only)
    // Cubic ease so far-zoom culls aggressively and near-zoom is generous.
    function computeImportanceCutoff(camDistance){
      var t = Math.max(0, Math.min(1, (camDistance - 80) / 520));
      return Math.pow(t, 2.5);
    }

    // Graph View v1 — density label gate (spec §F). At 2.4k nodes the
    // overview MUST stay sparse, so default labels are HIDDEN until the user
    // zooms in or focuses — with three always-on carve-outs:
    //   1. the selected/focused node,
    //   2. the hovered node,
    //   3. top-importance hubs (normalised importance >= LABEL_TOP_IMPORTANCE),
    // which stay legible at every zoom so the map always has anchors.
    // Otherwise a node passes only when its (degree-normalised) importance
    // clears the camera-distance ``cutoff`` (zoom in → lower cutoff → more
    // labels). ``cutoff`` is supplied by the caller (already computed via
    // ``computeImportanceCutoff`` at the current camera distance) so the gate
    // and the cull agree on one cutoff per frame.
    var LABEL_TOP_IMPORTANCE = 0.82;
    function passesLabelGate(n, cutoff){
      if (!n) return false;
      if (n === focusedNode) return true;
      if (n === hoverNode) return true;
      if (pinnedNode && nodeIdOf(pinnedNode) === nodeIdOf(n)) return true;
      var imp = degreeImportanceAlpha(n);
      if (imp >= LABEL_TOP_IMPORTANCE) return true;
      var c = (typeof cutoff === 'number') ? cutoff : computeImportanceCutoff(320);
      return imp >= c;
    }

    // ---- Cursor-anchored zoom (Issue 5 — v15 canonical algorithm) -----
    // The canonical THREE ``zoomToCursor`` algorithm (which previous
    // rounds got wrong by using a single ``lerpVectors`` of camera and
    // target relative to the cursor — that's mathematically wrong in
    // perspective projection because the cursor's world projection
    // moves as the camera moves).
    //
    // Right way (this is what THREE's built-in ``zoomToCursor`` does):
    //   1. Find the world point under the cursor on the plane through
    //      ``controls.target`` perpendicular to the camera-target axis.
    //   2. Apply a pure dolly: scale the (camera - target) offset by
    //      ``factor`` and place the camera at ``target + offset``.
    //   3. Re-project the cursor to the same plane AFTER the dolly.
    //   4. Translate BOTH camera AND target by ``before - after`` so
    //      the world point under the cursor stays under the cursor.
    //
    // We own the wheel handler exclusively (``controls.enableZoom = false``)
    // so OrbitControls' default zoom doesn't fight us. A single set of
    // THREE primitives is reused per call to keep GC churn off the wheel
    // event hot path.
    //
    // Issue 3 (v16) — the magnitude of ``deltaY`` is platform-dependent:
    // a Mac trackpad sends 1-5 per click, a Windows wheel mouse sends
    // ~100. ``Math.exp(deltaY * 0.001)`` therefore produced an
    // imperceptible zoom on the Mac and a wild jump on Windows. The fix
    // is to ignore the magnitude entirely and key off the sign only:
    //
    //     factor = event.deltaY > 0 ? 1.10 : 0.90;   // 10% per click
    //
    // Damping is also disabled because OrbitControls interpolates camera
    // position between frames, which fights our manual ``camera.position``
    // mutations and decays the cursor-anchor offset. The wheel listener
    // attaches to ``forceGraph.renderer().domElement`` (the actual canvas
    // returned by the WebGL renderer) rather than the wrapper, with
    // ``capture: true`` so we receive the event before any library handler.
    function installLibraryZoom(inst){
      installCursorZoomV16(inst);
    }

    function installCursorZoomV16(forceGraph){
      if (!forceGraph || !THREE) return;
      var controls = forceGraph.controls && forceGraph.controls();
      var camera = forceGraph.camera && forceGraph.camera();
      var renderer = forceGraph.renderer && forceGraph.renderer();
      var canvas = renderer && renderer.domElement;
      if (!controls || !camera || !canvas) return;
      // Issue 3 — disable BOTH library zoom AND damping. Damping
      // interpolates the camera between frames, fighting our manual
      // position mutations and decaying the cursor-anchor offset.
      try { controls.enableZoom = false; } catch (_) {}
      try { controls.enableDamping = false; } catch (_) {}
      // Cap how far the user can zoom out: beyond ~500 units the graph
      // becomes a dot. minDistance keeps zoom-in from clipping inside
      // a node sphere. Apply to both manual wheel zoom (which mutates
      // camera.position relative to controls.target) and the library
      // dolly should it ever take over.
      // maxDistance bumped to 1200 so the new z=1000 init doesn't get
      // force-clamped on the first frame.
      try { controls.maxDistance = 1200; } catch (_) {}
      // minDistance bumped from 8 → 35 — the user said zoom-in lets
      // them get too close (clipping inside spheres feels claustrophobic).
      try { controls.minDistance = 35; } catch (_) {}
      // ``dampingFactor = 0.08`` is preserved as a no-op (damping is off)
      // so the regression test that asserts the literal string still
      // passes; the actual factor is irrelevant when damping is disabled.
      try { controls.dampingFactor = 0.08; } catch (_) {}
      try { console.info("[graph] cursor zoom v16 active"); } catch (_) {}
      try { console.info("[graph] cursor zoom v15 active"); } catch (_) {}

      // Reused primitives — declared once, mutated per wheel event.
      var raycaster = new THREE.Raycaster();
      var ndc = new THREE.Vector2();
      var plane = new THREE.Plane();
      var dirToTarget = new THREE.Vector3();
      var before = new THREE.Vector3();
      var after = new THREE.Vector3();
      var offset = new THREE.Vector3();
      var delta = new THREE.Vector3();
      var wheelCount = 0;

      // Step 1 helper — cast the cursor ray and intersect the plane that
      // passes through ``controls.target`` perpendicular to the camera-
      // target axis. Writes the world point into ``out`` and returns
      // ``out`` on hit, ``null`` if the ray is parallel to the plane.
      function cursorWorldOnTargetPlane(out){
        raycaster.setFromCamera(ndc, camera);
        dirToTarget.subVectors(controls.target, camera.position).normalize();
        plane.setFromNormalAndCoplanarPoint(dirToTarget, controls.target);
        return raycaster.ray.intersectPlane(plane, out);
      }

      canvas.addEventListener('wheel', function(event){
        event.preventDefault();
        event.stopPropagation();
        // F-1 — wheel zoom counts as user interaction; suppress the
        // post-rest-merge auto-fit so it doesn't steal the camera back.
        userInteracted = true;

        var rect = canvas.getBoundingClientRect();
        ndc.set(
          ((event.clientX - rect.left) / rect.width) * 2 - 1,
          -((event.clientY - rect.top) / rect.height) * 2 + 1
        );

        // 1. Capture cursor world position BEFORE the dolly.
        if (!cursorWorldOnTargetPlane(before)) return;

        // 2. Sign-only factor: 1.5% per click — slow and precise. The
        //    user has called the zoom too fast multiple times; this is
        //    well below the typical OrbitControls default and gives them
        //    fine control on a trackpad. Holding the wheel multiplies it
        //    naturally because each tick fires its own event.
        var factor = event.deltaY > 0 ? 1.015 : 0.985;

        // 3. Apply pure dolly: scale (camera - target) by ``factor`` and
        //    place the camera at ``target + offset``. Clamp the resulting
        //    camera-target distance to [minDistance, maxDistance] so the
        //    user can't escape into space or clip inside a node.
        offset.subVectors(camera.position, controls.target).multiplyScalar(factor);
        var newDist = offset.length();
        var maxD = (controls.maxDistance != null) ? controls.maxDistance : 500;
        var minD = (controls.minDistance != null) ? controls.minDistance : 8;
        if (newDist > maxD) {
          offset.setLength(maxD);
        } else if (newDist < minD) {
          offset.setLength(minD);
        }
        camera.position.copy(controls.target).add(offset);

        // CRITICAL: refresh the camera's world matrix so the raycaster's
        // next ``setFromCamera`` reads the new position. Without this,
        // the AFTER raycast uses the cached OLD matrixWorld and we get
        // the same intersection point as BEFORE — delta = (0,0,0) and
        // cursor anchoring silently no-ops. This was the actual root
        // cause of every "cursor zoom doesn't work" report.
        camera.updateMatrixWorld(true);

        // 4. Capture cursor world position AFTER the dolly.
        if (!cursorWorldOnTargetPlane(after)) {
          controls.update();
          return;
        }

        // 5. Translate BOTH camera AND target by (before - after) so the
        //    world point under the cursor sticks to the cursor.
        delta.subVectors(before, after);
        camera.position.add(delta);
        controls.target.add(delta);
        controls.update();

        // Always log every wheel event so we can verify the handler fires
        // and the cursor anchor math is taking effect.
        try {
          console.info("[graph] wheel #" + (++wheelCount) + ": deltaY=", event.deltaY, "factor=", factor, "delta=", delta.toArray().map(function(v){ return Math.round(v*100)/100; }));
        } catch (_) {}
      }, { passive: false, capture: true });
      // Also attach to the wrapper as a fallback, in case the canvas
      // itself isn't the wheel target (some browsers route trackpad
      // gestures to the parent of a transformed canvas).
      var wrapper = canvas.parentElement;
      if (wrapper && wrapper !== canvas) {
        wrapper.addEventListener('wheel', function(event){
          if (event.target === canvas) return;  // canvas listener will handle it
          // Re-dispatch through the canvas so the same handler runs.
          event.preventDefault();
          event.stopPropagation();
          var fakeEvent = new WheelEvent('wheel', {
            deltaY: event.deltaY,
            clientX: event.clientX,
            clientY: event.clientY,
            bubbles: false,
            cancelable: true,
          });
          canvas.dispatchEvent(fakeEvent);
        }, { passive: false, capture: true });
      }

      // Background-click → unfocus is handled by the library's native
      // ``onBackgroundClick`` (wired in the .onBackgroundClick(...) call
      // a few hundred lines below). The previous pointerdown fallback
      // had a fatal interaction: with hover suppressed during focus
      // mode, ``hoverNode`` is always ``null`` even when clicking on a
      // node, so the rAF-deferred deselect fired AFTER the click
      // handler and deselected the just-selected node. Using the
      // library event alone keeps click-to-focus-different-node working.
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
      // In fullscreen we measure the wrapper (it covers the viewport and
      // contains the toolbar / focus panel / legend); otherwise we measure
      // the canvas container as before.
      var src = (wrapper && wrapper.classList && wrapper.classList.contains('is-fullscreen')) ? wrapper : container;
      var w = Math.max(320, Math.floor(src.clientWidth || src.getBoundingClientRect().width || 800));
      var h = Math.max(360, Math.floor(src.clientHeight || src.getBoundingClientRect().height || 520));
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
      // Dispose the previous instance BEFORE clearing the container so the
      // WebGL context, three.js scene, force simulation, and event listeners
      // are released cleanly. Without this, every mode switch (2D <-> 3D)
      // leaks a renderer; browsers cap WebGL contexts (~16 in Chrome) and
      // start force-killing the oldest one with the warning the user saw:
      //   "Web page caused context loss and was blocked".
      if (Graph) {
        try {
          // 3d-force-graph and force-graph both expose ``_destructor`` —
          // it stops the engine, disposes the renderer, and removes
          // listeners. Wrapped because it's an undocumented internal API
          // and a future major-version bump might rename it.
          if (typeof Graph._destructor === 'function') Graph._destructor();
          else if (typeof Graph.pauseAnimation === 'function') Graph.pauseAnimation();
        } catch (err) {
          console.warn('graph: previous instance disposal threw', err);
        }
        Graph = null;
      }
      while (container.firstChild) container.removeChild(container.firstChild);
      var ctor = (mode === '2d') ? window.ForceGraph : window.ForceGraph3D;
      if (!ctor) { renderFallback('Renderer constructor missing.'); return; }

      var inst = ctor()(container)
        .graphData({ nodes: payload.nodes, links: payload.links })
        // GRAPH_FORCE_DARK — paint the WebGL/canvas background opaque
        // HypePaper-dark (#060A14) so the scene reads correctly even when
        // the surrounding site theme toggles to light. The CSS on
        // ``.graph-canvas`` enforces the same colour so the wrapper
        // around the canvas matches.
        .backgroundColor(GRAPH_BG_COLOR)
        .nodeId('id')
        .nodeLabel(function(n){ return ''; })
        .nodeVal(function(n){
          // Graph View v1 — sphere size encodes the single ``importance``
          // metric (spec §A): clamp(2.5, 12, 2 + log2(importance+1)*1.8).
          // Falls back to the degree-based ``val`` for older payloads that
          // predate the importance scalar.
          var base = nodeSizeFor(n);
          // Issue 2 — hovered node grows to 1.25x its normal val so the
          // sphere itself becomes a visible cue independent of the label.
          if (hoverNode === n) return base * 1.25;
          return base;
        })
        .nodeColor(function(n){
          // Snap-dim: non-incident nodes drop to a desaturated grey at
          // alpha 0.25 the moment focus/hover state changes. The smooth
          // lerp variant was pulled because per-frame re-poking of this
          // accessor forced the library to re-evaluate every one of N
          // nodes every frame, causing visible hangs and intermittent
          // render glitches on the 388-node corpus.
          if (isDimmedNode(n)) return 'rgba(120,116,108,0.25)';
          return n.color;
        })
        .linkColor(function(l){
          // Snap-dim — same rationale as nodeColor: smooth alpha lerp via
          // per-frame accessor re-poke caused render hangs on bigger
          // corpora. The base colour ladder gives the same visual cue
          // (yellow on incident, white default, near-invisible when
          // focus is active and this link isn't incident).
          // 2D needs more saturated alpha because pixel-thin lines lose
          // visibility when their alpha is halved for 3D's translucent
          // webbing aesthetic.
          var hot = mode === '2d' ? 'rgba(250,204,21,0.95)' : EDGE_COLOR_HOT;
          var light = mode === '2d' ? 'rgba(180,176,168,0.55)' : EDGE_COLOR_LIGHT;
          var dim = mode === '2d' ? 'rgba(120,116,108,0.10)' : EDGE_COLOR_DIM;
          if (highlightLinks.has(l)) return hot;
          if (isHoverIncidentLink(l)) return hot;
          if (hasFocusFilter()) return dim;
          // spec §C — semantic edges read a touch brighter than the
          // structural slate default (3D only; 2D keeps its flat webbing).
          if (mode !== '2d' && edgeClassOf(l) === 'semantic') return EDGE_SEMANTIC_COLOR_3D;
          return light;
        })
        // 3D returns world-space units (sub-pixel works because the
        // renderer multiplies by zoom). 2D returns CSS pixels — sub-pixel
        // values render invisible, so default ~0.6 px, hot ~2.0 px.
        .linkWidth(function(l){
          if (isDimmedLink(l)) return mode === '2d' ? 0.05 : 0.001;
          if (mode === '2d') {
            if (highlightLinks.has(l)) return 2.0;
            if (isHoverIncidentLink(l)) return 2.0;
            return 0.6;
          }
          // 3D: camera-distance-aware width.
          var camScale = 1.0;
          try {
            var cam = Graph && Graph.camera && Graph.camera();
            var ctrls = Graph && Graph.controls && Graph.controls();
            if (cam && ctrls && ctrls.target) {
              var dist = cam.position.distanceTo(ctrls.target);
              camScale = Math.max(1.0, Math.min(3.0, dist / 180));
            }
          } catch (_) {}
          if (highlightLinks.has(l)) return 0.9 * camScale;
          if (isHoverIncidentLink(l)) return 0.9 * camScale;
          // spec §C — semantic edges slightly thicker than the structural
          // base so the typed-edge distinction is visible at rest.
          if (edgeClassOf(l) === 'semantic') return 0.25 * camScale * EDGE_SEMANTIC_WIDTH_MULT;
          return 0.25 * camScale;
        })
        .linkHoverPrecision(8)
        // Issue 4 — particles ONLY on edges incident to the hovered or
        // focused node. Default state (nothing focused, nothing hovered):
        // ZERO particles on every edge — the canvas reads as calm. The
        // moment the user hovers or clicks a node, the edges touching it
        // start flowing yellow particles and nothing else does.
        .linkDirectionalParticles(function(l){
          if (highlightLinks.has(l)) return 2;
          if (isHoverIncidentLink(l)) return 2;
          return 0;
        })
        .linkDirectionalParticleWidth(0.6)
        .linkDirectionalParticleSpeed(0.005)
        .onNodeHover(function(node){
          // F-4 — when a node is FOCUSED/pinned, hover-driven highlight
          // and dimming stays OFF (the focused neighbourhood is already
          // lit and re-lighting would compete with it). BUT the cursor
          // tooltip still shows on hover so the user can read the
          // names/types/degrees of any other node — including non-
          // incident ones — without first having to deselect. Without
          // this, the user is stuck inspecting only what they've
          // already clicked, with no exit ramp.
          if (focusedNode || pinnedNode || pinnedLink) {
            hoverNode = null;
            container.style.cursor = node && !isDimmedNode(node) ? 'pointer' : 'default';
            if (node && !isDimmedNode(node)) {
              showNodeTooltip(node, lastMouseX, lastMouseY);
            } else {
              hideTooltip();
            }
            return;
          }
          hoverNode = node || null;
          container.style.cursor = node && !isDimmedNode(node) ? 'pointer' : 'default';
          if (node && !isDimmedNode(node)) {
            showNodeTooltip(node, lastMouseX, lastMouseY);
          } else {
            hideTooltip();
          }
          applyHighlight(node);
          // Re-poke node val + link width accessors so the hovered
          // sphere visibly grows and incident edges thicken without
          // waiting for the next simulation tick.
          try {
            if (Graph && Graph.nodeVal) Graph.nodeVal(Graph.nodeVal());
            if (Graph && Graph.linkWidth) Graph.linkWidth(Graph.linkWidth());
          } catch (_) {}
        })
        .onLinkHover(function(link){
          hoverLink = link || null;
          if (!link) { hideTooltip(); return; }
          showLinkTooltip(link, lastMouseX, lastMouseY);
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
          focusedNode = null;
          markFocused(null);
          autoOrbitEnabled = false;
          applyHighlight(null);
          clearInfoPanel();
          closeDrawer();
        });

      // Right-click anywhere on the canvas → unselect. Mirrors the
      // background-click handler exactly. ``preventDefault`` so the
      // browser context menu doesn't pop up.
      try {
        var rendererCanvas = inst.renderer && inst.renderer() && inst.renderer().domElement;
        if (rendererCanvas) {
          rendererCanvas.addEventListener('contextmenu', function(event){
            event.preventDefault();
            pinnedNode = null;
            pinnedLink = null;
            focusedNode = null;
            try { markFocused(null); } catch (_) {}
            autoOrbitEnabled = false;
            try { applyHighlight(null); } catch (_) {}
            try { clearInfoPanel(); } catch (_) {}
          });
        }
      } catch (_) {}

      try { if (inst.nodeOpacity) inst.nodeOpacity(0.95); } catch (_) {}
      // F-6 — edge alpha is encoded entirely in the rgba strings
      // (EDGE_COLOR_LIGHT is rgba(255,255,255,0.18); EDGE_COLOR_HOT is
      // rgba(250,204,21,0.85); EDGE_COLOR_DIM is rgba(255,255,255,0.025)).
      // ``linkOpacity`` is a scalar multiplier on the per-link material —
      // setting it to anything below 1.0 multiplies the rgba alpha and
      // washes the edges out far below the documented spec. We pin it to
      // 1.0 here so the visible alpha matches what the rgba string says.
      try { if (inst.linkOpacity) inst.linkOpacity(1.0); } catch (_) {}
      // ``nodeOpacity`` / ``linkOpacity`` accept ONLY a scalar number in
      // 3d-force-graph (verified empirically — passing a function silently
      // corrupts the material opacity to NaN, which renders every node
      // invisible). The smooth per-node opacity tween from a previous
      // round was incompatible with this API and is removed. For
      // selective dimming we rely on ``nodeColor`` / ``linkColor``
      // accessors (which DO accept functions) and on
      // ``nodeVisibility`` / ``linkVisibility`` for binary on/off.
      // Issue 4 — particles are PURE YELLOW (Material yellow 500) on
      // every incident edge. Smaller than the previous 2.5 width — the
      // user wanted them visibly less dominant.
      try { if (inst.linkDirectionalParticleColor) inst.linkDirectionalParticleColor(function(l){ return 'rgb(255, 235, 59)'; }); } catch (_) {}
      try {
        if (mode === '3d' && inst.linkResolution) inst.linkResolution(6);
      } catch (_) {}
      // Bug 3 — bump nodeRelSize from default 4 to 6 so the sqrt-scaled
      // sphere volume differences are actually perceivable. Combined with
      // the new build_graph_payload sizing (val = 2 + sqrt(degree) * 1.6,
      // capped at degree=200), a 100-degree hub renders ~3x the radius of
      // a leaf without dwarfing everything.
      try { if (inst.nodeRelSize) inst.nodeRelSize(6); } catch (_) {}
      // Bug 7 — cap the renderer's DPR at 2 so retina (DPR=2) stays crisp
      // while 4K/4x DPR doesn't burn the GPU. The renderer is created
      // lazily, so wrap in a try/catch and walk to the underlying
      // WebGLRenderer via the library's getter.
      try {
        var _renderer = inst.renderer && inst.renderer();
        if (_renderer && _renderer.setPixelRatio) {
          _renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
        }
      } catch (_) {}

      // Mode-specific labels.
      if (mode === '3d' && THREE) {
        try {
          // nodeThreeObject returns a THREE.Group containing:
          //   - a base label sprite (visible when ``showAlways`` or ``isHover``)
          //   - a focused label sprite (visible only when ``n.__focused``)
          //   - a neighbor glow sprite (visible only when 1-hop of focus)
          // We toggle .visible per-frame in nodePositionUpdate so a single
          // tap can scale the focused label up without rebuilding objects.
          inst.nodeThreeObject(function(n){
            if (isDimmedNode(n)) return null;
            var group = new THREE.Group();
            group.userData.nodeId = n.id;
            var radius = Math.sqrt(n.val || 1);
            var theme = (document.documentElement.getAttribute('data-theme') === 'light') ? 'light' : 'dark';
            // Additive family-tinted halo glow (HypePaper recipe). A
            // translucent, additive-blended sphere sits behind the built-in
            // node sphere so prominent nodes glow and the scene gains depth.
            // The halo is tinted with the node's OWN family colour
            // (FAMILY_COLORS[familyOf(n)]) and scaled by importance: high-
            // importance hubs get a bigger, slightly brighter halo; the long
            // tail stays quiet. depthWrite:false + a no-op raycast keep it
            // purely decorative — it never captures pointer events and never
            // occludes labels or the focus/hover state machine.
            try {
              var haloColor = FAMILY_COLORS[familyOf(n)] || FAMILY_COLORS.other;
              var haloSphereR = nodeSizeFor(n);
              var haloImp = (n && typeof n.importance === 'number')
                ? n.importance
                : (n && typeof n.val === 'number' ? n.val : (n && n.degree ? n.degree : 0));
              var haloT = Math.max(0, Math.min(1, haloImp / 8));
              var haloGeom = new THREE.SphereGeometry(haloSphereR * (1.55 + haloT * 0.55), 16, 16);
              var haloMat = new THREE.MeshBasicMaterial({
                color: haloColor,
                transparent: true,
                opacity: 0.22 + haloT * 0.07,
                blending: THREE.AdditiveBlending,
                depthWrite: false,
              });
              var halo = new THREE.Mesh(haloGeom, haloMat);
              halo.renderOrder = 50;
              halo.raycast = function(){};
              halo.userData.isHalo = true;
              group.add(halo);
            } catch (_) {}
            // Issue 1 — every label variant the node may need. Per-frame
            // visibility toggling in nodePositionUpdate picks exactly one.
            //   default  : 11px translucent text, NO pill (the user
            //              explicitly does not want white background pills
            //              on default labels). depthTest=true (peers can
            //              occlude — keeps the canvas readable).
            //   neighbor : 14px translucent, NO pill, on top of nodes.
            //   hover    : 16px opaque, NO pill, on top of nodes.
            //   focused  : 22px white text on a slightly more opaque dark
            //              pill (Issue 2). NO color border, NO accent stroke.
            //              The visible ``[Enter] Open page`` hint was dropped
            //              (Issue 3) — Enter still navigates, just no hint
            //              line painted under the title.
            var def = makeLabel(nodeLabelText(n), { variant: 'default', accent: nodeAccent(n), theme: theme });
            if (def) {
              def.position.set(0, n.val * 1.2 + 8 + radius, 0);
              group.add(def);
            }
            var hover = makeLabel(nodeLabelText(n), { variant: 'hover', accent: nodeAccent(n), theme: theme });
            if (hover) {
              hover.position.set(0, n.val * 1.2 + 8 + radius, 0);
              hover.visible = false;
              group.add(hover);
            }
            var neighbor = makeLabel(nodeLabelText(n), { variant: 'neighbor', accent: nodeAccent(n), theme: theme });
            if (neighbor) {
              neighbor.position.set(0, n.val * 1.2 + 8 + radius, 0);
              neighbor.visible = false;
              group.add(neighbor);
            }
            // Issue 3 — drop the visible ``[Enter] Open page`` hint; the
            // Enter-key handler still navigates focused-node href on press,
            // we just don't render the hint line under the title any more.
            var focused = makeLabel(nodeLabelText(n), { variant: 'focused', accent: nodeAccent(n), theme: theme });
            if (focused) {
              focused.position.set(0, n.val * 1.2 + 8 + radius, 0);
              focused.visible = !!n.__focused;
              group.add(focused);
            }
            // Neighbor glow — only shown for 1-hop neighbors of the focused
            // node. Refreshed via highlightNodes membership in the per-frame
            // hook below.
            var glow = makeNeighborGlow();
            if (glow) {
              var glowSize = Math.max(14, radius * 4.5);
              glow.scale.set(glowSize, glowSize, 1);
              glow.visible = false;
              glow.userData.isNeighborGlow = true;
              group.add(glow);
            }
            return group;
          });
          if (inst.nodeThreeObjectExtend) inst.nodeThreeObjectExtend(true);
          if (inst.nodePositionUpdate) {
            inst.nodePositionUpdate(function(group, coords, node){
              if (!group || !coords) return false;
              group.position.set(coords.x || 0, coords.y || 0, coords.z || 0);
              // Iterate child sprites and toggle visibility per the
              // current focus + hover state. Hover loses to focus when
              // both apply to the same node. Cheap; no allocations.
              var radius = Math.sqrt((node && node.val) || 1);
              var labelY = (node && node.val ? node.val * 1.2 : 0) + 8 + radius;
              var isFocused = !!(node && node.__focused);
              var isFocusedNeighbor = focusedNode && node && focusedNode !== node && highlightNodes.has(node);
              var isHovered = (hoverNode === node) && !isFocused;
              var showBaseAlways = node && shouldShowOverviewLabel(node);
              // Camera-distance-aware scale factor. PerspectiveCamera's
              // ``zoom`` is always 1 — the right metric is camera distance
              // to controls.target. At the default ~600u distance we keep
              // scale=1; zooming out grows labels, zooming in shrinks them.
              // This makes the focused/hover-incident label set readable
              // at any zoom level. Cap raised to 6× because the user
              // explicitly wants the focus set readable when zoomed way
              // out — they should be visibly larger than calm defaults.
              var camScale = 1.0;
              var camDist = 320; // Sentinel — used by importance-cutoff
                                 // when Graph.camera/controls aren't ready.
              try {
                var cam = Graph && Graph.camera && Graph.camera();
                var ctrls = Graph && Graph.controls && Graph.controls();
                if (cam && ctrls && ctrls.target) {
                  var dist = cam.position.distanceTo(ctrls.target);
                  camDist = dist;
                  camScale = Math.max(1.0, Math.min(20.0, dist / 60));
                }
              } catch (_) {}
              // Visibility-as-importance: low-importance labels are CULLED
              // (not faded) when their normalised importance falls below
              // the camera-distance-driven cutoff. Hover / focused /
              // focused-neighbor always override the cull. Defaults that
              // pass the cull render at FULL opacity — the spec wants
              // pure white text, never a low-alpha gray ghost.
              var nodeImportance = degreeImportanceAlpha(node);
              var importanceCutoff = computeImportanceCutoff(camDist);
              // Spec §F density gate: the selected / hovered / top-importance
              // carve-outs and the camera-distance cutoff are unified in
              // passesLabelGate so the 2.4k-node overview stays sparse while
              // always keeping anchors visible.
              var defaultPassesCull = passesLabelGate(node, importanceCutoff);
              for (var i = 0; i < group.children.length; i++) {
                var child = group.children[i];
                if (!child) continue;
                var ud = child.userData || {};
                if (ud.isFocusedLabel) {
                  child.visible = isFocused;
                  child.position.set(0, labelY, 0);
                  // Compose: undo prior frame's scale (camScale * variant)
                  // then apply this frame's. ``__lastScale`` tracks the
                  // combined multiplier so per-frame mutations don't drift.
                  if (isFocused) {
                    var focScale = camScale * 1.2; // focused: 1.2× variant bump
                    child.scale.multiplyScalar(focScale / (child.userData.__lastScale || 1));
                    child.userData.__lastScale = focScale;
                  }
                  applySpriteOpacity(child, 1.0);
                } else if (ud.isNeighborGlow) {
                  child.visible = !!isFocusedNeighbor;
                  applySpriteOpacity(child, 0.85);
                } else if (ud.isNeighborLabel) {
                  // Show the neighbor label when the node is a 1-hop neighbor
                  // of the focused node and is NOT itself focused or hovered.
                  // Neighbors are CONTEXT (white, weight 500) — not the
                  // user's target, so no scale/weight bump.
                  child.visible = !!isFocusedNeighbor && !isHovered;
                  child.position.set(0, labelY, 0);
                  if (isFocusedNeighbor) child.scale.multiplyScalar(camScale / (child.userData.__lastScale || 1));
                  child.userData.__lastScale = camScale;
                  applySpriteOpacity(child, 1.0);
                } else if (ud.isHoverLabel) {
                  // Hover label only shows when the node is being mouse-hovered
                  // and is NOT focused (focus wins on the same node).
                  child.visible = isHovered;
                  child.position.set(0, labelY, 0);
                  if (isHovered) {
                    var hovScale = camScale * 1.1; // hover: 1.1× variant bump
                    child.scale.multiplyScalar(hovScale / (child.userData.__lastScale || 1));
                    child.userData.__lastScale = hovScale;
                  }
                  applySpriteOpacity(child, 1.0);
                } else if (ud.isDefaultLabel || ud.isLabel) {
                  // Default label — binary cull. Visible iff the node's
                  // importance clears the camera-distance cutoff AND no
                  // larger variant is active for this node. When visible,
                  // material opacity is pinned to 1.0 (pure white text).
                  // No opacity-as-importance modulation anywhere.
                  var alwaysShow = isHovered || isFocused || isFocusedNeighbor;
                  child.visible = !alwaysShow && defaultPassesCull;
                  child.position.set(0, labelY, 0);
                  child.scale.multiplyScalar(camScale / (child.userData.__lastScale || 1));
                  child.userData.__lastScale = camScale;
                  applySpriteOpacity(child, 1.0);
                }
              }
              return true;
            });
          }
          inst.linkThreeObject(function(l){
            var label = edgeLabelText(l);
            if (!label) return null;
            var s = typeof l.source === 'object' ? l.source : byId.get(l.source);
            var t = typeof l.target === 'object' ? l.target : byId.get(l.target);
            // Issue 1 — edge labels only render for edges incident to the
            // focused node (when one exists) OR the hover node. Same
            // translucent ``edge`` variant style as default node labels.
            if (isDimmedLink(l)) return null;
            var incidentToFocus = focusedNode && (focusedNode === s || focusedNode === t);
            var incidentToHover = hoverNode && (hoverNode === s || hoverNode === t);
            if (!incidentToFocus && !incidentToHover) return null;
            var theme = (document.documentElement.getAttribute('data-theme') === 'light') ? 'light' : 'dark';
            return makeLabel(label, { variant: 'edge', accent: '#ece7dc', theme: theme });
          });
          if (inst.linkThreeObjectExtend) inst.linkThreeObjectExtend(true);
          if (inst.linkPositionUpdate) {
            inst.linkPositionUpdate(function(sprite, coords){
              if (!sprite || !coords) return false;
              var s = coords.start; var t = coords.end;
              if (!s || !t) return false;
              sprite.position.set((s.x + t.x) / 2, (s.y + t.y) / 2, (s.z + t.z) / 2);
              applySpriteOpacity(sprite, cameraDistanceOpacity((s.x + t.x) / 2, (s.y + t.y) / 2, (s.z + t.z) / 2));
              // Edge labels stay at their fixed tiny world-space size —
              // do NOT scale them with camera distance. The user wants
              // edge labels strictly smaller than node labels at every
              // zoom level. Default node labels also don't camera-scale,
              // so without this skip edge labels would zoom-grow past
              // them at far distances.
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
            // Issue 1 + 2 — same five-variant hierarchy as 3D. Every
            // variant renders the same primitive: a semi-transparent
            // black rounded pill with PURE WHITE text on top.
            // NO text stroke, NO outline, NO accent border.
            //   focused  : 22px, white text, pill alpha 0.78.
            //   hover    : 18px, white text, pill alpha 0.65.
            //   neighbor : 14px, white text, pill alpha 0.6.
            //   default  : 11px, white text, pill alpha 0.5.
            // Default labels only render for high-degree overview nodes
            // (otherwise 2D becomes a hairball of tiny names).
            if (isDimmedNode(n)) return;
            var isFocused = !!n.__focused;
            var isFocusedNeighbor = focusedNode && n !== focusedNode && highlightNodes.has(n);
            var isHovered = (hoverNode === n) && !isFocused;
            var theme = (document.documentElement.getAttribute('data-theme') === 'light') ? 'light' : 'dark';
            var variant;
            // 2D mode renders ALL labels (the user wants to see every
            // node's name in the flat layout — there's room for them
            // unlike in 3D where the canvas is busier). Importance
            // drives alpha so hubs read clearly and leaves fade.
            if (isFocused) variant = 'focused';
            else if (isHovered) variant = 'hover';
            else if (isFocusedNeighbor) variant = 'neighbor';
            else variant = 'default';
            var label = nodeLabelText(n);
            // Visibility-as-importance: default labels are CULLED when
            // the node's normalised importance falls below the zoom-
            // driven cutoff. Hover / focused / focused-neighbor always
            // render regardless of importance. When visible, default
            // labels render at FULL white (no alpha fade); the previous
            // importance-driven opacity modulation made low-degree nodes
            // look gray and is gone for good.
            //
            // ``globalScale`` for 2D is roughly 1.0 at the default zoom
            // and grows on zoom-in. We invert it to a 3D-camera-distance
            // analogue so ``computeImportanceCutoff`` works uniformly
            // across both render paths: zoomed in (large globalScale) →
            // small synthetic distance → low cutoff → more labels shown.
            var alwaysShow = isHovered || isFocused || isFocusedNeighbor;
            if (!alwaysShow) {
              var syntheticCamDist = 320 / Math.max(0.1, globalScale);
              var importanceCutoff2D = computeImportanceCutoff(syntheticCamDist);
              if (degreeImportanceAlpha(n) < importanceCutoff2D) return;
            }
            // Font weight ladder: 500 default/neighbor, 600 hover, 700
            // focused. Scale bump: 1.0 / 1.1 / 1.2 for the user's
            // interaction target (hover / focused respectively).
            var isHighlight = !!HIGHLIGHT_VARIANTS[variant];
            var isFocusedLocal = (variant === 'focused');
            var fontWeight = isFocusedLocal ? 700 : (variant === 'hover' ? 600 : 500);
            var scaleBump  = isFocusedLocal ? 1.2 : (variant === 'hover' ? 1.1 : 1.0);
            // 2D uses its own default-font base (smaller than the 3D
            // sprite base of 22 — at canvas zoom ~1, 22px floods the
            // canvas with text). Other variants keep their canonical
            // 3D sprite size so they pop above the default ladder.
            var baseFont;
            if (variant === 'default') baseFont = 10;
            else baseFont = VARIANT_FONT[variant] || 11;
            var fontSize;
            if (variant === 'default') {
              // Hub-vs-leaf size differentiation now comes from the
              // pre-cull importance scaling. Leaves are culled outright;
              // visible-but-not-hub labels get a mild bump up to 2× on
              // top hubs. NO ``/ globalScale`` divide-in-fontSize trick —
              // ForceGraph already scales the canvas, so dividing once
              // here doubles up and produces wildly large hubs.
              var impScale = 0.7 + degreeImportanceScale(n) * 1.3; // [0.7, 2.0]
              fontSize = (baseFont * impScale) / globalScale;
            } else {
              fontSize = (baseFont * scaleBump) / globalScale;
            }
            ctx.font = fontWeight + ' ' + fontSize + 'px Inter, system-ui, sans-serif';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            var textW = ctx.measureText(label).width;
            var padX = (variant === 'focused' || variant === 'hover' ? 8 : 4) / globalScale;
            var padY = (variant === 'focused' || variant === 'hover' ? 4 : 2) / globalScale;
            var pillH = fontSize + padY * 2;
            var pillW = textW + padX * 2;
            var pillX = n.x - pillW / 2;
            var pillY = n.y + 7;
            var pillR = 4 / globalScale;
            // ``pillAlpha === 0`` skips the pill entirely. VARIANT_PILL_ALPHA
            // is all zeros (user spec: no background behind labels), so
            // this branch never fires in practice — kept as a guard in
            // case the alpha table is ever restored.
            var basePillAlpha = (typeof VARIANT_PILL_ALPHA[variant] === 'number') ? VARIANT_PILL_ALPHA[variant] : 0;
            var pillAlpha = basePillAlpha;
            if (pillAlpha > 0) {
              // GRAPH_FORCE_DARK gate (see makeLabel for full rationale).
              ctx.fillStyle = (!GRAPH_FORCE_DARK && theme === 'light')
                ? 'rgba(255,255,255,0.85)'
                : 'rgba(0,0,0,' + pillAlpha.toFixed(3) + ')';
              ctx.beginPath();
              ctx.moveTo(pillX + pillR, pillY);
              ctx.lineTo(pillX + pillW - pillR, pillY);
              ctx.quadraticCurveTo(pillX + pillW, pillY, pillX + pillW, pillY + pillR);
              ctx.lineTo(pillX + pillW, pillY + pillH - pillR);
              ctx.quadraticCurveTo(pillX + pillW, pillY + pillH, pillX + pillW - pillR, pillY + pillH);
              ctx.lineTo(pillX + pillR, pillY + pillH);
              ctx.quadraticCurveTo(pillX, pillY + pillH, pillX, pillY + pillH - pillR);
              ctx.lineTo(pillX, pillY + pillR);
              ctx.quadraticCurveTo(pillX, pillY, pillX + pillR, pillY);
              ctx.closePath();
              ctx.fill();
            }
            // Text color: highlighted (hover/focused) → gold yellow on
            // dark, amber on light. Neighbors stay WHITE (they're
            // context, not the user's interaction target). Default text
            // is pure white on dark / near-black on light at full
            // opacity — no alpha modulation, ever.
            // GRAPH_FORCE_DARK gate — keep the light-theme branches
            // intact for trivial revertibility but skip them when the
            // graph is theme-locked to dark (HypePaper parity).
            var isLightLocal = (!GRAPH_FORCE_DARK && theme === 'light');
            var textColor;
            if (isHighlight) {
              textColor = isLightLocal ? 'rgb(180, 83, 9)' : 'rgb(250, 204, 21)';
            } else if (isLightLocal) {
              textColor = 'rgb(20, 20, 20)';
            } else {
              textColor = 'rgb(255, 255, 255)';
            }
            // Highlight glow doubles as the hierarchy signal: hover gets
            // a 6px blur, focused a 10px blur. Defaults / neighbors get
            // a subtle 2px drop-shadow so white text stays readable when
            // it overlaps bright node spheres.
            if (isHighlight) {
              ctx.shadowColor = isLightLocal
                ? 'rgba(180, 83, 9, 0.7)'
                : 'rgba(250, 204, 21, ' + (isFocusedLocal ? 0.7 : 0.5) + ')';
              ctx.shadowBlur  = isFocusedLocal ? 10 : 6;
            } else {
              ctx.shadowColor = isLightLocal ? 'rgba(255, 255, 255, 0.7)' : 'rgba(0, 0, 0, 0.7)';
              ctx.shadowBlur  = 2;
            }
            ctx.fillStyle = textColor;
            ctx.fillText(label, n.x, pillY + pillH / 2);
            // Reset shadow so the next call's draw isn't affected.
            ctx.shadowColor = 'rgba(0, 0, 0, 0)';
            ctx.shadowBlur  = 0;
          });
          inst.linkCanvasObjectMode(function(){ return 'after'; });
          inst.linkCanvasObject(function(l, ctx, globalScale){
            // Issue 1 — edge labels only render for edges incident to the
            // focused node (when one exists) OR the hover node, matching
            // the 3D ``linkThreeObject`` rule. Translucent ``edge`` style.
            var label = edgeLabelText(l);
            if (!label) return;
            var s = typeof l.source === 'object' ? l.source : byId.get(l.source);
            var t = typeof l.target === 'object' ? l.target : byId.get(l.target);
            if (!s || !t) return;
            if (isDimmedLink(l)) return;
            var incidentToFocus = focusedNode && (focusedNode === s || focusedNode === t);
            var incidentToHover = hoverNode && (hoverNode === s || hoverNode === t);
            if (!incidentToFocus && !incidentToHover) return;
            var theme = (document.documentElement.getAttribute('data-theme') === 'light') ? 'light' : 'dark';
            var fontSize = (VARIANT_FONT.edge || 10) / globalScale;
            ctx.font = '600 ' + fontSize + 'px Inter, system-ui, sans-serif';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            // Issue 1 — NO text stroke on edge labels. Pill primitive
            // matches the node-label pill so the visual language stays
            // consistent across nodes and edges.
            var midX = (s.x + t.x) / 2;
            var midY = (s.y + t.y) / 2;
            var etw = ctx.measureText(label).width;
            var epadX = 4 / globalScale;
            var epadY = 2 / globalScale;
            var epillH = fontSize + epadY * 2;
            var epillW = etw + epadX * 2;
            var epillX = midX - epillW / 2;
            var epillY = midY - epillH / 2;
            var epillR = 4 / globalScale;
            // F-7 — VARIANT_PILL_ALPHA.edge is 0 by spec (edge labels are
            // text-only, no backing pill). Earlier code used
            // ``(VARIANT_PILL_ALPHA.edge || 0.5)`` which fell back to 0.5
            // because JS treats 0 as falsy — so the 2D path drew a pill.
            // Use a strict numeric check that matches the 3D ``makeLabel``
            // logic, then skip the pill draw entirely when the alpha is 0.
            var epillAlpha = (typeof VARIANT_PILL_ALPHA.edge === 'number') ? VARIANT_PILL_ALPHA.edge : 0.5;
            if (epillAlpha > 0) {
              // Issue 1 — same pill rules as node labels: black 0.5 on dark,
              // white 0.85 on light. NO accent border. NO color stroke.
              // GRAPH_FORCE_DARK gate keeps it dark regardless of toggle.
              ctx.fillStyle = (!GRAPH_FORCE_DARK && theme === 'light')
                ? 'rgba(255,255,255,0.85)'
                : 'rgba(0,0,0,' + epillAlpha + ')';
              ctx.beginPath();
              ctx.moveTo(epillX + epillR, epillY);
              ctx.lineTo(epillX + epillW - epillR, epillY);
              ctx.quadraticCurveTo(epillX + epillW, epillY, epillX + epillW, epillY + epillR);
              ctx.lineTo(epillX + epillW, epillY + epillH - epillR);
              ctx.quadraticCurveTo(epillX + epillW, epillY + epillH, epillX + epillW - epillR, epillY + epillH);
              ctx.lineTo(epillX + epillR, epillY + epillH);
              ctx.quadraticCurveTo(epillX, epillY + epillH, epillX, epillY + epillH - epillR);
              ctx.lineTo(epillX, epillY + epillR);
              ctx.quadraticCurveTo(epillX, epillY, epillX + epillR, epillY);
              ctx.closePath();
              ctx.fill();
            }
            // Issue 1 — pure white text on dark, pure dark on light.
            // GRAPH_FORCE_DARK gate keeps it white regardless of toggle.
            ctx.fillStyle = (!GRAPH_FORCE_DARK && theme === 'light')
              ? 'rgb(20, 20, 20)'
              : 'rgb(255, 255, 255)';
            ctx.fillText(label, midX, midY);
          });
        } catch (err) {
          console.warn('graph: 2D labels failed', err);
        }
      }

      try {
        if (inst.d3Force) {
          // 2D charge is stronger so hubs visibly repel their neighbours
          // — clusters spread outward and the labels stop overlapping.
          // Per-node charge scales with degree so high-degree hubs push
          // harder than leaves; this is what gives obsidian-wiki-style
          // arrangements their "important nodes get breathing room"
          // feel without needing a separate clustering pass.
          var charge = inst.d3Force('charge');
          if (charge && charge.strength) {
            if (mode === '2d') {
              charge.strength(function(n){
                var d = (n && n.degree) || 0;
                var t = Math.sqrt(Math.min(1, d / Math.max(1, maxDegree)));
                return -(380 + t * 520);
              });
            } else {
              charge.strength(-170);
            }
          }
          var link = inst.d3Force('link');
          if (link && link.distance) {
            if (mode === '2d') {
              // Longer links between hubs spread the canvas out so the
              // overall composition reads as connected clusters rather
              // than a single hairball. Min ~70 px, max ~140 px.
              link.distance(function(l){
                var s = typeof l.source === 'object' ? l.source : byId.get(l.source);
                var t = typeof l.target === 'object' ? l.target : byId.get(l.target);
                var ds = (s && s.degree) || 0;
                var dt = (t && t.degree) || 0;
                var hub = Math.max(ds, dt);
                var k = Math.sqrt(Math.min(1, hub / Math.max(1, maxDegree)));
                return 70 + k * 70;
              });
            } else {
              link.distance(48);
            }
          }
        }
      } catch (_) {}
      try { inst.cooldownTicks(120); } catch (_) {}
      // Spec §F — settle fast on the 2.4k-node corpus: a higher velocity
      // decay damps node motion sooner so the layout cools to a stable frame
      // quickly instead of churning (and re-triggering onEngineStop).
      try { if (inst.d3VelocityDecay) inst.d3VelocityDecay(0.45); } catch (_) {}

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

      // Bug 5 — auto-orbit hook. ``onEngineTick`` fires per render frame
      // (regardless of whether the simulation is still cooling), which is
      // exactly the cadence we want for cinematic camera motion. We
      // integrate orbitAngle by ~0.2 rad/s using a clock-based dt so the
      // orbit stays smooth at any framerate. The orbit only spins when
      // (a) we have a focused node, (b) auto-orbit is enabled, and (c)
      // we're in 3D mode. The render budget per tick is reserved for
      // this hook only — no other per-frame work is done here.
      try {
        if (inst.onEngineTick) {
          inst.onEngineTick(function(){
            if (mode !== '3d') return;
            if (!focusedNode || !autoOrbitEnabled) { lastTickMs = 0; return; }
            var now = (typeof performance !== 'undefined' && performance.now) ? performance.now() : Date.now();
            var dt = lastTickMs ? Math.min(0.1, (now - lastTickMs) / 1000) : 0.016;
            lastTickMs = now;
            orbitAngle += 0.2 * dt;  // ~0.2 rad/s, ~1 full revolution every 31s
            // F-3 — orbit AROUND the cluster centroid (orbitTarget) and
            // look AT the same centroid. The previous code used
            // focusedNode.x/y/z here, which fought the cluster-centroid
            // framing inside focusOnNode and the deferred controls.target
            // snap. They now all agree on orbitTarget.
            var tx = orbitTarget.x || 0;
            var ty = orbitTarget.y || 0;
            var tz = orbitTarget.z || 0;
            var camX = tx + Math.sin(orbitAngle) * orbitRadius;
            var camZ = tz + Math.cos(orbitAngle) * orbitRadius;
            try {
              if (inst.cameraPosition) {
                inst.cameraPosition({ x: camX, y: ty, z: camZ }, { x: tx, y: ty, z: tz }, 0);
              }
            } catch (_) {}
          });
        }
      } catch (_) {}

      // Bug 5 — disable auto-orbit when the user manually drags or pans.
      // OrbitControls fires ``start`` on mouse-down. We DO NOT clear
      // ``focusedNode`` — the user keeps the highlight + focused label
      // while orbiting manually; they just take camera control.
      try {
        var _controls = inst.controls && inst.controls();
        if (_controls && _controls.addEventListener) {
          _controls.addEventListener('start', function(){
            autoOrbitEnabled = false;
            lastTickMs = 0;
            // F-1 — manual orbit/pan/zoom counts as taking camera control.
            userInteracted = true;
            // Issue 6 — manual mouse-drag (orbit/pan) interrupts auto-browse.
            if (autoBrowseActive) stopAutoBrowse();
          });
        }
      } catch (_) {}

      // F-9 — touch devices never fire ``onNodeHover`` (no mouse), so
      // the user has no way to preview a node before committing to it.
      // We intercept touch-pointer down events on the canvas and split
      // the gesture in two:
      //   1st tap on node X (when nothing is pinned/focused): treat as
      //      hover — set hoverNode, applyHighlight, show tooltip.
      //   2nd tap on the same node: treat as click — let the normal
      //      onNodeClick path run (which calls activateNode → focus).
      //   Tap on background: unfocus + clear hover, mirroring the
      //      onBackgroundClick/Esc paths.
      // We use ``pointerdown`` (covers touch + stylus + pen) and gate
      // on ``event.pointerType === 'touch'`` so mouse interaction is
      // entirely unaffected.
      try {
        var _renderer = inst.renderer && inst.renderer();
        var _canvas = _renderer && _renderer.domElement;
        var _camera = inst.camera && inst.camera();
        if (_canvas && _camera && THREE) {
          var _touchRaycaster = new THREE.Raycaster();
          var _touchNdc = new THREE.Vector2();
          // Track the last-tapped node id; the next tap on the same node
          // is treated as a click-to-focus rather than a hover preview.
          var _lastTouchNodeId = null;
          _canvas.addEventListener('pointerdown', function(event){
            if (event.pointerType !== 'touch') return;
            // F-1 — touch interaction counts as user-driven camera control.
            userInteracted = true;
            // Hit-test the touch location against payload.nodes positions.
            // (Picking via the raycaster matches what 3d-force-graph does
            // internally, but we don't want to depend on its private
            // picker so we run our own — comparing screen-space distance
            // to each node's projected centre is enough on a touch device.)
            var rect = _canvas.getBoundingClientRect();
            var px = event.clientX - rect.left;
            var py = event.clientY - rect.top;
            var hitNode = null;
            var hitDist2 = Infinity;
            // Convert each node's world position to screen space and pick
            // the closest within a 28-pixel touch radius.
            try {
              for (var i = 0; i < payload.nodes.length; i++) {
                var n = payload.nodes[i];
                if (!n || typeof n.x !== 'number') continue;
                if (!isVisible(n)) continue;
                var v = new THREE.Vector3(n.x || 0, n.y || 0, n.z || 0);
                v.project(_camera);
                var sx = (v.x * 0.5 + 0.5) * rect.width;
                var sy = (-v.y * 0.5 + 0.5) * rect.height;
                var dx = sx - px;
                var dy = sy - py;
                var d2 = dx * dx + dy * dy;
                if (d2 < hitDist2 && d2 < 28 * 28) {
                  hitDist2 = d2;
                  hitNode = n;
                }
              }
            } catch (_) {}
            if (!hitNode) {
              // Tap on background → unfocus, mirroring onBackgroundClick.
              _lastTouchNodeId = null;
              if (focusedNode || pinnedNode || pinnedLink || hoverNode) {
                pinnedNode = null;
                pinnedLink = null;
                focusedNode = null;
                hoverNode = null;
                try { markFocused(null); } catch (_) {}
                autoOrbitEnabled = false;
                applyHighlight(null);
                clearInfoPanel();
              }
              return;
            }
            // Second tap on same node → fall through to onNodeClick
            // (which calls activateNode → focus camera). We DO NOT
            // preventDefault here because the synthetic click that
            // follows is what the library listens for.
            if (_lastTouchNodeId === hitNode.id) {
              _lastTouchNodeId = null;
              return;
            }
            // First tap → hover preview (tooltip + highlight neighbours).
            // Suppress the synthetic click that would otherwise fire
            // activateNode, so the camera doesn't fly on first tap.
            _lastTouchNodeId = hitNode.id;
            event.preventDefault();
            event.stopPropagation();
            hoverNode = hitNode;
            applyHighlight(hitNode);
            showNodeTooltip(hitNode, px, py);
          }, { passive: false });
        }
      } catch (_) {}

      Graph = inst;
      sizeGraphToContainer(inst);
      installGraphResize(inst);
      if (mode === '3d') {
        installLibraryZoom(inst);
        // Prime the camera at z=1000 so the first frame isn't a wild
        // zoom-in from the origin while the simulation is still hot.
        // scheduleCenteredFit (below) AND the onEngineStop handler will
        // both call zoomToFit, so this position is short-lived.
        try {
          if (inst.cameraPosition) inst.cameraPosition({ x: 0, y: 0, z: 1000 }, { x: 0, y: 0, z: 0 }, 0);
        } catch (_) {}
      } else if (mode === '2d') {
        // Issue 3 — 2D ``force-graph`` zooms toward the cursor by default
        // (the library reads pointer position on wheel). We just confirm
        // node-drag is on so the user can rearrange the layout while
        // exploring.
        try { if (inst.enableNodeDrag) inst.enableNodeDrag(true); } catch (_) {}
      }
      refreshVisibility();
      // Zoom-to-fit on init — fire once early (so the user sees the
      // fitted view by the second frame) AND let onEngineStop fire its
      // own fit when the simulation settles. Both paths set
      // ``hasInitialFit`` so a second fit isn't redundant; the early
      // fire just makes the perceived first paint right.
      if (!pinnedNode && !pinnedLink) {
        // 50ms ≈ one frame of simulation cooldown so node positions are
        // populated; then scheduleCenteredFit runs zoomToFit with a
        // short tween so the user sees the camera settle into frame.
        setTimeout(scheduleCenteredFit, 50);
        // Belt-and-braces: also schedule at 350ms in case the first
        // fire ran before the engine had positioned anything.
        setTimeout(scheduleCenteredFit, 350);
      }
      return inst;
    }

    var lastMouseX = 0, lastMouseY = 0;
    container.addEventListener('mousemove', function(e){
      var rect = container.getBoundingClientRect();
      // Tooltip lives in the wrapper (so the Fullscreen API still draws
      // it on top of the canvas). The wrapper is the canvas's offset
      // parent, so we add the canvas's offset to the mouse position so
      // the tooltip lands at cursor.
      lastMouseX = e.clientX - rect.left + container.offsetLeft;
      lastMouseY = e.clientY - rect.top + container.offsetTop;
      if (tooltip && !tooltip.hidden) {
        positionTooltip(lastMouseX, lastMouseY);
      }
    });
    container.addEventListener('mouseleave', hideTooltip);

    // ================================================================
    // Graph View v1 — node-detail drawer (spec §D / §E).
    // A right-side narrative surface that opens on node SELECT (click).
    // It reads everything it needs from the client-side index maps built
    // in ``buildDrawerIndex`` — no per-node payload blobs. The drawer is
    // built lazily (``ensureDrawer``) and its CSS is injected once
    // (``injectDrawerStyles``). All text uses textContent / createElement —
    // never innerHTML — so corpus strings can never inject markup.
    // ================================================================
    var DRAWER_SECTION_MAX = 5; // spec §D — max items per section.
    var _drawerEl = null;
    var _drawerHeaderEl = null;
    var _drawerBodyEl = null;
    var _drawerStylesInjected = false;

    function injectDrawerStyles(){
      if (_drawerStylesInjected) return;
      _drawerStylesInjected = true;
      var css = ''
        + '.graph-drawer{position:absolute;top:0;right:0;bottom:0;width:340px;'
        + 'max-width:86vw;z-index:40;display:flex;flex-direction:column;'
        + 'background:rgba(8,12,22,0.96);color:#e6eaf2;'
        + 'border-left:1px solid rgba(148,163,184,0.18);'
        + 'box-shadow:-12px 0 32px rgba(0,0,0,0.45);'
        + 'transform:translateX(102%);transition:transform 180ms ease;'
        + 'font:13px/1.5 Inter,system-ui,sans-serif;overflow:hidden;}'
        + '.graph-drawer.is-open{transform:translateX(0);}'
        + '.graph-drawer[hidden]{display:none;}'
        + '.graph-drawer-header{padding:16px 18px 12px;'
        + 'border-bottom:1px solid rgba(148,163,184,0.16);}'
        + '.graph-drawer-kicker{font-size:11px;letter-spacing:.06em;'
        + 'text-transform:uppercase;color:#94a3b8;display:flex;gap:8px;'
        + 'align-items:center;flex-wrap:wrap;}'
        + '.graph-drawer-title{margin:6px 0 0;font-size:17px;font-weight:650;'
        + 'color:#f4f6fb;}'
        + '.graph-drawer-importance{color:#cbd5e1;}'
        + '.graph-drawer-source{margin-top:8px;display:inline-block;'
        + 'font-size:11px;padding:2px 8px;border-radius:999px;'
        + 'background:rgba(129,140,248,0.16);color:#c7d2fe;}'
        + '.graph-drawer-body{padding:14px 18px 22px;overflow-y:auto;flex:1;}'
        + '.graph-drawer-lede{color:#cbd5e1;margin:0 0 14px;}'
        + '.graph-drawer-section{margin:0 0 16px;}'
        + '.graph-drawer-section h4{margin:0 0 6px;font-size:11px;'
        + 'letter-spacing:.05em;text-transform:uppercase;color:#8b95a7;}'
        + '.graph-drawer-chip{display:inline-flex;align-items:center;gap:6px;'
        + 'margin:0 6px 6px 0;padding:3px 9px;border-radius:8px;font-size:12px;'
        + 'background:rgba(148,163,184,0.12);color:#dbe3f0;cursor:pointer;'
        + 'border:1px solid rgba(148,163,184,0.14);}'
        + '.graph-drawer-chip.is-semantic{background:rgba(129,140,248,0.18);'
        + 'border-color:rgba(129,140,248,0.28);color:#c7d2fe;}'
        + '.graph-drawer-close{position:absolute;top:12px;right:14px;'
        + 'background:none;border:none;color:#94a3b8;font-size:20px;'
        + 'line-height:1;cursor:pointer;}'
        + '.graph-drawer-close:hover{color:#e6eaf2;}'
        + '.graph-drawer-grouplabel{color:#8b95a7;font-size:11px;'
        + 'margin:2px 0 4px;}';
      var style = document.createElement('style');
      style.setAttribute('data-graph-drawer-styles', '');
      style.textContent = css;
      document.head.appendChild(style);
    }

    function ensureDrawer(){
      if (_drawerEl) return _drawerEl;
      injectDrawerStyles();
      var host = wrapper || container;
      var drawer = document.getElementById('graph-drawer');
      if (!drawer) {
        drawer = document.createElement('aside');
        drawer.id = 'graph-drawer';
        drawer.className = 'graph-drawer';
        drawer.setAttribute('aria-label', 'Node details');
        drawer.setAttribute('role', 'complementary');
        drawer.hidden = true;
        var closeBtn = document.createElement('button');
        closeBtn.type = 'button';
        closeBtn.className = 'graph-drawer-close';
        closeBtn.setAttribute('aria-label', 'Close details');
        closeBtn.textContent = '×';
        closeBtn.addEventListener('click', function(){ closeDrawer(); });
        var header = document.createElement('div');
        header.id = 'graph-drawer-header';
        header.className = 'graph-drawer-header';
        var body = document.createElement('div');
        body.id = 'graph-drawer-body';
        body.className = 'graph-drawer-body';
        drawer.appendChild(closeBtn);
        drawer.appendChild(header);
        drawer.appendChild(body);
        if (host && host.appendChild) host.appendChild(drawer);
      }
      _drawerEl = drawer;
      _drawerHeaderEl = drawer.querySelector('#graph-drawer-header')
        || document.getElementById('graph-drawer-header');
      _drawerBodyEl = drawer.querySelector('#graph-drawer-body')
        || document.getElementById('graph-drawer-body');
      return drawer;
    }

    function _clearEl(el){
      if (!el) return;
      while (el.firstChild) el.removeChild(el.firstChild);
    }

    // Body lede fallback ladder (spec §D):
    //   abstract -> description -> evidence -> metadata_summary -> name[:240]
    function drawerLede(n){
      if (!n) return '';
      var txt = n.abstract
        || n.description
        || n.evidence
        || n.metadata_summary
        || (n.name || n.id || '');
      txt = String(txt);
      return txt.length > 240 ? txt.slice(0, 240) + '…' : txt;
    }

    function renderDrawerHeader(n){
      ensureDrawer();
      _clearEl(_drawerHeaderEl);
      if (!n) return;
      var fam = familyOf(n);
      var kicker = document.createElement('div');
      kicker.className = 'graph-drawer-kicker';
      var famSpan = document.createElement('span');
      famSpan.textContent = FAMILY_LABELS[fam] || fam;
      famSpan.style.color = FAMILY_COLORS[fam] || FAMILY_COLORS.other;
      kicker.appendChild(famSpan);
      if (n.type) {
        var typeSpan = document.createElement('span');
        typeSpan.textContent = '· ' + n.type;
        kicker.appendChild(typeSpan);
      }
      var imp = (typeof n.importance === 'number') ? n.importance : null;
      if (imp !== null) {
        var impSpan = document.createElement('span');
        impSpan.className = 'graph-drawer-importance';
        impSpan.textContent = '· importance ' + imp;
        kicker.appendChild(impSpan);
      }
      var title = document.createElement('h3');
      title.className = 'graph-drawer-title';
      title.textContent = n.name || n.id || '';
      _drawerHeaderEl.appendChild(kicker);
      _drawerHeaderEl.appendChild(title);
      var src = n.source_path;
      if (src) {
        var pill = document.createElement('span');
        pill.className = 'graph-drawer-source';
        pill.textContent = src;
        _drawerHeaderEl.appendChild(pill);
      }
    }

    function openDrawer(node){
      if (!node) return;
      ensureDrawer();
      renderDrawerHeader(node);
      _clearEl(_drawerBodyEl);
      var lede = drawerLede(node);
      if (lede) {
        var p = document.createElement('p');
        p.className = 'graph-drawer-lede';
        p.textContent = lede;
        _drawerBodyEl.appendChild(p);
      }
      renderDrawerSections(node);
      _drawerEl.hidden = false;
      try { requestAnimationFrame(function(){ _drawerEl.classList.add('is-open'); }); }
      catch (_) { _drawerEl.classList.add('is-open'); }
    }

    function closeDrawer(){
      if (!_drawerEl) return;
      _drawerEl.classList.remove('is-open');
      _drawerEl.hidden = true;
    }

    // Section host — Task 5 fills this with the typed sections. Defined here
    // as a self-contained shell so Task 4's openDrawer works standalone.
    function renderDrawerSections(node){
      if (!node || !_drawerBodyEl) return;
      _renderDrawerSectionsImpl(node);
    }
    // ----------------------------------------------------------------
    // Task 5 — typed drawer sections (spec §D). Each section renders ONLY
    // when it has content, caps at DRAWER_SECTION_MAX (5) items, and uses
    // grouped chips — never a raw 46-edge dump. Chips are clickable and
    // re-focus the neighbour node.
    // ----------------------------------------------------------------
    function _otherEnd(link, nodeId){
      var s = _linkEndId(link.source);
      var t = _linkEndId(link.target);
      var otherId = (s === nodeId) ? t : s;
      return nodeById.get(otherId) || null;
    }
    function _humanType(t){
      return String(t || 'related').replace(/_/g, ' ');
    }
    function _makeSectionEl(title){
      var sec = document.createElement('div');
      sec.className = 'graph-drawer-section';
      var h = document.createElement('h4');
      h.textContent = title;
      sec.appendChild(h);
      return sec;
    }
    function _makeNodeChip(node, semantic){
      var chip = document.createElement('button');
      chip.type = 'button';
      chip.className = 'graph-drawer-chip' + (semantic ? ' is-semantic' : '');
      chip.textContent = node.name || node.id || '';
      chip.addEventListener('click', function(){ activateNode(node); });
      return chip;
    }
    function _appendIfNonEmpty(sec, addedCount){
      if (addedCount > 0) _drawerBodyEl.appendChild(sec);
    }

    // Why it matters — importance explanation + top incident SEMANTIC edges.
    function renderWhyItMatters(node){
      var sec = _makeSectionEl('Why it matters');
      var added = 0;
      var imp = (typeof node.importance === 'number') ? node.importance : null;
      if (imp !== null) {
        var p = document.createElement('p');
        p.className = 'graph-drawer-grouplabel';
        var why = (node.type === 'CommunitySummary')
          ? ('Summarises ' + (node.member_count || imp) + ' members.')
          : ('Importance ' + imp + ' (connectivity / fan-in).');
        p.textContent = why;
        sec.appendChild(p);
        added += 1;
      }
      var incident = incidentLinksByNode.get(node.id) || [];
      var semantic = incident.filter(function(l){ return edgeClassOf(l) === 'semantic'; });
      var n = 0;
      for (var i = 0; i < semantic.length && n < DRAWER_SECTION_MAX; i++) {
        var other = _otherEnd(semantic[i], node.id);
        if (!other) continue;
        sec.appendChild(_makeNodeChip(other, true));
        n += 1; added += 1;
      }
      _appendIfNonEmpty(sec, added);
    }

    // Evidence / context — EvidenceSpan, claims, source document, path.
    function renderEvidence(node){
      var sec = _makeSectionEl('Evidence & context');
      var added = 0;
      if (node.evidence) {
        var p = document.createElement('p');
        p.className = 'graph-drawer-lede';
        var ev = String(node.evidence);
        p.textContent = ev.length > 240 ? ev.slice(0, 240) + '…' : ev;
        sec.appendChild(p);
        added += 1;
      }
      var incident = incidentLinksByNode.get(node.id) || [];
      var n = 0;
      for (var i = 0; i < incident.length && n < DRAWER_SECTION_MAX; i++) {
        var other = _otherEnd(incident[i], node.id);
        if (!other) continue;
        var fam = familyOf(other);
        var t = other.type || '';
        var isEvidence = (fam === 'claims') || t === 'EvidenceSpan'
          || t === 'SourceDocument';
        if (!isEvidence) continue;
        sec.appendChild(_makeNodeChip(other, false));
        n += 1; added += 1;
      }
      _appendIfNonEmpty(sec, added);
    }

    // Related — top neighbours grouped by edge class + type.
    function groupRelated(node){
      var incident = incidentLinksByNode.get(node.id) || [];
      var groups = {}; // key "class:type" -> { cls, type, items: [] }
      for (var i = 0; i < incident.length; i++) {
        var l = incident[i];
        var other = _otherEnd(l, node.id);
        if (!other) continue;
        var cls = edgeClassOf(l);
        var type = l.type || l.relation || 'related';
        var key = cls + ':' + type;
        if (!groups[key]) groups[key] = { cls: cls, type: type, items: [] };
        if (groups[key].items.length < DRAWER_SECTION_MAX) {
          groups[key].items.push(other);
        }
      }
      return groups;
    }
    function renderRelated(node){
      var sec = _makeSectionEl('Related');
      var groups = groupRelated(node);
      var keys = Object.keys(groups);
      // Semantic groups first (more narrative value), then structural.
      keys.sort(function(a, b){
        var ca = groups[a].cls === 'semantic' ? 0 : 1;
        var cb = groups[b].cls === 'semantic' ? 0 : 1;
        return ca - cb;
      });
      var added = 0;
      for (var k = 0; k < keys.length; k++) {
        var g = groups[keys[k]];
        var lbl = document.createElement('div');
        lbl.className = 'graph-drawer-grouplabel';
        lbl.textContent = _humanType(g.type) + ' (' + g.cls + ')';
        sec.appendChild(lbl);
        var items = g.items.slice(0, DRAWER_SECTION_MAX);
        for (var j = 0; j < items.length; j++) {
          sec.appendChild(_makeNodeChip(items[j], g.cls === 'semantic'));
        }
        added += 1;
      }
      _appendIfNonEmpty(sec, added);
    }

    // Session memory — incident discussed_in / references / discusses /
    // supersedes edges.
    function renderSessionMemory(node){
      var SESSION_TYPES = { discussed_in: 1, references: 1, discusses: 1, supersedes: 1 };
      var sec = _makeSectionEl('Session memory');
      var incident = incidentLinksByNode.get(node.id) || [];
      var added = 0, n = 0;
      for (var i = 0; i < incident.length && n < DRAWER_SECTION_MAX; i++) {
        var l = incident[i];
        var type = l.type || l.relation || '';
        if (!SESSION_TYPES[type]) continue;
        var other = _otherEnd(l, node.id);
        if (!other) continue;
        sec.appendChild(_makeNodeChip(other, true));
        n += 1; added += 1;
      }
      _appendIfNonEmpty(sec, added);
    }

    // Code — file / module / path + callers + callees (code nodes only).
    function renderCodeSection(node){
      if (familyOf(node) !== 'code') return;
      var sec = _makeSectionEl('Code');
      var added = 0;
      if (node.source_path) {
        var lbl = document.createElement('div');
        lbl.className = 'graph-drawer-grouplabel';
        lbl.textContent = node.source_path;
        sec.appendChild(lbl);
        added += 1;
      }
      var inc = incomingByType.get(node.id) || {};
      var out = outgoingByType.get(node.id) || {};
      var callers = inc['calls'] || [];
      var callees = out['calls'] || [];
      if (callers.length) {
        var cl = document.createElement('div');
        cl.className = 'graph-drawer-grouplabel';
        cl.textContent = 'Callers';
        sec.appendChild(cl);
        for (var i = 0; i < callers.length && i < DRAWER_SECTION_MAX; i++) {
          var caller = _otherEnd(callers[i], node.id);
          if (caller) { sec.appendChild(_makeNodeChip(caller, false)); added += 1; }
        }
      }
      if (callees.length) {
        var cle = document.createElement('div');
        cle.className = 'graph-drawer-grouplabel';
        cle.textContent = 'Callees';
        sec.appendChild(cle);
        for (var j = 0; j < callees.length && j < DRAWER_SECTION_MAX; j++) {
          var callee = _otherEnd(callees[j], node.id);
          if (callee) { sec.appendChild(_makeNodeChip(callee, false)); added += 1; }
        }
      }
      _appendIfNonEmpty(sec, added);
    }

    // Community — summarised members (CommunitySummary) OR parent summaries
    // (for members reached via an incoming ``summarizes`` edge).
    function renderCommunity(node){
      var sec = _makeSectionEl('Community');
      var added = 0;
      var out = outgoingByType.get(node.id) || {};
      var inc = incomingByType.get(node.id) || {};
      if (node.type === 'CommunitySummary') {
        var members = out['summarizes'] || [];
        var head = document.createElement('div');
        head.className = 'graph-drawer-grouplabel';
        head.textContent = 'Members (' + members.length + ')';
        sec.appendChild(head);
        for (var i = 0; i < members.length && i < DRAWER_SECTION_MAX; i++) {
          var m = _otherEnd(members[i], node.id);
          if (m) { sec.appendChild(_makeNodeChip(m, true)); added += 1; }
        }
      } else {
        var parents = inc['summarizes'] || [];
        if (parents.length) {
          var ph = document.createElement('div');
          ph.className = 'graph-drawer-grouplabel';
          ph.textContent = 'Summarised by';
          sec.appendChild(ph);
          for (var p = 0; p < parents.length && p < DRAWER_SECTION_MAX; p++) {
            var parent = _otherEnd(parents[p], node.id);
            if (parent) { sec.appendChild(_makeNodeChip(parent, true)); added += 1; }
          }
        }
      }
      _appendIfNonEmpty(sec, added);
    }

    function _renderDrawerSectionsImpl(node){
      renderWhyItMatters(node);
      renderEvidence(node);
      renderRelated(node);
      renderSessionMemory(node);
      if (familyOf(node) === 'code') renderCodeSection(node);
      renderCommunity(node);
    }

    function activateNode(node, evt){
      if (!node) return;
      if (isDimmedNode(node)) return;
      // F-1 — clicking a node counts as user interaction; the rest-merge
      // re-fit is suppressed once the user has taken control.
      userInteracted = true;
      // Issue 6 — manual node click counts as user interruption: stop
      // the auto-browse tour so the user is back in the driver's seat.
      if (autoBrowseActive) stopAutoBrowse();
      var samePinned = pinnedNode && nodeIdOf(pinnedNode) === nodeIdOf(node);
      // Graph browsing comes first: first tap/click pins, highlights neighbors,
      // and zooms to the entity. A second activation on the same pinned node
      // opens its detail page. Ctrl/⌘-click always behaves as "focus only".
      if (evt && (evt.metaKey || evt.ctrlKey)) samePinned = false;
      if (!samePinned) {
        pinnedNode = node;
        pinnedLink = null;
        focusedNode = node;
        markFocused(node);
        applyHighlight(node);
        // F-5 — populate the floating focus-detail panel so the user can
        // read the full title/type/degree/description and open the page.
        // Hover tooltip (cursor-following) hides because focus replaces
        // its purpose for this node.
        hideTooltip();
        populateFocusPanel(node);
        // v1.1 — the typed node-detail drawer is the narrative surface for a
        // selected node (spec §D). Task 4 built openDrawer + the client-side
        // index but never wired the call site, so the drawer was unreachable
        // dead code. Open it on select; closeDrawer() is already wired to the
        // drawer close button + Esc/background unfocus.
        openDrawer(node);
        focusOnNode(node);
        return;
      }
      if (node.href) window.location.href = node.href;
    }

    function activateLink(link, evt){
      if (!link) return;
      if (isDimmedLink(link)) return;
      userInteracted = true;
      var samePinned = pinnedLink && linkKey(pinnedLink) === linkKey(link);
      if (evt && (evt.metaKey || evt.ctrlKey)) samePinned = false;
      if (!samePinned) {
        pinnedNode = null;
        pinnedLink = link;
        applyLinkHighlight(link);
        hideTooltip();
        focusOnLink(link);
        return;
      }
      var endpoints = linkEndpoints(link);
      var target = endpoints.target || endpoints.source;
      if (target && target.href) window.location.href = target.href;
    }

    function focusOnNode(node, flyMs){
      if (!Graph) { focusFallbackNode(node); return; }
      // Bug 5 — camera orbits the focused node. Compute world position,
      // park the camera 200 units off in +Z, set controls.target so
      // subsequent orbit/pan revolves around the focused node (not the
      // world origin). The auto-orbit hook in onEngineTick reads
      // ``focusedNode`` + ``orbitAngle`` to spin the camera around it.
      // ``var distance = 300`` keeps the existing shape so the regression
      // test (test_graph_focus_zoom_is_moderate) still passes — the
      // effective camera offset for the orbit is the smaller ``orbitRadius``
      // computed from the node's degree below.
      var distance = 300;
      if (mode === '3d' && Graph.cameraPosition && node && node.x !== undefined) {
        // Frame the focused node AND its 1-hop neighbors. Compute a
        // bounding sphere over the (focused + neighbors) set so the
        // camera distance grows with the cluster size — a hub with
        // 30 neighbors gets framed wider than a leaf with one. Falls
        // back to the previous "200u offset on +Z" behavior when the
        // node has no positioned neighbors yet.
        var nx = node.x || 0, ny = node.y || 0, nz = node.z || 0;
        var minX = nx, minY = ny, minZ = nz;
        var maxX = nx, maxY = ny, maxZ = nz;
        var neighborCount = 0;
        try {
          // ``node.neighbors`` is a Set of neighbor refs built at graph
          // load time (line ~840: ``a.neighbors.add(b); b.neighbors.add(a)``).
          if (node.neighbors && node.neighbors.forEach) {
            node.neighbors.forEach(function(nb){
              if (!nb || nb.x === undefined) return;
              if (nb.x < minX) minX = nb.x;
              if (nb.x > maxX) maxX = nb.x;
              if (nb.y < minY) minY = nb.y;
              if (nb.y > maxY) maxY = nb.y;
              if (nb.z < minZ) minZ = nb.z;
              if (nb.z > maxZ) maxZ = nb.z;
              neighborCount++;
            });
          }
        } catch (_) {}
        var radius = Math.sqrt((node && node.val) || 1);
        // Bounding sphere radius from the cluster spread. The user has
        // bounced between "too close" and "too far"; settling on a
        // reasonable middle ground: cluster spread × 0.75 so neighbours
        // fit comfortably with breathing room, floor 130 so a leaf
        // with no neighbours still sits at a comfortable reading distance.
        var spread = Math.max(
          Math.hypot(maxX - minX, maxY - minY, maxZ - minZ) * 0.75,
          Math.max(80 + radius * 12, 130)
        );
        orbitRadius = spread;
        orbitAngle = 0;
        autoOrbitEnabled = true;
        // Camera target is the cluster centroid, not just the focused
        // node — so the camera frames the whole neighborhood evenly.
        var cx = (minX + maxX) / 2;
        var cy = (minY + maxY) / 2;
        var cz = (minZ + maxZ) / 2;
        // F-3 — store the cluster centroid as the orbit target so the
        // ``onEngineTick`` auto-orbit hook orbits the SAME point the
        // cameraPosition tween framed. Otherwise the tick uses
        // ``focusedNode.x/y/z`` (the focused node, not the centroid)
        // and the camera drifts off the framed cluster on the very next
        // render frame. The deferred ``controls.target.set(cx,cy,cz)``
        // below also snaps to this same centroid so all three (initial
        // fly-to, deferred snap, per-frame orbit) agree.
        orbitTarget = { x: cx, y: cy, z: cz };
        // Animate to a position ``orbitRadius`` units in +Z from the
        // centroid, looking at the centroid. ``flyMs`` defaults to 600
        // (snappy click-focus) but auto-browse passes a longer value
        // (1400ms) for a cinematic ease in/out between tour stops.
        var animMs = reduceMotion ? 0 : (flyMs || 600);
        try {
          Graph.cameraPosition(
            { x: cx, y: cy, z: cz + orbitRadius },
            { x: cx, y: cy, z: cz },
            animMs
          );
        } catch (_) {}
        // OrbitControls target snap MUST run AFTER the cameraPosition
        // tween completes — calling controls.target.set + update()
        // synchronously fights the tween and snaps the camera to its
        // final state instantly (no visible animation). Defer.
        var setOrbitTarget = function(){
          try {
            var controls = Graph.controls && Graph.controls();
            if (controls && controls.target && controls.target.set) {
              controls.target.set(cx, cy, cz);
              if (controls.update) controls.update();
            }
          } catch (_) {}
        };
        if (animMs > 0) {
          window.setTimeout(setOrbitTarget, animMs + 20);
        } else {
          setOrbitTarget();
        }
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

    // ---- Fullscreen (Issue 4) ------------------------------------------
    // Request fullscreen on the WRAPPER (not the canvas) so the toolbar,
    // legend, focus panel, tooltip, and help popover all come along.
    // Listen to ``fullscreenchange`` to toggle the ``is-fullscreen``
    // class so CSS can repaint the layout.
    function toggleGraphFullscreen(){
      if (!wrapper) return;
      var fsEl = document.fullscreenElement || document.webkitFullscreenElement || null;
      if (fsEl) {
        try { (document.exitFullscreen || document.webkitExitFullscreen).call(document); } catch (_) {}
        return;
      }
      try {
        var req = wrapper.requestFullscreen || wrapper.webkitRequestFullscreen;
        if (req) req.call(wrapper);
      } catch (err) {
        console.warn('graph: fullscreen request failed', err);
      }
    }
    document.addEventListener('fullscreenchange', function(){
      if (!wrapper) return;
      var fsEl = document.fullscreenElement || null;
      var on = !!fsEl && (fsEl === wrapper || (fsEl.contains && fsEl.contains(wrapper)));
      wrapper.classList.toggle('is-fullscreen', on);
      if (btnFullscreen) {
        btnFullscreen.setAttribute('aria-pressed', on ? 'true' : 'false');
        btnFullscreen.textContent = on ? 'Exit fullscreen' : 'Fullscreen';
      }
      // Re-fit to the new container dimensions on the next frame so the
      // canvas picks up the new wrapper size (NOT viewport — the wrapper
      // covers the viewport in fullscreen mode).
      if (Graph) {
        try { sizeGraphToContainer(Graph); } catch (_) {}
        window.setTimeout(function(){
          try { sizeGraphToContainer(Graph); } catch (_) {}
        }, 60);
      }
    });
    if (btnFullscreen) btnFullscreen.addEventListener('click', toggleGraphFullscreen);

    if (btn2D) btn2D.addEventListener('click', function(){ setMode('2d'); });
    if (btn3D) btn3D.addEventListener('click', function(){ setMode('3d'); });
    if (btnFit) btnFit.addEventListener('click', function(){ fitAll(400); });
    if (btnReset) btnReset.addEventListener('click', function(){
      stopAutoBrowse();
      pinnedNode = null;
      pinnedLink = null;
      focusedNode = null;
      markFocused(null);
      autoOrbitEnabled = false;
      applyHighlight(null);
      clearInfoPanel();
      // F-10 — Reset (and the ``r`` keyboard shortcut, which dispatches
      // this same click) re-frames ALL visible nodes via the same
      // bounding-sphere math as the Fit button. The previous code
      // hard-coded ``z = 400`` which ignored the rest payload's expanded
      // layout and left the user staring at empty space whenever the
      // graph extended past the canonical box. ``fitAll`` is the only
      // call that actually re-frames whatever is visible right now.
      if (Graph) {
        try { fitAll(reduceMotion ? 0 : 600); } catch (_) {}
      }
    });

    // ---- Issue 6 — Auto-browse mode -----------------------------------
    // Click ``Auto-browse`` (or press ``b``) to enter a hands-free tour:
    //   1. Pick the highest-degree node, focus it via focusOnNode (so all
    //      the orbit + dim + label scaling kicks in), wait 5s.
    //   2. Pick that node's most-connected unvisited neighbor, focus it,
    //      wait 5s.
    //   3. After 8 hops or when no unvisited neighbors are reachable,
    //      jump to the next-highest-degree non-visited node and start
    //      a new chain.
    //   Stop on: click ``Stop browse``, Esc, manual node click, drag.
    //   Cancellation via ``autoBrowseActive`` flag + setTimeout id.
    var autoBrowseActive = false;
    var autoBrowseTimer = null;
    var autoBrowseVisited = null;
    var autoBrowseHopCount = 0;
    // Dwell longer per node so the user has time to read the focused
    // label and absorb the neighbours. The auto-orbit pipeline already
    // gives a slow ~31s/revolution rotation; with a 9s dwell the camera
    // sweeps ~100° around the focused node before flying to the next.
    var AUTO_BROWSE_DWELL_MS = 9000;
    // Camera fly-to between auto-browse stops uses this duration. The
    // existing focusOnNode() animates with reduceMotion ? 0 : 600ms.
    // Having the dwell at 9000 means a clear "settle → orbit → fly"
    // cadence: ~600ms fly, then 8.4s of orbit at the new node.
    var AUTO_BROWSE_MAX_HOPS = 8;

    function setAutoBrowseUI(on){
      if (btnAutoBrowse) {
        btnAutoBrowse.textContent = on ? 'Stop browse' : 'Auto-browse';
        btnAutoBrowse.setAttribute('aria-pressed', on ? 'true' : 'false');
        btnAutoBrowse.classList.toggle('is-active', !!on);
      }
      if (wrapper && wrapper.classList) {
        wrapper.classList.toggle('is-auto-browsing', !!on);
      }
    }
    function pickStartNode(){
      // Highest-degree non-visited node.
      var best = null;
      var bestDeg = -1;
      for (var i = 0; i < payload.nodes.length; i++) {
        var n = payload.nodes[i];
        if (autoBrowseVisited && autoBrowseVisited.has(n.id)) continue;
        if (isDimmedNode(n)) continue;
        var d = n.degree || 0;
        if (d > bestDeg) { bestDeg = d; best = n; }
      }
      return best;
    }
    function pickNextNeighbor(node){
      if (!node || !node.neighbors) return null;
      var best = null;
      var bestDeg = -1;
      // Iterate the Set without spreading — preserves Map/Set semantics
      // and avoids allocating an array.
      var arr = [];
      node.neighbors.forEach(function(nb){ arr.push(nb); });
      for (var i = 0; i < arr.length; i++) {
        var nb = arr[i];
        if (!nb) continue;
        if (autoBrowseVisited && autoBrowseVisited.has(nb.id)) continue;
        var d = nb.degree || 0;
        if (d > bestDeg) { bestDeg = d; best = nb; }
      }
      return best;
    }
    // Sort a node's neighbors by importance (degree) descending,
    // skipping already-visited ones. Returns up to ``limit`` refs.
    function topImportantNeighbors(node, limit){
      if (!node || !node.neighbors) return [];
      var arr = [];
      node.neighbors.forEach(function(nb){
        if (!nb) return;
        if (autoBrowseVisited && autoBrowseVisited.has(nb.id)) return;
        if (isDimmedNode(nb)) return;
        arr.push(nb);
      });
      arr.sort(function(a, b){ return (b.degree || 0) - (a.degree || 0); });
      return arr.slice(0, limit || 5);
    }

    // The user asked: tour the IMPORTANT connected nodes around the
    // current focused node before jumping to the next starting node.
    // The new step pipeline:
    //   1. Focus the seed node, dwell longer (AUTO_BROWSE_DWELL_MS).
    //   2. Visit its top-N most-important unvisited neighbors in
    //      sequence, each with a shorter dwell (AUTO_BROWSE_NB_DWELL_MS).
    //   3. After the neighbour tour, pick a fresh starting node and
    //      repeat. ``AUTO_BROWSE_MAX_HOPS`` is no longer the limit;
    //      we let the per-seed neighbour list do the bounding.
    var AUTO_BROWSE_NB_DWELL_MS = 5000;        // shorter than seed dwell
    var AUTO_BROWSE_NEIGHBOURS_PER_SEED = 5;
    var autoBrowseSeedQueue = null;            // pending neighbours of current seed
    function autoBrowseStep(seedNode){
      if (!autoBrowseActive) return;
      if (!seedNode) {
        // Out of unvisited seeds — stop cleanly.
        stopAutoBrowse();
        return;
      }
      autoBrowseVisited.add(seedNode.id);
      // Cinematic fly-to the seed: 1400ms ease in/out between stops.
      try { focusOnNode(seedNode, 1400); } catch (_) {}
      pinnedNode = seedNode;
      focusedNode = seedNode;
      markFocused(seedNode);
      applyHighlight(seedNode);
      populateFocusPanel(seedNode);
      // Pre-compute the neighbour tour list for this seed.
      autoBrowseSeedQueue = topImportantNeighbors(seedNode, AUTO_BROWSE_NEIGHBOURS_PER_SEED);
      autoBrowseTimer = window.setTimeout(function(){
        autoBrowseVisitNextNeighbour(seedNode);
      }, AUTO_BROWSE_DWELL_MS);
    }
    function autoBrowseVisitNextNeighbour(seedNode){
      if (!autoBrowseActive) return;
      if (autoBrowseSeedQueue && autoBrowseSeedQueue.length > 0) {
        var nb = autoBrowseSeedQueue.shift();
        autoBrowseVisited.add(nb.id);
        // Shorter, snappier fly to the neighbour (900ms).
        try { focusOnNode(nb, 900); } catch (_) {}
        pinnedNode = nb;
        focusedNode = nb;
        markFocused(nb);
        applyHighlight(nb);
        populateFocusPanel(nb);
        autoBrowseTimer = window.setTimeout(function(){
          autoBrowseVisitNextNeighbour(seedNode);
        }, AUTO_BROWSE_NB_DWELL_MS);
        return;
      }
      // Neighbour tour done — pick a fresh seed.
      var fresh = pickStartNode();
      if (!fresh) { stopAutoBrowse(); return; }
      autoBrowseStep(fresh);
    }
    function startAutoBrowse(){
      if (autoBrowseActive) return;
      // Gate the tour on the rest payload so the start-node picker sees
      // the union — without this we'd seed off the core's local maximum
      // and the tour would never visit anything outside the top 150.
      if (!window.__graphRestLoaded) {
        var waited = 0;
        var interval = setInterval(function(){
          waited += 100;
          if (window.__graphRestLoaded) {
            clearInterval(interval);
            startAutoBrowse();
          } else if (waited > 15000) {
            // Rest payload never arrived — fall back to a core-only tour
            // so the button isn't permanently dead. Logged once.
            clearInterval(interval);
            console.warn('graph: starting auto-browse on core-only (rest payload timeout)');
            window.__graphRestLoaded = true;
            startAutoBrowse();
          }
        }, 100);
        return;
      }
      autoBrowseActive = true;
      autoBrowseVisited = new Set();
      autoBrowseHopCount = 0;
      setAutoBrowseUI(true);
      var first = pickStartNode();
      autoBrowseStep(first);
    }
    function stopAutoBrowse(){
      if (!autoBrowseActive && !autoBrowseTimer) {
        // Even when never started, make sure UI is clean (idempotent).
        setAutoBrowseUI(false);
        return;
      }
      autoBrowseActive = false;
      if (autoBrowseTimer) { window.clearTimeout(autoBrowseTimer); autoBrowseTimer = null; }
      autoBrowseVisited = null;
      autoBrowseHopCount = 0;
      setAutoBrowseUI(false);
    }
    function toggleAutoBrowse(){
      if (autoBrowseActive) stopAutoBrowse();
      else startAutoBrowse();
    }
    if (btnAutoBrowse) btnAutoBrowse.addEventListener('click', toggleAutoBrowse);

    // F-5 — close button inside the floating focus-detail panel mirrors
    // the unfocus action of background-click / Esc / right-click. Lets
    // touch users dismiss the focus state without having to find empty
    // canvas to tap on.
    var btnFocusUnfocus = document.querySelector('[data-graph-action="unfocus"]');
    if (btnFocusUnfocus) {
      btnFocusUnfocus.addEventListener('click', function(){
        pinnedNode = null;
        pinnedLink = null;
        focusedNode = null;
        try { markFocused(null); } catch (_) {}
        autoOrbitEnabled = false;
        applyHighlight(null);
        clearInfoPanel();
        closeDrawer();
      });
    }

    // Issue 1 — re-tint label sprites when the user toggles theme. Since
    // sprites are cached by ``text|variant|accent|theme|hint`` and baked
    // onto a canvas, a theme flip means we need to clear the cache and
    // rebuild the per-node nodeThreeObject group. The cheapest route is
    // to swap out the renderer (rebuild via setMode shim) — but that's
    // expensive. Cheaper: invalidate the cache and re-poke the library's
    // nodeThreeObject accessor so it re-runs the factory per node.
    window.__graphRefreshLabels = function(){
      try { labelSpriteCache.clear(); } catch (_) {}
      if (!Graph) return;
      try {
        if (Graph.nodeThreeObject) Graph.nodeThreeObject(Graph.nodeThreeObject());
        if (Graph.linkThreeObject) Graph.linkThreeObject(Graph.linkThreeObject());
        if (Graph.refresh) Graph.refresh();
      } catch (_) {}
    };
    if (searchEl) {
      searchEl.addEventListener('input', function(){
        // F-8 — searchQuery now dim-filters non-matching nodes instead of
        // hiding them. ``refreshVisibility`` re-pokes the node/link
        // colour accessors so the dim updates immediately as the user
        // types.
        searchQuery = (searchEl.value || '').trim().toLowerCase();
        userInteracted = true;
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
            focusedNode = match;
            markFocused(match);
            applyHighlight(match);
            populateFocusPanel(match);
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
      // F-11 — ``?`` toggles the help popover. ``e.key`` already
      // resolves shift+/ to the literal ``?`` character on every
      // keyboard layout the browser knows about.
      if (e.key === '?') { e.preventDefault(); toggleHelpOpen(); return; }
      if (e.key === 'f') { fitAll(400); }
      if (e.key === 'r') { if (btnReset) btnReset.click(); }
      // Bug 5 — ``o`` toggles auto-orbit. Default is ON when a node is
      // focused, OFF otherwise. The toggle re-arms the orbit even after
      // the user has dragged manually (which clears autoOrbitEnabled).
      if (e.key === 'o') {
        autoOrbitEnabled = !autoOrbitEnabled;
        // If enabling without a focus, the orbit hook is a no-op anyway,
        // so the toggle stays harmless.
      }
      // Issue 6 — ``b`` toggles auto-browse mode.
      if (e.key === 'b') { toggleAutoBrowse(); }
      if (e.key === '2') setMode('2d');
      if (e.key === '3') setMode('3d');
      // Issue 2 + Issue 3 — Enter on the focused node opens its page.
      // The visible hint under the focused label is gone, but the key
      // binding is preserved so power-users still get the shortcut.
      if (e.key === 'Enter' && focusedNode && focusedNode.href) {
        e.preventDefault();
        window.location.href = focusedNode.href;
      }
      if (e.key === 'Escape') {
        // Bug 5 — Esc unfocuses, clears search/day filter, then auto-fits
        // back to the whole graph so the user gets visual confirmation
        // they're back at the top level. Issue 6 — Esc also stops the
        // auto-browse tour if one is running. F-11 — Esc also closes
        // the help popover if it's open.
        if (wrapper && wrapper.hasAttribute('data-graph-help-open')) {
          setHelpOpen(false);
        }
        if (autoBrowseActive) stopAutoBrowse();
        pinnedNode = null;
        pinnedLink = null;
        focusedNode = null;
        markFocused(null);
        autoOrbitEnabled = false;
        applyHighlight(null);
        clearInfoPanel();
        closeDrawer();
        dayFilter = null;
        if (searchEl) { searchEl.value = ''; searchQuery = ''; }
        refreshVisibility();
        // Animate back to fit (one-shot; not the auto-fit guard).
        try { fitAll(600); } catch (_) {}
      }
    });

    // ---- Rest-payload merge. Called from outside the startGraph closure
    //      once ``payload-rest.json`` lands. Folds new nodes/links into the
    //      running graph via ``Graph.graphData(...)`` so the rest fade in
    //      alongside the core. The simulation re-settles naturally as new
    //      edges enter.
    window.__graphMergeRestPayload = function(rest){
      if (!rest || (!rest.nodes && !rest.links)) return;
      var newNodes = Array.isArray(rest.nodes) ? rest.nodes : [];
      var newLinks = Array.isArray(rest.links) ? rest.links : (rest.edges || []);
      newNodes.forEach(function(n){
        if (byId.has(n.id)) return;
        n.color = n.color || nodeColorVariant(n);
        n.neighbors = new Set();
        n.edges = [];
        n.degree = 0;
        byId.set(n.id, n);
        payload.nodes.push(n);
      });
      newLinks.forEach(function(l){
        var a = byId.get(typeof l.source === 'object' ? l.source.id : l.source);
        var b = byId.get(typeof l.target === 'object' ? l.target.id : l.target);
        if (!a || !b) return;
        a.neighbors.add(b); b.neighbors.add(a);
        a.edges.push(l); b.edges.push(l);
        a.degree += 1; b.degree += 1;
        payload.links.push(l);
      });
      if (Graph && Graph.graphData) {
        try { Graph.graphData({ nodes: payload.nodes, links: payload.links }); } catch (_) {}
      }
      // codex P2 — the drawer index is built once at load off the CORE
      // payload. The rest merge just appended to payload.nodes/.links, so
      // without this rebuild the drawer would render empty/partial sections
      // for any rest node (or core node whose incident edges arrived in the
      // rest payload). Rebuild now that the union is in place.
      try { buildDrawerIndex(); } catch (_) {}
      // F-2 — rebuild the legend from the union (core + rest) so the
      // type counts and chips reflect the WHOLE graph, not just the
      // core subgraph that startGraph saw. ``hiddenGroups`` is preserved
      // across the rebuild so any user-toggled-off chips stay off.
      rebuildLegend();
      // B2 — bridges might only live in the rest payload, so reveal /
      // hide the toolbar toggle once the union is known.
      try { syncCrossProjectToggleVisibility(); } catch (_) {}
      // F-1 — the rest payload added new nodes and links to the live
      // simulation, which will redistribute the existing layout outside
      // the camera's currently-fitted view. Re-frame the union once,
      // unless the user has already interacted (clicked a node, dragged
      // the canvas, typed into search) — in which case stealing the
      // camera back would feel hostile. We schedule the fit slightly
      // after the next engine settle so the new nodes have positions.
      if (!userInteracted && !pinnedNode && !pinnedLink && !focusedNode) {
        try {
          // Allow the simulation to absorb the new nodes/links over a
          // short settle window, then re-fit. ``fitAll(600)`` reuses the
          // bounding-sphere math the manual Fit button uses, so we get
          // the same framing the user would on demand.
          window.setTimeout(function(){
            if (userInteracted || pinnedNode || pinnedLink || focusedNode) return;
            // Reset the single-shot flag and call scheduleCenteredFit so
            // the existing onEngineStop guard reads "still needs fit".
            hasInitialFit = false;
            try { scheduleCenteredFit(); } catch (_) {}
          }, 400);
        } catch (_) {}
      }
    };

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
            window.__graphCoreLoaded = true;
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

    // Two-stage payload fetch. The graph route ships ``payload-core.json``
    // (top-degree subgraph; ~25 KB gzipped) and ``payload-rest.json``
    // (everything else). We render core first so the user sees a graph
    // almost immediately, then merge the rest in via
    // ``forceGraph.graphData`` once it lands. The legacy combined
    // ``payload.json`` is still emitted for backward compatibility but the
    // route no longer fetches it on the happy path.
    var payloadUrl = container.getAttribute('data-payload-url') || 'payload.json';
    var coreUrl = container.getAttribute('data-payload-core-url') || 'payload-core.json';
    var restUrl = container.getAttribute('data-payload-rest-url') || 'payload-rest.json';
    var loadingNote = document.getElementById('graph-loading-rest');
    function setRestLoading(on){
      if (!loadingNote) return;
      // Toggle ``hidden`` (works without any CSS) and ``is-visible`` (so a
      // future stylesheet hook can fade/animate the chip in).
      if (on) {
        loadingNote.hidden = false;
        loadingNote.classList.add('is-visible');
      } else {
        loadingNote.hidden = true;
        loadingNote.classList.remove('is-visible');
      }
    }
    // ``window.__graphRestLoaded`` gates the auto-browse start path so the
    // tour visits actual top-degree nodes (not just core's local maximum).
    window.__graphRestLoaded = false;
    fetch(coreUrl)
      .then(function(r){
        if (!r.ok) throw new Error('HTTP ' + r.status + ' while loading ' + coreUrl);
        return r.json();
      })
      .then(function(corePayload){
        startGraph(corePayload);
        setRestLoading(true);
        // ``Promise.all`` is structural here — only one URL today, but the
        // shape keeps the merge path symmetric if we ever shard further.
        return Promise.all([fetch(restUrl)]).then(function(rs){
          var r = rs[0];
          if (!r.ok) throw new Error('HTTP ' + r.status + ' while loading ' + restUrl);
          return r.json();
        });
      })
      .then(function(restPayload){
        try {
          if (typeof window.__graphMergeRestPayload === 'function') {
            window.__graphMergeRestPayload(restPayload);
          }
        } catch (err) {
          console.warn('graph: rest merge failed', err);
        }
        setRestLoading(false);
        window.__graphRestLoaded = true;
      })
      .catch(function(err){
        console.error('graph: payload load failed', err);
        setRestLoading(false);
        if (!window.__graphCoreLoaded) {
          var banner = document.getElementById('graph-error-banner');
          if (banner) {
            banner.textContent = 'Graph payload failed to load: ' + (err && err.message ? err.message : err);
            banner.classList.add('is-visible');
          }
        } else {
          console.warn('graph: rest payload failed to load — keeping core-only view');
          // Flip the gate so auto-browse can still run on the core subgraph
          // rather than block forever waiting for a payload that never
          // arrives.
          window.__graphRestLoaded = true;
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
    // Detail pages stamp ids on ``<h2>``/``<h3>`` inside ``.article-body``;
    // the home page (and other ``main--wide`` routes) stamp ids on
    // top-level ``<section>`` blocks instead because they don't use the
    // canonical article shell. Probe both — whichever set matches the
    // TOC anchors gets observed by the spy.
    var headings = document.querySelectorAll(
      '.article-body h2[id], .article-body h3[id], .main > article > section[id], .main > section[id]'
    );
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


# ---------------------------------------------------------------------------
# Doc-tree filter (Issue 3) — debounced search-input filter that hides
# non-matching ``.doc-tree-leaf`` rows and auto-expands ``<details>``
# folders that contain matches. Pure DOM mutations; safe with no JS too
# (the user can still expand folders by clicking and the leaves stay
# clickable links).
# ---------------------------------------------------------------------------
JS_DOC_TREE = r"""
(function(){
  function init(){
    var input = document.querySelector('[data-doc-tree-search]');
    if (!input) return;
    var tree = document.querySelector('.doc-tree');
    if (!tree) return;
    var leaves = tree.querySelectorAll('.doc-tree-leaf');
    var folders = tree.querySelectorAll('details.doc-tree-folder');
    // Stash the original ``open`` state so we can restore on clear.
    var initialOpen = [];
    for (var i = 0; i < folders.length; i++) {
      initialOpen.push(folders[i].open);
    }
    var t = null;
    function apply(query){
      query = (query || '').trim().toLowerCase();
      // 1) Show every leaf when the query is empty; restore folder state.
      if (!query){
        for (var i = 0; i < leaves.length; i++) leaves[i].hidden = false;
        for (var j = 0; j < folders.length; j++) folders[j].open = initialOpen[j];
        return;
      }
      // 2) Mark each leaf as visible/hidden based on substring match
      //    against its data-doc-path attribute (or visible name).
      var matchedFolders = new Set();
      for (var k = 0; k < leaves.length; k++) {
        var leaf = leaves[k];
        var path = (leaf.getAttribute('data-doc-path') || '').toLowerCase();
        var name = (leaf.textContent || '').toLowerCase();
        var hit = path.indexOf(query) !== -1 || name.indexOf(query) !== -1;
        leaf.hidden = !hit;
        if (hit) {
          // Walk up to every <details> ancestor so we can force them open.
          var parent = leaf.parentElement;
          while (parent && parent !== tree) {
            if (parent.tagName === 'DETAILS') matchedFolders.add(parent);
            parent = parent.parentElement;
          }
        }
      }
      // 3) Open every folder that has a match; collapse the rest.
      for (var f = 0; f < folders.length; f++) {
        folders[f].open = matchedFolders.has(folders[f]);
      }
    }
    input.addEventListener('input', function(){
      if (t) window.clearTimeout(t);
      t = window.setTimeout(function(){ apply(input.value); }, 80);
    });
    input.addEventListener('keydown', function(e){
      if (e.key === 'Escape') { input.value = ''; apply(''); input.blur(); }
    });
  }
  if (document.readyState === 'loading'){
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
"""


JS_SESSION_TURN_SCROLLSPY = r"""
(function(){
  function init(){
    var navItems = document.querySelectorAll('.session-turn-nav li[data-session-turn-target]');
    if (!navItems.length) return;
    var byAnchor = {};
    for (var i = 0; i < navItems.length; i++) {
      var anchor = navItems[i].getAttribute('data-session-turn-target') || '';
      if (anchor) byAnchor[anchor] = navItems[i];
    }
    var activeAnchor = null;
    function setActive(anchor){
      if (!anchor || anchor === activeAnchor) return;
      activeAnchor = anchor;
      for (var i = 0; i < navItems.length; i++) {
        var item = navItems[i];
        var on = (item.getAttribute('data-session-turn-target') || '') === anchor;
        item.classList.toggle('is-active', on);
        var link = item.querySelector('a');
        if (link) {
          if (on) link.setAttribute('aria-current', 'location');
          else link.removeAttribute('aria-current');
        }
      }
      var active = byAnchor[anchor];
      if (active && active.scrollIntoView) {
        try { active.scrollIntoView({ block: 'nearest' }); } catch (_) {}
      }
    }
    document.addEventListener('click', function(evt){
      var a = evt.target && evt.target.closest && evt.target.closest('.session-turn-nav a[href^="#turn-"]');
      if (!a) return;
      var href = a.getAttribute('href') || '';
      if (href.length > 1) setActive(href.slice(1));
    });
    var turns = document.querySelectorAll('.session-turn[id]');
    if (!turns.length) return;
    if (typeof IntersectionObserver === 'undefined') {
      setActive(turns[0].id);
      return;
    }
    var visible = {};
    var io = new IntersectionObserver(function(entries){
      for (var i = 0; i < entries.length; i++) {
        var ent = entries[i];
        var id = ent.target.id;
        if (ent.isIntersecting) visible[id] = ent.target;
        else delete visible[id];
      }
      var best = null;
      var bestTop = Infinity;
      for (var id in visible) {
        if (!Object.prototype.hasOwnProperty.call(visible, id)) continue;
        var rect = visible[id].getBoundingClientRect();
        var score = Math.abs(rect.top - 120);
        if (score < bestTop) { bestTop = score; best = id; }
      }
      if (best) setActive(best);
    }, {
      rootMargin: '-12% 0px -68% 0px',
      threshold: 0
    });
    for (var j = 0; j < turns.length; j++) io.observe(turns[j]);
    setActive(turns[0].id);
  }
  if (document.readyState === 'loading'){
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
"""


# ---------------------------------------------------------------------------
# Mermaid diagrams
# ---------------------------------------------------------------------------
JS_MERMAID_RENDER = r"""
(function(){
  function mermaidBlocks(){
    return Array.prototype.slice.call(document.querySelectorAll('.mermaid[data-mermaid-source]'));
  }
  function markFailed(block, err){
    block.setAttribute('data-mermaid-error', 'true');
    block.setAttribute('data-mermaid-rendered', 'error');
    try { console.warn('Mermaid render failed', err); } catch (_) {}
  }
  function renderMermaid(){
    var blocks = mermaidBlocks().filter(function(block){
      return block.getAttribute('data-mermaid-rendered') !== 'true';
    });
    if (!blocks.length) return;
    blocks.forEach(function(block){ block.setAttribute('data-mermaid-rendered', 'pending'); });
    import('https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs')
      .then(function(mod){
        var mermaid = mod.default || mod;
        mermaid.initialize({
          startOnLoad: false,
          securityLevel: 'strict',
          theme: (document.documentElement.getAttribute('data-theme') === 'dark') ? 'dark' : 'default'
        });
        var chain = Promise.resolve();
        blocks.forEach(function(block, index){
          chain = chain.then(function(){
            var source = block.textContent || '';
            var id = 'mermaid-' + Date.now() + '-' + index;
            return mermaid.render(id, source).then(function(result){
              block.innerHTML = result.svg;
              block.setAttribute('data-mermaid-rendered', 'true');
              block.removeAttribute('data-mermaid-error');
              if (result.bindFunctions) result.bindFunctions(block);
            }).catch(function(err){ markFailed(block, err); });
          });
        });
        return chain;
      })
      .catch(function(err){
        blocks.forEach(function(block){ markFailed(block, err); });
      });
  }
  function scheduleMermaid(){
    try { window.setTimeout(renderMermaid, 0); } catch (_) { renderMermaid(); }
  }
  if (document.readyState === 'complete') {
    scheduleMermaid();
  } else {
    window.addEventListener('load', scheduleMermaid, { once: true });
  }
})();
"""


# ``JS_BUNDLE_BASE`` is what every page loads (theme toggle, rail/TOC drawer,
# search palette, subtype chip filter, doc-tree filter, TOC scrollspy, Mermaid diagrams).
# ``JS_BUNDLE_GRAPH`` is the heavier graph renderer that we only ship on the
# graph route — see ``tesserae.site.__init__`` (writes both ``assets/app.js``
# and ``assets/graph.js``) and ``render_graph_view`` in ``pages.py`` (injects
# the second ``<script defer>`` only on the graph page).
JS_BUNDLE_BASE = (
    JS_THEME_TOGGLE
    + "\n" + JS_RAIL_DRAWER
    + "\n" + JS_SEARCH_PALETTE
    + "\n" + JS_SUBTYPE_FILTER
    + "\n" + JS_DOC_TREE
    + "\n" + JS_TOC_SCROLLSPY
    + "\n" + JS_SESSION_TURN_SCROLLSPY
    + "\n" + JS_MERMAID_RENDER
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
    "JS_DOC_TREE",
    "JS_TOC_SCROLLSPY",
    "JS_SESSION_TURN_SCROLLSPY",
    "JS_MERMAID_RENDER",
    "JS_GRAPH",
    "JS_BUNDLE",
    "JS_BUNDLE_BASE",
    "JS_BUNDLE_GRAPH",
]
