# Android Build Configuration

## Server Target

Copy `deploy/server/examples/build-targets.toml` to `/data/build-targets.toml`. Set `default_api_base_url` to the public TripCanvas API URL reachable from an Android phone. Keep `allow_release = false` until signing is configured and tested.

Store a fine-grained GitHub token in `/data/state/.env`:

```text
GITHUB_TOKEN=<fine-grained token>
```

Grant the token access only to `lmz-123/MyAPPs`, with Contents read and Actions read/write. Do not put the token in TOML, playbooks, commands, or chat.

## Android Map Key

Every Android build requires this Actions secret in `lmz-123/MyAPPs`:

```text
AMAP_ANDROID_KEY
```

Configure it in the GitHub repository settings. Do not put it in the AI Company server `.env`, build target TOML, repository, commands, or chat. The workflow injects it only into the Android build step.

## Feishu

The bot needs `im:resource` in addition to its existing send permission. Add the permission in the Feishu console, publish/approve the new app version, and recreate the AI Company container after updating the code. `claudeteam feishu send-file` only accepts regular files under `/data/artifacts` and files no larger than 200 MiB.

## Release Signing

Configure these additional Actions secrets in `lmz-123/MyAPPs` for release builds:

```text
ANDROID_KEYSTORE_BASE64
ANDROID_KEY_ALIAS
ANDROID_KEY_PASSWORD
ANDROID_STORE_PASSWORD
```

Generate `ANDROID_KEYSTORE_BASE64` from the keystore without committing either value. After the secrets are configured and a signed package has been independently verified, set `allow_release = true` in `/data/build-targets.toml`.
