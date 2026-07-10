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
    return _req(port, path)


def _req(port, path, *, data=None, referer=None, origin=None, extra_headers=None):
    headers = {}
    if referer:
        headers["Referer"] = referer
    if origin:
        headers["Origin"] = origin
    if data is not None:
        headers["Content-Type"] = "application/json"
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=data, headers=headers,
        method="POST" if data is not None else "GET",
    )
    try:
        r = urllib.request.urlopen(req)
        return r.getcode(), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        # Return the error body too so callers can assert on 400/403/… payloads.
        return exc.code, exc.read().decode("utf-8", "replace")


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


def test_fleet_ask_health_404_without_referer(tmp_path):
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
    assert code == 404  # no Referer -> no project context


def test_fleet_ask_routes_to_project_by_referer(tmp_path, monkeypatch):
    """In-page ask works live in fleet mode, routed to the page's project."""
    import types

    from tesserae.serve import build_fleet_handler

    site = tmp_path / "p" / ".tesserae" / "site"
    site.mkdir(parents=True)
    (site / "index.html").write_text("<html><body>ok</body></html>", encoding="utf-8")
    root = tmp_path / "p"
    served = tmp_path / "served"
    served.mkdir()

    seen = {}
    monkeypatch.setattr("tesserae.project.ProjectWiki.load",
                        lambda r: types.SimpleNamespace(project_root=str(r)))

    def fake_ask(wiki, question, **kwargs):
        seen["root"] = wiki.project_root
        seen["use_llm"] = kwargs.get("use_llm")
        seen["no_llm"] = kwargs.get("no_llm")
        return {"answer": "hi", "question": question}

    monkeypatch.setattr("tesserae.query.ask_project", fake_ask)

    srv = _serve(build_fleet_handler(served_root=served, project_sites={"p": site}, project_roots={"p": root}))
    try:
        port = srv.server_address[1]
        page = f"http://127.0.0.1:{port}/p/index.html"
        health_with_ref, _ = _req(port, "/api/ask/health", referer=page)
        health_no_ref, _ = _req(port, "/api/ask/health")
        post_code, post_body = _req(port, "/api/ask", data=b'{"question":"hi"}', referer=page)
    finally:
        srv.shutdown()
        srv.server_close()
    assert health_with_ref == 200 and health_no_ref == 404  # live per page only
    assert post_code == 200 and "hi" in post_body
    assert str(seen.get("root")).endswith("p")  # routed to project 'p'
    # Fleet /api/ask mirrors single-project serve: LLM defaults OFF (widget latency).
    assert seen.get("use_llm") is False and seen.get("no_llm") is False


def test_fleet_ask_rejects_cross_origin_and_oversized_body(tmp_path):
    from tesserae.serve import build_fleet_handler

    site = tmp_path / "p" / ".tesserae" / "site"
    site.mkdir(parents=True)
    root = tmp_path / "p"
    served = tmp_path / "served"
    served.mkdir()
    srv = _serve(build_fleet_handler(served_root=served, project_sites={"p": site}, project_roots={"p": root}))
    try:
        port = srv.server_address[1]
        page = f"http://127.0.0.1:{port}/p/"
        # a hostile cross-origin page (even with a valid Referer) is rejected
        xo_code, _ = _req(port, "/api/ask", data=b'{"question":"x"}', referer=page, origin="http://evil.example")
        xo_health, _ = _req(port, "/api/ask/health", referer=page, origin="http://evil.example")
        # oversized body is rejected before it's read
        big = b'{"question":"' + b"a" * (300 * 1024) + b'"}'
        big_code, _ = _req(port, "/api/ask", data=big, referer=page)
    finally:
        srv.shutdown()
        srv.server_close()
    assert xo_code == 403 and xo_health == 403
    assert big_code == 413


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


# --------------------------------------------------------------------------- #
# Fleet clip: route a clip (from an EXTERNAL page) to the right project.       #
# --------------------------------------------------------------------------- #


