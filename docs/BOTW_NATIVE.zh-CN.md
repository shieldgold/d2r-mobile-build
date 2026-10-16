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

当前目标是用户更换后的 **iPhone 12 / iOS 18.1**，已保留数据安装、普通启动复制修复版 `c2b4db8`。原 SDK 取得原始逻辑堆和根堆，进入十三个工作线程，随后受控停在 `nvnMemoryPoolInitialize` 的 physical NoCopy backing 准入：120 MiB 请求向外取整为 125,845,504 字节，首个 RW VM leaf 只剩 60,850,176 字节，具体分支为 `vm_span`。完整日志有 `host_return=1`，同一 PID 在启动约 125.1 秒后的系统进程查询中仍存活；这只是本次观察区间，尚未测得整次运行峰值，也尚未确认 VM 分裂来源。完整资源与原身份保留；尚无游戏画面、操作、音频、游戏存档或重开验收。下列记录保留历次真机证据，不能用旧机或 Mac 结果替代当前手机验证。

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

新手机的 `c13938e` 首次启动停在 `nninitStartup` 的初始堆分配：游戏固定请求 3,261,071,360 字节，超过宿主配置的应用预算，内核桥在虚拟预留及权限开放前返回 `0xd001`。这不能证明系统实际拒绝了该大小的虚拟映射。该次尚未进入 `nnMain`，证据保留在本地 `outputs/botw-ios-c13938e-iphone12/`。

随后 `e41e422` 加入显式、版本限定的低内存双堆策略：仅改写已核验原映像的两个参数站点，经只读 TSD 槽同时提供 SDK 堆和游戏根堆，保留原 32 MiB 差额。堆不超过真实 kernel 余量与最新应用可用内存扣除 256 MiB 保留量后的较小值，并按 2 MiB 向下对齐；这不是游戏完整运行峰值的保证。原 SVC 分配和 GetInfo 账目不伪造。512 MiB 根堆门槛只是明确的实验执行下限。

提交 `6272c09` 的 [iOS 构建](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38773081738) 已成功，新机完成保留数据的更新与启动。真机实际分配 SDK 堆 **1,904,214,016** 字节、根堆 **1,870,659,584** 字节，宿主核对原 SDK 真实分配结果后进入 `nnMain`。原程序随后完成零尺寸 scratch pool 路径与实际 2532×1170 VI 显示层创建，明确停止在尚未接通的 `nvnBufferBuilderSetDevice`。RomFS 字节数、37 个 shader 装载和原 UID/目录权限再次核验通过。证据位于本地 `outputs/botw-ios-6272c09-iphone12/`；实际画面、操作、游戏保存与重开仍未通过。

随后 `ed9f354` 将 37 份私有着色器的实际 Rust SPIR-V 1.4、原始 MSL、最终 MSL 与 scratch 证明接成同一条可复算来源链；`4fb5b23` 整组接入 17 个 Buffer 入口，保留 base-v0 对象布局、池代际和真实 GPU 完成生命周期。该版已在 iPhone 12 更新并装载新 bundle，越过 BufferBuilder，随后停在 16KB 宿主页的线程栈映射边界。

此前手机已安装并启动 `3e9c786`，对应 [iOS 构建](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38779653810) 与 [准备测试](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38779653775) 均成功。新的显式线程栈对齐 profile 保留原分配器与大小，只把固定版本的分配对齐提高为 16KB；运行前再次审计实际已映射指令。真机已越过原 MapMemory 停止点，实际 SDK 堆为 **1,906,311,168** 字节、根堆 **1,872,756,736** 字节，进入 `nnMain` 并读取 RomFS 文件表。随后出现原程序 `User Backtrace` 和 `am.IApplicationFunctions` 命令 22；原退出原因正在继续取证，不能把最后的服务拒绝当作最初错误。安装收据、完整日志与报告在本地 `outputs/botw-ios-3e9c786-iphone12/`。

更新 `8677910` 后，iPhone 12 已实际使用 `host-pthread-stack-v2`：两个游戏线程分别在 16KB、64KB 的真实 pthread 栈运行，完整 16KB SDK 尾页均保留，旧的线程创建失败已越过。该版 [iOS 构建](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38782965455) 与 [准备测试](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38782965686) 成功；新真机停止点为工作线程的 `GetInfo` id 8 查询。证据保留在本地 `outputs/botw-ios-8677910-iphone12/`。

`0f989ca` 已按当前进程的真实 native debugger 状态接入此查询，公开 `sysctl` 失败或短记录均明确停止，不推断为 false；严格区分 handle/subid 的内核错误。36 项内核、26 项运行时及 6 项真实线程回归通过，独立审查无阻塞。该版 [iOS 构建](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38785591686) 与 [准备测试](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38785591333) 均成功，已在 iPhone 12 保留数据更新并启动。真机已越过 `GetInfo` id 8，与固定 Mac 原程序同停在 `hid` 命令 1，即原 SDK 的 `ActivateDebugPad`；此处需要激活状态与共享内存中真实断开设备的采样环，不能仅回复成功。证据在本地 `outputs/botw-ios-0f989ca-iphone12/`；安装、启动及工作线程成功仍不代表游戏可玩。

`6bd434a` 接入真实 DebugPad 未连接样本环与 Mac 显示节奏采样，固定快照 513 项回归通过，最初跳过的两项私有资源测试另在同一快照通过。[iOS 构建](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38788565257) 和 [准备测试](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38788565291) 均成功；新手机完成保留数据更新与启动。真机及普通 Mac 执行均越过命令 1，之后出现 Npad 命令 103，再由 Prepare Thread 终止。独立 Mac 调试捕获证明 `InitializeNpad` 已返回，首个失败来自 `SetSupportedNpadIdType` 构造命令 102：服务报告的 pointer buffer 容量为零，原 SDK 为 36 字节输入预留对齐后的 48 字节时返回 `0x11a0b`，未发送请求。真实调用帧的原始 Result 与该原字节路径一致；终端日志的 `0x2a2` 可能来自后续回溯中的第二次断言，不能当作 HID 服务结果。容量合同正在补齐。真机证据保留在 `outputs/botw-ios-6bd434a-iphone12/`，调试探针独立保留且不作为普通运行验收。

