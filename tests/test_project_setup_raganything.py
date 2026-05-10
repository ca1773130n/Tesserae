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
