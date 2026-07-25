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


def test_batch_runner_drives_progress_scan_and_advance(tmp_path):
    """The per-file loop reports scan(total) once and advance() per file."""
    from tesserae.batch import BatchIngestRunner

    f1 = tmp_path / "a.md"
    f2 = tmp_path / "b.md"
    f3 = tmp_path / "c.md"
    for f in (f1, f2, f3):
        f.write_text("# x\nGaussian Splatting", encoding="utf-8")
    manifest = tmp_path / "manifest.json"

    class RecordingProgress:
        def __init__(self):
            self.scanned = None
            self.advances = 0
            self.extract_done_n = None

        def scan(self, total):
            self.scanned = total

        def extract_start(self, total):
            self.started = total

        def advance(self):
            self.advances += 1

        def extract_done(self, n):
            self.extract_done_n = n

    prog = RecordingProgress()
    runner = BatchIngestRunner(extractor=CountingExtractor(), manifest_path=manifest)
    result = runner.run([f1, f2, f3], source_kind="Paper", progress=prog)

    assert result.processed == 3
    assert prog.scanned == 3
    assert prog.advances == 3  # one per file visited (processed or skipped)
    assert prog.extract_done_n == 3


def test_batch_runner_progress_is_optional(tmp_path):
    """Existing callers that pass no progress still work unchanged."""
    from tesserae.batch import BatchIngestRunner

    f1 = tmp_path / "a.md"
    f1.write_text("# x", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    result = BatchIngestRunner(extractor=CountingExtractor(), manifest_path=manifest).run([f1])
    assert result.processed == 1


class FlakyExtractor(CountingExtractor):
    """Extractor that reports a deterministic fallback for chosen paths.

    Mirrors ``SelectiveClaudeResearchExtractor``'s duck-typed contract: the
    runner reads ``last_was_fallback`` after each extract_text call.
    """

    def __init__(self, failing: set[str] | None = None):
        super().__init__()
        self.failing = failing if failing is not None else set()
        self.last_was_fallback = False

    def extract_text(self, content, source_path, source_kind="SourceDocument"):
        self.last_was_fallback = Path(source_path).name in self.failing
        return super().extract_text(content, source_path, source_kind)


def test_fallback_docs_are_marked_and_only_retried_on_demand(tmp_path):
    """A doc whose typed extraction fell back is content-identical to a clean one,
    so plain --changed-only skips it forever. --retry-fallbacks re-attempts exactly
    those, and a clean entry stays byte-identical to what prior versions wrote."""
    good = tmp_path / "good.md"
    bad = tmp_path / "bad.md"
    good.write_text("# Good\nGaussian Splatting", encoding="utf-8")
    bad.write_text("# Bad\nNovel View Synthesis", encoding="utf-8")
    manifest = tmp_path / "manifest.json"

    flaky = FlakyExtractor(failing={"bad.md"})
    first = BatchIngestRunner(extractor=flaky, manifest_path=manifest).run(
        [good, bad], source_kind="Paper", changed_only=True
    )
    assert first.processed == 2
    assert first.fallback_paths == [str(bad)]

    entries = json.loads(manifest.read_text(encoding="utf-8"))["files"]
    assert entries[str(bad)]["fallback"] is True
    assert "fallback" not in entries[str(good)]  # clean entries unchanged

    # Plain changed-only leaves the degraded doc degraded — the bug.
    plain = FlakyExtractor(failing={"bad.md"})
    second = BatchIngestRunner(extractor=plain, manifest_path=manifest).run(
        [good, bad], source_kind="Paper", changed_only=True
    )
    assert second.processed == 0 and second.skipped == 2
    assert plain.calls == []

    # --retry-fallbacks re-attempts ONLY the fallback doc.
    recovered = FlakyExtractor(failing=set())  # provider healthy again
    third = BatchIngestRunner(extractor=recovered, manifest_path=manifest).run(
        [good, bad], source_kind="Paper", changed_only=True, retry_fallbacks=True
    )
    assert recovered.calls == [str(bad)]
    assert third.processed == 1 and third.skipped == 1
    assert third.fallback_paths == []

    # The mark is cleared, so the next run skips it again.
    entries = json.loads(manifest.read_text(encoding="utf-8"))["files"]
    assert "fallback" not in entries[str(bad)]
