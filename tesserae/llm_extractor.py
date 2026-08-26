"""LLM-backed extraction bridge for Tesserae research graphs.

The deterministic extractor remains the guardrail baseline. This module lets a
CLI/OAuth LLM such as Claude produce candidate graph JSON, then validates and
normalizes it through the same controlled ontology before anything is stored.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Optional, Sequence

from .llm_json import _note_failure, last_failure_kind
from .research_graph import (
    ALLOWED_EDGE_TYPES,
    EXTRACTABLE_EDGE_TYPES,
    ALLOWED_NODE_TYPES,
    EXTRACTABLE_NODE_TYPES,
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


class ProviderUnavailableError(RuntimeError):
    """Raised when the LLM backend never produced output to validate.

    Deliberately NOT a subclass of :class:`GraphJSONValidationError`: "the
    provider dropped the stream" and "the model emitted an out-of-vocab type"
    demand opposite responses — wait and re-run vs. fix the prompt/schema —
    and for three compiles they were reported as the same thing, which read as
    the *model* having 8x worse schema compliance during what was actually a
    provider capacity window. Safe as a sibling rather than a subclass:
    :class:`LLMResearchExtractor` is only ever wrapped by
    ``SelectiveClaudeResearchExtractor``, which catches bare ``Exception``.
    """


class ProviderAuthError(RuntimeError):
    """Raised when every configured account refused the credentials.

    A sibling of :class:`ProviderUnavailableError` for the same reason
    :class:`ExtractionTimeoutError` is one: the per-document line prints the
    class name first, and "unavailable (transport/capacity)" sends the operator
    to wait out a window that will never close — waiting does not refresh an
    expired OAuth session. Before this class existed, a 137-doc compile against
    an expired session printed 136 lines naming the wrong remedy and one, long
    scrolled away in the once-per-process login warning, naming the right one.
    Every failed document now raises THIS, carrying ``claude /login`` /
    ``codex login`` in its own message, which is what the selective router
    prints per doc. How loudly the JSON clients ALSO log it still differs —
    ``ClaudeCLIJsonClient`` de-duplicates its static hint to once per process,
    ``CodexCLIJsonClient`` logs one line per call — but that is log volume, not
    the diagnosis: the diagnosis rides on this exception either way.
    """


class ExtractionTimeoutError(RuntimeError):
    """Raised when nothing came back because the ATTEMPT ran out of time.

    A sibling of :class:`ProviderUnavailableError`, not a subclass, for the
    same reason that one is a sibling of :class:`GraphJSONValidationError`: the
    operator-facing line prints the class name first, and "unavailable" would
    send them to wait out a capacity window that doesn't exist. What the bound
    establishes is exactly one thing — this document did not finish inside
    ``TESSERAE_EXTRACT_TIMEOUT``. It does NOT establish that the provider took
    the request: the timeout is raised on a killed child process, and a
    DNS/connect stall raises the same one as a slow generation. So both
    remedies ship: raise the bound (``0`` = no bound) or split the document if
    it is large; check the provider is reachable from this host if it is not.
    """


# A single bad generation (out-of-vocab type, truncated JSON) is transient — the
# model is non-deterministic, so re-calling almost always validates. Retry the
# generation this many times before falling back. ponytail: small constant, not a
# config knob — nobody tunes it, and the compile already degrades gracefully.
_VALIDATION_RETRIES = 2


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
    dropped_nodes = 0

    for raw_node in payload["nodes"]:  # type: ignore[index]
        if not isinstance(raw_node, Mapping):
            raise GraphJSONValidationError("Every node must be an object")
        name = str(raw_node.get("name", "")).strip()
        type_name = str(raw_node.get("type", "")).strip()
        if not name:
            raise GraphJSONValidationError("Every node must have a non-empty name")
        if type_name not in EXTRACTABLE_NODE_TYPES:
            # Symmetric with the unknown-edge-type drop below, and for the same
            # reason: one bad entry must not abort a whole multi-doc compile.
            # It matters more here than it looks, because the vocabulary just
            # shrank — code types are no longer extractable, and 1,387 of the
            # 4,368 entries already in the response cache name one. Raising
            # would re-ask the LLM for every one of them; dropping keeps the
            # cache warm and salvages the rest of the payload. Edges pointing
            # at a dropped node fall into the "node the model never defined"
            # branch and are counted there.
            dropped_nodes += 1
            continue
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

    dropped_edges = 0
    for raw_edge in payload.get("edges", []):
        if not isinstance(raw_edge, Mapping):
            raise GraphJSONValidationError("Every edge must be an object")
        edge_type = str(raw_edge.get("type", "")).strip()
        if edge_type not in EXTRACTABLE_EDGE_TYPES:
            # ponytail: the LLM occasionally hallucinates an edge type outside the
            # 55-type vocab (e.g. 'used_by', the inverse of 'uses'). Skip the bad
            # edge — one hallucination must not abort a whole multi-doc compile.
            #
            # EXTRACTABLE, not ALLOWED: the causal layer is producer-owned and a
            # model reading a document may not assert one, even though the type
            # is real and the graph will happily store it. The prompt below never
            # offers these types; this is the enforcement, because a prompt is a
            # request and a filter is a rule.
            dropped_edges += 1
            continue
        source_ref = str(raw_edge.get("source", "")).strip()
        target_ref = str(raw_edge.get("target", "")).strip()
        source = key_to_node.get(source_ref) or name_to_node.get(source_ref)
        target = key_to_node.get(target_ref) or name_to_node.get(target_ref)
        if source is None or target is None:
            dropped_edges += 1  # edge points at a node the model never defined
            continue
        metadata = raw_edge.get("metadata", {})
        if metadata is None:
            metadata = {}
        if not isinstance(metadata, dict):
            raise GraphJSONValidationError(f"Edge metadata must be an object: {source_ref} -> {target_ref}")
        builder.add_edge(source, edge_type, target, evidence=str(raw_edge.get("evidence") or "") or None, metadata=dict(metadata))

    if dropped_nodes:  # non-silent: name what we discarded
        print(f"  extract: dropped {dropped_nodes} node(s) with a non-extractable type "
              f"from {source_path or 'payload'}", file=sys.stderr)
    if dropped_edges:  # non-silent: name what we discarded
        print(f"  extract: dropped {dropped_edges} edge(s) with unknown type/endpoints "
              f"from {source_path or 'payload'}", file=sys.stderr)

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
        guidance: str = "",
    ) -> None:
        self.runner = runner or run_claude_cli
        # Default extraction-feedback guidance injected into every prompt
        # unless an explicit ``guidance=`` is passed to extract_text/extract_file.
        # Empty by default so the off-flag path stays byte-identical.
        self.guidance = guidance
        # Mirror ClaudeCLIJsonClient resolution order: explicit arg →
        # CLAUDE_CONFIG_DIR env → auto-discover ~/.claude* dirs →
        # final fallback to [~/.claude]. The fallback loop in
        # extract_text tries each in order on auth failure.
        if config_dirs:
            self.config_dirs = list(config_dirs)
        elif os.environ.get("CLAUDE_CONFIG_DIR"):
            self.config_dirs = [os.environ["CLAUDE_CONFIG_DIR"]]
        else:
            home = Path.home()
            discovered = sorted(
                str(p)
                for p in home.glob(".claude*")
                if p.is_dir() and not p.name.endswith((".bak", ".old"))
            )
            self.config_dirs = discovered or [str(home / ".claude")]
        self.model = model
        self.timeout = timeout

    def extract_file(self, path: str | Path, source_kind: str = "SourceDocument", guidance: Optional[str] = None) -> ResearchGraph:
        file_path = Path(path)
        return self.extract_text(file_path.read_text(encoding="utf-8", errors="replace"), str(file_path), source_kind, guidance=guidance)

    def extract_text(self, text: str, source_path: Optional[str] = None, source_kind: str = "SourceDocument", guidance: Optional[str] = None) -> ResearchGraph:
        """Extract one document, splitting it first when it is large.

        A document that fits in one piece goes straight to ``_extract_once`` —
        same prompt, same cache key, same bytes as before chunking existed.
        """
        if guidance is None:
            guidance = self.guidance
        if source_path_looks_like_i18n_duplicate(source_path):
            return ResearchGraph(nodes=[], edges=[])
        return extract_in_chunks(
            text, source_path, source_kind, guidance, self._extract_once,
        )

    def _extract_once(self, text: str, source_path: Optional[str] = None, source_kind: str = "SourceDocument", guidance: Optional[str] = None) -> ResearchGraph:
        # ``guidance=None`` (the default) falls back to the instance-level
        # ``self.guidance`` set at construction; an explicit string (incl. "")
        # overrides it. This lets the compile path inject sliced guidance via
        # the constructor while keeping the explicit-arg call sites unchanged.
        if guidance is None:
            guidance = self.guidance
        # Belt-and-suspenders: skip localized i18n duplicates at the extractor
        # level so we don't spend LLM tokens producing concepts that the
        # post-merge filter would just drop. The canonical English source
        # has already produced (or will produce) the same concepts.
        if source_path_looks_like_i18n_duplicate(source_path):
            return ResearchGraph(nodes=[], edges=[])
        prompt = build_research_extraction_prompt(text=text, source_path=source_path, source_kind=source_kind, guidance=guidance)
        last_error: Optional[Exception] = None
        for config_dir in self.config_dirs:
            # ponytail: the model is non-deterministic, so a transient bad
            # generation (a node/edge type outside the vocab, truncated JSON)
            # usually validates on a re-call. Retry GraphJSONValidationError a
            # couple times before giving up on this dir; CLI/auth/config errors
            # fall through to the next config dir instead.
            for attempt in range(_VALIDATION_RETRIES + 1):
                try:
                    raw = self.runner(prompt, config_dir, self.model, self.timeout)
                    payload = extract_json_object(raw)
                    graph = graph_from_llm_payload(payload, source_path=source_path, source_kind=source_kind)
                    ensure_source_metadata(graph, text, source_path, source_kind)
                    return graph
                except GraphJSONValidationError as exc:
                    last_error = exc
                    if attempt < _VALIDATION_RETRIES:
                        print(f"  extract: invalid generation for {source_path or 'doc'} "
                              f"({exc}); retrying ({attempt + 1}/{_VALIDATION_RETRIES})", file=sys.stderr)
                    continue
                except Exception as exc:  # CLI/auth/config failure -> next config dir
                    last_error = exc
                    break
        raise GraphJSONValidationError(f"Claude CLI extraction failed: {last_error}")


class LLMResearchExtractor:
    """Provider-agnostic concept/claim extractor.

    Drives the *configured* LLM backend (codex / claude / anthropic) through the
    shared :class:`tesserae.llm_json.LLMJsonClient` — the SAME path session
    extraction uses — instead of shelling out to one hardcoded CLI. The client
    owns provider selection, OAuth/account rotation, content-keyed caching
    (``cache_key``) and retries, so this extractor is a thin prompt->validate
    shim. This extractor sets no timeout of its own; the bound lives on the
    client, per ATTEMPT, from ``cli._extract_timeout`` (``TESSERAE_EXTRACT_TIMEOUT``,
    default 1800s, ``0`` = run to completion). A doc that exhausts every
    configured profile lands in the deterministic fallback for THAT doc — the
    old ``--claude-timeout`` footgun is still gone; what replaced it bounds an
    attempt rather than the document."""

    def __init__(self, client: object, *, guidance: str = "") -> None:
        self.client = client
        self.guidance = guidance

    def extract_file(self, path: "str | Path", source_kind: str = "SourceDocument", guidance: Optional[str] = None) -> ResearchGraph:
        file_path = Path(path)
        return self.extract_text(
            file_path.read_text(encoding="utf-8", errors="replace"),
            str(file_path), source_kind, guidance=guidance,
        )

    def extract_text(self, text: str, source_path: Optional[str] = None, source_kind: str = "SourceDocument", guidance: Optional[str] = None) -> ResearchGraph:
        """Extract one document, splitting it first when it is large.

        A document that fits in one piece goes straight to ``_extract_once`` —
        same prompt, same cache key, same bytes as before chunking existed.
        """
        if guidance is None:
            guidance = self.guidance
        if source_path_looks_like_i18n_duplicate(source_path):
            return ResearchGraph(nodes=[], edges=[])
        return extract_in_chunks(
            text, source_path, source_kind, guidance, self._extract_once,
        )

    def _extract_once(self, text: str, source_path: Optional[str] = None, source_kind: str = "SourceDocument", guidance: Optional[str] = None) -> ResearchGraph:
        if guidance is None:
            guidance = self.guidance
        if source_path_looks_like_i18n_duplicate(source_path):
            return ResearchGraph(nodes=[], edges=[])
        prompt = build_research_extraction_prompt(text=text, source_path=source_path, source_kind=source_kind, guidance=guidance)
        # One binding for the system message: `complete_json` and the
        # `forget_cached_answer` below must address the SAME cache entry, and
        # that address is now a digest of the assembled system+user prompt.
        system = "You extract a typed research-intelligence graph as ONE JSON object (nodes + edges)."
        # Content-keyed cache: identical (doc, kind, guidance) reuses the prior
        # extraction -> cheap re-compiles + stable output, via the client cache.
        # The client hashes the prompt itself now, so this digest is belt-and-
        # braces rather than the sole guard against two documents colliding.
        cache_key = hashlib.sha256(
            ("research-graph-v1\n" + (guidance or "") + "\n" + (source_kind or "") + "\n"
             + (source_path or "") + "\n" + text).encode("utf-8")
        ).hexdigest()
        # Every client we build clears its own verdict on entry, but a
        # duck-typed client that never writes one would otherwise let the
        # PREVIOUS call's verdict misattribute this doc. Clear once here so
        # "unset" really means unset — and unset falls through to the
        # pre-existing GraphJSONValidationError behaviour.
        _note_failure(None)
        # Parity with ClaudeCLIResearchExtractor: a bad generation is transient
        # (the model is non-deterministic), so re-ask.
        last_error: Optional[Exception] = None
        for attempt in range(_VALIDATION_RETRIES + 1):
            payload = self.client.complete_json(
                system=system,
                user=prompt,
                schema_name="research-graph-v1",
                cache_key=cache_key,
            )
            if not isinstance(payload, dict):
                # These verdicts are final here because the client has already
                # exhausted the transport-level recovery it HAS — the codex
                # client re-runs its whole rotation with backoff, the Claude
                # client gives every configured profile a turn — so re-asking
                # would stack _VALIDATION_RETRIES on top of that for up to 9
                # codex spawns on one doc. What that does NOT mean is that
                # every shape got the same number of rolls: the codex retry is
                # bounded by cumulative elapsed time, so a rotation that spent
                # the whole TESSERAE_EXTRACT_TIMEOUT budget is tried once. The
                # invariant we rely on is only "the layer that owns transport
                # already decided"; adding a second re-ask here cannot improve
                # a verdict about transport. An unset verdict (a client that
                # doesn't report one) falls through to the old behaviour.
                kind = last_failure_kind()
                if kind == "timeout":
                    raise ExtractionTimeoutError(
                        f"LLM extraction timed out for {source_path or 'doc'} — the attempt "
                        f"did not finish inside TESSERAE_EXTRACT_TIMEOUT, which does not "
                        f"establish that the provider saw it; raise the bound (0 = no bound) "
                        f"or split the document, and check the provider is reachable from "
                        f"this host if the document is small"
                    )
                if kind == "auth":
                    raise ProviderAuthError(
                        f"LLM backend not logged in for {source_path or 'doc'} — every "
                        f"configured account refused the credentials, so this is NOT a "
                        f"capacity window and waiting will not clear it; re-auth the "
                        f"configured CLI (`claude /login` / `codex login`) and re-run"
                    )
                if kind == "unavailable":
                    raise ProviderUnavailableError(
                        f"LLM backend unavailable for {source_path or 'doc'} — no response "
                        f"(transport/capacity); the model never returned anything to validate"
                    )
                last_error = GraphJSONValidationError(
                    f"LLM backend returned no usable JSON for {source_path or 'doc'}"
                )
            else:
                try:
                    graph = graph_from_llm_payload(
                        payload, source_path=source_path, source_kind=source_kind
                    )
                    ensure_source_metadata(graph, text, source_path, source_kind)
                    return graph
                except GraphJSONValidationError as exc:
                    last_error = exc
            # Reached only by REJECTING what the client returned. The CLI
            # clients cache every PARSEABLE answer, and an out-of-vocab type
            # parses fine — so without dropping it here the next attempt reads
            # its own bad answer back off disk (zero extra LLM calls while
            # stderr claims "retrying"), and, because the cache has no
            # eviction, `--changed-only --retry-fallbacks` would re-fail on
            # that same entry forever. Dropping it is what makes the retry a
            # real re-ask AND keeps the doc recoverable on a later run.
            # Duck-typed: a client with no cache (Anthropic SDK, test fakes)
            # simply has no such method. `system`/`user` are the SAME pair the
            # complete_json above sent — the cache entry is addressed by that
            # prompt, so a drop that reconstructed them differently would
            # unlink nothing and leave the rejected answer to be served again.
            forget = getattr(self.client, "forget_cached_answer", None)
            if callable(forget):
                try:
                    forget(
                        cache_key,
                        schema_name="research-graph-v1",
                        system=system,
                        user=prompt,
                    )
                except Exception as exc:  # noqa: BLE001
                    # The point of the duck-typed getattr is tolerating client
                    # shapes we don't own, and a drop that raises must not
                    # replace the validation error we are in the middle of
                    # raising with an unrelated one (CompositeCLIClient fans
                    # out unguarded, so ONE bad sub-client would do it). Worst
                    # case the rejected answer survives on disk and this doc
                    # re-fails identically next run — the pre-existing
                    # no-cache-drop behaviour, not a new failure.
                    print(f"  extract: could not drop the rejected cached answer for "
                          f"{source_path or 'doc'} ({type(exc).__name__}: {exc})",
                          file=sys.stderr)
            if attempt < _VALIDATION_RETRIES:
                print(f"  extract: invalid generation for {source_path or 'doc'} "
                      f"({last_error}); retrying ({attempt + 1}/{_VALIDATION_RETRIES})",
                      file=sys.stderr)
        raise GraphJSONValidationError(f"LLM extraction failed: {last_error}")


#: Split a document larger than this before extracting, and extract each piece.
#: ``0`` disables splitting and restores the historical single-call behaviour.
#:
#: WHY THIS EXISTS. The prompt below embeds the WHOLE document in one call, and
#: the docstring of :class:`ExtractionTimeout` offers "split the document if it
#: is large" as advice to the OPERATOR — nothing in the compile ever did it. A
#: 38 KB paper therefore got one pass and the model returned what fitted its
#: output budget: a summary. Measured on 11 full papers with one model and one
#: instruction, varying only whether the document was split:
#:
#:     single call, whole document    20.9 factual relations per paper
#:     4,000-char chunks, unioned    124.8 factual relations per paper
#:
#: Six times the relations. The consequence of not doing it was measured all
#: over this project: the compiled graph held 29% of the relations its papers
#: actually state, so ``verify_claim`` could only speak to a third of the
#: corpus and the graph arm of every retrieval benchmark packed thinner
#: evidence than raw text.
#:
#: THE COST IS REAL AND IS NOT DOLLARS ON A SUBSCRIPTION: an N-chunk document
#: costs N extraction calls instead of 1, so a large corpus takes proportionally
#: longer to compile. 4,000 matches the size the 6x was measured at; raising it
#: trades density back for speed on a curve nobody has measured yet.
EXTRACT_CHUNK_CHARS = int(os.environ.get("TESSERAE_EXTRACT_CHUNK_CHARS", "4000"))

#: Paragraph break preferred within this many characters of the target size, so
#: a chunk boundary lands between sentences rather than inside one.
_CHUNK_BACKTRACK = 600


def split_for_extraction(text: str, chunk_chars: int = 0) -> List[str]:
    """Deterministically split ``text`` for per-chunk extraction.

    Returns ``[text]`` unchanged when splitting is disabled or the document
    already fits, so a small document takes byte-identical path to before.

    Boundaries prefer the last paragraph break, then the last sentence end,
    within :data:`_CHUNK_BACKTRACK` of the target — a relation split across the
    boundary is lost from both halves, and paragraph breaks are where a paper
    is least likely to be asserting one. Purely a function of the string: same
    text in, same pieces out, no randomness and no model involved.
    """
    limit = chunk_chars or EXTRACT_CHUNK_CHARS
    if limit <= 0 or len(text) <= limit:
        return [text]
    pieces: List[str] = []
    start = 0
    while start < len(text):
        end = min(start + limit, len(text))
        if end < len(text):
            window = text[max(start, end - _CHUNK_BACKTRACK):end]
            cut = window.rfind("\n\n")
            if cut == -1:
                cut = max(window.rfind(". "), window.rfind(".\n"))
            if cut != -1:
                end = max(start, end - _CHUNK_BACKTRACK) + cut + 2
        piece = text[start:end].strip()
        if piece:
            pieces.append(piece)
        start = max(end, start + 1)
    return pieces or [text]


def extract_in_chunks(
    text: str,
    source_path: Optional[str],
    source_kind: str,
    guidance: str,
    extract_one: Callable[[str, Optional[str], str, str], ResearchGraph],
) -> ResearchGraph:
    """Extract a document in pieces and merge the pieces into one graph.

    ``extract_one`` is the single-call extraction the caller already had, so a
    document that fits in one piece takes exactly the path it took before —
    same prompt, same cache key, same bytes.

    Every piece is extracted with the SAME ``source_path`` and ``source_kind``,
    so each yields its own anchor node for the document and the merge collapses
    them by name. That is the same machinery ``merge_graphs`` already runs
    across files; a document split into pieces is only a smaller instance of
    the problem it was written for.
    """
    pieces = split_for_extraction(text)
    if len(pieces) == 1:
        return extract_one(text, source_path, source_kind, guidance)

    from .batch import merge_graphs

    graphs: List[ResearchGraph] = []
    for index, piece in enumerate(pieces):
        try:
            graphs.append(extract_one(piece, source_path, source_kind, guidance))
        except GraphJSONValidationError as exc:
            # One bad piece must not cost the whole document. Losing a chunk
            # costs its relations; raising costs all of them, and the compile
            # already treats a failed document as a fallback rather than a stop.
            print(f"  extract: chunk {index + 1}/{len(pieces)} of "
                  f"{source_path or 'document'} failed to validate ({exc}); "
                  f"keeping the other chunks", file=sys.stderr)
    if not graphs:
        raise GraphJSONValidationError(
            f"every chunk of {source_path or 'document'} failed to validate"
        )
    return merge_graphs(graphs)


def build_research_extraction_prompt(text: str, source_path: Optional[str], source_kind: str, guidance: str = "") -> str:
    title = extract_title(text, source_path)
    prompt = f"""You are extracting a typed research intelligence graph for Tesserae.

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
{json.dumps(sorted(EXTRACTABLE_NODE_TYPES), ensure_ascii=False)}

