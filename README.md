# Incident-Triage-Agent

Infra incident triage agent project, built in phases.

| Phase | What | Status |
|-------|------|--------|
| 1 | nginx → Flask webapp → Postgres on one Docker network | done |
| 2 | Prometheus, exporters, alert rules, Grafana | done |
| 3 | Failure injection scripts | done |
| 4 | Triage agent (alerts + metrics + logs → diagnosis) | todo |
| 5 | Eval harness (inject → triage → score) | todo |

## Run

    cp .env.example .env
    docker compose up --build

| URL | Service |
|-----|---------|
| http://localhost:8080 | app via nginx (`/`, `/health`, `/db-check`) |
| http://localhost:9090 | Prometheus (targets, alerts) |
| http://localhost:3000 | Grafana (admin / `GRAFANA_ADMIN_PASSWORD`), "Triage Overview" dashboard |

Alert rules live in `monitoring/alerts.yml`.

## Failure injection

Requires `FAULT_INJECTION_ENABLED=true` in `.env` (see `.env.example`).

    ./injection/run.sh list
    ./injection/run.sh run webapp-5xx
    ./injection/run.sh status
    ./injection/run.sh restore-all

| Script | Alert |
|--------|-------|
| `01-target-down.sh` | TargetDown |
| `02-webapp-5xx.sh` | WebappHigh5xxRate |
| `03-webapp-latency.sh` | WebappHighLatencyP95 |
| `04-database-down.sh` | DatabaseDown |
| `05-db-connections-saturated.sh` | DatabaseConnectionsSaturated |
| `06-container-restarting.sh` | ContainerRestarting |

Each script cleans up after itself (Ctrl-C included); `restore-all.sh` forces everything back to healthy regardless of what was run.

## Triage agent

A host-side Python process that reads firing alerts from Prometheus, investigates with read-only tools (PromQL, `docker compose ps`/`logs`, HTTP checks through nginx), and writes a diagnosis using DeepSeek. It suggests fixes but never runs them.

    python -m venv .venv    # skip if .venv already exists
    .venv/bin/pip install -r agent/requirements.txt
    # set DEEPSEEK_API_KEY in .env (see .env.example)

    cd agent
    ../.venv/bin/python -m triage snapshot   # what the agent can see, no LLM call
    ../.venv/bin/python -m triage once       # triage whatever is firing now
    ../.venv/bin/python -m triage watch      # poll, triage each new incident once

Reports go to `reports/` as `.md` and `.json` (the JSON includes every tool call). Tests: `cd agent && ../.venv/bin/pytest`.

Try it: run `watch` in one terminal and `./injection/run.sh run database-down -- --duration 120` in another.

## Evals

Runs each injection scenario, waits for its alert, lets the agent triage, scores the diagnosis against the known root cause, then restores the stack.

    cd agent
    ../.venv/bin/python -m triage.eval list
    ../.venv/bin/python -m triage.eval run                        # all 6 cases, ~15 min
    ../.venv/bin/python -m triage.eval run --only database-down --repeat 3
    ../.venv/bin/python -m triage.eval score ../evals/<run-id>    # re-score after editing cases.py

A full run makes 6 agent investigations' worth of DeepSeek calls. Results go to `evals/<run-id>/`: `scorecard.md`, `results.json`, and one `.json` + `.log` per case. Checks are in `agent/triage/eval/cases.py`. The harness drives the stack with `docker compose`, so the current Docker context must be the daemon the stack runs on; preflight refuses to start otherwise.
