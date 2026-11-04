"""Runner for the lifecycle eval: one stage per invocation, one dated JSON out.

    # once: pull the pinned files into the cache (outside the repo)
    uv run python -m evals.lifecycle.run fetch halumem-medium factconsolidation lme-oracle

    # update stage, deterministic supersede — free, no LLM
    uv run --with pyarrow python -m evals.lifecycle.run update --dataset factconsolidation
    uv run python -m evals.lifecycle.run update --dataset halumem-medium

    # extraction stage — spends: --runs x sessions x (extract chunks + 1 judge call)
    uv run python -m evals.lifecycle.run extraction --llm haiku --judge llm:sonnet \\
        --sample 20 --runs 3 --i-know-this-costs-money

    # end to end on LongMemEval KU + abstention — spends
    uv run python -m evals.lifecycle.run e2e --backbone haiku --judge sonnet \\
        --variant oracle --memory lifecycle --runs 3 --i-know-this-costs-money

    # FactConsolidation QA — spends one reader call per question
    uv run --with pyarrow python -m evals.lifecycle.run fc-qa --backbone haiku \\
        --fc-source factconsolidation_mh_6k --runs 3 --i-know-this-costs-money

Guards, in the order they fire: ``CI`` set → SKIP, exit 0; a missing cached
file → SKIP naming the ``fetch`` command; an LLM stage without
``--i-know-this-costs-money`` → SKIP. ``--max-llm-calls`` is a hard cap shared
by every role in the invocation.

Every stage is run ``--runs`` times with the CLI response cache off, and the
per-item labels of those runs decide whether the stage is a gate (see
``agreement.py``) — unless more than :data:`UNANSWERED_BOUND` of its LLM calls
failed or were refused, which makes it ``contaminated``. The result lands in
``tesserae.eval_results``'s directory as
``<UTC stamp>-lifecycle-<stage>-<dataset>-<arm>.<token>.json`` (see
:func:`result_filename`), which is what the MCP ``doctor_report`` tool
surfaces.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import secrets
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from ..qa.run_qa_eval import Skip
from . import agreement, datasets
from .llm import BudgetedClient, CallBudget, build_client, client_identity, fresh_answers

logger = logging.getLogger("evals.lifecycle")

CONSENT_FLAG = "--i-know-this-costs-money"

#: Above this share of failed + over-budget LLM calls a result is
#: ``contaminated`` whatever its kappa: the fallbacks agree with themselves.
UNANSWERED_BOUND = 0.02


def _git_head() -> Optional[str]:
    try:
        out = subprocess.run(["git", "-C", str(datasets.REPO_ROOT), "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=10, check=True)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("could not read git HEAD for the result file: %s", exc)
        return None
    return out.stdout.strip() or None


def _version() -> str:
    from tesserae.cli_tree import package_version

    return package_version()


def _dataset_meta(name: str) -> dict:
    src = datasets.SOURCES[name]
    return {"name": name, "repo": src.repo, "revision": src.revision, "file": src.path,
            "sha256": src.sha256}


def _mean_metrics(per_run: Sequence[dict]) -> dict:
    """Mean of every numeric metric over runs; a metric missing (None) from
    any run is reported None rather than averaged over the runs that had it."""
    if not per_run:
        return {}
    out: Dict[str, Any] = {}
    for key, first in per_run[0].items():
        values = [r.get(key) for r in per_run]
        if all(v == first for v in values):
            out[key] = first
        elif all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values):
            out[key] = round(sum(values) / len(values), 4)
        else:
            out[key] = None
    return out


def execute(stage: str, dataset: str, protocol: dict, run_fn: Callable[[], dict], *,
            arm: str, runs: int, budget: CallBudget, out_dir: Path, deterministic: bool = False) -> Path:
    """Run ``run_fn`` ``runs`` times, judge agreement, write the result JSON."""
    results: List[dict] = []
    for i in range(runs):
        with fresh_answers():
            results.append(run_fn())
        logger.info("%s run %d/%d: %s", stage, i + 1, runs, json.dumps(results[-1]["metrics"]))
    rates = [r.get("unit_rates") for r in results]
    agree = agreement.test_retest(
        [r["labels"] for r in results],
        rates if all(x is not None for x in rates) else None,
    )
    notes: List[str] = []
    tally = budget.tally()
    failed_n = sum(tally["failed"].values())
    refused_n = sum(tally["refused_over_budget"].values())
    attempted = sum(tally["calls"].values()) + refused_n
    tally["unanswered_fraction"] = round((failed_n + refused_n) / attempted, 4) if attempted else None
    if refused_n:
        notes.append(
            f"the LLM budget ran out mid-stage: {refused_n} refused calls were excluded "
            "(extraction/e2e) or fell back to the deterministic arbiter (update) — "
            "this result mixes arms and is not a measurement of either"
        )
    if failed_n:
        notes.append(
            f"{failed_n} of {attempted} LLM calls failed: those items were excluded "
            "(extraction/e2e) or fell back to the deterministic arbiter (update) — "
            "this result mixes arms"
        )
    if deterministic:
        notes.append("deterministic arm: no LLM is called, so test-retest agreement is 1.0 by "
                     "construction — it checks the harness, not a judge")
    if agree.degenerate:
        notes.append("some run pairs labelled every item identically; kappa is 1.0 by convention there")
    verdict = agree.verdict
    if tally["unanswered_fraction"] is not None and tally["unanswered_fraction"] > UNANSWERED_BOUND:
        # A deterministic fallback agrees with itself, so failed calls push
        # kappa UP: an outage must not read as a gate.
        verdict = "contaminated"
        notes.append(f"verdict {agree.verdict!r} withheld: more than {UNANSWERED_BOUND:.0%} of "
                     "LLM calls went unanswered")
    now = datetime.now(timezone.utc)
    payload = {
        "schema": "tesserae.lifecycle-eval/1",
        "stage": stage,
        #: which variant of the stage ran (judge, arbiter, memory arm): results
        #: of two arms are both current and must not shadow each other
        "arm": arm,
        "generated_at": now.isoformat(timespec="seconds"),
        "tesserae_version": _version(),
        "git_head": _git_head(),
        "dataset": _dataset_meta(dataset),
        "protocol": protocol,
        "summary": _mean_metrics([r["metrics"] for r in results]),
        "agreement": agree.to_dict(),
        #: the agreement verdict, or ``contaminated`` when too many LLM calls
        #: went unanswered for the run to measure the arm it names
        "verdict": verdict,
        "llm": tally,
        "notes": notes,
        "runs": results,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / result_filename(now, stage, dataset, arm)
    _write_new(path, json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    return path


def result_filename(now: datetime, stage: str, dataset: str, arm: str) -> str:
    """``<UTC stamp>-lifecycle-<stage>-<dataset>-<arm slug>.<token>.json``.

    The arm is in the name (slug plus a short hash of the exact string, so two
    arms never share a slug) because two arms of one stage can finish in the
    same second, and ``eval_results`` picks the newest file per name stem
    without parsing the rest. The random token keeps two same-second runs of
    one arm apart."""
    slug = re.sub(r"[^A-Za-z0-9-]+", "_", arm).strip("_") or "arm"
    digest = hashlib.sha256(arm.encode("utf-8")).hexdigest()[:6]
    return f"{now:%Y-%m-%dT%H%M%SZ}-lifecycle-{stage}-{dataset}-{slug}_{digest}.{secrets.token_hex(4)}.json"


def _write_new(path: Path, text: str) -> None:
    """Write ``path`` atomically and never over an existing file.

    Through a tmp file, so a reader (another server on the shared disk, or a
    run killed mid-write) never sees half a JSON; published with ``os.link``,
    which raises ``FileExistsError`` instead of replacing a result."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.link(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# stages
