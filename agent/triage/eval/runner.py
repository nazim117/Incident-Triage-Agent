"""
Drives one eval run: for each case, inject the fault, wait for its alert,
let the agent triage, restore the stack, score.

Most of this file is about making runs FAIR and REPEATABLE, not about the
agent:
  - Preflight waits for a clean stack, so leftovers from the previous case
    (a 5xx rate that hasn't decayed yet, a lingering ContainerRestarting)
    can't leak into the next case's evidence.
  - A case whose alert never fires is recorded as alert_not_fired, not as
    an agent failure - the benchmark didn't happen, so it isn't scored.
  - Teardown always runs (try/finally), including on Ctrl-C, so an aborted
    eval never leaves the db stopped or faults enabled.
"""

import json
import os
import signal
import subprocess
import time
from pathlib import Path

from .. import docker_tools
from ..config import REPO_ROOT
from ..prometheus import PrometheusError
from .cases import Case
from .scoring import ALERT_NOT_FIRED, ERROR, SCORED, passed, render_scorecard, score

RUN_SH = REPO_ROOT / "injection" / "run.sh"
RESTORE_SH = REPO_ROOT / "injection" / "restore-all.sh"


class Scenario:
    """One running injection script.

    Started in its own session (process group) because of how bash handles
    signals: the scripts end with a foreground `sleep 300`, and bash defers
    running its `trap cleanup` until that foreground child exits. SIGTERM to
    bash alone would therefore wait out the whole sleep. Signalling the
    whole group also kills the sleep (plus loadgen and its curls), so bash
    runs cleanup immediately.
    """

    def __init__(self, case: Case, log_path: Path, popen=subprocess.Popen):
        self.case = case
        self.log_path = log_path
        self._popen = popen
        self.proc = None

    def start(self) -> None:
        self._log = open(self.log_path, "a")
        self.proc = self._popen(
            [str(RUN_SH), "run", self.case.scenario, "--", *self.case.args],
            cwd=REPO_ROOT,
            stdout=self._log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    def stop(self, timeout: float = 60) -> None:
        if self.proc is None:
            return
        try:
            if self.proc.poll() is None:
                _killpg(self.proc.pid, signal.SIGTERM)
                try:
                    self.proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    _killpg(self.proc.pid, signal.SIGKILL)
                    self.proc.wait(timeout=10)
        finally:
            self._log.close()


def _killpg(pid: int, sig: int) -> None:
    try:
        os.killpg(pid, sig)  # with start_new_session, pgid == pid
    except ProcessLookupError:
        pass  # already exited


def restore_all(log_path: Path) -> None:
    """Backstop after every case: each script cleans up after itself, but
    restore-all.sh forces a known-good state whatever happened."""
    with open(log_path, "a") as log:
        log.write("\n--- restore-all.sh ---\n")
        log.flush()
        subprocess.run([str(RESTORE_SH)], cwd=REPO_ROOT, stdout=log, stderr=subprocess.STDOUT, timeout=180)


class Runner:
    def __init__(
        self,
        prom,
        agent,
        out_dir: Path,
        settle_seconds: float,
        preflight_timeout: float = 360,
        poll_seconds: float = 5,
        sleep=time.sleep,
        clock=time.monotonic,
        log=print,
        popen=subprocess.Popen,
        restore=restore_all,
        service_status=docker_tools.service_status,
    ):
        self.prom = prom
        self.agent = agent
        self.out_dir = out_dir
        self.settle_seconds = settle_seconds
        self.preflight_timeout = preflight_timeout
        self.poll_seconds = poll_seconds
        self.sleep = sleep
        self.clock = clock
        self.log = log
        self.popen = popen
        self.restore = restore
        self.service_status = service_status
        self._last_dirty = ""

    # --- waiting helpers ---

    def _wait_until(self, predicate, timeout: float) -> bool:
        deadline = self.clock() + timeout
        while True:
            try:
                if predicate():
                    return True
            except (PrometheusError, docker_tools.DockerError) as exc:
                self.log(f"    (retrying: {exc})")
            if self.clock() >= deadline:
                return False
            self.sleep(self.poll_seconds)

    def _dirty_reason(self) -> str:
        """Why the stack isn't clean, or "" if it is."""
        # No alerts at all, pending included: a pending alert is about to
        # fire and would show up in the next case's evidence.
        alerts = self.prom.alerts()
        if alerts:
            return "alerts active: " + ", ".join(sorted({a["labels"].get("alertname", "?") for a in alerts}))
        down = [t.get("job") for t in self.prom.targets() if t["health"] != "up"]
        if down:
            return f"scrape targets down: {', '.join(map(str, down))}"
        # Also catches Prometheus answering on :9090 while `docker compose`
        # points at a DIFFERENT Docker daemon (e.g. Docker Desktop's context
        # vs the native engine) - then the scenarios would break the wrong
        # containers, so refusing to run is the right call.
        stopped = [s.get("service") for s in self.service_status() if s.get("state") != "running"]
        if stopped:
            return f"services not running (per docker compose): {', '.join(map(str, stopped))}"
        return ""

    def _stack_clean(self) -> bool:
        self._last_dirty = self._dirty_reason()
        return not self._last_dirty

    def _alert_firing(self, name: str) -> bool:
        return any(a["labels"].get("alertname") == name for a in self.prom.firing_alerts())

    def preflight(self, log_path: Path) -> bool:
        self.log("  preflight: waiting for a clean stack")
        if self._wait_until(self._stack_clean, self.preflight_timeout):
            return True
        self.log(f"  preflight: not clean ({self._last_dirty}); running restore-all.sh and retrying")
        self.restore(log_path)
        return self._wait_until(self._stack_clean, self.preflight_timeout)

    # --- one case ---

    def run_case(self, case: Case, label: str) -> dict:
        log_path = self.out_dir / f"{label}.log"
        result = {"case": case.name, "label": label, "outcome": ERROR, "checks": {}, "passed": False,
                  "diagnosis": None, "error": "", "scenario_log": log_path.name}

        if not self.preflight(log_path):
            result["error"] = f"stack not clean before injection ({self._last_dirty})"
            return result

        scenario = Scenario(case, log_path, popen=self.popen)
        try:
            self.log(f"  injecting: {case.scenario} {' '.join(case.args)}")
            scenario.start()
            if not self._wait_until(lambda: self._alert_firing(case.expect_alert), case.alert_timeout):
                result["outcome"] = ALERT_NOT_FIRED
                result["error"] = f"{case.expect_alert} did not fire within {case.alert_timeout:g}s"
                return result

            # Same settle idea as watcher.py: give cascading alerts a moment
            # to fire, then triage whatever is firing - exactly what the
            # agent would see in watch mode, collateral alerts included.
            self.log(f"  {case.expect_alert} firing; settling {self.settle_seconds:g}s")
            self.sleep(self.settle_seconds)
            firing = self.prom.firing_alerts()
            if not firing:
                result["error"] = "alerts resolved before triage started"
                return result

            self.log(f"  triaging: {', '.join(sorted({a['labels'].get('alertname', '?') for a in firing}))}")
            try:
                diagnosis = self.agent.triage(firing).to_dict()
            except Exception as exc:  # noqa: BLE001 - API errors etc. are harness errors, not agent answers
                result["error"] = f"triage failed: {type(exc).__name__}: {exc}"
                return result

            result["diagnosis"] = diagnosis
            result["checks"] = score(case, diagnosis)
            result["passed"] = passed(result["checks"])
            result["outcome"] = SCORED
            return result
        finally:
            self.log("  tearing down")
            scenario.stop()
            self.restore(log_path)

    # --- whole run ---

    def run(self, cases: list[Case], repeat: int, meta: dict) -> list[dict]:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        results: list[dict] = []
        started = self.clock()
        try:
            for case in cases:
                for i in range(1, repeat + 1):
                    label = case.name if repeat == 1 else f"{case.name}-r{i}"
                    self.log(f"[{label}]")
                    r = self.run_case(case, label)
                    verdict = ("PASS" if r["passed"] else "FAIL") if r["outcome"] == SCORED else r["outcome"].upper()
                    self.log(f"  -> {verdict} {r['error']}".rstrip())
                    results.append(r)
                    (self.out_dir / f"{label}.json").write_text(json.dumps(r, indent=2, default=str))
        finally:
            # Written even on Ctrl-C, so a partial run still has a scorecard.
            meta = {**meta, "wall_seconds": self.clock() - started}
            write_summary(self.out_dir, results, meta)
        return results


def write_summary(out_dir: Path, results: list[dict], meta: dict) -> Path:
    (out_dir / "results.json").write_text(json.dumps({"meta": meta, "results": results}, indent=2, default=str))
    path = out_dir / "scorecard.md"
    path.write_text(render_scorecard(results, meta))
    return path
