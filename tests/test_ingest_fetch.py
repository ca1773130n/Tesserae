import pytest

from tesserae.ingest.fetch import is_url
from tesserae.ingest.fetch import _slugify, _render_frontmatter
from tesserae.ingest.fetch import fetch_to_source
from tesserae.ingest.fetch import _arxiv_id_from_url


class _FakeResponse:
    def __init__(self, *, status_code=200, text="", content_type="text/html"):
        self.status_code = status_code
        self.text = text
        self.headers = {"content-type": content_type}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _fake_get(response):
    def _get(url, timeout=None, follow_redirects=True, headers=None):
        return response
    return _get


def test_is_url_true_for_http_and_https():
    assert is_url("http://example.com/a")
    assert is_url("https://arxiv.org/abs/2401.12345")


def test_is_url_false_for_paths():
    assert not is_url("notes/a.md")
    assert not is_url("/abs/path/to/file.md")
    assert not is_url("./relative.md")
    assert not is_url("ftp://example.com/x")  # only http(s) is a URL for ingest


def test_slugify_url_is_filesystem_safe_and_stable():
    slug = _slugify("https://arxiv.org/abs/2401.12345")
    assert slug == "arxiv-org-abs-2401-12345"
    assert _slugify("https://arxiv.org/abs/2401.12345") == slug


def test_slugify_truncates_long_urls_with_hash_suffix():
    long = "https://example.com/" + "a" * 200
    slug = _slugify(long)
    assert len(slug) <= 80
    other = "https://example.com/" + "a" * 199 + "b"
    assert _slugify(long) != _slugify(other)


def test_render_frontmatter_emits_sorted_yaml_block():
    fm = _render_frontmatter(
        {"source_url": "https://x.com", "content_sha256": "abc", "fetched_at": "2026-06-10T00:00:00Z"}
    )
    assert fm.startswith("---\n")
    assert fm.rstrip().endswith("---")
    assert "source_url: https://x.com" in fm
    assert fm.index("content_sha256") < fm.index("fetched_at") < fm.index("source_url")


def test_render_frontmatter_escapes_unsafe_values():
    fm = _render_frontmatter({"title": "Bad: title\ninjected: pwned", "source_url": "https://x.com"})
    # the malicious value must NOT create a sibling 'injected' key or break the block
    import re
    # exactly two '---' fences and exactly the keys we passed, one per line between them
    body = fm.split("---\n")[1] if fm.startswith("---\n") else fm
    inner = fm.strip().split("\n")[1:-1]  # lines between the --- fences
    keys = [ln.split(":", 1)[0].strip() for ln in inner]
    assert sorted(keys) == ["source_url", "title"], f"unexpected keys leaked: {keys}"
    # round-trips through a YAML parser without error and preserves the title value
    import yaml  # PyYAML is a test/dev dep; if unavailable, fall back to the manual check below
    parsed = yaml.safe_load(fm.strip().strip("-").strip() if False else "\n".join(inner))
    assert parsed["title"] == "Bad: title\ninjected: pwned"
    assert parsed["source_url"] == "https://x.com"


def test_fetch_html_writes_markdown_file_with_frontmatter(tmp_path, monkeypatch):
    html = "<html><body><h1>Title</h1><p>Hello world</p></body></html>"
    monkeypatch.setattr(
        "tesserae.ingest.fetch._http_get",
        _fake_get(_FakeResponse(text=html, content_type="text/html")),
    )
    dest = tmp_path / "data" / "ingested"
    path = fetch_to_source("https://example.com/post", dest)

    assert path.parent == dest
    assert path.suffix == ".md"
    body = path.read_text(encoding="utf-8")
    assert body.startswith("---\n")
    assert "source_url: https://example.com/post" in body
    assert "content_sha256:" in body
    assert "fetched_at:" in body
    assert "Title" in body
    assert "<h1>" not in body


def test_fetch_non_2xx_raises_and_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "tesserae.ingest.fetch._http_get",
        _fake_get(_FakeResponse(status_code=404, content_type="text/html")),
    )
    dest = tmp_path / "data" / "ingested"
    with pytest.raises(RuntimeError):
        fetch_to_source("https://example.com/missing", dest)
    assert not dest.exists() or not any(dest.iterdir())


def test_fetch_missing_extra_raises_actionable_error(tmp_path, monkeypatch):
    def _boom():
        raise ImportError("URL ingest requires the optional extra: pip install tesserae[ingest-url]")
    monkeypatch.setattr("tesserae.ingest.fetch._http_get", None)
    monkeypatch.setattr("tesserae.ingest.fetch._html_to_markdown", None)
    monkeypatch.setattr("tesserae.ingest.fetch._load_url_deps", _boom)
    with pytest.raises(ImportError, match=r"pip install tesserae\[ingest-url\]"):
        fetch_to_source("https://example.com/post", tmp_path)


