"""`tesserae compile --extractor` wires the LLM extractor into the compile pipeline."""

from __future__ import annotations


def test_compile_extractor_default_is_llm():
    from tesserae.cli import _build_compile_parser

    assert _build_compile_parser().parse_args([]).extractor == "llm"  # LLM by default


def test_compile_parser_accepts_extractor_flags():
    from tesserae.cli import _build_compile_parser

    args = _build_compile_parser().parse_args(
        ["--extractor", "selective-llm", "--llm-include", "docs/**/*.md", "--llm-limit", "5", "--llm-provider", "codex"]
    )
    assert args.extractor == "selective-llm"
    assert args.llm_include == ["docs/**/*.md"] and args.llm_limit == 5 and args.llm_provider == "codex"


def test_build_doc_extractor_selects_backend(monkeypatch):
    import tesserae.llm_json as lj
    from tesserae.cli import _build_compile_parser, _build_doc_extractor
    from tesserae.llm_extractor import LLMResearchExtractor
    from tesserae.research_graph import ResearchGraphExtractor
    from tesserae.selective_extractor import SelectiveClaudeResearchExtractor

    p = _build_compile_parser()
    # deterministic -> structural baseline
    assert isinstance(_build_doc_extractor(p.parse_args(["--extractor", "deterministic"])), ResearchGraphExtractor)

    # llm with NO backend available -> graceful fallback to deterministic
    monkeypatch.setattr(lj, "build_default_json_client", lambda **k: None)
    assert isinstance(_build_doc_extractor(p.parse_args(["--extractor", "llm"])), ResearchGraphExtractor)

    # llm WITH a backend -> LLMResearchExtractor wrapped in the per-doc fallback
    # router (include=["*"] routes every doc to the LLM; a failure falls back).
    monkeypatch.setattr(lj, "build_default_json_client", lambda **k: object())
    plain = _build_doc_extractor(p.parse_args(["--extractor", "llm"]))
    assert isinstance(plain, SelectiveClaudeResearchExtractor)
    assert isinstance(plain.claude, LLMResearchExtractor) and plain.include_patterns == ["*"]
    assert isinstance(_build_doc_extractor(p.parse_args(["--extractor", "selective-llm"])), SelectiveClaudeResearchExtractor)
    # deprecated alias still works
    assert isinstance(_build_doc_extractor(p.parse_args(["--extractor", "claude-cli"])), SelectiveClaudeResearchExtractor)


def test_selective_extract_text_routes_by_absolute_path_and_limit():
    from tesserae.research_graph import ResearchGraph
    from tesserae.selective_extractor import SelectiveClaudeResearchExtractor

    calls = []

    class _Rec:
        def __init__(self, tag):
            self.tag = tag

        def extract_text(self, text, source_path=None, source_kind="SourceDocument"):
            calls.append((self.tag, source_path))
            return ResearchGraph(nodes=[], edges=[])

        def extract_file(self, path, source_kind="SourceDocument"):
            return ResearchGraph(nodes=[], edges=[])

    sel = SelectiveClaudeResearchExtractor(
        deterministic=_Rec("det"), claude=_Rec("claude"),
        include_patterns=["*docs/superpowers*"], claude_limit=1,
    )
    sel.extract_text("a", "/Users/x/Agented/docs/superpowers/spec.md")   # matches -> claude
    sel.extract_text("b", "/Users/x/Agented/README.md")                  # no match -> det
    sel.extract_text("c", "/Users/x/Agented/docs/superpowers/spec2.md")  # limit hit -> det
    assert calls == [
        ("claude", "/Users/x/Agented/docs/superpowers/spec.md"),
        ("det", "/Users/x/Agented/README.md"),
        ("det", "/Users/x/Agented/docs/superpowers/spec2.md"),
    ]


def test_compile_paths_threads_extractor_into_ingest(monkeypatch):
    import tesserae.llm_json as lj
    from tesserae import cli
    from tesserae.llm_extractor import LLMResearchExtractor
    from tesserae.selective_extractor import SelectiveClaudeResearchExtractor

    captured = {}

    class _FakeWiki:
        def config(self):
            return {}

        def ingest(self, inputs, **kwargs):
            captured.update(kwargs)
            return {"processed_files": 1, "skipped_files": 0, "node_count": 0,
                    "edge_count": 0, "graph_path": "/tmp/x.json"}

    monkeypatch.setattr("tesserae.cli.ProjectWiki.load", lambda p: _FakeWiki())
    monkeypatch.setattr(lj, "build_default_json_client", lambda **k: object())  # a backend is available
    args = cli._build_compile_parser().parse_args(["--extractor", "llm", "doc.md"])
    assert cli._handle_compile(args) == 0  # dispatches to the paths-ingest branch
    doc_ex = captured.get("doc_extractor")
    assert isinstance(doc_ex, SelectiveClaudeResearchExtractor)  # per-doc fallback router
    assert isinstance(doc_ex.claude, LLMResearchExtractor)


