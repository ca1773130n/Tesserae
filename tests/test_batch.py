import json
from pathlib import Path

from tesserae.batch import BatchIngestRunner, sha256_text
from tesserae.research_graph import ResearchGraph, ResearchNode, ResearchNodeType


class CountingExtractor:
    def __init__(self):
        self.calls = []

    def extract_file(self, path, source_kind="SourceDocument"):
        self.calls.append(str(path))
        p = Path(path)
        return ResearchGraph(nodes=[ResearchNode(id=f"Paper:{p.stem}:test", name=p.stem, type=ResearchNodeType.PAPER)], edges=[])

    def extract_text(self, content, source_path, source_kind="SourceDocument"):
        self.calls.append(source_path)
        p = Path(source_path)
        return ResearchGraph(nodes=[ResearchNode(id=f"Paper:{p.stem}:test", name=p.stem, type=ResearchNodeType.PAPER)], edges=[])


def test_sha256_text_is_stable():
    assert sha256_text("abc") == sha256_text("abc")
    assert sha256_text("abc") != sha256_text("abcd")


def test_batch_runner_skips_unchanged_files_with_manifest(tmp_path):
    file1 = tmp_path / "a.md"
    file2 = tmp_path / "b.md"
    file1.write_text("# A\nGaussian Splatting", encoding="utf-8")
    file2.write_text("# B\nNovel View Synthesis", encoding="utf-8")
    manifest = tmp_path / "manifest.json"

    extractor = CountingExtractor()
    runner = BatchIngestRunner(extractor=extractor, manifest_path=manifest)
    first = runner.run([file1, file2], source_kind="Paper", changed_only=True)

    assert first.processed == 2
    assert first.skipped == 0
    assert len(extractor.calls) == 2
    assert manifest.exists()

    second_extractor = CountingExtractor()
    second = BatchIngestRunner(extractor=second_extractor, manifest_path=manifest).run([file1, file2], source_kind="Paper", changed_only=True)

    assert second.processed == 0
    assert second.skipped == 2
    assert second.graph.nodes == []
    assert second_extractor.calls == []

    file2.write_text("# B changed\nNovel View Synthesis", encoding="utf-8")
    third_extractor = CountingExtractor()
    third = BatchIngestRunner(extractor=third_extractor, manifest_path=manifest).run([file1, file2], source_kind="Paper", changed_only=True)

    assert third.processed == 1
    assert third.skipped == 1
    assert third_extractor.calls == [str(file2)]


def test_batch_runner_limit_caps_processed_files(tmp_path):
    files = []
    for idx in range(3):
        path = tmp_path / f"{idx}.md"
        path.write_text(f"# P{idx}", encoding="utf-8")
        files.append(path)

    extractor = CountingExtractor()
    result = BatchIngestRunner(extractor=extractor, manifest_path=tmp_path / "manifest.json").run(files, source_kind="Paper", limit=2)

    assert result.processed == 2
    assert len(result.graph.nodes) == 2
    assert len(extractor.calls) == 2


def test_batch_runner_handles_non_utf8_markdown_with_replacement(tmp_path):
    source = tmp_path / "raw.md"
    source.write_bytes(b"# Broken\nvalid text \xe3 invalid byte")
    manifest = tmp_path / "manifest.json"
    extractor = CountingExtractor()

    result = BatchIngestRunner(extractor=extractor, manifest_path=manifest).run([source], source_kind="Paper")

    assert result.processed == 1
    assert extractor.calls == [str(source)]
    assert manifest.exists()
