#!/usr/bin/env python3
"""Extract a relocatable shared JSON SDK from legacy TDLib's combined install."""
import argparse
from pathlib import Path
import shutil


def stage(prefix, output, platform):
    if output.exists():
        shutil.rmtree(output)
    headers = output / 'include/td/telegram'
    headers.mkdir(parents=True)
    for name in ('td_json_client.h', 'td_log.h', 'tdjson_export.h'):
        shutil.copy2(prefix / 'include/td/telegram' / name, headers / name)
    library_dir = output / 'lib'
    library_dir.mkdir()
    system = platform.split('-')[0]
    if system == 'windows':
        (output / 'bin').mkdir()
        shutil.copy2(prefix / 'bin/tdjson.dll', output / 'bin/tdjson.dll')
        shutil.copy2(prefix / 'lib/tdjson.lib', library_dir / 'tdjson.lib')
        library = 'bin/tdjson.dll'
    else:
        pattern = 'libtdjson*.dylib' if system == 'macos' else 'libtdjson.so*'
        files = list((prefix / 'lib').glob(pattern))
        if not files:
            raise RuntimeError(f'No shared tdjson libraries found in {prefix}')
        for path in files:
            shutil.copy2(path, library_dir / path.name, follow_symlinks=False)
        library = 'lib/libtdjson.dylib' if system == 'macos' else 'lib/libtdjson.so'
    config_dir = library_dir / 'cmake/Td'
    config_dir.mkdir(parents=True)
    # Legacy TdTargets references every installed static archive. Export only the
    # shared interface here so clients do not inherit build-machine dependencies.
    config = '''get_filename_component(_TDJSON_PREFIX "${CMAKE_CURRENT_LIST_DIR}/../../.." ABSOLUTE)
if(NOT TARGET Td::tdjson)
  add_library(Td::tdjson SHARED IMPORTED)
  set_target_properties(Td::tdjson PROPERTIES
    IMPORTED_LOCATION "${_TDJSON_PREFIX}/@LIBRARY@"
    INTERFACE_INCLUDE_DIRECTORIES "${_TDJSON_PREFIX}/include")
@IMPLIB@
endif()
if(NOT TARGET Td::TdJson)
  add_library(Td::TdJson INTERFACE IMPORTED)
  set_target_properties(Td::TdJson PROPERTIES INTERFACE_LINK_LIBRARIES Td::tdjson)
endif()
unset(_TDJSON_PREFIX)
'''
    implib = '  set_target_properties(Td::tdjson PROPERTIES IMPORTED_IMPLIB "${_TDJSON_PREFIX}/lib/tdjson.lib")' if system == 'windows' else ''
    (config_dir / 'TdConfig.cmake').write_text(
        config.replace('@LIBRARY@', library).replace('@IMPLIB@', implib), encoding='utf-8')
    shutil.copy2(prefix / 'lib/cmake/Td/TdConfigVersion.cmake', config_dir / 'TdConfigVersion.cmake')
    print(f'Staged legacy shared JSON SDK: {output}', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prefix', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--platform', required=True,
                        choices=[f'{system}-{arch}' for system in ('windows', 'linux', 'macos') for arch in ('x64', 'arm64')])
    args = parser.parse_args()
    if args.prefix.resolve() == args.output.resolve():
        parser.error('The staging output must differ from the installed prefix')
    stage(args.prefix.resolve(), args.output.resolve(), args.platform)


if __name__ == '__main__':
    main()