# ---------------------------------------------------------------------------


def _client(model: str, budget: CallBudget, role: str, factory: Optional[Callable[[str], Any]]) -> Any:
    inner = factory(model) if factory is not None else build_client(model)
    return BudgetedClient(inner, budget, role)


def _require_consent(args: argparse.Namespace) -> None:
    if not args.i_know_this_costs_money:
        raise Skip(f"stage {args.stage!r} calls an LLM", f"re-run with {CONSENT_FLAG}")


def _enter_work_dir(path: Path) -> None:
    """CLI agents file their transcript under the CURRENT directory's project
    key. Moving outside the repo keeps those transcripts out of every Tesserae
    project's session discovery, on top of the prompt signatures."""
    datasets.refuse_in_repo(path)
    path.mkdir(parents=True, exist_ok=True)
    os.chdir(path)


def _qa_arm(args: argparse.Namespace) -> str:
    return f"{args.memory}{'+llm-update' if args.update_llm else ''}@{args.backbone}"


def stage_extraction(args, budget, factory=None):
    from . import extraction

    _require_consent(args)
    users = datasets.load_halumem(datasets.fetch(args.dataset, args.cache_dir))
    sessions = extraction.sample_sessions(users, args.sample, seed=args.seed)
    extractor = _client(args.llm, budget, "extractor", factory)
    if args.judge == "lexical":
        judge = extraction.LexicalJudge()
    elif args.judge.startswith("llm:"):
        judge = extraction.LLMExtractionJudge(_client(args.judge[4:], budget, "judge", factory),
                                              args.judge[4:])
    else:
        raise Skip(f"--judge {args.judge!r}", "pass lexical or llm:<model>")
    _enter_work_dir(args.work_dir)
    protocol = {"system": "tesserae.session_graph_llm.extract_with_llm", "extractor": args.llm,
                "extractor_identity": client_identity(extractor.inner, args.llm),
                **judge.config(), "sample": len(sessions), "seed": args.seed,
                "sessions": [s.key for s in sessions]}
    return execute("extraction", args.dataset, protocol,
                   lambda: extraction.run_once(sessions, extractor, judge),
                   arm=f"{args.llm}+{args.judge}", runs=args.runs, budget=budget,
                   out_dir=args.out_dir)


