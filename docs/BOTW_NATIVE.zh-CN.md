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

当前目标是用户更换后的 **iPhone 12 / iOS 18.1**。当前安装 `3aaca29` 显式诊断版，argv仅 `--observe-graphics-lock`，PID1215；固定本机1055项中1054通过、1项条件跳过，精确head双CI成功，官方98项源码工件核验、组装签名后保数据更新。四个有界宿主锁样本已取齐，generation变化且包含idle，不支持同一轮持续持锁跨采样的解释；217.463秒完整采样无host_return/新error/真实帧，约600秒只查metadata仍无frame，后者不冒充重读boot内容。随后3a的observation-4已完整host_return=1，实际首错是 `nvnMemoryPoolInitialize: Memory pool CPU snapshot allocation failed`，在Metal工厂之前；与此前b7的Metal allocation generic首错不同，不能据此认为旧问题已修。正常日志的footprint287821504、resident350535680、available1912090944仅为当次advisory，不保证连续分配成功也不证明OOM。UID、14,420,017,152字节RomFS大小mtime、shader与权限均保留。**尚不能游玩**。

后续主线已纠正为最新完整上游host、NVN和shader工具链，平台差异通过薄适配接入，见[完整后端计划](superpowers/plans/2026-10-16-complete-upstream-backend.md)。当前手机图形核心仍有本地子集实现，37个shader只覆盖内置UI/字体；kernel/IPC/HID也不能统称整模块移植。旧上游5f已落后98提交；本次完整源码锁定21f，其启动记录达到开场文字，但没有菜单或可玩验收。新上游面向1.9/SDK20.5.22/NVN55.15，不能套用到当前base-v0/NVN52.32。完整工具与隔离后端的编译通过不代表已经替换手机生产实现。

此前 `c2b4db8` 取得原始逻辑堆和根堆，进入十三个工作线程，受控停在 `nvnMemoryPoolInitialize` 的 physical NoCopy backing 准入：120 MiB 请求向外取整为125,845,504字节，首个RW VM leaf只剩60,850,176字节，具体分支为 `vm_span`。该次完整日志有host_return=1，同一PID在启动约125.1秒后的系统进程查询中仍存活；这仅是该次观察区间，不是当前版本停止点、整次运行峰值或VM分裂来源证明。

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

提交 `6da5352` 的普通 Mac 原游戏新增一条真实零大小查询：`target=0 flags=0 format=0x25 width=4 height=1 depth=1 levels=1 samples=0 stride=0`，GL name 与 packaged data 均为空。它对应当前未准入的一维纹理，不能沿用其他版本的3D参数解释。随后仍为两次成功初始化及另一零 serial 对象 WriteTexels 停止；该查询与停止点的具体因果仍待适配后复测。binary SHA256 为 `036c7802bc262dd953106d7520d06d2b9623232d64fe74a2ffae4b1d76488a18`，参数报告位于 `outputs/botw-host39-mac-6da5352/zero-storage-query-evidence.json`。该纯诊断版尚未重装手机，手机已验证版本仍为275；目前没有可玩验收。

`6da5352` 的 [iOS 通用宿主构建与合成测试](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38840038047) 和 [Linux 检查](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38840038174) 均成功，精确 head 已核验；未将这些通用检查作为原游戏画面或可玩验收。

