"""`source_path` is attacker-influenced, and it is read straight into an LLM prompt.

Tesserae ingests documents and URLs from outside the project. A document carries
its own frontmatter, a wiki page is an ordinary file a user or tool can edit, and
both paths that pull "the source" trust that string. Unconfined, a crafted
`source_path: /etc/ssh/id_rsa` is read verbatim into the synthesis prompt and
leaves through the answer — an arbitrary file read with an exfiltration channel
attached.

These tests use a real file outside the project root, because a test that only
asserts "the guard is present" passes just as well when the guard is wrong.
"""

from __future__ import annotations

from pathlib import Path

from tesserae.ask_planner import _read_source
from tesserae.context_compiler import _source_text
from tesserae.research_graph import ResearchNode, ResearchNodeType


def _secret(tmp_path: Path) -> Path:
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "id_rsa"
    secret.write_text("BEGIN OPENSSH PRIVATE KEY", encoding="utf-8")
    return secret


def test_planner_refuses_a_path_outside_the_project(tmp_path: Path) -> None:
    root = tmp_path / "project"
    (root / ".tesserae").mkdir(parents=True)
    secret = _secret(tmp_path)

    assert _read_source(str(secret), {}, root) == ""
    # ..-escape from inside the root resolves outside and must also fail.
    escape = root / ".tesserae" / ".." / ".." / "outside" / "id_rsa"
    assert _read_source(str(escape), {}, root) == ""
    # No root at all reads nothing, rather than reading anything.
    assert _read_source(str(secret), {}, None) == ""


def test_planner_still_reads_a_legitimate_source(tmp_path: Path) -> None:
    root = tmp_path / "project"
    (root / "corpus").mkdir(parents=True)
    doc = root / "corpus" / "paper.md"
    doc.write_text("the mechanism is a ConvGRU update operator", encoding="utf-8")
    assert "ConvGRU" in _read_source(str(doc), {}, root)


def test_symlink_out_of_the_tree_is_refused(tmp_path: Path) -> None:
    """Resolving before comparing is what makes this fail; a string prefix
    check on the unresolved path would let it through."""
    root = tmp_path / "project"
    root.mkdir()
    secret = _secret(tmp_path)
    link = root / "innocent.md"
    try:
        link.symlink_to(secret)
    except (OSError, NotImplementedError):  # pragma: no cover - platform
        return
    assert _read_source(str(link), {}, root) == ""


def test_context_compiler_is_confined_too(tmp_path: Path) -> None:
    """The same value reaches the bundle by a second route, and one guarded
    caller is not a guarded system."""
    root = tmp_path / "project"
    root.mkdir()
    secret = _secret(tmp_path)
    node = ResearchNode(id="Concept:x", name="x", type=ResearchNodeType.CONCEPT,
                        source_path=str(secret))

    assert _source_text(node, {}, str(root)) == ""
    assert _source_text(node, {}, None) == ""

    inside = root / "real.md"
    inside.write_text("legitimate corpus text", encoding="utf-8")
    ok = ResearchNode(id="Concept:y", name="y", type=ResearchNodeType.CONCEPT,
                      source_path=str(inside))
    assert "legitimate" in _source_text(ok, {}, str(root))


def test_the_cache_cannot_serve_a_refused_read_to_a_permitted_root(tmp_path: Path) -> None:
    """The cache is keyed on (root, path), not path. Keyed on path alone, one
    permitted read would poison every later root — or a refusal would."""
    root_a = tmp_path / "a"; root_a.mkdir()
    doc = root_a / "d.md"; doc.write_text("inside A", encoding="utf-8")
    root_b = tmp_path / "b"; root_b.mkdir()

    cache: dict = {}
    assert _read_source(str(doc), cache, root_b) == ""      # refused first
    assert "inside A" in _read_source(str(doc), cache, root_a)  # still allowed
