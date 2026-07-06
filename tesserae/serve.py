"""HTTP handler factory used by ``tesserae serve``.

The default ``http.server.SimpleHTTPRequestHandler`` serves the compiled
static site. This module wraps it with two JSON routes used by the
per-page ask widget (Bet B3):

* ``GET /api/ask/health`` returns ``{"status": "ok"}``. The widget
  pings this on load to decide whether the backend is reachable; on
  failure it collapses to a one-line static footer.
* ``POST /api/ask`` accepts a JSON body
  ``{"node_id", "node_kind", "question", "backend"?, "top_k"?}`` and
  forwards the question to :func:`tesserae.query.ask_project`. The
  envelope ``ask_project`` returns is sent back verbatim.

Every other path falls through to the static-file handler so existing
static behaviour is preserved exactly.

The handler is constructed with :func:`build_ask_aware_handler` so the
project root can be baked into the class without globals — making the
handler easy to use both from the CLI ``serve`` command and from tests
that want to spin a tiny ``ThreadingTCPServer`` against a tmp project.
"""

from __future__ import annotations

import hmac
import http.server
import json
import os
import threading
from pathlib import Path
from typing import Optional, Tuple, Type
from urllib.parse import parse_qs, unquote, urlparse

from .live_sessions import CLAUDE_ONLY, live_session_list, live_transcript_search

# Hard ceiling on a clip request body. The extension caps captured content at
# ~200 KB client-side, but the server must never trust the client: a malicious
# or buggy caller could otherwise stream an arbitrary ``Content-Length`` into
# memory. 5 MB leaves generous headroom for legitimate clips while bounding the
# blast radius of a memory-exhaustion attempt.
_MAX_CLIP_BYTES = 5 * 1024 * 1024
# A question is small; cap the ask body so a remote caller can't force a large
# read or stall the single-threaded server.
_MAX_ASK_BYTES = 256 * 1024


