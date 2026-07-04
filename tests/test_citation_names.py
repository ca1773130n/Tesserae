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


def test_synthesis_body_rewrite():
    from tesserae.citation_names import rewrite_citations

    body = "Finding [SessionInsight:x:9f] supports it."
    assert (
        rewrite_citations(body, {"SessionInsight:x:9f": "Cache warm-up halves latency"})
        == "Finding [Cache warm-up halves latency] supports it."
    )


def test_synthesizer_rewrites_citations_to_input_names():
    """synthesize() returns a body whose [node_id] citations are input names."""
    from tesserae import llm_synthesis as ls

    class _Block:
        type = "text"

        def __init__(self, text: str) -> None:
            self.text = text

    class _Resp:
        model = "claude-sonnet-4-6"

        def __init__(self, text: str) -> None:
            self.content = [_Block(text)]

    class _Messages:
        def __init__(self, text: str) -> None:
            self._text = text

        def create(self, **kwargs):
            return _Resp(self._text)

    class _FakeClient:
        def __init__(self, text: str) -> None:
            self.messages = _Messages(text)

    cited = (
        "Two papers landed this week, both improving reconstruction quality "
        "[node-a] and the shared family [node-b]. Volumetric rendering "
        "refinements dominate the thread [node-a]."
    )
    ls.set_client_factory(lambda **kw: _FakeClient(cited))
    ls.reset_failure_log_for_tests()
    try:
        req = ls.LlmSynthesisRequest(
            kind="pulse",
            title="Pulse",
            inputs=(
                {"id": "node-a", "name": "Paper A", "type": "Paper"},
                {"id": "node-b", "name": "Splatting Family", "type": "ApproachFamily"},
            ),
            context={"summary": "snapshot"},
        )
        out = ls.LlmSynthesizer(model="claude-sonnet-4-6").synthesize(req)
    finally:
        ls.set_client_factory(None)
        ls.reset_failure_log_for_tests()

    assert out is not None
    # Body citations rendered as the input node names, not the raw ids.
    assert "[Paper A]" in out.body
    assert "[Splatting Family]" in out.body
    assert "[node-a]" not in out.body
    assert "[node-b]" not in out.body
    # citations list still carries the raw ids for internal tracking.
    assert "node-a" in out.citations
    assert "node-b" in out.citations
