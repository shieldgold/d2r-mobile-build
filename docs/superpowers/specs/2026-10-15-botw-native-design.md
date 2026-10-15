# 《旷野之息》原生移植：首阶段设计

用户已于 2026-10-15 确认原生路线及本阶段架构；在独立分支推进。

## 目标与验收边界

最终目标是在 iPhone 原生运行 Switch 版《旷野之息》。首阶段交付可复现的本地解包、模块审计与离线指令改写实验。此阶段不交付可玩的 IPA，不以扫描通过代替启动或游戏画面验证。

## 架构选择

选择复用 D2R 的通用 ARM64 离线改写器、后续逐步拆出宿主兼容层。另一条路线是基于游戏反编译重建源码，但已检查的 uking 项目不能运行游戏，且其 1.5.0 版本与当前输入不同。本阶段不走此路线。

原理是离线处理 x18、TLS、Horizon 系统调用与计时器，再把代码放入签名的 Mach-O 可执行段，后续由宿主接管系统服务与 NVN。不会引入 JIT 模拟器，也不会把 D2R 的游戏逻辑、补丁地址、着色器表用于塞尔达。

## 首个版本与输入

- 固定当前本体：Application ID `01007ef00011e000`，包版本 `0`；先验证本体，更新和 DLC 独立保留。
- 本体含七个 NSO：`main`、`rtld`、`sdk`、`subsdk0` 至 `subsdk3`，另含 `main.npdm`。
- 用完整模块 SHA256 与 build ID 固定实验输入，不只相信文件名。
- 用户的 NSZ 是 NCZSECTN + Zstandard 连续流。流式跳过大型资源段，只把有边界检查的 ExeFS 写入本地输出；不执行游戏代码。
- 此方式读取压缩包中已经保存的解压内容，不需要打印或导出 NCZ 内的密钥字段。遇到其他压缩格式或加密 NSP，明确拒绝并报告，不猜测密钥。

## 组件与错误处理

1. `scripts/botw_native/archive.py`：有界 PFS0 / NCZ 解析；拒绝越界、重叠、重复及目录穿越的成员；输出先暂存，完整验证后发布。
2. `scripts/botw_native/nso.py`：NSO 段解压与哈希校验、MOD0 / 动态符号解析、模块间未解析导入清单。
3. `scripts/botw_native/rewrite.py`：下载并校验固定提交及 SHA256 的上游工具，只运行 `native` 规则域、关闭私有门；记录成功或具体失败，不忽略断言。
4. `scripts/botw_native/__main__.py`：执行流水线，生成本地 JSON 报告；没有宿主与图形验证时，始终标注 `phone_ready=false`。

保留原文件，输出在被 Git 忽略的 `outputs/`；工具在 `.tools/`。仓库只保存工具源码、版本元数据、文档、人工合成的测试夹具，不提交游戏、密钥或生成的客体代码。

## 验证

- 单元测试覆盖恶意容器边界、截断流、输出回滚、NSO 段哈希损坏、符号表越界、版本不匹配。
- 对当前实际输入完整提取七个模块并比对 profile，审计所有模块的导入与导出。
- 对实际模块调用固定版改写器；逐模块保存日志与改写结果，检查 D2R 专用 cuts / obs / params 均为空。
- 区分“改写成功”“结构核验”“指令等价验证”“进入游戏”，分别报告，不合并为一个成功状态。
- 原有 D2R 49 项测试保持通过；完成代码审查、Git 提交与推送。

## 后续依赖

宿主阶段需重新建立塞尔达模块启动顺序、重定位与服务接管清单；图形阶段需适配塞尔达的着色器容器与 NVN 使用集合；之后才是签名、手机安装、实际入口与画面测试。完整 iOS SDK 尚未在当前命令行环境中找到，现有 D2R CI 可作为后续编译环境。已连接手机实时报告为 iPhone 15 Pro / iOS 27.2。

## 源码依据

- [D2R 原理](https://github.com/jhaizhou-ops/d2r-mobile/blob/e2a0c9a9b5d65f6cd505e967e4760e2745e90ca8/dm/README.zh-CN.md)
- [iOS 代码嵌入](https://github.com/jhaizhou-ops/d2r-mobile/blob/e2a0c9a9b5d65f6cd505e967e4760e2745e90ca8/dm/ios/README.zh-CN.md)
- [游戏专用记录](https://github.com/jhaizhou-ops/d2r-mobile/blob/e2a0c9a9b5d65f6cd505e967e4760e2745e90ca8/docs/DERIVED.zh-CN.md)
