"""Tests for the bundled TripCanvas Android build runner."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "skills" / "build-tripcanvas-android" / "scripts" / "build_android.py"
SPEC = importlib.util.spec_from_file_location("tripcanvas_android_builder", SCRIPT)
builder = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = builder
SPEC.loader.exec_module(builder)


def _config(path: Path, *, allow_release: bool = False,
            api_url: str = "https://api.example.com"):
    path.write_text(
        "\n".join([
            'artifact_dir = "/tmp/artifacts"',
            'token_secret = "GITHUB_TOKEN"',
            "",
            "[[targets]]",
            'name = "tripcanvas-android"',
            'owner = "lmz-123"',
            'repository = "MyAPPs"',
            'workflow = "android-build.yml"',
            'default_ref = "main"',
            'allowed_refs = ["main"]',
            f'default_api_base_url = "{api_url}"',
            f"allow_release = {'true' if allow_release else 'false'}",
            "",
        ]),
        encoding="utf-8",
    )


def _args(**overrides):
    values = {
        "ref": "main",
        "build_type": "debug",
        "artifact_format": "apk",
        "api_base_url": "https://api.example.com",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_dry_run_validates_without_github_token():
    with tempfile.TemporaryDirectory() as tmp:
        config = Path(tmp) / "targets.toml"
        _config(config)
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--config", str(config),
             "--target", "tripcanvas-android", "--dry-run"],
            capture_output=True, text=True,
        )
        assert proc.returncode == 0
        assert '"repository": "lmz-123/MyAPPs"' in proc.stdout
        assert "GITHUB_TOKEN is missing" not in proc.stderr


def test_validate_rejects_release_when_disabled():
    config = {"artifact_dir": "/tmp/artifacts"}
    target = {
        "owner": "lmz-123", "repository": "MyAPPs", "workflow": "android-build.yml",
        "default_ref": "main", "allowed_refs": ["main"], "allow_release": False,
    }
    try:
        builder.validate(config, target, _args(build_type="release"))
        assert False, "release should be rejected"
    except builder.BuildError as e:
        assert "disabled" in str(e)


def test_validate_rejects_phone_unreachable_localhost():
    config = {"artifact_dir": "/tmp/artifacts"}
    target = {
        "owner": "lmz-123", "repository": "MyAPPs", "workflow": "android-build.yml",
        "default_ref": "main", "allowed_refs": ["main"], "allow_release": False,
    }
    try:
        builder.validate(config, target, _args(api_base_url="http://127.0.0.1:8000"))
        assert False, "localhost should be rejected"
    except builder.BuildError as e:
        assert "physical phone" in str(e)


def test_build_download_url_returns_none_when_not_configured():
    assert builder.build_download_url({}, "ct-test", "app.apk") is None


def test_build_download_url_builds_public_link():
    url = builder.build_download_url(
        {"artifact_public_base_url": "https://downloads.example.com/artifacts"},
        "ct-test",
        "TripCanvas debug.apk",
    )
    assert url == "https://downloads.example.com/artifacts/ct-test/TripCanvas%20debug.apk"


def test_build_download_url_rejects_invalid_base_url():
    try:
        builder.build_download_url({"artifact_public_base_url": "downloads.example.com"}, "ct-test", "app.apk")
        assert False, "invalid base url should be rejected"
    except builder.BuildError as e:
        assert "artifact_public_base_url" in str(e)


def test_extract_and_verify_accepts_matching_manifest_and_hash():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        package = b"verified apk bytes"
        digest = hashlib.sha256(package).hexdigest()
        manifest = {
            "request_id": "ct-test",
            "commit": "a" * 40,
            "file_name": "TripCanvas-debug-aaaaaaa.apk",
            "sha256": digest,
        }
        archive = root / "artifact.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr(manifest["file_name"], package)
            zf.writestr("build-manifest.json", json.dumps(manifest))
        output, actual = builder.extract_and_verify(
            archive, root / "out", "ct-test", "apk", "a" * 40)
        assert output.read_bytes() == package
        assert actual["sha256"] == digest


def test_extract_and_verify_rejects_hash_mismatch():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        manifest = {
            "request_id": "ct-test", "commit": "a" * 40,
            "file_name": "TripCanvas-debug-aaaaaaa.apk", "sha256": "0" * 64,
        }
        archive = root / "artifact.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr(manifest["file_name"], b"different")
            zf.writestr("build-manifest.json", json.dumps(manifest))
        try:
            builder.extract_and_verify(archive, root / "out", "ct-test", "apk", "a" * 40)
            assert False, "hash mismatch should be rejected"
        except builder.BuildError as e:
            assert "SHA256" in str(e)
