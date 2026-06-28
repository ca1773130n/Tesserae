"""Multi-project ('fleet') serving: landing page + Projects-nav injection."""

from __future__ import annotations

import http.server
import threading
import urllib.request


def test_fleet_landing_lists_every_project():
    from tesserae.cli import _fleet_landing_html

    html = _fleet_landing_html([{"alias": "alpha", "title": "Alpha"},
                                {"alias": "beta", "title": "Beta"}])
    assert 'href="/alpha/"' in html and 'href="/beta/"' in html
    assert "2 registered project" in html


def _serve_once(handler_cls):
    srv = http.server.HTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_fleet_handler_injects_nav_and_preserves_html(tmp_path):
    from tesserae.serve import build_fleet_handler

    (tmp_path / "index.html").write_text("<html><body><h1>hi</h1></body></html>", encoding="utf-8")
    (tmp_path / "projects.json").write_text('[{"alias":"alpha","title":"A"}]', encoding="utf-8")
    srv = _serve_once(build_fleet_handler(served_root=tmp_path))
    try:
        port = srv.server_address[1]
        body = urllib.request.urlopen(f"http://127.0.0.1:{port}/").read().decode()
    finally:
        srv.shutdown()
        srv.server_close()
    assert "tesserae-projects-nav" in body and "/projects.json" in body  # nav injected
    assert "<h1>hi</h1>" in body  # original page preserved


def test_fleet_handler_is_browse_only(tmp_path):
    from tesserae.serve import build_fleet_handler

    (tmp_path / "index.html").write_text("<html><body>x</body></html>", encoding="utf-8")
    srv = _serve_once(build_fleet_handler(served_root=tmp_path))
    try:
        port = srv.server_address[1]
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ask/health")
            code = 200
        except urllib.error.HTTPError as exc:
            code = exc.code
    finally:
        srv.shutdown()
        srv.server_close()
    assert code == 404  # fleet mode -> widgets fall back to static
