#!/usr/bin/env bash
set -euo pipefail

# PID files belong to processes in the previous container namespace. Keeping
# them on the persistent volume risks a PID collision during the next boot.
rm -f /data/state/router.pid /data/state/watchdog.pid

claudeteam install-hooks
claudeteam up
exec sleep infinity