def _fleet_clip_env(tmp_path, monkeypatch, aliases):
    """Build project_roots for ``aliases`` and stub the clip write/ingest path so
    no real graph/LLM work runs. Returns (project_roots, served, seen)."""
    import types

    roots = {}
    for alias in aliases:
        r = tmp_path / alias
        (r / ".tesserae" / "site").mkdir(parents=True)
        roots[alias] = r
    served = tmp_path / "served"
    served.mkdir()

    seen = {}
    monkeypatch.setattr("tesserae.project.ProjectWiki.load",
                        lambda r: types.SimpleNamespace(project_root=str(r)))

    def fake_write(wiki, **kw):
        seen["root"] = wiki.project_root
        return tmp_path / "clip.md"

    monkeypatch.setattr("tesserae.clip.write_clip_file", fake_write)
    monkeypatch.setattr("tesserae.clip.ingest_clip", lambda *a, **k: {"status": "deferred"})
    return roots, served, seen


def test_fleet_clip_routes_to_payload_project(tmp_path, monkeypatch):
    from tesserae.serve import build_fleet_handler

    roots, served, seen = _fleet_clip_env(tmp_path, monkeypatch, ["alpha", "beta"])
    sites = {a: r / ".tesserae" / "site" for a, r in roots.items()}
    srv = _serve(build_fleet_handler(served_root=served, project_sites=sites, project_roots=roots))
    try:
        port = srv.server_address[1]
        body = b'{"url":"http://ex.com/a","content":"hello world","project":"beta"}'
        code, resp = _req(port, "/api/clip", data=body)
    finally:
        srv.shutdown()
        srv.server_close()
    assert code == 202 and "accepted" in resp
    assert str(seen.get("root")).endswith("beta")  # routed to the named project


def test_fleet_clip_single_project_uses_it_without_project_field(tmp_path, monkeypatch):
    from tesserae.serve import build_fleet_handler

    roots, served, seen = _fleet_clip_env(tmp_path, monkeypatch, ["solo"])
    sites = {a: r / ".tesserae" / "site" for a, r in roots.items()}
    srv = _serve(build_fleet_handler(served_root=served, project_sites=sites, project_roots=roots))
    try:
        port = srv.server_address[1]
        body = b'{"url":"http://ex.com/a","content":"hello"}'  # no 'project'
        code, resp = _req(port, "/api/clip", data=body)
    finally:
        srv.shutdown()
        srv.server_close()
    assert code == 202 and str(seen.get("root")).endswith("solo")


def test_fleet_clip_multiple_projects_no_project_field_400(tmp_path, monkeypatch):
    from tesserae.serve import build_fleet_handler

    roots, served, seen = _fleet_clip_env(tmp_path, monkeypatch, ["alpha", "beta"])
    sites = {a: r / ".tesserae" / "site" for a, r in roots.items()}
    srv = _serve(build_fleet_handler(served_root=served, project_sites=sites, project_roots=roots))
    try:
        port = srv.server_address[1]
        body = b'{"url":"http://ex.com/a","content":"hello"}'  # ambiguous
        code, resp = _req(port, "/api/clip", data=body)
        # A garbage alias is equally ambiguous -> must NOT reach ProjectWiki.load.
        code2, resp2 = _req(port, "/api/clip",
                            data=b'{"url":"http://ex.com/a","content":"x","project":"nope"}')
    finally:
        srv.shutdown()
        srv.server_close()
    assert code == 400 and "specify 'project'" in resp
    assert "alpha" in resp and "beta" in resp  # lists available aliases
    assert code2 == 400 and "root" not in seen  # unknown alias never loaded


def test_fleet_clip_enforces_cors_gate(tmp_path, monkeypatch):
    from tesserae.serve import build_fleet_handler

    roots, served, seen = _fleet_clip_env(tmp_path, monkeypatch, ["solo"])
    sites = {a: r / ".tesserae" / "site" for a, r in roots.items()}
    srv = _serve(build_fleet_handler(served_root=served, project_sites=sites, project_roots=roots))
    try:
        port = srv.server_address[1]
        body = b'{"url":"http://ex.com/a","content":"hi"}'
        # A real website Origin is rejected before any work happens.
        web_code, _ = _req(port, "/api/clip", data=body, origin="http://evil.example")
        rejected_before_load = "root" not in seen  # the gate ran before any write
        # A browser-extension Origin is the intended caller -> allowed.
        ext_code, _ = _req(port, "/api/clip", data=body, origin="chrome-extension://bcggimpleodcbhkidhicnbdmedoceobd")
    finally:
        srv.shutdown()
        srv.server_close()
    assert web_code == 403 and rejected_before_load  # rejected before project load
    assert ext_code == 202  # extension clip accepted


