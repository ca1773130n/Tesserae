"""LLM-backed extraction bridge for LLM-Wiki research graphs.

The deterministic extractor remains the guardrail baseline. This module lets a
CLI/OAuth LLM such as Claude produce candidate graph JSON, then validates and
normalizes it through the same controlled ontology before anything is stored.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Optional, Sequence

from .research_graph import (
    ALLOWED_EDGE_TYPES,
    ALLOWED_NODE_TYPES,
    ResearchEdge,
    ResearchGraph,
    ResearchGraphBuilder,
    ResearchNode,
    ResearchNodeType,
    extract_source_metadata,
    extract_title,
    filter_filename_shaped_concepts,
    source_kind_to_node_type,
    source_path_looks_like_i18n_duplicate,
)


class GraphJSONValidationError(ValueError):
    """Raised when LLM-produced graph JSON violates the controlled schema."""


ClaudeRunner = Callable[[str, str, str, int], str]


def graph_from_llm_payload(payload: Mapping[str, object], source_path: Optional[str] = None, source_kind: str = "SourceDocument") -> ResearchGraph:
    """Validate LLM JSON and convert it into a normalized ResearchGraph.

    Expected input shape:

    ```json
    {
      "nodes": [{"key": "paper", "name": "...", "type": "Paper"}],
      "edges": [{"source": "paper", "target": "method", "type": "uses"}]
    }
    ```

    `key` is a local LLM reference. It is not trusted as the stable node ID; the
    builder creates canonical stable IDs from controlled type + display name.
    """
    if not isinstance(payload.get("nodes"), list):
        raise GraphJSONValidationError("Payload must contain a nodes list")
    if not isinstance(payload.get("edges", []), list):
        raise GraphJSONValidationError("Payload edges must be a list")

    builder = ResearchGraphBuilder()
    key_to_node: Dict[str, ResearchNode] = {}
    name_to_node: Dict[str, ResearchNode] = {}

    for raw_node in payload["nodes"]:  # type: ignore[index]
        if not isinstance(raw_node, Mapping):
            raise GraphJSONValidationError("Every node must be an object")
        name = str(raw_node.get("name", "")).strip()
        type_name = str(raw_node.get("type", "")).strip()
        if not name:
            raise GraphJSONValidationError("Every node must have a non-empty name")
        if type_name not in ALLOWED_NODE_TYPES:
            raise GraphJSONValidationError(f"Unsupported node type: {type_name}")
        aliases = raw_node.get("aliases", [])
        if aliases is None:
            aliases = []
        if not isinstance(aliases, list) or not all(isinstance(alias, str) for alias in aliases):
            raise GraphJSONValidationError(f"Node aliases must be a list of strings: {name}")
        metadata = raw_node.get("metadata", {})
        if metadata is None:
            metadata = {}
        if not isinstance(metadata, dict):
            raise GraphJSONValidationError(f"Node metadata must be an object: {name}")
        node_type = ResearchNodeType(type_name)
        node_metadata = dict(metadata)
        if node_type == source_kind_to_node_type(source_kind, source_path):
            node_metadata = {"source_kind": source_kind, **extract_source_metadata("", source_path), **node_metadata}
        node = builder.add_node(
            name=name,
            node_type=node_type,
            aliases=aliases,
            description=str(raw_node.get("description", "") or ""),
            source_path=str(raw_node.get("source_path") or source_path or "") or None,
            metadata=node_metadata,
        )
        key = str(raw_node.get("key") or raw_node.get("id") or name)
        key_to_node[key] = node
        name_to_node[name] = node

    if not any(node.type == source_kind_to_node_type(source_kind, source_path) for node in key_to_node.values()):
        title = Path(source_path).stem if source_path else "Untitled Source"
        source = builder.add_node(title, source_kind_to_node_type(source_kind, source_path), source_path=source_path, metadata={"source_kind": source_kind, **extract_source_metadata("", source_path)})
        key_to_node["source"] = source
        name_to_node[source.name] = source

    for raw_edge in payload.get("edges", []):
        if not isinstance(raw_edge, Mapping):
            raise GraphJSONValidationError("Every edge must be an object")
        edge_type = str(raw_edge.get("type", "")).strip()
        if edge_type not in ALLOWED_EDGE_TYPES:
            raise GraphJSONValidationError(f"Unsupported edge type: {edge_type}")
        source_ref = str(raw_edge.get("source", "")).strip()
        target_ref = str(raw_edge.get("target", "")).strip()
        source = key_to_node.get(source_ref) or name_to_node.get(source_ref)
        target = key_to_node.get(target_ref) or name_to_node.get(target_ref)
        if source is None or target is None:
            raise GraphJSONValidationError(f"Edge references unknown nodes: {source_ref} -> {target_ref}")
        metadata = raw_edge.get("metadata", {})
        if metadata is None:
            metadata = {}
        if not isinstance(metadata, dict):
            raise GraphJSONValidationError(f"Edge metadata must be an object: {source_ref} -> {target_ref}")
        builder.add_edge(source, edge_type, target, evidence=str(raw_edge.get("evidence") or "") or None, metadata=dict(metadata))

    graph = builder.build()
    # Bug A: the LLM occasionally returns ``Concept``-typed nodes whose
    # names are literally filenames (``feature-map.md``, ``pyproject.toml``).
    # They duplicate the ``SourceDocument`` nodes that already represent
    # the same files with proper titles, so we strip them here before
    # downstream validation/persistence.
    graph = filter_filename_shaped_concepts(graph)
    validate_research_graph(graph)
    return graph


def validate_research_graph(graph: ResearchGraph) -> None:
    node_ids = {node.id for node in graph.nodes}
    for node in graph.nodes:
        if node.type.value not in ALLOWED_NODE_TYPES:
            raise GraphJSONValidationError(f"Unsupported node type: {node.type}")
    for edge in graph.edges:
        if edge.type not in ALLOWED_EDGE_TYPES:
            raise GraphJSONValidationError(f"Unsupported edge type: {edge.type}")
        if edge.source not in node_ids or edge.target not in node_ids:
            raise GraphJSONValidationError(f"Edge references missing node: {edge.source} -> {edge.target}")


def extract_json_object(text: str) -> Dict[str, object]:
    """Extract the final JSON object from raw Claude/Codex CLI output."""
    stripped = text.strip()
    parsed = _try_json_loads(stripped)
    if isinstance(parsed, dict):
        if isinstance(parsed.get("result"), str):
            return extract_json_object(str(parsed["result"]))
        return parsed

    if stripped.startswith("```"):
        stripped = _strip_markdown_fence(stripped)
        parsed = _try_json_loads(stripped)
        if isinstance(parsed, dict):
            return parsed

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise GraphJSONValidationError("No JSON object found in LLM output")
    candidate = _strip_markdown_fence(stripped[start : end + 1])
    parsed = _try_json_loads(candidate)
    if not isinstance(parsed, dict):
        raise GraphJSONValidationError("LLM output JSON is not an object")
    if isinstance(parsed.get("result"), str):
        return extract_json_object(str(parsed["result"]))
    return parsed


def _try_json_loads(text: str) -> object:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _strip_markdown_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return stripped


class ClaudeCLIResearchExtractor:
    """Extract ResearchGraph JSON with Claude CLI OAuth, then validate it."""

    def __init__(
        self,
        runner: Optional[ClaudeRunner] = None,
        config_dirs: Optional[Sequence[str]] = None,
        model: str = "sonnet",
        timeout: int = 180,
    ) -> None:
        self.runner = runner or run_claude_cli
        self.config_dirs = list(config_dirs or ["/Users/neo/.claude-personal1", "/Users/neo/.claude-personal2"])
        self.model = model
        self.timeout = timeout

    def extract_file(self, path: str | Path, source_kind: str = "SourceDocument") -> ResearchGraph:
        file_path = Path(path)
        return self.extract_text(file_path.read_text(encoding="utf-8", errors="replace"), str(file_path), source_kind)

    def extract_text(self, text: str, source_path: Optional[str] = None, source_kind: str = "SourceDocument") -> ResearchGraph:
        # Belt-and-suspenders: skip localized i18n duplicates at the extractor
        # level so we don't spend LLM tokens producing concepts that the
        # post-merge filter would just drop. The canonical English source
        # has already produced (or will produce) the same concepts.
        if source_path_looks_like_i18n_duplicate(source_path):
            return ResearchGraph(nodes=[], edges=[])
        prompt = build_research_extraction_prompt(text=text, source_path=source_path, source_kind=source_kind)
        last_error: Optional[Exception] = None
        for config_dir in self.config_dirs:
            try:
                raw = self.runner(prompt, config_dir, self.model, self.timeout)
                payload = extract_json_object(raw)
                graph = graph_from_llm_payload(payload, source_path=source_path, source_kind=source_kind)
                ensure_source_metadata(graph, text, source_path, source_kind)
                return graph
            except Exception as exc:  # Try fallback auth dirs for CLI/auth/config failures and malformed output.
                last_error = exc
        raise GraphJSONValidationError(f"Claude CLI extraction failed: {last_error}")


def build_research_extraction_prompt(text: str, source_path: Optional[str], source_kind: str) -> str:
    title = extract_title(text, source_path)
    return f"""You are extracting a typed research intelligence graph for LLM-Wiki.

