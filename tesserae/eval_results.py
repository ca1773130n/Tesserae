"""Where lifecycle-eval results live, and the latest one per stage.

The harness itself is ``evals/lifecycle`` and is not part of the installed
package; this module is the one piece the package needs so ``doctor_report``
can say what the last run measured. Results are machine-wide — they score the
extraction and update code, not one project's graph — so they live under
``~/.tesserae/evals/lifecycle`` (``TESSERAE_LIFECYCLE_RESULTS_DIR`` overrides),
one dated JSON per run.

Reading never raises on a bad file — unreadable, cut off mid-character, or
the wrong shape: it is reported under ``errors`` beside the readable ones, and
logged at warning, so a corrupt file cannot take ``doctor_report`` down with
it and cannot vanish silently either.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

RESULT_SCHEMA = "tesserae.lifecycle-eval/1"
RESULT_GLOB = "*-lifecycle-*.json"


def lifecycle_results_dir() -> Path:
    raw = (os.environ.get("TESSERAE_LIFECYCLE_RESULTS_DIR") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".tesserae" / "evals" / "lifecycle"


def _stem(path: Path) -> str:
    """Name stem that identifies one stage/dataset/arm series: the filename
    minus its leading timestamp and its trailing ``.<token>.json``. A legacy
    name without a token keeps everything up to ``.json``."""
    rest = path.name.split("-lifecycle-", 1)[-1]
    return rest.split(".", 1)[0]


def _read_result(path: Path) -> Dict[str, Any]:
    """Parse one result file, or raise ``ValueError`` naming what is wrong."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(str(exc)) from exc
    # json.JSONDecodeError and UnicodeDecodeError are both ValueError.
    if not isinstance(payload, dict) or payload.get("schema") != RESULT_SCHEMA:
        raise ValueError("unknown schema")
    for key in ("dataset", "agreement", "summary", "llm"):
        if payload.get(key) is not None and not isinstance(payload[key], dict):
            raise ValueError(f"{key!r} is not an object")
    for key in ("notes", "runs"):
        if payload.get(key) is not None and not isinstance(payload[key], list):
            raise ValueError(f"{key!r} is not a list")
    return payload


def latest_lifecycle_results(results_dir: Optional[Path] = None) -> Dict[str, Any]:
    """``{"dir", "stages": {"<stage>:<dataset>:<arm>": summary}, "errors": [...]}``
    for the newest result of each stage, dataset and arm (judge, arbiter or
    memory arm), newest by the ``generated_at`` the file records. The key is
    that wide because a lifecycle number is only meaningful beside its control
    — the ``raw`` arm of the same stage — and one must not hide the other.

    Only the newest READABLE file of each filename series is parsed (the
    runner names files by stage, dataset and arm after a sortable UTC stamp),
    so the cost does not grow with eval history: older files in a series are
    opened only when every newer one is unreadable.

    A stage summary carries the headline metrics, the verdict (``gate`` /
    ``diagnostic`` / ``rejected`` / ``insufficient-runs``, or ``contaminated``
    when too many LLM calls went unanswered), the result's notes (which say,
    e.g., that a deterministic arm's kappa is 1.0 by construction), the LLM
    failure tally and the file path, which is where the per-run detail lives.
    """
    root = Path(results_dir) if results_dir is not None else lifecycle_results_dir()
    out: Dict[str, Any] = {"dir": str(root), "stages": {}, "errors": []}
    if not root.is_dir():
        return out
    series: Dict[str, List[Path]] = {}
    for path in root.glob(RESULT_GLOB):
        series.setdefault(_stem(path), []).append(path)
    best: Dict[str, Dict[str, Any]] = {}
    for stem in sorted(series):
        for path in sorted(series[stem], key=lambda p: p.name, reverse=True):
            try:
                payload = _read_result(path)
            except ValueError as exc:
                logger.warning("lifecycle eval result %s is unreadable: %s", path, exc)
                out["errors"].append({"path": str(path), "error": str(exc)})
                continue
            dataset = str((payload.get("dataset") or {}).get("name") or "?")
            stage = f"{payload.get('stage') or '?'}:{dataset}:{payload.get('arm') or '?'}"
            generated = str(payload.get("generated_at") or "")
            agreement = payload.get("agreement") or {}
            llm = payload.get("llm") or {}
            current = best.get(stage)
            if current is None or generated > current["generated_at"]:
                best[stage] = {
                    "generated_at": generated,
                    "path": str(path),
                    "summary": payload.get("summary") or {},
                    "agreement": payload.get("verdict") or agreement.get("verdict"),
                    "kappa_min": agreement.get("kappa_min"),
                    "degenerate": agreement.get("degenerate"),
                    "runs": len(payload.get("runs") or []),
                    "notes": list(payload.get("notes") or []),
                    "llm_failed": llm.get("failed") or {},
                    "llm_refused": llm.get("refused_over_budget") or {},
                }
            break
    out["stages"] = best
    return out
