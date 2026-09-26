"""
Settings for the triage agent, read from the repo-root .env.

The agent runs on the HOST (not in a container), so it can't rely on
docker compose to inject environment variables the way the webapp does.
Instead it loads the same .env file itself with python-dotenv. Compose simply
ignores the DEEPSEEK_* / TRIAGE_* keys, since docker-compose.yml never
references them - one config file for the whole project, each consumer reads
only what it needs.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
REPORTS_DIR = REPO_ROOT / "reports"


@dataclass(frozen=True)
class Settings:
    deepseek_api_key: str
    deepseek_model: str
    deepseek_base_url: str
    prometheus_url: str
    # The only host-published URL for app traffic (see docker-compose.yml).
    # check_endpoint uses it so the agent sees exactly what a user would.
    webapp_url: str
    poll_seconds: float
    settle_seconds: float
    max_steps: int


def load_settings() -> Settings:
    # override=False: a variable already exported in the shell wins over
    # .env, so `DEEPSEEK_MODEL=deepseek-reasoner python -m triage once`
    # works for one-off experiments without editing the file.
    load_dotenv(REPO_ROOT / ".env", override=False)
    env = os.environ.get
    return Settings(
        deepseek_api_key=env("DEEPSEEK_API_KEY", ""),
        deepseek_model=env("DEEPSEEK_MODEL", "deepseek-chat"),
        deepseek_base_url=env("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        prometheus_url=env("PROMETHEUS_URL", "http://localhost:9090").rstrip("/"),
        webapp_url=env("TRIAGE_WEBAPP_URL", "http://localhost:8080").rstrip("/"),
        poll_seconds=float(env("TRIAGE_POLL_SECONDS", "15")),
        settle_seconds=float(env("TRIAGE_SETTLE_SECONDS", "20")),
        max_steps=int(env("TRIAGE_MAX_STEPS", "12")),
    )
