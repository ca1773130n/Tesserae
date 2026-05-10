from llm_wiki.project_setup import build_setup_plan


def _make_version_info(major, minor, micro=0, releaselevel="final", serial=0):
    """Build a sys.version_info-compatible namedtuple (the real type is uninstantiable)."""
    from collections import namedtuple
    V = namedtuple("version_info", ["major", "minor", "micro", "releaselevel", "serial"])
    return V(major, minor, micro, releaselevel, serial)


def _force_modern_python(monkeypatch):
    """Pin sys.version_info to a 3.10+ tuple so tests don't depend on the host interpreter."""
    import sys
    monkeypatch.setattr(sys, "version_info", _make_version_info(3, 11))


def test_build_setup_plan_with_raganything_appends_external_tool_and_backend(tmp_path, monkeypatch):
    _force_modern_python(monkeypatch)
    plan = build_setup_plan(
        tmp_path,
        name="demo",
        sources=["README.md"],
        include_raganything=True,
        install_raganything=True,
        raganything_parser="mineru",
        run_raganything=True,
    )
    raga = next(t for t in plan.external_tools if t["id"] == "raganything")
    assert raga["sync_mode"] == "native_graph"
    assert raga["parser"] == "mineru"
    assert raga["auto_refresh"] is True
    assert raga["artifact"] == ".llm-wiki/external/raganything/manifest.json"
    assert raga["install"]["auto_install"] is True
    assert "docling" in raga["install"]["command"]
    assert plan.memory_backends["raganything"]["enabled"] is True
    assert plan.memory_backends["raganything"]["parser"] == "mineru"


def test_build_setup_plan_without_raganything_does_not_add_entry(tmp_path):
    plan = build_setup_plan(tmp_path, name="demo", sources=["README.md"])
    assert all(t["id"] != "raganything" for t in plan.external_tools)
    assert "raganything" not in (plan.memory_backends or {})


def test_build_setup_plan_with_raganything_alone_defaults_to_install_when_missing(tmp_path, monkeypatch):
    _force_modern_python(monkeypatch)
    # Simulate raganything not being importable
    import sys
    monkeypatch.setitem(sys.modules, "raganything", None)
    # When sys.modules has a key mapped to None, `import raganything` raises ModuleNotFoundError.

    plan = build_setup_plan(
        tmp_path,
        name="demo",
        sources=["README.md"],
        include_raganything=True,
        # Note: install_raganything not passed -> defaults to None -> auto-detect
    )
    raga = next(t for t in plan.external_tools if t["id"] == "raganything")
    assert raga["install"]["auto_install"] is True


def test_build_setup_plan_with_raganything_alone_skips_install_when_already_present(tmp_path, monkeypatch):
    _force_modern_python(monkeypatch)
    import sys, types
    fake = types.ModuleType("raganything")
    monkeypatch.setitem(sys.modules, "raganything", fake)

    plan = build_setup_plan(
        tmp_path,
        name="demo",
        sources=["README.md"],
        include_raganything=True,
    )
    raga = next(t for t in plan.external_tools if t["id"] == "raganything")
    assert raga["install"]["auto_install"] is False


def test_build_setup_plan_explicit_install_raganything_false_overrides_auto(tmp_path, monkeypatch):
    _force_modern_python(monkeypatch)
    import sys
    monkeypatch.setitem(sys.modules, "raganything", None)

    plan = build_setup_plan(
        tmp_path,
        name="demo",
        sources=["README.md"],
        include_raganything=True,
        install_raganything=False,  # explicit override even though missing
    )
    raga = next(t for t in plan.external_tools if t["id"] == "raganything")
    assert raga["install"]["auto_install"] is False


def test_build_setup_plan_disables_raganything_on_python_below_3_10(tmp_path, monkeypatch):
    import sys
    monkeypatch.setattr(sys, "version_info", _make_version_info(3, 9))
    plan = build_setup_plan(
        tmp_path,
        name="demo",
        sources=["README.md"],
        include_raganything=True,
    )
    raga = next(t for t in plan.external_tools if t["id"] == "raganything")
    assert raga["enabled"] is False
    assert raga["install"]["auto_install"] is False
    assert "Python 3.10" in (raga.get("python_warning") or "")
    assert plan.memory_backends["raganything"]["enabled"] is False


def test_build_setup_plan_persists_raganything_llm_and_embedding(tmp_path, monkeypatch):
    _force_modern_python(monkeypatch)
    plan = build_setup_plan(
        tmp_path,
        name="demo",
        sources=["README.md"],
        include_raganything=True,
        raganything_llm_provider="claude",
        raganything_llm_model="claude-opus-4-7",
        raganything_claude_config_dir="/tmp/claude-personal2",
        raganything_embedding_provider="deterministic",
        raganything_embedding_dim=512,
    )
    raga_backend = plan.memory_backends["raganything"]
    assert raga_backend["llm"]["provider"] == "claude"
    assert raga_backend["llm"]["model"] == "claude-opus-4-7"
    assert raga_backend["llm"]["claude_config_dir"] == "/tmp/claude-personal2"
    assert raga_backend["embedding"]["provider"] == "deterministic"
    assert raga_backend["embedding"]["dim"] == 512


def test_build_setup_plan_defaults_codex_provider_and_deterministic_embedding(tmp_path, monkeypatch):
    _force_modern_python(monkeypatch)
    plan = build_setup_plan(
        tmp_path,
        name="demo",
        sources=["README.md"],
        include_raganything=True,
    )
    raga_backend = plan.memory_backends["raganything"]
    assert raga_backend["llm"]["provider"] == "codex"
    assert raga_backend["llm"]["model"] == "gpt-5.4"
    assert raga_backend["llm"]["claude_config_dir"] is None
    assert raga_backend["embedding"]["provider"] == "deterministic"
    assert raga_backend["embedding"]["dim"] == 768
