#!/usr/bin/env bash
# Scenario 3/6 -> WebappHighLatencyP95 (monitoring/alerts.yml): p95 of
# webapp_http_request_duration_seconds above 1s, sustained 30s.
#
# Same shape as 02-webapp-5xx.sh but toggles /admin/fault/latency instead.
# Default 1500ms clears the 1s p95 threshold with margin, while staying
# under nginx's proxy_read_timeout (5s, see nginx/default.conf) - so slow
# requests come back as real 200s within the histogram, not as 502/504s
# from nginx giving up, which would accidentally also trip
# WebappHigh5xxRate and muddy this scenario's evidence.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./lib.sh
source "$SCRIPT_DIR/lib.sh"

MS=1500
DURATION=90
RATE=5

while [ $# -gt 0 ]; do
    case "$1" in
        --ms) MS="$2"; shift 2 ;;
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

log_step "enabling latency fault at ${MS}ms"
webapp_admin POST /admin/fault/latency "{\"ms\": $MS}" >/dev/null
webapp_admin GET /admin/fault/status

log_step "generating traffic against /work for ${DURATION}s at ${RATE} req/s"
"$SCRIPT_DIR/loadgen.sh" /work "$RATE" "$DURATION"

log_ok "done. Watch http://localhost:9090/alerts for WebappHighLatencyP95; it should resolve once faults clear."
