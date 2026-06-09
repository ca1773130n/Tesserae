from tesserae.ingest.fetch import is_url
from tesserae.ingest.fetch import _slugify, _render_frontmatter


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
