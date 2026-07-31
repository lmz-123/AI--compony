#!/usr/bin/env bash
set -uo pipefail

usage() {
  cat <<'EOF'
Usage: runtime_snapshot.sh --repo-dir PATH --compose-file PATH [options]

Read-only, bounded TripCanvas runtime snapshot.

Options:
  --health-url URL             Local health URL
  --log-services CSV           Compose services to inspect (default: api,worker)
  --since DURATION             Docker log window (default: 15m)
  --log-lines NUMBER           Max lines per service (default: 200, max: 500)
  --postgres-service NAME      Enable PostgreSQL operational probe
  --postgres-db NAME           PostgreSQL database name
  --postgres-user NAME         PostgreSQL user name
  --redis-service NAME         Enable Redis operational probe
  -h, --help                   Show this help
EOF
}

repo_dir=""
compose_file=""
health_url=""
log_services="api,worker"
since="15m"
log_lines=200
postgres_service=""
postgres_db=""
postgres_user=""
redis_service=""

while (($#)); do
  case "$1" in
    --repo-dir) repo_dir=${2:-}; shift 2 ;;
    --compose-file) compose_file=${2:-}; shift 2 ;;
    --health-url) health_url=${2:-}; shift 2 ;;
    --log-services) log_services=${2:-}; shift 2 ;;
    --since) since=${2:-}; shift 2 ;;
    --log-lines) log_lines=${2:-}; shift 2 ;;
    --postgres-service) postgres_service=${2:-}; shift 2 ;;
    --postgres-db) postgres_db=${2:-}; shift 2 ;;
    --postgres-user) postgres_user=${2:-}; shift 2 ;;
    --redis-service) redis_service=${2:-}; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown argument: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ -z "$repo_dir" || -z "$compose_file" ]]; then
  usage >&2
  exit 2
fi
if [[ "$repo_dir" != /* || "$compose_file" == /* || "$compose_file" == *..* ]]; then
  printf 'repo-dir must be absolute; compose-file must be a safe relative path\n' >&2
  exit 2
fi
if [[ ! "$log_lines" =~ ^[0-9]+$ ]] || ((log_lines < 1 || log_lines > 500)); then
  printf 'log-lines must be between 1 and 500\n' >&2
  exit 2
fi
if [[ ! "$since" =~ ^[0-9]+[smhd]$ ]]; then
  printf 'since must look like 15m, 2h, or 1d\n' >&2
  exit 2
fi

valid_name() { [[ "$1" =~ ^[A-Za-z0-9_.-]+$ ]]; }
for name in "$postgres_service" "$postgres_db" "$postgres_user" "$redis_service"; do
  if [[ -n "$name" ]] && ! valid_name "$name"; then
    printf 'Unsafe service/database/user name: %s\n' "$name" >&2
    exit 2
  fi
done

IFS=',' read -r -a services <<< "$log_services"
for service in "${services[@]}"; do
  if ! valid_name "$service"; then
    printf 'Unsafe log service name: %s\n' "$service" >&2
    exit 2
  fi
done

if ! cd "$repo_dir" || [[ ! -f "$compose_file" ]]; then
  printf 'Repository or Compose file not found\n' >&2
  exit 2
fi

section() { printf '\n===== %s =====\n' "$1"; }
redact() {
  sed -E \
    -e 's/((api[_-]?key|token|secret|password|authorization)[=:][[:space:]]*)[^[:space:],]+/\1<redacted>/Ig' \
    -e 's/(Bearer[[:space:]]+)[A-Za-z0-9._~+\/-]+/\1<redacted>/Ig'
}

section "timestamp"
date -u '+utc=%Y-%m-%dT%H:%M:%SZ'
date '+local=%Y-%m-%dT%H:%M:%S%z'

section "version"
git rev-parse --short=12 HEAD 2>&1 || true
git status --short --branch --untracked-files=no 2>&1 | head -n 30 || true

section "host"
uptime 2>&1 || true
df -hP "$repo_dir" 2>&1 | head -n 5 || true
if command -v free >/dev/null 2>&1; then
  free -h 2>&1 | head -n 5 || true
else
  vm_stat 2>&1 | head -n 8 || true
fi

section "compose services"
docker compose -f "$compose_file" ps 2>&1 | redact || true

section "container resources"
container_ids=$(docker compose -f "$compose_file" ps -q 2>/dev/null || true)
if [[ -n "$container_ids" ]]; then
  # Container IDs contain no whitespace, so intentional word splitting is safe.
  # shellcheck disable=SC2086
  docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}\t{{.BlockIO}}' $container_ids 2>&1 | redact || true
else
  printf 'No running Compose containers found\n'
fi

if [[ -n "$health_url" ]]; then
  section "health endpoint"
  if [[ "$health_url" =~ ^https?://(127\.0\.0\.1|localhost)(:[0-9]+)?/ ]]; then
    curl --fail --silent --show-error --max-time 10 "$health_url" 2>&1 | head -c 2000 | redact || true
    printf '\n'
  else
    printf 'Skipped: health URL must use localhost or 127.0.0.1\n'
  fi
fi

section "recent application logs"
docker compose -f "$compose_file" logs --no-color --since "$since" --tail "$log_lines" "${services[@]}" 2>&1 | redact || true

if [[ -n "$postgres_service" ]]; then
  section "postgres operational state"
  if [[ -z "$postgres_db" || -z "$postgres_user" ]]; then
    printf 'Skipped: postgres-db and postgres-user are required\n'
  else
    docker compose -f "$compose_file" exec -T "$postgres_service" \
      psql -X -v ON_ERROR_STOP=1 -U "$postgres_user" -d "$postgres_db" -Atc \
      "SELECT 'version=' || current_setting('server_version'); SELECT 'connections=' || count(*) FROM pg_stat_activity; SELECT 'non_idle_connections=' || count(*) FROM pg_stat_activity WHERE state <> 'idle';" \
      2>&1 | redact | head -n 20 || true
  fi
fi

if [[ -n "$redis_service" ]]; then
  section "redis operational state"
  docker compose -f "$compose_file" exec -T "$redis_service" redis-cli --no-auth-warning PING 2>&1 | redact | head -n 5 || true
  docker compose -f "$compose_file" exec -T "$redis_service" redis-cli --no-auth-warning DBSIZE 2>&1 | redact | head -n 5 || true
  docker compose -f "$compose_file" exec -T "$redis_service" redis-cli --no-auth-warning INFO memory 2>&1 \
    | sed -n -E '/^(used_memory_human|maxmemory_human|maxmemory_policy|mem_fragmentation_ratio):/p' \
    | head -n 10 || true
  docker compose -f "$compose_file" exec -T "$redis_service" redis-cli --no-auth-warning INFO stats 2>&1 \
    | sed -n -E '/^(total_connections_received|total_commands_processed|instantaneous_ops_per_sec|keyspace_hits|keyspace_misses|evicted_keys|expired_keys|rejected_connections):/p' \
    | head -n 20 || true
fi

section "snapshot complete"
printf 'Read-only first pass complete. Correlate timestamps and run only focused follow-up queries.\n'
