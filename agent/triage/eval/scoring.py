"""
Deterministic scoring: plain checks over the agent's JSON, no LLM judge.

An LLM judge could grade wording-independent "is this right?", but it costs
money per score, can itself be wrong, and gives different answers on
re-runs. Regex checks are free and reproducible, and because they run on
saved diagnoses, changing a criterion only needs `python -m triage.eval score
<run-dir>` - no re-injecting faults. The trade-off is that an oddly-worded
correct answer can fail; the saved JSON makes those easy to spot and the
patterns in cases.py are kept loose for that reason.
"""

import re

from .cases import Case

# Outcomes other than "scored": the harness couldn't produce a fair test.
ALERT_NOT_FIRED = "alert_not_fired"
ERROR = "error"
SCORED = "scored"


def score(case: Case, d: dict) -> dict[str, bool | None]:
    """d is a Diagnosis as a dict (as saved in the run's JSON). None means
    the check doesn't apply to this case."""
    affected = {s.strip().lower() for s in d.get("affected_services", [])}
    text = f"{d.get('root_cause', '')}\n{d.get('summary', '')}"
    commands = [r.get("command", "") for r in d.get("suggested_remediation", [])]
    return {
        "valid_json": "did not return valid JSON" not in d.get("notes", ""),
        "root_service": case.root_service in affected,
        "root_cause": all(re.search(p, text, re.IGNORECASE) for p in case.root_cause_patterns),
        "remediation": (
            any(re.search(p, c, re.IGNORECASE) for p in case.remediation_patterns for c in commands)
            if case.remediation_patterns else None
        ),
        "no_false_blame": (
            not (affected & set(case.not_affected)) if case.not_affected else None
        ),
    }


def passed(checks: dict[str, bool | None]) -> bool:
    return all(v for v in checks.values() if v is not None)


def render_scorecard(results: list[dict], meta: dict) -> str:
    """results: one dict per case run, as written to results.json."""
    lines = [
        f"# Eval run {meta.get('run_id', '')}",
        "",
        f"Model `{meta.get('model', '')}` | {len(results)} case runs | wall time {meta.get('wall_seconds', 0):.0f}s",
        "",
        "| Case | Result | Failed checks | Confidence | Steps | Tool calls | Tokens | Seconds |",
        "|------|--------|---------------|------------|-------|------------|--------|---------|",
    ]
    for r in results:
        d = r.get("diagnosis") or {}
        if r["outcome"] == SCORED:
            result = "PASS" if r["passed"] else "FAIL"
            failed = ", ".join(k for k, v in r["checks"].items() if v is False) or "-"
        else:
            result, failed = r["outcome"].upper(), r.get("error", "") or "-"
        tokens = (d.get("prompt_tokens", 0) + d.get("completion_tokens", 0)) if d else 0
        lines.append(
            f"| {r['label']} | {result} | {failed} | {d.get('confidence', '-')} | {d.get('steps', '-')} "
            f"| {len(d.get('tool_calls', [])) if d else '-'} | {tokens or '-'} | {d.get('duration_seconds', '-')} |"
        )

    scored = [r for r in results if r["outcome"] == SCORED]
    n_pass = sum(r["passed"] for r in scored)
    lines += ["", "## Totals", ""]
    if scored:
        steps = [r["diagnosis"]["steps"] for r in scored]
        tokens = sum(r["diagnosis"].get("prompt_tokens", 0) + r["diagnosis"].get("completion_tokens", 0) for r in scored)
        lines += [
            f"- Pass rate: {n_pass}/{len(scored)} ({100 * n_pass / len(scored):.0f}%) of scored runs",
            f"- Mean steps: {sum(steps) / len(steps):.1f}",
            f"- Total tokens: {tokens}",
        ]
    not_scored = len(results) - len(scored)
    if not_scored:
        lines.append(f"- Not scored (harness errors / alert never fired): {not_scored}")

    # With --repeat, show per-case consistency: LLM runs vary, and a case that
    # passes 2/3 times is a very different signal from one that passes 3/3.
    by_case: dict[str, list[dict]] = {}
    for r in scored:
        by_case.setdefault(r["case"], []).append(r)
    if any(len(v) > 1 for v in by_case.values()):
        lines += ["", "## Per case", ""]
        lines += [f"- {c}: {sum(x['passed'] for x in v)}/{len(v)}" for c, v in by_case.items()]
    return "\n".join(lines) + "\n"
