#!/usr/bin/env bash
# Orchestrator for the Phase 3 failure-injection scripts.
#
# Usage:
#   run.sh list                          list scenarios and the alert each covers
#   run.sh run <number|name> [-- args]   run one scenario, forwarding extra args to it
#   run.sh status                        docker compose ps + webapp fault status
#   run.sh restore-all                   force everything back to healthy
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./lib.sh
source "$SCRIPT_DIR/lib.sh"

# number:name:script:alert - single source of truth for `list`, also used to
# resolve `run <number|name>` to a script.
SCENARIOS=(
    "1:target-down:01-target-down.sh:TargetDown"
    "2:webapp-5xx:02-webapp-5xx.sh:WebappHigh5xxRate"
    "3:webapp-latency:03-webapp-latency.sh:WebappHighLatencyP95"
    "4:database-down:04-database-down.sh:DatabaseDown"
    "5:db-connections-saturated:05-db-connections-saturated.sh:DatabaseConnectionsSaturated"
    "6:container-restarting:06-container-restarting.sh:ContainerRestarting"
)

cmd_list() {
    printf '%-3s %-26s %-30s %s\n' "#" "name" "alert" "script"
    for entry in "${SCENARIOS[@]}"; do
        IFS=: read -r num name script alert <<<"$entry"
        printf '%-3s %-26s %-30s %s\n' "$num" "$name" "$alert" "$script"
    done
}

cmd_run() {
    local selector="${1:?usage: run.sh run <number|name> [-- args]}"
    shift || true
    # A literal "--" separator is accepted (as advertised in the usage
    # string) but not required - either way, everything after the selector
    # is forwarded to the scenario script as-is.
    if [ "${1:-}" = "--" ]; then
        shift
    fi
    for entry in "${SCENARIOS[@]}"; do
        IFS=: read -r num name script alert <<<"$entry"
        if [ "$selector" = "$num" ] || [ "$selector" = "$name" ]; then
            exec "$SCRIPT_DIR/$script" "$@"
        fi
    done
    log_warn "no scenario matches '$selector' - run 'run.sh list' to see options"
    exit 1
}

cmd_status() {
    compose ps
    echo
    log_step "webapp fault status:"
    webapp_admin GET /admin/fault/status || log_warn "could not reach webapp admin endpoint (is FAULT_INJECTION_ENABLED=true and the stack up?)"
}

cmd_restore_all() {
    exec "$SCRIPT_DIR/restore-all.sh"
}

load_env

case "${1:-}" in
    list) cmd_list ;;
    run) shift; cmd_run "$@" ;;
    status) cmd_status ;;
    restore-all) cmd_restore_all ;;
    *)
        echo "Usage: run.sh {list|run <number|name> [-- args]|status|restore-all}"
        exit 1
        ;;
esac
