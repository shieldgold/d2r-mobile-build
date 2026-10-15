# 固定上游离线 shader 工具链

来源：TommyLau/botw-mobile，`botw-host-boot1.9`，固定提交 `5f673ae2ea4f90ad46403aa6fbe13537fb557271`。`UPSTREAM.json` 逐文件记录未修改源码的 SHA256。只保留离线构建脚本、原合成测试、dmsh_xlate 与 Ryujinx 的 Shader/Common/Spv.Generator 三个项目及构建配置/许可；没有事实表、游戏模块偏移、游戏输入或编译产物。

这是与现有 Rust 工具并存的完整离线链：捕获 control/code → 显式逐 shader facts → Ryujinx SPIR-V → SPIRV-Cross MSL → Apple metal/metallib → 哈希绑定 reflection/manifest。它不切换生产图形后端，也不保证存在所有 shader 的环境事实。

```sh
python3 -m scripts.botw_native.upstream_shaders verify-source
python3 -m scripts.botw_native.upstream_shaders export INPUTS_DIR /absolute/outside/git/captures
python3 -m scripts.botw_native.upstream_shaders tool -- validate /absolute/outside/git/captures
python3 -m scripts.botw_native.upstream_shaders tool -- build /absolute/outside/git/captures --out /absolute/outside/git/result --probe --xlate /absolute/dmsh_xlate.dll --spirv-cross /absolute/spirv-cross
```

`INPUTS_DIR` 是现有 BNSH 提取器的 `inputs.json` 和 SHA256 命名的 `.control`/`.nvn` 文件。导出前核验完整哈希、stage、代码头/常量区间、总预算；输出目录必须新建且位于 Git 工作树之外（即使 Git 忽略的 outputs 也不例外，保留上游约束）。相同控制段/代码重复输入仅生成一份捕获，`capture-sources.json` 保留全部来源。v8 控制段保持明确拒绝，不改版本号冒充 v9。

构建工具依赖 .NET SDK 10、SPIRV-Cross CLI、Apple metal/metallib；用固定官方工具并记录版本和摘要。用 `.NET --artifacts-path` 将 obj/bin 放在 ignored `.tools`，不要把产物写入本目录。示例：

```sh
DOTNET_CLI_TELEMETRY_OPTOUT=1 DOTNET_NOLOGO=1 DOTNET_CLI_HOME=/absolute/ignored/dotnet-home /absolute/dotnet publish native/botw_resources/shader_toolchain/dm/gfx/tools/dmsh_xlate/dmsh_xlate.csproj -c Release --artifacts-path /absolute/ignored/build-artifacts -o /absolute/ignored/xlate
```

上游 Program.cs 仍含 D2R c2 的 0x168/0x568 映射和带诊断的默认查询回答。严格 build 会拒绝任何 translator fallback 或未证明绑定；不能直接复用 D2R shader_facts、c1_full、shader_map、内建替代 MSL，也不能把 1.9 module offsets 用于当前本体。`--probe` 的产物不可运行；真正 build 需要绑定当前 capture SHA256 的全部环境 facts。运行时 manifest/Metal 回放模块仍由图形集成单独验证。

许可证：根 LICENSE 为上游 MIT；Ryujinx 和 Spv.Generator 的原始许可证随源码保留。

本机已验证的固定官方工具见 `EXTERNAL_TOOLS.json`。隔离安装 .NET 时，运行 `.dll` 前将其目录加入 PATH，并设置 DOTNET_ROOT；编译产物全部放 ignored `.tools`。本批实际将 main 的 68 条来源导出为 37 份精确捕获，37 份均完成 SPIR-V/MSL 与 Metal.framework 编译检查。缺少绑定/环境证据仍保留 251 项 unknown，产物为不可运行 probe，未绕过发布门禁。

面向 iPhone 12/iOS 18：本链固定输出 MSL 2.3 源。上游 `metal_compile` 使用 macOS SDK，产出的 macOS metallib 不能用于手机；本批采用 `--probe --metal runtime`，没有生成 iOS26 metallib。手机包须由现有 iOS18 构建/运行时加载器针对已证明事实的 MSL 单独编译并验证。

`dm/` 是逐文件摘要锁定的上游原文；局部 `.gitattributes` 保留其原始行尾/空白，避免 Git 自动改写。适配器、测试与本目录维护文件仍使用正常空白检查。
