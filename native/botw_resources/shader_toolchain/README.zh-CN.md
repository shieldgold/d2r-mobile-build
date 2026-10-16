# 固定上游离线 shader 工具链

来源：TommyLau/botw-mobile，固定提交 `21f026e0c7fd9fee575a1bfa8d925553bb4db88e`。`UPSTREAM.json` 逐文件记录 374 个未修改源文件的 SHA256 和原 Git blob SHA1。完整保留 build/derive/extract、Yaz0 C helper、NSO 读取依赖、原合成测试、dmsh_xlate 与 Ryujinx 的 Shader/Common/Spv.Generator 三个项目及构建配置/许可；没有游戏事实表、游戏输入或编译产物。

这是与现有 Rust 工具并存的完整离线链：捕获 control/code → 显式逐 shader facts → Ryujinx SPIR-V → SPIRV-Cross MSL → Apple metal/metallib → 哈希绑定 reflection/manifest。它不切换生产图形后端，也不保证存在所有 shader 的环境事实。

```sh
python3 -m scripts.botw_native.upstream_shaders verify-source
python3 -m scripts.botw_native.upstream_shaders export INPUTS_DIR /absolute/outside/git/captures
python3 -m scripts.botw_native.upstream_shaders tool -- validate /absolute/outside/git/captures
python3 -m scripts.botw_native.upstream_shaders tool -- build /absolute/outside/git/captures --out /absolute/outside/git/result --probe --xlate /absolute/dmsh_xlate.dll --spirv-cross /absolute/spirv-cross
python3 -m scripts.botw_native.upstream_shaders raw-extract -- --help
python3 -m scripts.botw_native.upstream_shaders raw-derive -- --help
```

`INPUTS_DIR` 是现有 BNSH 提取器的 `inputs.json` 和 SHA256 命名的 `.control`/`.nvn` 文件。导出前核验完整哈希、stage、代码头/常量区间、总预算；输出目录必须新建且位于 Git 工作树之外（即使 Git 忽略的 outputs 也不例外，保留上游约束）。相同控制段/代码重复输入仅生成一份捕获，`capture-sources.json` 保留全部来源。v8 控制段保持明确拒绝，不改版本号冒充 v9。

构建工具依赖 .NET SDK 10、SPIRV-Cross CLI、Apple metal/metallib；用固定官方工具并记录版本和摘要。用 `.NET --artifacts-path` 将 obj/bin 放在 ignored `.tools`，不要把产物写入本目录。示例：

```sh
DOTNET_CLI_TELEMETRY_OPTOUT=1 DOTNET_NOLOGO=1 DOTNET_CLI_HOME=/absolute/ignored/dotnet-home /absolute/dotnet publish native/botw_resources/shader_toolchain/dm/gfx/tools/dmsh_xlate/dmsh_xlate.csproj -c Release --artifacts-path /absolute/ignored/build-artifacts -o /absolute/ignored/xlate
```

上游新 make_job 已完整传入纹理/图像事实，Program.cs 使用已观测 1.9 的 c2 table offset 0x20；仍有带诊断的默认查询回答。derive v5 的 2D、float 和设备状态推导不等于 base-v0 事实，其 exit 0 也不表示可发布。不能直接复用 D2R shader_facts、c1_full、shader_map、内建替代 MSL，不能把 1.9 模块偏移或设备状态当作当前本体证据。纹理查询仍沿上游 handle 单键，未声称支持任意 (handle, cbufSlot) 事实。

`tool`、`raw-derive`、`raw-extract` 保留完整上游研发入口；生产入口是 `strict-check`、`strict-prepare`、`strict-build`。三者要求整个批次有明确的 `botw-switch-base-v0` 审查证据：原 inputs.json 摘要、每份 capture/code/control/C1/反射摘要、所有来源变体，以及每个 facts 字段的批准范围。缺失、非空或类型错误的 skipped/unresolved/assumed_texture_types/integer_output_candidates/established_differences 均拒绝；规则推导的 provenance 不会自动升级为权威。事实必须与独立 established 文件逐字节相等。证据本身仍需人工/上游合同核实，门禁仅强制其来源与范围可审计。

```sh
python3 -m scripts.botw_native.upstream_shaders strict-check INPUTS CAPTURES FACTS DERIVE_REPORT EVIDENCE
python3 -m scripts.botw_native.upstream_shaders strict-prepare INPUTS CAPTURES FACTS DERIVE_REPORT EVIDENCE /absolute/outside/git/admitted
python3 -m scripts.botw_native.upstream_shaders strict-build INPUTS CAPTURES FACTS DERIVE_REPORT EVIDENCE /absolute/outside/git/build-inputs --xlate /absolute/dmsh_xlate.dll --spirv-cross /absolute/spirv-cross
```

`strict-prepare` 在全部准入后才创建私有输出，并复制已核验的独立字节快照。`strict-build` 从该快照调用原完整 build，不提供 probe/merge/subset 绕过；上游继续验证翻译器 source fingerprint、未知查询、绑定和产物摘要。批准输入不代表着色器已编译，也不代表运行验证。v8/GS 保持明确拒绝；运行时 manifest/Metal 回放模块仍由图形集成单独验证。

许可证：根 LICENSE 为上游 MIT；Ryujinx 和 Spv.Generator 的原始许可证随源码保留。

本机已验证的固定官方工具见 `EXTERNAL_TOOLS.json`。隔离安装 .NET 时，运行 `.dll` 前将其目录加入 PATH，并设置 DOTNET_ROOT；编译产物全部放 ignored `.tools`。此前旧 5f 工具的 37 份 probe 与工具指纹属于历史证据，不能冒充本次 21f 完整工具链或已发布的场景 shader。升级后须重新构建 dmsh_xlate，不能沿用旧 DLL 的 source fingerprint。

面向 iPhone 12/iOS 18：本链固定输出 MSL 2.3 源。上游 `metal_compile` 使用 macOS SDK，产出的 macOS metallib 不能用于手机；本批采用 `--probe --metal runtime`，没有生成 iOS26 metallib。手机包须由现有 iOS18 构建/运行时加载器针对已证明事实的 MSL 单独编译并验证。

`dm/` 是逐文件摘要锁定的上游原文；局部 `.gitattributes` 保留其原始行尾/空白，避免 Git 自动改写。适配器、测试与本目录维护文件仍使用正常空白检查。
