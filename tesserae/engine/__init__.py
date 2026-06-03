"""Tesserae in-process engine: reusable pipeline orchestration.

Re-exports the sequential step runner so callers can simply
``from tesserae.engine import Pipeline, StepResult``.
"""

from .pipeline import Pipeline, StepResult

__all__ = ["Pipeline", "StepResult"]
