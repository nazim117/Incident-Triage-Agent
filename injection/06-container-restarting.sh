#!/usr/bin/env bash
# Scenario 6/6 -> ContainerRestarting (monitoring/alerts.yml):
# changes(container_start_time_seconds[5m]) > 2, i.e. more than 2 restarts
# in a 5-minute window.
#
# Plain restart loop is enough here - no need to simulate a real crash loop
# (e.g. killing the process inside the container); cAdvisor observes
# container_start_time_seconds changing regardless of why the container
# restarted, and 5s scrape interval reliably catches each one.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./lib.sh
source "$SCRIPT_DIR/lib.sh"

# nginx-exporter again: restarts in ~1-2s and carries no state, so churning
# it doesn't disrupt anything else running concurrently.
TARGET="nginx-exporter"
COUNT=3
INTERVAL=15

while [ $# -gt 0 ]; do
    case "$1" in
        --target) TARGET="$2"; shift 2 ;;
        --count) COUNT="$2"; shift 2 ;;
        --interval) INTERVAL="$2"; shift 2 ;;
        *) log_warn "unknown arg: $1"; exit 1 ;;
    esac
done

load_env
require_running "$TARGET"

cleanup() {
    log_step "restoring: ensuring $TARGET is running"
    compose start "$TARGET" >/dev/null 2>&1 || true
}
install_cleanup_trap

log_step "restarting $TARGET $COUNT times, ${INTERVAL}s apart (well within the 5m window)"
for i in $(seq 1 "$COUNT"); do
    log_step "restart $i/$COUNT"
    compose restart "$TARGET" >/dev/null
    sleep "$INTERVAL"
done

log_ok "done. Watch http://localhost:9090/alerts for ContainerRestarting (labelled by image, e.g. nginx/nginx-prometheus-exporter)."
