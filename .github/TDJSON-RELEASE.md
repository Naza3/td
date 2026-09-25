# TDLib 桌面客户端预编译库

工作流：`.github/workflows/build-tdjson.yml`（Build TDLib native SDKs）。
推送 `v*` 版本标签后，构建该标签的准确提交；六个平台验证成功后自动创建并发布 Release。
源码来自本仓库，不下载上游 master。构建脚本与源码分开检出，因此也能补发不含新工作流的旧标签。

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

1. 为包含工作流的提交创建版本标签并推送。标签使用 `v主版本.次版本.补丁版本`，
   可带 `-rc.1` 等预发布后缀。版本号必须与该提交的 `CMakeLists.txt` 中 TDLib 版本一致。

   ```sh
   git tag v1.8.67
   git push origin v1.8.67
   ```

   上述版本号只是示例，请先确认源码版本及标签是否已存在。无需手动创建 Release。
2. 工作流会自动构建、检查六个平台，创建草稿 Release，上传并核对全部 12 个文件，
   最后才公开发布。普通 `vX.Y.Z` 是正式版，带预发布后缀的标签发布为预发布版。
   Latest 按 GitHub 的日期及语义版本规则确定，补发旧版本不强制替换较新版本。
3. **补发已有标签**（包括工作流引入之前的旧标签）：Actions → **Build TDLib native SDKs** →
   **Run workflow**，选择包含新工作流的 `master`，在 `release_tag` 填写现有标签（例如 `v1.8.0`）。
   这会构建标签的源码并发布该版本，不移动或重建旧标签。
4. **仅测试**：同一页面将 `release_tag` 留空。会构建所选分支/标签并保留 Actions 产物 14 天，
   不创建 Release。普通推送分支不自动发布，手工创建 Release 也不会重复触发构建。
5. 构建失败时不会创建公开 Release；上传中断时保留草稿，可重跑恢复。
   已公开版本不会被覆盖。完整重跑若生成不同内容，应发布新的版本标签，而不是替换公开资产。

修改构建工作流/脚本的 PR 也会自动执行六个平台的验证，不上传 Release。
构建只需要默认 `GITHUB_TOKEN`；只有发布作业需要 `contents: write`，无需 PAT。
标签推送与手动补发共用同一个构建/发布流程，不依赖 Release 事件串联第二次构建。

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
- 使用原生 Python `ctypes` 加载解压的库，调用离线 JSON API 并检查版本。
  新版本另检查运行期提交哈希；`v1.8.0` 没有该接口，改为核对精确检出的提交及包内构建信息。
- 使用独立 CMake 工程通过 `find_package(Td)` 编译、链接并运行 C 客户端。
- 检查 Unix 动态依赖，拒绝意外依赖 OpenSSL/zlib 动态库或 Homebrew 绝对路径。
- 发布前检查六份归档齐全且 SHA-256 校验通过。
- 发布前再次确认标签未移动，包内版本/提交与标签一致，上传后的资产摘要与本地一致。

这些检查不需要 Telegram 账号或 API key；不覆盖账号登录、网络功能或旧系统兼容性。