def test_llm_payload_drops_bad_edges_instead_of_aborting():
    """One hallucinated edge type / dangling endpoint must not void a doc's
    extraction (else a single bad doc aborts a whole multi-doc compile)."""
    from tesserae.llm_extractor import graph_from_llm_payload

    payload = {
        "nodes": [{"name": "A", "type": "Concept"}, {"name": "B", "type": "Concept"}],
        "edges": [
            {"type": "used_by", "source": "A", "target": "B"},   # not in the vocab -> drop
            {"type": "uses", "source": "A", "target": "B"},      # valid -> keep
            {"type": "is_a", "source": "A", "target": "GHOST"},  # dangling endpoint -> drop
        ],
    }
    g = graph_from_llm_payload(payload, source_path="x.md", source_kind="SourceDocument")
    kept = {e.type for e in g.edges}
    assert "uses" in kept and "used_by" not in kept and "is_a" not in kept


def test_selective_extract_text_falls_back_when_claude_fails():
    """A claude timeout/error on one doc must fall back to deterministic, not
    raise (else one slow doc aborts the whole compile)."""
    from tesserae.research_graph import ResearchGraph
    from tesserae.selective_extractor import SelectiveClaudeResearchExtractor

    calls = []

    class _Det:
        def extract_text(self, t, sp=None, sk="SourceDocument"):
            calls.append("det")
            return ResearchGraph(nodes=[], edges=[])

        def extract_file(self, p, source_kind="SourceDocument"):
            return ResearchGraph(nodes=[], edges=[])

    class _Claude:
        def extract_text(self, t, sp=None, sk="SourceDocument"):
            raise TimeoutError("claude slow")

        def extract_file(self, p, source_kind="SourceDocument"):
            return ResearchGraph(nodes=[], edges=[])

    sel = SelectiveClaudeResearchExtractor(
        deterministic=_Det(), claude=_Claude(), include_patterns=["*docs*"], claude_limit=5
    )
    sel.extract_text("x", "/a/docs/spec.md")  # routed to claude -> raises -> deterministic
    assert calls == ["det"]  # fell back, did not propagate


def test_claude_extract_text_retries_transient_bad_generation():
    """A transient invalid generation (nameless node) is retried, not fatal.

    The trigger used to be an out-of-vocab node type; that shape is now a
    counted drop rather than an error, so these retry tests pick a violation
    that is still fatal — a node the builder cannot name.
    """
    from tesserae.llm_extractor import ClaudeCLIResearchExtractor

    calls = []
    bad = '{"nodes":[{"name":"   ","type":"Concept"}],"edges":[]}'   # unnameable
    good = '{"nodes":[{"name":"X","type":"Concept"}],"edges":[]}'

    def runner(prompt, cd, model, timeout):
        calls.append(1)
        return bad if len(calls) == 1 else good

    ex = ClaudeCLIResearchExtractor(model="sonnet", timeout=5)
    ex.config_dirs = [None]
    ex.runner = runner
    g = ex.extract_text("doc text", "x.md", "SourceDocument")
    assert len(calls) == 2          # first bad -> retried once -> good
    assert len(g.nodes) >= 1


def test_claude_extract_text_gives_up_after_retries():
    """A persistently invalid generation raises after the bounded retries."""
    import pytest

    from tesserae.llm_extractor import (
        ClaudeCLIResearchExtractor, GraphJSONValidationError, _VALIDATION_RETRIES,
    )

    calls = []

    def runner(prompt, cd, model, timeout):
        calls.append(1)
        return '{"nodes":[{"name":"   ","type":"Concept"}],"edges":[]}'

    ex = ClaudeCLIResearchExtractor(model="sonnet", timeout=5)
    ex.config_dirs = [None]
    ex.runner = runner
    with pytest.raises(GraphJSONValidationError):
        ex.extract_text("t", "x.md", "SourceDocument")
    assert len(calls) == _VALIDATION_RETRIES + 1   # initial + retries, then gives up


def test_llm_research_extractor_drives_the_client():
    """The provider-agnostic extractor calls the LLMJsonClient (any backend),
    passes a content cache_key, and validates the returned payload into a graph."""
    from tesserae.llm_extractor import LLMResearchExtractor

    seen = {}

    class _FakeClient:
        def complete_json(self, *, system, user, schema_name, cache_key=None, max_retries=2):
            seen["schema"] = schema_name
            seen["cache_key"] = cache_key
            return {"nodes": [{"name": "Gaussian Splatting", "type": "Concept"}], "edges": []}

    ex = LLMResearchExtractor(_FakeClient())
    g = ex.extract_text("Gaussian splatting renders radiance fields.", "x.md", "SourceDocument")
    assert any(n.name == "Gaussian Splatting" for n in g.nodes)
    assert seen["schema"] == "research-graph-v1" and seen["cache_key"]  # content-keyed


