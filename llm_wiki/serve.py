"""HTTP handler factory used by ``llm_wiki project serve``.

The default ``http.server.SimpleHTTPRequestHandler`` serves the compiled
static site. This module wraps it with two JSON routes used by the
per-page ask widget (Bet B3):

* ``GET /api/ask/health`` returns ``{"status": "ok"}``. The widget
  pings this on load to decide whether the backend is reachable; on
  failure it collapses to a one-line static footer.
* ``POST /api/ask`` accepts a JSON body
  ``{"node_id", "node_kind", "question", "backend"?, "top_k"?}`` and
  forwards the question to :func:`llm_wiki.query.ask_project`. The
  envelope ``ask_project`` returns is sent back verbatim.

Every other path falls through to the static-file handler so existing
static behaviour is preserved exactly.

The handler is constructed with :func:`build_ask_aware_handler` so the
project root can be baked into the class without globals — making the
handler easy to use both from the CLI ``serve`` command and from tests
that want to spin a tiny ``ThreadingTCPServer`` against a tmp project.
"""

from __future__ import annotations

import http.server
import json
from pathlib import Path
from typing import Type
from urllib.parse import urlparse


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

        # -------------------------------------------------------------- GET
        def do_GET(self):  # noqa: N802 — fixed by stdlib API
            parsed = urlparse(self.path)
            if parsed.path == "/api/ask/health":
                self._send_json(200, {"status": "ok"})
                return
            return super().do_GET()

        # -------------------------------------------------------------- POST
        def do_POST(self):  # noqa: N802 — fixed by stdlib API
            parsed = urlparse(self.path)
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

        # ---------------------------------------------------------- helpers
        def _send_json(self, status: int, body: dict) -> None:
            encoded = json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, format: str, *args):  # noqa: A002 — match stdlib
            # Suppress noisy "Bad request" logs from TLS scanners that try
            # to speak HTTPS to our plain HTTP socket.
            if args and isinstance(args[0], str) and args[0].startswith(("\\x16", "\\x17")):
                return
            super().log_message(format, *args)

    return _AskAwareHandler


__all__ = ["build_ask_aware_handler"]
