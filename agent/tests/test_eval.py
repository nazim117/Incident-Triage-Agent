import re
import signal
from pathlib import Path

import pytest

from triage.agent import Diagnosis
from triage.config import REPO_ROOT
from triage.eval import runner as runner_mod
from triage.eval.cases import CASES, Case, select
from triage.eval.runner import Runner
from triage.eval.scoring import ALERT_NOT_FIRED, ERROR, SCORED, passed, render_scorecard, score

DB_CASE = next(c for c in CASES if c.name == "database-down")
T_CASE = next(c for c in CASES if c.name == "target-down")


def diag(**kw):
    base = {"summary": "", "root_cause": "", "affected_services": [], "suggested_remediation": [], "notes": ""}
    return {**base, **kw}


# --- scoring ---

def test_correct_diagnosis_passes():
    d = diag(root_cause="The db container was stopped (exited 0)", affected_services=["db", "webapp"],
             suggested_remediation=[{"command": "docker compose start db", "why": ""}])
    checks = score(DB_CASE, d)
    assert checks == {"valid_json": True, "root_service": True, "root_cause": True,
                      "remediation": True, "no_false_blame": None}
    assert passed(checks)


def test_each_check_can_fail():
    d = diag(root_cause="nginx is down", affected_services=["nginx", "db"],
             suggested_remediation=[{"command": "docker compose restart nginx"}],
             notes="Model did not return valid JSON. Raw reply: ...")
    checks = score(T_CASE, d)
    assert checks == {"valid_json": False, "root_service": False, "root_cause": False,
                      "remediation": False, "no_false_blame": False}
    assert not passed(checks)


def test_not_applicable_checks_are_ignored():
    case = next(c for c in CASES if c.name == "container-restarting")
    checks = score(case, diag(root_cause="nginx-exporter restarted 3 times", affected_services=["nginx-exporter"]))
    assert checks["remediation"] is None and checks["no_false_blame"] is None
    assert passed(checks)


def test_all_root_cause_patterns_must_match():
    # Mentions the exporter but not what happened to it.
    d = diag(root_cause="something about nginx-exporter", affected_services=["nginx-exporter"],
             suggested_remediation=[{"command": "docker compose start nginx-exporter"}])
    assert score(T_CASE, d)["root_cause"] is False


def test_scorecard_renders_all_outcomes():
    d = Diagnosis(alerts=[], confidence="high", steps=3, prompt_tokens=100, completion_tokens=20).to_dict()
    results = [
        {"case": "a", "label": "a-r1", "outcome": SCORED, "passed": True, "checks": {"x": True}, "diagnosis": d},
        {"case": "a", "label": "a-r2", "outcome": SCORED, "passed": False, "checks": {"x": False}, "diagnosis": d},
        {"case": "b", "label": "b", "outcome": ALERT_NOT_FIRED, "passed": False, "checks": {}, "diagnosis": None,
         "error": "never fired"},
    ]
    md = render_scorecard(results, {"run_id": "r", "model": "m", "wall_seconds": 5})
    assert "| a-r1 | PASS |" in md and "| a-r2 | FAIL | x |" in md and "ALERT_NOT_FIRED" in md
    assert "1/2 (50%)" in md and "Total tokens: 240" in md and "- a: 1/2" in md


# --- cases stay in sync with the rest of the repo ---

def test_cases_match_injection_scenarios_and_alert_rules():
    run_sh = (REPO_ROOT / "injection" / "run.sh").read_text()
    scenarios = set(re.findall(r'"\d+:([\w-]+):', run_sh))
    alerts = set(re.findall(r"alert:\s*(\w+)", (REPO_ROOT / "monitoring" / "alerts.yml").read_text()))
    for c in CASES:
        assert c.scenario in scenarios, c.name
        assert c.expect_alert in alerts, c.name
        for p in (*c.root_cause_patterns, *c.remediation_patterns):
            re.compile(p)


def test_container_restarting_runs_last_and_select_keeps_order():
    assert CASES[-1].name == "container-restarting"
    assert [c.name for c in select("container-restarting,database-down")] == ["database-down", "container-restarting"]
    with pytest.raises(ValueError):
        select("nope")


# --- runner ---