def stage_update(args, budget, factory=None):
    from . import update

    client = None
    if args.llm:
        _require_consent(args)
        client = _client(args.llm, budget, "update", factory)
    if args.dataset == "factconsolidation":
        groups = datasets.load_factconsolidation(datasets.fetch(args.dataset, args.cache_dir),
                                                 sources=args.fc_source or None)
        cases = [update.factconsolidation_case(g) for g in groups]
    else:
        users = datasets.load_halumem(datasets.fetch(args.dataset, args.cache_dir),
                                      max_users=args.users)
        cases = [update.halumem_case(u) for u in users]
    if client is not None:
        _enter_work_dir(args.work_dir)
    protocol = {"system": "tesserae.memory.supersede.run_supersede_pass + "
                          "tesserae.memory.contradiction.detect_contradicting_pairs",
                "arbiter": f"llm:{args.llm}" if args.llm else "deterministic",
                "similarity_threshold": args.threshold,
                "units": [c.unit for c in cases],
                "gold": ("HaluMem is_update/original_memories" if args.dataset != "factconsolidation"
                         else "silver: datasets.conflict_pairs")}
    return execute("update", args.dataset, protocol,
                   lambda: update.run_once(cases, client, threshold=args.threshold),
                   arm=protocol["arbiter"], runs=args.runs, budget=budget, out_dir=args.out_dir,
                   deterministic=client is None)


def stage_e2e(args, budget, factory=None):
    from . import end_to_end

    _require_consent(args)
    name = "lme-oracle" if args.variant == "oracle" else "lme-s"
    questions = datasets.load_lme(datasets.fetch(name, args.cache_dir))
    questions = questions[: args.limit] if args.limit else questions
    reader = _client(args.backbone, budget, "reader", factory)
    judge = _client(args.judge, budget, "judge", factory)
    extractor = _client(args.backbone, budget, "extractor", factory) if args.memory == "lifecycle" else None
    update_client = _client(args.backbone, budget, "update", factory) if args.update_llm else None
    if args.judge == args.backbone:
        logger.warning("e2e: the judge and the backbone are the same model (%s) — it grades itself",
                       args.judge)
    _enter_work_dir(args.work_dir)
    protocol = {"backbone": args.backbone, "backbone_identity": client_identity(reader.inner, args.backbone),
                "judge": args.judge, "judge_identity": client_identity(judge.inner, args.judge),
                "rubric": end_to_end.LME_RUBRIC_SOURCE, "memory": args.memory, "variant": args.variant,
                "top_k": args.top_k, "update_arbiter": "llm" if args.update_llm else "deterministic",
                "questions": len(questions)}
    return execute("e2e", name, protocol,
                   lambda: end_to_end.run_once_lme(questions, reader=reader, judge=judge,
                                                   memory=args.memory, extractor=extractor,
                                                   update_client=update_client, top_k=args.top_k),
                   arm=_qa_arm(args), runs=args.runs, budget=budget, out_dir=args.out_dir)


def stage_fc_qa(args, budget, factory=None):
    from . import end_to_end

    _require_consent(args)
    groups = datasets.load_factconsolidation(datasets.fetch("factconsolidation", args.cache_dir),
                                             sources=args.fc_source or None)
    if len(groups) != 1:
        raise Skip("fc-qa scores one FactConsolidation source per invocation",
                   "pass exactly one --fc-source, e.g. factconsolidation_mh_6k")
    group = groups[0]
    reader = _client(args.backbone, budget, "reader", factory)
    update_client = _client(args.backbone, budget, "update", factory) if args.update_llm else None
    _enter_work_dir(args.work_dir)
    protocol = {"backbone": args.backbone, "backbone_identity": client_identity(reader.inner, args.backbone),
                "grader": "normalised substring match (SubEM), no judge", "memory": args.memory,
                "source": group.source, "limit": args.limit,
                "update_arbiter": "llm" if args.update_llm else "deterministic"}
    return execute("fc-qa", "factconsolidation", protocol,
                   lambda: end_to_end.run_once_fc(group, reader=reader, memory=args.memory,
                                                  update_client=update_client, limit=args.limit),
                   arm=_qa_arm(args), runs=args.runs, budget=budget, out_dir=args.out_dir)


