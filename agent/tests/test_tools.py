import json

import httpx
import pytest

from triage import docker_tools
from triage.tools import MAX_RESULT_CHARS, TOOL_SCHEMAS, Toolbox, _downsample, truncate


class FakeProm:
    def __init__(self):
        self.calls = []

    def alerts(self):
        return [{"labels": {"alertname": "DatabaseDown", "job": "postgres"}, "state": "firing",
                 "activeAt": "t", "annotations": {"summary": "s"}, "value": "0"}]

    def query(self, promql):
        self.calls.append(promql)
        return [{"labels": {}, "value": "1"}]

    def query_range(self, promql, minutes, step):
        self.calls.append((promql, minutes, step))
        return [{"labels": {}, "values": [[str(i), "1"] for i in range(200)]}]


@pytest.fixture
def toolbox(monkeypatch):
    monkeypatch.setattr(docker_tools, "compose_services", lambda: ("db", "webapp", "nginx"))
    http = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(503, text="db down")))
    return Toolbox(FakeProm(), "http://app", http=http)


def test_every_schema_has_a_handler(toolbox):
    for schema in TOOL_SCHEMAS:
        assert hasattr(toolbox, "_tool_" + schema["function"]["name"])


def test_unknown_service_is_rejected_without_running_docker(toolbox, monkeypatch):
    monkeypatch.setattr(docker_tools, "_compose", lambda *a: pytest.fail("docker must not run"))
    out = json.loads(toolbox.dispatch("get_service_logs", '{"service": "--volumes"}'))
    assert "unknown service" in out["error"]


def test_log_arguments_are_clamped(toolbox, monkeypatch):
    seen = {}
    monkeypatch.setattr(docker_tools, "_compose", lambda *a: seen.setdefault("args", a) and "line")
    toolbox.dispatch("get_service_logs", '{"service": "db", "since_minutes": 9999, "tail": -5}')
    args = seen["args"]
    assert args[args.index("--since") + 1] == "30m"
    assert args[args.index("--tail") + 1] == "1"
    assert args[-1] == "db"


def test_endpoint_allowlist(toolbox):
    assert "must be one of" in toolbox.dispatch("check_endpoint", '{"path": "/admin/fault/status"}')
    out = json.loads(toolbox.dispatch("check_endpoint", '{"path": "/db-check"}'))
    assert out["status"] == 503 and out["body"] == "db down"


def test_errors_are_returned_not_raised(toolbox):
    assert "unknown tool" in toolbox.dispatch("rm_rf", "{}")
    assert "bad arguments" in toolbox.dispatch("get_alerts", '{"extra": 1}')
    assert "error" in json.loads(toolbox.dispatch("query_prometheus", "not json"))


def test_range_query_is_clamped_and_downsampled(toolbox):
    out = json.loads(toolbox.dispatch("query_prometheus_range", '{"promql": "up", "minutes": 500, "step_seconds": 1}'))
    assert toolbox.prom.calls[-1] == ("up", 30, 5)
    values = out[0]["values"]
    assert len(values) <= 41 and values[-1][0] == "199"


def test_truncate_keeps_head_or_tail():
    text = "a" * MAX_RESULT_CHARS + "END"
    assert truncate(text).startswith("a") and "truncated 3 chars" in truncate(text)
    assert truncate(text, keep="tail").endswith("END")
    assert truncate("short") == "short"


def test_downsample_leaves_short_series_alone():
    series = [{"values": [[1, "x"], [2, "y"]]}]
    assert _downsample(series)[0]["values"] == [[1, "x"], [2, "y"]]
