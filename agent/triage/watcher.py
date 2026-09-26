"""
Watch mode: poll Prometheus for firing alerts and triage each new incident
once.

Two details matter more than the polling itself:

  - Settle window. Incidents cascade: stop the db and DatabaseDown fires
    after its 15s `for:`, but webapp errors may push WebappHigh5xxRate over
    its 30s `for:` a little later. Triaging the instant the first alert
    fires would produce one report per symptom. Waiting SETTLE_SECONDS and
    re-reading the alerts lets related alerts land in the same incident.

  - Dedupe. An alert stays "firing" for as long as the problem lasts, so a
    naive "triage whatever is firing" loop would call the LLM every poll.
    Instead the watcher remembers which alerts the current incident already
    covered and only triages again when a NEW alert shows up. An alert
    resolving (the set shrinking) is recovery, not a new problem. Once
    nothing is firing, the incident is over and the memory resets.
"""

import time
from collections.abc import Callable

from .prometheus import PrometheusError


def alert_key(alert: dict) -> tuple:
    """Identity of one alert instance: its full label set (which includes
    alertname). Two TargetDown alerts for different jobs are different
    alerts; the same one on the next poll is the same alert."""
    return tuple(sorted(alert.get("labels", {}).items()))


class Watcher:
    def __init__(
        self,
        fetch_firing: Callable[[], list[dict]],
        handle_incident: Callable[[list[dict]], None],
        poll_seconds: float,
        settle_seconds: float,
        sleep: Callable[[float], None] = time.sleep,
        log: Callable[[str], None] = print,
    ):
        self.fetch_firing = fetch_firing
        self.handle_incident = handle_incident
        self.poll_seconds = poll_seconds
        self.settle_seconds = settle_seconds
        self.sleep = sleep
        self.log = log
        self.covered: set[tuple] = set()

    def tick(self) -> None:
        try:
            firing = self.fetch_firing()
        except PrometheusError as exc:
            # Prometheus being down is itself worth knowing, but the watcher
            # can't triage without it - keep retrying rather than exiting.
            self.log(f"[watch] {exc}; retrying in {self.poll_seconds:g}s")
            return

        if not firing:
            if self.covered:
                self.log("[watch] all alerts resolved; incident closed")
                self.covered.clear()
            return

        new = {alert_key(a) for a in firing} - self.covered
        if not new:
            return

        names = sorted({dict(k).get("alertname", "?") for k in new})
        self.log(f"[watch] new alert(s): {', '.join(names)}; waiting {self.settle_seconds:g}s for related alerts")
        self.sleep(self.settle_seconds)
        try:
            firing = self.fetch_firing()
        except PrometheusError as exc:
            self.log(f"[watch] {exc}; will retry")
            return
        if not firing:
            self.log("[watch] alerts resolved during settle window; skipping")
            return

        # Mark covered BEFORE triaging: if the LLM call fails (bad API key,
        # network), retrying on every poll would just repeat the failure.
        # `python -m triage once` re-runs a triage on demand.
        self.covered |= new | {alert_key(a) for a in firing}
        try:
            self.handle_incident(firing)
        except Exception as exc:  # noqa: BLE001 - keep watching whatever went wrong
            self.log(f"[watch] triage failed: {type(exc).__name__}: {exc}")

    def run(self) -> None:
        self.log(f"[watch] polling every {self.poll_seconds:g}s (Ctrl-C to stop)")
        while True:
            self.tick()
            self.sleep(self.poll_seconds)
