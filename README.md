# Incident-Triage-Agent

Infra incident triage agent project, built in phases.

| Phase | What | Status |
|-------|------|--------|
| 1 | nginx → Flask webapp → Postgres on one Docker network | done |
| 2 | Prometheus, exporters, alert rules, Grafana | done |
| 3 | Failure injection scripts | todo |
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