`a252164` 补齐每个传输句柄独立的真实 `0x500` pointer arena、原 SDK 已证的 X/C 报文与账户输出合同；`c2aa57a` 将生产 IPC 复制接入公开 Mach VM 读写，并在业务副作用前完成整个输出范围的准备与真实可写性检查。`365f459` 修复旧资源测试夹具，使其使用真实内核堆和正确报文；固定快照 533 项回归通过，其中最初跳过的两项私有资源测试另在同一快照通过。该版 [iOS 构建](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38793963760) 与 [准备测试](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38793963749) 均成功，新机完成保留数据更新与启动。真机日志已观察到 HID 命令 102、100 及第三个游戏线程，越过旧的 `0x11a0b` 请求构造失败，随后停止在 `nvnCommandBufferReportCounter`。实际 SDK 堆 **1,904,214,016** 字节、根堆 **1,870,659,584** 字节；三个工作线程真实栈为 16KB、64KB、16KB，各保留完整 16KB SDK 尾页。证据位于 `outputs/botw-ios-365f459-iphone12/`。普通 Mac 原程序也到达同一停止点，实际执行二进制为 `c2aa57a`；它与 `365f459` 的整个 native 源码树相同，来源等同性单独记录，不能冒称运行过另一个二进制。

`9ffc84b` 接入真实 GPU 时间戳查询、计数录制和提交完成后的报告发布；录制时不提前写结果，提交时先检查整个批次的目标范围，再按实际 GPU 边界采样、校准并发布完整报告。`957e4d2` 保留 v2 栈合同，仅将已核验的 Volante 子堆从 16KB 扩至 64KB，保留原分配器和父堆；Python 打包器与 C 加载器共同审计三个固定站点。普通 Mac 原游戏已越过原栈分配失败，启动第四线程、读取语言资源，随后停止在应用语言查询命令 21。该结果不能代替手机证据。

固定 `957e4d2` 快照的 557 项测试中，554 项通过、3 项条件跳过；其中两项私有资源检查已在同一快照补跑通过，另一项物理缺采样能力测试不适用于本机 M3。该版 [iOS 构建](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38799268201) 与 [准备测试](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38799268204) 均成功。iPhone 12 已完成保留数据更新、普通启动并实际审计 v3 映像；SDK 堆 **1,906,311,168** 字节、根堆 **1,872,756,736** 字节。A14 真机报告 timestamp counter 和 stage boundary sampling 可用，已越过旧的 ReportCounter 录制停止点，但尚未观察到原游戏 GPU 报告实际发布。此次三个工作线程后先停在 `hid` 命令 66：当前通用处理器未按此命令的结构读取 ARUID。完整日志、资源与身份保留核验在 `outputs/botw-ios-957e4d2-iphone12/`；第四线程、应用语言及后续路径仍需各自在真机验证。

`585bdd0` 整组接入应用语言选择、旧 SDK 的 15 项标准语言目录，以及六轴启停和六个真实共享环。当前本体已核实支持 USen，运行时显式选择 `en-US`，来源为 `fixed-base-v0:USen:explicit-fallback`，不冒称读取了手机语言；标准语言目录也不等于已安装游戏语言资源。六轴句柄有持久启停、独立序号和解绑生命周期，尚无真实 motion backend 时仅发布明确未连接状态，不把触控或手柄按钮连接当作有效体感。固定快照 597 项测试中 596 通过、1 项物理缺采样条件不适用；两项私有资源检查已在此次完整运行中执行。该版 [iOS 构建](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38802210148) 与 [准备测试](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38802210339) 均成功。

这台 iPhone 已保留数据更新并启动 `585bdd0`，实际通过命令 66，随后通用 HID 身份检查错误拒绝无 PID 的命令 203。SDK 堆、根堆与前三个工作线程栈同上；资源大小、37 个 shader、原 UID 逐字节和 `0700`/`0600` 权限再次核验。普通 Mac 的同版原程序已通过 AM21、set1 和本地 136 字节启动参数交付，随后停在 `am.IStorageAccessor.Read` 命令 11 的 C pointer 传输。手机本轮先到达振动列表停止点，尚未验证 Mac 的语言、第四线程和存储读取进度；证据分存于 `outputs/botw-ios-585bdd0-iphone12/` 和 `outputs/botw-host39-sixaxis-585bdd0-probe/`，均保持游戏验收为 false。

`4777d03` 补齐启动参数存储的 X/A、B/C 自动选择传输，写入前检查整个输入、读出前准备整个输出，保留独立 pointer arena 与对象生命周期；另拒绝输出与 IPC 自身重叠。`0f13291` 接入真实振动设备列表对象、句柄登记和克隆/关闭状态；登记成功不代表物理振动可用。`762fe2a` 新增显式 `host-pthread-stack-v4`：继承 v3，仅将固定 SZS 解压器子堆的容量参数改为 64KB，保留原父堆、分配器、队列、对象及释放路径。原分配器对全部 2048 个八字节对齐基址残基均满足已核验的线程分配序列；原来成功的残基保留至少原有的剩余容量。Python 和 C 加载器共同审计第四个固定站点，旧 v1/v2/v3 字节与完整 manifest 保持一致。

合并版本 `c50e1e0` 的固定源码快照完成 630 项回归，629 通过、1 项物理缺采样条件不适用；私有资源检查在同次完整运行中执行。[iOS 构建](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38806009583) 与 [准备测试](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38806009673) 均成功。七个原始 NSO 不变；实际 v4 与 v3 仅 `main+0xe5ded4` 的四字节指令区域不同。独立解析最终 Guest.framework 的 Mach-O，核对了六个真实模块内容、十二份加载表与 iOS 18 部署目标。