def test_fleet_clip_enforces_token(tmp_path, monkeypatch):
    from tesserae.serve import build_fleet_handler

    roots, served, seen = _fleet_clip_env(tmp_path, monkeypatch, ["solo"])
    sites = {a: r / ".tesserae" / "site" for a, r in roots.items()}
    monkeypatch.setattr("tesserae.serve.configured_clip_token", lambda: "s3cret")
    srv = _serve(build_fleet_handler(served_root=served, project_sites=sites, project_roots=roots))
    try:
        port = srv.server_address[1]
        body = b'{"url":"http://ex.com/a","content":"hi"}'
        no_tok, _ = _req(port, "/api/clip", data=body)
        bad_tok, _ = _req(port, "/api/clip", data=body,
                          extra_headers={"X-Tesserae-Token": "wrong"})
        ok_tok, _ = _req(port, "/api/clip", data=body,
                         extra_headers={"X-Tesserae-Token": "s3cret"})
    finally:
        srv.shutdown()
        srv.server_close()
    assert no_tok == 401 and bad_tok == 401
    assert ok_tok == 202


def test_fleet_transcript_search_routes_by_referer(tmp_path, monkeypatch):
    from tesserae.serve import build_fleet_handler

    roots, served, _ = _fleet_clip_env(tmp_path, monkeypatch, ["p"])
    sites = {a: r / ".tesserae" / "site" for a, r in roots.items()}

    seen = {}

    def fake_search(query, **kwargs):
        seen["project"] = kwargs.get("project")
        return {"available": True, "results": [], "total": 0}

    monkeypatch.setattr("tesserae.memex_search.search_transcripts", fake_search)

    srv = _serve(build_fleet_handler(served_root=served, project_sites=sites, project_roots=roots))
    try:
        port = srv.server_address[1]
        page = f"http://127.0.0.1:{port}/p/sessions.html"
        with_ref, body = _req(port, "/api/transcript-search?q=hi", referer=page)
        no_ref, _ = _req(port, "/api/transcript-search?q=hi")
    finally:
        srv.shutdown()
        srv.server_close()
    assert with_ref == 200 and '"available"' in body
    assert seen.get("project") == "p"  # scoped to the page's project
    assert no_ref == 404  # unknown/missing alias -> no project context


def _options(port, path, *, origin=None):
    headers = {}
    if origin:
        headers["Origin"] = origin
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers=headers, method="OPTIONS")
    try:
        return urllib.request.urlopen(req).getcode()
    except urllib.error.HTTPError as exc:
        return exc.code


def test_fleet_transcript_search_passes_root_basename_not_alias(tmp_path, monkeypatch):
    """memex scopes by the project-root BASENAME (original case), not the
    registry alias (lowercased key) — the fleet route must pass the basename."""
    from tesserae.serve import build_fleet_handler

    root = tmp_path / "MyProj"                       # basename 'MyProj'
    (root / ".tesserae" / "site").mkdir(parents=True)
    served = tmp_path / "served"
    served.mkdir()
    roots = {"myproj": root}                          # registry alias 'myproj'
    sites = {"myproj": root / ".tesserae" / "site"}

    seen = {}

    def fake_search(query, **kwargs):
        seen["project"] = kwargs.get("project")
        return {"available": True, "results": [], "total": 0}

    monkeypatch.setattr("tesserae.memex_search.search_transcripts", fake_search)
    srv = _serve(build_fleet_handler(served_root=served, project_sites=sites, project_roots=roots))
    try:
        port = srv.server_address[1]
        code, _ = _req(port, "/api/transcript-search?q=hi", referer=f"http://127.0.0.1:{port}/myproj/sessions.html")
    finally:
        srv.shutdown()
        srv.server_close()
    assert code == 200 and seen.get("project") == "MyProj"  # basename, NOT the alias 'myproj'


