#!/usr/bin/env bash
# Scenario 4/6 -> DatabaseDown (monitoring/alerts.yml): `pg_up == 0` for 15s.
#
# Deliberately distinct from TargetDown (scenario 1): stopping `db` leaves
# postgres-exporter itself running and scrapable (up{job="postgres"} stays
# 1), it just fails to CONNECT to Postgres and reports pg_up=0 - exactly the
# same "process is alive but its dependency is broken" distinction already
# documented in app.py between /health and /db-check. That's why this is a
# separate alert rule rather than folded into TargetDown.
#
# As a side effect this also exercises webapp_db_check_errors_total, since
# webapp's own /db-check will start returning 503 for the duration.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./lib.sh
source "$SCRIPT_DIR/lib.sh"

DURATION=45

while [ $# -gt 0 ]; do
    case "$1" in
        --duration) DURATION="$2"; shift 2 ;;
        *) log_warn "unknown arg: $1"; exit 1 ;;
    esac
done

load_env
require_running db

cleanup() {
    log_step "restoring: starting db"
    compose start db >/dev/null
    # db has a healthcheck (pg_isready, 5s interval / 5 retries) so
    # dependents recover on their own; a short wait just makes the script's
    # own output reflect reality before it exits.
    sleep 5
}
install_cleanup_trap

log_step "stopping db for ${DURATION}s"
compose stop db >/dev/null
log_ok "stopped. Watch http://localhost:9090/alerts for DatabaseDown to fire within 15s."

log_step "confirming webapp sees it: /db-check should now report unreachable"
compose exec -T webapp curl -s http://localhost:8000/db-check || true
echo

sleep "$DURATION"
