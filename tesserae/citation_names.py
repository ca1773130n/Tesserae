"""Rewrite ``[node_id]`` citation markers to human-readable names.

The synthesis paths instruct the model to cite sources with the raw node id.
This turns those ids into the display name already carried alongside each
source, so agents read names instead of ``node-<hash>``. Unknown ids are left
verbatim so a stray citation never crashes rendering."""
from __future__ import annotations

import re
from typing import Mapping

# Same shape query.py has used for its citation check. Single source of truth.
NODE_CITATION_RE = re.compile(r"\[([a-zA-Z0-9_\-:./]{3,})\]")


def rewrite_citations(body: str, id_to_name: Mapping[str, str]) -> str:
    if not body or not id_to_name:
        return body

    def _sub(match: "re.Match[str]") -> str:
        node_id = match.group(1)
        name = id_to_name.get(node_id)
        return f"[{name}]" if name else match.group(0)

    return NODE_CITATION_RE.sub(_sub, body)
