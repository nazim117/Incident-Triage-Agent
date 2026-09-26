"""
Thin read-only client for the Prometheus HTTP API.

Prometheus is both the agent's trigger (its /api/v1/alerts endpoint lists
firing alerts - there's no Alertmanager in this stack to push webhooks) and
its main evidence source (PromQL over the metrics Phase 2 collects). Every
call here is a GET: the agent can look, never change.
"""

import time

import httpx


class PrometheusError(RuntimeError):
    pass


class Prometheus:
    def __init__(self, base_url: str, timeout: float = 10.0):
        self._client = httpx.Client(base_url=base_url, timeout=timeout)

    def _get(self, path: str, **params) -> dict | list:
        try:
            resp = self._client.get(path, params=params)
        except httpx.HTTPError as exc:
            raise PrometheusError(f"cannot reach Prometheus: {exc}") from exc
        # Prometheus answers bad PromQL with HTTP 400 and a JSON body that
        # explains the problem - pass that message on rather than a bare
        # status code, so the model can fix its query and retry.
        try:
            body = resp.json()
        except ValueError:
            raise PrometheusError(f"HTTP {resp.status_code}: {resp.text[:200]}")
        if body.get("status") != "success":
            raise PrometheusError(f"{body.get('errorType', 'error')}: {body.get('error', body)}")
        return body["data"]

    def alerts(self) -> list[dict]:
        """Active alerts: state is "pending" (condition true, `for:` not yet
        elapsed) or "firing"."""
        return self._get("/api/v1/alerts")["alerts"]

    def firing_alerts(self) -> list[dict]:
        return [a for a in self.alerts() if a.get("state") == "firing"]

    def targets(self) -> list[dict]:
        active = self._get("/api/v1/targets", state="active")["activeTargets"]
        return [
            {
                "job": t["labels"].get("job"),
                "instance": t["labels"].get("instance"),
                "health": t.get("health"),
                "lastError": t.get("lastError") or None,
                "lastScrape": t.get("lastScrape"),
            }
            for t in active
        ]

    def query(self, promql: str) -> list[dict]:
        data = self._get("/api/v1/query", query=promql)
        return _simplify(data)

    def query_range(self, promql: str, minutes: int, step_seconds: int) -> list[dict]:
        end = time.time()
        data = self._get(
            "/api/v1/query_range",
            query=promql,
            start=end - minutes * 60,
            end=end,
            step=step_seconds,
        )
        return _simplify(data)


def _simplify(data: dict) -> list[dict]:
    """Flattens Prometheus's result envelope into {"labels", "value"} or
    {"labels", "values"} dicts with short timestamps - less noise for the
    model to read than the raw API shape."""
    kind = data.get("resultType")
    result = data.get("result")
    if kind == "scalar" or kind == "string":
        return [{"labels": {}, "value": result[1]}]
    out = []
    for series in result:
        entry = {"labels": series.get("metric", {})}
        if "value" in series:
            entry["value"] = series["value"][1]
        if "values" in series:
            entry["values"] = [[_hhmmss(ts), v] for ts, v in series["values"]]
        out.append(entry)
    return out


def _hhmmss(ts: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(float(ts)))
