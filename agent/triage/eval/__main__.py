"""
    python -m triage.eval list
    python -m triage.eval run [--only database-down,webapp-5xx] [--repeat 3] [--model deepseek-chat]
    python -m triage.eval score evals/<run-id>     # re-score saved diagnoses, no injection

Run from the agent/ directory, with the stack up and DEEPSEEK_API_KEY in .env.
"""

import argparse
import json
import sys
import time
from pathlib import Path

from ..__main__ import build_agent, log
from ..config import REPO_ROOT, load_settings
from ..prometheus import Prometheus
from .cases import CASES, select
from .runner import Runner, write_summary
from .scoring import SCORED, passed, score

EVALS_DIR = REPO_ROOT / "evals"


def cmd_list() -> int:
    print(f"{'case':<26} {'alert':<30} {'root service':<15} args")
    for c in CASES:
        print(f"{c.name:<26} {c.expect_alert:<30} {c.root_service:<15} {' '.join(c.args)}")
    return 0


def cmd_run(args) -> int:
    settings = load_settings()
    cases = select(args.only)
    prom = Prometheus(settings.prometheus_url)
    agent = build_agent(settings, prom, model=args.model, log=lambda m: log("  " + m))
    run_id = time.strftime("%Y%m%d-%H%M%S")
    out_dir = EVALS_DIR / run_id
    meta = {"run_id": run_id, "model": agent.model, "cases": [c.name for c in cases], "repeat": args.repeat}

    log(f"eval {run_id}: {len(cases)} case(s) x {args.repeat}, model {agent.model} -> {out_dir}")
    runner = Runner(prom, agent, out_dir, settings.settle_seconds, log=log)
    try:
        runner.run(cases, args.repeat, meta)
    except KeyboardInterrupt:
        log("interrupted - partial scorecard written")
    print("\n" + (out_dir / "scorecard.md").read_text())
    return 0


def cmd_score(args) -> int:
    """Re-applies the current cases.py checks to a saved run. Useful after
    loosening or tightening a pattern: no faults, no API calls."""
    out_dir = Path(args.run_dir)
    saved = json.loads((out_dir / "results.json").read_text())
    by_name = {c.name: c for c in CASES}
    for r in saved["results"]:
        if r["outcome"] == SCORED and r["case"] in by_name:
            r["checks"] = score(by_name[r["case"]], r["diagnosis"])
            r["passed"] = passed(r["checks"])
    print(write_summary(out_dir, saved["results"], saved["meta"]).read_text())
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="triage.eval", description="Eval harness for the triage agent (Phase 5)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    run = sub.add_parser("run")
    run.add_argument("--only", help="comma-separated case names (default: all)")
    run.add_argument("--repeat", type=int, default=1, help="runs per case, to measure variance")
    run.add_argument("--model", help="override DEEPSEEK_MODEL for this run")
    sc = sub.add_parser("score")
    sc.add_argument("run_dir")
    args = parser.parse_args(argv)

    try:
        if args.command == "list":
            return cmd_list()
        if args.command == "run":
            return cmd_run(args)
        return cmd_score(args)
    except ValueError as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    sys.exit(main())
