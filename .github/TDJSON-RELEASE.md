# TDLib 桌面客户端预编译库

工作流：`.github/workflows/build-tdjson.yml`（Build TDLib native SDKs）。
构建当前仓库的对应提交；发布 Release 时构建该 Release 的 tag，不下载上游 master。

| 资产 | 系统与架构 | 动态库 |
| --- | --- | --- |
| `tdjson-windows-x64.zip` | Windows x64（MSVC） | `bin/tdjson.dll` |
| `tdjson-windows-arm64.zip` | Windows ARM64（MSVC） | `bin/tdjson.dll` |
| `tdjson-linux-x64.tar.gz` | Linux x64（Ubuntu 24.04 构建） | `lib/libtdjson.so` |
| `tdjson-linux-arm64.tar.gz` | Linux ARM64（Ubuntu 24.04 构建） | `lib/libtdjson.so` |
| `tdjson-macos-x64.tar.gz` | macOS 15+，Intel | `lib/libtdjson.dylib` |
| `tdjson-macos-arm64.tar.gz` | macOS 15+，Apple Silicon | `lib/libtdjson.dylib` |

每份资产附带 `.sha256` 校验文件。压缩包还包含 JSON C API 头文件、Windows 导入库
`lib/tdjson.lib`、CMake 配置、`td_api.tl`、许可证及记录版本/提交/架构的
`BUILD-INFO.json`。这是动态 JSON SDK，适用于 C/C++、Rust FFI、Dart FFI、Python 等；
不包含完整的 C++ 静态 SDK、JNI、Android 或 iOS 构建。

OpenSSL 和 zlib 静态链接。Windows 同时静态链接 MSVC CRT，不需要额外分发这三者的 DLL。
Linux 仍依赖系统 glibc、libstdc++ 等运行库，基线为 Ubuntu 24.04，不适用于 Alpine/musl
或旧版 glibc 发行版。macOS 保留系统运行库依赖。更换依赖版本后需要重新构建发布资产。

## 触发构建及发布

1. 将工作流合并到 `master`。
2. 仅测试：Actions → **Build TDLib native SDKs** → **Run workflow**，选择分支或 tag。
   六个平台通过后，可在该次运行的 Artifacts 下载；保留 14 天。
3. 正式发布：为含该工作流的提交创建 tag，在 GitHub Releases 点击 **Publish release**。
   `release.published` 会自动构建、校验，再向已有 Release 上传六份 SDK 及六份校验文件。
   正式版和预发布版均支持；仅保存草稿或只推 tag 不触发发布。
4. 如构建失败，修复后重新运行；上传作业只在六个平台都成功时执行。
   重跑相同 Release 的上传会覆盖同名资产，不会创建第二个 Release。

修改构建工作流/脚本的 PR 也会自动执行六个平台的验证，不上传 Release。
构建只需要默认 `GITHUB_TOKEN`；只有上传作业需要 `contents: write`。

## 客户端接入

解压后将 `tdjson-<平台>` 的绝对路径传给 CMake：

```sh
cmake -S . -B build -DCMAKE_PREFIX_PATH=/absolute/path/tdjson-linux-x64
cmake --build build --config Release
```

```cmake
find_package(Td CONFIG REQUIRED)
target_link_libraries(your_client PRIVATE Td::TdJson)
```

程序包含 `td/telegram/td_json_client.h` 即可调用 `td_execute`、`td_send` 等。
Windows 分发时将 `bin/tdjson.dll` 放到客户端可执行文件旁。
Linux 分发时保留 `libtdjson.so*` 及符号链接，配置 `$ORIGIN` RPATH 或加载目录。
macOS 分发时保留 `libtdjson*.dylib`，设置应用的 `@rpath`，并在最终应用打包时签名。
使用 FFI 时可按平台加载动态库的绝对路径。

## 自动验证内容

- 从安装目录生成 SDK，再解压到另一目录，检查库文件和目标架构。
- 使用原生 Python `ctypes` 加载解压的库，调用离线 JSON API，检查版本及提交哈希。
- 使用独立 CMake 工程通过 `find_package(Td)` 编译、链接并运行 C 客户端。
- 检查 Unix 动态依赖，拒绝意外依赖 OpenSSL/zlib 动态库或 Homebrew 绝对路径。
- 发布前检查六份归档齐全且 SHA-256 校验通过。

这些检查不需要 Telegram 账号或 API key；不覆盖账号登录、网络功能或旧系统兼容性。
