"""
Demo web app for the Infra Incident Triage Agent project.

This app exists to give the rest of the stack (Nginx, Prometheus/Grafana in
Phase 2, the failure-injection script in Phase 3, and the agent in Phase 4)
something real to observe and poke at. It is deliberately simple: a couple
of routes, one dependency (Postgres), nothing clever.

Two routes matter for infra reasons, and the distinction between them is
worth understanding up front because it's exactly the kind of thing an
incident-triage agent needs to reason about:

  - /health    -> "liveness" check. Answers ONLY from the Python process
                  itself, no external calls. If this fails, the process is
                  hung/crashed/deadlocked - restarting THIS container is the
                  right fix.
  - /db-check  -> "dependency" check. Actually talks to Postgres. If THIS
                  fails but /health is fine, the app process is healthy but
                  something downstream (DB down, network partition, bad
                  credentials) is broken - restarting the webapp container
                  would NOT fix that; you'd be looking at Postgres or the
                  network instead.

Mixing these two up is a classic real-world monitoring mistake: a
healthcheck that also pings the database means a slow/dead DB makes
orchestrators (Docker, Kubernetes, ...) kill and restart a perfectly healthy
app container in a loop, which does nothing to fix the actual problem and
adds churn on top of the outage.
"""

import os
import time

import psycopg2
from flask import Flask, Response, g, jsonify, request
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Histogram,
    generate_latest,
    multiprocess,
)

app = Flask(__name__)

# Connection details are injected via environment variables (set in
# docker-compose.yml from .env), never hardcoded. This is the standard
# "config via environment" pattern: the same image can run against a local
# dev DB, a staging DB, or prod, with no code change - only the environment
# differs. It also means secrets (the DB password) live in .env, which is
# gitignored, instead of in source code.
DB_HOST = os.environ.get("DB_HOST", "db")
DB_PORT = os.environ.get("DB_PORT", "5432")
DB_NAME = os.environ.get("DB_NAME", "triage")
DB_USER = os.environ.get("DB_USER", "triage")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")

# Prometheus metrics. gunicorn runs 2 worker processes (see Dockerfile), and
# each has its own in-memory counters, so a plain /metrics would return
# whichever worker happened to answer. prometheus_client's multiprocess mode
# (enabled by the PROMETHEUS_MULTIPROC_DIR env var, set in the Dockerfile)
# has every worker write to shared files, and /metrics aggregates them.
REQUEST_COUNT = Counter(
    "webapp_http_requests_total",
    "HTTP requests handled by the webapp",
    ["method", "route", "status"],
)
REQUEST_LATENCY = Histogram(
    "webapp_http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["route"],
)
DB_CHECK_ERRORS = Counter(
    "webapp_db_check_errors_total",
    "Failed database connectivity checks in /db-check",
)


@app.before_request
def _start_timer():
    g.start = time.perf_counter()


@app.after_request
def _record_metrics(response):
    # request.url_rule is the route pattern ("/db-check"), not the raw path,
    # so unknown URLs can't create unbounded label values.
    route = request.url_rule.rule if request.url_rule else "unmatched"
    if route != "/metrics":
        REQUEST_COUNT.labels(request.method, route, response.status_code).inc()
        REQUEST_LATENCY.labels(route).observe(time.perf_counter() - g.start)
    return response


def get_db_connection():
    """Open a fresh connection to Postgres.

    We open a new connection per request rather than pooling. That's fine
    for a low-traffic demo app; a real production service would use a
    connection pool (e.g. psycopg's pool, or pgbouncer in front of
    Postgres) to avoid the overhead of a TCP + auth handshake on every
    request. Keeping it simple here so the failure modes we care about
    later (DB down, DB slow, network dropped) stay easy to reason about.
    """
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        connect_timeout=3,
    )


@app.route("/")
def index():
    """Basic "is the app up" page, reached via Nginx -> webapp."""
    return jsonify(
        {
            "service": "incident-triage-demo-webapp",
            "message": "hello from behind the reverse proxy",
        }
    )


@app.route("/health")
def health():
    """Liveness check: no external calls, just confirms the process answers.

    This is what Docker's HEALTHCHECK for the webapp container calls, and
    it's what an orchestrator should use to decide "is this container worth
    keeping alive". It must stay cheap and dependency-free.
    """
    return jsonify({"status": "ok", "time": time.time()})


@app.route("/db-check")
def db_check():
    """Dependency check: proves the whole chain (app -> network -> Postgres)
    is working, by actually running a query. Used later by monitoring and
    by the agent's investigation tools to tell "app is up" apart from
    "app is up but its database isn't".
    """
    try:
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1;")
                cur.fetchone()
        finally:
            conn.close()
        return jsonify({"status": "ok", "db": "reachable"})
    except Exception as exc:  # noqa: BLE001 - we want to report ANY DB failure, not classify it
        DB_CHECK_ERRORS.inc()
        # Returning 503 (Service Unavailable) rather than 500 signals
        # specifically "a dependency is down", which matters once
        # Prometheus/Grafana and the agent start distinguishing status
        # codes.
        return jsonify({"status": "error", "db": "unreachable", "detail": str(exc)}), 503


@app.route("/metrics")
def metrics():
    """Prometheus scrape endpoint (aggregated across gunicorn workers)."""
    registry = CollectorRegistry()
    multiprocess.MultiProcessCollector(registry)
    return Response(generate_latest(registry), mimetype=CONTENT_TYPE_LATEST)


if __name__ == "__main__":
    # This block only runs if you execute `python app.py` directly, which we
    # don't do in the container (see Dockerfile - it uses gunicorn instead).
    # Flask's built-in server here is single-threaded and not meant for
    # anything beyond local debugging.
    app.run(host="0.0.0.0", port=8000)
