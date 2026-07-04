from tesserae.citation_names import rewrite_citations


def test_known_id_becomes_name():
    body = "We chose SQLite [SessionDecision:db:ab12cd] for storage."
    out = rewrite_citations(body, {"SessionDecision:db:ab12cd": "Use SQLite for the session index"})
    assert out == "We chose SQLite [Use SQLite for the session index] for storage."


def test_unknown_id_left_verbatim():
    body = "See [Type:slug:deadbeef]."
    assert rewrite_citations(body, {}) == "See [Type:slug:deadbeef]."


def test_non_citation_brackets_untouched():
    body = "array[0] and [ok] short token"
    assert rewrite_citations(body, {"array": "x"}) == body  # 'array' not a bracketed citation here


def test_multiple_and_repeated():
    body = "[a:b:c1] then [a:b:c1] and [d:e:f2]"
    out = rewrite_citations(body, {"a:b:c1": "Alpha", "d:e:f2": "Delta"})
    assert out == "[Alpha] then [Alpha] and [Delta]"
