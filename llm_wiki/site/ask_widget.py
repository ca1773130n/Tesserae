"""Per-page ask-box JS island and CSS for the static wiki.

Bet B3 from ``docs/superpowers/specs/2026-05-13-competitive-positioning-research.md``.

Every detail page (concept / paper / repo / synthesis / entity / topic /
question / source) mounts a small ``<section class="ask-widget">`` near
the bottom of the article body. The widget is a tiny JS island that:

* Health-checks ``/api/ask/health`` on load. If the backend is reachable
  (i.e. the wiki is being served by ``llm_wiki project serve``) the
  widget renders an input + submit button. If not (file://, GitHub
  Pages, S3, any plain static host) the widget collapses to a one-line
  static footer pointing readers at ``llm_wiki project serve``.
* POSTs ``{node_id, node_kind, question}`` to ``/api/ask``. The CLI
  ``serve`` handler delegates to :func:`llm_wiki.query.ask_project` and
  returns the JSON envelope verbatim.
* Renders the answer inline below the input. ``raganything`` returns a
  single ``answer`` string; ``cognee`` / ``wiki`` return a ``results``
  list which the widget renders as an ordered list with anchor links
  for any item that already carries an ``href``. Wiki-relative
  ``<kind>/<slug>.html`` substrings inside answer text become anchor
  tags so cross-references stay clickable.

The widget is a single JS island deliberately decoupled from the
heavier ``graph.js`` bundle: it must NOT block initial render or
download for non-graph routes, and it must NOT depend on the graph
payload. CSS lives next to the JS so the two travel together as one
asset.

Security: every piece of dynamic content (the user's question, the
backend's answer text, node names, list entries) reaches the DOM via
``document.createTextNode`` or attribute setters, never via
``innerHTML``. The widget shells are built node-by-node with
``createElement``; the linkifier walks a strict allow-list regex over
the answer text and splices anchor elements in alongside text nodes,
so no untrusted markup ever gets parsed as HTML.
"""

from __future__ import annotations


_ASK_WIDGET_JS = r"""(function(){
  var root = document.querySelector('[data-ask-widget]');
  if (!root) return;
  var nodeId = root.getAttribute('data-node-id') || '';
  var nodeKind = root.getAttribute('data-node-kind') || '';
  var nodeName = root.getAttribute('data-node-name') || '';

  // Health-check the backend endpoint at /api/ask/health. If unreachable,
  // collapse the widget into a one-line static footer.
  try {
    fetch('/api/ask/health', { method: 'GET' })
      .then(function(r){ return r.ok ? r.json() : Promise.reject(); })
      .then(function(){ renderWidget(); })
      .catch(function(){ renderDegraded(); });
  } catch (err) {
    renderDegraded();
  }

  function clear(node){
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  function el(tag, attrs, text){
    var node = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function(k){
        if (k === 'class') node.className = attrs[k];
        else node.setAttribute(k, attrs[k]);
      });
    }
    if (text != null) node.appendChild(document.createTextNode(String(text)));
    return node;
  }

  function renderDegraded(){
    clear(root);
    var p = el('p', { 'class': 'ask-degraded' });
    p.appendChild(document.createTextNode('Host this wiki with '));
    p.appendChild(el('code', null, 'llm_wiki project serve'));
    p.appendChild(document.createTextNode(' to ask questions about this page.'));
    root.appendChild(p);
  }

  function renderWidget(){
    clear(root);
    var form = el('form', { 'class': 'ask-form', 'data-ask-form': '' });
    form.appendChild(el('label', { 'class': 'ask-label', 'for': 'ask-input' }, 'Ask about this page'));
    var row = el('div', { 'class': 'ask-row' });
    var input = el('input', {
      id: 'ask-input', type: 'text', 'class': 'ask-input',
      placeholder: 'Ask about ' + nodeName + '...', autocomplete: 'off'
    });
    var submit = el('button', { type: 'submit', 'class': 'ask-submit' }, 'Ask');
    row.appendChild(input);
    row.appendChild(submit);
    form.appendChild(row);
    var meta = el('div', { 'class': 'ask-meta' });
    var backendSpan = el('span', { 'class': 'ask-backend' });
    backendSpan.appendChild(document.createTextNode('backend: '));
    backendSpan.appendChild(el('span', { 'data-ask-backend': '' }, 'auto'));
    meta.appendChild(backendSpan);
    meta.appendChild(el('span', { 'class': 'ask-status', 'data-ask-status': '' }));
    form.appendChild(meta);
    root.appendChild(form);
    var answer = el('div', { 'class': 'ask-answer', 'data-ask-answer': '', hidden: '' });
    root.appendChild(answer);

    form.addEventListener('submit', function(ev){
      ev.preventDefault();
      var question = input.value.trim();
      if (!question) return;
      submitQuestion(question);
    });
  }

  function submitQuestion(question){
    var status = root.querySelector('[data-ask-status]');
    var answer = root.querySelector('[data-ask-answer]');
    status.textContent = 'asking...';
    answer.hidden = true;

    // Prepend the node name to the question as a context hint. This is the
    // 90% solution: ``ask_project`` doesn't take a scoping argument yet, so
    // we attach scope via natural-language prefix on the JS side. A future
    // PR can wire real subgraph scoping into ``ask_project`` itself.
    var contextualized = question;
    if (nodeName) {
      contextualized = 'About `' + nodeName + '`: ' + question;
    }

    fetch('/api/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ node_id: nodeId, node_kind: nodeKind, question: contextualized })
    })
      .then(function(r){
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(function(envelope){
        status.textContent = '';
        var backendCell = root.querySelector('[data-ask-backend]');
        if (backendCell) backendCell.textContent = envelope.backend || 'auto';
        renderAnswerInto(answer, envelope);
        answer.hidden = false;
      })
      .catch(function(err){
        status.textContent = 'error: ' + (err && err.message ? err.message : 'request failed');
      });
  }

  function renderAnswerInto(answer, envelope){
    clear(answer);
    // envelope shapes (from llm_wiki.query.ask_project):
    //   { backend: "raganything", question, answer }
    //   { backend: "cognee", question, results: [...] }
    //   { backend: "wiki", question, results: [...] }
    //   { backend: "none", question, results: [], note: "..." }
    if (envelope.answer) {
      var div = el('div', { 'class': 'ask-answer-text' });
      appendLinkified(div, String(envelope.answer));
      answer.appendChild(div);
      return;
    }
    if (Array.isArray(envelope.results) && envelope.results.length) {
      var ol = el('ol', { 'class': 'ask-answer-list' });
      envelope.results.slice(0, 8).forEach(function(r){
        var li = el('li');
        var name = typeof r === 'string' ? r : (r.name || r.title || r.text || JSON.stringify(r));
        var href = (typeof r === 'object' && r && r.href) ? r.href : null;
        if (href) {
          var a = el('a', { href: href }, String(name));
          li.appendChild(a);
        } else {
          li.appendChild(document.createTextNode(String(name)));
        }
        ol.appendChild(li);
      });
      answer.appendChild(ol);
      return;
    }
    if (envelope.note) {
      answer.appendChild(el('p', { 'class': 'ask-answer-empty' }, String(envelope.note)));
      return;
    }
    answer.appendChild(el('p', { 'class': 'ask-answer-empty' }, 'No answer.'));
  }

  function appendLinkified(container, text){
    // Conservative linkifier: replace exact ``<kind>/<slug>.html`` matches
    // inside the answer text with anchor elements pointing at ``../<match>``.
    // Everything that doesn't match the strict pattern is appended as a
    // plain text node, so no untrusted markup ever reaches the DOM.
    var pattern = /((?:concepts|papers|repos|entities|topics|syntheses|questions)\/[\w\-]+\.html)/g;
    var last = 0;
    var m = pattern.exec(text);
    while (m !== null) {
      if (m.index > last) {
        container.appendChild(document.createTextNode(text.slice(last, m.index)));
      }
      container.appendChild(el('a', { href: '../' + m[1] }, m[1]));
      last = m.index + m[1].length;
      m = pattern.exec(text);
    }
    if (last < text.length) {
      container.appendChild(document.createTextNode(text.slice(last)));
    }
  }
})();
"""


