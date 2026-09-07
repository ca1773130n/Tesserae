import asyncio


def test_make_codex_llm_func_routes_to_run_codex_cli(monkeypatch):
    import tesserae.raganything_llm as mod
    captured = {}

    async def fake_run_codex_cli(prompt, model, timeout):
        captured["prompt"] = prompt
        captured["model"] = model
        captured["timeout"] = timeout
        return "codex-answer"

    # run_codex_cli was inlined into raganything_llm when its former home
    # was removed; the adapter resolves it via module-global lookup.
    monkeypatch.setattr("tesserae.raganything_llm.run_codex_cli", fake_run_codex_cli)

    func = mod.make_codex_llm_func(model="gpt-5.6-luna", timeout=60)
    answer = asyncio.run(func("What is X?", system_prompt="be concise."))
    assert answer == "codex-answer"
    assert "be concise." in captured["prompt"]
    assert "What is X?" in captured["prompt"]
    assert captured["model"] == "gpt-5.6-luna"
    assert captured["timeout"] == 60


def _isolate_llm_settings(monkeypatch, tmp_path):
    """make_claude_llm_func now consults the resolved Tesserae settings, so
    this box's ~/.tesserae/config.json and env must not leak into the test."""
    monkeypatch.setattr("tesserae.llm_json.GLOBAL_CONFIG_PATH", tmp_path / "no-global.json")
    for var in ("TESSERAE_CLAUDE_CONFIG_DIRS", "TESSERAE_LLM_BASE_URL",
                "TESSERAE_LLM_AUTH_TOKEN", "TESSERAE_LLM_API_KEY", "TESSERAE_LLM_API_STYLE",
                "ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(var, raising=False)


def test_make_claude_llm_func_sets_config_dir(monkeypatch, tmp_path):
    import tesserae.raganything_llm as mod
    captured = {}
    _isolate_llm_settings(monkeypatch, tmp_path)

    def fake_run_claude_cli(prompt, config_dir, model, timeout):
        captured["prompt"] = prompt
        captured["config_dir"] = config_dir
        captured["model"] = model
        captured["timeout"] = timeout
        return "claude-answer"

    monkeypatch.setattr("tesserae.llm_extractor.run_claude_cli", fake_run_claude_cli)

    custom_dir = tmp_path / "claude-personal2"
    custom_dir.mkdir()
    func = mod.make_claude_llm_func(config_dir=str(custom_dir), model="claude-opus-4-7", timeout=120)
    answer = asyncio.run(func("Hello?"))
    assert answer == "claude-answer"
    assert captured["config_dir"] == str(custom_dir)
    assert captured["model"] == "claude-opus-4-7"
    assert captured["timeout"] == 120


def test_make_claude_llm_func_falls_back_to_env_then_home(monkeypatch, tmp_path):
    import tesserae.raganything_llm as mod

    captured = {}
    _isolate_llm_settings(monkeypatch, tmp_path)

    def fake_run_claude_cli(prompt, config_dir, model, timeout):
        captured["config_dir"] = config_dir
        return ""

    monkeypatch.setattr("tesserae.llm_extractor.run_claude_cli", fake_run_claude_cli)

    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    func = mod.make_claude_llm_func()  # no config_dir, no env
    asyncio.run(func("x"))
    assert captured["config_dir"].endswith(".claude") or "/.claude" in captured["config_dir"]

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/tmp/claude-env")
    func2 = mod.make_claude_llm_func()
    asyncio.run(func2("x"))
    assert captured["config_dir"] == "/tmp/claude-env"


def test_deterministic_embedding_is_deterministic_and_right_dim():
    from tesserae.raganything_llm import _deterministic_embedding

    a = _deterministic_embedding("hello", 768)
    b = _deterministic_embedding("hello", 768)
    c = _deterministic_embedding("world", 768)
    assert len(a) == 768
    assert a == b
    assert a != c


def test_build_runtime_funcs_default_uses_codex_and_deterministic(monkeypatch):
    from tesserae.raganything_llm import build_runtime_funcs

    async def fake_codex(prompt, model, timeout):
        return f"codex({prompt[:20]})"

    monkeypatch.setattr("tesserae.raganything_llm.run_codex_cli", fake_codex)

    funcs = build_runtime_funcs({})  # empty config -> all defaults
    assert "llm_model_func" in funcs
    assert "embedding_func" in funcs
    assert funcs["vision_model_func"] is None
    # llm func wired
    answer = asyncio.run(funcs["llm_model_func"]("hi"))
    assert answer.startswith("codex(")


def test_build_runtime_funcs_uses_claude_with_custom_config_dir(monkeypatch, tmp_path):
    from tesserae.raganything_llm import build_runtime_funcs

    custom = tmp_path / "claude-personal-3"
    custom.mkdir()
    captured = {}

    def fake_claude(prompt, config_dir, model, timeout):
        captured["config_dir"] = config_dir
        return "ok"

    monkeypatch.setattr("tesserae.llm_extractor.run_claude_cli", fake_claude)
    funcs = build_runtime_funcs({
        "llm": {"provider": "claude", "claude_config_dir": str(custom), "model": "opus", "timeout": 30}
    })
    asyncio.run(funcs["llm_model_func"]("hi"))
    assert captured["config_dir"] == str(custom)


def test_make_llm_func_rejects_unknown_provider():
    import pytest

    from tesserae.raganything_llm import make_llm_func

    with pytest.raises(ValueError, match="Unsupported raganything llm provider"):
        make_llm_func(provider="openai")


def test_make_embedding_func_rejects_unknown_provider():
    import pytest

    from tesserae.raganything_llm import make_embedding_func

    with pytest.raises(ValueError, match="Unsupported raganything embedding provider"):
        make_embedding_func(provider="bogus", dim=128)


def test_deterministic_embedding_func_returns_correct_shape():
    from tesserae.raganything_llm import make_deterministic_embedding_func

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


def test_make_claude_llm_func_honours_configured_dirs_and_endpoint(monkeypatch, tmp_path):
    """The Tesserae-configured claude dir list and custom endpoint reach the
    raganything claude func too — it used to read only CLAUDE_CONFIG_DIR."""
    import json

    import tesserae.raganything_llm as mod

    captured = {}

    def fake_run_claude_cli(prompt, config_dir, model, timeout, **kwargs):
        captured["config_dir"] = config_dir
        captured["kwargs"] = kwargs
        return "ok"

    monkeypatch.setattr("tesserae.llm_extractor.run_claude_cli", fake_run_claude_cli)
    _isolate_llm_settings(monkeypatch, tmp_path)
    global_cfg = tmp_path / "global.json"
    global_cfg.write_text(json.dumps({
        "llm_claude_config_dirs": ["/acct/work", "/acct/personal"],
        "llm_base_url": "https://gw.example",
        "llm_auth_token": "tok",
    }), encoding="utf-8")
    monkeypatch.setattr("tesserae.llm_json.GLOBAL_CONFIG_PATH", global_cfg)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/tmp/ambient-session-dir")

    asyncio.run(mod.make_claude_llm_func()("x"))
    assert captured["config_dir"] == "/acct/work", "configured list beats the ambient env var"
    assert captured["kwargs"] == {"base_url": "https://gw.example", "auth_token": "tok"}
