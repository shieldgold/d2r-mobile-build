# nxnative Base-v0 Input Admission Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为现有 BOTW 1.0.0 建立固定 nxnative 来源和真实输入校验，随后逐步完成重写、完整宿主编译、着色器和真机接入；本批可独立执行的产物是来源与输入准入工具。

**Architecture:** 原样保存 nxnative `65fa9caab486768f70da98c51c2421aff76f676d`，在独立目录保存 base-v0 配置和薄适配。以 Git tree 身份绑定全部来源，以原 NSO 身份绑定六业务模块；`rtld` 只核身份。来源／版本校验不运行游戏，也不宣称着色器、宿主或可玩性已经通过。

**Tech Stack:** Python 3、unittest、Git 对象哈希；后续宿主使用 arm64 iPhoneOS 26 / Metal 4。

---

## 当前约束

- 用户明确选择已存在的 iOS 26+ 设备和现有 1.0.0 资源。当前有线目标为 iPhone 15 Pro SNAMKEY / iOS 27.2，开发者模式开启，现有签名覆盖该 UDID。
- 原有运行版为 1017.36。控件可见、游戏区域黑，尚未达到可玩验收。未提交的 37 显示诊断候选保存在 `outputs/botw-whole-phase37-deferred-for-nxnative/`。
- 不把 1.9.0 的三个模块、固定偏移、NVN 55.15 或 shader 表冒充为 1.0.0 的六模块与 NVN 52.32。
- 不读取 Guest 运行时 PC、寄存器、栈、堆、对象或 VM 诊断扫描；不启用原始日志和截图／readback 诊断。

## Task 1: 固定完整来源

**Files:**
- Create: `experiments/nxnative_base_v0/upstream/`（953 个原文件，保留许可和原 Git mode）
- Create: `experiments/nxnative_base_v0/SOURCE.lock.json`

- [x] 下载固定提交的官方归档，拒绝越界路径、链接和过大归档。
- [x] 对每个文件验证官方 Git blob；根 tree 必须为 `5b1545c089970a2aca0325aa31fa71bce217813f`。
- [x] 将适配器文件及其测试完成后的摘要加入锁；不改变上游文件。

## Task 2: 来源准入工具

**Files:**
- Create: `experiments/nxnative_base_v0/tools/verify_source.py`
- Test: `tests/test_nxnative_source.py`

- [x] 先用 unittest 编写独立小 Git repository 的 tree 对照测试；验证工具不存在时明确断言失败。
- [x] 实现相对路径、普通文件、文件 mode、完整文件集合、SHA256 与 Git blob 校验，并重算整个 Git tree。
- [x] 使用原 tree 拒绝“同时篡改文件与锁摘要”的输入；拒绝缺失、额外文件、symlink、越界路径及重复 JSON key。
- [x] 执行真实来源校验：

```sh
python3 experiments/nxnative_base_v0/tools/verify_source.py
python3 -m unittest discover -s tests -p test_nxnative_source.py -v
```

## Task 3: 1.0.0 输入准入

**Files:**
- Create: `experiments/nxnative_base_v0/profile/base_v0.json`
- Create: `experiments/nxnative_base_v0/tools/preflight_profile.py`
- Test: `tests/test_nxnative_base_v0_profile.py`

- [x] 先以自有 NSO header 和 RomFS 元数据构造错误身份、截断、缺失／额外模块测试，取得实际 RED。
- [x] 固定六个加载模块的原 NSO build ID、长度和摘要，另核 `rtld`，复用已有 base-v0 ABI 与资源身份。
- [x] 读取现有 `outputs/botw-native-base/ExeFS` 和 `outputs/botw-romfs-base/romfs.bin`，不重新解密，不无必要重扫 14 GB 镜像。
- [x] 输出版本、模块身份和资源校验结果；明确 rewrite / host compile / shader / gameplay 尚待通过。
- [x] 执行：

```sh
python3 -m unittest discover -s tests -p test_nxnative_base_v0_profile.py -v
```

## Task 4: 完整上游编译基线

**Files:**
- Create: `experiments/nxnative_base_v0/compile_sources.json`
- Create: `experiments/nxnative_base_v0/adapter/link_only_main.c`
- Create: `experiments/nxnative_base_v0/tools/build_link_only.py`
- Test: `tests/test_nxnative_host_build.py`
- Create: `.github/workflows/nxnative-base-v0.yml`

