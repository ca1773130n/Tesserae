"""Fail-fast sequential runner for the Tesserae refresh chain.

This module codifies the prose-only refresh sequence (ingest -> compile ->
project/publish) as an importable, callable Python object. It returns a
structured ``List[StepResult]`` instead of printing-and-exiting, so the daemon
(Phase 2), CLI (Plan 03), and MCP (later) can all call the SAME ``Pipeline.run()``
and decide for themselves how to surface outcomes (exit codes, JSON, logs).

The daemon phase (Phase 2) will extend this with retry/continue policy -- do NOT
add concurrency or threading here (01-RESEARCH.md, Common Implementation Traps).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple


@dataclass
class StepResult:
    """Outcome of a single pipeline step.

    ``error`` is typed ``BaseException`` so any raised object is representable,
    but ``Pipeline.run`` only catches ``Exception`` (KeyboardInterrupt/SystemExit
    propagate, as they should).
    """

    name: str
    ok: bool
    data: Dict[str, Any] = field(default_factory=dict)
    error: Optional[BaseException] = None


class Pipeline:
    """Run a list of named, zero-arg steps in registration order, fail-fast.

    Steps are ``(name, callable)`` tuples. ``run()`` calls each in order; the
    first step to raise ``Exception`` aborts the chain (no later step runs) with
    its exception captured. ``run()`` never re-raises and never prints -- callers
    inspect ``StepResult.ok`` to decide what to do.
    """

    def __init__(self, steps: List[Tuple[str, Callable[[], Any]]]) -> None:
        self._steps = list(steps)

    def run(self) -> List[StepResult]:
        results: List[StepResult] = []
        for name, fn in self._steps:
            try:
                data = fn()
            except Exception as exc:  # noqa: BLE001 - surfaced via StepResult.error
                results.append(StepResult(name=name, ok=False, error=exc))
                break
            results.append(
                StepResult(
                    name=name,
                    ok=True,
                    data=data if isinstance(data, dict) else {},
                )
            )
        return results
