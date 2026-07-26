#!/usr/bin/env python3
"""Swap Hermes's live primary model between local (oMLX) and cloud (OpenAI)
for the A/B test's "cloud" arm — see project_plan.md Phase 8 side quest.

Runs INSIDE the container (needs ruamel.yaml from Hermes's own venv and
access to ~/.hermes/config.yaml + ~/.hermes/.env). Copy it in and run it
via `docker exec`; see USER_GUIDE.md "Run another A/B test" for the full
sequence including the gateway restart on either side.

Hermes's native OPENAI_API_KEY env var is pinned to a placeholder at the
container level (docker-compose.yml — needed for the *local* oMLX
provider), so pointing the primary model at real OpenAI can't just rely on
that env var. Instead this writes the real key directly into
model.api_key in config.yaml (a documented override — "API key for
base_url, falls back to OPENAI_API_KEY") using the same value already
used by the household MCP server's cloud tools (OPENAI_CLOUD_API_KEY in
~/.hermes/.env).

Usage:
    python3.11 swap_primary_model.py to-openai
    python3.11 swap_primary_model.py restore
"""
import sys

from ruamel.yaml import YAML

CONFIG_PATH = "/home/hermes/.hermes/config.yaml"
BACKUP_PATH = "/home/hermes/.hermes/config.yaml.ab-test-backup"
ENV_PATH = "/home/hermes/.hermes/.env"
CLOUD_MODEL = "gpt-5.4"

yaml = YAML()
yaml.preserve_quotes = True
yaml.width = 4096  # don't let ruamel wrap the long api_key value onto its own line


def to_openai():
    with open(CONFIG_PATH, "rb") as f:
        backup_bytes = f.read()
    with open(BACKUP_PATH, "wb") as f:
        f.write(backup_bytes)

    with open(CONFIG_PATH) as f:
        data = yaml.load(f)

    real_key = None
    with open(ENV_PATH) as f:
        for line in f:
            line = line.strip()
            if line.startswith("OPENAI_CLOUD_API_KEY="):
                real_key = line.partition("=")[2].strip()
    if not real_key:
        raise SystemExit("OPENAI_CLOUD_API_KEY not found in ~/.hermes/.env")

    data["model"]["provider"] = "custom"
    data["model"]["base_url"] = "https://api.openai.com/v1"
    data["model"]["default"] = CLOUD_MODEL
    data["model"]["api_key"] = real_key

    with open(CONFIG_PATH, "w") as f:
        yaml.dump(data, f)
    print(f"Swapped model block to OpenAI {CLOUD_MODEL} (backup at {BACKUP_PATH})")


def restore():
    import os

    if not os.path.exists(BACKUP_PATH):
        raise SystemExit(f"No backup found at {BACKUP_PATH} — nothing to restore")
    with open(BACKUP_PATH, "rb") as f:
        backup_bytes = f.read()
    with open(CONFIG_PATH, "wb") as f:
        f.write(backup_bytes)
    os.remove(BACKUP_PATH)
    print("Restored config.yaml from backup")


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("to-openai", "restore"):
        raise SystemExit(__doc__)
    (to_openai if sys.argv[1] == "to-openai" else restore)()