- [x] 以已有静态清点逐项登记 126 个上游 iPhoneOS TU，加独立 link-only 入口，共 127 个；不调用会实际 make 的上游清点脚本。
- [x] 先写 owned 输入／路径／配置测试并取得实际 RED，再实现构建工具。
- [x] 仅复用已完整审查的 `dm/games/game.py` 纯 header 入口；生成原 1.9.0 基线元数据，收据明确 `base_v0_configured=false` 和 `installable=false`。
- [x] 禁止嵌入游戏字节；固定 `arm64-apple-ios26.0` 和 A17 Pro 兼容指令集，要求至少 SDK 26，逐 TU 记录实际依赖和编译结果。
- [x] 官方 CI 执行下面命令，产物去执行位且名称为 `LINK-ONLY-DO-NOT-RUN`：

```sh
python3 experiments/nxnative_base_v0/tools/build_link_only.py \
  --sdk iphoneos --baseline-only --output outputs/nxnative-host-iphoneos
```

- [x] 仅根据实际 compile / link 结果判断编译成功；计划命令或单测不代替编译证据。

## Task 5: 独立复核与留档

- [x] 作者测试通过后，由另一 agent 复核实际代码和独立负例，根任务核对结果和文件摘要。
- [x] `git diff --check` 和适用的来源／输入测试通过后提交、推送本批。
- [x] 继续下一批前，使用源码审计矩阵确定新 rewriter 的 base-v0 分支／跳转表差异，以及 iPhoneOS 完整 host 的正常入口和诊断封闭范围。后续代码在其实际合同和失败测试确定后实施。
- [ ] 后续真机验收仍要求真实游戏画面、按键和双摇杆、声音及保存重开；来源、安装或进程存活均不替代验收。

## 已执行的后续步骤

- 固定提交 `6de350ccdcc813e01e8e7645a85dec9a198b1768` 已推送；CI `39012668489` 在 SDK 26.5 中完成 127 个编译单元和强链接。51 个测试中仅跳过一项需要私有 NSO 的用例，本地该项通过。
- 六模块实际离线重写完成，报告 `outputs/nxnative-base-v0-rewrite-65fa9ca-01/report.json`；未执行产物，原始 H/R 字面值保留。
- 固定提交 `4e768d3d8c6cf483e2acafb28596b3c0f02e6ec5` 的 130 单元适配构建已在 CI `39014320818` 完成编译、强链接；本地确认来源与下载工件一致。该构建仍为原 1.9 元数据的 link-only 产物，不可安装。
- 六模块真实框架已经完整逐节还原核验；原 H/R 保持，未签名或执行。
- 新着色器源码打包器完整复用原条件发布验证器。实际 3173 捕获／12692 变体生成 38085 件允许文件，每份工件摘要及整体文件集合已复核，尚未进行 Metal 编译。
- 新宿主所需的完整资源目录已导出，39678 份 payload 与原固定 manifest 一致。
- 正在接入实际绘制点的条件着色器 provider、专属 1.0 metadata／force 合同、内存和账户／显示层接口。它们均不替代完整运行与可玩验收。
- 固定提交 `e01001fbe124f3920a14c365f68d0fbe33c99e04` 的来源、测试与两套完整 iPhoneOS 构建已在 CI `39015312170` 通过。
- 静态装载合同已实际核对 9 个启动入口与 283 处宿主引用；614 项 registry 仅绑定原 130 单元宿主，后续新接口和音频改动需要重新核验。
- 正常门总数为 230（main 93、sdk 137），原 H/R 字面值保持。完整逻辑内存、账户／显示层和同步返回适配已有 owned 回归测试，待正常宿主集成。
- 着色器独立复核确认的窗口原点竞争已修复，最终 guard 前移至首次 viewport／winding 编码前并保持到 draw。实际 setter、编码与并发回归通过；完整 iPhoneOS 构建待执行。
- 音频静态审计确认 1.0 SDK 缺少新版 CPU renderer 导出，参数转换会覆盖 REV1；改为复用已有完整 Eden 服务端，接入当前唯一消息／事件／映射实现。
- 正常安装资源已在本地新目录准备：完整 RomFS、条件着色器源包和上游生成的字体替身。该目录没有宿主入口，不可安装。
- 固定提交 `6c01b345c18b49da1255d762404359266e7f1afd` 已推送，本地 219 个测试无跳过、无失败。CI `39016541153` 完成原基线与新增条件着色器模块的 136 单元编译、强链接；下载产物与 1027 件来源、576 份日志一致。
- 专属 1.0 元数据与真实 UIKit 入口的 139 单元构建工具已实现，12 项 owned 回归通过；下一步执行真实 iPhoneOS 编译。未接入音频及最终装载合同的产物仍不可运行。
- 着色器缺口已分组：已有译码器能生成 7907／7908 个候选，当前合同仅接入无顶点属性 VS 与 output0 FS。带属性 VS、其他片元输出及查询能力仍须接通实际绑定后扩展，不能用空白输入或虚构格式替代。

