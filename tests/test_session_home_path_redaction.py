"""No session producer may publish the operator's home directory.

Step 4 gave the Event pass a home-path redactor and left the other four
session producers untouched. Measured on the live graph (47,132 nodes) that
is 57 nodes still publishing an absolute ``/Users/<name>/`` path in their
NAME — 51 ``SessionTakeaway``, 4 ``Session``, 1 ``SessionDecision``, 1
``SessionTODO`` — and a node name is rendered into graph.json, the vault
markdown and the static site alike.

The rule is the one :mod:`tesserae.okf` §6.2 already applies to source paths:
which file was touched is the point, whose machine it was on is not. Each
test below drives the producer that actually mints the name, so it fails on
the unredacted code because the BEHAVIOUR is absent.
"""

from __future__ import annotations

from pathlib import Path
from typing import List

from tesserae.harness_sessions import HarnessSession, _title_and_preview
from tesserae.research_graph import ResearchGraph, ResearchNode, ResearchNodeType
from tesserae.session_graph_path_index import DocPathIndex
from tesserae.session_graph_structural import extract_structural


def _doc_graph() -> ResearchGraph:
    return ResearchGraph(nodes=[], edges=[])


def _session(project_root: Path, **kw) -> HarnessSession:
    base = dict(
        id="sess-home",
        slug="sess-home",
        harness="claude-code",
        agent_label="Claude Code",
        project_name="demo",
        project_root=str(Path(project_root).resolve()),
        started_at="2026-05-19T10:00:00Z",
        ended_at="2026-05-19T11:00:00Z",
    )
    base.update(kw)
    return HarnessSession(**base)


def _structural(session: HarnessSession, project_root: Path) -> ResearchGraph:
    idx = DocPathIndex.from_graph(_doc_graph(), project_root)
    return extract_structural(
        sessions=[session], path_index=idx, project_root=project_root
    )


def _names(graph: ResearchGraph, node_type: ResearchNodeType) -> List[str]:
    return [n.name for n in graph.nodes if n.type == node_type]


# ---------------------------------------------------------------------------
# Producer 1 — the Session node's own display name (4 live leaks)
# ---------------------------------------------------------------------------


def test_a_session_title_does_not_publish_the_operators_home_directory(tmp_path: Path):
    session = _session(tmp_path, title="fix /Users/rivka/Developer/proj/src/app.py")
    (name,) = _names(_structural(session, tmp_path), ResearchNodeType.SESSION)
    assert "/Users/rivka" not in name
    assert "~/Developer/proj/src/app.py" in name


def test_the_title_is_redacted_where_it_is_minted_not_only_where_it_is_shown():
    """``session.title`` is also the summary, the ``redacted_preview`` and the
    subagent descriptor title — redacting it at one display site would leave
    the other three."""
    title, preview = _title_and_preview(
        ["rerun /home/dana/work/suite.sh after the merge", "second message"]
    )
    assert "/home/dana" not in title
    assert "~/work/suite.sh" in title
    assert "/home/dana" not in preview


# ---------------------------------------------------------------------------
# Producer 2 — the typed-subagent run (51 live leaks, the largest producer)
# ---------------------------------------------------------------------------


def test_a_subagent_run_name_does_not_publish_the_operators_home_directory(
    tmp_path: Path,
):
    session = _session(
        tmp_path,
        metadata={
            "subagents": [
                {
                    "id": "sub-1",
                    "type": "code-reviewer",
                    "title": "review /Users/rivka/Developer/proj/tesserae/verify.py",
                }
            ]
        },
    )
    graph = _structural(session, tmp_path)
    names = _names(graph, ResearchNodeType.SESSION_TAKEAWAY)
    assert names, "the typed subagent run must still mint a takeaway"
    assert not any("/Users/rivka" in n for n in names)
    assert any("~/Developer/proj/tesserae/verify.py" in n for n in names)


