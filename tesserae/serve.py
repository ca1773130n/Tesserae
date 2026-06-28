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

# Hard ceiling on a clip request body. The extension caps captured content at
# ~200 KB client-side, but the server must never trust the client: a malicious
# or buggy caller could otherwise stream an arbitrary ``Content-Length`` into
# memory. 5 MB leaves generous headroom for legitimate clips while bounding the
# blast radius of a memory-exhaustion attempt.
_MAX_CLIP_BYTES = 5 * 1024 * 1024


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
            """Decide whether the request may write a clip, and what origin to
            reflect back.

            The clip endpoint mutates the knowledge graph and can trigger an
            LLM summarization, so it must not be callable from arbitrary web
            pages the user happens to visit (CSRF). The policy:

            * No ``Origin`` header -> a non-browser caller (curl, a same-origin
              widget, the test client). Allow it; CORS reflection is moot.
            * A browser extension origin (``chrome-extension://`` /
              ``moz-extension://``) -> the intended caller. Allow + reflect.
            * An ``http(s)`` origin whose host is loopback -> a local tool /
              dev page. Allow + reflect.
            * Anything else (a real website) -> reject.

            Returns ``(allowed, origin_to_reflect)``. ``origin_to_reflect`` is
            ``None`` when there is nothing to echo (no Origin header).
            """
            origin = self.headers.get("Origin")
            if not origin:
                return (True, None)
            if origin.startswith(("chrome-extension://", "moz-extension://")):
                return (True, origin)
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
                from .memex_search import search_transcripts

                qs = parse_qs(parsed.query)
                query = (qs.get("q") or [""])[0]
                try:
                    limit = int((qs.get("limit") or ["20"])[0])
                except ValueError:
                    limit = 20
                result = search_transcripts(
                    query,
                    limit=limit,
                    source=(qs.get("source") or [None])[0],
                    hybrid=(qs.get("hybrid") or ["0"])[0] in ("1", "true"),
                )
                self._send_json(200, result)
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
            # bookmarklet posts cross-origin). Reflect only a validated origin
            # so a real website's preflight fails and its POST is blocked by
            # the browser before it ever reaches us.
            allowed, reflect = self._clip_origin()
            if not allowed:
                self.send_response(403)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(204)
            if reflect:
                self.send_header("Access-Control-Allow-Origin", reflect)
                self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Tesserae-Token")
            # Private Network Access: Chrome gates requests that target a more
            # private address space (localhost) than the initiator. A Web Store
            # extension posting to http://localhost trips this, sending a
            # preflight with `Access-Control-Request-Private-Network: true`. We
            # must answer with the matching allow header or the POST is blocked.
            if self.headers.get("Access-Control-Request-Private-Network") == "true":
                self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Content-Length", "0")
            self.end_headers()

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
            # Reject cross-origin writes from arbitrary websites before doing
            # any work (parsing, project load, LLM call). ``reflect`` is the
            # origin to echo on every response below.
            allowed, reflect = self._clip_origin()
            if not allowed:
                self._send_json_cors(403, {"error": "origin not allowed"}, reflect)
                return

            # Shared-secret auth (opt-in). When a clip token is configured (env
            # or `tesserae config clip-token`), every clip must carry a matching
            # X-Tesserae-Token header — so an endpoint bound to 0.0.0.0 / a public
            # IP can't be written to by anyone who reaches the port (the origin
            # gate alone is forgeable). No token => open, as before. Read fresh so
            # the token can be rotated without a restart. Constant-time compare.
            token = configured_clip_token()
            if token:
                provided = self.headers.get("X-Tesserae-Token") or ""
                if not hmac.compare_digest(provided, token):
                    self._send_json_cors(401, {"error": "invalid or missing clip token"}, reflect)
                    return

            # Bound the body BEFORE reading it into memory. The extension caps
            # content client-side, but the server must enforce its own limit.
            try:
                length = int(self.headers.get("Content-Length") or "0")
            except (TypeError, ValueError):
                self._send_json_cors(400, {"error": "bad Content-Length"}, reflect)
                return
            if length > _MAX_CLIP_BYTES:
                self._send_json_cors(413, {"error": "payload too large"}, reflect)
                return

            # Read + parse the JSON body exactly like /api/ask does.
            try:
                raw = self.rfile.read(length) if length > 0 else b""
                payload = json.loads(raw.decode("utf-8") or "{}")
            except Exception as exc:  # pragma: no cover — request shape
                self._send_json_cors(400, {"error": f"bad request: {exc}"}, reflect)
                return

            if not isinstance(payload, dict):
                self._send_json_cors(400, {"error": "expected JSON object"}, reflect)
                return

            url = (payload.get("url") or "").strip()
            if not url:
                self._send_json_cors(400, {"error": "url required"}, reflect)
                return

            # Selection wins over full content when present and non-empty.
            selection = payload.get("selection")
            content = selection if (isinstance(selection, str) and selection.strip()) \
                else payload.get("content")
            if not isinstance(content, str) or not content.strip():
                self._send_json_cors(400, {"error": "content required"}, reflect)
                return

            title = payload.get("title")
            note = payload.get("note")
            tags = payload.get("tags")
            tldr = payload.get("tldr")
            tldr = True if tldr is None else bool(tldr)

            # Import inside the handler so importing this module stays cheap
            # (the static-file path never touches clip/ingest machinery).
            from .project import ProjectWiki
            from .clip import ingest_clip, write_clip_file

            try:
                wiki = ProjectWiki.load(type(self).project_root)
            except FileNotFoundError as exc:
                self._send_json_cors(409, {"error": f"no project: {exc}"}, reflect)
                return
            except Exception as exc:
                self._send_json_cors(500, {"error": f"clip failed: {exc}"}, reflect)
                return

            # Persist the clip durably FIRST (fast — no LLM, no compile) so the
            # response is a truthful "accepted", then run the slow ingest/compile
            # in a BACKGROUND thread. The static server is single-threaded, so a
            # synchronous compile would block every other request (and the
            # extension would hang); returning immediately lets the extension
            # show success at once. The thread prints progress to this server's
            # stdout so the operator can watch each clip land.
            try:
                dest_path = write_clip_file(
                    wiki, content=content, url=url, title=title, note=note, tags=tags
                )
            except Exception as exc:
                self._send_json_cors(500, {"error": f"clip failed: {exc}"}, reflect)
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
            self._send_json_cors(202, {
                "status": "accepted",
                "path": str(dest_path),
                "detail": "clip saved; ingesting in the background — watch the tesserae serve log",
            }, reflect)

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

        def _send_json(self, status: int, obj):
            data = json.dumps(obj).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):  # noqa: N802 — fixed by stdlib API
            if urlparse(self.path).path != "/api/ask":
                self._send_json(404, {"error": "not found"})
                return
            alias = self._alias_from_referer()
            if alias is None:
                self._send_json(404, {"error": "no project for this page"})
                return
            try:
                length = int(self.headers.get("Content-Length") or "0")
                payload = json.loads((self.rfile.read(length) if length > 0 else b"").decode("utf-8") or "{}")
            except Exception as exc:
                self._send_json(400, {"error": f"bad request: {exc}"})
                return
            question = (payload.get("question") or "").strip() if isinstance(payload, dict) else ""
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
                ok = self._alias_from_referer() is not None
                self._send_json(200 if ok else 404, {"status": "ok"} if ok else {"error": "no project"})
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