- 固定提交 `669d761af4e8d6f1cdbab86410fdc950a87900f5` 的真实 UIKit／专属 1.0 元数据已在 CI `39016958941` 完成 139 单元强链接，本地确认全部来源及实际 616 项登记表。该产物未运行、不可安装。
- 正常装载保护与完整静态合同生产器已接入 140 单元构建；原框架的磁盘验证复用来源也已固定。15 项保护测试和 12 项构建合同测试通过，待根任务完整回归与真实 SDK 构建。最终音频登记表仍须重新读取后生成合同。

- 根任务重新运行本批 20 个测试模块：246 项、0 跳过、0 失败；来源闭合为 953 个原文件、37 个适配文件和 43 个外部来源。140 单元来源准备与真实 139 宿主的静态合同生成通过；后者实际选用 238 项接口、285 处按名引用，仍不是最终音频合同。

- 带属性 VS 已用完整 CPU 管线重新翻译并独立审核 2308 个捕获／9232 个四模式变体；15 项 owned 回归通过，完整原生格式与 descriptor 继续复用。根任务准备独立资源目录，27704 件新文件、144081373 字节均与原补充包逐文件 SHA256 相同；旧包和原输入未改。私有宿主接线和窄独立复核继续进行。

- `1fa3a56e4acaea8d946739c388b8f43e68f405d9` 的 CI `39018043850` 已完成 140 单元官方 SDK 编译与强链接，含正常装载保护。仍未安装或运行。
- 音频 15 件来源冻结；根任务重跑 kernel／output／完整 REV1+PCM 18 项回归均 0 跳过、0 失败。独立生命周期复核另覆盖实际 alias base/span 与失败重试 ACK；仅 owned CPU 输入，不是手机声音证据。308 份原始依赖规范化去重后为 297 份，全部摘要与冻结清单一致；SOURCE 只登记规范路径，不改原清单。235 单元和 typed VS 正在一并接入。

- VS 独立复核抓到原生 descriptor 在最终 range guard 前读取 stream16 的实际越界；新增纯 shape/type 检查前移至首次 pso_get 前，原最终注册范围及 viewport 事务保留。修复后的 17 项根任务回归均通过，独立完整 descriptor loop 的 stream16 从 UBSan 越界变为前置固定拒绝。
- 原上游 RED_ONCE 在禁用 MLOG 后仍递增日志计数器，实际 index84 超过 84 项数组；正常配置现整宏不求值，4 项独立回归通过，完整 MTL 编译仅消除此一警告，非正常配置原 once 行为保留。最终组合顺序为原9＋audio2→typedVSv2→counter。

- 235 单元组合工具已冻结。根任务完成 26 模块、287 项测试，0 跳过、0 失败；来源闭合为 953 个原文件、54 个适配件、342 个外部来源。实际 stage 已完成完整音频、typedVSv2 与 counter，仍未编译／运行新 iPhone 宿主。

- `3710e8c763cee336d6d59b5011776ba5cc230409` 的 CI `39019104689` 中来源、全部 owned 测试和 127／136 基线均通过；235 步骤在 libc++ 路径准入停止（0 planned、0 compiled），错误为 `selected compiler complete libc++ headers are unavailable`。音频／策略闭合标志保持 false；正在改为查询已绑定编译器的实际搜索根，保留完整标准库依赖前后核验。
- MRT 新 2112 捕获／8448 变体已用完整 CPU 管线和 validator 复核，根任务 11 项回归与独立 78 边界通过；实际三包共 7593 捕获。315 个剩余身份没有 variant，不冒充全目录覆盖。资源副本 03 新 25352 文件、144316538 字节均逐 SHA 与原 FS 包一致。
- DebugPad 两个固定 1.0 main 进口已由完整既有 disconnected producer 薄接，原 Npad 主链未改；根任务 7 项回归通过，getter 不推进采样，采样沿原 vsync 线程。纯静态 preload 校验工具的 7 项回归也已通过，真实 ad-hoc 资源副本待最终来源闭合后校验。