def test_fleet_transcript_search_rejects_cross_origin(tmp_path, monkeypatch):
    from tesserae.serve import build_fleet_handler

    roots, served, _ = _fleet_clip_env(tmp_path, monkeypatch, ["p"])
    sites = {a: r / ".tesserae" / "site" for a, r in roots.items()}
    monkeypatch.setattr("tesserae.memex_search.search_transcripts",
                        lambda q, **kw: {"available": True, "results": []})
    srv = _serve(build_fleet_handler(served_root=served, project_sites=sites, project_roots=roots))
    try:
        port = srv.server_address[1]
        page = f"http://127.0.0.1:{port}/p/sessions.html"
        code, _ = _req(port, "/api/transcript-search?q=hi", referer=page, origin="http://evil.example")
    finally:
        srv.shutdown()
        srv.server_close()
    assert code == 403  # foreign Origin can't read local transcripts even with a valid Referer


def test_fleet_clip_empty_project_string_is_unset(tmp_path, monkeypatch):
    """The extension always sends 'project' (possibly ''); '' must behave as unset."""
    from tesserae.serve import build_fleet_handler

    roots, served, seen = _fleet_clip_env(tmp_path, monkeypatch, ["solo"])
    sites = {a: r / ".tesserae" / "site" for a, r in roots.items()}
    srv = _serve(build_fleet_handler(served_root=served, project_sites=sites, project_roots=roots))
    try:
        port = srv.server_address[1]
        code1, _ = _req(port, "/api/clip", data=b'{"url":"http://e/a","content":"x","project":""}')
    finally:
        srv.shutdown()
        srv.server_close()
    assert code1 == 202 and str(seen.get("root")).endswith("solo")  # '' -> sole project

    roots2, served2, seen2 = _fleet_clip_env(tmp_path / "multi", monkeypatch, ["alpha", "beta"])
    sites2 = {a: r / ".tesserae" / "site" for a, r in roots2.items()}
    srv2 = _serve(build_fleet_handler(served_root=served2, project_sites=sites2, project_roots=roots2))
    try:
        code2, _ = _req(srv2.server_address[1], "/api/clip", data=b'{"url":"http://e/a","content":"x","project":""}')
    finally:
        srv2.shutdown()
        srv2.server_close()
    assert code2 == 400 and "root" not in seen2  # '' with 2+ projects -> ambiguous 400


def test_fleet_clip_options_preflight(tmp_path, monkeypatch):
    from tesserae.serve import build_fleet_handler

    roots, served, _ = _fleet_clip_env(tmp_path, monkeypatch, ["solo"])
    sites = {a: r / ".tesserae" / "site" for a, r in roots.items()}
    srv = _serve(build_fleet_handler(served_root=served, project_sites=sites, project_roots=roots))
    try:
        port = srv.server_address[1]
        evil = _options(port, "/api/clip", origin="http://evil.example")
        ext = _options(port, "/api/clip", origin="chrome-extension://bcggimpleodcbhkidhicnbdmedoceobd")
    finally:
        srv.shutdown()
        srv.server_close()
    assert evil == 403   # foreign preflight rejected -> its POST never lands
    assert ext == 204    # extension preflight ok


def test_fleet_transcript_search_fails_closed_on_basename_collision(tmp_path, monkeypatch):
    """Two registered projects sharing a directory name are ONE memex namespace;
    transcript search must 409 rather than mix their session history (codex)."""
    from tesserae.serve import build_fleet_handler

    roots = {}
    for alias, sub in (("alpha", "a"), ("beta", "b")):
        r = tmp_path / sub / "App"                     # same basename 'App'
        (r / ".tesserae" / "site").mkdir(parents=True)
        roots[alias] = r
    sites = {a: r / ".tesserae" / "site" for a, r in roots.items()}
    served = tmp_path / "served"
    served.mkdir()

    called = {"n": 0}

    def fake_search(query, **kwargs):
        called["n"] += 1
        return {"available": True, "results": []}

    monkeypatch.setattr("tesserae.memex_search.search_transcripts", fake_search)
    srv = _serve(build_fleet_handler(served_root=served, project_sites=sites, project_roots=roots))
    try:
        port = srv.server_address[1]
        code, body = _req(port, "/api/transcript-search?q=hi", referer=f"http://127.0.0.1:{port}/alpha/sessions.html")
    finally:
        srv.shutdown()
        srv.server_close()
    assert code == 409 and "ambiguous" in body
    assert called["n"] == 0  # never queried memex under the ambiguous namespace
