#!/usr/bin/env bash
# Scenario 1/6 -> TargetDown (monitoring/alerts.yml): `up == 0` for 15s.
#
# TargetDown fires whenever Prometheus can't scrape a target at all - the
# exporter/process itself is gone, as opposed to DatabaseDown (scenario 4),
# where the exporter is up but reports its OWN dependency as broken. This
# script just stops a target container outright.
#
# Default target is nginx-exporter: lowest blast radius of the four
# scrapeable services. Stopping webapp would also break nginx's upstream and
# obscure the DatabaseConnectionsSaturated scenario's /db-check evidence;
# stopping cadvisor would blind the ContainerRestarting scenario if run
# concurrently. --target lets you point it at any of them.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./lib.sh
source "$SCRIPT_DIR/lib.sh"

TARGET="nginx-exporter"
DURATION=45

while [ $# -gt 0 ]; do
    case "$1" in
        --target) TARGET="$2"; shift 2 ;;
        --duration) DURATION="$2"; shift 2 ;;
        *) log_warn "unknown arg: $1"; exit 1 ;;
    esac
done

# Matches the job_name values in monitoring/prometheus.yml - kept here as a
# fixed lookup (rather than parsed at runtime) since that file changes
# rarely; if it ever does, update this map to match.
declare -A JOB_FOR=(
    [nginx-exporter]=nginx
    [postgres-exporter]=postgres
    [cadvisor]=cadvisor
    [webapp]=webapp
)
JOB="${JOB_FOR[$TARGET]:-$TARGET}"

load_env
require_running "$TARGET"

cleanup() {
    log_step "restoring: starting $TARGET"
    compose start "$TARGET" >/dev/null
}
install_cleanup_trap

log_step "stopping $TARGET for ${DURATION}s (Prometheus job: $JOB)"
compose stop "$TARGET" >/dev/null
log_ok "stopped. Watch http://localhost:9090/alerts for TargetDown (job=\"$JOB\") to fire within 15s."

sleep "$DURATION"