_ASK_WIDGET_CSS = """\
.ask-widget {
    margin-top: 2.5rem;
    padding-top: 1.5rem;
    border-top: 1px solid rgba(255, 255, 255, 0.08);
}
[data-theme="light"] .ask-widget {
    border-top-color: rgba(0, 0, 0, 0.08);
}
.ask-degraded {
    font-size: 0.85rem;
    color: var(--muted, #9ca3af);
}
.ask-degraded code {
    background: rgba(255, 255, 255, 0.06);
    padding: 1px 5px;
    border-radius: 4px;
    font-size: 0.8rem;
}
.ask-form .ask-label {
    display: block;
    font-size: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--muted, #9ca3af);
    margin-bottom: 0.4rem;
}
.ask-row {
    display: flex;
    gap: 0.5rem;
    align-items: stretch;
}
.ask-input {
    flex: 1;
    padding: 0.55rem 0.75rem;
    border-radius: 6px;
    border: 1px solid rgba(255, 255, 255, 0.12);
    background: rgba(0, 0, 0, 0.30);
    color: inherit;
    font-size: 0.95rem;
}
[data-theme="light"] .ask-input {
    background: rgba(0, 0, 0, 0.02);
    border-color: rgba(0, 0, 0, 0.12);
}
.ask-submit {
    padding: 0.55rem 1.1rem;
    border-radius: 6px;
    border: 1px solid rgba(250, 204, 21, 0.5);
    background: rgba(250, 204, 21, 0.15);
    color: rgb(250, 204, 21);
    cursor: pointer;
    font-weight: 600;
}
.ask-submit:hover { background: rgba(250, 204, 21, 0.25); }
.ask-meta {
    margin-top: 0.4rem;
    font-size: 0.7rem;
    color: var(--muted, #9ca3af);
    display: flex;
    gap: 1rem;
}
.ask-answer {
    margin-top: 1rem;
    padding: 0.9rem 1rem;
    border-radius: 8px;
    background: rgba(255, 255, 255, 0.04);
    border: 1px solid rgba(255, 255, 255, 0.08);
}
[data-theme="light"] .ask-answer {
    background: rgba(0, 0, 0, 0.02);
    border-color: rgba(0, 0, 0, 0.08);
}
.ask-answer-text { white-space: pre-wrap; font-size: 0.95rem; line-height: 1.55; }
.ask-answer-list { margin: 0; padding-left: 1.3rem; }
.ask-answer-list li { margin: 0.25rem 0; }
.ask-answer-empty { color: var(--muted, #9ca3af); font-size: 0.9rem; margin: 0; }
"""


def ask_widget_js() -> str:
    """Return the JS source for the per-page ask widget."""
    return _ASK_WIDGET_JS


def ask_widget_css() -> str:
    """Return the CSS rules for the per-page ask widget."""
    return _ASK_WIDGET_CSS


__all__ = ["ask_widget_js", "ask_widget_css"]
