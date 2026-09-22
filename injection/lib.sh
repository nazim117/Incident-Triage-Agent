# Shared helpers for the Phase 3 failure-injection scripts. Sourced by every
# scenario script (and by loadgen.sh, restore-all.sh, run.sh) - not meant to
# be run directly.
#
# Every scenario script does roughly the same three things: load .env so it
# knows the Postgres credentials docker-compose would otherwise substitute
# for it, talk to the webapp's admin endpoints or docker compose, and clean
# up after itself even on Ctrl-C. Centralizing that here keeps each
# scenario script focused on just the mechanism specific to its alert.

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# The only host-reachable URL in the whole stack (see docker-compose.yml -
# nginx is the sole published port). loadgen.sh uses this to send real
# "user" traffic through the same path actual clients would use. Admin
# fault-toggle calls deliberately do NOT go through this URL - see
# webapp_admin() below.
WEBAPP_URL="http://localhost:8080"

load_env() {
    # Docker Compose reads .env itself; this just gives *scripts* the same
    # values (e.g. POSTGRES_PASSWORD) without re-parsing docker-compose.yml.
    if [ ! -f "$REPO_ROOT/.env" ]; then
        log_warn ".env not found - copy .env.example to .env first (cp .env.example .env)"
        exit 1
    fi
    set -a
    # shellcheck disable=SC1091
    source "$REPO_ROOT/.env"
    set +a
}

log_step() { printf '\033[1;34m[%s] ==>\033[0m %s\n' "$(date '+%H:%M:%S')" "$*"; }
log_ok()   { printf '\033[1;32m[%s] ok \033[0m %s\n' "$(date '+%H:%M:%S')" "$*"; }
log_warn() { printf '\033[1;33m[%s] !! \033[0m %s\n' "$(date '+%H:%M:%S')" "$*"; }

compose() {
    (cd "$REPO_ROOT" && docker compose "$@")
}

# Calls a webapp admin endpoint from INSIDE the container (docker compose
# exec), never through nginx/the host. This matches the rest of the stack's
# "nothing external can reach webapp directly" posture - see the "Least
# exposure" comments in nginx/default.conf and docker-compose.yml. webapp
# already has curl installed for its own Docker healthcheck.
webapp_admin() {
    local method="$1" path="$2" body="${3:-}"
    if [ -n "$body" ]; then
        compose exec -T webapp curl -sf -X "$method" \
            -H 'Content-Type: application/json' -d "$body" \
            "http://localhost:8000${path}"
    else
        compose exec -T webapp curl -sf -X "$method" "http://localhost:8000${path}"
    fi
}

# Fails loudly (rather than letting a scenario silently do nothing) if the
# stack isn't up, or if a service the scenario depends on isn't running.
require_running() {
    local service="$1"
    local status
    status="$(compose ps --status running --services)"
    if ! grep -qx "$service" <<<"$status"; then
        log_warn "$service is not running - start the stack first: docker compose up --build -d"
        exit 1
    fi
}

# Every scenario script calls this right after defining its own cleanup()
# function, so Ctrl-C (or any early exit) still restores state instead of
# leaving faults/stopped containers/held connections behind.
install_cleanup_trap() {
    trap cleanup EXIT INT TERM
}
