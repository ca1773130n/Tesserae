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
from .wizard import WizardNotInteractive, render_review, run_wizard

__all__ = [
    "DetectionReport",
    "DriftError",
    "InstallAction",
    "PlanValidationError",
    "RunAction",
    "SetupPlan",
    "SetupResult",
    "WizardNotInteractive",
    "apply_plan",
    "build_plan",
    "detect",
    "render_review",
    "run_wizard",
]
