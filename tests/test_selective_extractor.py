from tesserae.selective_extractor import SelectiveClaudeResearchExtractor
from tesserae.research_graph import ResearchGraph, ResearchNode, ResearchNodeType


class FakeExtractor:
    def __init__(self, label):
        self.label = label
        self.calls = []

    def extract_file(self, path, source_kind="SourceDocument"):
        self.calls.append(str(path))
        return ResearchGraph(nodes=[ResearchNode(id=f"Paper:{self.label}:test", name=self.label, type=ResearchNodeType.PAPER)], edges=[])


def test_selective_claude_extractor_uses_claude_only_for_matching_paths(tmp_path):
    deterministic = FakeExtractor("deterministic")
    claude = FakeExtractor("claude")
    selected = tmp_path / "important" / "paper.md"
    plain = tmp_path / "plain" / "paper.md"
    selected.parent.mkdir()
    plain.parent.mkdir()
    selected.write_text("# important", encoding="utf-8")
    plain.write_text("# plain", encoding="utf-8")

    extractor = SelectiveClaudeResearchExtractor(deterministic=deterministic, claude=claude, include_patterns=["*/important/*"])

    assert extractor.extract_file(selected, source_kind="Paper").nodes[0].name == "claude"
    assert extractor.extract_file(plain, source_kind="Paper").nodes[0].name == "deterministic"
    assert claude.calls == [str(selected)]
    assert deterministic.calls == [str(plain)]


def test_selective_claude_extractor_limit_falls_back_after_budget(tmp_path):
    deterministic = FakeExtractor("deterministic")
    claude = FakeExtractor("claude")
    first = tmp_path / "papers" / "a.md"
    second = tmp_path / "papers" / "b.md"
    first.parent.mkdir()
    first.write_text("# a", encoding="utf-8")
    second.write_text("# b", encoding="utf-8")

    extractor = SelectiveClaudeResearchExtractor(deterministic=deterministic, claude=claude, include_patterns=["*.md"], claude_limit=1)

    assert extractor.extract_file(first, source_kind="Paper").nodes[0].name == "claude"
    assert extractor.extract_file(second, source_kind="Paper").nodes[0].name == "deterministic"


def test_last_was_fallback_tracks_llm_failure_only():
    """The manifest's fallback mark must mean 'the LLM was asked and failed', not
    'this doc has no typed nodes' — an i18n duplicate legitimately extracts empty
    and must NOT be re-attempted forever by --retry-fallbacks."""
    from tesserae.research_graph import ResearchGraph
    from tesserae.selective_extractor import SelectiveClaudeResearchExtractor

    class Boom:
        def extract_text(self, *a, **k):
            raise RuntimeError("codex returned no usable JSON")

    class Empty:
        def extract_text(self, *a, **k):
            return ResearchGraph(nodes=[], edges=[])

    class Det:
        def extract_text(self, *a, **k):
            return ResearchGraph(nodes=[], edges=[])

    sel = SelectiveClaudeResearchExtractor(
        deterministic=Det(), claude=Boom(), include_patterns=["*.md"]
    )
    sel.extract_text("x", "/tmp/doc.md")
    assert sel.last_was_fallback is True          # LLM raised -> degraded

    # not routed to the LLM at all: got exactly what it asked for
    sel.extract_text("x", "/tmp/doc.txt")
    assert sel.last_was_fallback is False

    # LLM answered with an empty graph: legitimate, not a fallback
    sel2 = SelectiveClaudeResearchExtractor(
        deterministic=Det(), claude=Empty(), include_patterns=["*.md"]
    )
    sel2.extract_text("x", "/tmp/doc.md")
    assert sel2.last_was_fallback is False


def test_llm_limit_holds_under_concurrent_extraction():
    """``--llm-limit`` is a SPEND cap, so it has to survive parallel workers.

    ``BatchIngestRunner`` extracts on a thread pool. The routing decision used
    to read ``claude_calls`` and the caller incremented it afterwards, so every
    worker could pass a limit of 1 before any of them wrote — one paid call per
    worker instead of one call, silently. The barrier below forces exactly that
    interleaving; without the lock this asserts 8 == 1.
    """
    import threading

    workers = 8
    # Timed, so the test terminates BOTH ways. Unserialized, all eight decide
    # before any of them claims and the barrier trips; serialized, the first
    # holder simply times out and the rest are excluded by the counter. A plain
    # wait() would deadlock against a correct lock, which proves nothing.
    barrier = threading.Barrier(workers)

    class Det:
        def extract_text(self, text, source_path=None, source_kind="SourceDocument"):
            return ResearchGraph(nodes=[], edges=[])

    class Counting:
        def __init__(self):
            self.calls = []
            self._guard = threading.Lock()

        def extract_text(self, text, source_path=None, source_kind="SourceDocument"):
            with self._guard:
                self.calls.append(source_path)
            return ResearchGraph(nodes=[], edges=[])

    llm = Counting()
    sel = SelectiveClaudeResearchExtractor(
        deterministic=Det(), claude=llm, include_patterns=["*.md"], claude_limit=1
    )

    decide = sel._should_use_claude

    def widen_the_window(path):
        verdict = decide(path)
        try:
            barrier.wait(timeout=1.0)
        except threading.BrokenBarrierError:
            pass
        return verdict

    sel._should_use_claude = widen_the_window

    threads = [
        threading.Thread(target=lambda i=i: sel.extract_text("x", f"/tmp/doc{i}.md"))
        for i in range(workers)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(llm.calls) == 1, f"claude_limit=1 issued {len(llm.calls)} LLM calls"
    assert sel.claude_calls == 1