def test_a_subagent_run_id_is_a_function_of_the_subagent_not_of_its_name(
    tmp_path: Path,
):
    """The seed is ``session:{id}:subagent:{sub}:run`` — text-free, which is
    why redacting 51 SessionTakeaway names is a name rewrite that keeps every
    edge rather than graph churn.

    Pinned by feeding two DIFFERENT titles, not two spellings of one: comparing
    a leaky title against its already-redacted twin proves nothing, because
    redaction collapses them before the seed is built either way. A seed that
    hashed the title would survive that comparison and fail this one."""
    def _takeaway_ids(title: str) -> set:
        session = _session(
            tmp_path,
            metadata={
                "subagents": [
                    {"id": "sub-1", "type": "code-reviewer", "title": title}
                ]
            },
        )
        return {
            n.id
            for n in _structural(session, tmp_path).nodes
            if n.type == ResearchNodeType.SESSION_TAKEAWAY
        }

    first = _takeaway_ids("review the retrieval planner")
    second = _takeaway_ids("audit the federation whitelist instead")
    assert first and first == second


# ---------------------------------------------------------------------------
# Producer 3 — structural decisions
# ---------------------------------------------------------------------------


def test_a_structural_decision_does_not_publish_the_operators_home_directory(
    tmp_path: Path,
):
    session = _session(
        tmp_path, decisions=["pin the fixture day in /Users/rivka/proj/tests/conftest.py"]
    )
    names = _names(_structural(session, tmp_path), ResearchNodeType.SESSION_DECISION)
    assert names
    assert not any("/Users/rivka" in n for n in names)
    assert any("~/proj/tests/conftest.py" in n for n in names)


# ---------------------------------------------------------------------------
# Producer 4 — LLM finding bodies (the 2 nodes that DO churn)
# ---------------------------------------------------------------------------


def test_an_llm_finding_body_does_not_publish_the_operators_home_directory(
    tmp_path: Path,
):
    from tesserae.session_graph import SessionGraphExtractor

    root = tmp_path / "project"
    root.mkdir(parents=True, exist_ok=True)
    cache_dir = root / ".tesserae" / "session_findings"
    cache_dir.mkdir(parents=True, exist_ok=True)

    class _Client:
        def complete_json(self, **kwargs):
            return {
                "findings": [
                    {
                        "kind": "todo",
                        "body": "delete the stale venv at /Users/rivka/proj/.venv",
                        "turn_ids": [0],
                        "references": [],
                    }
                ]
            }

    session = _session(
        root, metadata={"turns": [{"role": "user", "text": "clean up the tree"}]}
    )
    graph = SessionGraphExtractor(
        project_root=root.resolve(),
        cache_dir=cache_dir,
        doc_graph=_doc_graph(),
        sessions=[session],
        json_client=_Client(),
    ).extract()

    names = _names(graph, ResearchNodeType.SESSION_TODO)
    assert names, "the finding must still be minted"
    assert not any("/Users/rivka" in n for n in names)
    assert any("~/proj/.venv" in n for n in names)


def test_a_finding_id_is_seeded_from_the_redacted_body_not_the_raw_one(tmp_path: Path):
    """Exempting the id seed would leave the home path inside the hash input,
    so the id would depend on WHOSE machine compiled — the machine-dependence
    the redactor exists to prevent. Same finding, two operators, one id.

    Asserted through the real mint, not through the helper: a test that only
    checks ``redact_home_paths`` collapses two strings is true of the helper
    whatever the mint site does with it."""
    from tesserae.session_graph import SessionGraphExtractor

    def _mint(body: str) -> str:
        root = tmp_path / f"p{abs(hash(body)) % 10_000}"
        cache_dir = root / ".tesserae" / "session_findings"
        cache_dir.mkdir(parents=True, exist_ok=True)

        class _Client:
            def complete_json(self, **kwargs):
                return {
                    "findings": [
                        {"kind": "todo", "body": body, "turn_ids": [0], "references": []}
                    ]
                }

        session = _session(
            root, metadata={"turns": [{"role": "user", "text": "clean up"}]}
        )
        graph = SessionGraphExtractor(
            project_root=root.resolve(),
            cache_dir=cache_dir,
            doc_graph=_doc_graph(),
            sessions=[session],
            json_client=_Client(),
        ).extract()
        (node,) = [
            n for n in graph.nodes if n.type == ResearchNodeType.SESSION_TODO
        ]
        return node.id

    rivka = _mint("delete the stale venv at /Users/rivka/proj/.venv")
    dana = _mint("delete the stale venv at /home/dana/proj/.venv")
    assert rivka == dana
