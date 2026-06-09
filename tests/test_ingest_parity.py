"""Golden parity: ingest(X) graph.json == a full compile of the same corpus, byte-for-byte."""
from pathlib import Path

from tesserae.project import ProjectWiki
from tesserae.ingest.orchestrator import ingest_sources


def _write(root: Path, name: str, body: str) -> Path:
    (root / "data").mkdir(parents=True, exist_ok=True)
    p = root / "data" / name
    p.write_text(body, encoding="utf-8")
    return p


# Distinct HEADINGS (the deterministic extractor keys node identity off the heading,
# not body prose), so the three docs are distinct nodes.
_CORPUS = {
    "a.md": "---\ntype: paper\n---\n# Graph Neural Networks\n\nbody a\n",
    "b.md": "---\ntype: paper\n---\n# Retrieval Augmented Generation\n\nbody b\n",
}
_NEW_NAME = "c.md"
_NEW_BODY = "---\ntype: paper\n---\n# Diffusion Planning\n\nbody c\n"


def _parity(root: Path, *, exact: bool) -> tuple[bytes, bytes]:
    wiki = ProjectWiki.init(root, name="parity")
    for name, body in _CORPUS.items():
        _write(root, name, body)
    wiki.compile(changed_only=False)                      # baseline WITHOUT the new doc
    new = _write(root, _NEW_NAME, _NEW_BODY)
    ingest_sources(wiki, [str(new)], exact=exact)
    ing = wiki.paths.graph.read_bytes()                   # graph after ingest
    wiki.compile(changed_only=False)                      # full recompile of corpus ∪ X
    full = wiki.paths.graph.read_bytes()                  # graph after full recompile
    return ing, full


def test_ingest_exact_matches_full_compile(tmp_path):
    ing, full = _parity(tmp_path / "exact", exact=True)
    assert ing == full, "ingest --exact graph.json must equal a full compile of the same corpus"


def test_ingest_fast_matches_full_compile(tmp_path):
    ing, full = _parity(tmp_path / "fast", exact=False)
    assert ing == full, (
        "INCREMENTAL ingest graph.json diverged from a full compile. The additive single-doc "
        "case hit a residual gap. FIX by extending the full-recompile fallback condition in "
        "Project.compile (the over-cap fallback near line ~843) so the offending shape degrades "
        "to a full recompile — do NOT weaken this assertion."
    )
