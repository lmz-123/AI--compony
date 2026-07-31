# Security Policy

ClaudeTeam handles Feishu application credentials (App ID / App Secret,
tenant access tokens) and drives local processes through tmux, so we take
security reports seriously.

## Reporting a vulnerability

**Please do not open a public GitHub issue for a security vulnerability.**

Report it privately through GitHub's
[private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability):
open the repository's **Security** tab → **Report a vulnerability**. That
keeps the details private until a fix is available.

We aim to acknowledge reports within a few days.

## Worth reporting

- Leakage of Feishu credentials or tenant tokens (e.g. world-readable
  cache files, secrets written to logs or chat).
- Command or argument injection reachable from a chat message or a config
  value.
- Path traversal in the file-backed store, or any write outside
  `$CLAUDETEAM_STATE_DIR`.

## Handling your own secrets

- Keep real `App ID` / `App Secret` out of commits — use `.env` (which is
  gitignored) or `claudeteam.toml` (also kept out of version control).
- The cached tenant token is written owner-only (`0600`) under your temp
  directory; don't relax those permissions on a shared host.
