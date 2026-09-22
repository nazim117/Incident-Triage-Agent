# Incident-Triage-Agent

Infra incident triage agent project, built in phases.

| Phase | What | Status |
|-------|------|--------|
| 1 | nginx → Flask webapp → Postgres on one Docker network | done |
| 2 | Prometheus, exporters, alert rules, Grafana | done |
| 3 | Failure injection scripts | done |
| 4 | Triage agent (alerts + metrics + logs → diagnosis) | todo |

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
