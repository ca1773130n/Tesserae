import json
from pathlib import Path
from tesserae.project import ProjectWiki


def _seed(root: Path) -> ProjectWiki:
    (root / "data").mkdir(parents=True, exist_ok=True)
    (root / "data" / "s.md").write_text("---\ntype: paper\n---\n# S\n\nretrieval graphs\n", encoding="utf-8")
    return ProjectWiki.init(root, name="seam_test")


def _last_build_mode(wiki: ProjectWiki) -> str:
    rows = [
        json.loads(line)
        for line in (wiki.root / ".build-history.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return rows[-1]["mode"]


def test_incremental_override_wins_over_a_config_flag_that_is_off(tmp_path):
    """`incremental_override=True` enables the seam even when the project opted out."""
    wiki = _seed(tmp_path)
    cfg = json.loads(wiki.paths.config.read_text(encoding="utf-8"))
    cfg["incremental_compile"] = False
    wiki.paths.config.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    wiki.compile(changed_only=False)

    (tmp_path / "data" / "n.md").write_text("---\ntype: paper\n---\n# N\n\ndiffusion planning\n", encoding="utf-8")
    wiki.ingest([str(tmp_path / "data" / "n.md")], changed_only=True, incremental_override=True)
    assert _last_build_mode(wiki) == "incremental"
