#!/usr/bin/env bash
# Scenario 2/6 -> WebappHigh5xxRate (monitoring/alerts.yml): more than 10%
# of webapp_http_requests_total are 5xx, sustained 30s.
#
# Toggles the /admin/fault/5xx endpoint (webapp/app.py, gated behind
# FAULT_INJECTION_ENABLED) so /work starts failing at the given probability,
# then drives real traffic at /work through nginx via loadgen.sh so
# Prometheus's rate() has volume to compute a ratio from - an idle stack has
# no requests for rate() to work with, alert or not.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./lib.sh
source "$SCRIPT_DIR/lib.sh"

# 0.6, not the 0.1 threshold itself - comfortably above it so the alert
# fires reliably despite request-timing jitter, without needing to tune it.
PROBABILITY=0.6
DURATION=90
RATE=5

while [ $# -gt 0 ]; do
    case "$1" in
        --probability) PROBABILITY="$2"; shift 2 ;;
        --duration) DURATION="$2"; shift 2 ;;
        --rate) RATE="$2"; shift 2 ;;
        *) log_warn "unknown arg: $1"; exit 1 ;;
    esac
done

load_env
require_running webapp

cleanup() {
    log_step "restoring: clearing webapp faults"
    webapp_admin POST /admin/fault/clear >/dev/null || true
}
install_cleanup_trap

log_step "enabling 5xx fault at probability=$PROBABILITY"
webapp_admin POST /admin/fault/5xx "{\"probability\": $PROBABILITY}" >/dev/null
webapp_admin GET /admin/fault/status

log_step "generating traffic against /work for ${DURATION}s at ${RATE} req/s"
"$SCRIPT_DIR/loadgen.sh" /work "$RATE" "$DURATION"

log_ok "done. Watch http://localhost:9090/alerts for WebappHigh5xxRate; it should resolve once faults clear."
