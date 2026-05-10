def test_default_raganything_backend_config_has_required_fields():
    from llm_wiki.project import default_raganything_backend_config

    cfg = default_raganything_backend_config("demo")
    assert cfg["enabled"] is False  # opt-in: keys may not be configured
    assert cfg["working_dir"] == ".llm-wiki/external/raganything/working_dir"
    assert cfg["parser"] == "mineru"
    assert cfg["parse_method"] == "auto"
    assert cfg["query_mode"] == "hybrid"
    assert cfg["vlm_enhanced"] is True
    assert cfg["install"]["command"].startswith("{python} -m pip install")
    assert cfg["install"]["auto_install"] is False