Return ONLY one valid JSON object. No markdown fences, no commentary.

Schema:
{{
  "nodes": [
    {{"key": "local-reference", "name": "display name", "type": "one allowed node type", "aliases": [], "description": "", "metadata": {{}}}}
  ],
  "edges": [
    {{"source": "node key or name", "target": "node key or name", "type": "one allowed edge type", "evidence": "exact source sentence/span", "metadata": {{}}}}
  ]
}}

Allowed node types:
{json.dumps(sorted(ALLOWED_NODE_TYPES), ensure_ascii=False)}

Allowed edge types:
{json.dumps(sorted(ALLOWED_EDGE_TYPES), ensure_ascii=False)}

Forbidden node/edge labels: Entity, software, technique, domain, topic, technology, feature, related_to.
Map them to controlled research types instead.

Extraction policy:
- Include exactly one source artifact node for this document when possible.
- Source kind: {source_kind}
- Preferred source title: {title}
- Extract reusable research concepts, methods, math concepts, datasets, benchmarks, metrics, tasks, approach families, claims, and evidence spans.
- Every factual claim node should connect to an EvidenceSpan via evidenced_by.
- Use exact source text as evidence where possible.
- Do not invent claims that are not supported by the document.

Source path: {source_path or ''}

Document:
{text}
"""


def run_claude_cli(prompt: str, config_dir: str, model: str, timeout: int) -> str:
    env = os.environ.copy()
    env["CLAUDE_CONFIG_DIR"] = config_dir
    cmd = ["claude", "-p", "--output-format", "text", "--max-turns", "1"]
    if model:
        cmd.extend(["--model", model])
    proc = subprocess.run(cmd, input=prompt, text=True, capture_output=True, env=env, timeout=timeout, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"claude exited {proc.returncode}: {proc.stderr.strip() or proc.stdout.strip()}")
    return proc.stdout


def ensure_source_metadata(graph: ResearchGraph, text: str, source_path: Optional[str], source_kind: str) -> None:
    """Best-effort metadata backfill for source artifact nodes.

    Nodes are frozen dataclasses, so this mutates only the metadata dictionaries
    they own; the graph topology and IDs remain unchanged.
    """
    source_type = source_kind_to_node_type(source_kind, source_path)
    metadata = {"source_kind": source_kind, **extract_source_metadata(text, source_path)}
    for node in graph.nodes:
        if node.type == source_type:
            node.metadata.update({key: value for key, value in metadata.items() if key not in node.metadata})
            if source_path and not node.source_path:
                # Frozen dataclass prevents assigning source_path; graph_from_llm_payload
                # already sets source_path for all LLM nodes, so this is only defensive.
                pass
