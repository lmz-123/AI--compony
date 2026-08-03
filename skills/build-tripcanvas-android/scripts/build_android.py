#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

try:
    import tomllib
except ImportError:
    import tomli as tomllib


API_ROOT = "https://api.github.com"
MAX_ARTIFACT_BYTES = 250 * 1024 * 1024
SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")


class BuildError(RuntimeError):
    pass


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class GitHub:
    def __init__(self, token: str, owner: str, repository: str):
        self.token = token
        self.repo_path = f"/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(repository)}"

    def _request(self, method: str, path: str, payload: dict | None = None,
                 *, accept: str = "application/vnd.github+json"):
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(
            API_ROOT + path,
            data=data,
            method=method,
            headers={
                "Accept": accept,
                "Authorization": f"Bearer {self.token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "ai-company-tripcanvas-builder",
                **({"Content-Type": "application/json"} if data else {}),
            },
        )
        try:
            return urllib.request.urlopen(req, timeout=60)
        except urllib.error.HTTPError as e:
            try:
                detail = json.loads(e.read(4096).decode("utf-8", "replace")).get("message", "")
            except (ValueError, OSError):
                detail = ""
            raise BuildError(f"GitHub API {e.code} for {method} {path}: {detail or e.reason}") from None
        except urllib.error.URLError as e:
            raise BuildError(f"GitHub API request failed: {e.reason}") from None

    def json(self, method: str, path: str, payload: dict | None = None) -> dict:
        with self._request(method, path, payload) as response:
            body = response.read()
        return json.loads(body.decode()) if body else {}

    def dispatch(self, workflow: str, ref: str, inputs: dict) -> None:
        path = f"{self.repo_path}/actions/workflows/{urllib.parse.quote(workflow)}/dispatches"
        with self._request("POST", path, {"ref": ref, "inputs": inputs}) as response:
            if response.status not in (200, 201, 204):
                raise BuildError(f"Unexpected workflow dispatch status: {response.status}")

    def find_run(self, workflow: str, request_id: str) -> dict | None:
        query = urllib.parse.urlencode({"event": "workflow_dispatch", "per_page": 30})
        path = f"{self.repo_path}/actions/workflows/{urllib.parse.quote(workflow)}/runs?{query}"
        for run in self.json("GET", path).get("workflow_runs", []):
            if request_id in str(run.get("display_title", "")):
                return run
        return None

    def run(self, run_id: int) -> dict:
        return self.json("GET", f"{self.repo_path}/actions/runs/{run_id}")

    def artifacts(self, run_id: int) -> list[dict]:
        data = self.json("GET", f"{self.repo_path}/actions/runs/{run_id}/artifacts?per_page=20")
        return list(data.get("artifacts", []))

    def download(self, artifact_id: int, destination: Path) -> None:
        path = f"{self.repo_path}/actions/artifacts/{artifact_id}/zip"
        req = urllib.request.Request(
            API_ROOT + path,
            method="GET",
            headers={
                "Accept": "application/octet-stream",
                "Authorization": f"Bearer {self.token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "ai-company-tripcanvas-builder",
            },
        )
        opener = urllib.request.build_opener(NoRedirectHandler)
        try:
            response = opener.open(req, timeout=60)
        except urllib.error.HTTPError as e:
            if e.code not in (301, 302, 303, 307, 308):
                try:
                    detail = json.loads(e.read(4096).decode("utf-8", "replace")).get("message", "")
                except (ValueError, OSError):
                    detail = ""
                raise BuildError(f"GitHub artifact API {e.code}: {detail or e.reason}") from None
            location = e.headers.get("Location")
            if not location:
                raise BuildError("GitHub artifact redirect did not include a Location header") from None
            storage_req = urllib.request.Request(
                location,
                method="GET",
                headers={"User-Agent": "ai-company-tripcanvas-builder"},
            )
            try:
                response = urllib.request.urlopen(storage_req, timeout=120)
            except urllib.error.HTTPError as storage_error:
                raise BuildError(
                    f"GitHub artifact storage download failed: HTTP {storage_error.code} {storage_error.reason}") from None
            except urllib.error.URLError as storage_error:
                raise BuildError(f"GitHub artifact storage download failed: {storage_error.reason}") from None
        except urllib.error.URLError as e:
            raise BuildError(f"GitHub artifact API request failed: {e.reason}") from None
        with response:
            total = 0
            with destination.open("xb") as output:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_ARTIFACT_BYTES:
                        raise BuildError("Downloaded artifact exceeds 250 MiB")
                    output.write(chunk)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build and deliver an allowlisted TripCanvas Android artifact")
    parser.add_argument("--config", default="/data/build-targets.toml")
    parser.add_argument("--target", required=True)
    parser.add_argument("--ref", default="")
    parser.add_argument("--build-type", choices=("debug", "release"), default="debug")
    parser.add_argument("--format", dest="artifact_format", choices=("apk", "aab"), default="apk")
    parser.add_argument("--api-base-url", default="")
    parser.add_argument("--timeout", type=int, default=2400)
    parser.add_argument("--poll-seconds", type=int, default=10)
    parser.add_argument("--send-to-feishu", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def load_config(path: Path, target_name: str) -> tuple[dict, dict]:
    try:
        with path.open("rb") as handle:
            config = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise BuildError(f"Cannot read build target config {path}: {e}") from None
    for target in config.get("targets", []):
        if target.get("name") == target_name:
            return config, target
    raise BuildError(f"Build target is not allowlisted: {target_name}")


def validate(config: dict, target: dict, args: argparse.Namespace) -> tuple[str, str, Path]:
    for key in ("owner", "repository", "workflow"):
        if not SAFE_NAME.fullmatch(str(target.get(key, ""))):
            raise BuildError(f"Invalid or missing target field: {key}")
    ref = args.ref or str(target.get("default_ref", ""))
    allowed_refs = [str(item) for item in target.get("allowed_refs", [])]
    if not ref or ref not in allowed_refs:
        raise BuildError(f"Ref is not allowlisted: {ref or '<empty>'}")
    if args.build_type == "debug" and args.artifact_format != "apk":
        raise BuildError("Debug builds must use APK; AAB requires release")
    if args.build_type == "release" and not target.get("allow_release", False):
        raise BuildError("Release builds are disabled for this target")
    api_url = args.api_base_url or str(target.get("default_api_base_url", ""))
    if not re.fullmatch(r"https?://[^\s]+", api_url):
        raise BuildError("A phone-reachable absolute http(s) API base URL is required")
    if re.match(r"https?://(localhost|127\.0\.0\.1)(:|/|$)", api_url, re.I):
        raise BuildError("localhost/127.0.0.1 is not reachable from a physical phone")
    artifact_dir = Path(str(config.get("artifact_dir", "/data/artifacts")))
    if not artifact_dir.is_absolute() or artifact_dir == Path("/"):
        raise BuildError("artifact_dir must be a specific absolute path")
    return ref, api_url.rstrip("/"), artifact_dir


def build_download_url(config: dict, request_id: str, package_name: str) -> str | None:
    base_url = str(config.get("artifact_public_base_url", "") or "").strip()
    if not base_url:
        return None
    if not re.fullmatch(r"https?://[^\s]+", base_url):
        raise BuildError("artifact_public_base_url must be an absolute http(s) URL")
    safe_request_id = urllib.parse.quote(request_id, safe="")
    safe_package_name = urllib.parse.quote(package_name, safe="")
    return f"{base_url.rstrip('/')}/{safe_request_id}/{safe_package_name}"


def load_token(secret_name: str) -> str:
    if not re.fullmatch(r"[A-Z][A-Z0-9_]+", secret_name):
        raise BuildError("Invalid token_secret name")
    value = ""
    try:
        from claudeteam.runtime.agent_auth import load_secrets
        value = load_secrets().get(secret_name, "")
    except ImportError:
        pass
    value = value or os.environ.get(secret_name, "")
    if not value:
        raise BuildError(f"{secret_name} is missing from the private secrets file")
    return value


def wait_for_run(client: GitHub, workflow: str, request_id: str,
                 timeout: int, poll_seconds: int) -> dict:
    deadline = time.monotonic() + timeout
    run = None
    while time.monotonic() < deadline:
        run = run or client.find_run(workflow, request_id)
        if run:
            run = client.run(int(run["id"]))
            status = str(run.get("status", "unknown"))
            print(f"workflow status: {status}", flush=True)
            if status == "completed":
                if run.get("conclusion") != "success":
                    raise BuildError(
                        f"Workflow failed ({run.get('conclusion')}): {run.get('html_url', '')}")
                return run
        time.sleep(poll_seconds)
    raise BuildError(f"Timed out waiting for workflow request {request_id}")


def select_artifact(artifacts: list[dict], run_id: int) -> dict:
    expected = f"tripcanvas-android-"
    matches = [item for item in artifacts if str(item.get("name", "")).startswith(expected)]
    if len(matches) != 1:
        raise BuildError(f"Expected one Android artifact for run {run_id}, found {len(matches)}")
    artifact = matches[0]
    if artifact.get("expired"):
        raise BuildError("GitHub artifact has expired")
    size = int(artifact.get("size_in_bytes", 0) or 0)
    if size <= 0 or size > MAX_ARTIFACT_BYTES:
        raise BuildError("GitHub artifact size is invalid or exceeds 250 MiB")
    return artifact


def extract_and_verify(zip_path: Path, output_dir: Path, request_id: str,
                       expected_format: str, head_sha: str) -> tuple[Path, dict]:
    try:
        archive = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile:
        raise BuildError("Downloaded artifact is not a valid ZIP") from None
    with archive:
        package_infos = [i for i in archive.infolist()
                         if not i.is_dir() and Path(i.filename).suffix == f".{expected_format}"]
        manifest_infos = [i for i in archive.infolist()
                          if not i.is_dir() and Path(i.filename).name == "build-manifest.json"]
        if len(package_infos) != 1 or len(manifest_infos) != 1:
            raise BuildError("Artifact must contain exactly one package and one build manifest")
        package_info = package_infos[0]
        if package_info.file_size <= 0 or package_info.file_size > 200 * 1024 * 1024:
            raise BuildError("Android package is empty or exceeds 200 MiB")
        try:
            manifest = json.loads(archive.read(manifest_infos[0]).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise BuildError("Build manifest is invalid") from None
        package_name = Path(package_info.filename).name
        if manifest.get("request_id") != request_id or manifest.get("file_name") != package_name:
            raise BuildError("Build manifest does not match this request or package")
        if head_sha and manifest.get("commit") != head_sha:
            raise BuildError("Build manifest commit does not match the workflow commit")
        output_dir.mkdir(parents=True, exist_ok=False)
        package_path = output_dir / package_name
        digest = hashlib.sha256()
        with archive.open(package_info) as source, package_path.open("xb") as destination:
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                destination.write(chunk)
        if digest.hexdigest() != manifest.get("sha256"):
            raise BuildError("Package SHA256 does not match the build manifest")
        (output_dir / "build-manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return package_path, manifest


def main() -> int:
    args = parse_args()
    try:
        if not 60 <= args.timeout <= 3600 or not 5 <= args.poll_seconds <= 60:
            raise BuildError("timeout must be 60..3600 and poll-seconds must be 5..60")
        config, target = load_config(Path(args.config), args.target)
        ref, api_url, artifact_dir = validate(config, target, args)
        request_id = "ct-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + secrets.token_hex(4)
        plan = {
            "target": args.target,
            "repository": f"{target['owner']}/{target['repository']}",
            "workflow": target["workflow"],
            "ref": ref,
            "build_type": args.build_type,
            "artifact_format": args.artifact_format,
            "api_base_url": api_url,
            "artifact_dir": str(artifact_dir),
            "request_id": request_id,
            "send_to_feishu": args.send_to_feishu,
        }
        print(json.dumps({"build_plan": plan}, ensure_ascii=False))
        if args.dry_run:
            return 0

        token = load_token(str(config.get("token_secret", "GITHUB_TOKEN")))
        client = GitHub(token, str(target["owner"]), str(target["repository"]))
        client.dispatch(str(target["workflow"]), ref, {
            "request_id": request_id,
            "build_type": args.build_type,
            "artifact_format": args.artifact_format,
            "api_base_url": api_url,
        })
        print(f"workflow dispatched: {request_id}", flush=True)
        run = wait_for_run(client, str(target["workflow"]), request_id,
                           args.timeout, args.poll_seconds)
        artifact = select_artifact(client.artifacts(int(run["id"])), int(run["id"]))
        artifact_dir.mkdir(parents=True, exist_ok=True)
        zip_path = artifact_dir / f".{request_id}.zip"
        client.download(int(artifact["id"]), zip_path)
        try:
            package_path, manifest = extract_and_verify(
                zip_path, artifact_dir / request_id, request_id,
                args.artifact_format, str(run.get("head_sha", "")))
        finally:
            try:
                zip_path.unlink()
            except OSError:
                pass

        if args.send_to_feishu:
            result = subprocess.run(
                ["claudeteam", "feishu", "send-file", str(package_path),
                 "--name", package_path.name], check=False)
            if result.returncode != 0:
                raise BuildError(f"Build succeeded but Feishu delivery failed; artifact kept at {package_path}")

        summary = {
            "workflow_url": run.get("html_url"),
            "commit": manifest.get("commit"),
            "version": manifest.get("version"),
            "file": str(package_path),
            "download_url": build_download_url(config, request_id, package_path.name),
            "size_bytes": package_path.stat().st_size,
            "sha256": manifest.get("sha256"),
            "build_type": manifest.get("build_type"),
            "artifact_format": manifest.get("artifact_format"),
            "api_base_url": manifest.get("api_base_url"),
            "sent_to_feishu": args.send_to_feishu,
        }
        print(json.dumps({"build_result": summary}, ensure_ascii=False))
        return 0
    except BuildError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
