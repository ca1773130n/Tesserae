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


def test_query_answer_rewrites_citations():
    """answer() must expose the shared rewrite so [node_id] renders as the hit title."""
    from tesserae import query as q

    hits = [
        q.QueryHit(
            title="Use SQLite",
            kind="decision",
            href="",
            score=1.0,
            excerpt="",
            page_path=None,
            node_id="Dec:db:ab12",
        )
    ]
    body = "We decided [Dec:db:ab12]."
    id_to_name = {h.node_id: h.title for h in hits if h.node_id}
    assert q.rewrite_citations(body, id_to_name) == "We decided [Use SQLite]."
