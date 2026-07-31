#!/usr/bin/env bash
set -euo pipefail

# PID files belong to processes in the previous container namespace. Keeping
# them on the persistent volume risks a PID collision during the next boot.
rm -f /data/state/router.pid /data/state/watchdog.pid

# Codex's first-run TUI requires auth.json even when OPENAI_API_KEY is exported.
# Materialize the API-key auth file in the container's ephemeral operator HOME;
# lifecycle then symlinks it into each isolated CODEX_HOME. The key stays in the
# gitignored /data/state/.env and is never printed or baked into the image.
python - <<'PY'
import json
import os
from pathlib import Path

from claudeteam.runtime.agent_auth import load_secrets

key = load_secrets().get("OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
if not key:
    raise SystemExit("OPENAI_API_KEY is missing from /data/state/.env")

auth = Path("/root/.codex/auth.json")
auth.parent.mkdir(parents=True, exist_ok=True)
auth.write_text(json.dumps({"auth_mode": "apikey", "OPENAI_API_KEY": key}) + "\n")
auth.chmod(0o600)
PY

claudeteam install-hooks
claudeteam up
exec sleep infinity
