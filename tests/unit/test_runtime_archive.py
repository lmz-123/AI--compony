"""Tests for runtime/archive.py — fire/rehire workspace archival."""
from __future__ import annotations

from datetime import datetime

from claudeteam.runtime import archive, paths
from helpers import isolated_env


def _fixed_now():
    return datetime(2026, 6, 13, 9, 30, 0)


def test_archive_moves_workspace_and_writes_records():
    with isolated_env():
        ws = paths.agent_dir("x")
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "identity.md").write_text("I am x", encoding="utf-8")
        (ws / "memory.jsonl").write_text("{}", encoding="utf-8")

        dst = archive.archive_agent(
            "x", {"cli": "claude-code", "model": "opus", "role": "专家"},
            executor="manager", now=_fixed_now)

        # dated archive dir
        assert dst.name == "x_20260613"
        assert dst.parent == paths.state_dir() / "agents" / "_archived"
        # workspace moved (original gone), files preserved
        assert not ws.exists()
        assert (dst / "identity.md").read_text() == "I am x"
        # records written
        assert (dst / "_roster.json").exists()
        term = (dst / "_termination.md").read_text()
        assert "agent: `x`" in term
        assert "executor: manager" in term
        assert "model: `opus`" in term


def test_archive_no_workspace_still_records():
    """An agent with no workspace dir (already archived / never ran) still
    gets an archive dir holding the termination record."""
    with isolated_env():
        dst = archive.archive_agent("y", {"cli": "kimi-code"}, now=_fixed_now)
        assert dst.exists()
        assert (dst / "_termination.md").exists()


def test_archive_same_day_twice_does_not_clobber():
    with isolated_env():
        paths.agent_dir("x").mkdir(parents=True, exist_ok=True)
        d1 = archive.archive_agent("x", {"cli": "c"}, now=_fixed_now)
        paths.agent_dir("x").mkdir(parents=True, exist_ok=True)
        d2 = archive.archive_agent("x", {"cli": "c"}, now=_fixed_now)
        assert d1 != d2          # time-suffixed variant
        assert d1.exists() and d2.exists()


def test_find_archived_returns_latest():
    with isolated_env():
        assert archive.find_archived("x") is None
        paths.agent_dir("x").mkdir(parents=True, exist_ok=True)
        archive.archive_agent("x", {"cli": "c"}, now=lambda: datetime(2026, 6, 1))
        paths.agent_dir("x").mkdir(parents=True, exist_ok=True)
        latest = archive.archive_agent("x", {"cli": "c"}, now=lambda: datetime(2026, 6, 13))
        assert archive.find_archived("x") == latest


def test_restore_workspace_moves_business_files_back_not_records():
    with isolated_env():
        ws = paths.agent_dir("x")
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "memory.jsonl").write_text("keep me", encoding="utf-8")
        dst = archive.archive_agent("x", {"cli": "c"}, now=_fixed_now)
        assert not ws.exists()

        archive.restore_workspace(dst, "x")
        # business file restored
        assert (paths.agent_dir("x") / "memory.jsonl").read_text() == "keep me"
        # archive metadata NOT dragged into the live workspace
        assert not (paths.agent_dir("x") / "_roster.json").exists()
        assert not (paths.agent_dir("x") / "_termination.md").exists()
        # archive tombstone left in place for audit
        assert (dst / "_termination.md").exists()


def test_find_archived_ignores_prefix_named_agents():
    """`find_archived('alice')` must NOT match the archive of 'alice_ai' —
    a prefix collision would restore the wrong agent's workspace on rehire."""
    with isolated_env():
        for name in ("alice", "alice_ai", "alice_bob"):
            paths.agent_dir(name).mkdir(parents=True, exist_ok=True)
            archive.archive_agent(name, {"cli": "c", "who": name}, now=_fixed_now)
        found = archive.find_archived("alice")
        assert found is not None
        # the dir is exactly alice_<stamp>, not alice_ai_<stamp>
        assert found.name.startswith("alice_2")
        assert archive.read_roster_stash(found)["who"] == "alice"
        # and the longer names still resolve to their own archives
        assert archive.read_roster_stash(archive.find_archived("alice_ai"))["who"] == "alice_ai"


def test_archive_stashes_and_reads_verbatim_block():
    with isolated_env():
        paths.agent_dir("x").mkdir(parents=True, exist_ok=True)
        block = '[team.agents.x]\ncli   = "kimi-code"\nnotes = """\na\nb\n"""\n'
        dst = archive.archive_agent("x", {"cli": "kimi-code"},
                                    block_text=block, now=_fixed_now)
        assert (dst / "_roster.toml").read_text() == block
        assert archive.read_roster_block(dst) == block
        # absent block stash → "" (dict fallback)
        dst2 = archive.archive_agent("y", {"cli": "c"}, now=_fixed_now)
        assert archive.read_roster_block(dst2) == ""


def test_read_roster_stash_roundtrip_and_missing():
    with isolated_env():
        paths.agent_dir("x").mkdir(parents=True, exist_ok=True)
        dst = archive.archive_agent(
            "x", {"cli": "codex-cli", "model": "gpt-5.5"}, now=_fixed_now)
        assert archive.read_roster_stash(dst) == {"cli": "codex-cli", "model": "gpt-5.5"}
        # missing stash → {}
        empty = paths.state_dir() / "agents" / "_archived" / "nope"
        empty.mkdir(parents=True, exist_ok=True)
        assert archive.read_roster_stash(empty) == {}
