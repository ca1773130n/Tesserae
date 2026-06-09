from tesserae.ingest.fetch import is_url


def test_is_url_true_for_http_and_https():
    assert is_url("http://example.com/a")
    assert is_url("https://arxiv.org/abs/2401.12345")


def test_is_url_false_for_paths():
    assert not is_url("notes/a.md")
    assert not is_url("/abs/path/to/file.md")
    assert not is_url("./relative.md")
    assert not is_url("ftp://example.com/x")  # only http(s) is a URL for ingest
