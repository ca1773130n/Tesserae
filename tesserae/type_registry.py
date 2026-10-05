"""Registry-backed open vocabulary (0.42, T1).

The core vocabulary — :class:`~tesserae.research_graph.ResearchNodeType` and
:data:`~tesserae.research_graph.ALLOWED_EDGE_TYPES` — is closed and versioned
with the package. A host that grows its own ontology (HypePaper's concept
layer proposes, gates and promotes relation types weekly) cannot wait for a
wheel release per type, and must not write a type an older reader cannot load.

This module is the answer to both. A host registers :class:`TypeSpec` rows at
runtime; every spec names a ``core_parent`` in the core vocabulary, so anything
that does not know the type can always fall back to the parent:

* **Nodes never carry a registry type as ``type``.** The enum is unchanged. A
  node keeps its core type and names the finer kind in ``metadata.subtype``
  (:attr:`ResearchNode.subtype` reads it), so a 0.41 reader loads it unchanged.
* **Edges may carry an ACTIVE registry type in memory** —
  ``ResearchEdge.__post_init__`` accepts core ∪ active registry edge types —
  but the write paths that persist into ``graph.json`` (``agent_write``) map a
  registry type onto its core parent plus ``metadata.relation``. That is what
  keeps a rollback to 0.41 safe: no registry-only type is ever persisted.
* ``graph_from_payload(..., type_mode="lenient")`` maps a type it does not know
  onto its registry parent (else ``Concept`` / ``references``) instead of
  raising, keeping the original in ``metadata.raw_type``.

Statuses follow the host's lifecycle: ``core`` (describes a type the host
treats as core), ``seed`` and ``shadow`` (rows are written, traversal uses the
PARENT's weight), ``promoted`` (own view and weight), ``proposed`` (from drift,
not yet written), ``vetoed`` / ``retired`` (rows are read as the parent again).
Only :data:`ACTIVE_STATUSES` are accepted by strict construction.

Loading: :func:`register_types` takes specs, dicts, a JSON file path, a
``{"types": [...]}`` mapping, or a zero-argument callable returning any of
those. ``TESSERAE_TYPE_REGISTRY=/path.json`` is read once, lazily, the first
time the registry is consulted. Nothing here imports
:mod:`tesserae.research_graph` at module load (it imports us).
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, FrozenSet, Iterable, List, Mapping, Optional, Union

__all__ = [
    "ACTIVE_STATUSES",
    "KNOWN_STATUSES",
    "PRODUCER_ONLY_EDGE_TYPES",
    "TypeRegistry",
    "TypeSpec",
    "get_registry",
    "register_types",
    "reset_registry",
]

#: Every lifecycle status a spec may carry (design §3.6).
KNOWN_STATUSES: FrozenSet[str] = frozenset(
    {"core", "seed", "proposed", "shadow", "promoted", "vetoed", "retired"}
)

#: Statuses whose rows are written and traversed. ``proposed`` is not written
#: yet; ``vetoed`` / ``retired`` rows are read as their parent again.
ACTIVE_STATUSES: FrozenSet[str] = frozenset({"core", "seed", "shadow", "promoted"})

#: Edge types no extraction LLM may assert and no agent may write as a type
#: (T9). Cross-domain bridges are derived by SQL miners or generators and
#: validated; ``merged_into`` is the canonicalizer's ledger. Same posture as the
#: ``recovers`` precedent in ``research_graph.CAUSAL_EDGE_TYPES``: an LLM can
#: never mint a bridge. Defined here (not in research_graph) so the registry
#: can enforce it without an import cycle; research_graph re-exports it.
PRODUCER_ONLY_EDGE_TYPES: FrozenSet[str] = frozenset(
    {"transfers_to", "candidate_transfer", "analogous_to", "merged_into"}
)

_KINDS = frozenset({"node", "edge"})


@dataclass(frozen=True)
class TypeSpec:
    """One registry row. ``core_parent`` must resolve to the core vocabulary."""

    name: str
    kind: str
    core_parent: str
    definition: str = ""
    view: Optional[str] = None
    ppr_weight: Optional[float] = None
    extractable: bool = True
    inverse_of: Optional[str] = None
    symmetric: bool = False
    acyclic: bool = False
    status: str = "seed"

    def __post_init__(self) -> None:
        if not str(self.name or "").strip():
            raise ValueError("TypeSpec.name must be non-empty")
        if self.kind not in _KINDS:
            raise ValueError(f"TypeSpec {self.name!r}: kind must be 'node' or 'edge', got {self.kind!r}")
        if not str(self.core_parent or "").strip():
            raise ValueError(f"TypeSpec {self.name!r}: core_parent is required")
        if self.status not in KNOWN_STATUSES:
            raise ValueError(
                f"TypeSpec {self.name!r}: unknown status {self.status!r} — "
                f"expected one of {sorted(KNOWN_STATUSES)}"
            )
        if self.ppr_weight is not None and float(self.ppr_weight) < 0.0:
            raise ValueError(f"TypeSpec {self.name!r}: ppr_weight must be >= 0")
        if self.kind == "edge" and self.name in PRODUCER_ONLY_EDGE_TYPES and self.extractable:
            # Producer-only is not a per-host preference: force it rather than
            # trusting every host to remember.
            object.__setattr__(self, "extractable", False)

    @property
    def active(self) -> bool:
        return self.status in ACTIVE_STATUSES

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "TypeSpec":
        if not isinstance(raw, Mapping):
            raise ValueError(f"type registry entry must be an object, got {type(raw).__name__}")
        known = set(cls.__dataclass_fields__)
        unknown = sorted(set(raw) - known)
        if unknown:
            raise ValueError(f"type registry entry {raw.get('name')!r}: unknown keys {unknown}")
        values = dict(raw)
        if values.get("ppr_weight") is not None:
            values["ppr_weight"] = float(values["ppr_weight"])
        return cls(**values)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


RegistrySource = Union[
    "TypeSpec",
    Mapping[str, Any],
    Iterable[Union["TypeSpec", Mapping[str, Any]]],
    str,
    Path,
    Callable[[], Any],
]


def _core_node_types() -> FrozenSet[str]:
    from .research_graph import ALLOWED_NODE_TYPES

    return frozenset(ALLOWED_NODE_TYPES)


def _core_edge_types() -> FrozenSet[str]:
    from .research_graph import ALLOWED_EDGE_TYPES

    return frozenset(ALLOWED_EDGE_TYPES)


@dataclass
class TypeRegistry:
    """A name -> :class:`TypeSpec` map with core-parent resolution.

    Thread-safe for the register/read pattern the host uses (register at
    startup or on a weekly refresh, read on every load).
    """

    _specs: Dict[str, TypeSpec] = field(default_factory=dict)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False, compare=False)

    # ----------------------------------------------------------------- writes
    def register(self, spec: TypeSpec, *, replace: bool = True) -> None:
        core = _core_node_types() if spec.kind == "node" else _core_edge_types()
        if spec.name in core and spec.status != "core":
            raise ValueError(
                f"type registry: {spec.name!r} is already a core {spec.kind} type; "
                "only status='core' may describe it"
            )
        with self._lock:
            prior = self._specs.get(spec.name)
            if prior is not None and prior.kind != spec.kind:
                raise ValueError(
                    f"type registry: {spec.name!r} is registered as a {prior.kind} type "
                    f"and cannot be re-registered as a {spec.kind} type"
                )
            if prior is not None and not replace:
                return
            self._specs[spec.name] = spec
            try:
                self._resolve_parent(spec.name, spec.kind)
            except ValueError:
                # Never leave a registry that cannot resolve a parent behind.
                if prior is None:
                    del self._specs[spec.name]
                else:
                    self._specs[spec.name] = prior
                raise

    def clear(self) -> None:
        with self._lock:
            self._specs.clear()

    # ------------------------------------------------------------------ reads
    def get(self, name: str) -> Optional[TypeSpec]:
        return self._specs.get(name)

    def specs(self, kind: Optional[str] = None) -> List[TypeSpec]:
        with self._lock:
            items = list(self._specs.values())
        return sorted(
            (s for s in items if kind is None or s.kind == kind), key=lambda s: s.name
        )

    def __len__(self) -> int:
        return len(self._specs)

    def is_active(self, name: str, kind: str) -> bool:
        spec = self._specs.get(name)
        return spec is not None and spec.kind == kind and spec.active

    def active_types(self, kind: str) -> FrozenSet[str]:
        return frozenset(s.name for s in self.specs(kind) if s.active and s.status != "core")

    def _resolve_parent(self, name: str, kind: str) -> str:
        core = _core_node_types() if kind == "node" else _core_edge_types()
        seen: List[str] = []
        current = name
        while current not in core:
            spec = self._specs.get(current)
            if spec is None or spec.kind != kind:
                raise ValueError(
                    f"type registry: {name!r} has no core {kind} ancestor "
                    f"(chain {' -> '.join(seen + [current])})"
                )
            if current in seen:
                raise ValueError(
                    f"type registry: parent cycle {' -> '.join(seen + [current])}"
                )
            seen.append(current)
            current = spec.core_parent
        return current

    def core_parent(self, name: str, kind: str) -> Optional[str]:
        """The core-vocabulary ancestor of ``name``, or ``None`` if unregistered.

        A core type resolves to itself. Any registered status counts — a vetoed
        type's rows are still read, as its parent.
        """
        core = _core_node_types() if kind == "node" else _core_edge_types()
        if name in core:
            return name
        spec = self._specs.get(name)
        if spec is None or spec.kind != kind:
            return None
        try:
            return self._resolve_parent(name, kind)
        except ValueError:
            return None

    def extractable_edge_types(self) -> FrozenSet[str]:
        """Active, extractable, non-producer-only registry edge types."""
        return frozenset(
            s.name
            for s in self.specs("edge")
            if s.active and s.status != "core" and s.extractable
            and s.name not in PRODUCER_ONLY_EDGE_TYPES
        )

    def edge_weight_overlay(self, defaults: Mapping[str, float]) -> Dict[str, float]:
        """Per-type PPR weights for active registry edge types.

        A ``promoted`` type walks at its own ``ppr_weight`` (when declared).
        Every other active status walks at its core parent's weight — seeds get
        no head start until they pass the host's gate (design §3.6).
        """
        overlay: Dict[str, float] = {}
        for spec in self.specs("edge"):
            if not spec.active or spec.status == "core":
                continue
            parent = self.core_parent(spec.name, "edge")
            parent_weight = float(defaults.get(parent, 1.0)) if parent else 1.0
            if spec.status == "promoted" and spec.ppr_weight is not None:
                overlay[spec.name] = float(spec.ppr_weight)
            else:
                overlay[spec.name] = parent_weight
        return overlay

    def to_payload(self) -> Dict[str, Any]:
        return {"types": [s.to_dict() for s in self.specs()]}


_REGISTRY = TypeRegistry()
_ENV_LOADED = False
_ENV_LOCK = threading.Lock()
ENV_REGISTRY_PATH = "TESSERAE_TYPE_REGISTRY"


def _iter_specs(source: Any) -> Iterable[TypeSpec]:
    if callable(source) and not isinstance(source, (TypeSpec, Mapping)):
        yield from _iter_specs(source())
        return
    if isinstance(source, TypeSpec):
        yield source
        return
    if isinstance(source, (str, Path)):
        path = Path(source)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"type registry: cannot read {path}: {exc}") from exc
        yield from _iter_specs(payload)
        return
    if isinstance(source, Mapping):
        if "types" in source and "name" not in source:
            yield from _iter_specs(source["types"])
            return
        yield TypeSpec.from_dict(source)
        return
    if isinstance(source, Iterable):
        for item in source:
            yield from _iter_specs(item)
        return
    raise ValueError(f"type registry: unsupported source {type(source).__name__}")


def register_types(source: RegistrySource, *, registry: Optional[TypeRegistry] = None,
                   replace: bool = True) -> List[TypeSpec]:
    """Register every spec in ``source`` and return them.

    Validation is all-or-nothing per spec: a spec whose parent chain does not
    reach the core vocabulary raises ``ValueError`` and is not registered.
    Registration order matters only for parent chains (register a parent
    before the child that names it).
    """
    target = registry if registry is not None else get_registry()
    registered: List[TypeSpec] = []
    for spec in _iter_specs(source):
        target.register(spec, replace=replace)
        registered.append(spec)
    return registered


def get_registry() -> TypeRegistry:
    """The process registry, loading ``$TESSERAE_TYPE_REGISTRY`` once."""
    global _ENV_LOADED
    if not _ENV_LOADED:
        with _ENV_LOCK:
            if not _ENV_LOADED:
                _ENV_LOADED = True
                path = os.environ.get(ENV_REGISTRY_PATH, "").strip()
                if path:
                    register_types(path, registry=_REGISTRY)
    return _REGISTRY


def reset_registry() -> None:
    """Empty the process registry and re-arm the env load (tests, hot reload)."""
    global _ENV_LOADED
    _REGISTRY.clear()
    with _ENV_LOCK:
        _ENV_LOADED = False
