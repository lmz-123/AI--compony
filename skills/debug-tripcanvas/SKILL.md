---
name: debug-tripcanvas
description: Diagnose TripCanvas production and test incidents using bounded evidence from Docker Compose, application logs, health checks, host resources, PostgreSQL, Redis, and optionally Aliyun SLS. Use when users report bugs, errors, timeouts, unhealthy services, failed jobs, missing or inconsistent data, Redis/cache problems, database symptoms, deployment regressions, or ask to inspect logs and locate a root cause.
---

# TripCanvas Intelligent Operations Debugging

Use this workflow to turn an incident report into an evidence-based diagnosis and a precise handoff. Stay read-only unless the manager and owner explicitly authorize a separate remediation action.

## Safety Contract

- Read `/data/ops-targets.toml` before touching any environment. Only use declared aliases, paths, services, and endpoints.
- Use SSH host aliases from `/root/.ssh/config`; keep strict host-key checking enabled.
- Do not edit business code. Hand code changes to `developer` through `manager`.
- Do not mutate PostgreSQL or Redis. Allow only `SELECT`, catalog inspection, `PING`, selected `INFO`, `DBSIZE`, and bounded `SCAN` when key names are necessary. Never use Redis `KEYS`.
- Do not restart services, deploy, roll back, clean disks, kill processes, or change configuration while diagnosing.
- Never print environment files, container environment, credentials, tokens, connection strings, full request bodies, personal data, or large log dumps.
- Bound every query by time and result count. Default to the last 15 minutes, at most 200 local log lines per service, and at most 5 SLS samples.

## Load Target Context

Read [references/tripcanvas-runtime.md](references/tripcanvas-runtime.md) for the known TripCanvas topology and commands. Read [references/aliyun-sls.md](references/aliyun-sls.md) only when SLS is enabled for the target or local evidence points to a remote logging gap.

Before querying, capture:

- symptom and user-visible impact;
- environment and affected endpoint, job, or feature;
- incident time and timezone, using a narrow window;
- trace ID, request ID, user-safe correlation key, or exact error text;
- last known good version and recent deployment commit, when known.

Missing optional details do not block the first read-only pass. State assumptions explicitly.

## Diagnose

### 1. Establish Version and Health

Confirm the current Git commit, worktree state, Compose service state, health endpoint, recent deploy timing, disk pressure, memory pressure, and container resource state. Do not infer application health only from a running container.

For the standard target, run the bundled snapshot through the declared SSH alias:

```bash
ssh local-production 'bash -s -- --repo-dir /srv/apps/MyAPPs --compose-file tripcanvas-backend/docker-compose.yml --health-url http://127.0.0.1:8000/health --log-services api,worker --postgres-service db --postgres-db tripcanvas --postgres-user tripcanvas --redis-service redis' < /app/skills/debug-tripcanvas/scripts/runtime_snapshot.sh
```

If a configured value differs, use `/data/ops-targets.toml`; do not guess. The script is a bounded first pass, not proof of root cause.

### 2. Follow the Evidence

Correlate by timestamp and correlation ID across API, worker, database, cache, and deploy events. Prefer the smallest query that can disprove a hypothesis.

- Container or health failure: inspect service status, health response, resource pressure, and only the relevant service logs.
- Request failure: trace one request from ingress through API and worker/downstream events.
- Missing or inconsistent data: first confirm the write/read path in code, then run narrow read-only database queries against specific IDs and expected state transitions.
- Cache symptom: use `PING`, bounded `INFO`, `DBSIZE`, TTL/type checks for known keys, or a bounded `SCAN MATCH ... COUNT ...`; do not enumerate the keyspace.
- Deployment regression: compare current commit and deployment time with the first error timestamp and last-known-good behavior.
- External or unclear failure: use SLS only when configured, then correlate its timestamp and IDs with local evidence.

Do not treat co-occurrence as causation. Record facts separately from hypotheses and attach a confidence level to each hypothesis.

### 3. Inspect PostgreSQL Safely

Use only read-only statements. Start a transaction with `SET TRANSACTION READ ONLY` for application-table queries, add selective predicates, and use a small `LIMIT`. Prefer metadata and aggregates before row samples. Never use `SELECT *` on an unbounded table and never expose sensitive columns in chat.

If the schema or identifier is unknown, inspect catalogs or code first. Do not invent table or column names.

### 4. Inspect Redis Safely

Begin with connectivity and bounded operational signals. Query a known key only when the incident provides a safe correlation key and the code confirms the key format. Report types, TTLs, counts, and state transitions rather than raw values. Never run mutating commands, `MONITOR`, `FLUSH*`, or an unbounded scan.

### 5. Use SLS Progressively

When SLS is enabled, first verify the CLI, named profile, permission, project, logstore, and index. Then query a histogram for the narrow time window, retrieve no more than 5 recent samples, and only run SQL aggregation after samples prove that the chosen event represents the metric being counted. See the SLS reference for command templates.

## Correlate With Code

Locate the exact code path and deployed commit that produced the evidence. Use code reading to validate log meanings, database state transitions, Redis key semantics, retry behavior, and whether a log event is emitted once per request. Do not change code in this role.

When evidence indicates a code defect, send the manager a developer-ready task containing:

- affected repository and deployed commit;
- minimal reproduction or triggering conditions;
- relevant code path;
- evidence timestamps and sanitized excerpts;
- likely root cause and confidence;
- behavioral acceptance criteria and regression tests;
- production verification signals after deployment.

## Report and Close the Loop

Use this structure:

```text
Impact:
Scope and time window:
Current version and health:
Confirmed facts:
Hypotheses (confidence + missing evidence):
Likely root cause:
Sanitized evidence / reproduction:
Recommended developer task:
Deployment and rollback considerations:
Post-deploy verification plan:
```

Send the report to `manager`. The manager assigns code work to `developer`; after tests and explicit production approval, `deployer` publishes the approved commit. Then re-run the relevant health, log, database, or Redis checks and report whether the original symptom and error signal are gone. A successful health endpoint alone does not close the incident.
