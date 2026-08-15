"""Guards for the removed contrast pass and the residue it left behind.

The contrast pass was dropped (01a298c1) after minting zero edges from 80 judged
pairs on two real corpora. A v2 was designed and measured offline against both
real graphs — five difference-selecting candidate generators, zero LLM calls —
and none cleared the bar, so nothing was rebuilt. See the commit message.

What DID survive the removal was a lint remediation string still telling users to
run ``TESSERAE_CONTRAST_PASS=1 tesserae compile``. That flag has had no reader
anywhere in the package since the pass was deleted, and the check that emits it
is below its floor on both real graphs, so every real lint run advertised a
no-op. These tests keep that class of defect from coming back: a documented flag
must either work or not be documented.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from tesserae.lint import WikiLinter

_PKG = Path(__file__).resolve().parent.parent / "tesserae"
_ENV_FLAG = re.compile(r"TESSERAE_[A-Z0-9_]+")


def _scaffold(tmp_path: Path, graph: dict) -> Path:
    project = tmp_path / "demo"
    wiki_root = project / ".tesserae"
    for sub in ("papers", "concepts", "repos", "syntheses", "entities"):
        (wiki_root / "wiki" / sub).mkdir(parents=True)
    (wiki_root / "site").mkdir(parents=True)
    (wiki_root / "graph.json").write_text(json.dumps(graph), encoding="utf-8")
    return project


def _ratio_graph(structural: int, reasoning: int) -> dict:
    nodes = [{"id": "c0", "type": "Claim", "name": "anchor"}]
    edges = []
    for i in range(structural):
        nodes.append({"id": f"s{i}", "type": "Paper", "name": f"paper {i}"})
        edges.append({"source": f"s{i}", "target": "c0", "type": "discussed_in"})
    for i in range(reasoning):
        nodes.append({"id": f"r{i}", "type": "Paper", "name": f"support {i}"})
        edges.append({"source": f"r{i}", "target": "c0", "type": "supports_claim"})
    return {"nodes": nodes, "edges": edges}


def test_reasoning_ratio_fix_does_not_advertise_the_dropped_contrast_pass(
    tmp_path: Path,
) -> None:
    """Below-floor is the path real graphs take; it must not sell a dead flag."""
    project = _scaffold(tmp_path, _ratio_graph(structural=99, reasoning=1))
    report = WikiLinter(project).run()

    matches = [f for f in report.findings if f.code == "REASONING_EDGE_RATIO"]
    assert len(matches) == 1
    fix = matches[0].suggested_fix or ""
    assert fix, "the below-floor branch must still suggest something actionable"
    assert "CONTRAST" not in fix.upper()
    assert "graph_write" in fix


def test_lint_suggested_fixes_never_name_an_unimplemented_env_flag() -> None:
    """Generic ratchet: every TESSERAE_* flag lint tells users to set must be read.

    Source-level on purpose. The defect this catches is a remediation string that
    outlives its feature, which no graph fixture can reproduce — the string is
    wrong even when the check that emits it is right.
    """
    lint_src = (_PKG / "lint.py").read_text(encoding="utf-8")
    advertised = set()
    for block in re.findall(r"suggested_fix=\((.*?)\n            \),", lint_src, re.S):
        # Only what reaches the user. A `#` comment may name a retired flag to
        # explain why it is gone; the remediation string may not.
        shown = "\n".join(
            line for line in block.splitlines() if not line.lstrip().startswith("#")
        )
        advertised |= set(_ENV_FLAG.findall(shown))

    readers = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(_PKG.rglob("*.py"))
    )
    # ``_env_truthy`` is the package's own env reader (project.py) and most
    # compile-pass flags are read through it and never through ``os.environ``
    # directly. Without it here the ratchet fails a remediation string that
    # names a flag which IS implemented — a false alarm that pressures the next
    # author to delete accurate guidance instead of fixing this regex.
    unread = sorted(
        flag
        for flag in advertised
        if not re.search(
            rf"(?:environ(?:\.get)?\(|getenv\(|_env_truthy\()\s*[\"']{flag}[\"']",
            readers,
        )
    )
    assert not unread, (
        f"lint suggests setting {unread}, but nothing in tesserae/ reads "
        f"them — setting the flag would silently do nothing"
    )