此前 iPhone 已保留数据更新、普通启动 `c50e1e0`，实际映像 v4 加载审计通过。真机观察到 HID 203 和子对象登记命令 0、AM21、set1、136 字节启动参数及 StorageAccessor.Read11，随后成功进入十二个工作线程。实际 SDK 堆 **1,904,214,016** 字节、根堆 **1,870,659,584** 字节。首个宿主错误为原程序 `main+0x112cc68` 读取地址 `0x9`，现场 `x0=0`；其上游原因仍待取证。普通 Mac 的同版实际映像启动十三个工作线程后停在 `nvnDeviceGetInteger` 查询 `0x39`，这一 Mac 图形停止点不能替代手机故障。完整日志与机器可读报告保留在 `outputs/botw-ios-c50e1e0-iphone12/`；受控调试探针另存，不作为普通运行验收。

`b35a8c0` 接入完整 linear 纹理能力与生命周期检查，以及原 SDK 的 pitch StorageClass 元数据分支；保留尚未证明的布局分支明确拒绝。固定源码完成 656 项回归，655 通过、1 项硬件条件跳过；[iOS 构建](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38809782103) 和 [准备测试](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38809782009) 均成功。该版已在当前手机保留数据更新并普通启动，实际 SDK 堆 **1,902,116,864** 字节、根堆 **1,868,562,432** 字节，仍进入十二个工作线程后停在同一原构造函数的空指针读取。新增现场 LR 对应 `main+0x112d7d4`，只确认共同构造 wrapper，不能唯一确定更上层的分配 caller。

该次故障恢复后，公开 `task_info` 成功返回 93 个字，`phys_footprint=43,289,856`、`resident_size=179,617,792`、`virtual_size=425,875,734,528` 字节；`limit_bytes_remaining` 与 `os_proc_available_memory` 均为 **2,156,622,592** 字节。三种内存量不能互换，这个瞬时记录也不证明局部堆最大连续块或未来游戏峰值。A14 实际 Metal 查询得到 43 个普通颜色格式、最大 host 对齐 64、普通 linear stride 对齐 64、render stride 对齐 128，当前只接通 coherent pool。普通 Mac 同版原程序另停在 `nvnTextureBuilderGetStorageClass` 的 2D、flags=0、RGBA8、samples=0 请求；后续非 pitch 分支继续按原 SDK 核验。手机日志与完整来源收据保留在 `outputs/botw-ios-b35a8c0-iphone12/`。

`10c8b06` 按原 SDK 完整有效格式表补齐普通未压缩颜色的 StorageClass `0x1fe`；`0e8717f` 为内存池拒绝增加已验证 builder 快照的有限标量。普通 Mac 随后确定停在 `flags=0x189`、120 MiB、非空 backing 的 physical pool，而不是 virtual 或超过设备大小上限。下一组 physical/virtual pool、GPU 消费、别名及释放合同正在按完整上游模块审计；这一 Mac 停止点不是下述手机终止原因。

`aa0210a` 新增独立、显式的 `original-logical-v1` 宿主模式；默认 `bounded-v1` 保持原行为。原 v4 Guest.framework 及七个 NSO 不变，新模式仍使用实际已审计的 Host39、双 TSD 参数和 v4 栈指令。逻辑池为实际模块映像大小加原 SDK 堆，SetHeapSize 必须经真实虚拟预留、权限开放后才发布成功账目。启动入口、构造函数、子线程、同步回调和堆增长前重新读取公开内存余量，低于 256 MiB 时停止，并唤醒 focus 等待。该准入是瞬时观测，不能保证两次检查之间的实际写入不会触发 jetsam。

本地组装通过 `--host-memory-policy original-logical-v1` 显式选择；组装器核验新宿主来源和重新生成的 Host39/heap/v4 元数据，默认覆盖为 bounded。新模式在缺少所需公开应用限额来源的 macOS 上明确拒绝；正常 Mac 的默认模式另行验证。固定源码完成 683 项回归，682 通过、1 项硬件条件跳过；[iOS 构建](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38812587153) 和 [准备测试](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38812587236) 均成功。

此前 `aa0210a` 已保留数据更新并普通启动该显式模式。原 SDK 实际取得 **3,261,071,360** 字节堆和 **3,227,516,928** 字节根堆，逻辑池 **3,316,826,112** 字节；进入 `nnMain` 时公开 footprint 为 **27,264,768** 字节、剩余额度 **2,172,647,680** 字节。十三个工作线程进入，原 `main+0x112cc68` 空指针未重现，继续发出最大 **143,743,378** 字节的 RomFS 读取请求。随后 PID 996 于 `2026-10-16 05:55:32 +0800` 被系统 `jetsam per-process-limit` 终止，内核报告 `2148359KB`；宿主日志没有正常返回或受控错误，不能以安装、原堆成功或早期低 footprint 代替整次运行验收。

公开系统日志还确认旧 `b35a8c0` 的 PID 989 后来同样被该限额终止，因此其故障恢复前的 41 MiB 记录也不是整个进程的峰值证明。宿主逐条更新 UITextView 与资源载入均在继续核查，尚未证明其中哪项占据峰值。完整启动、来源、数据保留和系统终止收据位于 `outputs/botw-ios-aa0210a-iphone12/`。画面、操作、音频、游戏保存、重开与可玩验收仍全部为 false。

`7017c59` 将 UIKit 日志历史限制为 512 条、65,536 个 UTF-16 单元，并以 50ms 批次更新；保留完整 stdout/boot.log。固定源码完成 687 项回归，686 通过、1 项硬件条件跳过；[iOS 构建](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38814663816) 与 [准备测试](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38814663884) 均成功。该版与 `aa0210a` 的 runtime、kernel、graphics 源码及 v4 Guest.framework 均相同，手机继续显式使用 `original-logical-v1`。

