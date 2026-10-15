# 《旷野之息》原生移植：程序准备、宿主与真机实验

本目录对应用户已确认的 D2R 式原生移植路线。**开发版宿主已在 iPhone 上安装和执行原始 SDK 代码，尚不能游玩。** 现有 D2R 工作流不变。

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

这项测试只证明新规则的人工合成用例，不代表整个游戏全部改写位置的语义等价验证已完成。离线准备报告保持 `semantic_equivalence_verified=false`、`guest_execution_tested=false`、`phone_ready=false`。后续真实执行另记宿主日志，不反写离线报告。

## 原生宿主与手机证据

独立宿主位于 `native/botw_host/`，实现六个业务模块的装载、重定位、动态符号解析、线程槽与系统调用门。`rtld` 的装载职责由宿主接管；七个输入均审计和改写，实际宿主只需六个业务模块进入框架。遇到未知系统服务时记录原程序偏移并停止。

2026-10-16 已完成的真机测试，iPhone 15 Pro / iOS 27.2，宿主源码提交 `3d1622c`：

- 本机组装 Guest.framework、开发签名与 `codesign --verify --deep --strict` 通过；独立 Bundle ID 为 `local.botw.native`，手机显示 `BOTW Native`。
- `devicectl` 确认安装与解锁后启动成功。手机 `Documents/boot.log` 已取回，证实六模块映射、269,473 条重定位、230 个门登记，以及实际映像的 Host39 HIPC 传输检查通过。
- 真机已分配游戏初始堆 `0xc2600000`（3,261,071,360 字节），完成 SDK 初始化及模块构造，并真实进入游戏 `nnMain`。这仍不是已进入可操作游戏画面的证据。
- 完整本体 RomFS 已重新传入手机 Documents，14,420,017,152 字节；手机应用真实读到 `/System/Version.txt` 为 `1.0.0`，随后通过 FSP 读取原始镜像头部。升级前后需核验资源与用户数据，不能仅凭安装成功推断容器内容仍完整。
- 该次真机已通过缺失补丁的正常回退，读取 RomFS 目录与文件元数据；Metal 平台、实际 2556×1179 显示层、FitToLayer 策略和原 SDK SystemEvent 构造均已完成。队列预算、纹理设备重置和命令内存注册的早期停止点均已越过，当前明确停止在 `nvnDeviceGetInteger` 的 `0x19` 查询。37 个私有着色器均经实际 Metal 装载通过，尚无原游戏绘制呈现。较早 `973b131` 真机版本还独立验证了原 SDK 16MiB TransferMemory 创建路径。
- 手机中原有持久离线 UID 已向账户桥发布并显式预选；升级前后身份文件与最初备份逐字节一致。存档后端就绪仍不能证明原游戏已经写入存档。
- 手机日志、机器可读 `device-run-report.json` 与签名包只保留本地 `outputs/botw-ios-3d1622c/`。开发界面和报告保持 `gameplay_verified=false`。

该次装机对应的 [iOS 宿主构建](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38764679143) 与 [准备测试](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38764678915) 均通过。外层工件核对 GitHub 官方摘要，宿主源码使用该提交的快照核对。

同一源码的 Mac ARM64 原生执行与手机均停在上述查询。后续工作优先按完整模块审计、复用上游，再补有证据的版本和后端差异；具体范围见[上游复用矩阵](BOTW_UPSTREAM_REUSE.zh-CN.md)。最新启动日志保留在 `outputs/botw-host39-probe/`。图形、触摸/手柄、线程、只读资源和存档后端已有独立实现与测试，实际画面、操作及游戏存档验收仍未完成。

Host39 是固定版本的显式原生适配：两个原 SDK 地址分配器使用真实宿主保留区，SDK 和 subsdk 中共 36 处 HIPC 内联地址编码按固定原始上下文改写，另接管五个已审计的 PLT helper。宿主在任何原程序执行前检查实际映射的分支和 thunk，未适配的镜像不能用 `--host39` 启动。36 位默认策略保留。原始 NSO 不改动。

云端工作流 `botw-native-ios.yml` 只编译通用 C/汇编/UIKit 宿主，精确上传三文件组成的无游戏 IPA。游戏程序只在 Mac 本机加入后签名；不向 GitHub 上传游戏、生成的 Guest.framework、描述文件或私钥。

传输资源目录时使用默认合并行为，显式指定 `--remove-existing-content false`。当前 `devicectl` 的目录清理选项已在独立 temporary 域复现为移除目标父目录的兄弟文件；禁止在应用数据容器中使用 `--remove-existing-content true`。每次更新后分别核验 RomFS 字节数、着色器加载结果和本地用户身份；用户身份与存档仅备份在本机 ignored 输出中。

可信下载工具 `botw_native.artifact` 使用 GitHub CLI 的现有认证读取指定成功 run，核对仓库、提交、分支与工件身份，验证 GitHub 官方外层 ZIP 摘要和字节数，再解出唯一宿主 IPA。`shell-provenance.json` 中的 `shell_sha256` 用于下面的 `SHELL_SHA256`；不能从 IPA 自带 manifest 建立二进制信任。

```sh
PYTHONPATH=scripts .tools/botw-venv/bin/python -m botw_native.artifact \
  --repository shieldgold/d2r-mobile-build --run-id "$HOST_RUN_ID" \
  --commit "$HOST_COMMIT" --output outputs/botw-ios-shell
```