STAGES = {"extraction": stage_extraction, "update": stage_update, "e2e": stage_e2e, "fc-qa": stage_fc_qa}


def build_parser() -> argparse.ArgumentParser:
    from tesserae.eval_results import lifecycle_results_dir

    parser = argparse.ArgumentParser(prog="python -m evals.lifecycle.run", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="stage", required=True)

    fetch = sub.add_parser("fetch", help="download pinned dataset files into the cache")
    fetch.add_argument("names", nargs="*", default=["halumem-medium", "factconsolidation", "lme-oracle"],
                       help=f"any of {sorted(datasets.SOURCES)}")
    fetch.add_argument("--cache-dir", type=Path, default=None)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--runs", type=int, default=3, help="re-runs for test-retest (default 3)")
        p.add_argument("--max-llm-calls", type=int, default=None, help="hard cap shared by every role")
        p.add_argument("--out-dir", type=Path, default=lifecycle_results_dir())
        p.add_argument("--cache-dir", type=Path, default=None)
        p.add_argument("--work-dir", type=Path, default=datasets.default_cache_dir() / "work",
                       help="cwd for LLM calls; must be outside the repo")
        p.add_argument("--seed", default="r1")
        p.add_argument(CONSENT_FLAG, dest="i_know_this_costs_money", action="store_true")

    ex = sub.add_parser("extraction", help="stage 1: HaluMem gold points vs session extraction")
    common(ex)
    ex.add_argument("--dataset", choices=["halumem-medium", "halumem-long"], default="halumem-medium")
    ex.add_argument("--llm", required=True, help="extractor model")
    ex.add_argument("--judge", default="lexical", help="lexical | llm:<model>")
    ex.add_argument("--sample", type=int, default=20, help="sessions, seeded hash order")

    up = sub.add_parser("update", help="stage 2: supersede vs HaluMem updates / FactConsolidation")
    common(up)
    up.add_argument("--dataset", choices=["halumem-medium", "halumem-long", "factconsolidation"],
                    default="halumem-medium")
    up.add_argument("--llm", default=None, help="arbitrate with this model (default: deterministic)")
    up.add_argument("--users", type=int, default=None, help="HaluMem users to include (default all)")
    up.add_argument("--fc-source", action="append", default=[], help="FactConsolidation source(s)")
    up.add_argument("--threshold", type=float, default=0.55, help="supersede Jaccard threshold")

    e2e = sub.add_parser("e2e", help="stage 3: LongMemEval knowledge-update + abstention")
    common(e2e)
    e2e.add_argument("--backbone", required=True, help="model that builds memory and answers")
    e2e.add_argument("--judge", required=True, help="model that grades with the LongMemEval rubric")
    e2e.add_argument("--variant", choices=["oracle", "s"], default="oracle")
    e2e.add_argument("--memory", choices=["lifecycle", "raw"], default="lifecycle")
    e2e.add_argument("--top-k", type=int, default=None)
    e2e.add_argument("--limit", type=int, default=None, help="first N questions only")
    e2e.add_argument("--update-llm", action="store_true", help="arbitrate supersede with the backbone")

    fc = sub.add_parser("fc-qa", help="stage 3b: FactConsolidation QA over live facts")
    common(fc)
    fc.add_argument("--backbone", required=True)
    fc.add_argument("--fc-source", action="append", default=[])
    fc.add_argument("--memory", choices=["lifecycle", "raw"], default="lifecycle")
    fc.add_argument("--limit", type=int, default=None)
    fc.add_argument("--update-llm", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None, *, client_factory: Optional[Callable[[str], Any]] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = build_parser().parse_args(argv)
    try:
        if os.environ.get("CI"):
            raise Skip("CI is set — the lifecycle eval never runs in CI",
                       "run it by hand; CI runs the unit tests in tests/test_lifecycle_eval.py")
        if args.stage == "fetch":
            for name in args.names:
                print(datasets.fetch(name, args.cache_dir, allow_download=True))
            return 0
        if args.runs < 1:
            raise Skip("--runs must be at least 1", "pass --runs 3 for a test-retest verdict")
        for path in (args.cache_dir, args.out_dir, args.work_dir):
            if path is not None:
                datasets.refuse_in_repo(path)
        args.out_dir = args.out_dir.expanduser().resolve()
        args.work_dir = args.work_dir.expanduser().resolve()
        budget = CallBudget(args.max_llm_calls)
        path = STAGES[args.stage](args, budget, client_factory)
    except Skip as skip:
        return skip.emit()
    payload = json.loads(path.read_text(encoding="utf-8"))
    print(json.dumps({"result": str(path), "summary": payload["summary"],
                      "agreement": payload["agreement"], "verdict": payload["verdict"],
                      "llm": payload["llm"],
                      "notes": payload["notes"]}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
