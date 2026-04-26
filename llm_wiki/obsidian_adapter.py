"""Obsidian vault export for compiled LLM-Wiki graphs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict

from .markdown_projection import GraphMarkdownProjector
from .research_graph import ResearchGraph


@dataclass(frozen=True)
class ObsidianVaultAdapter:
    vault_name: str = "LLM-Wiki"

    def write_vault(self, graph: ResearchGraph, vault_path: str | Path) -> Dict[str, object]:
        vault = Path(vault_path)
        vault.mkdir(parents=True, exist_ok=True)
        written = GraphMarkdownProjector().write_projection(graph, vault)
        obsidian = vault / ".obsidian"
        obsidian.mkdir(parents=True, exist_ok=True)
        raw_assets = vault / "raw" / "assets"
        raw_assets.mkdir(parents=True, exist_ok=True)

        (obsidian / "app.json").write_text(json.dumps({"attachmentFolderPath": "raw/assets", "alwaysUpdateLinks": True}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (obsidian / "graph.json").write_text(json.dumps(default_graph_config(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (obsidian / "community-plugins.json").write_text(json.dumps(["dataview"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (obsidian / "core-plugins.json").write_text(json.dumps(["file-explorer", "global-search", "graph", "backlink", "outgoing-link", "tag-pane", "page-preview", "templates"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        meta = vault / "_meta"
        meta.mkdir(parents=True, exist_ok=True)
        (meta / "dashboard.md").write_text(render_dashboard(self.vault_name), encoding="utf-8")
        (vault / "README.md").write_text(render_readme(self.vault_name), encoding="utf-8")
        return {"vault_path": str(vault), "notes": len(written), "dashboard_path": str(meta / "dashboard.md")}


def default_graph_config() -> Dict[str, object]:
    return {
        "collapse-filter": False,
        "search": "",
        "showTags": True,
        "showAttachments": False,
        "hideUnresolved": False,
        "showOrphans": True,
        "colorGroups": [
            {"query": "path:papers", "color": {"a": 1, "rgb": 14701138}},
            {"query": "path:concepts", "color": {"a": 1, "rgb": 6737151}},
            {"query": "path:claims", "color": {"a": 1, "rgb": 16750745}},
        ],
    }


def render_readme(vault_name: str) -> str:
    return f"""# {vault_name}

This is a generated Obsidian vault for an LLM-Wiki project.

## Start here

- [[index]] — generated graph projection index
- [[_meta/dashboard|Dashboard]] — Dataview-oriented dashboard

## Notes

Markdown is a projection, not the source of truth. Re-run:

```bash
python3 -m llm_wiki.cli project compile
```

or:

```bash
python3 -m llm_wiki.cli project export-obsidian --vault /path/to/vault
```

Dataview is optional but recommended. The generated dashboard includes Dataview queries and still remains readable without the plugin.
"""


def render_dashboard(vault_name: str) -> str:
    return f"""# {vault_name} Dashboard

## Recent generated pages

```dataview
TABLE type, source_path
FROM "papers" OR "concepts" OR "claims"
SORT file.mtime DESC
LIMIT 25
```

## Papers

```dataview
TABLE source_path, analysis_date
FROM "papers"
SORT file.name ASC
```

## Concepts and claims

```dataview
TABLE type
FROM "concepts" OR "claims"
SORT type ASC, file.name ASC
```
"""
