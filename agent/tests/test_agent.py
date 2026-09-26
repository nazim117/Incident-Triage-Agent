import json
from types import SimpleNamespace as NS

from triage.agent import TriageAgent, parse_json_object

ALERTS = [{"labels": {"alertname": "DatabaseDown", "job": "postgres"}, "activeAt": "t",
           "annotations": {"summary": "Postgres is unreachable"}}]

FINAL = {
    "summary": "db is stopped",
    "root_cause": "db container exited",
    "affected_services": ["db", "webapp"],
    "evidence": [{"source": "pg_up", "detail": "0"}],
    "confidence": "high",
    "suggested_remediation": [{"command": "docker compose start db", "why": "start it"}],
    "notes": "",
}


def tool_reply(*calls):
    return NS(content=None, tool_calls=[
        NS(id=f"call{i}", function=NS(name=name, arguments=args)) for i, (name, args) in enumerate(calls)
    ])


def text_reply(text):
    return NS(content=text, tool_calls=None)


class FakeClient:
    """Plays back scripted replies and records every request."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(json.loads(json.dumps(kwargs, default=str)))
        return NS(choices=[NS(message=self.replies.pop(0))])


class FakeToolbox:
    def __init__(self):
        self.calls = []

    def dispatch(self, name, args):
        self.calls.append((name, args))
        return '{"value": "0"}'


def make_agent(replies, max_steps=12):
    client, toolbox = FakeClient(replies), FakeToolbox()
    return TriageAgent(client, "test-model", toolbox, max_steps, log=lambda m: None), client, toolbox


def test_tool_loop_then_final_json():
    agent, client, toolbox = make_agent([
        tool_reply(("query_prometheus", '{"promql": "pg_up"}'), ("get_service_status", "{}")),
        text_reply("```json\n" + json.dumps(FINAL) + "\n```"),
    ])
    d = agent.triage(ALERTS)

    assert toolbox.calls == [("query_prometheus", '{"promql": "pg_up"}'), ("get_service_status", "{}")]
    assert d.root_cause == "db container exited" and d.confidence == "high"
    assert d.suggested_remediation[0]["command"] == "docker compose start db"
    assert d.steps == 2 and len(d.tool_calls) == 2
    # Second request must carry the assistant tool_calls turn followed by one
    # tool message per call id, or the real API rejects it.
    msgs = client.requests[1]["messages"]
    assert msgs[2]["role"] == "assistant" and len(msgs[2]["tool_calls"]) == 2
    assert [m["tool_call_id"] for m in msgs[3:5]] == ["call0", "call1"]
    assert "DatabaseDown" in msgs[1]["content"]


def test_step_limit_forces_final_answer():
    agent, client, _ = make_agent(
        [tool_reply(("get_alerts", "{}")), tool_reply(("get_alerts", "{}")), text_reply(json.dumps(FINAL))],
        max_steps=2,
    )
    d = agent.triage(ALERTS)
    assert d.summary == "db is stopped"
    assert client.requests[-1]["tool_choice"] == "none"


def test_invalid_json_is_retried_once_then_degrades():
    agent, client, _ = make_agent([text_reply("I think the db is down."), text_reply("still prose")])
    d = agent.triage(ALERTS)
    assert d.confidence == "low" and "still prose" in d.notes
    assert len(client.requests) == 2


def test_off_schema_values_are_coerced():
    odd = {"summary": "x", "affected_services": "db", "evidence": ["pg_up=0"],
           "suggested_remediation": "docker compose start db", "confidence": "HIGH"}
    agent, _, _ = make_agent([text_reply(json.dumps(odd))])
    d = agent.triage(ALERTS)
    assert d.affected_services == ["db"] and d.confidence == "high"
    assert d.evidence == [{"source": "", "detail": "pg_up=0"}]
    assert d.suggested_remediation == [{"command": "docker compose start db", "why": ""}]


def test_parse_json_object():
    assert parse_json_object('Here you go: {"a": 1} hope that helps') == {"a": 1}
    assert parse_json_object("```\n{\"a\": 1}\n```") == {"a": 1}
    assert parse_json_object("[1, 2]") is None
    assert parse_json_object(None) is None
