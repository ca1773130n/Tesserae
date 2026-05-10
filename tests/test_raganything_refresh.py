import json
from pathlib import Path

import pytest


def _force_modern_python(monkeypatch):
    """Pin sys.version_info to 3.11 so refresh tests run regardless of host interpreter."""
    import sys
    from collections import namedtuple
    V = namedtuple("version_info", ["major", "minor", "micro", "releaselevel", "serial"])
    monkeypatch.setattr(sys, "version_info", V(3, 11, 0, "final", 0))


def test_artifact_is_current_returns_false_when_manifest_missing(tmp_path):
    from llm_wiki.raganything_refresh import _artifact_is_current
    assert _artifact_is_current(tmp_path) is False


def test_artifact_is_current_returns_true_when_meta_matches_head(tmp_path, monkeypatch):
    import llm_wiki.raganything_refresh as mod
    base = tmp_path / ".llm-wiki" / "external" / "raganything"
    base.mkdir(parents=True)
    (base / "manifest.json").write_text("{}", encoding="utf-8")
    (base / "meta.json").write_text(json.dumps({"gitCommitHash": "abc"}), encoding="utf-8")
    monkeypatch.setattr(mod, "_git_head", lambda p: "abc")
    assert mod._artifact_is_current(tmp_path) is True


def test_artifact_is_current_returns_false_when_meta_differs(tmp_path, monkeypatch):
    import llm_wiki.raganything_refresh as mod
    base = tmp_path / ".llm-wiki" / "external" / "raganything"
    base.mkdir(parents=True)
    (base / "manifest.json").write_text("{}", encoding="utf-8")
    (base / "meta.json").write_text(json.dumps({"gitCommitHash": "old"}), encoding="utf-8")
    monkeypatch.setattr(mod, "_git_head", lambda p: "new")
    assert mod._artifact_is_current(tmp_path) is False


def test_discover_sources_returns_non_code_files_only(tmp_path):
    from llm_wiki.raganything_refresh import discover_sources
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("# code")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "spec.md").write_text("# spec")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "paper.pdf").write_bytes(b"%PDF-1.4")
    (tmp_path / "data" / "img.png").write_bytes(b"\x89PNG\r\n")
    (tmp_path / "data" / "notes.docx").write_bytes(b"PK\x03\x04")
    (tmp_path / ".llm-wiki").mkdir()
    (tmp_path / ".llm-wiki" / "scratch.md").write_text("# excluded")

    sources = sorted(str(p.relative_to(tmp_path)) for p in discover_sources(tmp_path, roots=["docs", "data"]))
    assert sources == ["data/img.png", "data/notes.docx", "data/paper.pdf", "docs/spec.md"]


def test_write_manifest_serializes_documents_with_sha256(tmp_path):
    from llm_wiki.raganything_refresh import write_manifest

    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4 hello")
    documents = [
        {
            "path": pdf,
            "content_list": [
                {"type": "text", "page_idx": 0, "text": "Hello"},
                {"type": "image", "page_idx": 0, "img_path": "x.png"},
            ],
        }
    ]
    manifest_path = write_manifest(
        tmp_path,
        documents=documents,
        parser="mineru",
        parser_version="2.0",
        git_commit="abc123",
    )

    assert manifest_path.exists()
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert payload["parser"] == "mineru"
    assert payload["git_commit"] == "abc123"
    assert payload["documents"][0]["path"] == "doc.pdf"
    assert len(payload["documents"][0]["sha256"]) == 64
    meta = json.loads((tmp_path / ".llm-wiki" / "external" / "raganything" / "meta.json").read_text())
    assert meta["gitCommitHash"] == "abc123"
    assert meta["parser"] == "mineru"


def test_refresh_runs_parse_documents_and_writes_manifest(tmp_path, monkeypatch):
    import llm_wiki.raganything_refresh as mod
    _force_modern_python(monkeypatch)

    (tmp_path / "data").mkdir()
    pdf = tmp_path / "data" / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4")

    fake_called = {}

    def fake_parse(project, *, sources, parser, parse_method, working_dir, llm_funcs, **kwargs):
        fake_called["sources"] = sorted(str(s.relative_to(project)) for s in sources)
        fake_called["parser"] = parser
        return [
            {
                "path": pdf,
                "content_list": [{"type": "text", "page_idx": 0, "text": "ok"}],
            }
        ]

    monkeypatch.setattr(mod, "parse_documents", fake_parse)
    monkeypatch.setattr(mod, "_git_head", lambda p: "deadbeef")

    rc = mod.refresh_raganything(tmp_path, parser="mineru", roots=["data"], force=True)
    assert rc == 0
    assert fake_called["sources"] == ["data/paper.pdf"]
    manifest = tmp_path / ".llm-wiki" / "external" / "raganything" / "manifest.json"
    assert manifest.exists()
    payload = json.loads(manifest.read_text())
    assert payload["parser"] == "mineru"
    assert payload["git_commit"] == "deadbeef"


