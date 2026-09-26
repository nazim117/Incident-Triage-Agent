from triage.prometheus import PrometheusError
from triage.watcher import Watcher, alert_key


def alert(name, **labels):
    return {"labels": {"alertname": name, **labels}, "state": "firing"}


class Harness:
    """Drives a Watcher with a scripted sequence of Prometheus responses."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.incidents = []
        self.logs = []
        self.watcher = Watcher(self._fetch, self.incidents.append, poll_seconds=0, settle_seconds=0,
                               sleep=lambda s: None, log=self.logs.append)

    def _fetch(self):
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    def ticks(self, n):
        for _ in range(n):
            self.watcher.tick()


def names(incident):
    return sorted(a["labels"]["alertname"] for a in incident)


def test_cascade_during_settle_is_one_incident():
    db, fivexx = alert("DatabaseDown"), alert("WebappHigh5xxRate")
    # tick 1: sees db, settles, re-reads db+5xx. Following ticks: still firing.
    h = Harness([[db], [db, fivexx], [db, fivexx], [db, fivexx]])
    h.ticks(3)
    assert [names(i) for i in h.incidents] == [["DatabaseDown", "WebappHigh5xxRate"]]


def test_new_alert_later_triggers_second_triage_but_shrinking_does_not():
    a, b = alert("DatabaseDown"), alert("TargetDown", job="nginx")
    h = Harness([[a], [a], [a, b], [a, b], [b], [b]])
    h.ticks(4)
    assert [names(i) for i in h.incidents] == [["DatabaseDown"], ["DatabaseDown", "TargetDown"]]


def test_resolve_resets_so_recurrence_is_triaged_again():
    a = alert("DatabaseDown")
    h = Harness([[a], [a], [], [a], [a]])
    h.ticks(3)
    assert len(h.incidents) == 2
    assert any("resolved" in m for m in h.logs)


def test_resolved_during_settle_skips_triage():
    h = Harness([[alert("ContainerRestarting")], []])
    h.ticks(1)
    assert h.incidents == []


def test_prometheus_errors_and_triage_errors_do_not_stop_the_loop():
    a = alert("DatabaseDown")
    h = Harness([PrometheusError("down"), [a], [a]])

    def boom(_):
        raise RuntimeError("api key invalid")

    h.watcher.handle_incident = boom
    h.ticks(2)
    assert any("down" in m for m in h.logs) and any("triage failed" in m for m in h.logs)
    assert alert_key(a) in h.watcher.covered


def test_alert_key_distinguishes_labels():
    assert alert_key(alert("TargetDown", job="nginx")) != alert_key(alert("TargetDown", job="webapp"))
