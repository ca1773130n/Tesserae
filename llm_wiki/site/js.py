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
  // Issue 4 — hot edges (incident to focus or hover) jump to 0.85 alpha
  // so they pop against the calm 0.34 baseline. Pure yellow particles
  // ride on top — the color stays warm but readable across themes.
  var EDGE_COLOR_HOT   = 'rgba(250,204,21,0.85)';
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

    // Issue 2 — the bottom-right ``#graph-info-panel`` overlay (with its
    // empty/content/neighbors children) is GONE. The cursor-following
    // ``#graph-tooltip`` below replaces it for hover preview; the focused
    // node's label sprite carries the focus details inline.
    var tooltip      = document.getElementById('graph-tooltip');
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

    // Issue 2 — the bottom-right info-panel write functions are GONE.
    // Hover preview lives in the cursor-following ``#graph-tooltip``;
    // focused-node details live in the focused label sprite (Issue 3 —
    // the visible ``[Enter] Open page`` hint that used to render under
    // the title is dropped; pressing Enter still navigates).
    // The functions kept below are the only DOM mutations we still do
    // per interaction.
    function clearInfoPanel(){
      // Kept as a no-op for the call sites that still invoke it (Esc key,
      // background click, reset button) — every focus-clear path used to
      // call this and we want the call sites to keep reading naturally.
      hideTooltip();
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
      var hint = document.createElement('span');
      hint.className = 'graph-tooltip-hint';
      hint.textContent = 'click to focus';
      tooltip.appendChild(hint);
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

    function hasFocusFilter(){
      return highlightNodes.size > 0 || highlightLinks.size > 0;
    }

    function isDimmedNode(node){
      return hasFocusFilter() && !highlightNodes.has(node);
    }

    function isDimmedLink(link){
      return hasFocusFilter() && !highlightLinks.has(link);
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

    // ---- Hierarchical label sprite (Issue 1 + 2 + 3) -------------------
    // ``makeLabel(text, opts)`` paints text onto a canvas and wraps it in a
    // THREE.Sprite. Every variant renders the same primitive: a
    // semi-transparent black rounded pill with light gray / white text on
    // top — NO text stroke, NO outline, NO accent border. The only thing
    // that changes per variant is the pill alpha, the text alpha, the
    // font size, and the depth/render-order config:
    //
    //   variant='default'  : every non-focused / non-hover / non-neighbor
    //                        node. Pill rgba(0,0,0,0.55), text
    //                        rgba(220,225,235,0.85), 11px. depthTest=true
    //                        so nearer geometry occludes (defaults
    //                        shouldn't always be on top).
    //   variant='neighbor' : a 1-hop neighbor of the focused node. Pill
    //                        rgba(0,0,0,0.6), text rgba(255,255,255,0.92),
    //                        14px. depthTest=false renderOrder=998.
    //   variant='hover'    : the node currently under the mouse. Pill
    //                        rgba(0,0,0,0.7), text pure white, 18px.
    //                        depthTest=false renderOrder=999.
    //   variant='focused'  : the clicked node (exactly one). Pill
    //                        rgba(0,0,0,0.78), text pure white, 22px.
    //                        depthTest=false depthWrite=false renderOrder=999.
    //                        NO ``[Enter] Open page`` hint — the Enter-key
    //                        handler still works, but we don't paint a
    //                        visible hint line underneath the title.
    //   variant='edge'     : an edge label, only rendered for edges
    //                        incident to the focused or hover node. Pill
    //                        rgba(0,0,0,0.55), text rgba(255,255,255,0.78),
    //                        10px. depthTest=true.
    //
    // Light theme inverts: pills become near-white, text becomes
    // near-black. Same — NO strokes, NO borders, NO color outlines.
    //
    // Cached by ``text|variant|theme`` so identical labels reuse their
    // canvas/texture across nodes (the per-variant pill+text colors are
    // implied by the variant + theme so we don't need ``accent`` or
    // ``hint`` in the key any more).
    var VARIANT_FONT       = { default: 11, edge: 10, neighbor: 14, hover: 18, focused: 22 };
    var VARIANT_OPACITY    = { default: 0.85, edge: 0.78, neighbor: 0.92, hover: 1.0, focused: 1.0 };
    // Stroke widths are kept in the table for back-compat but are NEVER
    // applied to label text (Issue 1 — explicit "NO text stroke. NO outline.
    // NO border."). The previous round used these to paint accent-tinted
    // text strokes; we now leave them unused.
    var VARIANT_STROKE     = { default: 0, edge: 0, neighbor: 0, hover: 0, focused: 0 };
    var VARIANT_RENDER_ORDER = { default: 1, edge: 1, neighbor: 998, hover: 999, focused: 999 };
    // Per-variant pill alpha (dark theme). Light theme inverts the base
    // color but reuses these alphas so the visual weight matches.
    var VARIANT_PILL_ALPHA = { default: 0.55, edge: 0.55, neighbor: 0.6, hover: 0.7, focused: 0.78 };
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
      var pillAlpha = VARIANT_PILL_ALPHA[variant] || 0.55;
      var pillFill = (theme === 'light')
        ? 'rgba(255,255,255,' + pillAlpha + ')'
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
      // No stroke: the user explicitly said NO text border, NO outline,
      // NO color border on any variant.
      ctx.font = (variant === 'focused' ? '700 ' : '600 ') + fontPx + 'px "Inter", system-ui, sans-serif';
      ctx.textBaseline = 'middle';
      ctx.textAlign = 'center';
      // Text fill: light gray (rgba(220,225,235,0.85)) for the default
      // variant, white at the variant's opacity for hover/neighbor/focused.
      // Light theme inverts to a dark cool gray.
      var textOpacity = VARIANT_OPACITY[variant] || 1.0;
      var textFill;
      if (variant === 'default') {
        textFill = (theme === 'light')
          ? 'rgba(40,40,50,0.85)'
          : 'rgba(220,225,235,0.85)';
      } else if (variant === 'edge') {
        textFill = (theme === 'light')
          ? 'rgba(40,40,50,' + textOpacity + ')'
          : 'rgba(255,255,255,' + textOpacity + ')';
      } else {
        // hover / focused / neighbor — pure white on dark, near-black on light.
        textFill = (theme === 'light')
          ? 'rgba(20,20,28,' + textOpacity + ')'
          : 'rgba(255,255,255,' + textOpacity + ')';
      }
      ctx.fillStyle = textFill;
      var textY = padY + lineH / 2;
      ctx.fillText(text, w / 2, textY);
      var tex = new THREE.CanvasTexture(canvas);
      tex.minFilter = THREE.LinearFilter;
      // Issue 1 — defaults and edge labels keep depthTest=true so nearer
      // geometry occludes them (defaults shouldn't always be on top).
      // Hover / neighbor / focused turn depthTest off so they always sit
      // on top. The focused variant additionally turns depthWrite off and
      // bumps renderOrder to 999 so it renders above EVERYTHING.
      var depthTest = (variant === 'default' || variant === 'edge');
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
    function installLibraryZoom(inst){
      var controls = inst && inst.controls && inst.controls();
      if (!controls) return;
      try {
        controls.enableDamping = true;
        controls.dampingFactor = 0.08;
      } catch (_) {}
      // Issue 5 — own the wheel exclusively. We never use the library's
      // built-in zoom (``controls.enableZoom = false``) because it
      // doesn't anchor on the cursor in the version of OrbitControls
      // that 3d-force-graph ships with.
      try { controls.enableZoom = false; } catch (_) {}
      var canvas = container && container.querySelector && container.querySelector('canvas');
      if (!canvas || !THREE) return;
      var camera = inst.camera && inst.camera();
      if (!camera) return;
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
      // Step 1 helper — cast the cursor ray and intersect the plane that
      // passes through ``controls.target`` perpendicular to the camera-
      // target axis. Writes the world point into ``out`` and returns
      // true; returns false if the ray is parallel to the plane.
      function cursorWorldOnTargetPlane(out){
        raycaster.setFromCamera(ndc, camera);
        dirToTarget.subVectors(controls.target, camera.position).normalize();
        plane.setComponents(dirToTarget.x, dirToTarget.y, dirToTarget.z, -dirToTarget.dot(controls.target));
        return raycaster.ray.intersectPlane(plane, out) !== null;
      }
      canvas.addEventListener('wheel', function(event) {
        event.preventDefault();
        event.stopPropagation();
        var rect = canvas.getBoundingClientRect();
        ndc.set(
          ((event.clientX - rect.left) / rect.width) * 2 - 1,
          -((event.clientY - rect.top) / rect.height) * 2 + 1
        );
        // 1+2. Capture cursor world position BEFORE zoom.
        if (!cursorWorldOnTargetPlane(before)) return;
        // 3. Apply pure dolly: scale (camera - target) by factor and
        //    place the camera at target + offset.
        var factor = Math.exp(event.deltaY * 0.001);  // wheel up → < 1 → zoom in
        offset.subVectors(camera.position, controls.target).multiplyScalar(factor);
        camera.position.copy(controls.target).add(offset);
        // 4. Capture cursor world position AFTER the dolly.
        if (!cursorWorldOnTargetPlane(after)) {
          controls.update();
          return;
        }
        // 5. Translate BOTH camera AND target by (before - after) so the
        //    world point under the cursor stays under the cursor.
        delta.subVectors(before, after);
        camera.position.add(delta);
        controls.target.add(delta);
        controls.update();
      }, { passive: false });
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
      // contains the toolbar/info panel/legend); otherwise we measure the
      // canvas container as before.
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
      while (container.firstChild) container.removeChild(container.firstChild);
      var ctor = (mode === '2d') ? window.ForceGraph : window.ForceGraph3D;
      if (!ctor) { renderFallback('Renderer constructor missing.'); return; }

      var inst = ctor()(container)
        .graphData({ nodes: payload.nodes, links: payload.links })
        .backgroundColor('rgba(0,0,0,0)')
        .nodeId('id')
        .nodeLabel(function(n){ return ''; })
        .nodeVal(function(n){
          // Issue 2 — hovered node grows to 1.25x its normal val so the
          // sphere itself becomes a visible cue independent of the label.
          var base = Math.max(1, n.val || 1);
          if (hoverNode === n) return base * 1.25;
          return base;
        })
        .nodeColor(function(n){
          // Bug 6 — non-incident nodes dim to 25% opacity (spec target).
          // Keep the focused node + 1-hop neighbors at full saturation;
          // every other node falls to a desaturated grey at alpha 0.25.
          if (isDimmedNode(n)) return 'rgba(120,116,108,0.25)';
          return n.color;
        })
        .linkColor(function(l){
          // Issue 4 — incident edges (focus highlight OR hover incident)
          // light up at 0.85 alpha; everything else stays at the calm
          // 0.34 baseline so the canvas reads as quiet by default.
          if (highlightLinks.has(l)) return EDGE_COLOR_HOT;
          if (isHoverIncidentLink(l)) return EDGE_COLOR_HOT;
          if (hasFocusFilter()) return EDGE_COLOR_DIM;
          return EDGE_COLOR_LIGHT;
        })
        // Issue 4 — edges are visibly THINNER everywhere. Default drops to
        // 0.25; incident edges (hover or focus) bump to 0.9. Non-incident
        // dimmed edges drop to 0.001 so they read as ~no-line (combined
        // with EDGE_COLOR_DIM alpha 0.012). Single ladder: focus wins
        // first, then hover-incident, then default.
        .linkWidth(function(l){
          if (isDimmedLink(l)) return 0.001;
          if (highlightLinks.has(l)) return 0.9;
          if (isHoverIncidentLink(l)) return 0.9;
          return 0.25;
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
        .linkDirectionalParticleWidth(1.5)
        .linkDirectionalParticleSpeed(0.005)
        .onNodeHover(function(node){
          hoverNode = node || null;
          container.style.cursor = node && !isDimmedNode(node) ? 'pointer' : 'default';
          // Issue 2 — hover preview lives in the cursor-following tooltip.
          // The focused-node label sprite already shows everything important
          // for the focused node, so we suppress the tooltip when hovering
          // the focused node itself (don't double-show).
          if (node && node !== focusedNode && !isDimmedNode(node)) {
            showNodeTooltip(node, lastMouseX, lastMouseY);
          } else {
            hideTooltip();
          }
          // Highlight ring still updates when nothing is pinned, so the
          // 1-hop neighbors light up under the cursor.
          if (!pinnedNode && !pinnedLink) {
            applyHighlight(node);
          }
          // Issue 2 — re-poke node val + link width accessors so the
          // hovered sphere visibly grows and incident edges thicken
          // without waiting for the next simulation tick.
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
        });

      try { if (inst.nodeOpacity) inst.nodeOpacity(0.95); } catch (_) {}
      // Issue 4 — keep base edge opacity at 0.35 so non-incident edges
      // read as faint background structure. Incident edges get an opacity
      // bump via the ``linkColor`` accessor (it returns EDGE_COLOR_HOT at
      // alpha 1.0 for focus highlights, EDGE_COLOR_LIGHT at 0.34 baseline,
      // and a brighter 0.85 alpha when the link is hover-incident).
      try { if (inst.linkOpacity) inst.linkOpacity(0.35); } catch (_) {}
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
              // Camera-zoom-aware scale factor. The library uses a perspective
              // camera; ``camera.zoom`` is normally 1, so this is a no-op
              // unless an upstream change swaps in an ortho camera.
              var camScale = 1.0;
              try {
                var cam = Graph && Graph.camera && Graph.camera();
                if (cam && cam.zoom) camScale = Math.max(0.6, Math.min(1.6, 1 / cam.zoom));
              } catch (_) {}
              for (var i = 0; i < group.children.length; i++) {
                var child = group.children[i];
                if (!child) continue;
                var ud = child.userData || {};
                if (ud.isFocusedLabel) {
                  child.visible = isFocused;
                  child.position.set(0, labelY, 0);
                  if (isFocused) child.scale.multiplyScalar(camScale / (child.userData.__lastScale || 1));
                  child.userData.__lastScale = camScale;
                  applySpriteOpacity(child, 1.0);
                } else if (ud.isNeighborGlow) {
                  child.visible = !!isFocusedNeighbor;
                  applySpriteOpacity(child, 0.85);
                } else if (ud.isNeighborLabel) {
                  // Show the neighbor label when the node is a 1-hop neighbor
                  // of the focused node and is NOT itself focused or hovered.
                  child.visible = !!isFocusedNeighbor && !isHovered;
                  child.position.set(0, labelY, 0);
                  applySpriteOpacity(child, 1.0);
                } else if (ud.isHoverLabel) {
                  // Hover label only shows when the node is being mouse-hovered
                  // and is NOT focused (focus wins on the same node).
                  child.visible = isHovered;
                  child.position.set(0, labelY, 0);
                  applySpriteOpacity(child, 1.0);
                } else if (ud.isDefaultLabel || ud.isLabel) {
                  // Default label — translucent text, no pill (Issue 1).
                  // Hidden when ANY larger variant is showing on this node
                  // so we never stack titles. Otherwise: visible for the
                  // high-degree overview nodes (avoids hairball when every
                  // tiny leaf draws its name).
                  child.visible = !isFocused && !isFocusedNeighbor && !isHovered && showBaseAlways;
                  child.position.set(0, labelY, 0);
                  applySpriteOpacity(child, cameraDistanceOpacity(coords.x, coords.y, coords.z));
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
            // black rounded pill with light gray / white text on top.
            // NO text stroke, NO outline, NO accent border.
            //   focused  : 22px, white text, pill alpha 0.78.
            //   hover    : 18px, white text, pill alpha 0.7.
            //   neighbor : 14px, white text 0.92, pill alpha 0.6.
            //   default  : 11px, light gray rgba(220,225,235,0.85),
            //              pill alpha 0.55.
            // Default labels only render for high-degree overview nodes
            // (otherwise 2D becomes a hairball of tiny names).
            if (isDimmedNode(n)) return;
            var isFocused = !!n.__focused;
            var isFocusedNeighbor = focusedNode && n !== focusedNode && highlightNodes.has(n);
            var isHovered = (hoverNode === n) && !isFocused;
            var showDefault = shouldShowOverviewLabel(n);
            var theme = (document.documentElement.getAttribute('data-theme') === 'light') ? 'light' : 'dark';
            var variant;
            if (isFocused) variant = 'focused';
            else if (isHovered) variant = 'hover';
            else if (isFocusedNeighbor) variant = 'neighbor';
            else if (showDefault) variant = 'default';
            else return;
            var label = nodeLabelText(n);
            var fontSize = (VARIANT_FONT[variant] || 11) / globalScale;
            ctx.font = (variant === 'focused' ? '700 ' : '600 ') + fontSize + 'px Inter, system-ui, sans-serif';
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
            var pillAlpha = (VARIANT_PILL_ALPHA[variant] || 0.55);
            ctx.fillStyle = (theme === 'light')
              ? 'rgba(255,255,255,' + pillAlpha + ')'
              : 'rgba(0,0,0,' + pillAlpha + ')';
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
            // Issue 1 — NO text stroke on any variant. Plain text on the
            // pill (the user explicitly does not want a text border).
            var op = VARIANT_OPACITY[variant] || 1.0;
            if (variant === 'default') {
              ctx.fillStyle = (theme === 'light')
                ? 'rgba(40,40,50,0.85)'
                : 'rgba(220,225,235,0.85)';
            } else {
              ctx.fillStyle = (theme === 'light')
                ? 'rgba(20,20,28,' + op + ')'
                : 'rgba(255,255,255,' + op + ')';
            }
            ctx.fillText(label, n.x, pillY + pillH / 2);
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
            var epillAlpha = (VARIANT_PILL_ALPHA.edge || 0.55);
            ctx.fillStyle = (theme === 'light')
              ? 'rgba(255,255,255,' + epillAlpha + ')'
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
            var eop = VARIANT_OPACITY.edge || 0.78;
            ctx.fillStyle = (theme === 'light')
              ? 'rgba(20,20,28,' + eop + ')'
              : 'rgba(255,255,255,' + eop + ')';
            ctx.fillText(label, midX, midY);
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

      // Bug 5 — auto-orbit hook. ``onEngineTick`` fires per render frame
      // (regardless of whether the simulation is still cooling), which is
      // exactly the cadence we want for cinematic camera motion. We
      // integrate orbitAngle by ~0.2 rad/s using a clock-based dt so the
      // orbit stays smooth at any framerate. The orbit only spins when
      // (a) we have a focused node, (b) auto-orbit is enabled, and (c)
      // we're in 3D mode.
      try {
        if (mode === '3d' && inst.onEngineTick) {
          inst.onEngineTick(function(){
            if (!focusedNode || !autoOrbitEnabled) { lastTickMs = 0; return; }
            var now = (typeof performance !== 'undefined' && performance.now) ? performance.now() : Date.now();
            var dt = lastTickMs ? Math.min(0.1, (now - lastTickMs) / 1000) : 0.016;
            lastTickMs = now;
            orbitAngle += 0.2 * dt;  // ~0.2 rad/s, ~1 full revolution every 31s
            var nx = focusedNode.x || 0;
            var ny = focusedNode.y || 0;
            var nz = focusedNode.z || 0;
            var camX = nx + Math.sin(orbitAngle) * orbitRadius;
            var camZ = nz + Math.cos(orbitAngle) * orbitRadius;
            try {
              if (inst.cameraPosition) {
                inst.cameraPosition({ x: camX, y: ny, z: camZ }, { x: nx, y: ny, z: nz }, 0);
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
            // Issue 6 — manual mouse-drag (orbit/pan) interrupts auto-browse.
            if (autoBrowseActive) stopAutoBrowse();
          });
        }
      } catch (_) {}

      Graph = inst;
      sizeGraphToContainer(inst);
      installGraphResize(inst);
      if (mode === '3d') {
        installLibraryZoom(inst);
        // Start the camera at a known distance so the first frame isn't
        // a wild zoom-out from the origin. The single-shot scheduleCenteredFit
        // will refine the framing once the simulation settles.
        try {
          if (inst.cameraPosition) inst.cameraPosition({ x: 0, y: 0, z: 600 }, { x: 0, y: 0, z: 0 }, 0);
        } catch (_) {}
      } else if (mode === '2d') {
        // Issue 3 — 2D ``force-graph`` zooms toward the cursor by default
        // (the library reads pointer position on wheel). We just confirm
        // node-drag is on so the user can rearrange the layout while
        // exploring.
        try { if (inst.enableNodeDrag) inst.enableNodeDrag(true); } catch (_) {}
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

    function activateNode(node, evt){
      if (!node) return;
      if (isDimmedNode(node)) return;
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
        // Issue 2 — focused node's label sprite carries the focus details
        // inline (Issue 3 — no visible Enter-hint line; key still works).
        // The bottom-right info panel is gone.
        hideTooltip();
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
        hideTooltip();
        focusOnLink(link);
        return;
      }
      var endpoints = linkEndpoints(link);
      var target = endpoints.target || endpoints.source;
      if (target && target.href) window.location.href = target.href;
    }

    function focusOnNode(node){
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
        var nx = node.x || 0, ny = node.y || 0, nz = node.z || 0;
        var norm = Math.max(240, Math.hypot(nx || 1, ny || 1, nz || 1));
        var distRatio = 1 + distance / norm;
        // Adapt orbit radius to the node's visual size so big hubs aren't
        // clipped by the camera. Floors at 200 units (spec).
        var radius = Math.sqrt((node && node.val) || 1);
        orbitRadius = Math.max(200, 60 + radius * 14);
        orbitAngle = 0;
        autoOrbitEnabled = true;
        // Animate to a position 200u in +Z from the node, looking at it.
        try {
          Graph.cameraPosition(
            { x: nx, y: ny, z: nz + orbitRadius },
            { x: nx, y: ny, z: nz },
            reduceMotion ? 0 : 600
          );
        } catch (_) {}
        // Set OrbitControls target so manual drag pivots around the node.
        try {
          var controls = Graph.controls && Graph.controls();
          if (controls && controls.target && controls.target.set) {
            controls.target.set(nx, ny, nz);
            if (controls.update) controls.update();
          }
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

    // ---- Fullscreen (Issue 4) ------------------------------------------
    // Request fullscreen on the WRAPPER (not the canvas) so the toolbar +
    // legend + info panel come along. Listen to ``fullscreenchange`` to
    // toggle the ``is-fullscreen`` class so CSS can repaint the layout.
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
      if (Graph && Graph.cameraPosition && mode === '3d') {
        try { Graph.cameraPosition({ x: 0, y: 0, z: 400 }, { x: 0, y: 0, z: 0 }, reduceMotion ? 0 : 600); } catch (_) {}
      } else if (Graph && Graph.centerAt) {
        try { Graph.centerAt(0, 0, reduceMotion ? 0 : 600); Graph.zoom(1, reduceMotion ? 0 : 600); } catch (_) {}
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
    var AUTO_BROWSE_DWELL_MS = 5000;
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
    function autoBrowseStep(node){
      if (!autoBrowseActive) return;
      if (!node) {
        // Out of unvisited reachable nodes — start a fresh chain from
        // the next-highest-degree unvisited node, or stop if none.
        var fresh = pickStartNode();
        if (!fresh) { stopAutoBrowse(); return; }
        autoBrowseHopCount = 0;
        autoBrowseStep(fresh);
        return;
      }
      autoBrowseVisited.add(node.id);
      autoBrowseHopCount += 1;
      try { focusOnNode(node); } catch (_) {}
      // Treat focus as a pin so re-builds don't drop the highlight.
      pinnedNode = node;
      focusedNode = node;
      markFocused(node);
      applyHighlight(node);
      autoBrowseTimer = window.setTimeout(function(){
        if (!autoBrowseActive) return;
        var nextNode;
        if (autoBrowseHopCount >= AUTO_BROWSE_MAX_HOPS) {
          autoBrowseHopCount = 0;
          nextNode = pickStartNode();
        } else {
          nextNode = pickNextNeighbor(node);
          if (!nextNode) {
            autoBrowseHopCount = 0;
            nextNode = pickStartNode();
          }
        }
        autoBrowseStep(nextNode);
      }, AUTO_BROWSE_DWELL_MS);
    }
    function startAutoBrowse(){
      if (autoBrowseActive) return;
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
            focusedNode = match;
            markFocused(match);
            applyHighlight(match);
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
        // auto-browse tour if one is running.
        if (autoBrowseActive) stopAutoBrowse();
        pinnedNode = null;
        pinnedLink = null;
        focusedNode = null;
        markFocused(null);
        autoOrbitEnabled = false;
        applyHighlight(null);
        clearInfoPanel();
        dayFilter = null;
        if (searchEl) { searchEl.value = ''; searchQuery = ''; }
        refreshVisibility();
        // Animate back to fit (one-shot; not the auto-fit guard).
        try { fitAll(600); } catch (_) {}
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


# ``JS_BUNDLE_BASE`` is what every page loads (theme toggle, rail/TOC drawer,
# search palette, subtype chip filter, doc-tree filter, TOC scrollspy).
# ``JS_BUNDLE_GRAPH`` is the heavier graph renderer that we only ship on the
# graph route — see ``llm_wiki.site.__init__`` (writes both ``assets/app.js``
# and ``assets/graph.js``) and ``render_graph_view`` in ``pages.py`` (injects
# the second ``<script defer>`` only on the graph page).
JS_BUNDLE_BASE = (
    JS_THEME_TOGGLE
    + "\n" + JS_RAIL_DRAWER
    + "\n" + JS_SEARCH_PALETTE
    + "\n" + JS_SUBTYPE_FILTER
    + "\n" + JS_DOC_TREE
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
    "JS_DOC_TREE",
    "JS_TOC_SCROLLSPY",
    "JS_GRAPH",
    "JS_BUNDLE",
    "JS_BUNDLE_BASE",
    "JS_BUNDLE_GRAPH",
]
