#!/usr/bin/env python3
"""Build and exercise a relocatable desktop TDLib JSON SDK on a native host."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import zipfile


def run(*args, **kwargs):
    print('+', ' '.join(map(str, args)), flush=True)
    return subprocess.run(list(map(str, args)), check=True, **kwargs)


def output(*args):
    return subprocess.check_output(args, text=True).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--platform', required=True, choices=[
        'windows-x64', 'windows-arm64', 'linux-x64', 'linux-arm64',
        'macos-x64', 'macos-arm64'])
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    os.chdir(root)
    scripts = root / '.github/scripts'
    build = root / 'build-sdk'
    prefix = root / 'install-sdk'
    common = ['-DCMAKE_BUILD_TYPE=Release', '-DBUILD_TESTING=OFF',
              '-DTD_INSTALL_STATIC_LIBRARIES=OFF', '-DTD_INSTALL_SHARED_LIBRARIES=ON',
              '-DTD_ENABLE_JNI=OFF', '-DTD_ENABLE_DOTNET=OFF',
              '-DCMAKE_DISABLE_FIND_PACKAGE_Crc32c=ON', '-DOPENSSL_USE_STATIC_LIBS=ON',
              f'-DCMAKE_INSTALL_PREFIX={prefix}']
    dependencies = []
    consumer = []
    if args.platform.startswith('windows-'):
        triplet = args.platform.removeprefix('windows-') + '-windows-static'
        vcpkg = root / '.deps/vcpkg'
        dependencies.append(vcpkg / 'installed' / triplet)
        common += [f'-DCMAKE_TOOLCHAIN_FILE={vcpkg}/scripts/buildsystems/vcpkg.cmake',
                   f'-DVCPKG_TARGET_TRIPLET={triplet}', '-DVCPKG_MANIFEST_MODE=OFF',
                   # TDLib's vcpkg install rule expects DLLs in the config subdirectory.
                   f'-DCMAKE_RUNTIME_OUTPUT_DIRECTORY={build}/Release',
                   '-DCMAKE_POLICY_DEFAULT_CMP0091=NEW',
                   '-DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreaded']
    elif args.platform.startswith('linux-'):
        multiarch = output('gcc', '-print-multiarch')
        common += ['-DCMAKE_C_COMPILER=clang', '-DCMAKE_CXX_COMPILER=clang++',
                   f'-DZLIB_LIBRARY=/usr/lib/{multiarch}/libz.a',
                   '-DCMAKE_CXX_FLAGS_RELEASE=-O2 -DNDEBUG']
    else:
        openssl = Path(output('brew', '--prefix', 'openssl@3'))
        zlib = Path(output('brew', '--prefix', 'zlib'))
        dependencies += [openssl, zlib]
        consumer = ['-DCMAKE_OSX_DEPLOYMENT_TARGET=15.0']
        common += [f'-DOPENSSL_ROOT_DIR={openssl}', f'-DZLIB_LIBRARY={zlib}/lib/libz.a',
                   f'-DZLIB_INCLUDE_DIR={zlib}/include', '-DCMAKE_INSTALL_NAME_DIR=@rpath',
                   *consumer]

    run('cmake', '-S', root, '-B', build, '-G', 'Ninja', *common)
    run('cmake', '--build', build, '--target', 'tdjson', '--parallel',
        os.environ.get('CMAKE_BUILD_PARALLEL_LEVEL', '2'))
    run('cmake', '--install', build, '--config', 'Release')
    commit = output('git', 'rev-parse', 'HEAD')
    package = [sys.executable, scripts / 'package-tdjson.py', '--prefix', prefix,
               '--output', root / 'dist', '--platform', args.platform, '--commit', commit]
    for dependency in dependencies:
        package += ['--dependency-root', dependency]
    run(*package)

    # Verify the distributable after extracting into a different prefix.
    extracted = root / 'sdk-check'
    extracted.mkdir(exist_ok=True)
    basename = f'tdjson-{args.platform}'
    if args.platform.startswith('windows-'):
        with zipfile.ZipFile(root / 'dist' / f'{basename}.zip') as archive:
            archive.extractall(extracted)
    else:
        with tarfile.open(root / 'dist' / f'{basename}.tar.gz') as archive:
            archive.extractall(extracted, filter='data')
    sdk = extracted / basename
    metadata = json.loads((sdk / 'BUILD-INFO.json').read_text(encoding='utf-8'))
    run(sys.executable, scripts / 'check-tdjson.py', '--prefix', sdk,
        '--platform', args.platform, '--commit', commit, '--version', metadata['version'])
    smoke = root / 'build-smoke'
    run('cmake', '-S', scripts / 'smoke', '-B', smoke, '-G', 'Ninja',
        '-DCMAKE_BUILD_TYPE=Release', f'-DCMAKE_PREFIX_PATH={sdk}', *consumer)
    run('cmake', '--build', smoke, '--config', 'Release')
    env = os.environ.copy()
    if args.platform.startswith('windows-'):
        # Windows resolves an executable's dependencies beside that executable.
        for dll in (sdk / 'bin').glob('*.dll'):
            shutil.copy2(dll, smoke / dll.name)
        executable = smoke / 'tdjson-smoke.exe'
        linked = output('dumpbin', '/dependents', str(sdk / 'bin/tdjson.dll'))
        print(linked, flush=True)
        if any(name in linked.lower() for name in ('libcrypto', 'libssl', 'zlib', 'vcruntime', 'msvcp')):
            raise RuntimeError('SDK unexpectedly depends on an external crypto/zlib/MSVC runtime DLL')
    else:
        executable = smoke / 'tdjson-smoke'
        loader = 'DYLD_LIBRARY_PATH' if sys.platform == 'darwin' else 'LD_LIBRARY_PATH'
        env[loader] = str(sdk / 'lib')
        library = sdk / ('lib/libtdjson.dylib' if sys.platform == 'darwin' else 'lib/libtdjson.so')
        linked = output('otool', '-L', str(library)) if sys.platform == 'darwin' else output('ldd', str(library))
        print(linked, flush=True)
        if any(name in linked for name in ('libcrypto', 'libssl', 'libz.so', 'libz.1.dylib', '/opt/homebrew/', '/usr/local/opt/')):
            raise RuntimeError('SDK unexpectedly depends on external OpenSSL/zlib/Homebrew libraries')
    run(executable, env=env)
    print(f'Built and verified {basename} at commit {commit}', flush=True)


if __name__ == '__main__':
    main()
