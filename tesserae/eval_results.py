"""Where lifecycle-eval results live, and the latest one per stage.

The harness itself is ``evals/lifecycle`` and is not part of the installed
package; this module is the one piece the package needs so ``doctor_report``
can say what the last run measured. Results are machine-wide — they score the
extraction and update code, not one project's graph — so they live under
``~/.tesserae/evals/lifecycle`` (``TESSERAE_LIFECYCLE_RESULTS_DIR`` overrides),
one dated JSON per run.

Reading never raises on a bad file: an unreadable result is reported as
``error`` beside the readable ones, and logged at warning, so a corrupt file
cannot take ``doctor_report`` down with it and cannot vanish silently either.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

RESULT_SCHEMA = "tesserae.lifecycle-eval/1"
RESULT_GLOB = "*-lifecycle-*.json"


def lifecycle_results_dir() -> Path:
    raw = (os.environ.get("TESSERAE_LIFECYCLE_RESULTS_DIR") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".tesserae" / "evals" / "lifecycle"


def latest_lifecycle_results(results_dir: Optional[Path] = None) -> Dict[str, Any]:
    """``{"dir", "stages": {"<stage>:<dataset>:<arm>": summary}, "errors": [...]}``
    for the newest result of each stage, dataset and arm (judge, arbiter or
    memory arm), newest by the ``generated_at`` the file records. The key is
    that wide because a lifecycle number is only meaningful beside its control
    — the ``raw`` arm of the same stage — and one must not hide the other.

    A stage summary carries the headline metrics, the test-retest verdict
    (``gate`` / ``diagnostic`` / ``rejected`` / ``insufficient-runs``) and the
    file path, which is where the per-run detail lives.
    """
    root = Path(results_dir) if results_dir is not None else lifecycle_results_dir()
    out: Dict[str, Any] = {"dir": str(root), "stages": {}, "errors": []}
    if not root.is_dir():
        return out
    best: Dict[str, Dict[str, Any]] = {}
    for path in sorted(root.glob(RESULT_GLOB)):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("lifecycle eval result %s is unreadable: %s", path, exc)
            out["errors"].append({"path": str(path), "error": str(exc)})
            continue
        if not isinstance(payload, dict) or payload.get("schema") != RESULT_SCHEMA:
            logger.warning("lifecycle eval result %s has no %s schema tag", path, RESULT_SCHEMA)
            out["errors"].append({"path": str(path), "error": "unknown schema"})
            continue
        dataset = str((payload.get("dataset") or {}).get("name") or "?")
        stage = f"{payload.get('stage') or '?'}:{dataset}:{payload.get('arm') or '?'}"
        generated = str(payload.get("generated_at") or "")
        current = best.get(stage)
        if current is None or generated > current["generated_at"]:
            best[stage] = {
                "generated_at": generated,
                "path": str(path),
                "summary": payload.get("summary") or {},
                "agreement": (payload.get("agreement") or {}).get("verdict"),
                "kappa_min": (payload.get("agreement") or {}).get("kappa_min"),
                "runs": len(payload.get("runs") or []),
            }
    out["stages"] = best
    return out