class FakeProm:
    def __init__(self, firing_after=0, alert="DatabaseDown"):
        self.polls = 0
        self.firing_after = firing_after
        self.alert = alert
        self.injected = False

    def alerts(self):
        return self.firing_alerts() if self.injected else []

    def targets(self):
        return [{"health": "up"}]

    def firing_alerts(self):
        if not self.injected:
            return []
        self.polls += 1
        if self.firing_after is None or self.polls <= self.firing_after:
            return []
        return [{"labels": {"alertname": self.alert}, "state": "firing"}]


class FakeProc:
    pid = 4242

    def __init__(self, prom):
        prom.injected = True
        self.waited = False

    def poll(self):
        return None

    def wait(self, timeout=None):
        self.waited = True
        return 0


class FakeAgent:
    def __init__(self, diagnosis=None, exc=None):
        self.diagnosis, self.exc, self.seen = diagnosis, exc, None

    def triage(self, alerts):
        self.seen = alerts
        if self.exc:
            raise self.exc
        return self.diagnosis


def make_runner(tmp_path, prom, agent, events):
    procs = []

    def popen(cmd, **kw):
        events.append(("popen", cmd, kw.get("start_new_session")))
        procs.append(FakeProc(prom))
        return procs[-1]

    r = Runner(prom, agent, tmp_path, settle_seconds=0, preflight_timeout=0, poll_seconds=0,
               sleep=lambda s: None, log=lambda m: None, popen=popen,
               restore=lambda path: events.append(("restore",)),
               service_status=lambda: [{"state": "running"}])
    return r


@pytest.fixture
def killpg(monkeypatch):
    calls = []
    monkeypatch.setattr(runner_mod.os, "killpg", lambda pid, sig: calls.append((pid, sig)))
    return calls


def test_scored_case_tears_down_with_killpg_then_restore(tmp_path, killpg):
    events = []
    good = Diagnosis(alerts=[], root_cause="db was stopped", affected_services=["db"],
                     suggested_remediation=[{"command": "docker compose start db", "why": ""}])
    prom, agent = FakeProm(firing_after=2), FakeAgent(good)
    r = make_runner(tmp_path, prom, agent, events).run_case(DB_CASE, "database-down")

    assert r["outcome"] == SCORED and r["passed"]
    assert agent.seen[0]["labels"]["alertname"] == "DatabaseDown"
    popen = events[0]
    assert popen[1][1:4] == ["run", "database-down", "--"] and popen[1][4:] == list(DB_CASE.args)
    assert popen[2] is True  # own process group, see Scenario docstring
    assert killpg == [(4242, signal.SIGTERM)]
    assert events[-1] == ("restore",)


def test_alert_that_never_fires_is_not_scored(tmp_path, killpg):
    events = []
    case = Case(**{**DB_CASE.__dict__, "alert_timeout": 0})
    r = make_runner(tmp_path, FakeProm(firing_after=None), FakeAgent(), events).run_case(case, "x")
    assert r["outcome"] == ALERT_NOT_FIRED and r["diagnosis"] is None
    assert killpg and events[-1] == ("restore",)


def test_triage_exception_is_a_harness_error_and_still_tears_down(tmp_path, killpg):
    events = []
    r = make_runner(tmp_path, FakeProm(), FakeAgent(exc=RuntimeError("401")), events).run_case(DB_CASE, "x")
    assert r["outcome"] == ERROR and "401" in r["error"]
    assert killpg and events[-1] == ("restore",)


def test_dirty_stack_is_restored_then_skipped(tmp_path, killpg):
    events = []
    prom = FakeProm()
    prom.injected = True  # alerts already firing before injection
    r = make_runner(tmp_path, prom, FakeAgent(), events).run_case(DB_CASE, "x")
    assert r["outcome"] == ERROR and "(alerts active: DatabaseDown)" in r["error"]
    assert events == [("restore",)]  # never injected


def test_run_writes_scorecard_even_when_interrupted(tmp_path, killpg):
    events = []
    runner = make_runner(tmp_path, FakeProm(), FakeAgent(exc=KeyboardInterrupt()), events)
    with pytest.raises(KeyboardInterrupt):
        runner.run([DB_CASE], 1, {"run_id": "t", "model": "m"})
    assert (tmp_path / "scorecard.md").exists() and (tmp_path / "results.json").exists()
    assert events[-1] == ("restore",)
