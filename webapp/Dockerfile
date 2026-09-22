# python:3.12-slim rather than the full python:3.12 image: it's a much
# smaller Debian base with no compilers/docs/extra tooling baked in. Smaller
# image = faster builds/pulls and a smaller attack surface. We're NOT using
# "alpine" here (unlike nginx/postgres below) because psycopg2 needs
# libpq and building Python C-extensions against Alpine's musl libc is more
# hassle than it's worth for a learning project - slim (Debian-based, glibc)
# keeps that simple.
FROM python:3.12-slim

# Install curl so Docker's HEALTHCHECK (see docker-compose.yml) can actually
# curl the app's own /health endpoint from inside the container. The slim
# base image does not include curl by default.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy and install dependencies BEFORE copying the rest of the app code.
# Docker caches each layer; as long as requirements.txt doesn't change, this
# `pip install` layer is reused on rebuilds even if app.py changes, so
# `docker compose up --build` after an app.py edit is fast instead of
# reinstalling every dependency from scratch every time.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

# Run as a non-root user inside the container. If the app were ever
# compromised (e.g. via a dependency vulnerability), the attacker lands as
# an unprivileged user rather than root - standard container hardening.
RUN useradd --create-home --uid 1000 appuser
USER appuser

# Shared directory where gunicorn workers write metric files (see app.py).
# Wiped on every container start so stale files from a previous run don't
# leak into the new one.
ENV PROMETHEUS_MULTIPROC_DIR=/tmp/prometheus

EXPOSE 8000

# gunicorn instead of `flask run` / `app.run()`: gunicorn is a real WSGI
# server (pre-forking worker processes, proper handling of slow/concurrent
# requests). Flask's built-in dev server is single-threaded, not designed
# for anything beyond local debugging, and will silently become your
# bottleneck if you ever put real load through this stack.
#
# --worker-class gthread --threads 8: plain sync workers handle exactly one
# request at a time each, full stop. That matters once Phase 3's latency
# fault injection (app.py's /work route) is in the picture: a handful of
# slow /work requests would otherwise completely occupy both workers and
# starve /health and /metrics behind them - Docker would then mark the
# container unhealthy and Prometheus's scrape would fail too, turning a
# "high latency" scenario into an unrelated "target down" one. Threads
# release the GIL during time.sleep() (what the fault uses), so a worker
# can keep serving /health and /metrics on another thread while one thread is
# sleeping through a slow /work request - the same fix a real service
# would reach for to avoid one slow endpoint taking the whole process down.
CMD ["sh", "-c", "rm -rf $PROMETHEUS_MULTIPROC_DIR && mkdir -p $PROMETHEUS_MULTIPROC_DIR && exec gunicorn --bind 0.0.0.0:8000 --workers 2 --worker-class gthread --threads 8 app:app"]
