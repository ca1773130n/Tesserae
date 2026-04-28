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
  var TOKEN_RE = /[\wÀ-ɏ가-힯]+/g;

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
    sources:   '#5b574f',
    papers:    '#be185d',
    repos:     '#2563eb',
    concepts:  '#0891b2',
    entities:  '#7c3aed',
    topics:    '#b3502b',
    syntheses: '#2a6f4f',
    questions: '#c08a1a',
    other:     '#64748b'
  };
  var EDGE_COLOR_LIGHT = 'rgba(91,87,79,0.35)';
  var EDGE_COLOR_DIM   = 'rgba(91,87,79,0.06)';
  var EDGE_COLOR_HOT   = 'rgba(179,80,43,0.95)';
  var THREE_URL = 'https://esm.sh/three@0.169.0';

  function ready(fn){
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', fn);
    } else { fn(); }
  }

  ready(function(){
    var dataNode  = document.getElementById('graph-data');
    var container = document.getElementById('graph-canvas');
    if (!dataNode || !container) return;

    var payload = { nodes: [], links: [] };
    try { payload = JSON.parse(dataNode.textContent || '{}') || payload; } catch (_) {}
    if (!Array.isArray(payload.nodes)) payload.nodes = [];
    if (!Array.isArray(payload.links)) payload.links = (payload.edges || []);

    var byId = new Map();
    payload.nodes.forEach(function(n){
      n.color = n.color || GROUP_COLORS[n.group || 'other'] || GROUP_COLORS.other;
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

    // Compute the median val (or degree if val missing) for label visibility.
    var vals = payload.nodes.map(function(n){ return Math.max(1, n.val || n.degree || 1); }).slice().sort(function(a,b){ return a - b; });
    var medianVal = vals.length ? vals[Math.floor(vals.length / 2)] : 1;

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
    var Graph = null;
    var THREE = null;
    var mode = '3d';
    var searchQuery = '';
    var dayFilter = null;

    function nodeAccent(n){
      return GROUP_COLORS[n.group || 'other'] || GROUP_COLORS.other;
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
        var a = document.createElement('a');
        a.className = 'graph-info-link';
        a.href = node.href;
        a.textContent = 'Open page →';
        infoPanel.appendChild(a);
      }
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
      if (Graph && Graph.refresh) {
        try { Graph.refresh(); } catch (_) {}
      }
      if (Graph && Graph.nodeColor) {
        try { Graph.nodeColor(Graph.nodeColor()); } catch (_) {}
      }
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
      payload.links.forEach(function(e){
        var sId = typeof e.source === 'object' ? e.source.id : e.source;
        var tId = typeof e.target === 'object' ? e.target.id : e.target;
        var a = positions[sId]; var b = positions[tId];
        if (!a || !b) return;
        var line = document.createElementNS(NS, 'line');
        line.setAttribute('x1', a.x); line.setAttribute('y1', a.y);
        line.setAttribute('x2', b.x); line.setAttribute('y2', b.y);
        line.setAttribute('stroke', EDGE_COLOR_LIGHT);
        line.setAttribute('stroke-width', '1');
        svg.appendChild(line);
      });
      visible.forEach(function(n){
        var p = positions[n.id]; if (!p) return;
        var link = document.createElementNS(NS, 'a');
        link.setAttribute('href', n.href || '#');
        var circle = document.createElementNS(NS, 'circle');
        circle.setAttribute('cx', p.x); circle.setAttribute('cy', p.y);
        circle.setAttribute('r', String(3 + Math.min(8, Math.sqrt(n.val || 1))));
        circle.setAttribute('fill', n.color);
        var title = document.createElementNS(NS, 'title');
        title.textContent = (n.name || '') + ' — ' + (n.type || '');
        circle.appendChild(title);
        link.appendChild(circle);
        svg.appendChild(link);
      });
      container.appendChild(svg);
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
      ctx.font = '500 ' + fontSize + 'px "Inter", system-ui, sans-serif';
      var metrics = ctx.measureText(text);
      var w = Math.ceil(metrics.width) + pad * 2;
      var h = fontSize + pad * 2;
      canvas.width = w;
      canvas.height = h;
      ctx = canvas.getContext('2d');
      ctx.font = '500 ' + fontSize + 'px "Inter", system-ui, sans-serif';
      ctx.fillStyle = 'rgba(20,18,15,0.78)';
      ctx.fillRect(0, 0, w, h);
      ctx.fillStyle = color;
      ctx.textBaseline = 'middle';
      ctx.fillText(text, pad, h / 2);
      var tex = new THREE.CanvasTexture(canvas);
      tex.minFilter = THREE.LinearFilter;
      var mat = new THREE.SpriteMaterial({ map: tex, transparent: true, depthWrite: false });
      var sprite = new THREE.Sprite(mat);
      var scale = 0.18;
      sprite.scale.set(w * scale, h * scale, 1);
      sprite.userData.isLabel = true;
      spriteCache.set(key, sprite);
      return sprite.clone();
    }

    // ---- Cursor-anchored zoom (raycast through cursor → world) -----------
    function installCursorZoom(inst){
      if (!THREE) return;
      var renderer = inst.renderer && inst.renderer();
      var camera = inst.camera && inst.camera();
      var controls = inst.controls && inst.controls();
      if (!renderer || !camera || !controls) return;
      var dom = renderer.domElement;
      if (!dom) return;
      var raycaster = new THREE.Raycaster();
      var mouseNDC = new THREE.Vector2();
      var plane = new THREE.Plane();
      var intersect = new THREE.Vector3();
      var camDir = new THREE.Vector3();
      dom.addEventListener('wheel', function(e){
        if (mode !== '3d') return;
        e.preventDefault();
        var rect = dom.getBoundingClientRect();
        mouseNDC.x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
        mouseNDC.y = -((e.clientY - rect.top) / rect.height) * 2 + 1;
        raycaster.setFromCamera(mouseNDC, camera);
        // Build a plane at the controls.target perpendicular to the camera view.
        camera.getWorldDirection(camDir);
        plane.setFromNormalAndCoplanarPoint(camDir, controls.target);
        if (!raycaster.ray.intersectPlane(plane, intersect)) return;
        var factor = Math.exp(e.deltaY * 0.001);
        // Move the camera and target toward (or away from) the intersect point.
        var dxC = camera.position.x - intersect.x;
        var dyC = camera.position.y - intersect.y;
        var dzC = camera.position.z - intersect.z;
        camera.position.set(
          intersect.x + dxC * factor,
          intersect.y + dyC * factor,
          intersect.z + dzC * factor
        );
        var dxT = controls.target.x - intersect.x;
        var dyT = controls.target.y - intersect.y;
        var dzT = controls.target.z - intersect.z;
        controls.target.set(
          intersect.x + dxT * factor,
          intersect.y + dyT * factor,
          intersect.z + dzT * factor
        );
        if (controls.update) controls.update();
      }, { passive: false });
    }

    // ---- Fit-to-view via bounding sphere over current node positions ----
    function fitAll(durationMs){
      if (!Graph) return;
      if (mode === '2d') {
        try { Graph.zoomToFit(durationMs || 600, 60); } catch (_) {}
        return;
      }
      if (!THREE) {
        try { Graph.zoomToFit(durationMs || 600, 60); } catch (_) {}
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
      if (!camera) {
        try { Graph.zoomToFit(durationMs || 600, 60); } catch (_) {}
        return;
      }
      var fov = (camera.fov || 50) * Math.PI / 180;
      var distance = sphere.radius / Math.sin(fov / 2);
      distance = Math.max(distance, 80);
      var center = sphere.center;
      try {
        Graph.cameraPosition(
          { x: center.x, y: center.y, z: center.z + distance },
          { x: center.x, y: center.y, z: center.z },
          reduceMotion ? 0 : (durationMs || 600)
        );
      } catch (_) {}
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
          if (highlightNodes.size && !highlightNodes.has(n)) return 'rgba(120,116,108,0.18)';
          return n.color;
        })
        .nodeOpacity(0.95)
        .linkColor(function(l){
          if (highlightLinks.size === 0) return EDGE_COLOR_LIGHT;
          return highlightLinks.has(l) ? EDGE_COLOR_HOT : EDGE_COLOR_DIM;
        })
        .linkWidth(function(l){ return highlightLinks.has(l) ? 1.6 : 0.4; })
        .linkHoverPrecision(8)
        .linkDirectionalParticles(function(l){ return highlightLinks.has(l) ? 2 : 0; })
        .linkDirectionalParticleWidth(1.8)
        .onNodeHover(function(node){
          hoverNode = node || null;
          container.style.cursor = node ? 'pointer' : 'default';
          if (!pinnedNode) {
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
          if (!node) return;
          if (evt && (evt.metaKey || evt.ctrlKey)) {
            pinnedNode = node;
            applyHighlight(node);
            showInfoPanel(node);
            focusOnNode(node);
            return;
          }
          if (node.href) window.location.href = node.href;
        })
        .onBackgroundClick(function(){
          pinnedNode = null;
          applyHighlight(null);
          showInfoPanel(null);
        });

      // Mode-specific labels.
      if (mode === '3d' && THREE) {
        try {
          inst.nodeThreeObject(function(n){
            var showAlways = (n.val || n.degree || 1) > medianVal;
            var isHover = (hoverNode === n) || highlightNodes.has(n);
            if (!showAlways && !isHover) return null;
            var sprite = makeSpriteLabel(n.name || n.id || '', nodeAccent(n));
            if (sprite) sprite.position.set(0, 6 + Math.sqrt(n.val || 1), 0);
            return sprite;
          });
          if (inst.nodeThreeObjectExtend) inst.nodeThreeObjectExtend(true);
          inst.linkThreeObject(function(l){
            var label = l.label || l.type || '';
            if (!label) return null;
            var s = typeof l.source === 'object' ? l.source : byId.get(l.source);
            var t = typeof l.target === 'object' ? l.target : byId.get(l.target);
            var important = (hoverNode && (hoverNode === s || hoverNode === t)) || highlightLinks.has(l);
            if (!important) return null;
            return makeSpriteLabel(label, '#ece7dc');
          });
          if (inst.linkPositionUpdate) {
            inst.linkPositionUpdate(function(sprite, coords){
              if (!sprite || !coords) return false;
              var s = coords.start; var t = coords.end;
              if (!s || !t) return false;
              sprite.position.set((s.x + t.x) / 2, (s.y + t.y) / 2, (s.z + t.z) / 2);
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
            var showAlways = (n.val || n.degree || 1) > medianVal;
            var isHover = (hoverNode === n);
            if (!showAlways && !isHover) return;
            var label = n.name || n.id || '';
            var fontSize = 12 / globalScale;
            ctx.font = fontSize + 'px Inter, system-ui, sans-serif';
            ctx.fillStyle = nodeAccent(n);
            ctx.textAlign = 'center';
            ctx.textBaseline = 'top';
            ctx.fillText(label, n.x, n.y + 6);
          });
          inst.linkCanvasObjectMode(function(){ return 'after'; });
          inst.linkCanvasObject(function(l, ctx, globalScale){
            var label = l.label || l.type || '';
            if (!label) return;
            var s = typeof l.source === 'object' ? l.source : byId.get(l.source);
            var t = typeof l.target === 'object' ? l.target : byId.get(l.target);
            if (!s || !t) return;
            var important = (hoverNode && (hoverNode === s || hoverNode === t)) || highlightLinks.has(l);
            if (!important) return;
            var fontSize = 10 / globalScale;
            ctx.font = fontSize + 'px Inter, system-ui, sans-serif';
            ctx.fillStyle = '#5b574f';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText(label, (s.x + t.x) / 2, (s.y + t.y) / 2);
          });
        } catch (err) {
          console.warn('graph: 2D labels failed', err);
        }
      }

      try {
        if (inst.d3Force) {
          var charge = inst.d3Force('charge'); if (charge && charge.strength) charge.strength(-120);
          var link = inst.d3Force('link'); if (link && link.distance) link.distance(40);
        }
      } catch (_) {}
      try { inst.cooldownTicks(120); } catch (_) {}

      try {
        inst.onEngineStop(function(){
          if (reduceMotion) return;
          setTimeout(function(){ fitAll(600); }, 50);
        });
      } catch (_) {}

      Graph = inst;
      if (mode === '3d') installCursorZoom(inst);
      refreshVisibility();
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

    function focusOnNode(node){
      if (!Graph) return;
      if (mode === '3d' && Graph.cameraPosition && node && node.x !== undefined) {
        var distance = 120;
        var distRatio = 1 + distance / Math.hypot(node.x || 1, node.y || 1, node.z || 1);
        try {
          Graph.cameraPosition(
            { x: (node.x || 0) * distRatio, y: (node.y || 0) * distRatio, z: (node.z || 0) * distRatio },
            node,
            reduceMotion ? 0 : 600
          );
        } catch (_) {}
      } else if (mode === '2d' && Graph.centerAt && node) {
        try { Graph.centerAt(node.x || 0, node.y || 0, reduceMotion ? 0 : 600); Graph.zoom(4, reduceMotion ? 0 : 600); } catch (_) {}
      }
    }

    function setMode(next){
      if (next === mode) return;
      buildGraph(next);
      if (btn2D) btn2D.classList.toggle('is-active', next === '2d');
      if (btn3D) btn3D.classList.toggle('is-active', next === '3d');
      if (btn2D) btn2D.setAttribute('aria-pressed', String(next === '2d'));
      if (btn3D) btn3D.setAttribute('aria-pressed', String(next === '3d'));
    }

    if (btn2D) btn2D.addEventListener('click', function(){ setMode('2d'); });
    if (btn3D) btn3D.addEventListener('click', function(){ setMode('3d'); });
    if (btnFit) btnFit.addEventListener('click', function(){ fitAll(400); });
    if (btnReset) btnReset.addEventListener('click', function(){
      pinnedNode = null;
      applyHighlight(null);
      showInfoPanel(null);
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
        applyHighlight(null);
        showInfoPanel(null);
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
  });
})();
"""


# ``JS_BUNDLE_BASE`` is what every page loads (theme toggle, rail/TOC drawer,
# search palette). ``JS_BUNDLE_GRAPH`` is the heavier graph renderer that we
# only ship on the graph route — see ``llm_wiki.site.__init__`` (writes both
# ``assets/app.js`` and ``assets/graph.js``) and ``render_graph_view`` in
# ``pages.py`` (injects the second ``<script defer>`` only on the graph page).
JS_BUNDLE_BASE = (
    JS_THEME_TOGGLE
    + "\n" + JS_RAIL_DRAWER
    + "\n" + JS_SEARCH_PALETTE
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
    "JS_GRAPH",
    "JS_BUNDLE",
    "JS_BUNDLE_BASE",
    "JS_BUNDLE_GRAPH",
]
