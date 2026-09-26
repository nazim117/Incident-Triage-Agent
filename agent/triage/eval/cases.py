"""
The benchmark: one Case per injection/ scenario, pairing "how to break it"
with "what a correct diagnosis says".

Each Phase 3 scenario breaks exactly one known thing, which is what makes
it usable as a test case: the ground truth is decided before the agent looks.
The checks are regexes over the agent's JSON (see scoring.py), deliberately
loose - they test that the diagnosis names the right service and mechanism,
not that it uses particular wording.

Durations are much longer than the scripts' defaults so the fault is still
live while the agent investigates (a fault that heals mid-investigation
would make the agent's evidence contradict the alert). The runner stops
each scenario itself as soon as triage finishes.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Case:
    name: str
    scenario: str                      # name in injection/run.sh's SCENARIOS
    args: tuple[str, ...]
    expect_alert: str                  # the alert this scenario exists to fire
    root_service: str                  # compose service that is actually broken
    # Every pattern must match root_cause + summary (case-insensitive); use
    # alternation inside one pattern for "any of these words".
    root_cause_patterns: tuple[str, ...]
    # If set, at least one suggested command must match one of these. Left
    # empty where no command can fix it from the agent's point of view (e.g.
    # the synthetic 5xx fault can only be cleared via the hidden admin API).
    remediation_patterns: tuple[str, ...] = ()
    # Services that are healthy in this scenario and must not be blamed.
    not_affected: tuple[str, ...] = ()
    alert_timeout: float = 150.0


CASES: tuple[Case, ...] = (
    Case(
        name="target-down",
        scenario="target-down",
        args=("--duration", "300"),
        expect_alert="TargetDown",
        root_service="nginx-exporter",
        # The trap here: job="nginx" is the EXPORTER, nginx itself is fine.
        root_cause_patterns=(r"nginx-exporter", r"stop|exit|down|not running"),
        remediation_patterns=(r"docker compose (start|up|restart)\b.*nginx-exporter",),
        not_affected=("db",),
    ),
    Case(
        name="webapp-5xx",
        scenario="webapp-5xx",
        args=("--duration", "300"),
        expect_alert="WebappHigh5xxRate",
        root_service="webapp",
        root_cause_patterns=(r"/work",),
        not_affected=("db",),
    ),
    Case(
        name="webapp-latency",
        scenario="webapp-latency",
        args=("--duration", "300"),
        expect_alert="WebappHighLatencyP95",
        root_service="webapp",
        root_cause_patterns=(r"/work|latenc|slow",),
        not_affected=("db",),
    ),
    Case(
        name="database-down",
        scenario="database-down",
        args=("--duration", "300"),
        expect_alert="DatabaseDown",
        root_service="db",
        root_cause_patterns=(r"stop|exit|down|not running",),
        remediation_patterns=(r"docker compose (start|up|restart)\b.*\bdb\b",),
    ),
    Case(
        name="db-connections-saturated",
        scenario="db-connections-saturated",
        args=("--hold-seconds", "300"),
        expect_alert="DatabaseConnectionsSaturated",
        root_service="db",
        root_cause_patterns=(r"connection", r"pg_sleep|idle|held|hold|leak|long-running"),
        remediation_patterns=(r"pg_terminate_backend|pg_cancel_backend|restart\b.*\bdb\b",),
    ),
    # Last on purpose: ContainerRestarting uses changes(...[5m]), so it keeps
    # firing ~5 minutes after the restarts stop. Anything scheduled after it
    # would have to wait that out in preflight.
    Case(
        name="container-restarting",
        scenario="container-restarting",
        args=(),
        expect_alert="ContainerRestarting",
        root_service="nginx-exporter",
        root_cause_patterns=(r"restart",),
    ),
)


def select(only: str | None) -> list[Case]:
    if not only:
        return list(CASES)
    wanted = [n.strip() for n in only.split(",") if n.strip()]
    by_name = {c.name: c for c in CASES}
    unknown = [n for n in wanted if n not in by_name]
    if unknown:
        raise ValueError(f"unknown case(s) {unknown}; valid: {', '.join(by_name)}")
    # Keep CASES order (container-restarting last) whatever order was typed.
    return [c for c in CASES if c.name in wanted]
