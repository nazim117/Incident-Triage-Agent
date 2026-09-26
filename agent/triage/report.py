"""
Writes each diagnosis to reports/ as JSON (machine-readable, including the
full tool-call transcript - handy for reviewing HOW the agent reached its
conclusion) and Markdown (for humans / pasting into an incident channel).
"""

import json
import re
import time
from pathlib import Path

from .agent import Diagnosis
from .config import REPORTS_DIR


def render_markdown(d: Diagnosis) -> str:
    names = ", ".join(a["labels"].get("alertname", "?") for a in d.alerts)
    out = [
        f"# Incident: {names}",
        "",
        f"**Summary:** {d.summary or '(none)'}",
        "",
        f"**Root cause:** {d.root_cause or '(none)'}",
        "",
        f"**Confidence:** {d.confidence}  |  **Affected:** {', '.join(d.affected_services) or '-'}",
        "",
        "## Evidence",
    ]
    out += [f"- *{e.get('source', '')}*: {e.get('detail', '')}" for e in d.evidence] or ["- (none)"]
    out += ["", "## Suggested remediation (not executed)"]
    for r in d.suggested_remediation:
        out += [f"- `{r.get('command', '')}`", f"  {r.get('why', '')}"]
    if not d.suggested_remediation:
        out.append("- (none)")
    if d.notes:
        out += ["", "## Notes", d.notes]
    out += [
        "",
        "## Investigation",
        f"{len(d.tool_calls)} tool calls, {d.steps} model steps, {d.duration_seconds}s, model `{d.model}`",
        "",
    ]
    out += [f"{i}. `{c.name}({c.arguments})`" for i, c in enumerate(d.tool_calls, 1)]
    return "\n".join(out) + "\n"


def write_report(d: Diagnosis, reports_dir: Path = REPORTS_DIR) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    names = sorted({a["labels"].get("alertname", "alert") for a in d.alerts})
    slug = re.sub(r"[^A-Za-z0-9-]+", "-", "-".join(names))[:80]
    base = reports_dir / f"{time.strftime('%Y%m%d-%H%M%S')}-{slug}"
    base.with_suffix(".json").write_text(json.dumps(d.to_dict(), indent=2, default=str))
    md = base.with_suffix(".md")
    md.write_text(render_markdown(d))
    return md