def test_llm_extractor_falls_back_per_doc_on_backend_failure(monkeypatch):
    """A backend failure on ONE doc (complete_json -> None: auth/timeout/parse)
    must fall back to deterministic for that doc, NOT abort the whole compile."""
    import tesserae.llm_json as lj
    from tesserae.cli import _build_compile_parser, _build_doc_extractor

    class _FailClient:
        def complete_json(self, **k):
            return None  # auth expiry / timeout / unparseable

    monkeypatch.setattr(lj, "build_default_json_client", lambda **k: _FailClient())
    ex = _build_doc_extractor(_build_compile_parser().parse_args(["--extractor", "llm"]))
    g = ex.extract_text("a document body", "/proj/doc.md", "SourceDocument")  # must NOT raise
    assert g is not None  # fell back to the deterministic baseline


def test_llm_extractor_raises_provider_unavailable_not_validation_error(monkeypatch):
    """A provider that never answered must NOT be reported as a schema violation.

    Conflating them cost three rounds of blaming the model for what was a
    provider capacity window. The transport layer already retried with backoff,
    so this verdict is final — no stacked re-ask.
    """
    import pytest

    import tesserae.llm_json as lj
    from tesserae.llm_extractor import (
        GraphJSONValidationError, LLMResearchExtractor, ProviderUnavailableError,
    )

    calls = []

    class _UnavailableClient:
        def complete_json(self, **k):
            calls.append(1)
            lj._note_failure("unavailable")
            return None

    with pytest.raises(ProviderUnavailableError) as err:
        LLMResearchExtractor(_UnavailableClient()).extract_text("body", "/proj/doc.md")
    assert not isinstance(err.value, GraphJSONValidationError)  # sibling, not subclass
    assert "transport/capacity" in str(err.value)
    assert len(calls) == 1  # final verdict: no _VALIDATION_RETRIES on top of the transport ones


def test_llm_extractor_retries_a_bad_generation_then_gives_up(monkeypatch):
    """A real bad generation keeps the old contract: re-ask, then raise the
    schema error (parity with ClaudeCLIResearchExtractor)."""
    import pytest

    import tesserae.llm_json as lj
    from tesserae.llm_extractor import (
        _VALIDATION_RETRIES, GraphJSONValidationError, LLMResearchExtractor,
        ProviderUnavailableError,
    )

    calls = []

    class _UnparseableClient:
        def complete_json(self, **k):
            calls.append(1)
            lj._note_failure("unparseable")
            return None

    with pytest.raises(GraphJSONValidationError) as err:
        LLMResearchExtractor(_UnparseableClient()).extract_text("body", "/proj/doc.md")
    assert not isinstance(err.value, ProviderUnavailableError)
    assert len(calls) == _VALIDATION_RETRIES + 1  # initial + retries


def test_llm_extractor_retries_a_schema_violation_then_gives_up():
    """The other bad-generation shape: parseable JSON that violates the schema.

    Counts calls into a CACHELESS fake, so it pins the loop shape only. The
    "does the retry actually reach the provider" question needs the real
    client's on-disk cache — see
    ``test_llm_extractor_retry_re_asks_and_leaves_no_poisoned_cache``.
    """
    import pytest

    from tesserae.llm_extractor import (
        _VALIDATION_RETRIES, GraphJSONValidationError, LLMResearchExtractor,
    )

    calls = []

    class _BadSchemaClient:
        def complete_json(self, **k):
            calls.append(1)
            return {"nodes": [{"name": "   ", "type": "Concept"}], "edges": []}

    with pytest.raises(GraphJSONValidationError):
        LLMResearchExtractor(_BadSchemaClient()).extract_text("body", "/proj/doc.md")
    assert len(calls) == _VALIDATION_RETRIES + 1


