"""Setup pipeline: detection → plan → wizard → apply."""

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
    "InstallAction",
    "PlanValidationError",
    "RunAction",
    "SetupPlan",
    "build_plan",
    "detect",
]
