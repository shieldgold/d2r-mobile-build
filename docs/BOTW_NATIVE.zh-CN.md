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

当前目标是用户更换后的 **iPhone 12 / iOS 18.1**，已安装、启动 `6bd434a`；完整资源与原身份保留，进入 `nnMain` 并成功启动两个游戏工作线程，已越过 HID 调试手柄初始化。当前在设置 Npad 标识列表前的请求构造失败，尚无游戏画面、操作或游戏存档验收。下列记录保留历次真机证据，不能用旧机结果替代当前手机验证。

较早完成的真机测试为 iPhone 15 Pro / iOS 27.2，宿主源码提交 `3d1622c`：

- 本机组装 Guest.framework、开发签名与 `codesign --verify --deep --strict` 通过；独立 Bundle ID 为 `local.botw.native`，手机显示 `BOTW Native`。
- `devicectl` 确认安装与解锁后启动成功。手机 `Documents/boot.log` 已取回，证实六模块映射、269,473 条重定位、230 个门登记，以及实际映像的 Host39 HIPC 传输检查通过。
- 真机已分配游戏初始堆 `0xc2600000`（3,261,071,360 字节），完成 SDK 初始化及模块构造，并真实进入游戏 `nnMain`。这仍不是已进入可操作游戏画面的证据。
- 完整本体 RomFS 已重新传入手机 Documents，14,420,017,152 字节；手机应用真实读到 `/System/Version.txt` 为 `1.0.0`，随后通过 FSP 读取原始镜像头部。升级前后需核验资源与用户数据，不能仅凭安装成功推断容器内容仍完整。
- 该次真机已通过缺失补丁的正常回退，读取 RomFS 目录与文件元数据；Metal 平台、实际 2556×1179 显示层、FitToLayer 策略和原 SDK SystemEvent 构造均已完成。队列预算、纹理设备重置和命令内存注册的早期停止点均已越过，当前明确停止在 `nvnDeviceGetInteger` 的 `0x19` 查询。37 个私有着色器均经实际 Metal 装载通过，尚无原游戏绘制呈现。较早 `973b131` 真机版本还独立验证了原 SDK 16MiB TransferMemory 创建路径。
- 手机中原有持久离线 UID 已向账户桥发布并显式预选；升级前后身份文件与最初备份逐字节一致。存档后端就绪仍不能证明原游戏已经写入存档。
- 手机日志、机器可读 `device-run-report.json` 与签名包只保留本地 `outputs/botw-ios-3d1622c/`。开发界面和报告保持 `gameplay_verified=false`。

该次装机对应的 [iOS 宿主构建](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38764679143) 与 [准备测试](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38764678915) 均通过。外层工件核对 GitHub 官方摘要，宿主源码使用该提交的快照核对。

随后按用户要求改装到 **iPhone 12 / iOS 18.1**。提交 `c13938e` 将宿主实际编译目标与 Guest.framework 默认最低系统版本改为 iOS 18，组装器继续核验 Mach-O 和 plist 一致，并拒绝要求高于宿主版本的 framework。[iOS 18 构建](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38767921182) 通过后，已在新手机成功安装、启动，传入完整 14,420,017,152 字节 RomFS 与着色器。新机实际装载 37 个 shader、发布离线账户并挂载保存后端。

USB 导入目录的权限与私有身份后端要求不一致时，保留传输副本，由应用重新创建 `0700` 目录，再在应用停止后仅还原原 `0600` UID 文件。新机取回的 32 字节身份与原手机备份逐字节一致；身份验证规则没有放宽。以后迁移身份应采用这种文件级还原方式。

新手机的 `c13938e` 首次启动停在 `nninitStartup` 的初始堆分配：游戏固定请求 3,261,071,360 字节，超过这台手机测得的应用预算，返回真实 OOM `0xd001`。该次尚未进入 `nnMain`，证据保留在本地 `outputs/botw-ios-c13938e-iphone12/`。