一维纹理适配沿用已引入的完整上游 layout、texwrite、deswizzle 与 flush 模块，base-v0 独立静态核验本例 Size/Align 均为512。GPU 首域限 ordinary pool、未压缩普通颜色、flags=0、raw samples=0、h=d=1 与单级 mip，实际创建 MTLTextureType1D；不借2D视图代换。一维元数据仍可计算合法 mip 链，但 [Apple MSL 规范](https://developer.apple.com/metal/Metal-Shading-Language-Specification.pdf) §6.13.1 限制 native 1D 不支持 mip，因此多级 GPU Init 在 Metal 物化/put 前拒绝。virtual pool、depth、压缩、linear、跨维视图、格式重解释、RT 与 Window 不因本批而开放；既有回收、authority、错误回调和对象 get 规则保持。当前诊断未记录 packaged layout 字段，不能声称实际字段为0；保留的合法 H0..5、W=D=0 范围有原静态尺寸/Write等价证明，packagedData 非空保持拒绝且不读取所指内容。 该组相关三模块 **65 项全部通过，20.426 秒，零跳过**；真实 Metal 校验开启，包含9个公共读写/边界模式与6个采样/反射/authority模式，宽33的1536字节 allocation 使用独立 GOB 地址与 padding canary 核验。CPU查询先复现0→期望512的行为失败；格式重解释的真实 Metal 拒绝由新增受控 guard 前拒，不借 driver assertion 接受不支持的格式。 独立 CPU 检查16项通过（6.499秒、零跳过、ASan/UBSan），独立 GPU 检查2项方法内15个 native 模式通过（2.656秒、零跳过、全部实际 Metal API Validation Enabled）；六个来源文件摘要稳定，graphics.m 与6da逐字节相同。

提交 `3656212` 的普通 Mac 原游戏首个 fatal 已从 WriteTexels 改为 `nvnTextureCompare` 的另一个零 serial 对象；本生命周期三次初始化全部成功，新增 `target=0 flags=0 format=0x25 samples=0` 的一维对象，serial=0x31，本次没有零大小查询。比较的 raw 与三条成功记录均不同，not_retained 不能作为合法空对象或生命周期错误的直接结论。binary SHA256 为 `6f3ad63e8931ad6315a5f1a03e1bc91be1f66d1bff3ccfa7b67adf55a34d9118`，证据位于 `outputs/botw-host39-mac-3656212/texture-lifecycle-evidence.json` 与 `ordinary-admission-evidence.json`。host_return=1，仍无游戏呈现或可玩验收；后续需核对原 Compare 合同，不将未知对象直接构造为纹理。

`3656212` 固定全套 **899 项，898通过、1个原硬件条件跳过**，196.072秒；632个来源文件前后逐项SHA稳定，source manifest SHA256 为 `e0bd85de433432a6a116fb3fca01c79326733dd1bb443c1e32c527d58702b83c`。该结果仍不替代原游戏或手机版本的实际验收。

`3656212` 的 [iOS 通用宿主构建与合成测试](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38841242056) 与 [Linux 检查](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38841241974) 均成功，精确 head 已核验。该版已保数据更新到当前 iPhone 12 / iOS 18.1 并普通启动 PID1076，executable 与本次安装位置匹配；启动标记27→28，旧日志前缀逐字节保持。手机同样记录三次成功初始化（含一维 serial0x31），无零大小查询，后续首个 fatal 为 Compare 的另一个零 serial 对象，host_return=1，仍无游戏呈现。签名 IPA 19,141,532 字节，SHA256 为 `a93ae33e3c2d2876165518f244fe840cb741fc4dec31f0c0d7a702b242241880`；RomFS 大小/mtime、32字节UID、shader清单和权限分别核验保留，未重传 RomFS。报告位于 `outputs/botw-ios-3656212-iphone12/device-run-report.json` 与 `texture-lifecycle-evidence.json`。累计日志采用分块前缀/完整SHA校验，只将有界的新启动留在内存，既有日志不截断；新报告的 fresh SHA 与 `latest-boot.log` 精确字节一致，旧275报告的 bootstrap 标记视图另保留其一字节换行差异说明。

原版 Compare 的静态实现只按值比较，main 已有先清零0xC0再在赋值前 Compare 的正常占位路径。后续 Compare-only 适配仅对 key0 输入，在正常接口功能内安全读取原实际比较的 +8..97 字段；全部语义空时，双空为 true、空与有效登记纹理为 false。两边均分类，非空未登记、错误类型、未知 serial 与 stale pool 仍拒绝；不发布对象或 serial，不开放读写/Finalize/GPU 生命周期。比较字段的读取不输出内容、不作诊断扫描，未读原未比较的0xC0尾部。有效对象会在键预读后再经过原 get，两次8字节检查的计数已明确测试；双有效的旧比较算法不改。7项新增测试在365基线编译成功后有6项真实行为失败、1项旧有效副本行为通过，修复后相关23项 CPU 全部通过（6.650秒），独立23项同样通过（6.669秒），均用 ASan/UBSan、ARC exceptions 和 fake Metal。独立静态逆剥精确还原365生产源；四个来源SHA稳定。此时原游戏的零 serial 是否满足语义空字段仍待后续普通运行验证，不能仅凭首字断言。

提交 `dff132d` 的普通 Mac 原游戏已不再出现上述 Compare fatal，首个停止点改为尚未实现的 `nvnSamplerBuilderSetLodBias`；本次无零大小查询和对象校验失败上下文。此结果支持该适配已使运行继续，但本次未记录 Compare 的实际分支、对象字段内容或 LOD bias 数值。binary SHA256 为 `7655eb25ad231097e6817d06e69c68f09f6f786a501eaa9d315459d4781a94f0`，报告位于 `outputs/botw-host39-mac-dff132d/ordinary-admission-evidence.json`，host_return=1，仍无游戏呈现或可玩验收。

`dff132d` 固定全套 **906项，905通过、1个原硬件条件跳过**，196.848秒；632个来源文件前后逐项SHA稳定，source manifest SHA256 为 `0731fe0bdb50b78de1e1c798e36663f5c1722ee3c8b3371d5d8458bd95abf373`。检查仍不构成游戏画面、操作、音频或存档验收。

`dff132d` 的 [iOS 通用宿主构建与合成测试](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38842321625) 与 [Linux 检查](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38842321543) 均成功，精确 head 已核验。该版已保数据更新到 iPhone 12 / iOS 18.1 并普通启动 PID1077，executable 匹配本次安装；启动标记28→29、旧日志前缀逐字节保留。手机首个 fatal 同 Mac 为 `nvnSamplerBuilderSetLodBias`，没有 Compare 对象失败或零大小查询行；LOD bias 值仍未记录。签名 IPA 19,142,210 字节，SHA256 为 `fbfb8935d720a33f028810fe343a082666ade7a5098263af2ce5116cf993fff3`。RomFS 大小/mtime、UID、shader清单及权限分别核验保留，未重传 RomFS；累计日志32,018,620字节由分块reader完整核验，旧日志未截断。本次仍 host_return=1，没有游戏呈现或可玩验收。证据位于 `outputs/botw-ios-dff132d-iphone12/device-run-report.json`。

采样器整族薄适配依据固定 base-v0 的 Defaults、参数、Compare 与原编码器静态合同，修正 Builder 字段偏移、默认值和保留区域，实现35个原参数/对象入口。原37入口中的 DebugLabel、DebugID 仍未实现，LodSnap 与 BuilderGetDevice 也未因其他版本而新增。Setter/Getter 保留原浮点与 border 位值；对象保存不可变参数，Compare 保持严格登记对象语义，Finalize 在移除登记前核验可写，未扩空对象规则。LOD clamp 按原编码投影到 [0,15] 的 Q8，原参数仍保留；GPU 各向异性首域仅1/2/4/8/16，border wrap 和非 average reduction 保持拒绝。两套完整上游 sampler 虽保存 bias，但未把它消费到 Metal；当前 iOS18/A14 无对应 sampler-state bias 功能，非零 bias 必须在 Metal 创建与发布前具名拒绝，不能静默置零。仅正负零准入，按位检查避免 FPCR.FZ 把非零 subnormal 当零；实际游戏 bias 数值尚未观测。

最终 freeze3 的相关采样器20项全部通过（5.934秒，含7个真实 Metal 模式），独立公共接口复核3项方法通过（6.777秒，含11次实际 Metal validation 运行与1个 CPU 模式），独立最小 CPU 增量3项通过（4.677秒）；非零 subnormal 的同一旧源码真实失败探针，在修复后确认拒绝、Metal 创建数不增加且正常回调。较早83项相关回归绑定 freeze1，不作为 freeze3 全套结果；后续固定提交需另跑全套。8个来源 SHA 在两次独审前后稳定，manifest SHA256 为 `47e93436d967e21f1fd0885fdda056f7a504429b713bc72cdce6ebc2719a90b6`。新增 sampler include 后生产 native 文件95个，既有100文件上限与来源 schema 不改；iOS 工作流对新测试的 push/pull_request 均有精确触发路径。以上是合成接口验证，未替代原游戏或手机可玩验收。

提交 `ea7fc87` 的普通 Mac 原游戏越过原 `nvnSamplerBuilderSetLodBias` 停止点，首个 fatal 改为 `nvnSamplerInitialize` 内的 `texture metadata overlaps live NVN object`。binary SHA256 为 `1d9d8870a7400caccffeec70f3c0f51a7d941c58b94c76dbed57ee8a9d3fff8e`，证据位于 `outputs/botw-host39-mac-ea7fc87/ordinary-admission-evidence.json`。没有 bias 具名拒绝行，但本次未输出实际参数；不能把未记录的位值或重叠对象身份写成实际观测。原 Init 不读取 prior header、Finalize 为直接返回，main 另有按值缓存路径，后续需核对宿主同址替换与旧引用保护。host_return=1，仍无游戏呈现或可玩验收。

`ea7fc87` 固定全套 **926项，925通过、1个原硬件条件跳过**，207.655秒；634个来源文件前后逐项SHA稳定，source manifest SHA256 为 `9cb1befce58d3e9ba93f1f2d21ecc180f065c6235c4a81c52f3c5fa9dd86a9f4`。全套包含最终 sampler 源码；测试通过仍不构成游戏或手机版本可玩验收。

后续固定 main/SDK 静态链确认：临时 sampler 与 Builder 各占 main 栈布局0x60，游戏将 sampler 按值复制进缓存后立即 Finalize 源对象；原 Finalize 是 RET。宿主旧实现删除 serial 快照会破坏这条正常路径，旧测试中“Finalize 后查询失效”和“只读 Finalize 必须拒绝”的假设因此更正。sampler-only 修复把不可变值快照和原址 storage span 分开：Init 只允许同址、同完整 host 预算的 BWSampler 替换，新对象发布成功后才退旧 span；Finalize 不改 raw 或删除快照，只退精确本址及同对象身份的 primary span，Finalize 副本不会退另地址的 span。旧快照及 Metal state 保持到 graphics_shutdown，这是当前显式资源成本。跨类、部分重叠、队列/录制区与忙回调仍在创建前拒绝；nil 或异常保留 raw、serial、objects 和 spans。没有扩大原对象预算、全局 get/put 或新增 guest 内容读取。实际重叠对象类型及地址尚未观测，静态与夹具只能证明该兼容缺口存在，不能替代现场归因。

本次25项 CPU 回归在 ea 基线上编译成功后有8项真实失败，修复后25项全部通过（4.663秒，ASan/UBSan、fake Metal）；最窄真实 GPU 链通过（0.897秒），覆盖按值副本、源 Finalize、同槽重用、从旧副本注册 descriptor，以及副本 Finalize 后仍采到原 mip2。独立 CPU 8项通过（4.292秒）；独立 public GPU 两项方法/两个模式通过（1.783秒，均实际 Metal validation banner，无 GPU sanitizer），包含新 value-copy 链和旧 retained-snapshot。双独审 CLOSED，四个修改来源 SHA 前后稳定，manifest SHA256 为 `cf37b5f0a8579a4ffb26d1b3e4eb1b5b41793b1d44ec1f8ca885cbddcdb1152b`，native95与schema100保持。固定提交后的原游戏验证另行记录，不把合成像素当成游戏画面。

提交 `ca3cea3` 的普通 Mac 原游戏仍停在 `nvnSamplerInitialize`。该版既有 guard 的有限 host-owned 元数据明确冲突为 `BWTexture`、span192字节、`exact_base=false`，因此不能把本次现场归因为同址 sampler 重建；未读取冲突对象的客体内容。binary SHA256 为 `a83ac4d5960b42442124c76c8fe34170cc7a2c9b052af1387f0201bd74ac09f7`，证据位于 `outputs/botw-host39-mac-ca3cea3/ordinary-admission-evidence.json`。下一步需核对原纹理复制与临时存储复用，保持其他重叠拒绝，不靠全局忽略 span 继续。host_return=1，仍无游戏呈现或可玩验收。

`ea7fc87` 的 [iOS 通用宿主构建与合成测试](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38843884880) 与 [Linux 检查](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38843884894) 均成功，精确 head 已核验。该版已保数据更新到 iPhone 12 / iOS18.1 并普通启动 PID1083，executable 匹配本次安装；启动标记29→30，旧日志前缀逐字节保持。首个 fatal 同该版 Mac 为 SamplerInitialize 的通用重叠检查，该版本没有记录实际冲突类型或 bias bits，不能将后续 ca 的 Mac 元数据当成此次手机观测。签名 IPA 19,146,084字节，SHA256 为 `e77fc2b7e528ef3cb2116a205a4d62ed9d49cf34c2aaaa8f60bcd1fe42429305`；RomFS 大小/mtime、UID、shader清单和权限均核验保留，未重传 RomFS。累计日志34,189,788字节使用流式校验，不截断原日志；仍 host_return=1，无游戏呈现或可玩验收，报告位于 `outputs/botw-ios-ea7fc87-iphone12/device-run-report.json`。

`ca3cea3` 固定全套 **933项，932通过、1个原硬件条件跳过**，208.894秒；634个来源文件前后逐项SHA稳定，source manifest SHA256 为 `19594495c9fe6efff4d7002a34c0220ea817e85acf3ffd6e6b179034fcdc7038`。手机此时已验证版本仍为ea，后续构建和安装结果另记。

`ca3cea3` 的 [iOS 通用宿主构建与合成测试](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38844647775) 与 [Linux 检查](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38844647763) 均成功，精确 head 已核验。该版已保数据更新到 iPhone12 / iOS18.1 并普通启动 PID1085，executable 匹配本次安装；启动标记30→31，旧日志逐字节保持。手机同样具名记录 SamplerInitialize 与 BWTexture、span192、非同址的冲突；未读取现场对象内容、地址或调用者栈，未记录 bias bits。签名 IPA 19,147,027字节，SHA256 为 `8e4f9e59fd261d84fe56505e6a0b1a2ed66a168eb3dc6effb20b717602aff94e`。RomFS 大小/mtime、UID、shader清单和权限均保留，未重传资源；累计日志36,361,246字节采用完整流式前缀及 SHA 校验。仍 host_return=1，无游戏呈现或可玩验收，证据位于 `outputs/botw-ios-ca3cea3-iphone12/device-run-report.json`。

固定 main 的纹理转移路径另已静态闭合：`A37700` 在 `A37824` 通过普通 memcpy PLT 复制0xC0，返回点 `A37828`，w23 bit0保存 ownership；成功调用者跳过临时 TextureFinalize 并退栈。原 TextureFinalize 可调用 NvRmGpuMappingClose，不能改为空函数。两固定上游没有可直接接入并保当前严格 raw-span 的迁移模块，因此本批采用已版本限定的薄适配：独立 ARM64 入口从正常 LR/w23 传参，运行时核对实际加载的完整0x140字节 main 上下文、PLT、原 SDK memcpy 定义及其6处既有 x18 thunk 摘要。仅精确返回点、0xC0长度及 ownership 匹配才进入迁移协议，其他调用仍执行原 SDK memcpy；未改 Guest 重写、报告或框架。

图形 host-only preflight/commit/abort 不读 guest 字节或查询 VM，不注册任意副本，也不放宽全局 get、对象写入或 Finalize。64个有界 pending 记录强持原 BWTexture，并绑定生命周期、线程、原 primary span epoch 与目的范围；原 SDK copy 在图形锁外执行，正常返回后才重核并提交 span 迁移。纹理 identity、pool generation、descriptor/GPU 引用及 private authority 不变，目的地仍负责真实 Finalize。错误不迁移登记、不释放纹理资源；copy 字节本身保持原非原子合同，不回滚。匹配复制通过既有局部故障恢复先 abort token 再停止；pending 时 checked shutdown 保留资源，guest workers 未停止时原 SDK 映射不解除。旧无图形 fixture 的不可用 provider 标记明确返回0，真实强定义覆盖；匹配调用缺任一真实 provider 即拒绝，不能用标记制造成功。

本批运行时先复现编译成功后的真实行为失败，修复后13项 CPU 测试通过（2.508秒），包括自有 ARM64 的 LR/ownership 与 ABI、原函数旁路、优化后的缺 provider 链接、复制故障 abort、单次 token、pending worker 映射保留和最多4条标量成功记录。图形先复现8项真实行为失败，最终9项 CPU 与2项 GPU 方法全部通过（合计6.282秒）；4个实际 Metal validation 模式验证复制前登记的 array descriptor 多层 mip、源槽 sampler 重用、private GPU 内容保持和 live/retired canonical alias 拒绝。旧存储/descriptor 的3项定点回归通过（10.268秒）；两个类名拼错的初始命令另存，不算行为 RED。两个新增生产 include/header 使联合 native97，既有100上限和来源 schema 不变；新增测试的两个 CI 事件路径已核对。冻结来源与独审结果须分别核验，以上合成结果不替代原游戏或手机可玩验收。

独立运行时13项 CPU 通过（2.425秒，O2、无 sanitizer），owner 另以 ASan/UBSan 验证13个模式（编译及运行合计5.879秒），均完整退出；生产 C 摘要函数对固定本地 v4 磁盘映像的4段上下文核验成功，两输入 SHA 前后稳定，此项不是实际游戏执行。独立图形11项通过（6.430秒、零跳过，CPU9 ASan/UBSan与GPU2方法/4模式，均实际 Metal validation）；两组独审 CLOSED，11个来源 SHA 稳定，联合 native97。原游戏实际加载及手机运行结果另记。

提交 `bfd9c82` 的普通 Mac 原游戏实际记录一次 `texture_value_storage_transferred=true bytes=192 index=1`，旧重叠 fatal 消失。新的一条正常零大小查询为 `target=3 flags=0 format=0x25 width=4 height=1 depth=1 levels=1 samples=0 stride=0`、GL name 与 packaged data 均为空；target3 是一维数组，非3D。随后首个 fatal 为 `nvnDeviceGetTextureHandle` 的 descriptor index 检查；尚未记录实际索引，未证明零大小查询与该停止点的具体因果。binary SHA256 为 `a1a69620e465847f74dc4847b7ad2cf11a5398675bf836510b93355a2f2bd9ca`，证据位于 `outputs/botw-host39-mac-bfd9c82/transfer-and-storage-evidence.json`。仍 host_return=1，无游戏呈现或可玩验收，后续按原一维数组完整布局合同核验，不放宽索引 guard。

`bfd9c82` 固定全套 **957项，956通过、1个原硬件条件跳过**，215.633秒；639个来源文件前后逐项SHA稳定，source manifest SHA256 为 `46bb1264a9f607ebc178ce5afc9446018d4b999992e42cba87be2ee8f5aa88c5`。手机此时已验证版本仍为ca，后续通用构建和安装结果另记。

`bfd9c82` 的 [iOS 通用宿主构建与合成测试](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38846560670) 与 [Linux 检查](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38846560675) 均成功，精确 head 已核验。native97 的同版通用宿主已本机组装、签名并保数据更新到 iPhone12 / iOS18.1，普通启动 PID1093，executable 匹配本次安装；启动标记31→32，旧日志逐字节保持。手机实际记录一次 `texture_value_storage_transferred=true bytes=192 index=1`，并同 Mac 记录 target3 的单层一维数组零大小查询；首个 fatal 为 DeviceGetTextureHandle 的 descriptor index，未记录实际索引，不推断具体因果。签名 IPA 19,153,469字节，SHA256 为 `9901ffecaf71b2723c89c8aeb671210708fed49d4e33b793314c06bb850a2091`。RomFS 大小/mtime、UID、shader清单和权限均核验保留，未重传资源；累计日志38,532,611字节经流式完整校验，既有日志未截断。仍 host_return=1，无游戏呈现或可玩验收，报告位于 `outputs/botw-ios-bfd9c82-iphone12/device-run-report.json` 和 `policy-and-transfer-evidence.json`。

一维数组适配继续复用完整 layout、texwrite、deswizzle 与 flush 模块：原 base-v0 与两固定上游均以 Builder.height 表示层数、空间高度固定1，每层独立 GOB 与512字节对齐；实际4×1层/单级 format25 的 Size/Align 均512。CPU 元数据在明确支持范围内可计算合法 mip 与多层，GPU 首域仅 ordinary、普通未压缩精确颜色格式、flags0、raw samples0、height=depth=levels1，真实创建 `MTLTextureType1DArray`、arrayLength1，不偷换其他维数。[Apple MSL 规范](https://developer.apple.com/metal/Metal-Shading-Language-Specification.pdf) §2.9 明确 iOS 普通1DArray语言域，§6.13.2 单列其无 mip；这不替代 A14 实际创建和像素验收。GPU view 只同 target/format、level0/layer0；virtual、多层、多 mip、RT/present、跨维和格式重解释保持前拒。packaged 参数仅旧 W=D0/H0..5 的原有效 H0 交集，未把未记录字段断言为0。

base-v0 的原 Write/Read 与 vendor1.9 的多层坐标语义尚未证明相同，因此仅规范单层 y=z0、height/depth各0..1；正常有效对象、View 和 region 检查后，任一 extent0不搬 payload。特别 height1/depth0由 host 显式判空，不能套 vendor 的 height 判层；坏对象、负数、多层不因空区域被放行。已保存初始合法空域被 guard 拒绝的真实失败，再修复并完成12项相关回归（13.271秒），覆盖新CPU查询/初始化、旧1D、公开上传/采样与 handle边界。独立 GPU 两项方法11个实际模式通过（2.410秒，均实际 Metal validation、无 GPU sanitizer），包含33宽跨GOB、类型反射、同维 view、copied cache 和 authority。DeviceGetTextureHandle 仅在原失败路径追加正常参数的十进制/十六进制索引，原 guard 和20/12位 handle算法不改。8个来源已冻结，native仍97；实际游戏后续索引与手机GPU行为另记，不把合成结果当可玩验收。

一维数组独立 CPU3项通过（4.191秒，ASan/UBSan、fake Metal、无GPU），并核对空域在 snapshot/prepare/drain/encoder 前返回；双独审 CLOSED，8个来源 SHA 前后精确一致。冻结文件 SHA256 为 `6769484fd60f7676181a0c28ac4d1242bca0551648c2b3a02cb872b809ad0d74`，其中 paths payload 摘要为 `9f7a0d3ccea3432559af3489cc79e8531617e103b03b9497ebdd92b91bb1b392`，两者口径不同。

提交 `28c0b30` 的普通 Mac 原游戏不再输出前版 target3 的零大小查询，实际先完成一次192字节纹理存储迁移，随后首个错误为 `texture move preflight: texture storage transfer destination overlaps live NVN storage`。本版尚未执行到 DeviceGetTextureHandle 的新索引诊断；不能推断实际冲突对象类别、精确重叠关系或旧对象是否仍有效。binary SHA256 为 `79317dc89f43560829ce3ca1ff223fd7f7b69080c81084bfba618f5c380ab9e9`，完整进程已退出，host_return=1，仍无游戏画面或可玩验收。证据位于 `outputs/botw-host39-mac-28c0b30/ordinary-admission-evidence.json` 和 `transfer-and-storage-evidence.json`。后续先用有限 host 登记元数据定位，保持重叠门禁，不读取旧 guest 对象来判断存活。

`28c0b30` 固定全套 **962项，961通过、1个原硬件条件跳过**，217.905秒；639个来源文件前后逐项SHA稳定，source manifest SHA256 为 `a7d15c5b43b18bb237f3978ad1718e5ca3f1d2f0a4ad5f3112c220cffdd01b73`。[iOS 通用宿主构建与合成测试](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38847830436) 与 [Linux 检查](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38847830442) 均成功，精确 head 已核验；手机更新准备与后续原游戏结果另记。

目标冲突诊断保持原谓词、返回0、登记及 token 生命周期，仅在已命中的 span 上追加有界 host class、长度、exact-base/exact-span、BWTexture primary/epoch 与相对偏移；没有读取 guest raw/header/serial、扫描 VM 或新增 validator 调用。新夹具先在编译成功的旧实现上真实失败，最终10项 CPU 回归通过（4.681秒，ASan/UBSan、fake Metal）；两个独立新增 CPU 用例分别通过（4.456秒、4.257秒），零跳过、无GPU，三来源 SHA 前后稳定。逆剥格式化增量后 production 文件精确还原旧版本。384字节局部错误与512字节 runtime buffer 的容量已核验，12字节 caller buffer 的 NUL/越界 canary 通过；此诊断不构成目的存储已失效或可以覆盖的证据。freeze 文件 SHA256 为 `1389e0204c5f6343b2d7f3b76c561bdcace8d5321a1d95cc1631b48fc9943482`，native仍97。

提交 `190d33c` 普通 Mac 完整退出后，首次冲突的已登记 host 元数据明确为 `BWTexture span_bytes=192 exact_base=true exact_span=true existing_primary=true existing_move_epoch=1 relative_offset=+0`，其前有一次成功迁移。该信息证明完整的、此前已迁移的宿主 primary 槽位冲突，不证明旧资源已死亡。固定 main 的旧共享引用/非 owner 分支会在不 Finalize 旧资源时给目的槽赋新值，后续适配需只交接槽位登记，保留旧 value、descriptor 和 GPU authority；其他类、部分覆盖及未迁移目的仍拒绝。本版 binary SHA256 为 `9a532333ef8e8b4e3c8580770c4e537adc03dad28834608da4ea8f0a1058a5b9`，证据位于 `outputs/botw-host39-mac-190d33c/destination-conflict-evidence.json`。仍 host_return=1，无游戏画面或可玩验收，未读取旧客体字节。

`28c0b30` 的官方通用工件已核验并本机加入固定私有输入，native97来源核对后签名，IPA 19,153,725字节，SHA256 为 `45b03146cbfa5af247d16a7cee061aed65c0d104fb866029105f61c98465a1df`。主动 `lockState` 查询确认同一 iPhone12 的 passcodeRequired=false 后，已保数据安装并普通启动 PID1097，executable 匹配本次安装，启动标记32→33，累计日志40,704,362字节完整流式校验，旧前缀保留。手机实际完成一次纹理迁移，不再出现前版一维数组零大小行，首个 host error 同本版 Mac 为通用目的 storage 重叠；本版不含190d的细分字段，不能把后者 Mac 元数据写成此手机观测。RomFS 大小/mtime、UID、shader清单及权限均保留，未重传资源。仍 host_return=1，无真实游戏画面、操作、声音或存档验收；不能仅凭执行越过该查询而断言 A14 已完成1DArray真实采样。证据位于 `outputs/botw-ios-28c0b30-iphone12/device-run-report.json` 和 `policy-and-transfer-evidence.json`。

完整目标槽替换继续受已审核 main profile、正常返回点、C0长度与 ownership bit0 门禁约束，不改 runtime。图形层只允许唯一、与源不同、此前迁移过且 epoch>0 的 exact192 owned BWTexture primary 目的；未迁移、部分覆盖、其他类、selected/错误identity和多重冲突仍拒绝。pending record 强持旧目的 object/key/span/epoch，commit重新核对完整快照及双方epoch，旧目的若正是另一pending记录的source则拒绝。所有可抛的 span 创建、表克隆和两次编辑在私有clone内完成，成功后一次发布，再推进双方epoch；nil/异常/abort不改变原登记。只移交primary span，旧 objects、descriptor/recorded引用、MTL资源、mapping、texown/flush及private authority均保留；旧alias按原serial Finalize，不能删除新key的span。无原Finalize的旧值和资源可能积累至shutdown，64个pending上限不构成资源总量上限。字节copy保持原非原子合同，不回滚。

新替换夹具在旧实现编译成功后3项真实失败，最终18项通过（6.791秒，CPU16 ASan/UBSan、GPU2方法6个实际Metal validation模式，无GPU sanitizer）。包括clone的真实copy/remove/set异常和nil、源/目的epoch变化、pending、旧alias Finalize、旧recorded draw及descriptor继续采旧图案、新descriptor采不同backing的新图案，以及两份private纹理像素和live/retired别名保护。生产来源仍97，三文件冻结 SHA256 为 `c46427b7490cb830e66baf6e2aadb472f5a2be744abdbd56a4881d0662573477`；独审及固定提交实际运行结果另记，不把夹具像素当游戏画面。

该替换 freeze 的独立CPU16项通过（4.607秒，ASan/UBSan、fake Metal、无GPU），独立GPU2方法6个真实模式通过（2.266秒、零跳过、无GPU sanitizer）。双方审查 CLOSED，三来源 SHA 前后精确一致，GPU已释放供普通原游戏验证；没有增加生产文件或修改通用对象门禁。

替换提交 `dc9cfa6` 已推送，普通 Mac 实际输出成功迁移index1至4，旧目的槽冲突消失；trace上限4，不能据此断言总迁移次数恰为4。新正常零大小查询为 `target=2 flags=0 format=0x25 width=4 height=4 depth=1 levels=1 samples=0 stride=0`、GL name及packaged data均空；target2是真正3D。随后首个fatal为DeviceGetTextureHandle，现场参数已明确 `texture=-1(0xffffffff) sampler=257(0x101)`，本桥既有索引guard保持，未把两个事件的具体因果写成已证明。binary SHA256 为 `a46956c60e72ab29c22368e9a416d94fcd0554c4b28a3118a5c8dd0a51e6bddd`，完整退出 host_return=1，仍无游戏画面或可玩验收；证据位于 `outputs/botw-host39-mac-dc9cfa6/transfer-and-storage-evidence.json`。后续按原3D布局、上传和真实Metal维度合同继续薄适配。

`dc9cfa6` 固定全套 **971项，970通过、1个原硬件条件跳过**，221.106秒；639个来源文件前后逐项SHA稳定，manifest SHA256 为 `42b0f93e318db334e6d608598f34e2e809395ac31ba841674d91e8869e3c9fbf`。[iOS 通用宿主构建与合成测试](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38849566097) 与 [Linux 检查](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38849565933) 均成功，精确head已核验。该版已保数据更新iPhone12/iOS18.1并普通启动PID1104，executable匹配本次安装；启动标记33→34，累计日志42,876,686字节完整流式核验，旧前缀保留。手机同样迁移trace1..4，随后出现3D零大小查询及texture=-1/sampler257；不推总迁移次数或具体因果。IPA 19,155,092字节，SHA256 为 `433b4a0e899c70952f1c525a66ba5dd6e436a4af33e8e970c8fadb54aeca099d`。RomFS大小/mtime、UID、shader清单及权限均保留，未重传资源；仍host_return=1，无可玩验收。证据位于 `outputs/botw-ios-dc9cfa6-iphone12/device-run-report.json` 和 `policy-and-transfer-evidence.json`。

3D适配复用完整vendor的体积布局、texwrite、deswizzle和flush模块；原SDK与自有81组CPU布局核验确认普通4×4×1单mip的Size/Align均512，mip的宽高深共同缩小、不是数组乘层。metadata三轴≤2048、合法mips；原packaged H能改变3D布局而vendor未消费该覆盖，因此首域packaged W/H/D均0。GPU只ordinary精确未压缩颜色、flags0/raw samples0、depth1/level1，真正创建 `MTLTextureType3D`、float3采样及同维精确view；virtual、多depth/mip、RT/Window、跨维/格式重解释保持前拒。零extent先验证对象、View、坐标与stride，不搬payload。GetStorageSize的既有cap4零结果诊断仅追加正常快照已有的layout-set及12位layout标量，不新增guest读取、provider调用或VM扫描；现有handle逻辑未改。

Size/Align/Init三项先真实失败，新增trace字段也有独立行为RED。相关storage+family组首次72项中71通过，唯一旧generic-target负例因3D已进入新正域而失败；将该负例迁至仍不支持的Cube后，1方法4子例定点通过（1.026秒），未宣称重跑72全绿。另增正常SetLevels/Layers(-1)经原无符号字段截断后，空/非空Read/Write八次受控拒，1项CPU通过（4.297秒）；没有伪称生产负count漏洞或行为RED。独立CPU先5项通过（4.416秒），最终增量1项8次public拒通过（4.240秒）；独立GPU2方法11个实际Metal validation模式通过（2.477秒、零跳过、无GPU sanitizer），覆盖跨GOB非零图案、padding、empty、swizzle、真3D反射和live/retired authority。最终7文件与GPU时生产/依赖精确相同，双独审CLOSED；freeze文件SHA256为 `0603406898785dfc2d17233d39b145c6e3e27169b7a4b43fc2a26990a3c3eea5`，native97/100保持。固定原游戏及后续整套结果另记。

3D提交 `026265b` 已推送，普通Mac不再输出3D零大小查询，下一条为 `target=8 flags=0 format=0x25 width=4 height=4 depth=6 levels=1 samples=0 stride=0` 的Cube查询，packaged layout-set=false/bits0、GL及packaged data均空。随后仍由本桥GetTextureHandle额外准入拒绝texture=-1/sampler257。binary SHA256 为 `7c4f2fb92f14a74e570d1b94db7ce58df92b2b39478132fa52be0a94e6a1050f`，完整退出host_return=1，无游戏画面；证据位于 `outputs/botw-host39-mac-026265b/transfer-and-storage-evidence.json`。本中间版尚未本机固定全套或安装手机，手机仍为dc；最终连贯候选另跑完整流水线。

独立原合同审计进一步确认GetTextureHandle为无device读取/无范围验证的纯编码叶函数，`low32 = uint32(texture) | (uint32(sampler)<<20)` 后符号扩展并置bit32，(-1,257)编码UINT64_MAX。当前编码本身已匹配，额外构造阶段准入与原SDK不同；原Bind只消费低32，现所有已支持消费者也按低20/高12解析，并在使用前核descriptor bounds、注册语义、device/map/witness与authority。原main存在texture=-1构造/clear来源及不筛texture的调用路径，但不能据此推断实际caller或Cube查询与该值的因果。下一批先恢复纯编码合同，保全部use-time验证、不采用上游dummy fallback。只读证据位于 `outputs/botw-texture-handle-audit/kernel-static-dc9/report.zh-CN.md`，未读取运行客体内容或执行原字节。

组合句柄修复仅将GetTextureHandle额外device/range那一行替换为忽略device；低32 OR、可移植符号扩展、bit32及所有已支持consumer逐字节不变。整数输入保持完整32位与溢出碰撞，UINT64_MAX仍按低20/12解析为fffff/fff；实际槽位注册、bounds、pool/device/map/witness、shader反射及authority决定使用是否有效，没有新增sentinel特殊分支或dummy资源。两个CPU方法在旧源码编译成功后真实失败，修复后289个独立任意精度golden及五种device参数（含NULL、自有PROT_NONE和错误类型）通过，validator/VM/callback为0，registry/serial/lastError与无Metal初始化状态保持。

最终定点5方法全部通过（8.190秒、零跳过，CPU2 ASan/UBSan，新增真实GPU4模式、旧公开codec和vertex/fragment/upperword18模式）。正常及真正注册最大槽的UINT64_MAX均能绘制；受控小pool的UMax、未绑定pool和未注册槽，在同batch前置copy/counter之后的draw预检中拒绝，真实Finish及有界all-inflight drain后arena、copy/counter canary与原像素均保持。GPU使用Metal validation、无GPU sanitizer。独立CPU2项通过（3.968秒），生产逆剥唯一替换行即精确还原026前的graphics SHA，其他consumer依赖保持；四文件freeze SHA256为 `9c8d72438f8c2b96060e3a3c364c37305d63339fc2db794f25d1acf22318e620`，native97/100不变。独立GPU与实际普通游戏结果另记，不把编码或合成像素当可玩验收。

该codec的独立GPU新方法4模式全部通过（1.349秒、零跳过），实际Metal validation及counter capability查询完整结束，arena/copy/counter/RT不发布的负例已复核。四SHA及consumer依赖前后稳定，双独审CLOSED，GPU已释放供固定提交原游戏；没有为UINT64_MAX提供特殊成功或特殊拒绝。

codec提交 `b41cf07` 已推送并普通Mac完整运行，原句柄构造阶段错误消失。Cube零大小查询实际出现三次，均为target8/4×4×6/单mip/flags0/raw0/layoutbits0；它们没有在此阶段阻断程序，不等于Cube资源已可用。新首fatal为 `nvnBufferBuilderSetDevice: buffer storage overlaps live NVN object`，实际冲突类别/范围尚未记录，不推断原对象死亡或实际写足迹相交。binary SHA256为 `2eb971a8d17f8bff9362f29aca42eb7e43782368586f21f6a6ba07a30fea679e`，完整退出host_return=1，无游戏画面；证据位于 `outputs/botw-host39-mac-b41cf07/transfer-and-storage-evidence.json`。该合并候选固定全套981项完整结束：980通过、1项既有跳过，230.304秒；641项tracked源码身份及前后快照稳定，iOS/Linux精确head CI均成功。该候选已保数据更新当前iPhone12并普通启动，PID1120；首份采样发生在加载中，保留为中间证据，后续独立observation-2取得完整host_return=1及13个guest线程，手机同样越过GetHandle并停在BufferBuilder storage overlap。累计boot从42,876,686增至45,054,162字节，前缀逐字节保留且仅新增一次bootstrap；UID、14,420,017,152字节RomFS大小/mtime、shader清单及权限均保。IPA SHA256 `2aa09f4e8a592d02113266cae5ea796e52adb564883fa8d8170a3d0c7371d546`，尚无真实画面。最终设备证据位于 `outputs/botw-ios-b41cf07-iphone12/observation-2/device-run-report.json`。

后继缓冲构建器调查先保现guard，仅在原命中分支追加有限host class/span/exact/relative及Texture primary/epoch，不新增guest读取、VM扫描或旧raw存活判断。该纯诊断已完成两个CPU方法/10场景：owner 4.459秒、独审4.168秒，ASan/UBSan及ARC exceptions启用；PROT_NONE候选早拒、非重叠首8字节正常写入、原validator/回调/registry/canary/锁恢复均保留。三SHA稳定，生产逆剥精确恢复b41；另一独审静态核正文上界314字节小于384预算，双CLOSED，无GPU。证据位于 `outputs/botw-buffer-storage-diagnostic-audit/`。原BufferBuilderSetDevice只写8字节，Defaults/SetStorage写+8..31；现桥却对opaque64字节统一判span交叠。诊断提交 `320a0fc` 已推送并普通Mac完整退出：实际BWTexture192字节、primary=true、move epoch1，Builder相对offset+48，因此SetDevice实际写[48,56)确实命中旧记录范围，padding-only解释排除；不能据此认旧对象已死。binary SHA256 `0dc00423ce55e7b0cb09ac3127047088e8476174729cd7456e5aaaf20238a2f8`，证据 `outputs/botw-host39-mac-320a0fc/buffer-conflict-evidence.json`。后续须分开POD存储、primary记录与资源快照寿命；不能把永久rawspan表当guest内存sandbox，也不能据此放宽Queue/CommandBuffer、arena/busy、权限、authority或descriptor witness。


BufferBuilder后继实现使用统一 `metadata_write.inc`，仍复用完整vendor setter，在独立宿主临时值上生成8/24字节输出；SetDevice只存pointer，Initialize继续严格消费provider。实际写只退已审moved BWTexture primary claim，不删value/resource，也不改全局get；其它class/selected/unmoved/错误identity/epoch耗尽继续拒绝，不用+48或调用地址作特判。queue/recording/callback、pending transfer全部source/destination范围、logical/actual权限与dirty准备保留。所有分配和私有span clone/remove在输出前完成，受保护固定写成功后单发布span并递增epoch；不宣称dirty准备副作用或并发字节写可回滚。旧copy/descriptor/recorded资源和private authority须独立验证，原对象死亡不作推断。

该实现最终owner freeze2八路径SHA256 `a7f4b013328a02b2216092343f22980139383b6b68652fe73520b879eafe6c9d`，native98/100且schema不改；第八路径只澄清transfer顶部注释。相关28CPU全部通过13.913秒，新10方法及既有诊断2/transfer16均完整结束；新增精确原字段、pointer-only setter/初始化消费拒绝、真实mmap RW/RO/NONE跨页、NULL/overflow、缺失/错误claim、clone nil/真实异常、pending两端、arena/recording/callback和旧值保留边界。独立CPU12/12通过9.352秒；新增两GPU方法/四模式在旧生产真RED后，以冻结实现完整GREEN1.944秒并实际启用MetalValidation，旧descriptor/recorded与private authority保持。旧Buffer夹具permission oracle因Defaults改为真实+8/24写范围而迁移deny地址，GetSize负例恢复整读地址，保原FAIL且不为旧预期修改生产。最终freeze3九路径SHA256 `128605449a74f889d4d081eec048f55dcbb5974d7f7275c84ec371e3664b3fd3`，前八SHA不变；旧probe定点owner1/1通过0.877秒、独立1/1通过0.867秒，编译/运行均exit0且启用MTLValidation，双独审CLOSED/GPU_RELEASED。提交 `cae045f` 已推送，binary `bc995ad14674a0cc03600ddf20ac13b8ae47e066445db8db46abb869a75154a6` 及200私有输入核后普通Mac完整退出host_return=1；旧构建器fatal不再出现，新停止点是TextureInitialize generic拒绝，实际Init参数尚未知，不能把此前3次Cube零查询当因果。证据 `outputs/botw-host39-mac-cae045f/ordinary-admission-evidence.json`。cae固定全套995项中994通过、1既有跳过，254.925秒，643项源码及manifest `7127c3aaed6c4936410161cfa44ac6019b1facbe74eb7b0f72bf03dcbcb48aaa` 稳定。精确head的iOS/Linux CI与官方工件通过后已保数据更新iPhone12/PID1160；首份运行采样仍无host_return/真实帧，UID/RomFS大小mtime/shader权限均保。IPA SHA256 `45c03f4b2a7c9f28d38345871d79d8e95dc8a7e86cec82a991372e194ea1a1b7`；继续独立采样，不能把安装或无旧错误当游戏验收。

后继TextureInitialize通用失败的三路径纯诊断保原错误前缀/条件/短路/textureBytes次数/FALSE/单回调，只追加同一次已验证Builder快照的形状、flags/format/samples/stride、pool是否非空、GL及packaged布尔/layout位值；不解引用pool/pkg指针，不增加成功日志或validator/VM查询。owner4CPU/11mode通过4.739秒、独立同组4.644秒，ASan/UBSan与ARC exceptions启用；真RED只计编译成功后的字段缺失失败，原validator2次误预期3次的夹具错误单独保留。freeze SHA256 `b9cbd0dbd392caa9d87a913b4249550fbe2f32172b34eec9afe92b054e936a15` 三SHA稳，生产单行逆剥精确恢复cae，独立上界407B小于runtime512，双CLOSED且native98/100不变；实际Init参数仍待固定普通运行确认，不把GetStorageSize零查询当Init因果。

cae真机后续独立 `observation-3` 已取得完整host_return=1，13个guest线程，首个图形拒绝同样是TextureInitialize generic。采样前主动核实手机已解锁，PID1160仍在；累计boot47,232,060字节，保留45,054,162字节原前缀且只增加一次bootstrap，新段2,177,898字节SHA256 `77d3c837d1dd5cb5db77a71d7670457ca5d6e26dcaaedd91c9e7476f644fbbf6`。记录SHA256 `a86859fc3806f2d563a2279a972d3385a5cd171c4fafbdefd3ff46e195a83275`；此前两份非终态观察原样保留。无真实游戏帧，不能从进程存活推定继续运行游戏。

纯诊断提交 `6d6caaa` 已推送，但其普通Mac运行只有前台灰窗、约单核100%及非终态日志，没有获得实际Init参数；root保存checkpoint后精确终止自己的测试进程释放GPU，不将该次agent stop记作guest完成。独立固定原SDK、33个counter gate和内核时钟均为19.2MHz，超时换算为纳秒，未见混频；所审锁路径未证实本次死锁，cae约39分钟间隔仍无已证原因。证据在 `outputs/botw-clock-contract-audit/6d6caaa/`。

普通Cube8接入复用完整vendor布局、deswizzle、writer及flush，方形、精确颜色格式、flags0/samples0、无GL/packaged和普通池；rawDepth1..2048元数据保留，内部固定六面。4×4 RGBA8单mip为3072字节、对齐512；GPU真实MTLTypeCube/arrayLength1、六face×全合法mip上传，同维同格式完整六面View可选mip子链。CPU传输限明确单mip，NULL region用该mip宽高构造六面的宿主region后交完整planner；RT/window/virtual/CubeArray9及未证域仍拒。7路径freeze `6059d61d5901577ae3b574f8d4ccfef0d996cac372235b8154f180c794b1fa8a`，native98/100。owner相关CPU36/36通过7.256秒，独立CPU8/8通过5.711秒；真实旧生产RED后独立GPU2方法/7模式通过2.972秒，最终public/旧负域/2DArray独审3方法/15模式通过3.598秒。全部依赖与7SHA稳，实际MetalValidation启用，CPU ASan/UBSan启用，无GPU sanitizer；双CLOSED/GPU_RELEASED。这是已证查询与资源合同修复，未证明旧generic Init与Cube的因果或原游戏画面；新固定版本普通运行与真机验收仍需分别记录。

Cube提交 `7ee9a1acbbf97d9eff4993385fd32a682d9a62ed` 已推送，固定完整1004项中1003通过、1既有跳过，254.899秒；644项tracked来源及manifest `9356b5489d047d7eb7dac6b4910d5ab592b42e01315e91e4c79c1f960fd361e1` 前后稳定。[iOS构建](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38873214176)及[Linux准备检查](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38873214174)均成功，官方无游戏工件核验后本机组装、签名并保数据更新当前iPhone12，PID1175；IPA SHA256 `bbed523ab94e5d8bf2415a4e63d01046c0ef6c60f8c7fb48ef8f41c8e8e45736`。首40秒及独立observation-2约295秒两份均为13线程、无host_return/新fatal/真实帧，完整boot49,408,805字节、原47,232,060字节前缀保留、仅新增一次bootstrap；新增2,176,745字节SHA256 `68253c09caa58865a75b72c6ce7a7df5759a1d696cb0a0f2e6f83f6908cb0418`。后份报告SHA256 `c114b16057762656e0c41e17387c255c45b0221afba5b777f1dbd6116e388231`，资源与身份保留，未证可玩。普通Mac binary `e0c77e8705c26e36a937d31aaf4062c93c2c8e492713149f2e8c7cb78966d209` 与200私有输入核后启动，早期样本尚无完整host_return（后续自然退出见下）；桌面接口报告锁屏而系统IOConsoleLocked=No，屏幕状态尚未成功核验，不将其归因为死锁或游戏完成。

7ee真机observation-3后续完整host_return=1，13线程，无真实游戏帧。实际首图形拒绝为 `nvnTextureInitialize target=2 flags=0x4 format=0x25 width=32 height=32 depth=32 levels=1 samples=0 stride=0 pool_present=true gl_name_nonzero=false packaged_data_nonnull=true packaged_layout_set=false packaged_layout_bits=0x0`。累计boot49,410,624字节，原47,232,060字节前缀保留且只一次新增bootstrap；新增2,178,564字节SHA256 `57efa1ca96d6d9be0327ede23533f5dfd277c2e904ffd04f95fba198615258ee`，报告SHA256 `e2b316db6bf98477dffed90ba32de1206e385f2b84ddd48d93581e7c81c48d82`。约1321秒的进程查询仍见PID1175，只表示该时刻存活，不定位故障发生时刻或长间隔原因。完整3D、flags4及packaged data合同另行静态核验，不通过忽略指针来放行。

7ee普通Mac后续自然退出，完整记录`mac_host_return=1`及`mac_process_exit=1`，13线程，实际首图形拒绝与手机上述3D参数完全相同。binary SHA仍为`e0c77e8705c26e36a937d31aaf4062c93c2c8e492713149f2e8c7cb78966d209`；boot SHA `ee9fc75284ff5506ea1bb332980b9595f895b3c16e6bf25f3cc0da42d81f83e2`、stderr SHA `7cbbae999124db4ce775d99502a856c4e192ac60c6dbe6e03809a9ee0d2114ab`，报告位于`outputs/botw-host39-mac-7ee9a1a/ordinary-admission-evidence.json`。较早的非终态CPU/IO观察保留，只能证明当时约一核CPU活动、归账磁盘IO为0和日志未增长；没有证明具体guest循环、缓存读取量或死锁。

固定原SDK及两完整上游的独立静态核验已闭合本次normal路径：Builder的PackagedData只保存和返回opaque值，Initialize/layout与池回调不消费其内容；不能推广到GL或显式packagedLayout。flags4为IMAGE，32³RGBA8自动布局131072字节/512对齐、深度GOB参数D4，z切片起点不是平面图片跨度。既有完整vendor已含volume deswizzle及局部texwrite；桥将薄接真实Type3D、每mip完整深度、slice0/z-origin及单allocation coherence/authority。IMAGE资源usage与GPU写消费者分别验收，首批只读/采样，typed write/read_write在整批提交前受控拒绝；不把Initialize通过写成已支持image写执行。

宿主锁诊断提交`c31ef9a`已推送，[iOS通用构建](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38879013990)与[Linux准备检查](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38879013995)均为精确head成功。该诊断默认关闭，只有Mac显式`--observe-graphics-lock`时在独立队列取10/30/90/180秒四次宿主原子元数据，不读取运行guest内容、驱动显示或放宽资源。新增producer15项/22native模式及前端6项CPU测试均通过，双独审已闭合；原锁、回调与资源正文逆剥等于baseline。

固定c31 binary SHA `b945f91d756ef7ff945d2ef99de84726c9690d1fa3dd4bbec1130c95b940d69f`、200私有输入核验后的四个实际样本均为MemoryPoolMap，代次50886/282877/423024/628736递增、持锁43/27/546/636微秒，没有同一轮长期持锁的证据。五秒公开进程计数显示约97.166%单核CPU及归账IO0，不能定位guest循环或排除缓存读。四点后仍无host_return/真实帧，已保存日志SHA、LC_UUID和进程身份后明确SIGTERM自有有限诊断并释放GPU；这是agent-stop，未伪作自然完成。收据`outputs/botw-host39-mac-c31ef9a/host-lock-checkpoint.json` SHA `4a4b3195383d732fa30470e550ba073d682f58859054ff4a6ff3c8cf700079c7`。该诊断没有重复装机；手机仍是独立验证的7ee。

完整3D适配现已实现：普通及既有FE虚拟池、真正Type3D多深度/合法mip、完整volume上传、单mip的xyz/strided Read/Write、全深度Invalidate和单allocation证书；原完整vendor算法未改。PackagedData按已证normal合同保留opaque值，初始数据仍取pool backing。GPU view仅同维同格式/slice0，正z GetViewOffset独立作metadata查询；IMAGE真实usage限已证Apple7/36标准颜色格式，所有3D typed write/read_write在共享整批反射门前拒，未宣称支持GPU image写入。

Size/Init/planner及opaque先有4项真实CPU RED，正z查询另有63个独立黄金offset及9种count组合，真实GPU正例在旧源码上先复现Size0。相关旧域89项通过（27.414秒），独立新4个GPU方法17个native模式通过；最后两行容量工厂前移后，相同17模式再次通过（4.997秒、零跳过），每模式开启真实Metal Validation、无GPU sanitizer声明。覆盖32³独立GOB初始数据、z15/16/31、非2幂/多mip、选中view、局部读写与padding、缓存、FE remap和live/retired private authority；有copy/counter真实正控再证明不支持的写整批零前缀。

容量资格在MakeBase的MTL descriptor/factory前以及upload/Read/Write/Invalidate的大snapshot和目标准备前统一核验。自有CPU先证小cap仍调用工厂的真实RED，再证工厂调用0、无opaque读取；public Initialize的错误回调恢复后，raw/registry/serial保持且锁已解开，未观察到正常BOOL返回，不把该恢复写作FALSE。最终7路径freeze2 SHA `f8ab39dc06c796fa9f6552b291a693d44bd54fab010f418bd07482a9b905349a`，native来源仍98/100。相关测试与独审不替代固定全套、iOS工件或原游戏/真机画面；当前手机仍为7ee，新的完整候选按后续流水线验收。

3D提交 `a1c60fa` 已推送，固定本机完整回归1034项中1033通过、1项条件跳过，279.060秒；648项tracked源码身份及前后快照稳定，manifest SHA `f9b115de0093c7e2fce7feb2515e97b84a6154a63759aef28fd95eb67eed7779`。Linux精确head CI成功；iOS运行38882420803的合成测试在Apple Paravirtual虚拟GPU上出现11个新增IMAGE用例失败：4个descriptor正例、3个virtual物化和4个public transfer模式，均缺生产要求的Apple7资格。此前公开probe只有counter字段，没有apple7字段；不能把旧JSON写成已查询Apple7=false。生产资格保持，修正测试以独立公开supportsFamily结果判定，并保留同一method内flags0只读模式及查询错误判失败。该失败候选未组装、签名或安装手机。

同一a1普通Mac binary SHA `52a1dff6153da7da1097801e3747b6cdc85cc7f578d641698fdef726500d1634`，200份私有输入核验后普通启动PID73263，没有启用锁观察选项。检查点仍无host_return、下一fatal或真实画面，不能据此声称已越过3D初始化。为释放GPU完成测试修正，先核磁盘SHA、LC_UUID和进程start身份并保存有界正常日志，再SIGTERM自有运行；launcher完整exit0且进程消失，此为agent-stop，不是guest自然完成。checkpoint SHA `a63ab887c9aa46ebb5e0d788287d34a490371c40337e24148b22f767c3e6998c`，证据位于 `outputs/botw-host39-mac-a1c60fa/`；未调用普通终态记录器。


后继宿主图形锁诊断默认关闭，只有Mac显式 `--observe-graphics-lock` 才在guest前启用，并用独立host队列于约10/30/90/180秒采四次；不驱动显示/VI/输入。记录只含宿主静态入口、线程ID、每次真实acquire的代际与单调时间，callback锁外段明确断开；快照全部payload为lock-free原子、最多两次一致性读取，不取graphics/lifecycle锁、对象表或guest内容，不改原锁/错误回调/资源准入。producer五路径freeze `0e1a004b222317d9d06aba005d50ad743a7333e6232a3e257fe4d1826aaea3da`，15CPU方法/22模式owner4.818秒、最终作者4.480秒、独立4.835秒完整通过，ASan/UBSan及ARC exceptions启用；四源逆剥精确恢复原body。前端6CPU owner12.435秒、独立12.426秒通过，包含默认无queue/read、cap4、非stable不印payload和主线程阻塞时真实GCD首采样；native98/100，无GPU/窗口或实际持锁结论。7ee公开进程五秒两点另核CPU4.930539125秒、归账IO0、boot metadata未变；这些指标不定位guest循环或证明死锁。

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

测试前提修正仅改两个测试文件，生产98项逐字节保持a1。新增7项纯CPU mock先真实RED再全GREEN，两个独立审查均CLOSED；编译、查询、JSON及非bool/缺字段继续失败，不按设备名或生产错误文本判跳过。`VOLUME_FLAGS0_MODES` 同时生成native flag表达式和子case前提，缺Apple7仍调度flags0读取；write-noimage仍受既有counter前提，未宣称云端已执行。最终两测试freeze SHA `820cc2118ab991038dc1f45a5958f22c5dc73fb6635d8d65e9f83225eced74e9`。真实公开查询得到M3 Max/Apple7=true，同4个方法17个native模式完整通过5.020秒、零跳过，实际Metal Validation启用；GPU收据SHA `d96eb08c59d47201a75c534e7b88e5b90cc6012c589409c5887b2d6c61a15034`。后继完整候选仍须固定全套、精确head双CI和工件核验，不把条件跳过写成GPU功能验收。

固定候选 `b7bd5a2` 已push，本机完整1041项/1040通过+1条件跳过，280.042秒；648项tracked源码及前后快照稳定，manifest SHA `e7c853f3a0d24fc20111b5023a0f7056fcd64f0f19d825f89a6998cea65cd576`，完整日志SHA `150e5d8e8271a64bdfd491cb1b898f8b115c18097dc66417d33f10f8c78b81f4`。[iOS官方运行](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38884375964) 与 [Linux检查](https://github.com/shieldgold/d2r-mobile-build/actions/runs/38884375878) 均completed success且精确head匹配。iOS合成992项/45条件跳过，486.408秒；非mock公开probe两次均为Apple Paravirtual/Apple7=false，CPU mock的arbitrary-name真假值独立排除。四个descriptor IMAGE、三个FE IMAGE子case及public IMAGE method依硬件前提跳过，flags0 volume-read实际执行检查通过；原counter组整method跳过，write-noimage本轮未执行。完整CI日志及官方精确head两测试文件另取SHA绑定，审计收据SHA `618af4ea47af45ee78c398c6b340e67fed6553639cc8a55ec543b3316823f205`。

该候选已来源校验、组装并本机签名为19,169,322字节IPA，SHA `33f34a74e2591415c455230d3717c1b809b0c09f91b05b4c36ff6bba4f5b6b9d`；普通更新当前iPhone12、启动PID1181，实际executable路径匹配本次安装，未卸载或移除数据。首份采样37.683秒处进程仍在，13线程，原始heap/root验证及125,829,120字节两段physical pool初始化均成功；无host_return、error、graphics停止行、呈现claim或first-game-frame文件，只作为非终态检查点。boot启动标记37→38，旧49,410,624字节前缀逐字节保留，累计51,587,549字节；fresh2,176,925字节SHA `72e542905815d45a7ba372401e5c39c3a765ebb5de86796a718d192210d12006`。UID逐字节一致、RomFS大小/mtime、37shader清单及0700/0600权限均保，未重传或完整重哈希资源。报告 `outputs/botw-ios-b7bd5a2-iphone12/device-run-report.json` SHA `c7167a625f6c0712db1d3492dd15ad9ecfe80b104766b6a7219f07b5734a6d18`，后续普通观察另建目录，不覆盖本次首样本。

后续b7普通运行新增1,323字节正常日志，独立observation-3完整host_return=1/13线程，无真实frame，首个NVN错误为MemoryPoolInitialize的Metal分配失败。源中有borrowed-segments与snapshot-copy两个同文案分支，均有既有maxBufferLength资格检查；现有日志不足以判断具体分支、请求大小或OOM原因，继续补有限失败参数，不更改factory、权限、alias或资源准入。累计boot51,588,872字节、旧49,410,624字节前缀完整保留且仅一次新增bootstrap；fresh2,178,248字节SHA `f6c4fb34bf925c3185c63fe3de413885c2bc9935e10020139f6ee640be305fee`。报告SHA `c3eaa4a1330ccc8db7ce4d2e802d34fe9063d0b2d477b8aa1aa87dfa5559ce1a`，UID/RomFS大小mtime/shader/权限再次fresh核保全。约1636秒查询仍见PID1181仅表示当时进程存在，不定位错误发生时刻；文件mtime也不替代单条错误时间。

为长时间无新输出准备的iOS显式宿主锁观测已实现并双独审：完整mac consumer迁至现有BOTWEventLog共享文件，独立serialGCD最多10/30/90/180秒四次，不经UI日志NSLock/handler，不驱动display/input/clock，不改producer/资源。精确argv token在UIApplicationMain前启用，首次guest pthread成功才start；失败提示在原boot输出接通后仅一次。默认off零queue/read/output；Start即使off也封住后续enable配置窗口，先有编译成功/run91的真实RED，再验证enable0且producer0。owner相关18方法中17通过、1既有WindowServer条件跳过14.285秒；kernel独立6/6通过0.498秒，linear12/12通过14.069秒及3个自有ASan/UBSan Foundation/GCD模式完整exit0。主线程持UI日志NSLock仍能由独立queue采样，四delay夹具不冒称真实等待180秒；已有真实GCD首采样测试保留。最终7路径freeze2 SHA `dd4d882851ed258deb40d204e548c9ca10ee7db6b09b972767b0b1ff29addce0`，native仍98/100，原UI logger逐字节不变，Mac主体逆剥精确恢复；只为新process-once合同调整off-start封口顺序。尚未把该诊断单独安装手机，后继将与实际分配失败的有限诊断合成连贯候选，再跑完整测试/双CI及签名更新；不将这些CPU证据当游戏画面。

Metal内存池失败诊断已完成：只在原有失败分支记录路径、阶段、种类/flags、请求/分段大小和原先实际求值的cap，未求值槽以cap_fields标明；不追加Metal/VM/validator调用、不输出地址或内容、不改变准入/工厂/BOOL/清理/回调。borrowed-segments区分group_nil与segment_buffer_nil，CPU缓存快照分支标为snapshot-copy/buffer_nil；deferred调用传NULL，保持原文案。owner 46项CPU回归全通过14.479秒；kernel独审8项及完整public wrapper的正常返回/siglongjmp两模式均通过，计数和清理与b7相同；linear逆剥整个family逐字节恢复基线，4项真实formatter CPU夹具通过，含运行时前缀和NUL的保守全宽消息414字节，小于512且canary保持。全部使用自有假设备，未据此认定实际分配原因或GPU/游戏通过。3路径最终收据SHA `3472fef3fe14cadbaaf13195222b09182cdc850b4545618858557ecc064e4492`，native仍98。此批与共享iOS观察器合为下一固定候选，完整回归/精确head双CI/工件和签名核验后保数据更新；当前手机仍是b7，尚不能游玩。