def test_arxiv_id_extracted_from_abs_url():
    assert _arxiv_id_from_url("https://arxiv.org/abs/2401.12345") == "2401.12345"
    assert _arxiv_id_from_url("https://arxiv.org/abs/2401.12345v2") == "2401.12345"
    assert _arxiv_id_from_url("https://example.com/post") is None


def test_fetch_arxiv_url_records_arxiv_id_in_frontmatter(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "tesserae.ingest.fetch._http_get",
        _fake_get(_FakeResponse(text="<p>abstract</p>", content_type="text/html")),
    )
    path = fetch_to_source("https://arxiv.org/abs/2401.12345", tmp_path / "ing")
    body = path.read_text(encoding="utf-8")
    assert "arxiv_id: 2401.12345" in body


# ---------------------------------------------------------------------------
# Binary bodies must never be decoded. `.text` over a PDF produces mojibake,
# content_sha256 then hashes the mojibake rather than what the server sent, and
# the result is written under a .md suffix — so unlike a local PDF, which the
# markdown walker at least ignores, this one IS picked up and fed to the
# extractor as prose.
#
# The declared content-type is not enough on its own: a server can omit it, or
# get it wrong. These tests use a real httpx.Response, which carries the raw
# bytes the production code path actually has.
# ---------------------------------------------------------------------------

_PDF_BYTES = b"%PDF-1.4\n\x89\xa0\xfe\x0c binary \xff\xfe\x00\x01\n%%EOF\n"


def _httpx_response(url, body, headers):
    import httpx

    return httpx.Response(
        200, content=body, headers=headers, request=httpx.Request("GET", url)
    )


def _fetch(monkeypatch, response, dest):
    monkeypatch.setattr(
        "tesserae.ingest.fetch._http_get",
        lambda u, timeout=None, follow_redirects=True, headers=None: response,
    )
    monkeypatch.setattr("tesserae.ingest.fetch._html_to_markdown", lambda html: html)
    return fetch_to_source("https://arxiv.org/pdf/2310.11511v1", dest)


def test_fetch_refuses_a_declared_pdf(tmp_path, monkeypatch):
    url = "https://arxiv.org/pdf/2310.11511v1"
    response = _httpx_response(url, _PDF_BYTES, {"content-type": "application/pdf"})

    with pytest.raises(Exception) as exc:
        _fetch(monkeypatch, response, tmp_path)

    assert "application/pdf" in str(exc.value)
    assert not list(tmp_path.glob("*.md"))


def test_fetch_refuses_binary_when_the_server_sends_no_content_type(
    tmp_path, monkeypatch
):
    """Declared-type alone leaves the hole open: omit the header and the same
    mojibake .md gets written, hashed over the decoded string, and compiled."""
    url = "https://arxiv.org/pdf/2310.11511v1"
    response = _httpx_response(
        url, _PDF_BYTES, {"content-length": str(len(_PDF_BYTES))}
    )

    with pytest.raises(Exception):
        _fetch(monkeypatch, response, tmp_path)

    assert not list(tmp_path.glob("*.md"))


def test_fetch_refuses_binary_when_the_content_type_is_wrong(tmp_path, monkeypatch):
    """A server that mislabels a PDF as text/plain must not get a free pass:
    the bytes decide, not the label."""
    url = "https://arxiv.org/pdf/2310.11511v1"
    response = _httpx_response(url, _PDF_BYTES, {"content-type": "text/plain"})

    with pytest.raises(Exception):
        _fetch(monkeypatch, response, tmp_path)

    assert not list(tmp_path.glob("*.md"))


def test_fetch_still_accepts_real_text_without_a_content_type(tmp_path, monkeypatch):
    """Refusing every missing content-type would break plain-text sources that
    are perfectly readable. Sniff the bytes; do not punish a missing label."""
    url = "https://example.com/notes"
    body = "# Retrieval\n\nReinforcement learning from human feedback.\n".encode("utf-8")
    response = _httpx_response(url, body, {"content-length": str(len(body))})

    path = _fetch(monkeypatch, response, tmp_path)

    assert path.exists()
    assert "Reinforcement learning" in path.read_text(encoding="utf-8")


def test_fetch_accepts_utf8_text_with_multibyte_characters(tmp_path, monkeypatch):
    """A binary sniff that keys on "non-ASCII" would reject every non-English
    source. Only real binary signatures and embedded NULs count."""
    url = "https://example.com/ko"
    body = "# 검색 증강\n\n한국어 본문입니다.\n".encode("utf-8")
    response = _httpx_response(url, body, {"content-type": "text/markdown"})

    path = _fetch(monkeypatch, response, tmp_path)

    assert "한국어" in path.read_text(encoding="utf-8")
