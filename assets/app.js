
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


(function(){
  function renderMermaid(){
    var blocks = document.querySelectorAll('.mermaid');
    if (!blocks.length) return;
    for (var i = 0; i < blocks.length; i++) {
      blocks[i].setAttribute('data-processed', 'false');
    }
    import('https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs')
      .then(function(mod){
        var mermaid = mod.default || mod;
        mermaid.initialize({
          startOnLoad: false,
          securityLevel: 'strict',
          theme: (document.documentElement.getAttribute('data-theme') === 'dark') ? 'dark' : 'default'
        });
        return mermaid.run({ nodes: blocks });
      })
      .catch(function(err){
        document.documentElement.setAttribute('data-mermaid-error', '');
        try { console.warn('Mermaid render failed', err); } catch (_) {}
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
