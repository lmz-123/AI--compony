---
name: build-tripcanvas-android
description: Build, validate, download, and deliver TripCanvas Android APK or AAB artifacts through an allowlisted GitHub Actions workflow, returning a server download link by default. Use when users ask to package, build, generate, download, or send an Android app, APK, AAB, debug build, test build, or signed release for TripCanvas, including investigating failed mobile builds.
---

# Build TripCanvas Android

## Quick Path

For ordinary debug APK work, the short path is:

1. Use configured defaults from `/data/build-targets.toml`
2. Run the build script without `--send-to-feishu`
3. Return only `download_url`, filename, SHA256, and workflow URL to `manager`

Only read the rest of this file when:

- configuration is being changed;
- a release build is requested;
- delivery failed;
- the workflow failed and needs diagnosis.

Use the bundled runner instead of manually composing GitHub API calls. It keeps the GitHub token out of commands and limits builds to targets and refs declared in `/data/build-targets.toml`.

## Safety and Ownership

- Read `/data/build-targets.toml` before acting. Do not build an undeclared repository, workflow, or ref.
- Default to `debug + apk`. A release requires explicit approval in the current user request, `allow_release = true`, and configured GitHub signing secrets.
- Build only committed and pushed code. Record the requested ref and resulting immutable commit.
- Never print `GITHUB_TOKEN`, keystore data, aliases, or signing passwords. The runner reads the token internally from `/data/state/.env`.
- Every Android build requires `AMAP_ANDROID_KEY` in the MyAPPs GitHub Actions secrets. Never request or place that value in chat, server TOML, or the repository.
- Do not publish to an app store. This workflow only produces an artifact plus its delivery metadata.
- Embed an API URL reachable by the target phone. Do not use `localhost` or `127.0.0.1` for a physical-device APK.

## Build Workflow

1. For an ordinary request, use the configured defaults: `main`, `debug`, `apk`, and the API URL from `/data/build-targets.toml`. Ask follow-up questions only when the user requests a different value or a release.
2. For code changes, complete relevant tests and push the requested ref before building. If no code change is requested, `ops` may run this workflow alone.
3. Use dry validation only while changing setup or diagnosing configuration:

```bash
python /app/skills/build-tripcanvas-android/scripts/build_android.py \
  --target tripcanvas-android \
  --ref main \
  --build-type debug \
  --format apk \
  --api-base-url https://api.example.com \
  --dry-run
```

4. For a routine build, directly trigger, wait, download, verify, and return the APK metadata:

```bash
python /app/skills/build-tripcanvas-android/scripts/build_android.py \
  --target tripcanvas-android \
  --ref main \
  --build-type debug \
  --format apk \
  --api-base-url https://api.example.com
```

Use the real URL from `/data/build-targets.toml` or the manager's task. Never copy the example URL.

5. Default delivery path: give `manager` the returned `download_url`, filename, SHA256, and workflow URL. Do **not** add `--send-to-feishu` for ordinary debug APK work.

6. Only when the user explicitly asks to send the file to Feishu, and robot file-upload permissions are already confirmed working, append:

```bash
--send-to-feishu
```

## Deterministic Verification

The runner must verify all of the following before delivery:

- the matching workflow run completed successfully;
- the artifact is not expired and stays under the size limit;
- exactly one APK or AAB is present;
- the build manifest request ID and filename match;
- the package SHA256 matches the manifest;
- the manifest records commit, version, build type, format, and embedded API URL.

These runner checks do not call a model and should remain enabled. Report only the download/delivery result, workflow URL, filename, size, SHA256, and `download_url` when present; include other manifest fields only when requested or troubleshooting. Do not ask another agent to re-verify a successful delivery.

If `/data/build-targets.toml` includes:

```text
artifact_public_base_url=https://你的服务器域名或IP:端口/artifacts
```

the runner will emit both:

- local `file` path for retained server artifact storage
- public `download_url` for manager/user delivery

The server must expose `artifact_dir` read-only at that URL path. For ordinary debug APKs, this `download_url` is the primary handoff artifact.

## Failure Handoff

On a build failure, preserve the GitHub run URL and sanitized failing step summary. Send those facts to `manager`; keep environment/configuration issues with `ops` and assign `developer` only when the failure indicates a code or test defect. Do not involve both by default.

Read [references/configuration.md](references/configuration.md) only when setting up the server, GitHub token, release signing, or Feishu permission.