该版已在当前 iPhone 12 成功安装并普通启动，原堆、根堆与十三个工作线程均验证通过。此次继续读取相同最大 143,743,378 字节的 RomFS 请求，旧空指针未重现，随后受控停在 **`nvnMemoryPoolInitialize flags=0x189`、125,829,120 字节、非空 backing**；手机实际 `max_buffer_bytes=2,147,483,648`，不是大小超限。完整日志有 `host_return=1`，系统进程列表仍有同 PID。本次观察区间没有重现此前的 jetsam，不能据此宣称整次峰值已测量、唯一根因已证明或更远阶段内存安全。启动、数据保留、固定来源和延迟进程收据位于本地 `outputs/botw-ios-7017c59-iphone12/`；下一步继续完整 physical/virtual pool 与 GPU 消费、别名和释放合同。

本次更新继续保留完整 14,420,017,152 字节 RomFS，取回的原 UID 逐字节一致，目录与 UID 权限分别为 `0700`、`0600`。37 份着色器已装载，手机 manifest 摘要与本地 direct-v2 一致。实际游戏画面、操作、音频、游戏保存与重开仍未通过。后续按完整模块审计和复用推进，具体范围见[上游复用矩阵](BOTW_UPSTREAM_REUSE.zh-CN.md)。

`e47312f` 接入完整上游 vpool interval/lease 的薄适配、coherent physical/virtual pool、当前映射下的 GPU 消费、代码与 descriptor 写入冲突预检，以及全部借用资源实际析构后才允许释放 backing 的 checked shutdown。固定源码完成 **782 项回归，781 通过、1 项硬件条件跳过**；[iOS 构建](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38821411620) 与 [准备测试](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38821411667) 均成功。跨物理映射碎片的 GPU 消费、未知 raw kind、私有纹理权威写回仍未开放；旧 copied-private texture 导入的缓存限制另见 `native/botw_graphics/MEMORY_POOLS.md`，不能将 coherent 路径的合成 GPU 结果推广到该分支。

该版已在当前手机保留数据更新并普通启动，实际 PID **1018**，相对安装前完整 boot.log 恰好新增一次 bootstrap。SDK 堆 **3,261,071,360** 字节、根堆 **3,227,516,928** 字节，进入十三个工作线程并继续读取最大 **143,743,378** 字节的 RomFS 请求，随后在 NoCopy backing 校验受控返回。手机此次通用错误不能独立证明 pool kind 或具体 VM 分支；完整动态报告在 `outputs/botw-ios-e47312f-iphone12/`。RomFS 大小及修改时间、UID 字节与权限、shader manifest 均保留。旧 screenshotr 服务不可用，未取得可作为验收的屏幕截图；日志也没有原游戏绘制呈现证据，全部可玩验收保持 false。

