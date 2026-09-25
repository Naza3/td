#!/usr/bin/env python3
"""Load the extracted native SDK and exercise its JSON API without networking."""
import argparse
import ctypes
import json
import os
from pathlib import Path
import platform
import time


def legacy_version(sdk):
    """Read v1.8.0's version without initializing Telegram or logging in."""
    sdk.td_json_client_create.argtypes = []
    sdk.td_json_client_create.restype = ctypes.c_void_p
    sdk.td_json_client_send.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    sdk.td_json_client_send.restype = None
    sdk.td_json_client_receive.argtypes = [ctypes.c_void_p, ctypes.c_double]
    sdk.td_json_client_receive.restype = ctypes.c_char_p
    sdk.td_json_client_destroy.argtypes = [ctypes.c_void_p]
    sdk.td_json_client_destroy.restype = None
    client = sdk.td_json_client_create()
    if not client:
        raise RuntimeError('Could not create a legacy JSON client')
    try:
        sdk.td_json_client_send(client, b'{"@type":"getCurrentState","@extra":"sdk-version-check"}')
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            raw = sdk.td_json_client_receive(client, 0.5)
            if not raw:
                continue
            response = json.loads(raw)
            if response.get('@extra') != 'sdk-version-check':
                continue
            if response.get('@type') != 'updates':
                raise RuntimeError(f'Unexpected legacy getCurrentState response: {response}')
            for update in response.get('updates', []):
                if update.get('@type') == 'updateOption' and update.get('name') == 'version':
                    return update.get('value', {})
            raise RuntimeError(f'Legacy current state did not contain the version: {response}')
        raise RuntimeError('Timed out reading the legacy TDLib version')
    finally:
        try:
            sdk.td_json_client_send(client, b'{"@type":"close"}')
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                raw = sdk.td_json_client_receive(client, 0.5)
                if raw:
                    response = json.loads(raw)
                    if response.get('authorization_state', {}).get('@type') == 'authorizationStateClosed':
                        break
        finally:
            sdk.td_json_client_destroy(client)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", required=True, type=Path)
    parser.add_argument("--platform", required=True)
    parser.add_argument("--version")
    parser.add_argument("--commit")
    args = parser.parse_args()
    system, arch = args.platform.rsplit("-", 1)
    host = platform.machine().lower()
    native_arch = {"amd64": "x64", "x86_64": "x64", "aarch64": "arm64", "arm64": "arm64"}.get(host)
    native_system = {"Windows": "windows", "Linux": "linux", "Darwin": "macos"}.get(platform.system())
    if (system, arch) != (native_system, native_arch):
        raise SystemExit(f"SDK target {args.platform} does not match host {platform.system()}/{host}")
    relative = {"windows": "bin/tdjson.dll", "linux": "lib/libtdjson.so", "macos": "lib/libtdjson.dylib"}[system]
    library = (args.prefix / relative).resolve(strict=True)
    metadata = json.loads((args.prefix / 'BUILD-INFO.json').read_text(encoding='utf-8'))
    if args.commit and metadata.get('commit') != args.commit:
        raise RuntimeError('SDK build provenance does not match the requested source commit')
    dll_directory = os.add_dll_directory(str(library.parent)) if system == "windows" else None
    try:
        sdk = ctypes.CDLL(str(library))
        sdk.td_execute.argtypes = [ctypes.c_char_p]
        sdk.td_execute.restype = ctypes.c_char_p

        def execute(query):
            response = sdk.td_execute(json.dumps(query).encode())
            if not response:
                raise RuntimeError(f"td_execute returned null for {query}")
            return json.loads(response)

        entities = execute({"@type": "getTextEntities", "text": "https://telegram.org"})
        if entities.get("@type") != "textEntities" or not any(item.get("type", {}).get("@type") == "textEntityTypeUrl" for item in entities.get("entities", [])):
            raise RuntimeError(f"Unexpected text entity response: {entities}")
        version = execute({"@type": "getOption", "name": "version"})
        legacy = version.get('@type') == 'error' and version.get('code') == 400 and 'synchronous' in version.get('message', '').lower()
        if legacy:
            version = legacy_version(sdk)
        checks = [("version", args.version, version)]
        if legacy:
            print(f"Legacy TDLib has no runtime commit_hash option; provenance-only commit check: {metadata['commit']}")
        else:
            checks.append(("commit_hash", args.commit, execute({"@type": "getOption", "name": "commit_hash"})))
        for name, expected, response in checks:
            if response.get("@type") != "optionValueString" or not response.get("value") or (expected and response["value"] != expected):
                raise RuntimeError(f"Unexpected {name} response (expected {expected}): {response}")
            print(f"{name}: {response['value']}")
        print(f"SDK load and JSON API smoke check passed: {library}")
    finally:
        if dll_directory:
            dll_directory.close()


if __name__ == "__main__":
    main()