随后 `e41e422` 加入显式、版本限定的低内存双堆策略：仅改写已核验原映像的两个参数站点，经只读 TSD 槽同时提供 SDK 堆和游戏根堆，保留原 32 MiB 差额。堆不超过真实 kernel 余量与最新应用可用内存扣除 256 MiB 保留量后的较小值，并按 2 MiB 向下对齐；这不是游戏完整运行峰值的保证。原 SVC 分配和 GetInfo 账目不伪造。512 MiB 根堆门槛只是明确的实验执行下限。

提交 `6272c09` 的 [iOS 构建](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38773081738) 已成功，新机完成保留数据的更新与启动。真机实际分配 SDK 堆 **1,904,214,016** 字节、根堆 **1,870,659,584** 字节，宿主核对原 SDK 真实分配结果后进入 `nnMain`。原程序随后完成零尺寸 scratch pool 路径与实际 2532×1170 VI 显示层创建，明确停止在尚未接通的 `nvnBufferBuilderSetDevice`。RomFS 字节数、37 个 shader 装载和原 UID/目录权限再次核验通过。证据位于本地 `outputs/botw-ios-6272c09-iphone12/`；实际画面、操作、游戏保存与重开仍未通过。

随后 `ed9f354` 将 37 份私有着色器的实际 Rust SPIR-V 1.4、原始 MSL、最终 MSL 与 scratch 证明接成同一条可复算来源链；`4fb5b23` 整组接入 17 个 Buffer 入口，保留 base-v0 对象布局、池代际和真实 GPU 完成生命周期。该版已在 iPhone 12 更新并装载新 bundle，越过 BufferBuilder，随后停在 16KB 宿主页的线程栈映射边界。

此前手机已安装并启动 `3e9c786`，对应 [iOS 构建](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38779653810) 与 [准备测试](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38779653775) 均成功。新的显式线程栈对齐 profile 保留原分配器与大小，只把固定版本的分配对齐提高为 16KB；运行前再次审计实际已映射指令。真机已越过原 MapMemory 停止点，实际 SDK 堆为 **1,906,311,168** 字节、根堆 **1,872,756,736** 字节，进入 `nnMain` 并读取 RomFS 文件表。随后出现原程序 `User Backtrace` 和 `am.IApplicationFunctions` 命令 22；原退出原因正在继续取证，不能把最后的服务拒绝当作最初错误。安装收据、完整日志与报告在本地 `outputs/botw-ios-3e9c786-iphone12/`。

更新 `8677910` 后，iPhone 12 已实际使用 `host-pthread-stack-v2`：两个游戏线程分别在 16KB、64KB 的真实 pthread 栈运行，完整 16KB SDK 尾页均保留，旧的线程创建失败已越过。该版 [iOS 构建](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38782965455) 与 [准备测试](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38782965686) 成功；新真机停止点为工作线程的 `GetInfo` id 8 查询。证据保留在本地 `outputs/botw-ios-8677910-iphone12/`。

`0f989ca` 已按当前进程的真实 native debugger 状态接入此查询，公开 `sysctl` 失败或短记录均明确停止，不推断为 false；严格区分 handle/subid 的内核错误。36 项内核、26 项运行时及 6 项真实线程回归通过，独立审查无阻塞。该版 [iOS 构建](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38785591686) 与 [准备测试](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38785591333) 均成功，已在 iPhone 12 保留数据更新并启动。真机已越过 `GetInfo` id 8，与固定 Mac 原程序同停在 `hid` 命令 1，即原 SDK 的 `ActivateDebugPad`；此处需要激活状态与共享内存中真实断开设备的采样环，不能仅回复成功。证据在本地 `outputs/botw-ios-0f989ca-iphone12/`；安装、启动及工作线程成功仍不代表游戏可玩。