def test_llm_extractor_retry_re_asks_and_leaves_no_poisoned_cache(monkeypatch, tmp_path):
    """A rejected generation must not be served back from the on-disk cache.

    Run against the REAL ``CodexCLIJsonClient`` cache, because that is where
    the defect lived: ``_cli_cache_put`` stores every PARSEABLE answer, and an
    out-of-vocab node type parses fine. So the retry loop re-read its own bad
    answer — ONE subprocess spawn while stderr printed "retrying (1/2)" and
    "(2/2)", two false statements per failed doc — and since
    ``~/.tesserae/llm_cache`` has no eviction, ``--changed-only
    --retry-fallbacks`` then spent ZERO llm calls on that doc and failed
    identically forever. A fake client with no cache passes this vacuously.

    The rejected answer here is a nameless node: an out-of-vocab node TYPE no
    longer rejects anything, it is dropped and counted.
    """
    from pathlib import Path

    import pytest

    import tesserae.llm_json as lj
    from tesserae.llm_extractor import (
        _VALIDATION_RETRIES, GraphJSONValidationError, LLMResearchExtractor,
    )

    cache_dir = tmp_path / "cache"
    monkeypatch.setattr(lj, "_CLI_CACHE_DIR", cache_dir)
    monkeypatch.delenv("TESSERAE_LLM_CACHE", raising=False)

    answer = {"raw": '{"nodes": [{"name": "   ", "type": "Concept"}], "edges": []}'}
    spawns: list = []

    class _Proc:
        def __init__(self, rc=0, stdout="", stderr=""):
            self.returncode, self.stdout, self.stderr = rc, stdout, stderr

    def fake_run(cmd, *, prompt, env, timeout):
        spawns.append(1)
        Path(cmd[cmd.index("--output-last-message") + 1]).write_text(
            answer["raw"], encoding="utf-8"
        )
        return _Proc()

    monkeypatch.setattr(lj, "_run_cli", fake_run)
    client = lj.CodexCLIJsonClient(codex_homes=[str(tmp_path / "home")])

    with pytest.raises(GraphJSONValidationError):
        LLMResearchExtractor(client).extract_text("body", "/proj/doc.md")
    # (i) every "retrying (n/2)" line corresponds to a real provider call.
    assert len(spawns) == _VALIDATION_RETRIES + 1
    # (ii) nothing rejected survived on disk to re-fail on the next run.
    assert list(cache_dir.rglob("*.json")) == []

    # ...so `--retry-fallbacks` genuinely re-asks, and the doc is recoverable.
    answer["raw"] = '{"nodes": [{"name": "X", "type": "Concept"}], "edges": []}'
    graph = LLMResearchExtractor(client).extract_text("body", "/proj/doc.md")
    assert any(node.name == "X" for node in graph.nodes)
    assert len(spawns) == _VALIDATION_RETRIES + 2  # a real call, not a cache hit


def test_llm_extractor_reports_a_timeout_as_a_timeout(monkeypatch, tmp_path):
    """A doc too big to extract inside TESSERAE_EXTRACT_TIMEOUT is NOT a
    capacity outage, and `codex login` is not the remedy.

    Reporting it as ``ProviderUnavailableError`` sent the operator to wait out
    a window that does not exist and re-run ``--retry-fallbacks`` forever.
    """
    import subprocess

    import pytest

    import tesserae.llm_json as lj
    from tesserae.llm_extractor import (
        ExtractionTimeoutError, LLMResearchExtractor, ProviderUnavailableError,
    )

    def fake_run(cmd, *, prompt, env, timeout):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)

    monkeypatch.setattr(lj, "_run_cli", fake_run)
    client = lj.CodexCLIJsonClient(codex_homes=[str(tmp_path / "home")], timeout=1800)

    with pytest.raises(ExtractionTimeoutError) as err:
        LLMResearchExtractor(client).extract_text("body", "/proj/big.md")
    assert not isinstance(err.value, ProviderUnavailableError)  # sibling, not subclass
    assert "TESSERAE_EXTRACT_TIMEOUT" in str(err.value)
    assert "codex login" not in str(err.value)


def test_selective_fallback_line_names_the_cause(capsys):
    """The operator-facing per-doc line must say WHICH failure it was, and a
    provider outage must still be marked a fallback so --retry-fallbacks
    can recover it."""
    from tesserae.llm_extractor import ProviderUnavailableError
    from tesserae.research_graph import ResearchGraph
    from tesserae.selective_extractor import SelectiveClaudeResearchExtractor

    class _Down:
        def extract_text(self, *a, **k):
            raise ProviderUnavailableError("LLM backend unavailable for /proj/doc.md — no response")

    class _Det:
        def extract_text(self, *a, **k):
            return ResearchGraph(nodes=[], edges=[])

    sel = SelectiveClaudeResearchExtractor(
        deterministic=_Det(), claude=_Down(), include_patterns=["*.md"]
    )
    sel.extract_text("x", "/proj/doc.md")
    err = capsys.readouterr().err
    assert "ProviderUnavailableError" in err and "no response" in err
    assert "used deterministic" in err
    assert sel.last_was_fallback is True  # a provider outage IS recoverable work


