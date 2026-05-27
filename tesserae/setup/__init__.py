"""Setup pipeline: detection → plan → wizard → apply."""

from .apply import DriftError, SetupResult, apply_plan
from .detection import DetectionReport, detect
from .plan import (
    InstallAction,
    PlanValidationError,
    RunAction,
    SetupPlan,
    build_plan,
)

__all__ = [
    "DetectionReport",
    "DriftError",
    "InstallAction",
    "PlanValidationError",
    "RunAction",
    "SetupPlan",
    "SetupResult",
    "apply_plan",
    "build_plan",
    "detect",
]