`6bd434a` 接入真实 DebugPad 未连接样本环与 Mac 显示节奏采样，固定快照 513 项回归通过，最初跳过的两项私有资源测试另在同一快照通过。[iOS 构建](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38788565257) 和 [准备测试](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38788565291) 均成功；新手机完成保留数据更新与启动。真机及普通 Mac 执行均越过命令 1，之后出现 Npad 命令 103，再由 Prepare Thread 终止。独立 Mac 调试捕获证明 `InitializeNpad` 已返回，首个失败来自 `SetSupportedNpadIdType` 构造命令 102：服务报告的 pointer buffer 容量为零，原 SDK 为 36 字节输入预留对齐后的 48 字节时返回 `0x11a0b`，未发送请求。真实调用帧的原始 Result 与该原字节路径一致；终端日志的 `0x2a2` 可能来自后续回溯中的第二次断言，不能当作 HID 服务结果。容量合同正在补齐。真机证据保留在 `outputs/botw-ios-6bd434a-iphone12/`，调试探针独立保留且不作为普通运行验收。

本次更新继续保留完整 14,420,017,152 字节 RomFS，取回的原 UID 逐字节一致，目录与 UID 权限分别为 `0700`、`0600`。37 份着色器已装载，手机 manifest 摘要与本地 direct-v2 一致。实际游戏画面、操作、游戏保存与重开仍未通过。后续按完整模块审计和复用推进，具体范围见[上游复用矩阵](BOTW_UPSTREAM_REUSE.zh-CN.md)。

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

低内存实验必须显式增加 `--heap-policy bounded-host-memory-v1`，并同时使用 `--host39 --rewrite`，选择新的输出目录保留原产物。框架按实际宿主模块集合使用 `--modules main subsdk0 subsdk1 subsdk2 subsdk3 sdk`；七个原始输入仍全部核验。运行时会审计两个完整 thunk、分支、TSD 槽和真实分配结果；单站点修改、截断或未知载荷均拒绝。

Apple 线程栈适配另用 `--thread-stack-policy host-pthread-stack-v2`，同样要求 `--host39 --rewrite`。仅适配已核验 base-v0 的原线程构造器：对有效的正数、4KB 整倍数且不溢出 SDK 有符号容量的请求，在容量存入对象之前统一计算 `align_up(requested, 16KB) + 16KB`，让原分配器、SDK 参数与释放路径消费同一个真实容量。额外完整尾页保留 SDK 的 entry/fenv；内核仅在已拥有的映射内向下对齐真实 pthread 栈顶，保留至少原请求容量。原始输入、两个改写站点、68 字节 thunk 和实际映射均需审计；其他线程分配路径没有因此被自动准入。

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

固定源码 `8677910` 的完整本地回归运行 **500 项**，89.189 秒通过，包含新纹理模块、完整线程栈容量、服务预写、内存追踪与退出结果诊断；快照初次不含原始 NSO/RomFS，2 项私有数据检查跳过。随后在同一固定快照连接本地只读输入，两项精确检查在 0.906 秒内均通过。线程栈 profile 另经实际原程序执行，启动了两个真实游戏工作线程；其余调用族仍按各自分配、释放和尾部数据合同独立审计。包括实际 ARM64 执行、HIPC 高地址与 x18 保留、宿主边界与线程、真实 Metal 像素、资源路径、账户与保存文件 IPC、写入持久性、回调恢复和音频边界测试。低内存策略另有独立审查、5 档原始指令参数联合执行、8 种打包篡改拒绝，以及 17 个真实 ARM64 线程的 340,000 次 TSD 读取。可选 Mac 窗口测试曾间歇超时，现保留主线程窗口状态和超时前输出以便诊断；这次固定快照运行通过，不代表已确诊原间歇故障。Host39 两个分配器另有 1024 次原始 ARM64 算法和真实映射试验。云端运行 Linux 准备测试及真实 iPhoneOS 编译，再执行 macOS 原生宿主测试；测试通过与安装成功都不能替代可玩验收。

AudioOut 已整模块接入真实 AudioUnit、宿主复制、完成事件和前后台暂停，19 项测试及真实 Mac 零 PCM 回调通过；这不是游戏声音验证。若系统重置音频服务，本批明确暂停并要求重开应用，尚不自动重建仍被游戏持有的输出对象。AudioRenderer 仍未实现。

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
