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
- 正在接入 base-v0 着色器身份桥、NVN52 TextureView 和正常入口。它们均不替代完整运行与可玩验收。
