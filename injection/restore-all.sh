#!/usr/bin/env bash
# Brings the whole stack back to a known-healthy baseline, regardless of
# which scenario(s) were run or whether they exited cleanly. Useful for
# re-running scenarios back to back, and later for Phase 4 (the agent needs
# a clean baseline to diagnose against between test runs).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./lib.sh
source "$SCRIPT_DIR/lib.sh"

load_env

log_step "clearing any webapp faults"
webapp_admin POST /admin/fault/clear >/dev/null 2>&1 || log_warn "could not clear webapp faults (is the stack up?)"

log_step "ensuring every service is running (no-op if already up)"
compose start db webapp nginx nginx-exporter postgres-exporter cadvisor prometheus grafana >/dev/null 2>&1 || true

log_step "terminating any lingering injected Postgres connections"
compose exec -T -e PGPASSWORD="$POSTGRES_PASSWORD" db \
    psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -h localhost -tAc \
    "select pg_terminate_backend(pid) from pg_stat_activity where pid <> pg_backend_pid() and query ilike 'select pg_sleep%';" \
    >/dev/null 2>&1 || true

log_step "killing any stray loadgen processes"
pkill -f "injection/loadgen.sh" 2>/dev/null || true

log_ok "restore complete. Current state:"
compose ps
