"""
The tools the model can call, in OpenAI function-calling format.

DeepSeek's API is OpenAI-compatible, so tools are described with JSON
Schema: the model reads these descriptions to decide what to call, and
replies with a tool name plus JSON arguments. Toolbox.dispatch() runs the
call and returns a string that goes back to the model as a `role: tool`
message.

Every tool is read-only by design (Phase 4 diagnoses and SUGGESTS fixes; it
never applies them). Notably absent: the webapp's /admin/fault/status
endpoint. It would tell the agent exactly which fault was injected - the
answer key rather than evidence - so the agent has to infer faults from
symptoms, like it would for a real incident.
"""

import json
import time

import httpx

from . import docker_tools
from .prometheus import Prometheus

# Rough cap per tool result. Tool output is re-sent to the model on every
# later turn, so one 200 KB log dump would crowd out everything else (and
# cost money on each step). 6k chars is roughly 1.5k tokens.
MAX_RESULT_CHARS = 6000

# Public paths only (the ones nginx serves to real users). Each answers a
# different question: "/" = is the request path through nginx to the app
# working, "/health" = is the app process alive, "/db-check" = can the app
# reach Postgres. Comparing them is how you tell app-vs-database faults apart.
ENDPOINT_ALLOWLIST = ("/", "/health", "/db-check")

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_alerts",
            "description": "List active Prometheus alerts (firing and pending) with labels, annotations and when they became active.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_scrape_targets",
            "description": "Health of every Prometheus scrape target (job, up/down, last scrape error).",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_prometheus",
            "description": "Run an instant PromQL query and return the current value of each series.",
            "parameters": {
                "type": "object",
                "properties": {"promql": {"type": "string", "description": "PromQL expression"}},
                "required": ["promql"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_prometheus_range",
            "description": "Run a PromQL query over the recent past to see how a metric changed (when did it start, is it recovering).",
            "parameters": {
                "type": "object",
                "properties": {
                    "promql": {"type": "string"},
                    "minutes": {"type": "integer", "description": "How far back to look, 1-30", "default": 10},
                    "step_seconds": {"type": "integer", "description": "Resolution, 5-300", "default": 30},
                },
                "required": ["promql"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_service_status",
            "description": "docker compose ps for every service: running/exited, health, status text, exit code.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_service_logs",
            "description": "Recent logs (with timestamps) from one docker compose service.",
            "parameters": {
                "type": "object",
                "properties": {
                    "service": {"type": "string", "description": "Compose service name, e.g. webapp, db, nginx"},
                    "since_minutes": {"type": "integer", "description": "1-30", "default": 10},
                    "tail": {"type": "integer", "description": "Max lines, 1-300", "default": 100},
                },
                "required": ["service"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_endpoint",
            "description": "HTTP GET a public path through nginx and report status code, latency and body. "
            "'/' = request path works, '/health' = app process alive (no DB), '/db-check' = app can query Postgres.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "enum": list(ENDPOINT_ALLOWLIST)}},
                "required": ["path"],
            },
        },
    },
]


def truncate(text: str, limit: int = MAX_RESULT_CHARS, keep: str = "head") -> str:
    if len(text) <= limit:
        return text
    marker = f"\n...[truncated {len(text) - limit} chars]...\n"
    # Logs keep the tail (the newest lines are usually the relevant ones);
    # everything else keeps the head.
    return marker + text[-limit:] if keep == "tail" else text[:limit] + marker


def _clamp(value, lo: int, hi: int, default: int) -> int:
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


def _downsample(series: list[dict], max_points: int = 40) -> list[dict]:
    """Keeps range results readable: every Nth point plus the last one."""
    for s in series:
        values = s.get("values")
        if values and len(values) > max_points:
            stride = -(-len(values) // max_points)
            s["values"] = values[::stride] + ([values[-1]] if (len(values) - 1) % stride else [])
    return series


class Toolbox:
    def __init__(self, prom: Prometheus, webapp_url: str, http: httpx.Client | None = None):
        self.prom = prom
        self.webapp_url = webapp_url
        self.http = http or httpx.Client(timeout=10.0)

    def dispatch(self, name: str, raw_args: str | dict | None) -> str:
        """Runs one tool call and returns its result as a string. Errors
        (bad args, Prometheus down, unknown service...) come back as
        {"error": ...} instead of raising: the model can read the message
        and try something else, the same way you'd fix a typo'd command."""
        try:
            args = json.loads(raw_args or "{}") if isinstance(raw_args, str) else dict(raw_args or {})
            handler = getattr(self, f"_tool_{name}", None)
            if handler is None:
                raise ValueError(f"unknown tool {name!r}")
            result = handler(**args)
        except TypeError as exc:
            result = {"error": f"bad arguments for {name}: {exc}"}
        except Exception as exc:  # noqa: BLE001 - every failure is reported to the model
            result = {"error": str(exc)}
        if isinstance(result, str):
            return truncate(result, keep="tail")
        return truncate(json.dumps(result, default=str))

    # --- tool implementations (names must match TOOL_SCHEMAS) ---

    def _tool_get_alerts(self):
        return [
            {
                "alertname": a["labels"].get("alertname"),
                "state": a.get("state"),
                "activeAt": a.get("activeAt"),
                "labels": a.get("labels"),
                "summary": a.get("annotations", {}).get("summary"),
                "value": a.get("value"),
            }
            for a in self.prom.alerts()
        ]

    def _tool_get_scrape_targets(self):
        return self.prom.targets()

    def _tool_query_prometheus(self, promql: str):
        return self.prom.query(promql)

    def _tool_query_prometheus_range(self, promql: str, minutes=10, step_seconds=30):
        minutes = _clamp(minutes, 1, 30, 10)
        step = _clamp(step_seconds, 5, 300, 30)
        return _downsample(self.prom.query_range(promql, minutes, step))

    def _tool_get_service_status(self):
        return docker_tools.service_status()

    def _tool_get_service_logs(self, service: str, since_minutes=10, tail=100):
        logs = docker_tools.service_logs(
            service, _clamp(since_minutes, 1, 30, 10), _clamp(tail, 1, 300, 100)
        )
        return logs or f"(no log lines from {service} in that window)"

    def _tool_check_endpoint(self, path: str):
        if path not in ENDPOINT_ALLOWLIST:
            raise ValueError(f"path must be one of {ENDPOINT_ALLOWLIST}")
        started = time.perf_counter()
        try:
            resp = self.http.get(self.webapp_url + path)
        except httpx.HTTPError as exc:
            # Connection refused here means nginx itself is down (it's the
            # only published port) - a meaningful result, not a tool bug.
            return {"path": path, "error": f"{type(exc).__name__}: {exc}"}
        return {
            "path": path,
            "status": resp.status_code,
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
            "body": resp.text[:500],
        }
