#!/usr/bin/env python3
"""Offline regression tests for release integrity and safe publication ordering."""
import importlib.util
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import urllib.parse
import zipfile

SPEC = importlib.util.spec_from_file_location("release_tdjson", Path(__file__).with_name("release-tdjson.py"))
r = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(r)
SHA = "a" * 40
TAG = "v1.8.0"


def fixtures(dist, override=None, missing_info=None):
    for platform in r.PLATFORMS:
        info = {"platform": platform, "version": "1.8.0", "commit": SHA}
        if override and platform == "windows-x64":
            info.update(override)
        name = f"tdjson-{platform}" + (".zip" if platform.startswith("windows") else ".tar.gz")
        path = dist / name
        data = json.dumps(info).encode()
        member = f"tdjson-{platform}/BUILD-INFO.json" if platform != missing_info else "unexpected.json"
        if name.endswith(".zip"):
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr(member, data)
        else:
            with tarfile.open(path, "w:gz") as archive:
                item = tarfile.TarInfo(member)
                item.size = len(data)
                archive.addfile(item, io.BytesIO(data))
        (dist / (name + ".sha256")).write_text(f"{r.digest(path)}  {name}\n", encoding="ascii")


class FakeGitHub:
    base = "/repos/example/td"

    def __init__(self, release=None):
        self.release = release
        self.remote = {}
        self.calls = []
        self.sha = SHA
        self.annotated = False
        self.object_type = "commit"
        self.missing_digest = False
        self.move_on_upload = False
        self.next_id = 1

    def request(self, method, path, data=None, file=None):
        self.calls.append((method, path))
        suffix = path.removeprefix(self.base)
        if method == "GET":
            if suffix.startswith("/git/ref/tags/"):
                return {"object": {"type": "tag" if self.annotated else self.object_type, "sha": "c" * 40 if self.annotated else self.sha}}
            if suffix.startswith("/git/tags/"):
                return {"object": {"type": self.object_type, "sha": self.sha}}
            if suffix.startswith("/git/commits/"):
                return {"sha": self.sha}
            if suffix.startswith("/releases/tags/"):
                if self.release and not self.release["draft"]:
                    return self.release.copy()
                raise r.APIError(404, "Not found")
            if suffix.startswith("/releases?per_page="):
                return [self.release.copy()] if self.release else []
            if suffix.startswith("/releases/7/assets?"):
                return list(self.remote.values())
        if method == "POST" and suffix == "/releases":
            self.release = dict(data, id=7, upload_url="https://uploads.github.com/repos/example/td/releases/7/assets{?name,label}", html_url="https://github.com/example/td/releases/tag/" + TAG)
            return self.release.copy()
        if method == "POST" and path.startswith("https://uploads.github.com/"):
            name = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)["name"][0]
            item = {"id": self.next_id, "name": name, "digest": "sha256:" + r.digest(file), "size": file.stat().st_size, "state": "uploaded"}
            self.next_id += 1
            if self.missing_digest:
                item.pop("digest")
            if self.move_on_upload:
                self.sha = "b" * 40
            self.remote[name] = item
            return item.copy()
        if method == "DELETE":
            asset_id = int(suffix.rsplit("/", 1)[1])
            name = next(name for name, asset in self.remote.items() if asset["id"] == asset_id)
            del self.remote[name]
            return None
        if method == "PATCH" and suffix == "/releases/7":
            self.release.update(data)
            return self.release.copy()
        raise AssertionError(f"Unexpected API call {method} {path}")

    def mutations(self):
        return [(method, path) for method, path in self.calls if method != "GET"]


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dist = Path(self.temp.name)
        fixtures(self.dist)

    def populate_remote(self, api, draft=False):
        api.release = {"id": 7, "tag_name": TAG, "draft": draft, "html_url": "https://github.com/example/td/releases/tag/" + TAG,
                       "upload_url": "https://uploads.github.com/repos/example/td/releases/7/assets{?name,label}"}
        for name, item in r.validate(self.dist, TAG, SHA).items():
            api.remote[name] = dict(item, name=name, state="uploaded", id=api.next_id)
            api.next_id += 1

    def test_exact_six_platforms_and_core_prerelease_version(self):
        self.assertEqual(len(r.validate(self.dist, TAG, SHA)), 12)
        self.assertEqual(len(r.validate(self.dist, TAG + "-rc.1+build.2", SHA)), 12)

    def test_missing_archive_and_extra_file(self):
        path = self.dist / "tdjson-windows-x64.zip"
        original = path.read_bytes()
        path.unlink()
        with self.assertRaisesRegex(ValueError, "exactly six"):
            r.validate(self.dist, TAG, SHA)
        path.write_bytes(original)
        (self.dist / "unexpected.txt").write_text("extra")
        with self.assertRaisesRegex(ValueError, "exactly six"):
            r.validate(self.dist, TAG, SHA)

    def test_checksum_rejects_changed_contents_or_filename(self):
        path = self.dist / "tdjson-windows-x64.zip.sha256"
        original = path.read_text()
        for contents in ("0" * 64 + original[64:], original.replace("tdjson-windows-x64.zip", "wrong.zip")):
            with self.subTest(contents=contents):
                path.write_text(contents)
                with self.assertRaisesRegex(ValueError, "Checksum"):
                    r.validate(self.dist, TAG, SHA)

    def test_metadata_must_match_platform_version_and_source(self):
        for field, value in (("platform", "linux-x64"), ("version", "1.8.1"), ("commit", "b" * 40)):
            with self.subTest(field=field):
                fixtures(self.dist, {field: value})
                with self.assertRaisesRegex(ValueError, "BUILD-INFO"):
                    r.validate(self.dist, TAG, SHA)

    def test_missing_metadata_in_zip_and_tar(self):
        for platform in ("windows-x64", "linux-x64"):
            with self.subTest(platform=platform):
                fixtures(self.dist, missing_info=platform)
                with self.assertRaisesRegex(ValueError, "metadata"):
                    r.validate(self.dist, TAG, SHA)

    def test_lightweight_and_annotated_tags(self):
        api = FakeGitHub()
        self.assertEqual(r.resolve_tag(api, TAG), SHA)
        api.annotated = True
        self.assertEqual(r.resolve_tag(api, TAG), SHA)
        api.object_type = "tree"
        with self.assertRaisesRegex(ValueError, "commit"):
            r.resolve_tag(api, TAG)

    def test_invalid_tags_are_rejected_before_api(self):
        for tag in ("latest", "v1.8", "v01.8.0", "v1.8.0/evil", "v1.8.0\n", "v1.8.0-01"):
            api = FakeGitHub()
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                r.resolve_tag(api, tag)
            self.assertEqual(api.calls, [])

    def test_resolve_manual_tag_and_pr_source(self):
        output = self.dist / "output"
        env = {"GITHUB_EVENT_NAME": "workflow_dispatch", "INPUT_RELEASE_TAG": TAG, "GITHUB_SHA": "b" * 40, "GITHUB_OUTPUT": str(output)}
        with patch.dict(os.environ, env):
            r.resolve(FakeGitHub())
        self.assertEqual(output.read_text(), f"release_tag={TAG}\nsource_sha={SHA}\n")
        output.unlink()
        env["GITHUB_EVENT_NAME"] = "pull_request"
        with patch.dict(os.environ, env):
            r.resolve()
        self.assertEqual(output.read_text(), f"release_tag=\nsource_sha={'b' * 40}\n")

    def test_all_local_checks_before_api(self):
        fixtures(self.dist, {"commit": "b" * 40})
        api = FakeGitHub()
        with self.assertRaises(ValueError):
            r.publish(api, self.dist, TAG, SHA)
        self.assertEqual(api.calls, [])

    def test_tag_movement_before_publish_has_no_mutation(self):
        api = FakeGitHub()
        api.sha = "b" * 40
        with self.assertRaisesRegex(ValueError, "Tag moved"):
            r.publish(api, self.dist, TAG, SHA)
        self.assertEqual(api.mutations(), [])

    def test_create_draft_upload_twelve_then_publish(self):
        api = FakeGitHub()
        r.publish(api, self.dist, TAG, SHA)
        changes = api.mutations()
        self.assertEqual(len(changes), 14)
        self.assertEqual(changes[0], ("POST", api.base + "/releases"))
        self.assertTrue(all(method == "POST" and path.startswith("https://uploads.github.com/") for method, path in changes[1:13]))
        self.assertEqual(changes[-1], ("PATCH", api.base + "/releases/7"))
        self.assertFalse(api.release["draft"])
        self.assertEqual(api.release["make_latest"], "legacy")

    def test_missing_server_digest_leaves_draft(self):
        api = FakeGitHub()
        api.missing_digest = True
        with self.assertRaisesRegex(ValueError, "digest/size"):
            r.publish(api, self.dist, TAG, SHA)
        self.assertTrue(api.release["draft"])
        self.assertFalse(any(method == "PATCH" for method, _ in api.calls))

    def test_prerelease_does_not_become_latest(self):
        api = FakeGitHub()
        r.publish(api, self.dist, TAG + "-rc.1", SHA)
        self.assertTrue(api.release["prerelease"])
        self.assertEqual(api.release["make_latest"], "false")

    def test_tag_movement_during_upload_leaves_draft(self):
        api = FakeGitHub()
        api.move_on_upload = True
        with self.assertRaisesRegex(ValueError, "Tag moved during"):
            r.publish(api, self.dist, TAG, SHA)
        self.assertTrue(api.release["draft"])
        self.assertFalse(any(method == "PATCH" for method, _ in api.calls))

    def test_existing_draft_resumes_and_replaces_only_bad_asset(self):
        api = FakeGitHub()
        self.populate_remote(api, draft=True)
        api.remote["tdjson-windows-x64.zip"]["digest"] = "sha256:" + "0" * 64
        r.publish(api, self.dist, TAG, SHA)
        self.assertEqual([method for method, _ in api.mutations()], ["DELETE", "POST", "PATCH"])
        self.assertFalse(api.release["draft"])

    def test_identical_published_release_is_read_only(self):
        api = FakeGitHub()
        self.populate_remote(api)
        r.publish(api, self.dist, TAG, SHA)
        self.assertEqual(api.mutations(), [])

    def test_published_assets_cannot_be_overwritten(self):
        api = FakeGitHub()
        self.populate_remote(api)
        api.remote["tdjson-windows-x64.zip"]["digest"] = "sha256:" + "0" * 64
        with self.assertRaisesRegex(ValueError, "refusing to overwrite"):
            r.publish(api, self.dist, TAG, SHA)
        self.assertEqual(api.mutations(), [])

    def test_token_not_sent_to_untrusted_host_or_redirect(self):
        with patch.dict(os.environ, {"GITHUB_REPOSITORY": "example/td", "GH_TOKEN": "test-token"}):
            api = r.GitHub()
        with self.assertRaisesRegex(ValueError, "untrusted"):
            api.request("POST", "https://example.org/upload")
        with self.assertRaisesRegex(RuntimeError, "redirect"):
            r.NoRedirect().redirect_request(None, None, 302, "redirect", None, "https://example.org")


if __name__ == "__main__":
    unittest.main()
