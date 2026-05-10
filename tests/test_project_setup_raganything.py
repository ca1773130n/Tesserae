from llm_wiki.project_setup import build_setup_plan


def test_build_setup_plan_with_raganything_appends_external_tool_and_backend(tmp_path):
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
    assert plan.memory_backends["raganything"]["enabled"] is True
    assert plan.memory_backends["raganything"]["parser"] == "mineru"


def test_build_setup_plan_without_raganything_does_not_add_entry(tmp_path):
    plan = build_setup_plan(tmp_path, name="demo", sources=["README.md"])
    assert all(t["id"] != "raganything" for t in plan.external_tools)
    assert "raganything" not in (plan.memory_backends or {})


def test_build_setup_plan_with_raganything_alone_defaults_to_install_when_missing(tmp_path, monkeypatch):
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
