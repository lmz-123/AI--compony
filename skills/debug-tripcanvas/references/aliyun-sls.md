# Optional Aliyun SLS Workflow

Use SLS only when the selected target has `[sls].enabled = true`. Credentials belong in the operator's configured Aliyun CLI profile and must never be stored in this repository, `/data/ops-targets.toml`, commands pasted into chat, or diagnostic output.

## Environment Check

Verify the declared profile and resources before querying:

```bash
aliyun version
aliyun configure list
aliyun --profile <profile> sls ListLogStores --project <project>
aliyun --profile <profile> sls GetIndex --project <project> --logstore <logstore>
```

If the CLI, profile, permission, project, logstore, or index is unavailable, report that gap. Do not guess resource names or fall back to unrelated credentials.

## Progressive Query

Convert the incident window to Unix timestamps and start with a histogram:

```bash
aliyun --profile <profile> sls GetHistograms \
  --project <project> \
  --logstore <logstore> \
  --from <from_ts> \
  --to <to_ts> \
  --query '<narrow query>'
```

Then inspect at most 5 recent samples:

```bash
aliyun --profile <profile> sls GetLogs \
  --project <project> \
  --logstore <logstore> \
  --from <from_ts> \
  --to <to_ts> \
  --query '<narrow query>' \
  --line 5 \
  --reverse true
```

Prefer exact trace/request IDs, a verified function or event name, `failed`, `panic`, `timeout`, and exact error phrases. A generic `error` query is often noisy. Shrink the time window before broadening terms.

## Validated Aggregation

Never equate raw keyword matches with requests, successes, or failures. First read the deployed code to identify request-start, completion, and failure events; then validate 3 to 5 samples; only then aggregate:

```bash
aliyun --profile <profile> sls GetLogsV2 \
  --project <project> \
  --logstore <logstore> \
  --body '{"from":<from_ts>,"to":<to_ts>,"query":"<validated query> | select count(*) as c limit 1"}'
```

If the event is not one-per-request or required fields are not indexed, label the result as an approximation. Sanitize samples before sharing them with the team.
