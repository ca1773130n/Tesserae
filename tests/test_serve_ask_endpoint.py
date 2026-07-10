"""Tests for the ``/api/ask`` endpoint served by ``tesserae serve``.

The handler is built by :func:`tesserae.serve.build_ask_aware_handler`
and wraps :class:`http.server.SimpleHTTPRequestHandler` with two JSON
routes (``GET /api/ask/health`` and ``POST /api/ask``). Everything else
falls through to static-file serving so the existing site keeps
working.

These tests start a real ``ThreadingTCPServer`` on a free port,
exchange requests via ``urllib``, then shut down cleanly.
``ask_project`` is monkeypatched to a stub so tests never reach a real
LLM.
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
# health endpoint
# ---------------------------------------------------------------------------


def test_serve_health_endpoint_returns_200(tmp_path: Path) -> None:
    project = _bootstrap_project(tmp_path)
    site_dir = project / ".tesserae" / "site"
    with _running_server(project, site_dir) as (host, port):
        with urllib.request.urlopen(
            f"http://{host}:{port}/api/ask/health", timeout=5
        ) as resp:
            assert resp.status == 200
            payload = json.loads(resp.read().decode("utf-8"))
            assert payload["status"] == "ok"


def test_serve_static_files_still_work(tmp_path: Path) -> None:
    """Non-``/api/...`` paths fall through to the static-file handler."""
    project = _bootstrap_project(tmp_path)
    site_dir = project / ".tesserae" / "site"
    with _running_server(project, site_dir) as (host, port):
        with urllib.request.urlopen(f"http://{host}:{port}/index.html", timeout=5) as resp:
            assert resp.status == 200
            body = resp.read().decode("utf-8")
            assert "<html>ok</html>" in body


# ---------------------------------------------------------------------------
# ask endpoint
# ---------------------------------------------------------------------------


def test_serve_ask_endpoint_delegates_to_ask_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /api/ask with a question reaches ``ask_project`` and returns its envelope."""
    project = _bootstrap_project(tmp_path)
    site_dir = project / ".tesserae" / "site"

    captured: dict = {}

    def _stub_ask_project(wiki, question, *, backend="auto", top_k=5, **kw):
        captured["question"] = question
        captured["backend"] = backend
        captured["top_k"] = top_k
        captured.update(kw)
        return {
            "backend": "raganything",
            "question": question,
            "answer": "stub-answer",
        }

    # Patch on the module the handler imports from (it does a late import).
    monkeypatch.setattr("tesserae.query.ask_project", _stub_ask_project)

    body = json.dumps(
        {
            "node_id": "concept:test",
            "node_kind": "concept",
            "question": "About `Foo`: What is foo?",
            "backend": "raganything",
        }
    ).encode("utf-8")

    with _running_server(project, site_dir) as (host, port):
        req = urllib.request.Request(
            f"http://{host}:{port}/api/ask",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert resp.status == 200
            envelope = json.loads(resp.read().decode("utf-8"))

    assert envelope["backend"] == "raganything"
    assert envelope["answer"] == "stub-answer"
    assert "About `Foo`: What is foo?" in envelope["question"]
    assert captured["backend"] == "raganything"
    # Widget latency: /api/ask defaults LLM OFF (unlike the CLI/MCP default).
    assert captured["use_llm"] is False
    assert captured["no_llm"] is False


def _post_ask(host: str, port: int, payload: dict) -> dict:
    req = urllib.request.Request(
        f"http://{host}:{port}/api/ask",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        assert resp.status == 200
        return json.loads(resp.read().decode("utf-8"))


def test_serve_ask_endpoint_accepts_llm_payload_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """{"llm": true} opts a single request into synthesis; {"llm": false}
    force-disables (beats TESSERAE_QUERY_LLM=1)."""
    project = _bootstrap_project(tmp_path)
    site_dir = project / ".tesserae" / "site"

    calls: list = []

    def _stub_ask_project(wiki, question, **kw):
        calls.append(kw)
        return {"backend": "wiki", "question": question, "answer": None, "hits": []}

    monkeypatch.setattr("tesserae.query.ask_project", _stub_ask_project)

    with _running_server(project, site_dir) as (host, port):
        _post_ask(host, port, {"question": "q1", "llm": True})
        _post_ask(host, port, {"question": "q2", "llm": False})
        _post_ask(host, port, {"question": "q3"})

    assert calls[0]["use_llm"] is True and calls[0]["no_llm"] is False
    assert calls[1]["use_llm"] is False and calls[1]["no_llm"] is True
    # No llm key at all: default off, but not the force-off (env may enable).
    assert calls[2]["use_llm"] is False and calls[2]["no_llm"] is False


def test_serve_ask_endpoint_rejects_empty_question(tmp_path: Path) -> None:
    project = _bootstrap_project(tmp_path)
    site_dir = project / ".tesserae" / "site"

    body = json.dumps({"question": "   "}).encode("utf-8")
    with _running_server(project, site_dir) as (host, port):
        req = urllib.request.Request(
            f"http://{host}:{port}/api/ask",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req, timeout=5)
        assert exc_info.value.code == 400


def test_serve_unknown_api_path_returns_404(tmp_path: Path) -> None:
    project = _bootstrap_project(tmp_path)
    site_dir = project / ".tesserae" / "site"

    with _running_server(project, site_dir) as (host, port):
        req = urllib.request.Request(
            f"http://{host}:{port}/api/unknown",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req, timeout=5)
        assert exc_info.value.code == 404


def test_serve_ask_endpoint_surfaces_backend_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If ``ask_project`` raises, the handler returns a 500 with the error message."""
    project = _bootstrap_project(tmp_path)
    site_dir = project / ".tesserae" / "site"

    def _boom(*args, **kw):
        raise RuntimeError("boom from backend")

    monkeypatch.setattr("tesserae.query.ask_project", _boom)

    body = json.dumps({"question": "anything"}).encode("utf-8")
    with _running_server(project, site_dir) as (host, port):
        req = urllib.request.Request(
            f"http://{host}:{port}/api/ask",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req, timeout=5)
        assert exc_info.value.code == 500
        payload = json.loads(exc_info.value.read().decode("utf-8"))
        assert "boom from backend" in payload["error"]