仅增加有限标量诊断的 `6b085f0` 已在普通 Mac 运行：实际 `flags=0x189`、120 MiB physical pool 的 rounded NoCopy 跨度为 **125,845,504** 字节，logical/read-write 校验允许，但首个 RW VM leaf 只剩 **60,850,176** 字节，明确在 `vm_span` 拒绝。它不是手机诊断结果，也未证明后续 leaf 的覆盖或权限。Apple 的 [NoCopy 合同](https://developer.apple.com/documentation/metal/mtldevice/makebuffer%28bytesnocopy%3Alength%3Aoptions%3Adeallocator%3A%29?language=objc) 要求已有分配位于单个 VM region，因此没有启用“只检查多段 RW 就直接包装为单一 Metal buffer”的方案。底层分配与符合该合同的分段 backing 仍在评估，未宣称已修复此停止点。

相同诊断源码加上历史证据文档的 `09cd584` 完成固定源码 **789 项回归，788 通过、1 项硬件条件跳过**，并通过 [iOS 构建](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38823218909) 和 [准备测试](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38823218742)。该版在当前手机保留数据安装并普通启动，PID **1020**；完整 boot.log 相对紧邻安装前基线恰好新增一次 bootstrap。手机独立确认 `flags=0x189 kind=1 stage=vm_span`，请求长度、取整跨度、首叶剩余长度、页内偏移 **8192** 与 Mac 相同；logical/write 校验均允许，VM query 成功且叶权限为 RW。这只能确认同一拒绝条件，仍未证明分裂来源。SDK 堆、根堆、十三个线程和最大 RomFS 读取与 `e47312f` 一致，受控 `host_return=1`，没有原游戏绘制呈现日志。动态完整报告位于 `outputs/botw-ios-09cd584-iphone12/device-run-report.json`；RomFS 大小及修改时间、原 UID 与 shader manifest 保留，全部可玩验收仍为 false。

`c2b4db8` 将 runtime 和 graphics 的受保护 Mach 复制合并到同一原语：保留原 logical/dirty preparation，先校验完整 source READ 与 destination WRITE VM 范围，再按 4095 字节及两侧宿主页边界分块；支持同一数值 VA 映射的前后向重叠，Mach 错误及短读取仍拒绝。当前调用点一端为独立宿主缓冲；不保证不同 VA 物理别名重叠，也不提供并发 remap 事务或跨 OS 版本的不分裂保证。固定源码 **806 项回归，805 通过、1 项硬件条件跳过**，独立 CPU 复验 24 项通过。实际普通 Mac 启动仍进入十三个线程、最大 RomFS 读取 143,743,378 字节后，在同一 `vm_span` 条件返回 1，跨度与首叶长度均未改变；因此该复制修复没有解决当前原游戏启动阻塞，不能用自有 CPU/GPU 映射测试代替此结果。动态证据位于 `outputs/botw-host39-mac-c2b4db8/ordinary-admission-evidence.json`。NoCopy 单 VM region 合同保持，后续合法分段实现仍须独立验证。

该版通过 [iOS 构建](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38824544740) 与 [准备测试](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38824544713)，在当前手机保留数据安装并普通启动为 PID **1024**；启动的可执行路径与本次安装收据一致，完整 boot.log 恰好新增一次 bootstrap。手机再次在同一 `vm_span` 分支受控 `host_return=1`，SDK 堆、根堆、线程及最大读取与前版一致，28.2 秒及 125.1 秒的系统查询均仍见同 PID；没有原游戏绘制呈现日志。RomFS 大小及修改时间、原 UID 字节与权限、shader manifest 均保留。完整动态报告位于 `outputs/botw-ios-c2b4db8-iphone12/device-run-report.json`；全部可玩验收仍为 false。

`4d9ff08` 为 physical pool 的每个实际 VM leaf 分别建立合法 NoCopy resource，完整范围校验先于资源创建；CPU canonical identity 与单段 GPU 绑定分层，全部 segment 的实际析构回执完成后才允许归还 backing。ordinary 保持单 buffer 和明确的 copied/deferred 边界，完整合同见 `native/botw_graphics/MEMORY_POOLS.md`。最终同源定点验证 91 项通过，固定全回归 **828 项，827 通过、1 项硬件条件跳过**，源码快照保持一致。普通 Mac 此次受控返回 1 的首个图形停止点改为 `nvnTextureBuilderGetStorageClass`：`target=1 flags=0 format=0x32 samples=0`，不再出现旧 physical `vm_span` 错误；仍进入十三个线程、最大读取 143,743,378 字节，没有游戏绘制呈现日志。该日志没有 physical publish 成功记录，不能仅凭不同线程的停止顺序独立断言某个 120 MiB pool 已初始化成功。原 SDK 静态核验确认 `0x32` 为 DEPTH16。完整 Mac 动态报告位于 `outputs/botw-host39-mac-4d9ff08/ordinary-admission-evidence.json`。

该版通过 [iOS 构建](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38826490640) 与 [准备测试](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38826490659)，在当前手机保留数据更新并启动为 PID **1033**；可执行路径与本次安装一致，完整 boot.log 保留原前缀并恰好新增一次 bootstrap。手机独立复现上述 DEPTH16 查询停止点，`host_return=1`；SDK 堆、根堆、线程数和最大读取均与前版一致，26.8 秒的系统查询仍见该 PID。RomFS 大小及修改时间、UID 原字节与权限、shader manifest 均保持，完整报告位于 `outputs/botw-ios-4d9ff08-iphone12/device-run-report.json`。尚需补齐深度纹理存储查询及后续真实 GPU 路径；全部可玩验收保持 false。

`b5d60eb` 补齐五种已证深度格式在原 samples=0、COMPRESSIBLE 标志清除子域中的 storage class、64KiB alignment、allocation size 及初始化对象的 64 位 size 快照；texel/GOB 范围与 allocation padding 分开，MapVirtual 和 GPU kind 准入未放宽。相关定点验证 **205 项，204 通过、1 项原有硬件条件跳过**，独立公开查询及真实 NoCopy 回执验证 **22 项通过**，固定全回归 **840 项，839 通过、1 项硬件条件跳过**，632 个 tracked 文件的源码快照保持一致。普通 Mac 原游戏首次直接记录 `physical_pool_initialized=true kind=1 flags=0x189 bytes=125829120 segments=2`，独立证明该 120 MiB physical pool 已成功发布。随后首个图形停止点为 `nvnTextureBuilderGetStorageClass` 的 `target=1 flags=0x8 format=0x1 samples=0`，即请求压缩的 R8；`mac_host_return=1`，仍有十三个线程、原堆参数及 143,743,378 字节最大读取，没有游戏绘制呈现日志。完整报告位于 `outputs/botw-host39-mac-b5d60eb/ordinary-admission-evidence.json`；该记录不构成首帧、输入、音频或可玩验收。

该版通过 [iOS 构建](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38829115589) 与 [准备测试](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38829115510)，94 个通用 native 源文件含新增 policy header 均通过来源校验。在当前手机保留数据更新并普通启动为 PID **1037**，本次安装路径与启动收据一致，完整 boot.log 保留前缀且恰好新增一次 bootstrap。真机也记录上述 120 MiB pool 的两段成功发布，随后独立复现同一 R8 `flags=0x8` 查询停止点，`host_return=1`；24.0 秒的系统查询仍见该 PID。原堆参数、线程数和最大读取与前版一致，RomFS 大小及修改时间、UID 字节与权限、shader manifest 全部保留。动态报告位于 `outputs/botw-ios-b5d60eb-iphone12/device-run-report.json`；全部可玩验收保持 false。

`61519b3` 保留请求中的 COMPRESSIBLE 标志，对原格式资格检查恒选有效 compression=0 的 65 种颜色格式，共同适配 `class=0x1fe`、alignment=512、有限 raw samples=0 的 GOB size 和现有 FE 虚拟纹理消费；新 size/GPU flags 域限定为 `COMPRESSIBLE|DISPLAY|IMAGE` 组合。GL size 提前返回 0，原 flags、provider、格式能力、别名、私有 GPU authority、提交前预检及真实 NoCopy 退役合同均保留。未证明的压缩请求在普通纹理创建发布前拒绝；其余 Class/Size/Alignment 的旧域范围以 `native/botw_graphics/TEXTURES.md` 为准。相关定点 **215 项，214 通过、1 项原有硬件条件跳过**，独立公开查询和实际 R8/ASTC GPU 正反场景 **31 项通过**。普通 Mac 本次仍记录 120 MiB pool 的两段成功发布，首个图形停止点改为 `target=1 flags=0x8 format=0xb samples=0`，即 R32UI 的压缩请求；`mac_host_return=1`，原堆参数、十三个线程和最大读取保持，没有游戏绘制呈现日志。报告位于 `outputs/botw-host39-mac-61519b3/ordinary-admission-evidence.json`，全部可玩验收保持 false。

`61519b3` 固定源码完整回归共 **850 项，849 通过、1 项原有硬件条件跳过**，耗时 178.184 秒；632 个跟踪源码文件的固定快照保持不变，源码清单 SHA256 为 `f6189765dc04815808d8974458a8290d43eeff9a6bc5fea018765ad14e222e0d`。测试通过不代表原游戏已经可玩。

`61519b3` 的 iOS/Linux 构建均成功；官方通用宿主经摘要与 94 个源码文件来源核验后，本地加入原有六模块 Guest 并签名，已保数据更新到当前 iPhone 12 / iOS 18.1。新启动 PID 1045，累计日志保留原前缀并恰新增一次 bootstrap；同样记录 120 MiB physical pool 的两段成功发布，随后在 R32UI `target=1 flags=0x8 format=0xb samples=0` 查询受控停止，`host_return=1`。约 20.0 秒后的进程快照仍含同 PID，不构成长期稳定或可玩证明。14,420,017,152 字节 RomFS 的大小与 mtime、32 字节 UID、shader manifest 和权限均保持，未重传资源；仍无游戏帧、输入、音频或存档验收。报告位于 `outputs/botw-ios-61519b3-iphone12/device-run-report.json`，签名 IPA SHA256 为 `fcef177d3fabd5c01ef146859285b9ac951f67cfdfdb874ab8edb0bc37126727`。

Metal 后端随后采用明确、只在设备初始化时确定的 `MetalPlainColor` 配置：本桥尚不表示 NVIDIA 硬件压缩 kind，保留原请求 flags，并统一选择原 SDK 已存在的 effective compression0 普通 color 分支。113 个原有效 color 的 Class、Alignment、Size、Initialize 与 virtual kind 使用同一 live-device 策略；原 65 个 provider 无关格式的事实仍独立保留，另 48 个属于宿主配置选择。此配置不改原 provider 字段，不宣称还原原默认配置或支持 NV 预压缩数据。新 Size/GPU 范围仍限 raw samples0 与 flags8/9/C/D，原格式能力、depth COMP、DB Map 拒绝和映射/权威/退役边界继续保留；详见 `native/botw_graphics/TEXTURES.md`。

该批最终定点 **72 项通过**，独立公开查询与实际整数纹理 GPU 正反场景 **36 项通过**。独立审查发现的新 COMP 初始化来源顺序问题，以 foreign/不可读/复制 provider 的 guarded-header 读取计数真 RED 复现；修复后三项均在读取对象头前拒绝。独立完整 graphics TU 的纯 CPU 九场景同时确认新域零读取，旧无 COMP/pitch 顺序保留。此前冻结版的相关 **223 项通过**另行保存，不替代最终修改后的固定源码全套回归。R32UI/RG32UI/RGBA32UI 使用正确整数纹理 shader，核验高位图案、FE 重映和别名，以及错误 kind/跨 segment 整批拒绝、GPU 权威和实际清退；这些结果仍不是原游戏首帧或可玩证明。

该策略提交 `d5746f9` 的普通 Mac 原游戏启动实际记录 `MetalPlainColor` 来源和 120 MiB physical pool 两段成功发布，首个停止点从 R32UI 改为 D16 `target=1 flags=0x8 format=0x32 samples=0` 的 StorageClass 查询，`mac_host_return=1`、`mac_process_exit=1`，`frontend_resources_released=false`。原堆参数、十三线程、143,743,378 字节最大读取保持；无游戏绘制呈现日志。对应 binary SHA256 为 `01f523bd0e5367112520c1d6f11c7d77846a8523ff775ef3a2414440418b2e67`，报告位于 `outputs/botw-host39-mac-d5746f9/ordinary-admission-evidence.json`。depth COMP 仍是明确未实现边界，没有将 D16 当作普通 color 资格放行。

`d5746f9` 固定源码完整回归共 **859 项，858 通过、1 项原有硬件条件跳过**，耗时 181.760 秒；632 个跟踪源码文件保持不变，源码清单 SHA256 为 `97f233cb59cfb27aa5fce15c4ace777658ea08ca666dd154053d99b6c85d90e0`。该轮涵盖最终初始化顺序修复，不能据此将原游戏停止状态写成可玩。

该版 [iOS 构建](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38832450939) 与 [Linux 检查](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38832450926) 均成功，核验 94 个 native 文件后本地组装签名，已保数据更新当前 iPhone 12 / iOS 18.1 并普通启动为 PID 1049。累计日志保留原前缀，bootstrap 从 24 增至 25，实际策略来源为 `MetalPlainColor`；手机同样记录 120 MiB physical pool 两段成功发布，随后在 D16 `target=1 flags=0x8 format=0x32 samples=0` 受控停止，`host_return=1`，约 24.3 秒后仍见同 PID。RomFS 大小/mtime、UID、shader manifest 和权限均保持，未重传资源。无游戏画面或操作、音频、存档、重开验收。报告位于 `outputs/botw-ios-d5746f9-iphone12/device-run-report.json`，IPA SHA256 为 `cbc134f15a92f2ad004cd0769e210f6bc96857d7f114dca5c99b8bef876df8eb`。

后续深度适配将初始化配置明确扩为枚举2 `MetalPlainColorDepth`，枚举1保留仅 color 含义。五种原普通 depth 格式的 COMPRESSIBLE metadata 分别保留双 HAL 的 raw `1/46/7B/46/C3`、64 KiB 对齐及受检大小取整；原请求 flags 不清零，Size 新域仅 flags8/108。实际 GPU 新域另限 2D、raw samples0、flags恰8的 D16/D32F，virtual Map 仅新增精确 `0x101/0x17B`，由既有完整 interval/transaction/resolver 处理。其余压缩 kind、S8、组合 depth/stencil、MSAA、array 和 fast-clear GPU 语义没有因此获得支持。完整合同见 [TEXTURES.md](../native/botw_graphics/TEXTURES.md) 与 [MEMORY_POOLS.md](../native/botw_graphics/MEMORY_POOLS.md)。

深度资源继续复用 GOB、staging、别名、映射代际与 private GPU 权威检查；buffer、descriptor、vertex、uniform、storage 和 copy 消费仍要求各自原 kind。Window 三入口针对既有 float 颜色 shader 校验资源类型，RGB10A2UI 归入共享 uint scalar trait；这些兼容层检查不构成原游戏首帧、手机呈现或可玩验收。

该批独立最终定点 **22 项通过**，涵盖真实 Metal 深度采样、读回、mip、清除/深度测试、重映、别名、CPU 局部写及 padding、错误 kind/跨段整批拒绝和旧颜色回归；20 个冻结文件 SHA256 前后保持。独立公共入口复现并修复了直接 Window Builder 的 array 类型旁路，同一无 GPU fixture 的 Initialize/Present 两场景由 RED 转为 GREEN，Present 拒绝发生在取 drawable 前。完整 vendor 模块 **9 项通过**，保留 109 个原检查、3000 组随机批和 20000 次并发重映；只将原来作为非法样本的、现已支持的 `0x101` 改为仍不支持的 `0x107`。此前相关 262 项的那次单一旧负例失败仍保留，后续定点通过不改写其记录。

提交 `3a22d53` 的固定源码全套回归共 **879 项，878 通过、1 项原有硬件条件跳过**，耗时 184.620 秒；632 个跟踪源码文件保持，源码清单 SHA256 为 `782dca35f2d1ef6e0ad147b451a6b227b4b95ae000fc6f790c19a73c4b50ec7c`。普通 Mac 原游戏启动实际记录 `MetalPlainColorDepth`，首个停止点从 D16 存储查询推进到 `nvnMemoryPoolMapVirtual index=0 storageClass=0x146`，`mac_host_return=1`、`mac_process_exit=1`、`frontend_resources_released=false`。原堆、十三线程、最大读取和 120 MiB physical pool 两段发布保持，仍无游戏呈现；不能由越过查询推断深度 GPU 已被原游戏消费。binary SHA256 为 `4050b4bb2f85ee8f4559c0010ae8134dc6163c51b56c283cd4accf2b906b134a`，报告位于 `outputs/botw-host39-mac-3a22d53/ordinary-admission-evidence.json`。

该版 [iOS 构建](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38835428644) 和 [Linux 检查](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38835428480) 均成功，94 个 native 文件来源经核验后本机组装、签名，保数据安装当前 iPhone 12 / iOS 18.1，普通启动 PID 1057。旧日志前缀保留，bootstrap 从 25 增至 26，手机实际策略为 `MetalPlainColorDepth`，同样停在 MapVirtual `0x146`，`host_return=1`；没有原游戏画面、操作、音频、存档、重开验收。RomFS 大小/mtime、32 字节 UID、shader 清单和权限均保持，未重传资源。报告位于 `outputs/botw-ios-3a22d53-iphone12/device-run-report.json`，IPA 为 19,139,615 字节，SHA256 `d4fb9f1d35407865789e646e6fd0f033191a8831dfb366be2b00ebc01f88c4e0`。

随后仅增加精确 `0x146→raw46` 的 Map/CPU identity 资格，复用现有完整 interval/transaction、lease、别名和代际算法。D24 与 D24S8 共用该 label，单凭 Map 错误不能区分实际格式。GPU resolver 在身份验证后、任何 Metal backing 物化前明确拒绝 raw46，包括 expected=-1；原 raw0、FE、D16/32F 的 GPU 域不扩大，D24/组合 depth-stencil 的 Texture Initialize 继续拒绝。现有两种初始化错误仅追加已验证 Builder 快照的 target/flags/format/samples，不读取纹理内容、地址或新增 VM 查询。

该小批四个相关模块共 **100 项通过、零跳过**，35.358 秒，涵盖完整 vendor 基线和事务、CPU Map/别名/代际、GPU 物化前拒绝及初始化错误上下文。初始四项行为 RED 与中途“只开放 identity 会误入 GPU resolver”的独立 RED 均单独保留；未实现 24 位深度转换，未将这些测试计作原游戏 GPU 消费或可玩证明。

该批提交 `429bf9e` 后普通 Mac 原游戏已通过 Map `0x146`，下一首个停止点为 `nvnMemoryPoolMapVirtual index=0 storageClass=0x1c3`；本次错误只暴露映射类别，不能据此确认实际纹理创建或消费。`mac_host_return=1`、`mac_process_exit=1`、`frontend_resources_released=false`，无游戏呈现。binary SHA256 为 `ccf3190f9dfceeee1eceaf1aaf93b7deee3654849b1df163ac71426db2eb4a63`，报告位于 `outputs/botw-host39-mac-429bf9e/ordinary-admission-evidence.json`。本批只依据相关测试和独立审查验证，未重跑固定全套，也未另行安装手机；手机当前已验证版本仍为 `3a22d53`，不混用两版执行证据。

`429bf9e` 的 [iOS 通用宿主构建与合成测试](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38836936522) 和 [Linux 检查](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38836936534) 后续均成功，精确 head 已核验；这些结果没有改变上述原游戏停止点或手机版本。

下一小批将精确 `0x1C3→rawC3 / ZF32_X24S8` 纳入相同 Map/CPU identity 合同；raw46/C3 互为不同 kind，相邻区间不合并，不能取得跨 kind 的连续 lease。GPU 对两者分别在身份验证后、backing 物化前具名拒绝，包含 expected=-1；C3 不替代 raw7B，也不开放 D32FS8/stencil 创建或转换。三组相关模块共 **65 项通过、零跳过**，30.522 秒，保留完整 vendor 算法与原随机/并发压力测试。初始 C3 未准入和中间仅开放 identity 后误入 GPU resolver 的行为 RED 均保留，修复后两个 CPU/前拒 mode 表驱动验证46/C3；它们不构成原游戏 GPU 消费或可玩验收。

提交 `179e3a9` 的普通 Mac 原游戏已越过上述映射停止点，实际首个 fatal 改为 `nvnTextureWriteTexels` 的 `BWTexture serial=0 actual=<none>`，`mac_host_return=1`、`mac_process_exit=1`，没有游戏呈现证据。binary SHA256 为 `98599b3ee59a2595b3a0d64ad8884f0e0e2d797d0d043f2f8dee98607a7cd00f`，报告位于 `outputs/botw-host39-mac-179e3a9/ordinary-admission-evidence.json`。本次 stderr 没有前序 Texture Initialize 失败行；现有 `report()` 本来就会输出 stderr 并触发错误回调，不能以此认定出现过被后续错误掩盖的初始化失败，也不能据此归因于 D24 或断言初始化从未调用。该版尚未安装手机，实际对象传递原因继续核验。

`179e3a9` 的 [iOS 通用宿主构建与合成测试](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38837378033) 和 [Linux 检查](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38837377969) 均成功，精确 head 已核验；原游戏和手机的验收结论保持按各自实际版本记录。

为核验对象传递，后续诊断只保存宿主拥有的最近四条初始化记录和饱和计数：API 已传入的 raw 指针值、已验证 Builder 的四个标量、put 已发布的 serial，以及 started/returned_false/published 状态。BWTexture 校验失败时最多输出四次，每行最多四条历史；复用 get 原已读的 key/可读性，不增加游戏内存、对象头、栈或 VM 查询。成功初始化不输出；正常返回、原错误文字、回调和支持域保持。最近四条未命中只写 not_retained；异常中断不伪装为 FALSE。完整成功的图形初始化/关闭重置记录，checked shutdown 失败保留。

该诊断相关 43 项测试先全部通过（9.661 秒）；随后独立行为 RED 发现发布标记应紧随既有 put/identity，而不能放在可能异常的引用登记之后。修正后最终 **8 项 CPU 测试通过**（5.175 秒），覆盖12个自有 native 模式，包括同/异 raw、零 serial、未验证 Builder、已验证后异常、原回调/错误保持、日志及环容量、成功/失败关闭和 UINT64 饱和。它们使用 fake Metal 工厂，不创建真实 GPU 设备或命令；该批用于定位，尚未证明或修复原游戏对象失效根因。

提交 `275952b` 的普通 Mac 原游戏实测仍在 WriteTexels 的零 serial 对象停止；宿主记录显示本生命周期 `bridge_init_entry_calls=2 validated_init_attempts=2 published_successes=2`，历史未饱和且两条全部保留，均为 `target=1 flags=1 format=0x25 samples=0` 的 published 对象。出错 raw 与两条记录均不同，`match=not_retained`，没有 returned_false 记录。该事实用于继续查原接口路由与对象传递，不能直接断言原 SDK 全局从未初始化此对象，也不能认定为 D24。binary SHA256 为 `a4fbe47dfc975d8506bafe65bdaab46ef718d125e94632b28170012415fe6479`，受限解析报告位于 `outputs/botw-host39-mac-275952b/texture-lifecycle-evidence.json`；只使用已有日志和宿主记录，无新增 VM 查询或游戏栈内容读取，仍无游戏画面验收。

当前 `275952b` 的固定全套共 **890 项，889 通过、1 个原硬件条件跳过**，190.833 秒，632 个源码文件前后摘要不变；source manifest SHA256 为 `5c2a40b7c8b6ce73a6ff155ed592455a8cb8ad73f09ee85d1b5ab406785681f7`。[iOS 通用宿主构建与合成测试](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38838373697) 和 [Linux 检查](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38838373727) 均成功，精确 head 已核验。通用宿主保持94个 native 来源文件，上限100未改。

该版已保数据更新到当前 iPhone 12 / iOS 18.1，并普通启动 PID1068；安装后 executable 与本次安装地址匹配。累计启动标记26→27、旧日志前缀逐字节保持，本次恰好一次新启动。新日志与 Mac 同样只有两条成功初始化记录，随后向不同 raw 的零 serial 对象 WriteTexels 时停止，host_return=1，没有游戏呈现、操作、音频、存档或重开验收。两条日志视图分别保留精确 fresh 文件及从 bootstrap 标记开始的摘要，后者仅省略开头一个换行；额外受限解析报告位于 `outputs/botw-ios-275952b-iphone12/texture-lifecycle-evidence.json` 与 `policy-evidence.json`。签名 IPA 19,141,010 字节，SHA256 为 `79113d33813e28f66448850c003edc2cb4f6d6e12754ea82cf3933cde3ef5964`。RomFS 14,420,017,152 字节及 mtime、32字节 UID、着色器清单和目录/文件权限分别核验保留，未重传 RomFS。

窄静态审查确认 main 存在 pool 为空时跳过纹理初始化、随后条件进入 WriteTexels 的路径，但尚未证明本次实际走过它；局部 GetStorageSize 返回0仅存入布局字段，未直接清空该 pool。原 base-v0 的 MEMORY_POOL_PAGE_SIZE 查询来自 provider 初始化的 device 字段，不能以其他版本的常量或 depth 的64KiB对齐规则证明其恒为65536；当前无修改页策略的依据。下一观测仅限正常 GetStorageSize 已验证输入与返回0的标量，保持返回和支持域。该增量先在275源码上得到5项真实行为失败，再通过14项相关 CPU 测试；独立复核同样14项通过并确认四个来源文件摘要不变。每图形生命周期最多四条，非零结果静默，未验证输入、返回、回调、错误、锁和成功/失败生命周期重置语义保持；没有新增 guest 内容或 VM 读取。

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
