"""Render/parse/slice the human-curatable extraction-guidance markdown.

Headings carry routing (## Extractor: / ### Node Type:); HTML comments carry
machine identity (cluster hash, source, field, event count) without hurting
readability. Users may delete bullets; deletions survive because parse only
reads what's present.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Set

_SCHEMA_LINE = "<!-- tesserae-guidance-schema: 1 -->"
_CLUSTER_RE = re.compile(
    r"<!--\s*cluster:\s*(?P<hash>sha256:[0-9a-f]+)\s+source=(?P<source>\S+)\s+"
    r"field=(?P<field>\S+)\s+events=(?P<events>\d+)\s*-->"
)


@dataclass
class GuidanceBullet:
    extractor: str
    node_type: str
    cluster_hash: str
    source: str
    field: str
    events: int
    text: str


def render_guidance(bullets: List[GuidanceBullet]) -> str:
    lines = ["# Tesserae Extraction Guidance", "", _SCHEMA_LINE, ""]
    by_ext: dict = {}
    for b in bullets:
        by_ext.setdefault(b.extractor, {}).setdefault(b.node_type, []).append(b)
    for ext in sorted(by_ext):
        lines.append(f"## Extractor: {ext}")
        lines.append("")
        for nt in sorted(by_ext[ext]):
            lines.append(f"### Node Type: {nt}")
            lines.append("")
            for b in by_ext[ext][nt]:
                lines.append(
                    f"<!-- cluster: {b.cluster_hash} source={b.source} "
                    f"field={b.field} events={b.events} -->"
                )
                lines.append(f"- {b.text}")
                lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def parse_guidance(md: str) -> List[GuidanceBullet]:
    bullets: List[GuidanceBullet] = []
    ext = nt = None
    pending = None
    for line in md.splitlines():
        s = line.strip()
        if s.startswith("## Extractor:"):
            ext = s[len("## Extractor:"):].strip(); nt = None
        elif s.startswith("### Node Type:"):
            nt = s[len("### Node Type:"):].strip()
        elif (m := _CLUSTER_RE.search(s)):
            pending = m
        elif s.startswith("- ") and ext and nt:
            text = s[2:].strip()
            if pending:
                bullets.append(GuidanceBullet(
                    extractor=ext, node_type=nt, cluster_hash=pending["hash"],
                    source=pending["source"], field=pending["field"],
                    events=int(pending["events"]), text=text))
            else:
                bullets.append(GuidanceBullet(
                    extractor=ext, node_type=nt, cluster_hash="",
                    source="", field="", events=0, text=text))
            pending = None
    return bullets


def slice_guidance(bullets: List[GuidanceBullet], *, extractor: str,
                   node_types: Set[str]) -> List[GuidanceBullet]:
    return [b for b in bullets if b.extractor == extractor and b.node_type in node_types]
