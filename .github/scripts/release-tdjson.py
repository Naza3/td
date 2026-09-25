#!/usr/bin/env python3
"""Resolve existing version tags and publish six verified TDLib SDKs from a draft."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import tarfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile

TAG = re.compile(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?\Z")
SHA = re.compile(r"[0-9a-f]{40}\Z")
PLATFORMS = [f"{system}-{arch}" for system in ("windows", "linux", "macos") for arch in ("x64", "arm64")]


class APIError(RuntimeError):
    def __init__(self, status, message):
        super().__init__(f"GitHub API HTTP {status}: {message}")
        self.status = status


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("Refusing authenticated API redirect")


class GitHub:
    def __init__(self):
        repository = os.environ["GITHUB_REPOSITORY"]
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", repository):
            raise ValueError("Invalid GITHUB_REPOSITORY")
        self.base = f"/repos/{repository}"
        self.token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        if not self.token:
            raise ValueError("GH_TOKEN or GITHUB_TOKEN is required")
        self.opener = urllib.request.build_opener(NoRedirect())

    def request(self, method, path, data=None, file=None):
        url = path if path.startswith("https://") else "https://api.github.com" + path
        parsed = urllib.parse.urlsplit(url)
        if (parsed.scheme != "https" or parsed.hostname not in {"api.github.com", "uploads.github.com"}
                or parsed.username or parsed.password or parsed.port not in (None, 443)):
            raise ValueError("Refusing untrusted authenticated API URL")
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json",
                   "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "tdjson-release"}
        body = file.read_bytes() if file else json.dumps(data).encode() if data is not None else None
        if body is not None:
            headers["Content-Type"] = "application/octet-stream" if file else "application/json"
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=180) as response:
                result = response.read()
                return json.loads(result) if result else None
        except urllib.error.HTTPError as error:
            raise APIError(error.code, error.reason) from error


def check_tag(tag):
    match = TAG.fullmatch(tag)
    if not match or (match[4] and any(part.isdigit() and len(part) > 1 and part[0] == "0" for part in match[4].split("."))):
        raise ValueError("Release tag must be an existing vMAJOR.MINOR.PATCH[-prerelease][+build] tag")
    return match


def resolve_tag(api, tag):
    check_tag(tag)
    obj = api.request("GET", f"{api.base}/git/ref/tags/{urllib.parse.quote(tag, safe='')}")["object"]
    for _ in range(10):
        if not SHA.fullmatch(obj.get("sha", "")):
            raise ValueError("Tag resolved to an invalid object SHA")
        if obj.get("type") == "commit":
            commit = api.request("GET", f"{api.base}/git/commits/{obj['sha']}")
            if commit.get("sha") != obj["sha"]:
                raise ValueError("GitHub commit verification failed")
            return obj["sha"]
        if obj.get("type") != "tag":
            raise ValueError("Release tag must ultimately reference a commit")
        obj = api.request("GET", f"{api.base}/git/tags/{obj['sha']}")["object"]
    raise ValueError("Too many nested annotated tags")


def resolve(api=None):
    event = os.environ["GITHUB_EVENT_NAME"]
    tag = os.environ.get("GITHUB_REF_NAME", "") if event == "push" else os.environ.get("INPUT_RELEASE_TAG", "").strip() if event == "workflow_dispatch" else ""
    sha = resolve_tag(api or GitHub(), tag) if tag else os.environ["GITHUB_SHA"]
    if not SHA.fullmatch(sha):
        raise ValueError("Invalid source commit SHA")
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        output.write(f"release_tag={tag}\nsource_sha={sha}\n")
    print(f"Source: {tag or 'workflow commit'} at {sha}")


def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def validate(dist, tag, sha):
    version = ".".join(check_tag(tag).group(1, 2, 3))
    if not SHA.fullmatch(sha):
        raise ValueError("Invalid SOURCE_SHA")
    archives = {f"tdjson-{platform}{'.zip' if platform.startswith('windows') else '.tar.gz'}": platform for platform in PLATFORMS}
    expected = set(archives) | {name + ".sha256" for name in archives}
    if {path.name for path in dist.iterdir()} != expected or any(not (dist / name).is_file() or (dist / name).is_symlink() for name in expected):
        raise ValueError("Distribution must contain exactly six SDK archives and their six checksum files")
    result = {}
    for name, platform in archives.items():
        path = dist / name
        actual = digest(path)
        if (dist / (name + ".sha256")).read_text(encoding="ascii") != f"{actual}  {name}\n":
            raise ValueError(f"Checksum or checksum filename mismatch: {name}")
        member = f"tdjson-{platform}/BUILD-INFO.json"
        if name.endswith(".zip"):
            with zipfile.ZipFile(path) as archive:
                entries = [entry for entry in archive.infolist() if entry.filename == member]
                if len(entries) != 1 or entries[0].file_size > 65536:
                    raise ValueError(f"Missing, duplicate or oversized metadata: {name}")
                info = json.loads(archive.read(entries[0]))
        else:
            with tarfile.open(path, "r:gz") as archive:
                entries = [entry for entry in archive.getmembers() if entry.name == member]
                if len(entries) != 1 or not entries[0].isfile() or entries[0].size > 65536:
                    raise ValueError(f"Missing, duplicate or oversized metadata: {name}")
                info = json.load(archive.extractfile(entries[0]))
        if any(info.get(key) != value for key, value in {"version": version, "commit": sha, "platform": platform}.items()):
            raise ValueError(f"BUILD-INFO version/commit/platform mismatch: {name}")
    for name in sorted(expected):
        path = dist / name
        result[name] = {"path": path, "digest": "sha256:" + digest(path), "size": path.stat().st_size}
    return result


def matches(asset, local):
    return asset.get("state") == "uploaded" and asset.get("digest") == local["digest"] and asset.get("size") == local["size"]


def assets(api, release_id):
    result = {}
    page = 1
    while True:
        batch = api.request("GET", f"{api.base}/releases/{release_id}/assets?per_page=100&page={page}")
        for asset in batch:
            if asset["name"] in result:
                raise ValueError("Duplicate remote asset names")
            result[asset["name"]] = asset
        if len(batch) < 100:
            return result
        page += 1


def find_release(api, tag):
    try:
        return api.request("GET", f"{api.base}/releases/tags/{urllib.parse.quote(tag, safe='')}")
    except APIError as error:
        if error.status != 404:
            raise
    # The tag endpoint may omit drafts. Listing with the authenticated token can find them.
    matches = []
    page = 1
    while True:
        batch = api.request("GET", f"{api.base}/releases?per_page=100&page={page}")
        matches.extend(release for release in batch if release["tag_name"] == tag)
        if len(batch) < 100:
            if len(matches) > 1:
                raise ValueError("Multiple releases reference the same tag; refusing to guess")
            return matches[0] if matches else None
        page += 1


def publish(api, dist, tag, sha):
    local = validate(dist, tag, sha)  # No API mutation is allowed before all local checks pass.
    if resolve_tag(api, tag) != sha:
        raise ValueError("Tag moved since the build; refusing publication")
    release = find_release(api, tag)
    if release is None:
        run = os.environ.get("GITHUB_RUN_ID", "")
        repository = api.base.removeprefix("/repos/")
        notes = (f"TDLib JSON C API SDKs built from `{tag}` at `{sha}`.\n\n"
                 "Includes Windows, Linux and macOS SDKs for x64 and ARM64, with headers, libraries, CMake configuration and SHA-256 checksums.\n\n"
                 "Compatibility: Linux requires Ubuntu 24.04 or compatible runtime; macOS requires 15 or later. Windows uses the static MSVC runtime.\n\n"
                 "All six builds passed dynamic-library loading, JSON API and standalone CMake client smoke checks. Archive metadata and SHA-256 digests are verified before publication.")
        if run.isdigit():
            notes += f"\n\n[Build and verification](https://github.com/{repository}/actions/runs/{run})"
        if resolve_tag(api, tag) != sha:
            raise ValueError("Tag moved before draft creation; refusing publication")
        release = api.request("POST", f"{api.base}/releases", {"tag_name": tag, "target_commitish": sha,
                              "name": f"TDLib {tag}", "body": notes, "draft": True, "prerelease": bool(check_tag(tag)[4])})
    if release.get("tag_name") != tag:
        raise ValueError("Release tag does not match requested tag")
    remote = assets(api, release["id"])
    if not release["draft"]:
        if not all(name in remote and matches(remote[name], item) for name, item in local.items()):
            raise ValueError("Published release assets differ or have no verified digest; refusing to overwrite")
        print(f"Release already published with identical verified assets: {release['html_url']}")
        return
    upload = release["upload_url"].split("{", 1)[0]
    for name, item in local.items():
        if name in remote and matches(remote[name], item):
            continue
        if name in remote:
            api.request("DELETE", f"{api.base}/releases/assets/{remote[name]['id']}")
        if "sha256:" + digest(item["path"]) != item["digest"]:
            raise ValueError(f"Local asset changed after validation: {name}")
        asset = api.request("POST", upload + "?" + urllib.parse.urlencode({"name": name}), file=item["path"])
        if not matches(asset, item):
            raise ValueError(f"Uploaded asset failed server digest/size verification: {name}; release remains draft")
    remote = assets(api, release["id"])
    if not all(name in remote and matches(remote[name], item) for name, item in local.items()):
        raise ValueError("Final release asset verification failed; release remains draft")
    if resolve_tag(api, tag) != sha:
        raise ValueError("Tag moved during upload; release remains draft")
    prerelease = bool(check_tag(tag)[4])
    result = api.request("PATCH", f"{api.base}/releases/{release['id']}", {"draft": False,
                         "prerelease": prerelease, "make_latest": "false" if prerelease else "legacy"})
    print(f"Published {result['html_url']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("resolve", "publish"))
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    args = parser.parse_args()
    try:
        if args.command == "resolve":
            resolve()
        else:
            publish(GitHub(), args.dist, os.environ["RELEASE_TAG"], os.environ["SOURCE_SHA"])
    except (OSError, ValueError, KeyError, RuntimeError, tarfile.TarError, zipfile.BadZipFile) as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
