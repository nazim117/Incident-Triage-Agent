"""
CLI entry point:

    python -m triage snapshot   # tools only, no LLM: alerts, targets, services
    python -m triage once       # triage whatever is firing right now
    python -m triage watch      # keep polling, triage each new incident

Run from the agent/ directory (so Python finds the `triage` package).
"""

import argparse
import json
import sys
import time

from openai import OpenAI

from . import docker_tools
from .agent import TriageAgent
from .config import load_settings
from .prometheus import Prometheus, PrometheusError
from .report import render_markdown, write_report
from .tools import Toolbox
from .watcher import Watcher


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def cmd_snapshot(prom: Prometheus) -> int:
    # Everything the agent can see, without spending an API call - the
    # quickest way to check the tools work (and what a human would glance
    # at first anyway).
    sections = {
        "alerts": lambda: [
            {"alertname": a["labels"].get("alertname"), "state": a["state"], "labels": a["labels"]}
            for a in prom.alerts()
        ],
        "scrape targets": prom.targets,
        "services": docker_tools.service_status,
    }
    for title, fetch in sections.items():
        print(f"== {title}")
        try:
            print(json.dumps(fetch(), indent=2))
        except Exception as exc:  # noqa: BLE001
            print(f"error: {exc}")
    return 0


def build_agent(settings, prom: Prometheus, model: str | None = None, log=log) -> TriageAgent:
    """Raises ValueError without an API key (rather than exiting) so the
    eval harness can reuse it."""
    if not settings.deepseek_api_key:
        raise ValueError("DEEPSEEK_API_KEY is not set - add it to .env (see .env.example)")
    # DeepSeek speaks the OpenAI API, so the official openai SDK works as-is:
    # only the base URL and key differ.
    client = OpenAI(api_key=settings.deepseek_api_key, base_url=settings.deepseek_base_url)
    toolbox = Toolbox(prom, settings.webapp_url)
    return TriageAgent(client, model or settings.deepseek_model, toolbox, settings.max_steps, log=log)


def triage_and_report(agent: TriageAgent, alerts: list[dict]) -> None:
    names = ", ".join(sorted({a["labels"].get("alertname", "?") for a in alerts}))
    log(f"triaging: {names}")
    diagnosis = agent.triage(alerts)
    path = write_report(diagnosis)
    print("\n" + render_markdown(diagnosis), flush=True)
    log(f"report written to {path}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="triage", description="Incident triage agent (Phase 4)")
    parser.add_argument("command", choices=["snapshot", "once", "watch"])
    args = parser.parse_args(argv)

    settings = load_settings()
    prom = Prometheus(settings.prometheus_url)

    if args.command == "snapshot":
        return cmd_snapshot(prom)

    try:
        agent = build_agent(settings, prom)
    except ValueError as exc:
        sys.exit(str(exc))

    if args.command == "once":
        try:
            firing = prom.firing_alerts()
        except PrometheusError as exc:
            sys.exit(str(exc))
        if not firing:
            log("nothing firing")
            return 0
        triage_and_report(agent, firing)
        return 0

    watcher = Watcher(
        fetch_firing=prom.firing_alerts,
        handle_incident=lambda alerts: triage_and_report(agent, alerts),
        poll_seconds=settings.poll_seconds,
        settle_seconds=settings.settle_seconds,
        log=log,
    )
    try:
        watcher.run()
    except KeyboardInterrupt:
        log("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
