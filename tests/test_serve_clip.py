"""Tests for the ``/api/clip`` endpoint served by ``tesserae serve``.

The handler is built by :func:`tesserae.serve.build_ask_aware_handler`,
which (besides ``/api/ask``) routes ``POST /api/clip`` to the web-clip
ingestion path and answers ``OPTIONS /api/clip`` CORS preflights.

These tests start a real ``ThreadingTCPServer`` on a free port, exchange
requests via ``urllib``, then shut down cleanly. They never reach a real
LLM or the network:

* ``tesserae.clip.ingest_clip`` is monkeypatched to a stub, so neither the
  summarizer (Claude CLI) nor ``ingest_sources`` / ``wiki.compile()`` runs.
"""

from __future__ import annotations

import json
import socket
import socketserver
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Tuple

import pytest

from tesserae.serve import build_ask_aware_handler


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _bootstrap_project(tmp_path: Path) -> Path:
    """Minimal ``.tesserae/config.json`` so ``ProjectWiki.load`` succeeds."""
    project = tmp_path / "demo"
    project.mkdir()
    cfg = project / ".tesserae"
    cfg.mkdir()
    (cfg / "config.json").write_text(
        json.dumps({"name": "demo", "sources": ["README.md"], "external_tools": []}),
        encoding="utf-8",
    )
    (project / "README.md").write_text("# demo\n", encoding="utf-8")
    site = cfg / "site"
    site.mkdir()
    (site / "index.html").write_text("<html>ok</html>", encoding="utf-8")
    return project