Allowed edge types:
{json.dumps(sorted(EXTRACTABLE_EDGE_TYPES), ensure_ascii=False)}

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
    if guidance:
        prompt += (
            "\n\n## Project-specific extraction guidance "
            "(learned from prior human corrections)\n" + guidance
        )
    return prompt


def run_claude_cli(prompt: str, config_dir: str, model: str, timeout: int) -> str:
    env = os.environ.copy()
    # Same Claude CLI quirk workaround as ClaudeCLIJsonClient: setting
    # CLAUDE_CONFIG_DIR explicitly to the canonical default ~/.claude
    # breaks the CLI's auth-lookup chain. Pop the env in that case so
    # the CLI's native discovery works.
    default_claude_dir = str(Path.home() / ".claude")
    if config_dir == default_claude_dir:
        env.pop("CLAUDE_CONFIG_DIR", None)
    else:
        env["CLAUDE_CONFIG_DIR"] = config_dir
    # ponytail: --strict-mcp-config, NOT --max-turns 1 — the turn cap counted
    # tool calls, so a configured MCP server made the CLI exit 1 before
    # answering. See the same note in llm_json.ClaudeCLIJsonClient._run_prompt.
    cmd = ["claude", "-p", "--output-format", "text", "--strict-mcp-config"]
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
