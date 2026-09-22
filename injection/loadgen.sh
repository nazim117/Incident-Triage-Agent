#!/usr/bin/env bash
# Generic traffic generator: curls a path through nginx (the same public
# ingress real clients use) at a fixed rate for a fixed duration.
#
# Why this is needed at all: WebappHigh5xxRate and WebappHighLatencyP95 are
# both rate()/histogram_quantile() over a 1m window. A rate is meaningless
# without volume - this demo stack has no real users hitting it, so without
# something generating requests, toggling a fault on /work would produce a
# handful of samples at best and the alerts would never have enough data to
# fire. This script is that traffic source, reused by both scenarios.
#
# Usage: loadgen.sh <path> <requests-per-second> <duration-seconds>
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./lib.sh
source "$SCRIPT_DIR/lib.sh"

PATH_="${1:?usage: loadgen.sh <path> <rate> <duration>}"
RATE="${2:?usage: loadgen.sh <path> <rate> <duration>}"
DURATION="${3:?usage: loadgen.sh <path> <rate> <duration>}"

log_step "loadgen: hitting ${WEBAPP_URL}${PATH_} at ${RATE} req/s for ${DURATION}s"

END=$(( $(date +%s) + DURATION ))
SLEEP="$(awk -v r="$RATE" 'BEGIN { printf "%.3f", 1 / r }')"

while [ "$(date +%s)" -lt "$END" ]; do
    curl -s -o /dev/null "${WEBAPP_URL}${PATH_}" &
    sleep "$SLEEP"
done
wait

log_ok "loadgen: done"
