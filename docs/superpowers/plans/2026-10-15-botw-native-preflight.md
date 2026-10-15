# BOTW 原生移植首阶段实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. 本次按用户已确认的架构在当前会话顺序执行。

**Goal:** 用当前本体完成可复现的本地模块审计与离线改写实验。

**Architecture:** 新增独立 `botw_native` Python 包，保留 D2R 所有现有接口。版本 profile 固定模块摘要，输出与下载工具均不进入 Git。

**Tech Stack:** Python、Zstandard、LZ4、Capstone、固定提交的 D2R ARM64 改写器。

## 任务 1：容器与 NSO 解析

- [x] 新建 `tests/test_botw_native.py`，用人工构造的 PFS0、NCZ 与 NSO 验证正常解析及损坏拒绝。
- [x] 运行 `python3 -m unittest discover -s tests -p test_botw_native.py -v`，确认缺少实现导致断言失败。
- [x] 实现 `scripts/botw_native/archive.py` 与 `nso.py`，限额、范围和摘要检查直接对应测试中的输入边界。
- [x] 重跑测试，预期全部通过。

核心调用约定：

```python
members = read_pfs0(stream, offset=0, size=package_size)
modules = extract_exefs(package_path, output_directory)
report = inspect_nso(module_path.read_bytes(), module_path.name)
```

## 任务 2：版本与改写流水线

- [x] 新建七模块 profile 和专用依赖文件。
- [x] 为 profile 不匹配、工具哈希不匹配、D2R 专用改写泄漏增加失败测试。
- [x] 实现 `rewrite.py` 和 CLI：固定工具版本，隔离环境，输出机器可读报告，`phone_ready` 始终为 false。
- [x] 实际运行：

```sh
PYTHONPATH=scripts python3 -m botw_native \
  --package '/absolute/path/to/base.nsz' \
  --output outputs/botw-native-base --rewrite
```

- [x] 检查七个模块的 build ID、摘要、导入、改写日志；所有失败保留具体原因。

## 任务 3：验证与留档

- [x] 写 `docs/BOTW_NATIVE.zh-CN.md`，记录真实结果与运行命令。
- [x] 执行完整 `python3 -m unittest discover -s tests -q`，确认 D2R 回归无新增失败。
- [x] 检查 Git 差异、凭据与游戏文件排除情况；提交并推送 `codex/botw-native-preflight`。
- [x] 报告已验证结果及尚未实现的宿主、图形与手机安装阶段。
