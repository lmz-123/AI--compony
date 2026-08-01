---
name: build-tripcanvas-android
description: Build, validate, download, and deliver TripCanvas Android APK or AAB artifacts through an allowlisted GitHub Actions workflow and the current Feishu group. Use when users ask to package, build, generate, download, or send an Android app, APK, AAB, debug build, test build, or signed release for TripCanvas, including investigating failed mobile builds.
---

# Build TripCanvas Android

Use the bundled runner instead of manually composing GitHub API calls. It keeps the GitHub token out of commands and limits builds to targets and refs declared in `/data/build-targets.toml`.

## Safety and Ownership

- Read `/data/build-targets.toml` before acting. Do not build an undeclared repository, workflow, or ref.
- Default to `debug + apk`. A release requires explicit approval in the current user request, `allow_release = true`, and configured GitHub signing secrets.
- Build only committed and pushed code. Record the requested ref and resulting immutable commit.
- Never print `GITHUB_TOKEN`, keystore data, aliases, or signing passwords. The runner reads the token internally from `/data/state/.env`.
- Every Android build requires `AMAP_ANDROID_KEY` in the MyAPPs GitHub Actions secrets. Never request or place that value in chat, server TOML, or the repository.
- Do not publish to an app store. This workflow only produces and sends an artifact.
- Embed an API URL reachable by the target phone. Do not use `localhost` or `127.0.0.1` for a physical-device APK.

## Build Workflow

1. Confirm build type, output format, ref, API base URL, whether `AMAP_ANDROID_KEY` is configured in GitHub, and whether this is a test or release delivery.
2. For code changes, complete tests and push the requested ref before building.
3. Run a dry validation first:

```bash
python /app/skills/build-tripcanvas-android/scripts/build_android.py \
  --target tripcanvas-android \
  --ref main \
  --build-type debug \
  --format apk \
  --api-base-url https://api.example.com \
  --dry-run
```

4. Trigger, wait, download, verify, and send the APK:

```bash
python /app/skills/build-tripcanvas-android/scripts/build_android.py \
  --target tripcanvas-android \
  --ref main \
  --build-type debug \
  --format apk \
  --api-base-url https://api.example.com \
  --send-to-feishu
```

Use the real URL from `/data/build-targets.toml` or the manager's task. Never copy the example URL.

## Verification

The runner must verify all of the following before delivery:

- the matching workflow run completed successfully;
- the artifact is not expired and stays under the size limit;
- exactly one APK or AAB is present;
- the build manifest request ID and filename match;
- the package SHA256 matches the manifest;
- the manifest records commit, version, build type, format, and embedded API URL.

Report the workflow URL, immutable commit, app version, filename, size, SHA256, build type, API URL, and Feishu delivery result. Do not claim completion when the build passed but file delivery failed.

## Failure Handoff

On a build failure, preserve the GitHub run URL and sanitized failing step summary. Send those facts to `manager`; the manager may assign `ops` to diagnose environment/dependency failures or `developer` to fix code/test failures. After a fix, start a new request ID and build the new commit rather than reusing an old artifact.

Read [references/configuration.md](references/configuration.md) only when setting up the server, GitHub token, release signing, or Feishu permission.
