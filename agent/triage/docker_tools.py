"""
Read-only views of the Docker Compose stack: container state and logs.

These shell out to the host's `docker compose` CLI, the same one you'd type
into a terminal. The alternative - running the agent in a container with
/var/run/docker.sock mounted - would hand it full control of the Docker
daemon (socket access is effectively root on the host), which is far more
power than a read-only diagnostic tool should have.

Two guardrails, since the arguments ultimately come from an LLM:
  - subprocess gets a list of args, never a shell string, so nothing the
    model writes can be interpreted as extra shell syntax.
  - `service` must be one of the names `docker compose config --services`
    reports, so the model can't point these at arbitrary containers or
    smuggle in extra CLI flags.
"""

import json
import subprocess
from functools import lru_cache

from .config import REPO_ROOT

TIMEOUT_SECONDS = 20


class DockerError(RuntimeError):
    pass


def _compose(*args: str) -> str:
    try:
        proc = subprocess.run(
            ["docker", "compose", *args],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DockerError(f"docker compose {' '.join(args)} failed: {exc}") from exc
    if proc.returncode != 0:
        raise DockerError(proc.stderr.strip() or f"exit code {proc.returncode}")
    return proc.stdout


@lru_cache(maxsize=1)
def compose_services() -> tuple[str, ...]:
    """Every service defined in docker-compose.yml, running or not. Cached:
    the file doesn't change while the agent runs."""
    return tuple(_compose("config", "--services").split())


def check_service(service: str) -> str:
    if service not in compose_services():
        raise ValueError(f"unknown service {service!r}; valid: {', '.join(compose_services())}")
    return service


def service_status() -> list[dict]:
    # -a includes stopped containers: a service that was `docker compose
    # stop`-ed is precisely the kind of thing the agent needs to see, and
    # without -a it would silently vanish from the list.
    out = _compose("ps", "-a", "--format", "json")
    # Newer compose prints one JSON object per line; older versions print a
    # single JSON array. Accept both.
    out = out.strip()
    if not out:
        rows = []
    elif out.startswith("["):
        rows = json.loads(out)
    else:
        rows = [json.loads(line) for line in out.splitlines() if line.strip()]
    listed = {r.get("Service") for r in rows}
    result = [
        {
            "service": r.get("Service"),
            "state": r.get("State"),
            "health": r.get("Health") or None,
            "status": r.get("Status"),
            "exit_code": r.get("ExitCode"),
            "image": r.get("Image"),
        }
        for r in rows
    ]
    # A service with no container at all (never started, or removed) would
    # otherwise be invisible - call it out explicitly.
    for svc in compose_services():
        if svc not in listed:
            result.append({"service": svc, "state": "no container"})
    return result


def service_logs(service: str, since_minutes: int, tail: int) -> str:
    check_service(service)
    return _compose(
        "logs", "--no-color", "--timestamps",
        "--since", f"{since_minutes}m", "--tail", str(tail),
        service,
    )
