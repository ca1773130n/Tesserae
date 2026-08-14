"""The CHARTER re-scope's retirements stay visible where they were promised.

The 2026-08-14 re-scope retires ``render_brief``, the altitude quota/floor
table, ``altitude_lift`` and CH-02..CH-05 unbuilt. Two documents were written
against that machinery before it was retired: shipped steps of the
cognitive-memory roadmap, and an in-source decision record in ``charter.py``.
Retiring machinery obliges edits to both, because a shipped step that still
tells a reader to build against a retired surface reads as a live instruction —
which is how a retired spec gets cited as evidence six months later.

These are guards against the notes being dropped, not tests of behaviour. They
assert placement and absence, never wording, so a rewrite of a note passes and
a deletion of one fails.
"""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPECS = ROOT / "docs" / "superpowers" / "specs"
RESCOPE = "2026-08-14-charter-rescope-roadmap.md"

# Names of machinery the re-scope retires unbuilt. A roadmap step that mentions
# one is reasoning about something that will never exist.
RETIRED = ("render_brief", "altitude_lift", "CH-04", "altitude quotas",
           "carry_quota", "support_floor")


def _numbered_steps(text: str) -> dict[str, str]:
    """Split a roadmap into its numbered ``### N.`` step sections.

    Non-numbered ``###`` headings (the source-mechanism appendices) are
    excluded: they survey outside papers rather than instruct this repo.
    """
    sections: dict[str, str] = {}
    parts = re.split(r"^### ", text, flags=re.MULTILINE)
    for part in parts[1:]:
        heading = part.split("\n", 1)[0].strip()
        if re.match(r"^\d+\.", heading):
            sections[heading] = part
    return sections


def test_roadmap_steps_citing_retired_charter_machinery_point_at_the_rescope() -> None:
    """No shipped step names retired machinery without naming its retirement."""
    text = (SPECS / "2026-08-08-cognitive-memory-roadmap.md").read_text(encoding="utf-8")
    steps = _numbered_steps(text)
    assert len(steps) == 12, f"expected 12 numbered steps, found {sorted(steps)}"

    unannotated = [
        heading
        for heading, body in steps.items()
        if any(name in body for name in RETIRED) and RESCOPE not in body
    ]
    assert not unannotated, (
        "these steps reason about machinery the CHARTER re-scope retired, with "
        f"no pointer to {RESCOPE}: {unannotated}"
    )


def test_charter_module_makes_no_commitment_about_unwritten_code() -> None:
    """charter.py records why CH-04 was not written, not how it would work.

    The record shrank when CH-04 was retired. What must not come back is a
    design commitment about code nobody is going to write: that is the shape
    that gets read years later as a plan in progress.
    """
    source = (ROOT / "tesserae" / "charter.py").read_text(encoding="utf-8")
    assert RESCOPE in source, "charter.py must point a reader at the re-scope"
    for phrase in ("THE DECISION", "DESIGN COMMITMENT"):
        assert phrase not in source, (
            f"{phrase!r} is back in charter.py — CH-04 has no subject to decide "
            "about; see the re-scope's 'Deliberately not building'"
        )