@contextmanager
def _running_server(project_root: Path, site_dir: Path) -> Iterator[Tuple[str, int]]:
    handler_cls = build_ask_aware_handler(project_root=project_root)

    from functools import partial

    handler = partial(handler_cls, directory=str(site_dir))

    class _Reusable(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    port = _free_port()
    httpd = _Reusable(("127.0.0.1", port), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield ("127.0.0.1", port)
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


# ---------------------------------------------------------------------------
# POST /api/clip
# ---------------------------------------------------------------------------


def test_serve_clip_endpoint_delegates_to_ingest_clip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /api/clip reaches ``ingest_clip`` and returns its report + CORS header."""
    project = _bootstrap_project(tmp_path)
    site_dir = project / ".tesserae" / "site"

    captured: dict = {}

    def _stub_ingest_clip(wiki, *, content, url, title=None, note=None,
                          tags=None, tldr=True, clipped_at=None):
        captured.update(
            content=content, url=url, title=title, note=note,
            tags=tags, tldr=tldr, clipped_at=clipped_at,
        )
        return {
            "status": "ok",
            "path": str(project / "data" / "ingested" / "clip.md"),
            "tldr": "stub-tldr" if tldr else None,
            "node_count": 5,
            "edge_count": 2,
        }

    # The handler does a late import from tesserae.clip — patch on that module.
    monkeypatch.setattr("tesserae.clip.ingest_clip", _stub_ingest_clip)

    body = json.dumps(
        {
            "url": "https://example.com/post",
            "title": "A Post",
            "content": "Full article body.",
            "note": "later",
            "tags": ["x", "y"],
            "tldr": True,
        }
    ).encode("utf-8")

    with _running_server(project, site_dir) as (host, port):
        req = urllib.request.Request(
            f"http://{host}:{port}/api/clip",
            data=body,
            headers={
                "Content-Type": "application/json",
                # A real browser-extension origin: the handler must reflect it
                # back exactly (not "*"), proving the validated-CORS path.
                "Origin": "chrome-extension://bcggimpleodcbhkidhicnbdmedoceobd",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            # The clip is persisted synchronously and ACCEPTED immediately; the
            # ingest/compile runs in a background thread.
            assert resp.status == 202
            assert (
                resp.headers.get("Access-Control-Allow-Origin")
                == "chrome-extension://bcggimpleodcbhkidhicnbdmedoceobd"
            )
            report = json.loads(resp.read().decode("utf-8"))

        assert report["status"] == "accepted"
        assert "path" in report

        # ingest_clip runs asynchronously — wait for the background thread to
        # call it (still inside the server context so the thread is alive).
        deadline = time.time() + 3.0
        while "url" not in captured and time.time() < deadline:
            time.sleep(0.02)

    assert captured["url"] == "https://example.com/post"
    assert captured["title"] == "A Post"
    assert captured["content"] == "Full article body."
    assert captured["note"] == "later"
    assert captured["tags"] == ["x", "y"]


def test_serve_clip_endpoint_uses_selection_as_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When ``selection`` is present it overrides ``content``."""
    project = _bootstrap_project(tmp_path)
    site_dir = project / ".tesserae" / "site"

    captured: dict = {}

    def _stub_ingest_clip(wiki, *, content, url, **kw):
        captured["content"] = content
        return {
            "status": "ok",
            "path": str(project / "data" / "ingested" / "clip.md"),
            "tldr": None,
            "node_count": 1,
            "edge_count": 0,
        }

    monkeypatch.setattr("tesserae.clip.ingest_clip", _stub_ingest_clip)

    body = json.dumps(
        {
            "url": "https://example.com/post",
            "title": "A Post",
            "content": "The full page text.",
            "selection": "Just the highlighted bit.",
        }
    ).encode("utf-8")

    with _running_server(project, site_dir) as (host, port):
        req = urllib.request.Request(
            f"http://{host}:{port}/api/clip",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert resp.status == 202
        deadline = time.time() + 3.0
        while "content" not in captured and time.time() < deadline:
            time.sleep(0.02)

    assert captured["content"] == "Just the highlighted bit."


def test_serve_clip_options_preflight_returns_cors_headers(tmp_path: Path) -> None:
    """OPTIONS /api/clip is a CORS preflight: 204 + Access-Control-* headers."""
    project = _bootstrap_project(tmp_path)
    site_dir = project / ".tesserae" / "site"

    with _running_server(project, site_dir) as (host, port):
        req = urllib.request.Request(
            f"http://{host}:{port}/api/clip",
            headers={
                "Origin": "http://localhost:1234",
                "Access-Control-Request-Method": "POST",
            },
            method="OPTIONS",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert resp.status in (200, 204)
            # A loopback origin is allowed and reflected verbatim.
            assert (
                resp.headers.get("Access-Control-Allow-Origin")
                == "http://localhost:1234"
            )
            allow_methods = resp.headers.get("Access-Control-Allow-Methods") or ""
            assert "POST" in allow_methods
            assert "OPTIONS" in allow_methods
            allow_headers = resp.headers.get("Access-Control-Allow-Headers") or ""
            assert "Content-Type" in allow_headers


def test_serve_clip_options_grants_private_network_access(tmp_path: Path) -> None:
    """A PNA preflight (Access-Control-Request-Private-Network) is answered with
    the matching allow header, so a Web-Store extension can reach localhost."""
    project = _bootstrap_project(tmp_path)
    site_dir = project / ".tesserae" / "site"

    with _running_server(project, site_dir) as (host, port):
        req = urllib.request.Request(
            f"http://{host}:{port}/api/clip",
            headers={
                "Origin": "chrome-extension://bcggimpleodcbhkidhicnbdmedoceobd",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Private-Network": "true",
            },
            method="OPTIONS",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert resp.status in (200, 204)
            assert (
                resp.headers.get("Access-Control-Allow-Private-Network") == "true"
            )


def test_serve_clip_rejects_foreign_origin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A POST from an arbitrary website is rejected with 403 and never reaches
    ``ingest_clip`` — the CSRF guard."""
    project = _bootstrap_project(tmp_path)
    site_dir = project / ".tesserae" / "site"

    called = {"hit": False}

    def _stub_ingest_clip(*args, **kw):
        called["hit"] = True
        return {"status": "ok", "path": "x", "tldr": None,
                "node_count": 0, "edge_count": 0}

    monkeypatch.setattr("tesserae.clip.ingest_clip", _stub_ingest_clip)

    body = json.dumps({"url": "https://example.com/x", "content": "body"}).encode("utf-8")
    with _running_server(project, site_dir) as (host, port):
        req = urllib.request.Request(
            f"http://{host}:{port}/api/clip",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Origin": "https://evil.example.com",
            },
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req, timeout=5)
        assert exc_info.value.code == 403

    # The write path must not have run.
    assert called["hit"] is False


def test_serve_clip_synchronous_persist_failure_is_500(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the SYNCHRONOUS persist (``write_clip_file``) fails, the handler returns
    500 with the message — that's the only clip error the client can see now."""
    project = _bootstrap_project(tmp_path)
    site_dir = project / ".tesserae" / "site"

    def _boom(*args, **kw):
        raise RuntimeError("boom from write")

    monkeypatch.setattr("tesserae.clip.write_clip_file", _boom)

    body = json.dumps({"url": "https://example.com/x", "content": "body"}).encode("utf-8")
    with _running_server(project, site_dir) as (host, port):
        req = urllib.request.Request(
            f"http://{host}:{port}/api/clip",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req, timeout=5)
        assert exc_info.value.code == 500
        payload = json.loads(exc_info.value.read().decode("utf-8"))
        assert "boom from write" in payload["error"]


def test_serve_clip_async_ingest_error_still_accepts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure in the BACKGROUND ingest must not affect the client: the clip was
    already persisted + ACCEPTED (202); the error is only logged to stdout."""
    project = _bootstrap_project(tmp_path)
    site_dir = project / ".tesserae" / "site"

    def _boom(*args, **kw):
        raise RuntimeError("boom from ingest")

    monkeypatch.setattr("tesserae.clip.ingest_clip", _boom)

    body = json.dumps({"url": "https://example.com/x", "content": "body"}).encode("utf-8")
    with _running_server(project, site_dir) as (host, port):
        req = urllib.request.Request(
            f"http://{host}:{port}/api/clip",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert resp.status == 202
            assert json.loads(resp.read().decode("utf-8"))["status"] == "accepted"
    # The clip file was written synchronously despite the async ingest failure.
    assert list((project / "data" / "ingested").glob("*.md"))


def _clip_request(host, port, *, token_header=None):
    body = json.dumps({"url": "https://example.com/x", "content": "body"}).encode("utf-8")
    headers = {"Content-Type": "application/json", "Origin": "chrome-extension://bcggimpleodcbhkidhicnbdmedoceobd"}
    if token_header is not None:
        headers["X-Tesserae-Token"] = token_header
    return urllib.request.Request(
        f"http://{host}:{port}/api/clip", data=body, headers=headers, method="POST"
    )


def test_serve_clip_requires_token_when_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With TESSERAE_CLIP_TOKEN set, a clip without the matching header is 401."""
    project = _bootstrap_project(tmp_path)
    site_dir = project / ".tesserae" / "site"
    monkeypatch.setenv("TESSERAE_CLIP_TOKEN", "s3cret")
    monkeypatch.setattr("tesserae.clip.write_clip_file",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run")))

    with _running_server(project, site_dir) as (host, port):
        # Missing token → 401.
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(_clip_request(host, port), timeout=5)
        assert exc.value.code == 401
        # Wrong token → 401.
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(_clip_request(host, port, token_header="nope"), timeout=5)
        assert exc.value.code == 401


def test_serve_clip_accepts_matching_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _bootstrap_project(tmp_path)
    site_dir = project / ".tesserae" / "site"
    monkeypatch.setenv("TESSERAE_CLIP_TOKEN", "s3cret")
    monkeypatch.setattr("tesserae.clip.ingest_clip", lambda *a, **k: {"status": "ok", "node_count": 0, "edge_count": 0})

    with _running_server(project, site_dir) as (host, port):
        with urllib.request.urlopen(_clip_request(host, port, token_header="s3cret"), timeout=5) as resp:
            assert resp.status == 202


def test_serve_clip_token_read_dynamically_from_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The token can be set in ~/.tesserae/config.json (no env, no restart) and
    is read fresh per request — so `tesserae config clip-token` rotates it live."""
    project = _bootstrap_project(tmp_path)
    site_dir = project / ".tesserae" / "site"
    monkeypatch.delenv("TESSERAE_CLIP_TOKEN", raising=False)
    home = tmp_path / "home"
    (home / ".tesserae").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    (home / ".tesserae" / "config.json").write_text('{"clip_token": "cfg-key"}', encoding="utf-8")
    monkeypatch.setattr("tesserae.clip.ingest_clip", lambda *a, **k: {"status": "ok", "node_count": 0, "edge_count": 0})

    with _running_server(project, site_dir) as (host, port):
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(_clip_request(host, port), timeout=5)
        assert exc.value.code == 401
        with urllib.request.urlopen(_clip_request(host, port, token_header="cfg-key"), timeout=5) as resp:
            assert resp.status == 202


def test_serve_clip_options_allows_token_header(tmp_path: Path) -> None:
    """The CORS preflight must permit X-Tesserae-Token so the browser sends it."""
    project = _bootstrap_project(tmp_path)
    site_dir = project / ".tesserae" / "site"
    with _running_server(project, site_dir) as (host, port):
        req = urllib.request.Request(
            f"http://{host}:{port}/api/clip",
            headers={"Origin": "chrome-extension://bcggimpleodcbhkidhicnbdmedoceobd", "Access-Control-Request-Method": "POST"},
            method="OPTIONS",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert "X-Tesserae-Token" in (resp.headers.get("Access-Control-Allow-Headers") or "")


def test_clip_origin_pins_to_published_extension(tmp_path, monkeypatch):
    """Only the published extension id (and loopback) may clip — an ARBITRARY
    installed extension is rejected, which is the whole point of the pin."""
    from tesserae.serve import _eval_clip_origin, PUBLISHED_CLIP_EXTENSION_ID

    monkeypatch.setenv("HOME", str(tmp_path))  # no ~/.tesserae/config.json -> just the published id
    ev = lambda o: _eval_clip_origin({"Origin": o} if o else {})

    pub = f"chrome-extension://{PUBLISHED_CLIP_EXTENSION_ID}"
    assert ev(pub) == (True, pub)                                         # published -> allowed + reflected
    assert ev("chrome-extension://someotherinstalledextension0000")[0] is False  # unknown -> rejected
    assert ev("moz-extension://11111111-2222-3333-4444-555555555555")[0] is False
    assert ev("http://127.0.0.1:8765")[0] is True                        # loopback unchanged
    assert ev(None) == (True, None)                                      # non-browser caller
    assert ev("https://evil.example.com")[0] is False                    # a real website


def test_clip_extension_ids_config_allowlist(tmp_path, monkeypatch):
    """A dev build / fork id added to clip_extension_ids is trusted too."""
    from tesserae import serve

    (tmp_path / ".tesserae").mkdir()
    (tmp_path / ".tesserae" / "config.json").write_text(
        json.dumps({"clip_extension_ids": ["mydevbuildextensionidaaaaaaaaaaaa"]}), encoding="utf-8"
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    ev = lambda o: serve._eval_clip_origin({"Origin": o})

    assert ev("chrome-extension://mydevbuildextensionidaaaaaaaaaaaa")[0] is True   # config-allowed
    assert ev(f"chrome-extension://{serve.PUBLISHED_CLIP_EXTENSION_ID}")[0] is True  # published still allowed
    assert ev("chrome-extension://notinthelistxxxxxxxxxxxxxxxxxxxx")[0] is False     # still pinned