def test_refresh_returns_5_when_every_source_fails(tmp_path, monkeypatch):
    import llm_wiki.raganything_refresh as mod
    _force_modern_python(monkeypatch)

    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "a.pdf").write_bytes(b"%PDF-1.4")
    (tmp_path / "data" / "b.pdf").write_bytes(b"%PDF-1.4")

    def fake_parse(project, *, sources, parser, parse_method, working_dir, llm_funcs, **kwargs):
        return [{"path": s, "content_list": [], "error": "boom"} for s in sources]

    monkeypatch.setattr(mod, "parse_documents", fake_parse)
    monkeypatch.setattr(mod, "_git_head", lambda p: "abc")
    rc = mod.refresh_raganything(tmp_path, parser="mineru", roots=["data"], force=True)
    assert rc == 5


def test_refresh_skips_when_artifact_current(tmp_path, monkeypatch, capsys):
    import llm_wiki.raganything_refresh as mod
    _force_modern_python(monkeypatch)
    base = tmp_path / ".llm-wiki" / "external" / "raganything"
    base.mkdir(parents=True)
    (base / "manifest.json").write_text("{}")
    (base / "meta.json").write_text(json.dumps({"gitCommitHash": "abc"}))
    monkeypatch.setattr(mod, "_git_head", lambda p: "abc")
    monkeypatch.setattr(mod, "parse_documents", lambda *a, **k: pytest.fail("should not parse"))

    rc = mod.refresh_raganything(tmp_path, parser="mineru")
    assert rc == 0
    out = capsys.readouterr().out
    assert "already current" in out


def test_refresh_returns_6_when_python_too_old(tmp_path, monkeypatch, capsys):
    import sys, llm_wiki.raganything_refresh as mod
    from collections import namedtuple
    V = namedtuple("version_info", ["major", "minor", "micro", "releaselevel", "serial"])
    monkeypatch.setattr(sys, "version_info", V(3, 9, 0, "final", 0))
    rc = mod.refresh_raganything(tmp_path, parser="mineru", force=True)
    assert rc == 6
    err = capsys.readouterr().err
    assert "Python 3.10+" in err


def test_pick_parser_for_path_routes_text_to_text_parser():
    from llm_wiki.raganything_refresh import pick_parser_for_path
    from pathlib import Path
    assert pick_parser_for_path(Path("a.md"), default_parser="mineru") == "docling"
    assert pick_parser_for_path(Path("a.txt"), default_parser="mineru") == "docling"
    assert pick_parser_for_path(Path("a.rst"), default_parser="mineru") == "docling"


def test_pick_parser_for_path_routes_office_to_office_parser():
    from llm_wiki.raganything_refresh import pick_parser_for_path
    from pathlib import Path
    assert pick_parser_for_path(Path("a.docx"), default_parser="mineru") == "docling"
    assert pick_parser_for_path(Path("a.pptx"), default_parser="mineru") == "docling"


def test_pick_parser_for_path_falls_through_for_pdf_and_images():
    from llm_wiki.raganything_refresh import pick_parser_for_path
    from pathlib import Path
    assert pick_parser_for_path(Path("a.pdf"), default_parser="mineru") == "mineru"
    assert pick_parser_for_path(Path("a.png"), default_parser="mineru") == "mineru"
    assert pick_parser_for_path(Path("a.pdf"), default_parser="docling") == "docling"


def test_pick_parser_for_path_honors_custom_overrides():
    from llm_wiki.raganything_refresh import pick_parser_for_path
    from pathlib import Path
    assert pick_parser_for_path(Path("a.md"), default_parser="mineru", text_parser="paddleocr") == "paddleocr"
    assert pick_parser_for_path(Path("a.docx"), default_parser="mineru", office_parser="mineru") == "mineru"


def test_install_hint_for_known_parsers():
    from llm_wiki.raganything_refresh import _install_hint_for
    assert "mineru[core]" in _install_hint_for("mineru")
    assert "raganything[all]" in _install_hint_for("docling")
    assert "paddleocr" in _install_hint_for("paddleocr")


def test_verify_parsers_or_raise_passes_when_all_ok():
    from llm_wiki.raganything_refresh import _verify_parsers_or_raise

    class FakeRag:
        def check_parser_installation(self, parser_name=None):
            return True

    _verify_parsers_or_raise(FakeRag(), ["mineru", "docling"])  # should not raise


def test_verify_parsers_or_raise_raises_with_actionable_hint():
    from llm_wiki.raganything_refresh import _verify_parsers_or_raise
    import pytest

    class FakeRag:
        def check_parser_installation(self, parser_name=None):
            return parser_name != "mineru"

    with pytest.raises(RuntimeError) as exc:
        _verify_parsers_or_raise(FakeRag(), ["mineru"])
    msg = str(exc.value)
    assert "mineru" in msg
    assert "mineru[core]" in msg  # hint included


def test_verify_parsers_handles_old_api_without_parser_name_kwarg():
    from llm_wiki.raganything_refresh import _verify_parsers_or_raise

    class OldRag:
        def check_parser_installation(self):
            return True

    _verify_parsers_or_raise(OldRag(), ["mineru"])  # should not raise (legacy fallback)
