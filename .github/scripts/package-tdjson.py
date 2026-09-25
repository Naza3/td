#!/usr/bin/env python3
"""Package an installed TDLib shared SDK, preserving Unix library symlinks."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import tarfile
import tempfile
import zipfile

PLATFORMS = [f"{system}-{arch}" for system in ("windows", "linux", "macos") for arch in ("x64", "arm64")]
ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path, help="Output directory")
    parser.add_argument("--platform", required=True, choices=PLATFORMS)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--version")
    parser.add_argument("--dependency-root", action="append", default=[], type=Path)
    args = parser.parse_args()
    version = args.version or re.search(r"project\(TDLib VERSION ([\d.]+)", (ROOT / "CMakeLists.txt").read_text())[1]
    sdk = args.prefix.resolve()
    system = args.platform.split("-")[0]
    library = {"windows": "bin/tdjson.dll", "linux": "lib/libtdjson.so", "macos": "lib/libtdjson.dylib"}[system]
    for relative in (library, "include/td/telegram/td_json_client.h", "include/td/telegram/tdjson_export.h", "lib/cmake/Td/TdConfig.cmake"):
        if not (sdk / relative).is_file():
            raise SystemExit(f"Missing installed SDK file: {sdk / relative}")
    if system == "windows" and not (sdk / "lib/tdjson.lib").is_file():
        raise SystemExit("Missing Windows import library: lib/tdjson.lib")
    args.output.mkdir(parents=True, exist_ok=True)
    name = f"tdjson-{args.platform}"
    archive = args.output.resolve() / (name + (".zip" if system == "windows" else ".tar.gz"))
    with tempfile.TemporaryDirectory(prefix="tdjson-package-") as temporary:
        stage = Path(temporary) / name
        shutil.copytree(sdk, stage, symlinks=True)
        # Upstream embeds the build machine's install prefix in pkg-config files.
        for metadata in (stage / "lib/pkgconfig").glob("*.pc"):
            contents = metadata.read_text(encoding="utf-8")
            metadata.write_text(re.sub(r"(?m)^prefix=.*$", "prefix=${pcfiledir}/../..", contents), encoding="utf-8")
        readme = ROOT / ".github/TDJSON-RELEASE.md"
        if readme.is_file():
            shutil.copy2(readme, stage / "README.md")
        licenses = stage / "licenses"
        licenses.mkdir(exist_ok=True)
        shutil.copy2(ROOT / "LICENSE_1_0.txt", licenses / "TDLib-LICENSE_1_0.txt")
        for index, dependency in enumerate(args.dependency_root):
            if not dependency.is_dir():
                raise SystemExit(f"Dependency directory does not exist: {dependency}")
            candidates = list(dependency.glob("share/*/copyright"))
            candidates += [dependency / filename for filename in ("LICENSE", "LICENSE.txt", "LICENSE.md", "LICENSE_1_0.txt", "COPYING")]
            for source in candidates:
                if source.is_file():
                    shutil.copy2(source, licenses / f"dependency-{index}-{source.parent.name}-{source.name}")
        if system == "linux" and not args.dependency_root:
            for package in ("libssl-dev", "zlib1g-dev"):
                source = Path("/usr/share/doc") / package / "copyright"
                if source.is_file():
                    shutil.copy2(source, licenses / f"{package}-copyright")
        info = {"version": version, "commit": args.commit, "platform": args.platform, "interface": "TDLib JSON C API", "library": library}
        (stage / "BUILD-INFO.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
        shutil.copy2(ROOT / "td/generate/scheme/td_api.tl", stage / "td_api.tl")
        if system == "windows":
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
                for source in sorted(stage.rglob("*")):
                    if source.is_file():
                        output.write(source, source.relative_to(stage.parent))
        else:
            with tarfile.open(archive, "w:gz", dereference=False) as output:
                output.add(stage, arcname=name)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_name(archive.name + ".sha256").write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    print(archive)


if __name__ == "__main__":
    main()