复现组装：

```sh
PYTHONPATH=scripts .tools/botw-venv/bin/python -m botw_native.guest_framework \
  --report outputs/botw-native-base/report.json \
  --rewrite outputs/botw-native-base/rewrite --output outputs/botw-guest-framework

PYTHONPATH=scripts .tools/botw-venv/bin/python -m botw_native.ios assemble \
  --shell outputs/botw-ios-shell/BOTWNative-shell.ipa \
  --guest outputs/botw-guest-framework \
  --report outputs/botw-native-base/report.json \
  --rewrite outputs/botw-native-base/rewrite \
  --expected-shell-sha256 "$SHELL_SHA256" \
  --output-app outputs/botw-ios-local/BOTWNative.app
```

`SHELL_SHA256` 必须来自独立可信构建工件通道，不可仅采信 IPA 内自行声明的摘要。下载的宿主还必须与本地源码摘要匹配；使用历史构建时通过 `--source-root` 指向该提交的源码快照。`ios sign --help` 列出本地签名参数。签名校验会确认设备、证书、Team、Bundle ID、描述文件有效期，仅使用其允许的普通开发权限。

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

准备当前 Host39 开发目标时使用独立输出目录：

```sh
PYTHONPATH=scripts .tools/botw-venv/bin/python -m botw_native \
  --exefs outputs/botw-native-local/ExeFS \
  --output outputs/botw-native-host39 --rewrite --host39
```

Host39 框架打包仍核验固定七模块指纹、通用改写摘要及全部地址补丁；本机组装器从验证结果写入 `BOTWGuestAddressBits`，前端读取后选择对应策略。没有这个证据链时不会自动选择 39 位。

不带 `--rewrite` 只进行模块审计。带此参数会下载 SHA256 固定的开源构建工具，并以 `native` 规则域运行，关闭 D2R 私有门；**不会执行游戏程序**。

输出包括 `report.json`、`ExeFS/`、`rewrite/*.rewrite.log`、改写后的映像与门表。`report.json` 是状态依据；中途失败不能用目录里残留的个别文件判定成功。当前实际实验结果位于本工作区 `outputs/botw-native-base/`。

## 测试

```sh
PYTHONPATH=scripts .tools/botw-venv/bin/python -c \
  'from pathlib import Path; from botw_native.rewrite import fetch_tool; fetch_tool(Path(".tools/botw-native"))'
BOTW_REWRITER_PATH=.tools/botw-native/e2a0c9a9b5d65f6cd505e967e4760e2745e90ca8/patch_nso.py \
  .tools/botw-venv/bin/python -m unittest discover -s tests -v
```

首阶段为 **66 项测试通过**：原有 D2R 49 项、新增 17 项，其中一项集成测试执行上述 48 组 CPU 状态对比。宿主阶段另增人工程序执行、框架布局、来源校验、ZIP 边界和本地签名流程测试。GitHub Actions 仅使用人工合成输入，不上传游戏文件。

固定源码 `3d1622c` 的完整本地回归运行 **397 项**：396 项通过，1 项真实 RomFS 检查因快照不含私有资源跳过；该项曾在实际资源目录独立通过，相关资源代码未变。包括实际 ARM64 执行、HIPC 高地址与 x18 保留、宿主边界与线程、真实 Metal 像素、资源路径、账户与保存文件 IPC、写入持久性及回调恢复测试。Host39 两个分配器另有 1024 次原始 ARM64 算法和真实映射试验。云端分别运行 Linux 准备测试与 macOS 原生宿主测试，再编译 iPhoneOS 宿主；测试通过与安装成功都不能替代可玩验收。

未安装可选原生实验依赖或未设置 `BOTW_REWRITER_PATH` 时，相关测试会明确标为跳过；不得把这种运行宣称为完整的原生实验测试通过。

## 当前限制与后续工作

- 当前只支持已固定本体版本的 solid Zstandard NCZ，要求连续 section；不支持 NCZBLOCK 或直接解密 NSP。本地更新包 1.9.0 与两个 DLC 尚未应用。
- 完整本体 RomFS 已本地导出并挂载到手机；使用独立 BOTW 目标。
- 上游指令改写器来自固定提交，本地识别扩展的源码摘要也记录在报告中。D2R 专用 cuts、obs、params 全部为零。
- Mac 实际游戏已走完 `sdk → subsdk3 → subsdk2 → subsdk1 → subsdk0 → main` 的 DT_INIT 并进入 nnMain；系统服务及图形路径仍按实际调用继续补齐。
- 手机当前仍是尚未通过游戏画面、操作及存档验收的开发版本。完整本体资源已经存在，尚不能游玩。

游戏文件、密钥、生成的程序代码与日志只保留在被忽略的本地目录。仓库不分发这些内容。

## 上游与许可

复用 [jhaizhou-ops/d2r-mobile](https://github.com/jhaizhou-ops/d2r-mobile) 的 MIT 许可改写器，固定提交 `e2a0c9a9b5d65f6cd505e967e4760e2745e90ca8`、工具 SHA256 `c9a0c7574547ab945e4648bd6664e86fa4a77de12253a55e7b464232b2cdf274`。原始工具单独校验，本地控制流识别扩展保存在 `scripts/botw_native/`；未来更新需重新审查和固定版本。
