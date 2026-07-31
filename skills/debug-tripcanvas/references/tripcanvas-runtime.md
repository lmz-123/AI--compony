# TripCanvas Runtime Reference

Use `/data/ops-targets.toml` as the runtime source of truth. These values document the initial production target and must not override that file.

## Initial Topology

| Item | Value |
| --- | --- |
| Project | TripCanvas |
| Repository | `git@github.com:lmz-123/MyAPPs.git` |
| Server directory | `/srv/apps/MyAPPs` |
| Branch | `main` |
| Compose file | `tripcanvas-backend/docker-compose.yml` |
| Health endpoint | `http://127.0.0.1:8000/health` |
| Application services | `api`, `worker` |
| PostgreSQL service | `db` |
| Redis service | `redis` |
| SSH alias | `local-production` |

The Flutter frontend is not deployed on this server.

## First-Pass Snapshot

Run the bundled script from the AI Company container and execute it on the target over SSH:

```bash
ssh local-production 'bash -s -- --repo-dir /srv/apps/MyAPPs --compose-file tripcanvas-backend/docker-compose.yml --health-url http://127.0.0.1:8000/health --log-services api,worker --postgres-service db --postgres-db tripcanvas --postgres-user tripcanvas --redis-service redis' < /app/skills/debug-tripcanvas/scripts/runtime_snapshot.sh
```

The SSH user needs read access to the repository and permission to inspect this Compose project. It does not need permission to modify application data.

## Focused Follow-Up Rules

- Always add `--since` and `--tail` to Compose log reads.
- Prefer `docker compose ps` and a real health request over raw `docker ps` alone.
- Do not run `docker compose config` in incident reports because its rendered output can contain interpolated secrets.
- Do not display `.env`, `inspect` container environment, database URLs, or Redis authentication settings.
- Use explicit service names and project paths from the target file.
- Database row queries must be read-only, selective, and limited.
- Redis key inspection must use a code-confirmed pattern and bounded `SCAN`, never `KEYS`.
