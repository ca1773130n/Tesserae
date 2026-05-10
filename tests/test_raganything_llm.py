import asyncio


def test_make_codex_llm_func_routes_to_run_codex_cli(monkeypatch):
    import llm_wiki.raganything_llm as mod
    captured = {}

    async def fake_run_codex_cli(prompt, model, timeout):
        captured["prompt"] = prompt
        captured["model"] = model
        captured["timeout"] = timeout
        return "codex-answer"

    monkeypatch.setattr("llm_wiki.cognee_codex.run_codex_cli", fake_run_codex_cli)

    func = mod.make_codex_llm_func(model="gpt-5.4", timeout=60)
    answer = asyncio.run(func("What is X?", system_prompt="be concise."))
    assert answer == "codex-answer"
    assert "be concise." in captured["prompt"]
    assert "What is X?" in captured["prompt"]
    assert captured["model"] == "gpt-5.4"
    assert captured["timeout"] == 60


def test_make_claude_llm_func_sets_config_dir(monkeypatch, tmp_path):
    import llm_wiki.raganything_llm as mod
    captured = {}

    def fake_run_claude_cli(prompt, config_dir, model, timeout):
        captured["prompt"] = prompt
        captured["config_dir"] = config_dir
        captured["model"] = model
        captured["timeout"] = timeout
        return "claude-answer"

    monkeypatch.setattr("llm_wiki.llm_extractor.run_claude_cli", fake_run_claude_cli)

    custom_dir = tmp_path / "claude-personal2"
    custom_dir.mkdir()
    func = mod.make_claude_llm_func(config_dir=str(custom_dir), model="claude-opus-4-7", timeout=120)
    answer = asyncio.run(func("Hello?"))
    assert answer == "claude-answer"
    assert captured["config_dir"] == str(custom_dir)
    assert captured["model"] == "claude-opus-4-7"
    assert captured["timeout"] == 120


def test_make_claude_llm_func_falls_back_to_env_then_home(monkeypatch):
    import llm_wiki.raganything_llm as mod

    captured = {}

    def fake_run_claude_cli(prompt, config_dir, model, timeout):
        captured["config_dir"] = config_dir
        return ""

    monkeypatch.setattr("llm_wiki.llm_extractor.run_claude_cli", fake_run_claude_cli)

    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    func = mod.make_claude_llm_func()  # no config_dir, no env
    asyncio.run(func("x"))
    assert captured["config_dir"].endswith(".claude") or "/.claude" in captured["config_dir"]

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/tmp/claude-env")
    func2 = mod.make_claude_llm_func()
    asyncio.run(func2("x"))
    assert captured["config_dir"] == "/tmp/claude-env"


def test_deterministic_embedding_is_deterministic_and_right_dim():
    from llm_wiki.raganything_llm import _deterministic_embedding

    a = _deterministic_embedding("hello", 768)
    b = _deterministic_embedding("hello", 768)
    c = _deterministic_embedding("world", 768)
    assert len(a) == 768
    assert a == b
    assert a != c


def test_build_runtime_funcs_default_uses_codex_and_deterministic(monkeypatch):
    from llm_wiki.raganything_llm import build_runtime_funcs

    async def fake_codex(prompt, model, timeout):
        return f"codex({prompt[:20]})"

    monkeypatch.setattr("llm_wiki.cognee_codex.run_codex_cli", fake_codex)

    funcs = build_runtime_funcs({})  # empty config -> all defaults
    assert "llm_model_func" in funcs
    assert "embedding_func" in funcs
    assert funcs["vision_model_func"] is None
    # llm func wired
    answer = asyncio.run(funcs["llm_model_func"]("hi"))
    assert answer.startswith("codex(")


def test_build_runtime_funcs_uses_claude_with_custom_config_dir(monkeypatch, tmp_path):
    from llm_wiki.raganything_llm import build_runtime_funcs

    custom = tmp_path / "claude-personal-3"
    custom.mkdir()
    captured = {}

    def fake_claude(prompt, config_dir, model, timeout):
        captured["config_dir"] = config_dir
        return "ok"

    monkeypatch.setattr("llm_wiki.llm_extractor.run_claude_cli", fake_claude)
    funcs = build_runtime_funcs({
        "llm": {"provider": "claude", "claude_config_dir": str(custom), "model": "opus", "timeout": 30}
    })
    asyncio.run(funcs["llm_model_func"]("hi"))
    assert captured["config_dir"] == str(custom)


def test_make_llm_func_rejects_unknown_provider():
    import pytest

    from llm_wiki.raganything_llm import make_llm_func

    with pytest.raises(ValueError, match="Unsupported raganything llm provider"):
        make_llm_func(provider="openai")


def test_make_embedding_func_rejects_unknown_provider():
    import pytest

    from llm_wiki.raganything_llm import make_embedding_func

    with pytest.raises(ValueError, match="Unsupported raganything embedding provider"):
        make_embedding_func(provider="bogus", dim=128)


def test_deterministic_embedding_func_returns_correct_shape():
    from llm_wiki.raganything_llm import make_deterministic_embedding_func

    func_or_obj = make_deterministic_embedding_func(dim=256)
    # Could be either an EmbeddingFunc wrapper or a plain async callable.
    if hasattr(func_or_obj, "func"):
        callable_ = func_or_obj.func
        assert func_or_obj.embedding_dim == 256
    else:
        callable_ = func_or_obj
    vecs = asyncio.run(callable_(["alpha", "beta", "gamma"]))
    assert len(vecs) == 3
    assert all(len(v) == 256 for v in vecs)
