---
name: debug-tripcanvas
description: Diagnose TripCanvas production and test incidents using bounded evidence from Docker Compose, application logs, health checks, host resources, PostgreSQL, Redis, and optionally Aliyun SLS. Use when users report bugs, errors, timeouts, unhealthy services, failed jobs, missing or inconsistent data, Redis/cache problems, database symptoms, deployment regressions, or ask to inspect logs and locate a root cause.
---

# TripCanvas Intelligent Operations Debugging

Use the smallest read-only query sequence that can explain the reported symptom. Stop when the question is answered or the next action belongs to another role.

## Safety Contract

- Read `/data/ops-targets.toml` before touching any environment. Only use declared aliases, paths, services, and endpoints.
- Use SSH host aliases from `/root/.ssh/config`; keep strict host-key checking enabled.
- Do not edit business code. Hand code changes to `developer` through `manager`.
- Do not mutate PostgreSQL or Redis. Allow only `SELECT`, catalog inspection, `PING`, selected `INFO`, `DBSIZE`, and bounded `SCAN` when key names are necessary. Never use Redis `KEYS`.
- Do not restart services, deploy, roll back, clean disks, kill processes, or change configuration while diagnosing.
- Never print environment files, container environment, credentials, tokens, connection strings, full request bodies, personal data, or large log dumps.
- Bound every query by time and result count. Default to the last 15 minutes, one relevant service, at most 100 local log lines, and at most 5 SLS samples.

## Load Target Context

Read [references/tripcanvas-runtime.md](references/tripcanvas-runtime.md) for the known TripCanvas topology and commands. Read [references/aliyun-sls.md](references/aliyun-sls.md) only when SLS is enabled for the target or local evidence points to a remote logging gap.

Before querying, capture:

- symptom and user-visible impact;
- environment and affected endpoint, job, or feature;
- incident time and timezone, using a narrow window;
- trace ID, request ID, user-safe correlation key, or exact error text;

Missing optional details do not block the first read-only pass. State assumptions explicitly.

## Diagnose

### 1. Start With Relevant Logs

Query only the service most likely to contain the reported symptom. Filter by the provided error text or correlation ID when possible, and keep a narrow time window. Do not begin with a full runtime snapshot, commit/worktree inspection, every service, or every backing store.

The bundled helper is optional. Use it only when a bounded log read plus specific optional probes matches the current hypothesis:

```bash
ssh local-production 'bash -s -- --repo-dir /srv/apps/MyAPPs --compose-file tripcanvas-backend/docker-compose.yml --log-services api --since 15m --log-lines 100' < /app/skills/debug-tripcanvas/scripts/runtime_snapshot.sh
```

If a configured value differs, use `/data/ops-targets.toml`; do not guess. The script is a bounded first pass, not proof of root cause.

### 2. Follow One Evidence Path

Correlate by timestamp and correlation ID across API, worker, database, cache, and deploy events. Prefer the smallest query that can disprove a hypothesis.

- Container or health failure: after logs, inspect service status and the configured health response; inspect resources only when the evidence indicates pressure.
- Request failure: trace one request from ingress through API and worker/downstream events.
- Missing or inconsistent data: first confirm the write/read path in code, then run narrow read-only database queries against specific IDs and expected state transitions.
- Cache symptom: use `PING`, bounded `INFO`, `DBSIZE`, TTL/type checks for known keys, or a bounded `SCAN MATCH ... COUNT ...`; do not enumerate the keyspace.
- Suspected deployment regression: ask the manager or deployer for the deployed version only when the first error time aligns with a release.
- External or unclear failure: use SLS only when configured, then correlate its timestamp and IDs with local evidence.

Do not treat co-occurrence as causation. Record facts separately from hypotheses and attach a confidence level to each hypothesis.

### 3. Inspect PostgreSQL Safely

Use only read-only statements. Start a transaction with `SET TRANSACTION READ ONLY` for application-table queries, add selective predicates, and use a small `LIMIT`. Prefer metadata and aggregates before row samples. Never use `SELECT *` on an unbounded table and never expose sensitive columns in chat.

If the schema or identifier is unknown, inspect catalogs or code first. Do not invent table or column names.

### 4. Inspect Redis Safely

Begin with connectivity and bounded operational signals. Query a known key only when the incident provides a safe correlation key and the code confirms the key format. Report types, TTLs, counts, and state transitions rather than raw values. Never run mutating commands, `MONITOR`, `FLUSH*`, or an unbounded scan.

### 5. Use SLS Progressively

When SLS is enabled, first verify the CLI, named profile, permission, project, logstore, and index. Then query a histogram for the narrow time window, retrieve no more than 5 recent samples, and only run SQL aggregation after samples prove that the chosen event represents the metric being counted. See the SLS reference for command templates.

## Correlate With Code Only When Needed

Locate the relevant code path only when logs indicate a code defect or their meaning is ambiguous. Use code reading to validate log meanings, database state transitions, Redis key semantics, retry behavior, and whether a log event is emitted once per request. Do not inspect commit/worktree by default and do not change code in this role.

When evidence indicates a code defect, send the manager a developer-ready task containing:

- affected repository and code path;
- minimal reproduction or triggering conditions;
- relevant code path;
- evidence timestamps and sanitized excerpts;
- likely root cause and confidence;
- behavioral acceptance criteria and regression tests;

## Report

Use this structure:

```text
Result:
Time window:
Sanitized evidence:
Likely cause (confidence):
Next action, if any:
```

Send one concise report to `manager`. Do not automatically schedule post-deploy verification. Recheck only when the manager explicitly requests it because the deployment failed, evidence conflicts, or the change is high risk.
