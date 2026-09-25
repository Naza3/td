#!/usr/bin/env python3
"""Load the extracted native SDK and exercise its JSON API without networking."""
import argparse
import ctypes
import json
import os
from pathlib import Path
import platform


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
        for name, expected in (("version", args.version), ("commit_hash", args.commit)):
            response = execute({"@type": "getOption", "name": name})
            if response.get("@type") != "optionValueString" or not response.get("value") or (expected and response["value"] != expected):
                raise RuntimeError(f"Unexpected {name} response (expected {expected}): {response}")
            print(f"{name}: {response['value']}")
        print(f"SDK load and JSON API smoke check passed: {library}")
    finally:
        if dll_directory:
            dll_directory.close()


if __name__ == "__main__":
    main()
