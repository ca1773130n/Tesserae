"""Multi-project ('fleet') serving: landing, nav injection, and containment."""

from __future__ import annotations

import http.server
import threading
import urllib.error
import urllib.request


def test_fleet_landing_lists_every_project():
    from tesserae.cli import _fleet_landing_html

    html = _fleet_landing_html([{"alias": "alpha", "title": "Alpha"},
                                {"alias": "beta", "title": "Beta"}])
    assert 'href="/alpha/"' in html and 'href="/beta/"' in html
    assert "2 registered project" in html


def _serve(handler_cls):
    srv = http.server.HTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _get(port, path):
    try:
        r = urllib.request.urlopen(f"http://127.0.0.1:{port}{path}")
        return r.getcode(), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, ""


def test_fleet_handler_injects_nav_and_preserves_html(tmp_path):
    from tesserae.serve import build_fleet_handler

    served = tmp_path / "served"
    served.mkdir()
    (served / "index.html").write_text("<html><body><h1>hi</h1></body></html>", encoding="utf-8")
    (served / "projects.json").write_text('[{"alias":"alpha","title":"A"}]', encoding="utf-8")
    srv = _serve(build_fleet_handler(served_root=served, project_sites={}))
    try:
        code, body = _get(srv.server_address[1], "/")
    finally:
        srv.shutdown()
        srv.server_close()
    assert code == 200
    assert "tesserae-projects-nav" in body and "/projects.json" in body  # nav injected
    assert "<h1>hi</h1>" in body  # original preserved


def test_fleet_handler_is_browse_only(tmp_path):
    from tesserae.serve import build_fleet_handler

    served = tmp_path / "served"
    served.mkdir()
    (served / "index.html").write_text("<html><body>x</body></html>", encoding="utf-8")
    srv = _serve(build_fleet_handler(served_root=served, project_sites={}))
    try:
        code, _ = _get(srv.server_address[1], "/api/ask/health")
    finally:
        srv.shutdown()
        srv.server_close()
    assert code == 404  # fleet mode -> widgets fall back to static


def test_fleet_handler_rejects_symlink_escape(tmp_path):
    """A symlink inside a project site pointing OUTSIDE it must not be served."""
    from tesserae.serve import build_fleet_handler

    site = tmp_path / "p" / ".tesserae" / "site"
    site.mkdir(parents=True)
    (site / "index.html").write_text("<html><body>ok</body></html>", encoding="utf-8")
    secret = tmp_path / "secret.txt"
    secret.write_text("TOPSECRET", encoding="utf-8")
    (site / "leak.html").symlink_to(secret)  # escapes the site dir

    served = tmp_path / "served"
    served.mkdir()
    (served / "projects.json").write_text('[{"alias":"p","title":"P"}]', encoding="utf-8")
    srv = _serve(build_fleet_handler(served_root=served, project_sites={"p": site}))
    try:
        port = srv.server_address[1]
        escape_code, escape_body = _get(port, "/p/leak.html")
        _, dotdot_body = _get(port, "/p/../../secret.txt")
        ok_code, _ = _get(port, "/p/")
    finally:
        srv.shutdown()
        srv.server_close()
    assert escape_code in (403, 404) and "TOPSECRET" not in escape_body
    assert "TOPSECRET" not in dotdot_body  # '..' is stripped, never escapes
    assert ok_code == 200  # the legit page still serves