def test_doc_extractor_honors_project_provider_and_default_timeout(monkeypatch):
    """_build_doc_extractor threads the PROJECT config's llm_provider and arms the
    default wedge guard (extraction is bounded unless explicitly disabled)."""
    import tesserae.llm_json as lj
    from tesserae.cli import (
        _DEFAULT_EXTRACT_TIMEOUT,
        _build_compile_parser,
        _build_doc_extractor,
    )

    monkeypatch.delenv("TESSERAE_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("TESSERAE_EXTRACT_TIMEOUT", raising=False)
    seen = {}

    def _fake(**k):
        seen.update(k)
        return object()

    monkeypatch.setattr(lj, "build_default_json_client", _fake)
    _build_doc_extractor(_build_compile_parser().parse_args(["--extractor", "llm"]),
                         cfg={"llm_provider": "codex"})
    assert seen["provider"] == "codex"   # project config wins, not the global default
    assert seen["timeout"] == _DEFAULT_EXTRACT_TIMEOUT  # guard armed by default


def test_extract_timeout_env_parsing(monkeypatch):
    """TESSERAE_EXTRACT_TIMEOUT bounds each extraction call so a wedged codex child
    is killed and that doc falls back to deterministic — instead of hanging the compile.
    Armed by DEFAULT; only an explicit '0' disables it. Garbage must not silently
    disarm the guard — it warns and keeps the default."""
    import tesserae.llm_json as lj
    from tesserae.cli import (
        _DEFAULT_EXTRACT_TIMEOUT,
        _build_compile_parser,
        _build_doc_extractor,
        _extract_timeout,
    )

    # helper parses the env directly
    monkeypatch.delenv("TESSERAE_EXTRACT_TIMEOUT", raising=False)
    assert _extract_timeout() == _DEFAULT_EXTRACT_TIMEOUT
    monkeypatch.setenv("TESSERAE_EXTRACT_TIMEOUT", "600")
    assert _extract_timeout() == 600
    # explicit opt-out — the ONLY way to get an unbounded run
    for off in ("0", "0.0", "-0"):
        monkeypatch.setenv("TESSERAE_EXTRACT_TIMEOUT", off)
        assert _extract_timeout() is None, off
    # unset-equivalent and unusable values keep the guard armed
    for bad in ("-5", "", "   ", "abc", "10m", "600s", "inf", "nan"):
        monkeypatch.setenv("TESSERAE_EXTRACT_TIMEOUT", bad)
        assert _extract_timeout() == _DEFAULT_EXTRACT_TIMEOUT, bad

    # Whole seconds >= 1: the CLI clients coerce with int(), so a fractional value
    # must NOT round down to 0 (an instant timeout that would degrade every doc to
    # deterministic while the anthropic client kept the float).
    for frac, expected in (("0.5", 1), ("0.01", 1), ("600.2", 601), ("1.0", 1)):
        monkeypatch.setenv("TESSERAE_EXTRACT_TIMEOUT", frac)
        got = _extract_timeout()
        assert got == expected, f"{frac} -> {got}"
        assert isinstance(got, int) and int(got) >= 1

    # and it is threaded into the client the compile builds
    seen = {}
    monkeypatch.setattr(lj, "build_default_json_client", lambda **k: seen.update(k) or object())
    monkeypatch.setenv("TESSERAE_EXTRACT_TIMEOUT", "600")
    _build_doc_extractor(_build_compile_parser().parse_args(["--extractor", "llm"]))
    assert seen["timeout"] == 600.0


def test_every_transport_failure_shape_gets_the_same_number_of_rolls(monkeypatch, tmp_path):
    """The extractor skips its own re-ask because the transport layer retried.

    That was structurally false for one shape: `codex exec` exiting 0 with an
    EMPTY last message returned straight out of the rotation, so it never
    entered the retry loop — and then the extractor refused to retry it on the
    grounds that it already had. A capacity blip presenting as a clean exit with
    an empty body was condemned to the deterministic baseline on ONE roll while
    a non-zero exit got three. Measured in provider spawns, which is what the
    operator pays.
    """
    import pytest

    import tesserae.llm_json as lj
    from tesserae.llm_extractor import LLMResearchExtractor, ProviderUnavailableError

    def _spawns_for(returncode: int, last_message: str) -> int:
        spawns: list = []

        def fake_run(cmd, *, prompt, env, timeout):
            spawns.append(1)
            if returncode == 0:
                Path(cmd[cmd.index("--output-last-message") + 1]).write_text(
                    last_message, encoding="utf-8"
                )
            return types.SimpleNamespace(returncode=returncode, stdout="", stderr=last_message)

        monkeypatch.setattr(lj, "_run_cli", fake_run)
        monkeypatch.setattr(lj.time, "sleep", lambda _s: None)
        client = lj.CodexCLIJsonClient(codex_homes=[str(tmp_path / "home")], timeout=1800)
        with pytest.raises(ProviderUnavailableError):
            LLMResearchExtractor(client).extract_text("body", "/proj/doc.md")
        return len(spawns)

    import types
    from pathlib import Path

    clean_exit_empty_body = _spawns_for(0, "")
    non_zero_exit = _spawns_for(1, "stream error: high demand")
    assert clean_exit_empty_body == non_zero_exit == lj._TRANSPORT_RETRIES + 1


def test_llm_extractor_reports_an_auth_failure_as_auth_not_capacity(monkeypatch):
    """An expired session is not a capacity window, and it is reported per doc.

    ``ProviderUnavailableError(... "(transport/capacity)")`` told the operator to
    wait and re-run on every one of 137 lines; the single line naming
    `claude /login` is gated on a once-per-process flag and had scrolled away.
    """
    import pytest

    import tesserae.llm_json as lj
    from tesserae.llm_extractor import (
        ExtractionTimeoutError, GraphJSONValidationError, LLMResearchExtractor,
        ProviderAuthError, ProviderUnavailableError,
    )

    calls: list = []

    class _LoggedOutClient:
        def complete_json(self, **k):
            calls.append(1)
            lj._note_failure("auth")
            return None

    with pytest.raises(ProviderAuthError) as err:
        LLMResearchExtractor(_LoggedOutClient()).extract_text("body", "/proj/doc.md")
    message = str(err.value)
    assert "login" in message  # the real remedy, on THIS line
    assert "transport/capacity" not in message
    assert len(calls) == 1  # an auth verdict is final: no re-ask, no backoff
    # A fourth sibling, not a subclass: the per-doc line prints the class name,
    # and an `except` ladder that collapses these re-creates the whole defect.
    for other in (ProviderUnavailableError, ExtractionTimeoutError, GraphJSONValidationError):
        assert not issubclass(ProviderAuthError, other)
        assert not issubclass(other, ProviderAuthError)


def test_a_cache_drop_that_raises_does_not_replace_the_validation_error():
    """``forget_cached_answer`` is duck-typed, so it can raise anything.

    ``CompositeCLIClient`` fans the drop out to every sub-client unguarded, so
    ONE sub-client whose drop fails used to abort the retry loop mid-flight with
    an unrelated exception — the operator sees OSError on a document whose real
    problem is a schema violation, and loses two of the three re-asks.
    """
    import pytest

    import tesserae.llm_json as lj
    from tesserae.llm_extractor import (
        _VALIDATION_RETRIES, GraphJSONValidationError, LLMResearchExtractor,
    )

    calls: list = []

    class _BadSchema:
        def complete_json(self, **k):
            calls.append(1)
            return {"nodes": [{"name": "   ", "type": "Concept"}], "edges": []}

        def forget_cached_answer(self, cache_key, *, schema_name, system, user):
            pass

    class _DropRaises:
        def complete_json(self, **k):
            return None

        def forget_cached_answer(self, cache_key, *, schema_name, system, user):
            raise OSError("read-only filesystem")

    client = lj.CompositeCLIClient([_BadSchema(), _DropRaises()])
    with pytest.raises(GraphJSONValidationError):
        LLMResearchExtractor(client).extract_text("body", "/proj/doc.md")
    assert len(calls) == _VALIDATION_RETRIES + 1  # the re-asks still happened


def test_a_killed_compile_replays_its_finished_documents_for_free(tmp_path, monkeypatch):
    """The resumability that already exists but was documented and tested nowhere.

    A compile killed at document 900 of 2,524 advances no ``graphed`` marker, so
    the next ``--changed-only`` correctly refuses its no-op and re-walks the
    WHOLE corpus. Read as "3h35m of work thrown away" that looks catastrophic —
    and it is what the operator on the 2026-08-15 run believed. It is wrong.

    The re-walk is not a re-purchase. ``LLMResearchExtractor`` addresses the
    response cache under ``~/.tesserae/llm_cache`` by a digest over
    (guidance, source_kind, source_path, text) — and since #171 the client folds
    the assembled prompt in too — so every document the killed run FINISHED is
    replayed from disk and costs no model call at all. The re-walk pays only for
    the documents that had not finished.

    This test is the guard on that claim: run the same corpus twice through
    ``BatchIngestRunner`` with a counting client, kill the first run partway,
    and assert the second run's real calls cover only the unfinished remainder.
    If the cache key ever stops being content-addressed, the second count jumps
    to the full corpus and this fails — which is precisely the regression that
    would turn an interrupted compile into genuinely lost work.

    The one thing that does NOT survive is clearing the cache. That is asserted
    at the end, because it is the difference between a cheap re-run and a full
    re-purchase, and an operator deleting a cache directory to "clean up" has no
    other warning.
    """
    import json
    import shutil

    from tesserae import llm_json as lj
    from tesserae.batch import BatchIngestRunner
    from tesserae.llm_extractor import LLMResearchExtractor

    cache_dir = tmp_path / "llm-cache"
    monkeypatch.setattr(lj, "_CLI_CACHE_DIR", cache_dir)

    class _CountingClient:
        """Counts REAL calls. Never touches a network or a subprocess."""

        def __init__(self) -> None:
            self.calls = 0

        def _cache_coords(self, schema_name):
            return ("test-model", schema_name)

        def complete_json(self, *, system, user, schema_name, cache_key=None, max_retries=2):
            prompt = lj._stitch_json_prompt(system=system, user=user, schema_name=schema_name)
            model, extra = self._cache_coords(schema_name)
            cached = lj._cli_cache_get(cache_key, model=model, prompt=prompt, extra=extra)
            if cached is not None:
                return json.loads(cached)
            self.calls += 1
            raw = json.dumps(
                {"nodes": [{"id": f"Concept:c{self.calls}", "type": "Concept",
                            "name": f"c{self.calls}"}], "edges": []}
            )
            lj._cli_cache_put(cache_key, raw, model=model, prompt=prompt, extra=extra)
            return json.loads(raw)

    docs = tmp_path / "docs"
    docs.mkdir()
    paths = []
    for i in range(6):
        p = docs / f"doc-{i:02d}.md"
        p.write_text(f"# Doc {i}\n\nDistinct body {i}.\n", encoding="utf-8")
        paths.append(p)

    client = _CountingClient()
    extractor = LLMResearchExtractor(client)

    # The interrupted run: four of six documents finished before the kill.
    BatchIngestRunner(extractor, tmp_path / "killed.json").run(paths[:4])
    assert client.calls == 4

    # The repair run. It re-walks EVERY document — a fresh manifest, exactly
    # like the full re-extract the missing ``graphed`` marker forces — but the
    # four already-extracted ones are served from disk.
    BatchIngestRunner(extractor, tmp_path / "repair.json").run(paths)
    assert client.calls == 6, (
        "the re-walk re-purchased finished documents; the response cache is no "
        "longer content-addressed and an interrupted compile now loses work"
    )

    # A third run over an untouched corpus is entirely free.
    before = client.calls
    BatchIngestRunner(extractor, tmp_path / "third.json").run(paths)
    assert client.calls == before

    # ...unless the cache is gone. Then the same re-walk pays full price, which
    # is the caveat the docs have to carry.
    shutil.rmtree(cache_dir)
    BatchIngestRunner(extractor, tmp_path / "cleared.json").run(paths)
    assert client.calls == before + 6


# ------------------------------------------- chunked extraction (density)


def _stub_extract(seen):
    from tesserae.llm_extractor import graph_from_llm_payload

    def go(text, source_path=None, source_kind="SourceDocument", guidance=""):
        seen.append(len(text))
        i = len(seen)
        return graph_from_llm_payload(
            {"nodes": [{"name": "Doc", "type": "SourceDocument"},
                       {"name": f"Algo{i}", "type": "Algorithm"}],
             "edges": []},
            source_path=source_path, source_kind=source_kind,
        )
    return go


def _long_text():
    return ("A sentence stating a relation. " * 30 + "\n\n") * 20


def test_a_small_document_still_takes_the_single_call_path():
    """The historical path must be byte-identical for anything that fits, or
    every cached extraction in every project is invalidated."""
    from tesserae.llm_extractor import extract_in_chunks

    seen = []
    extract_in_chunks("short. " * 20, "/tmp/d.md", "SourceDocument", "", _stub_extract(seen))
    assert len(seen) == 1


def test_a_large_document_is_split_and_every_chunk_contributes():
    """One call over a 38KB paper returned 20.9 factual relations; the same
    prompt over 4,000-char chunks returned 124.8. The compile embedded the
    whole document and never split it."""
    from tesserae.llm_extractor import extract_in_chunks

    seen = []
    g = extract_in_chunks(_long_text(), "/tmp/d.md", "SourceDocument", "", _stub_extract(seen))
    assert len(seen) > 1
    algos = [n for n in g.nodes if n.type.value == "Algorithm"]
    assert len(algos) == len(seen), "a chunk's findings must survive the merge"


def test_the_document_anchor_is_merged_not_duplicated():
    """Every chunk emits its own anchor for the same file; they must collapse
    to one, or the document appears N times in the graph."""
    from tesserae.llm_extractor import extract_in_chunks

    g = extract_in_chunks(_long_text(), "/tmp/d.md", "SourceDocument", "", _stub_extract([]))
    assert len([n for n in g.nodes if n.type.value == "SourceDocument"]) == 1


def test_one_bad_chunk_does_not_cost_the_whole_document():
    """Losing a chunk costs its relations; raising costs all of them."""
    from tesserae.llm_extractor import GraphJSONValidationError, extract_in_chunks, graph_from_llm_payload

    calls = [0]

    def flaky(text, source_path=None, source_kind="SourceDocument", guidance=""):
        calls[0] += 1
        if calls[0] == 2:
            raise GraphJSONValidationError("truncated JSON")
        return graph_from_llm_payload(
            {"nodes": [{"name": f"E{calls[0]}", "type": "Algorithm"}], "edges": []},
            source_path=source_path, source_kind=source_kind)

    g = extract_in_chunks(_long_text(), "/tmp/d.md", "SourceDocument", "", flaky)
    assert [n for n in g.nodes if n.type.value == "Algorithm"]


def test_every_chunk_failing_still_raises():
    """Degrading to an empty graph would report a document as extracted when
    nothing was — the silent-success shape this project keeps hitting."""
    import pytest as _pytest
    from tesserae.llm_extractor import GraphJSONValidationError, extract_in_chunks

    def always(text, source_path=None, source_kind="SourceDocument", guidance=""):
        raise GraphJSONValidationError("bad")

    with _pytest.raises(GraphJSONValidationError):
        extract_in_chunks(_long_text(), "/tmp/d.md", "SourceDocument", "", always)


def test_splitting_is_deterministic_and_bounded():
    """Same text in, same pieces out — the compile's byte-idempotence depends
    on it, and no piece may exceed the limit it was split to."""
    from tesserae.llm_extractor import EXTRACT_CHUNK_CHARS, split_for_extraction

    text = _long_text()
    first = split_for_extraction(text)
    assert first == split_for_extraction(text)
    assert max(len(p) for p in first) <= EXTRACT_CHUNK_CHARS
    assert split_for_extraction(text, -1) == [text], "0/negative disables splitting"
# ------------------------------------ spans must be reachable from their doc


def _payload(nodes, edges=()):
    return {"nodes": list(nodes), "edges": list(edges)}


def test_a_span_the_model_did_not_link_is_still_reachable_from_its_document():
    """`source_path` is stamped on every node; edges come only from what the
    model emitted. So reachability was the model's discretion, and it mostly
    declined: 28.2% of spans on one compiled corpus, 10.6% on another. A span
    nothing points at is evidence the packer cannot find."""
    from tesserae.llm_extractor import graph_from_llm_payload

    g = graph_from_llm_payload(
        _payload([
            {"name": "Doc", "type": "SourceDocument"},
            {"name": "s1", "type": "EvidenceSpan", "description": "a measured claim"},
            {"name": "s2", "type": "EvidenceSpan", "description": "another one"},
        ]),
        source_kind="SourceDocument", source_path="/tmp/doc.md",
    )
    spans = {n.id for n in g.nodes if n.type.value == "EvidenceSpan"}
    reached = {e.target for e in g.edges if e.type == "contains"}
    assert spans and spans <= reached


def test_a_span_the_model_attributed_elsewhere_is_not_adopted():
    """Inventing containment for a span that names another file would be
    fabricated provenance — the one thing this project must never do."""
    from tesserae.llm_extractor import graph_from_llm_payload

    g = graph_from_llm_payload(
        _payload([
            {"name": "Doc", "type": "SourceDocument"},
            {"name": "s", "type": "EvidenceSpan", "description": "x",
             "source_path": "/tmp/somewhere-else.md"},
        ]),
        source_kind="SourceDocument", source_path="/tmp/doc.md",
    )
    assert not [e for e in g.edges if e.type == "contains"]


def test_an_edge_the_model_did_emit_keeps_its_own_evidence():
    """Re-adding would be a no-op on the builder's (source, type, target) key,
    but it would also discard the model's evidence string. Leave it alone."""
    from tesserae.llm_extractor import graph_from_llm_payload

    g = graph_from_llm_payload(
        _payload(
            [{"name": "Doc", "type": "SourceDocument"},
             {"name": "s", "type": "EvidenceSpan", "description": "x"}],
            [{"source": "Doc", "type": "contains", "target": "s",
              "evidence": "the model's own words"}],
        ),
        source_kind="SourceDocument", source_path="/tmp/doc.md",
    )
    edges = [e for e in g.edges if e.type == "contains"]
    assert len(edges) == 1 and edges[0].evidence == "the model's own words"


def test_non_span_nodes_are_not_swept_into_the_document():
    """Scoped to EvidenceSpan on purpose. Attaching every extracted node would
    add an edge per node and change what `contains` means."""
    from tesserae.llm_extractor import graph_from_llm_payload

    g = graph_from_llm_payload(
        _payload([{"name": "Doc", "type": "SourceDocument"},
                  {"name": "c", "type": "Claim", "description": "z"}]),
        source_kind="SourceDocument", source_path="/tmp/doc.md",
    )
    assert not [e for e in g.edges if e.type == "contains"]
