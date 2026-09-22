#!/usr/bin/env bash
# Scenario 5/6 -> DatabaseConnectionsSaturated (monitoring/alerts.yml):
# sum(pg_stat_activity_count) / max_connections above 80%, sustained 30s.
#
# Postgres's port is deliberately not published to the host (see
# docker-compose.yml), so this runs entirely inside the `db` container via
# `docker compose exec` rather than connecting from the host or spinning up
# a throwaway container. Each held connection is a real `psql` backend
# running `pg_sleep`, launched detached (`exec -d`) so N of them can be
# fired off without blocking this script on N foreground processes; each
# stays counted in pg_stat_activity for the full sleep duration.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./lib.sh
source "$SCRIPT_DIR/lib.sh"

# 0.85, not the 0.8 threshold itself - postgres-exporter and webapp already
# hold a handful of baseline connections, so a small margin above the
# threshold avoids having to account for those exactly.
FRACTION=0.85
HOLD_SECONDS=60

while [ $# -gt 0 ]; do
    case "$1" in
        --fraction) FRACTION="$2"; shift 2 ;;
        --hold-seconds) HOLD_SECONDS="$2"; shift 2 ;;
        *) log_warn "unknown arg: $1"; exit 1 ;;
    esac
done

load_env
require_running db

psql_exec() {
    compose exec -T -e PGPASSWORD="$POSTGRES_PASSWORD" db \
        psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -h localhost "$@"
}

cleanup() {
    log_step "restoring: terminating any lingering injected connections"
    psql_exec -tAc \
        "select pg_terminate_backend(pid) from pg_stat_activity where pid <> pg_backend_pid() and query ilike 'select pg_sleep%';" \
        >/dev/null || true
}
install_cleanup_trap

MAX_CONN="$(psql_exec -tAc 'show max_connections;' | tr -d '[:space:]')"
N=$(awk -v m="$MAX_CONN" -v f="$FRACTION" 'BEGIN { printf "%d", (m * f) + 0.999 }')
log_step "max_connections=$MAX_CONN, opening $N held connections (${FRACTION} of max) for ${HOLD_SECONDS}s"

for i in $(seq 1 "$N"); do
    compose exec -d -e PGPASSWORD="$POSTGRES_PASSWORD" db \
        psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -h localhost \
        -c "select pg_sleep($HOLD_SECONDS);" >/dev/null
done

log_ok "opened $N connections. Watch http://localhost:9090/alerts for DatabaseConnectionsSaturated to fire within 30s."
sleep "$HOLD_SECONDS"
