# Contributing to ClaudeTeam

Thanks for helping out! This project is under active development and PRs
are welcome.

## Before you start

- For anything substantial (a new command, a new CLI adapter, a refactor
  that moves files) please open an issue first so we can agree on the
  design.
- Read [`CLAUDE.md`](CLAUDE.md) — it documents the repo layout and the
  building rules every change is expected to follow.

## Development setup

```bash
git clone https://github.com/zylMozart/ClaudeTeam.git
cd ClaudeTeam
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

ClaudeTeam runs on the Python standard library only — there are no
runtime dependencies to install in order to run the test suite.

## The test gate (must stay green)

```bash
python3 tests/run.py        # needs Python 3.10+
```

A stdlib-only runner; it should report `tests: N passed, 0 failed`. CI
runs this same gate on Python 3.10–3.13 for every pull request, and a
failing gate blocks a merge.

If the `python3` on your machine is older than 3.10, invoke an explicit
interpreter, e.g. `python3.12 tests/run.py`.

## What every change ships with

The full rules live in [`CLAUDE.md`](CLAUDE.md); the short version:

1. **A unit test in the same commit.** Touching `commands/X.py`? Add
   `tests/unit/test_commands_X.py`.
2. **An operator playbook** in `tests/scenarios/` for any new public
   command (Given/When/Then, for human regression checks).
3. **Keep it small.** Prefer the smallest thing that works; don't add an
   abstraction until its third call site.

## Pull request checklist

- [ ] `python3 tests/run.py` is green.
- [ ] New modules/commands ship their unit test (and a playbook, if it's
      a public command).
- [ ] No internal-only notes left in comments (dates, ticket/round IDs,
      private chat or run names).
