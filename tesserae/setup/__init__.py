"""Setup pipeline: detection → plan → wizard → apply."""

from .detection import DetectionReport, detect

__all__ = ["DetectionReport", "detect"]
