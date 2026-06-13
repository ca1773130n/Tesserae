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
                "Origin": "chrome-extension://abcdefgh",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert resp.status == 200
            assert (
                resp.headers.get("Access-Control-Allow-Origin")
                == "chrome-extension://abcdefgh"
            )
            report = json.loads(resp.read().decode("utf-8"))

    assert report["status"] == "ok"
    assert report["node_count"] == 5
    assert report["edge_count"] == 2
    assert report["tldr"] == "stub-tldr"
    assert "path" in report

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
            assert resp.status == 200

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
                "Origin": "chrome-extension://abcdefgh",
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


def test_serve_clip_endpoint_surfaces_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If ``ingest_clip`` raises, the handler returns a 500 with the message."""
    project = _bootstrap_project(tmp_path)
    site_dir = project / ".tesserae" / "site"

    def _boom(*args, **kw):
        raise RuntimeError("boom from clip")

    monkeypatch.setattr("tesserae.clip.ingest_clip", _boom)

    body = json.dumps(
        {"url": "https://example.com/x", "content": "body"}
    ).encode("utf-8")
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
        assert "boom from clip" in payload["error"]
