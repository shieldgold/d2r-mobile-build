# 《旷野之息》原生移植：本地程序准备与改写实验

本目录对应用户已确认的 D2R 式原生移植路线。**目前完成程序准备与改写实验，尚无可玩的 iOS 应用。** 现有 D2R 工作流不变。

## 已在当前输入上验证

2026-10-15，以当前下载的 Switch 本体 `01007ef00011e000` / 包版本 `0` 为实验输入：

| 模块 | 离线改写与结构检查 | 改写位置数 |
| --- | --- | ---: |
| main | 通过 | 9,965 |
| rtld | 通过 | 28 |
| sdk | 通过 | 4,973 |
| subsdk0 | 通过 | 967 |
| subsdk1 | 通过 | 31 |
| subsdk2 | 通过 | 120 |
| subsdk3 | 通过 | 6,775 |
| 合计 | 七个模块均通过 | 22,859 |

七个模块均完成 NSO 段校验和完整文件 SHA256 固定。模块间符号审计得到 1,152 个不同导入名，其中 26 个未在其他模块导出表中定义。**这 26 个不等于全部待实现的系统接口**：SDK 内已定义、但底层依赖 Horizon 服务的函数，后续仍需逐项审计和接管。

原版 D2R 改写器在两个模块的间接跳转处拒绝执行。新增的本地适配器识别有 unsigned 上界检查的只读 signed-offset 跳转表，再复用上游的密集落地垫实现；不移除原断言，不写死游戏地址。规则同时检查 W18 写入、标志位来源、控制流入口、表边界、目标地址与展开范围。两种人工合成的跳转形式经过 48 组原始 / 改写后的 Unicorn CPU 状态对比，核对通用寄存器、虚拟 x18、SP、NZCV 和落点。

这项测试只证明新规则的人工合成用例，不代表整个游戏全部改写位置的语义等价验证已完成。JSON 报告始终保持 `semantic_equivalence_verified=false`、`guest_execution_tested=false`、`phone_ready=false`。

## 本地运行

在仓库目录创建工具环境：

```sh
python3 -m venv .tools/botw-venv
.tools/botw-venv/bin/python -m pip install -r requirements.botw.txt
```

将 `BOTW_PACKAGE` 设置为自己的本体 NSZ 绝对路径，再执行：

```sh
PYTHONPATH=scripts .tools/botw-venv/bin/python -m botw_native \
  --package "$BOTW_PACKAGE" \
  --output outputs/botw-native-local --rewrite
```

程序流式读取 NSZ，跳过大型资源段，只保存 ExeFS 的七个模块与 `main.npdm`，然后核对 `profiles/botw-switch-base-v0.json` 中的完整模块摘要。源文件不改动。输出目录已存在时会检查来源并重新验证模块，不静默覆盖另一份输入。

已有 ExeFS 时可直接审计或重跑改写：

```sh
PYTHONPATH=scripts .tools/botw-venv/bin/python -m botw_native \
  --exefs outputs/botw-native-local/ExeFS \
  --output outputs/botw-native-local --rewrite
```

不带 `--rewrite` 只进行模块审计。带此参数会下载 SHA256 固定的开源构建工具，并以 `native` 规则域运行，关闭 D2R 私有门；**不会执行游戏程序**。

输出包括 `report.json`、`ExeFS/`、`rewrite/*.rewrite.log`、改写后的映像与门表。`report.json` 是状态依据；中途失败不能用目录里残留的个别文件判定成功。当前实际实验结果位于本工作区 `outputs/botw-native-base/`。

## 测试

```sh
PYTHONPATH=scripts .tools/botw-venv/bin/python -c \
  'from pathlib import Path; from botw_native.rewrite import fetch_tool; fetch_tool(Path(".tools/botw-native"))'
BOTW_REWRITER_PATH=.tools/botw-native/e2a0c9a9b5d65f6cd505e967e4760e2745e90ca8/patch_nso.py \
  .tools/botw-venv/bin/python -m unittest discover -s tests -v
```

本次本地完整结果为 **66 项测试通过**：原有 D2R 49 项、新增 17 项，其中一项集成测试执行上述 48 组 CPU 状态对比。新增 GitHub Actions 使用人工合成输入执行相同测试，不上传游戏文件。

未安装可选原生实验依赖或未设置 `BOTW_REWRITER_PATH` 时，相关测试会明确标为跳过；不得把这种运行宣称为完整的原生实验测试通过。

## 当前限制与后续工作

- 当前只支持已固定本体版本的 solid Zstandard NCZ，要求连续 section；不支持 NCZBLOCK 或直接解密 NSP。本地更新包 1.9.0 与两个 DLC 尚未应用。
- 资源段尚未完整导出；当前输出不是可以导入 D2R Mobile 的 Data。
- 上游指令改写器来自固定提交，本地识别扩展的源码摘要也记录在报告中。D2R 专用 cuts、obs、params 全部为零。
- 下一阶段需建立塞尔达模块装载与启动顺序，接通系统服务，再适配游戏着色器容器和 NVN 图形路径。之后才能编译签名、安装手机，并以实际游戏画面验证。
- 当前连接设备报告为 iPhone 15 Pro / iOS 27.2；没有向其安装测试包，也没有修改其系统设置。

游戏文件、密钥、生成的程序代码与日志只保留在被忽略的本地目录。仓库不分发这些内容。

## 上游与许可

复用 [jhaizhou-ops/d2r-mobile](https://github.com/jhaizhou-ops/d2r-mobile) 的 MIT 许可改写器，固定提交 `e2a0c9a9b5d65f6cd505e967e4760e2745e90ca8`、工具 SHA256 `c9a0c7574547ab945e4648bd6664e86fa4a77de12253a55e7b464232b2cdf274`。原始工具单独校验，本地控制流识别扩展保存在 `scripts/botw_native/`；未来更新需重新审查和固定版本。