- 235 单元 runtime v2 已冻结，补齐 MRT、DebugPad 与同一 clang 实际 libc++ 搜索路径查询。18 项构建回归通过，owned 旧 toolchain／新 SDK 布局取得实际 RED→GREEN；空输入本机驱动查询及完整标准库前后快照通过，仅是 Mac 查询证据，官方 iPhoneOS 构建仍待执行。完整原始来源与依赖门保留。
- 根任务实际运行签名后 Guest 资源副本的完整 preload：32 件输入、8 个实际命令、来源／输入／builtin 前后相同，十八节 canonical 校验通过；没有加载镜像，也未改原 packer 报告。runtime v2 独立复核的 18 项回归、4 个额外负边界和 1683 份标准库前后快照无确认阻断。
- 根任务最终完整回归为 30 模块、316 项测试，0 跳过、0 失败；953 个原文件、61 个适配件和 342 个外部来源闭合，235 单元完整 stage 及专属 header 通过。官方 SDK 编译与最终手机验收待执行。

- 提交 `811572d64fdc49d0803f9ea78d4531cde8143e01` 的官方 iPhoneOS CI `39020136804` 全部成功；实际 235 单元、95 音频／79 C++、83 个必需强符号均通过。根任务核对 1008 份日志、1685 份 libc++ 前后及本地完整私有 stage；独立 peer 另核 1540 个私有文件、4530 条 MMD 绑定记录和实际二进制。原工件 2776296B，SHA256 `72dcc5d18c7720caabee161589c641df5a4b43da62e147f7c6111f7e270b4c42`，仍保 0444、未执行。
- 实际最终 registry 为 614 项，canonical SHA256 `36b462b95e1d17e1337db733f22d7cc45a2f7f7f3dbf1dd66cfb6040d46aa5f1`；新的完整合同绑定 236 个选用接口、283 处引用与 230 个正常门。此为本次实际读数，并未复用早期 614／616 合同。
- 本地正常开发签名候选 `outputs/nxnative-base-v0-normal-1017.38-01/BOTWNative.app` 已生成（1017.38），91,176 件准备资源逐 SHA 与 typed-03 一致；按目标 profile 先签 Guest、后签外 app，strict verify 通过。签后完整 preload 与 Host registry 不变量再次通过；原 packed Guest、原 CI 工件和全部构建收据保持不变。
- 当前仍未安装、未执行 Host／Guest／GPU。fresh CoreDevice 目标 iPhone 15 Pro 仍 unavailable，USB iPhone 清单为空；候选应用内不重复内嵌 14GB RomFS，连接后需传入 Documents/romfs，并保留原 Documents/save。实际画面、操作、声音与保存重开仍全部待验。

- 用户再次连接后，独立 IORegistry／usbmux 核验纠正 USB 报告盲区：`system_profiler SPUSBDataType` 空清单不等于没有物理手机。实际有线设备为 Cookie 的 iPhone（iPhone 12），普通 lockdown／CoreDevice 当前实读 iOS 26.5（23F77）、wired tunnel connected；旧 iOS 18.1 结论不可继续沿用。此设备满足 Apple 公布的 iPhone12／iOS26 Metal4 平台范围，仍不是游戏实际输出证明。
- 当前手机 CoreDevice 实读开发者模式 disabled，应用查询实际返回 error10005；已请求用户在手机开启并按系统提示重启确认。标准本地目录与项目签名产物共 86 份配置、16 个唯一 UUID 全部正常解码，均未包含这台新手机，冻结 validate_profile 正确拒绝；原 1017.38 签名候选及其 iPhone15Pro 准入记录保留，不能当成新手机的签名许可。
- Apple Developer 官方签名入口已打开，设备注册／开发配置材料已在本地准备；等待用户自行完成账户登录及手机设置，再经正规设备登记和 profile 重签。未修改现有 profile、未导出私钥、未安装或启动候选，原文稿与存档未改。
- 随后再次实读，当前 iPhone12 的开发者模式已 enabled、DDI services true、wired tunnel connected。仅查询 `local.botw.native` 得到未安装，普通磁盘元数据可用空间 101009825792 字节；手机设置这项前置条件已完成。Apple 官方页面仍为登录页，剩余签名配置尚未签发，未尝试以不匹配 profile 安装。