def configured_clip_token() -> str:
    """The clip auth token, or "" when auth is off. Read FRESH each call so a
    user can rotate it with ``tesserae config clip-token`` without restarting
    the server: env ``TESSERAE_CLIP_TOKEN`` wins (fixed at launch), else the
    ``clip_token`` field of ``~/.tesserae/config.json`` (dynamic)."""
    env = (os.environ.get("TESSERAE_CLIP_TOKEN") or "").strip()
    if env:
        return env
    try:
        cfg = json.loads((Path.home() / ".tesserae" / "config.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return ""
    return str(cfg.get("clip_token") or "").strip() if isinstance(cfg, dict) else ""


# The published "Clip to Tesserae" Chrome Web Store extension id. Trusted by
# default so the shipped extension works with zero config; see
# _allowed_clip_extension_ids for how to trust additional ones.
PUBLISHED_CLIP_EXTENSION_ID = "bcggimpleodcbhkidhicnbdmedoceobd"


def _allowed_clip_extension_ids() -> set:
    """Extension ids whose ``chrome-extension://`` / ``moz-extension://`` origin
    may POST clips: the published extension, plus any in the ``clip_extension_ids``
    list of ``~/.tesserae/config.json`` (your own unpacked dev build, Firefox, a
    fork). Read FRESH each call like :func:`configured_clip_token`. Pinning to this
    set — instead of trusting ANY installed extension — stops a random/malicious
    extension the user happens to have from clipping into their local server."""
    ids = {PUBLISHED_CLIP_EXTENSION_ID}
    try:
        cfg = json.loads((Path.home() / ".tesserae" / "config.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        cfg = None
    extra = cfg.get("clip_extension_ids") if isinstance(cfg, dict) else None
    if isinstance(extra, list):
        ids.update(str(x).strip().lower() for x in extra if str(x).strip())
    return ids


# --------------------------------------------------------------------------- #
# Shared clip / transcript-search logic.                                       #
#                                                                              #
# Both the single-project handler (build_ask_aware_handler) and the multi-     #
# project fleet handler (build_fleet_handler) accept clips and run transcript  #
# searches. The security logic — the CORS origin policy, the clip-token check, #
# the body cap — must be IDENTICAL in both, so it lives here as module-level   #
# helpers parameterised by a resolved project root rather than being copied.   #
# --------------------------------------------------------------------------- #


def _eval_clip_origin(headers) -> Tuple[bool, Optional[str]]:
    """The clip CORS policy, as a pure function of request headers.

    Returns ``(allowed, origin_to_reflect)``. Allow browser-extension origins
    (``chrome-extension://`` / ``moz-extension://``) ONLY for an allow-listed
    extension id (see :func:`_allowed_clip_extension_ids`) and loopback http(s)
    origins; a real website's Origin, or an unknown extension, is rejected. No
    Origin header -> a non-browser / same-origin caller, allowed with nothing to
    reflect.
    """
    origin = headers.get("Origin")
    if not origin:
        return (True, None)
    if origin.startswith(("chrome-extension://", "moz-extension://")):
        ext_id = origin.split("://", 1)[1].split("/", 1)[0].strip().lower()
        return (True, origin) if ext_id in _allowed_clip_extension_ids() else (False, None)
    try:
        parsed = urlparse(origin)
    except ValueError:
        return (False, None)
    if parsed.scheme in ("http", "https") and parsed.hostname in (
        "localhost",
        "127.0.0.1",
        "::1",
    ):
        return (True, origin)
    return (False, None)


def _run_clip_preflight(handler) -> None:
    """CORS preflight (``OPTIONS``) for the clip endpoint, shared by both
    handlers. Reflect only a validated origin so a real website's preflight
    fails and its POST never reaches us."""
    allowed, reflect = _eval_clip_origin(handler.headers)
    if not allowed:
        handler.send_response(403)
        handler.send_header("Content-Length", "0")
        handler.end_headers()
        return
    handler.send_response(204)
    if reflect:
        handler.send_header("Access-Control-Allow-Origin", reflect)
        handler.send_header("Vary", "Origin")
    handler.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type, X-Tesserae-Token")
    if handler.headers.get("Access-Control-Request-Private-Network") == "true":
        handler.send_header("Access-Control-Allow-Private-Network", "true")
    handler.send_header("Content-Length", "0")
    handler.end_headers()


def _qs_int(qs: dict, key: str, default: int) -> int:
    """One positive int from a parsed query string, tolerating garbage."""
    try:
        return int((qs.get(key) or [str(default)])[0])
    except (ValueError, TypeError):
        return default


# Live serve scans claude only (slug-scoped, sub-second) and a short default
# window so the page load / search box stay snappy; the compiled page + memex
# index cover codex and anything older. See CLAUDE_ONLY in live_sessions.
_LIVE_DAYS_DEFAULT = 7


def _run_sessions(handler, query_string: str, *, project_root, project_name: str) -> None:
    """Reply with the project's CURRENT sessions, scanned live from the harness
    roots (recent-window bounded). The caller gates origin BEFORE calling this."""
    qs = parse_qs(query_string)
    sessions = live_session_list(
        [(project_name, Path(project_root))],
        days=_qs_int(qs, "days", _LIVE_DAYS_DEFAULT),
        max_turns=_qs_int(qs, "max_turns", 100_000),
        harnesses=CLAUDE_ONLY,
    )
    handler._send_json(200, {"sessions": sessions})


def _run_transcript_search(
    handler,
    query_string: str,
    *,
    project: Optional[str] = None,
    project_root=None,
    project_name: Optional[str] = None,
) -> None:
    """Execute a transcript search and reply, shared by both handlers. The
    caller is responsible for the origin/alias gate BEFORE calling this.
    ``project`` scopes the memex index search to one project (fleet); ``None``
    searches across all indexed transcripts. When ``project_root`` is given, a
    live recent-window scan runs too and its fresh hits are merged AHEAD of the
    (possibly lagging) index results — so newly-typed turns show up instantly."""
    from .memex_search import search_transcripts

    qs = parse_qs(query_string)
    query = (qs.get("q") or [""])[0]
    limit = _qs_int(qs, "limit", 20)
    result = search_transcripts(
        query,
        limit=limit,
        project=project,
        source=(qs.get("source") or [None])[0],
        hybrid=(qs.get("hybrid") or ["0"])[0] in ("1", "true"),
    )

    live_hits = []
    if project_root is not None and query.strip():
        live_hits = live_transcript_search(
            query,
            [(project_name or Path(project_root).name, Path(project_root))],
            days=_qs_int(qs, "days", _LIVE_DAYS_DEFAULT),
            max_turns=_qs_int(qs, "max_turns", 100_000),
            limit=limit,
            harnesses=CLAUDE_ONLY,
        )
    if live_hits:
        # Merge live ahead of the index, deduped by (session_id, ts) so a turn the
        # index already carries isn't shown twice, capped to the requested limit.
        seen = {(h.get("session_id"), h.get("ts")) for h in live_hits}
        index_results = [
            r for r in (result.get("results") or [])
            if (r.get("session_id") or r.get("session"), r.get("ts")) not in seen
        ]
        merged = (live_hits + index_results)[:limit]
        result = {
            "available": True,
            "results": merged,
            "total": len(merged),
            "live": len(live_hits),
        }
    handler._send_json(200, result)


def _run_clip(handler, *, resolve_root) -> None:
    """The full clip flow — origin gate, token gate, body cap, parse, validate,
    project load, durable write, background ingest, respond — shared by both
    handlers. ``resolve_root(payload)`` returns ``(root, None)`` to proceed
    against ``root``, or ``(None, (status, body))`` to short-circuit with an
    error (e.g. fleet's "specify 'project'"). ``handler`` must provide
    ``_send_json_cors(status, body, reflect)``."""
    allowed, reflect = _eval_clip_origin(handler.headers)
    if not allowed:
        handler._send_json_cors(403, {"error": "origin not allowed"}, reflect)
        return

    # Shared-secret auth (opt-in). Read fresh so the token can be rotated
    # without a restart. Constant-time compare.
    token = configured_clip_token()
    if token:
        provided = handler.headers.get("X-Tesserae-Token") or ""
        if not hmac.compare_digest(provided, token):
            handler._send_json_cors(401, {"error": "invalid or missing clip token"}, reflect)
            return

    # Bound the body BEFORE reading it into memory.
    try:
        length = int(handler.headers.get("Content-Length") or "0")
    except (TypeError, ValueError):
        handler._send_json_cors(400, {"error": "bad Content-Length"}, reflect)
        return
    if length > _MAX_CLIP_BYTES:
        handler._send_json_cors(413, {"error": "payload too large"}, reflect)
        return

    try:
        raw = handler.rfile.read(length) if length > 0 else b""
        payload = json.loads(raw.decode("utf-8") or "{}")
    except Exception as exc:  # pragma: no cover — request shape
        handler._send_json_cors(400, {"error": f"bad request: {exc}"}, reflect)
        return

    if not isinstance(payload, dict):
        handler._send_json_cors(400, {"error": "expected JSON object"}, reflect)
        return

    url = (payload.get("url") or "").strip()
    if not url:
        handler._send_json_cors(400, {"error": "url required"}, reflect)
        return

    selection = payload.get("selection")
    content = selection if (isinstance(selection, str) and selection.strip()) \
        else payload.get("content")
    if not isinstance(content, str) or not content.strip():
        handler._send_json_cors(400, {"error": "content required"}, reflect)
        return

    title = payload.get("title")
    note = payload.get("note")
    tags = payload.get("tags")
    tldr = payload.get("tldr")
    tldr = True if tldr is None else bool(tldr)

    # Resolve the target project (single: the baked root; fleet: by 'project'
    # alias). A garbage value never reaches ProjectWiki.load — resolve_root
    # only ever returns a registered root or an error.
    root, err = resolve_root(payload)
    if err is not None:
        handler._send_json_cors(err[0], err[1], reflect)
        return

    from .project import ProjectWiki
    from .clip import ingest_clip, write_clip_file

    try:
        wiki = ProjectWiki.load(root)
    except FileNotFoundError as exc:
        handler._send_json_cors(409, {"error": f"no project: {exc}"}, reflect)
        return
    except Exception as exc:
        handler._send_json_cors(500, {"error": f"clip failed: {exc}"}, reflect)
        return

    # Persist durably FIRST (fast), then run the slow ingest in a BACKGROUND
    # thread so the single-threaded server stays responsive.
    try:
        dest_path = write_clip_file(
            wiki, content=content, url=url, title=title, note=note, tags=tags
        )
    except Exception as exc:
        handler._send_json_cors(500, {"error": f"clip failed: {exc}"}, reflect)
        return

    label = url or title or dest_path.name

    def _ingest_async():
        print(f"[clip] received {label} -> {dest_path.name}; ingesting…", flush=True)
        try:
            report = ingest_clip(
                wiki, content=content, url=url, title=title,
                note=note, tags=tags, tldr=tldr,
            )
        except Exception as exc:  # noqa: BLE001 — log, never crash the thread
            print(f"[clip] ERROR ingesting {label}: {exc}", flush=True)
            return
        if report.get("status") == "deferred":
            print(f"[clip] deferred: {label} saved; will ingest on the next compile", flush=True)
        else:
            print(
                f"[clip] done: {label} — nodes={report.get('node_count')} "
                f"edges={report.get('edge_count')}",
                flush=True,
            )

    threading.Thread(target=_ingest_async, name="clip-ingest", daemon=True).start()
    handler._send_json_cors(202, {
        "status": "accepted",
        "path": str(dest_path),
        "detail": "clip saved; ingesting in the background — watch the tesserae serve log",
    }, reflect)


def build_ask_aware_handler(*, project_root: Path) -> Type[http.server.SimpleHTTPRequestHandler]:
    """Return a request handler class bound to ``project_root``.

    Keeping ``project_root`` on the class (rather than a module global)
    means multiple servers (e.g. one per test) can coexist without
    stepping on each other's wiki configuration.
    """

    resolved = Path(project_root).resolve()

    class _AskAwareHandler(http.server.SimpleHTTPRequestHandler):
        # Class attribute so tests can introspect / override.
        project_root: Path = resolved

        # ----------------------------------------------------------- CORS gate
        def _clip_origin(self) -> Tuple[bool, Optional[str]]:
            """The clip CORS policy (see :func:`_eval_clip_origin`). Kept as a
            thin instance method so the gate reads naturally at the call sites
            below; the policy itself is shared with the fleet handler."""
            return _eval_clip_origin(self.headers)

        # -------------------------------------------------------------- GET
        def do_GET(self):  # noqa: N802 — fixed by stdlib API
            parsed = urlparse(self.path)
            if parsed.path == "/api/ask/health":
                self._send_json(200, {"status": "ok"})
                return
            if parsed.path == "/api/transcript-search":
                # Reads LOCAL indexed transcripts, so it must not be callable
                # from arbitrary websites the user visits (a hostile page could
                # probe local history). Reuse the clip gate: a cross-site browser
                # Origin is rejected; same-origin / no-Origin / loopback /
                # extension callers pass. We also never emit
                # Access-Control-Allow-Origin, so a slipped-through cross-origin
                # request still can't READ the results.
                allowed, _ = self._clip_origin()
                if not allowed:
                    self._send_json(403, {"error": "forbidden"})
                    return
                # Index across all transcripts (project=None) + a live recent
                # scan of THIS project's roots merged ahead of the index.
                _run_transcript_search(
                    self, parsed.query,
                    project_root=self.project_root, project_name=self.project_root.name,
                )
                return
            if parsed.path == "/api/sessions":
                # Same local-history exposure as transcript-search — origin-gated.
                allowed, _ = self._clip_origin()
                if not allowed:
                    self._send_json(403, {"error": "forbidden"})
                    return
                _run_sessions(
                    self, parsed.query,
                    project_root=self.project_root, project_name=self.project_root.name,
                )
                return
            try:
                return super().do_GET()
            except (BrokenPipeError, ConnectionResetError):
                # The client disconnected before we finished streaming the
                # response (mobile network drop, browser back/refresh,
                # prefetcher cancellation). Routine on any HTTP server and
                # entirely harmless — but stdlib's http.server lets the
                # exception bubble all the way to the request thread,
                # producing a multi-line traceback for every cancelled
                # request. Swallow it so the operator log stays readable.
                return

        # ---------------------------------------------------------- exception
        def handle_one_request(self):  # noqa: N802 — fixed by stdlib API
            # Same protection one level up: even before ``do_GET`` runs the
            # request line may already be coming in over a half-closed
            # socket. Without this guard a ``ConnectionResetError`` during
            # ``self.raw_requestline = self.rfile.readline(...)`` propagates
            # out of the worker thread.
            try:
                return super().handle_one_request()
            except (BrokenPipeError, ConnectionResetError):
                self.close_connection = True
                return

        # ----------------------------------------------------------- OPTIONS
        def do_OPTIONS(self):  # noqa: N802 — fixed by stdlib API
            # CORS preflight for the clip endpoint (browser extension /
            # bookmarklet posts cross-origin). Shared with the fleet handler.
            _run_clip_preflight(self)

        # -------------------------------------------------------------- POST
        def do_POST(self):  # noqa: N802 — fixed by stdlib API
            parsed = urlparse(self.path)
            if parsed.path == "/api/clip":
                self._handle_clip()
                return
            if parsed.path != "/api/ask":
                self._send_json(404, {"error": "not found"})
                return

            try:
                length = int(self.headers.get("Content-Length") or "0")
                body = self.rfile.read(length) if length > 0 else b""
                payload = json.loads(body.decode("utf-8") or "{}")
            except Exception as exc:  # pragma: no cover — request shape
                self._send_json(400, {"error": f"bad request: {exc}"})
                return

            if not isinstance(payload, dict):
                self._send_json(400, {"error": "expected JSON object"})
                return

            question = (payload.get("question") or "").strip()
            if not question:
                self._send_json(400, {"error": "question required"})
                return

            backend = payload.get("backend") or "auto"
            try:
                top_k = int(payload.get("top_k") or 5)
            except (TypeError, ValueError):
                top_k = 5

            # Import inside the handler so importing this module stays
            # cheap (no model / wiki configuration touched at import time).
            from .project import ProjectWiki
            from .query import ask_project

            try:
                wiki = ProjectWiki.load(type(self).project_root)
                envelope = ask_project(
                    wiki,
                    question,
                    backend=backend,
                    top_k=top_k,
                )
            except Exception as exc:
                self._send_json(500, {"error": f"ask failed: {exc}"})
                return

            self._send_json(200, envelope)

        # -------------------------------------------------------------- clip
        def _handle_clip(self):
            # Single-project serve: every clip targets the one baked project
            # root. The full clip flow (gates, write, ingest) is shared with the
            # fleet handler via :func:`_run_clip`.
            _run_clip(self, resolve_root=lambda _payload: (type(self).project_root, None))

        # ---------------------------------------------------------- helpers
        def _send_json(self, status: int, body: dict) -> None:
            encoded = json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(encoded)

        def _send_json_cors(
            self, status: int, body: dict, origin: Optional[str] = None
        ) -> None:
            # Identical to _send_json but reflects a *validated* CORS origin so
            # the clip endpoint works from the extension/a localhost tool while
            # staying closed to arbitrary websites. ``origin`` is None for
            # non-browser callers (no Origin header) — nothing to echo.
            encoded = json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            if origin:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, format: str, *args):  # noqa: A002 — match stdlib
            # Suppress noisy "Bad request" logs from TLS scanners that try
            # to speak HTTPS to our plain HTTP socket.
            if args and isinstance(args[0], str) and args[0].startswith(("\\x16", "\\x17")):
                return
            super().log_message(format, *args)

    return _AskAwareHandler


# --------------------------------------------------------------------------- #
# Multi-project ("fleet") serving — one server for every registered project.   #
# --------------------------------------------------------------------------- #

# A self-contained Projects switcher injected into every served page. It reads
# /projects.json, drops into the page's header bar if one exists (so it sits ON
# the header) and otherwise pins to the top-right. Pure overlay — the per-project
# sites are served unchanged, so this never touches the site builder.
_PROJECTS_NAV = """
<style>
.tesserae-projects-nav{font:600 13px system-ui,-apple-system,sans-serif}
.tesserae-projects-nav.tpn-float{position:fixed;top:10px;right:14px;z-index:99999}
.tesserae-projects-nav details{position:relative;display:inline-block}
.tesserae-projects-nav summary{cursor:pointer;list-style:none;padding:6px 12px;border:1px solid #3a4254;border-radius:7px;background:#1b2030;color:#cde}
.tesserae-projects-nav summary::-webkit-details-marker{display:none}
.tesserae-projects-nav .tpn-menu{position:absolute;right:0;margin-top:4px;min-width:190px;display:flex;flex-direction:column;padding:5px;background:#161b27;border:1px solid #3a4254;border-radius:8px;box-shadow:0 6px 24px rgba(0,0,0,.4)}
.tesserae-projects-nav .tpn-menu a{padding:7px 11px;color:#bcd;text-decoration:none;border-radius:5px;white-space:nowrap}
.tesserae-projects-nav .tpn-menu a:hover{background:#252c3c;color:#fff}
.tesserae-projects-nav .tpn-menu a.tpn-active{color:#74e0c0}
.tesserae-projects-nav .tpn-menu hr{border:0;border-top:1px solid #2c3344;margin:4px 2px}
</style>
<script>
(function(){
  fetch('/projects.json').then(function(r){return r.json();}).then(function(ps){
    if(!Array.isArray(ps)) return;
    var cur=(location.pathname.split('/').filter(Boolean)[0]||'');
    var host=document.querySelector('.topbar, .site-header, header, .header');
    var box=document.createElement('div');
    box.className='tesserae-projects-nav'+(host?'':' tpn-float');
    var det=document.createElement('details');
    var sum=document.createElement('summary');
    sum.textContent=(cur?('Project: '+cur):'Projects')+' \\u25be';  // textContent -> no injection
    var menu=document.createElement('div'); menu.className='tpn-menu';
    var all=document.createElement('a'); all.href='/'; all.textContent='\\u2302 All projects'; menu.appendChild(all);
    menu.appendChild(document.createElement('hr'));
    ps.forEach(function(p){
      if(!p||typeof p.alias!=='string') return;
      var a=document.createElement('a');
      a.href='/'+encodeURIComponent(p.alias)+'/';
      if(p.alias===cur) a.className='tpn-active';
      a.textContent=p.alias;                                       // text, never HTML
      if(p.title&&p.title!==p.alias){
        var s=document.createElement('span');
        s.style.cssText='color:#7a8699;font-weight:400;margin-left:6px';
        s.textContent=String(p.title); a.appendChild(s);
      }
      menu.appendChild(a);
    });
    det.appendChild(sum); det.appendChild(menu); box.appendChild(det);
    (host||document.body).appendChild(box);
  }).catch(function(){});
})();
</script>
""".strip()


def _contained(target: Path, base: Path) -> bool:
    """True iff ``target`` (after resolving symlinks) stays under ``base``."""
    try:
        target.resolve().relative_to(base.resolve())
        return True
    except (ValueError, OSError):
        return False


def build_fleet_handler(
    *, served_root: Path, project_sites: dict, project_roots: Optional[dict] = None
) -> Type[http.server.SimpleHTTPRequestHandler]:
    """Serve a multi-project view from one server. ``served_root`` holds only the
    landing ``index.html`` + ``projects.json``; ``project_sites`` maps each alias
    to its REAL ``.tesserae/site`` dir (``project_roots`` maps alias -> project
    root for live ask). Requests are CONTAINED — a resolved path that escapes its
    alias's site dir (e.g. a planted symlink) is rejected — so no symlink tree and
    no traversal out of a project. The Projects switcher is injected into every
    HTML page. ``/api/ask*`` is routed to the project of the page that called it
    (via the Referer alias), so the in-page ask widget works live per project."""
    root = Path(served_root).resolve()
    sites = {alias: Path(d).resolve() for alias, d in project_sites.items()}
    roots = {alias: Path(r) for alias, r in (project_roots or {}).items()}
    # memex namespaces transcripts by the project-dir BASENAME, so two registered
    # roots that share a basename (e.g. ~/work/api and ~/side/api) are one memex
    # namespace and can't be scoped apart — transcript search must fail closed
    # for them rather than mix one project's session history into another's.
    basename_counts: dict = {}
    for _p in roots.values():
        basename_counts[_p.name] = basename_counts.get(_p.name, 0) + 1
    nav = _PROJECTS_NAV.encode("utf-8")

    class _FleetHandler(http.server.SimpleHTTPRequestHandler):
        def _alias_from_referer(self):
            """The project alias of the page that issued an /api/* call, or None."""
            try:
                ref_path = urlparse(self.headers.get("Referer") or "").path
            except ValueError:
                return None
            segs = [s for s in ref_path.split("/") if s]
            return segs[0] if segs and segs[0] in roots else None

        def _cross_origin(self) -> bool:
            """True if a browser Origin is present and is NOT same-origin. Blocks a
            hostile web page from forging a Referer to trigger ask for any project.
            A missing Origin (same-origin nav / non-browser) is allowed."""
            origin = self.headers.get("Origin")
            if not origin:
                return False
            try:
                origin_host = urlparse(origin).hostname
            except ValueError:
                return True
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
            return origin_host not in (host, "localhost", "127.0.0.1", "::1")

        def _send_json(self, status: int, obj):
            data = json.dumps(obj).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send_json_cors(self, status: int, obj, origin: Optional[str] = None):
            # Clip responses reflect a *validated* CORS origin (so the extension
            # / a localhost tool can read the reply) while staying closed to
            # arbitrary websites. Mirrors the single handler's variant.
            data = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            if origin:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
            self.end_headers()
            self.wfile.write(data)

        def _resolve_clip_root(self, payload):
            """Pick the target project root for a clip. A clip comes from an
            EXTERNAL page, so the Referer is NOT a /<alias>/ page — resolve by
            the body's 'project' alias instead. Order: (1) a registered 'project'
            alias; (2) the sole project if exactly one is registered; (3) else a
            400 listing the available aliases. A garbage alias never reaches
            ProjectWiki.load — only registered aliases map to roots."""
            alias = payload.get("project")
            if isinstance(alias, str) and alias in roots:
                return roots[alias], None
            if len(roots) == 1:
                return next(iter(roots.values())), None
            return None, (400, {"error": "specify 'project'", "available": sorted(roots)})

        def do_OPTIONS(self):  # noqa: N802 — fixed by stdlib API
            # CORS preflight for the clip endpoint (the extension posts
            # cross-origin from chrome-extension://). Shared with the single
            # handler. The fleet ask widget is same-origin and needs no preflight.
            _run_clip_preflight(self)

        def do_POST(self):  # noqa: N802 — fixed by stdlib API
            path = urlparse(self.path).path
            if path == "/api/clip":
                # Clip uses the extension/loopback CORS gate (not _cross_origin,
                # which would reject the chrome-extension:// origin). The shared
                # flow resolves the project from the body's 'project' alias.
                _run_clip(self, resolve_root=self._resolve_clip_root)
                return
            if path != "/api/ask":
                self._send_json(404, {"error": "not found"})
                return
            if self._cross_origin():
                self._send_json(403, {"error": "cross-origin request rejected"})
                return
            alias = self._alias_from_referer()
            if alias is None:
                self._send_json(404, {"error": "no project for this page"})
                return
            try:
                length = int(self.headers.get("Content-Length") or "0")
            except ValueError:
                self._send_json(400, {"error": "bad Content-Length"})
                return
            if length > _MAX_ASK_BYTES:
                self._send_json(413, {"error": "request too large"})
                return
            try:
                payload = json.loads((self.rfile.read(length) if length > 0 else b"").decode("utf-8") or "{}")
            except Exception as exc:
                self._send_json(400, {"error": f"bad request: {exc}"})
                return
            if not isinstance(payload, dict):
                self._send_json(400, {"error": "expected JSON object"})
                return
            question = (payload.get("question") or "").strip()
            if not question:
                self._send_json(400, {"error": "question required"})
                return
            backend = payload.get("backend") or "auto"
            try:
                top_k = int(payload.get("top_k") or 5)
            except (TypeError, ValueError):
                top_k = 5
            from .project import ProjectWiki
            from .query import ask_project

            try:
                wiki = ProjectWiki.load(roots[alias])
                envelope = ask_project(wiki, question, backend=backend, top_k=top_k)
            except Exception as exc:
                self._send_json(500, {"error": f"ask failed: {exc}"})
                return
            self._send_json(200, envelope)

        def _resolve(self, url_path: str):
            """Map a URL path to a contained filesystem path, or None if it would
            escape. Drops '.'/'..' segments, then enforces containment."""
            segs = [s for s in (unquote(url_path).split("/")) if s and s not in (".", "..")]
            if segs and segs[0] in sites:
                base, rel = sites[segs[0]], segs[1:]
            else:
                base, rel = root, segs
            target = base
            for seg in rel:
                target = target / seg
            if os.path.isdir(target):
                target = target / "index.html"
            return target if _contained(target, base) else None

        def do_GET(self):  # noqa: N802 — fixed by stdlib API
            parsed = urlparse(self.path)
            if parsed.path == "/api/ask/health":
                # Live per page: the widget's health-check resolves to the project
                # of the page that asked (Referer alias); unknown page -> 404.
                if self._cross_origin():
                    self._send_json(403, {"error": "cross-origin request rejected"})
                    return
                ok = self._alias_from_referer() is not None
                self._send_json(200 if ok else 404, {"status": "ok"} if ok else {"error": "no project"})
                return
            if parsed.path == "/api/transcript-search":
                # The sessions page calls this from /<alias>/ — route by the
                # Referer alias and scope the search to that project. Same
                # cross-origin guard as ask; unknown/missing alias -> 404.
                if self._cross_origin():
                    self._send_json(403, {"error": "cross-origin request rejected"})
                    return
                alias = self._alias_from_referer()
                if alias is None:
                    self._send_json(404, {"error": "no project"})
                    return
                # memex scopes transcripts by the session cwd BASENAME (original
                # case, e.g. 'Tesserae'), NOT the registry alias (lowercased key,
                # e.g. 'tesserae') — pass the project root's basename so the
                # filter actually matches.
                root = roots.get(alias)
                if root is None:
                    self._send_json(404, {"error": "no project"})
                    return
                if basename_counts.get(root.name, 0) > 1:
                    # Another registered project shares this directory name, so
                    # memex can't scope them apart — fail closed rather than leak
                    # one project's transcripts into the other (codex review).
                    self._send_json(409, {"error": (
                        f"transcript search is ambiguous: project '{alias}' shares its "
                        f"directory name '{root.name}' with another registered project, "
                        f"which memex indexes under the same namespace. Rename one "
                        f"project directory to disambiguate.")})
                    return
                _run_transcript_search(
                    self, parsed.query, project=root.name,
                    project_root=root, project_name=root.name,
                )
                return
            if parsed.path == "/api/sessions":
                # Live current-sessions for the page's project (Referer alias),
                # same cross-origin guard as ask/transcript-search.
                if self._cross_origin():
                    self._send_json(403, {"error": "cross-origin request rejected"})
                    return
                alias = self._alias_from_referer()
                root = roots.get(alias) if alias else None
                if root is None:
                    self._send_json(404, {"error": "no project"})
                    return
                _run_sessions(self, parsed.query, project_root=root, project_name=root.name)
                return
            if parsed.path == "/api/ask":
                self.send_response(405)
                self.end_headers()
                return
            target = self._resolve(parsed.path)
            if target is None:
                self.send_response(403)
                self.end_headers()
                return
            if not target.is_file():
                self.send_response(404)
                self.end_headers()
                return
            if target.suffix == ".html":
                body = target.read_bytes()
                marker = body.lower().rfind(b"</body>")
                body = body[:marker] + nav + body[marker:] if marker != -1 else body + nav
                ctype = "text/html; charset=utf-8"
            else:
                body = target.read_bytes()
                ctype = self.guess_type(str(target))
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args):  # noqa: A002 — match stdlib
            if args and isinstance(args[0], str) and args[0].startswith(("\\x16", "\\x17")):
                return
            super().log_message(format, *args)

    return _FleetHandler


__all__ = ["build_ask_aware_handler", "build_fleet_handler"]
