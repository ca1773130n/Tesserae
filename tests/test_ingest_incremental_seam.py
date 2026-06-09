from pathlib import Path
from tesserae.project import ProjectWiki


def _seed(root: Path) -> ProjectWiki:
    (root / "data").mkdir(parents=True, exist_ok=True)
    (root / "data" / "s.md").write_text("---\ntype: paper\n---\n# S\n\nretrieval graphs\n", encoding="utf-8")
    return ProjectWiki.init(root, name="seam_test")


def test_incremental_override_enables_without_config_flag(tmp_path):
    wiki = _seed(tmp_path)
    wiki.compile(changed_only=False)
    import logging
    records = []
    handler = logging.Handler()
    handler.emit = lambda r: records.append(r.getMessage())
    logging.getLogger("tesserae.project").addHandler(handler)

    (tmp_path / "data" / "n.md").write_text("---\ntype: paper\n---\n# N\n\ndiffusion planning\n", encoding="utf-8")
    wiki.ingest([str(tmp_path / "data" / "n.md")], changed_only=True, incremental_override=True)
    assert any("incremental_compile is ENABLED" in m for m in records)
