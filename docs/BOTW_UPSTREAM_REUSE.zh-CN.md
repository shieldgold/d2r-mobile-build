# BOTW 上游模块复用与接入边界

先审计完整上游，按模块接入已有实现，再补有原始证据的版本和后端差异。登记了一个接口、成功安装、进入 `nnMain`，均不代表游戏可玩。

## 固定来源

| 来源 | 固定提交 | 本次用途 |
| --- | --- | --- |
| [D2R Mobile](https://github.com/jhaizhou-ops/d2r-mobile/tree/9de307ecaff42b3c9d45e58992c848ee89b8f6a1) | `9de307ecaff42b3c9d45e58992c848ee89b8f6a1` | 宿主、完整 NVN/Metal、纹理与音频模块基线 |
| [BOTW host boot 分支](https://github.com/TommyLau/botw-mobile/tree/5f673ae2ea4f90ad46403aa6fbe13537fb557271) | `5f673ae2ea4f90ad46403aa6fbe13537fb557271` | BOTW 专用工具链、资源与宿主适配候选 |
| [Hades iOS 分支](https://github.com/imink/hades-ios/tree/928eef29db4a6ac5e2460c7217f8c8ad34769ae2) | `928eef29db4a6ac5e2460c7217f8c8ad34769ae2` | 对照真机验证与生命周期经验；其 Vulkan 路线不作为 NVN 实现 |

前两份源码均以独立 Git checkout 核对完整提交，保留 MIT 许可。当前已有改写器固定于 D2R 的 `e2a0c9a9b5d65f6cd505e967e4760e2745e90ca8`；其 `patch_nso.py` 与上述 `9de307e` 逐字节相同，因此不为更换提交名称而重做改写。

本项目输入是 BOTW **1.0.0 / base-v0**；BOTW 上游分支面向 **1.9.0**。该分支自己的 README 仍明确没有第一帧、菜单或可玩构建，AudioRenderer 与 iOS 打包仍在后续工作中。不能把文档中的私有 CPU 音频探针当作仓库已提供的音频渲染器。

## 已有实现与整模块接入范围

“可直接复用”针对纯算法或源码模块；一旦涉及客体对象、GPU 或系统生命周期，仍需本项目的薄适配和实际测试。

| 模块 | 分类 | 接入方式与依赖 |
| --- | --- | --- |
| ARM64 改写器 | 已复用 | 继续执行摘要固定的完整上游工具，关闭 D2R 私有规则；本地仅补已验证的跳转表和 Host39 差异。 |
| 装载、重定位、门、TSD 与签名代码映射 | 已复用架构，需薄适配 | 保留六业务模块、符号抢占、段权限、真实线程清理及异常边界；不能整包覆盖成上游 D2R 模块集合、分辨率参数或宽屏门。 |
| SDK 堆与游戏根堆 | 显式 base-v0 双参数适配已接入 | `e41e422` 复用固定改写器的 TSD 参数读取模式，同时服从真实 kernel 与手机实时内存限额；真实 SVC 分配、不扩大账目。iPhone 12 已分配约 1.90 GB SDK 堆并进入 `nnMain`；完整游戏峰值仍待验证。 |
| BufferBuilder / Buffer | 整组复用并适配 | `4fb5b23` 接入全部 17 个 typed 入口，保留 base-v0 默认值、GetMemoryPool 指针 ABI、稳定 DebugID、池代际与完成持有；60 项图形回归通过，真机已越过旧 BufferBuilder 停止点。 |
| 纹理布局、格式映射、解平铺 | 已复用纯模块 | `vendor/nvn/nvn_tex.*` 与 `vendor/tex/` 已接入，继续复用整套布局算法；另核视图、mip、数组、块压缩与 CPU/GPU 所有权。 |
| 渲染状态与 Metal 管线 | 首批已整组复用并薄适配 | `d9f818c` 新增 28 个 typed 入口，复用 packed defaults/setters、blend factor/op、颜色写掩码、raster、depth 映射；适配命令录制、锁、缓存与编码器，49 项测试通过。CullBoth 保留真实顶点执行与 SSBO 写回；未知枚举、未接入 stencil/MSAA 继续拒绝。 |
| TextureBuilder / View / Texture 与存储一致性 | 整组复用并适配，尚待原游戏验收 | `ac76949` 接入原 base-v0 82 个相关入口中的 76 个真实实现，另有 2 项现代 Strided 读写；6 项 driver/sparse/ZCull 查询继续具名拒绝。保留 View 36 字节 ABI、精确 GOB 写入、mip/layer 视图、真实 GPU 读回及 dirty/ownership 生命周期。宿主 `7b4193f` 接通 fault/checked-prepare；真实 runtime 与 Metal 联合 RO 写故障、VM 写入和 GPU 像素验证通过。范围与拒绝项见[纹理合同](../native/botw_graphics/TEXTURES.md)。 |
| Shader capture、facts、反射与构建工具链 | 首批已整组复用并薄适配 | `3006192` 保留 368 份固定上游源文件与许可，11 项适配和 26 项上游测试通过。37 份真实 main shader 转为 SPIR-V/MSL 并经 Metal 编译，但 251 项未知事实仍保留为未知，严格链当前 0 份标记可运行。既有 Rust 路线继续使用其独立验证的 bundle。 |
| NVN program 与 shader 事实表 | 通用装载可复用，游戏事实不可复用 | 只装载本地实际生成并核验身份的函数、绑定和反射；D2R 的 shader hash、常量缓冲区和贴图假设不适用于 BOTW。 |
| AudioOut PCM 队列、下混、AudioUnit 与完成通知 | 首批已整模块复用并薄适配 | `d49e28e` 模块与 `90aa17b` 宿主接线保留上游 SPSC/pull/notify 结构，适配 base-v0 buffer 布局、默认设备、客体复制、内核事件和生命周期。19 项测试与真实 Mac 零 PCM 回调通过；音频设备失败继续明确报错。iPhoneOS 复制使用公开 `vm_*` API。 |
| 账户、只读资源、保存文件与 HID | 通用算法可复用，服务协议需薄适配 | 保留已验证的持久离线 UID、真实 FS/文件 IPC、HID shared memory 和事件；1.9 的 C++ 对象布局及直接函数接管不能覆盖 1.0 原 SDK。 |
| 同步、事件、GPU residency/cache 与队列生命周期 | 可复用算法，需薄适配 | 使用真实提交与完成信号管理借用资源，单独核对跨队列、缓存失效、关闭与后台恢复；上游单全局队列和固定帧数回收不作为本项目的完成证明。 |
| Shader scratch 与零容量 pool | 后端差异合同已接入，绘制证据仍受限 | `6272c09` 保留 4096/131072 对齐与粒度，Metal 外部 scratch 要求为零；建立真实零容量对象、16 字节 typed 命令、代际与队列完成持有，55 项图形测试通过。`6272c09` 的 Mac 和真机已越过零尺寸分配与 PoolInitialize；后续 Buffer 接入版又越过 BufferBuilder，但尚无原程序抵达 SetShaderScratchMemory 的证据。实际绘制还要求精确 shader 证明，不能用上游 `g_nops` 代替。 |

## 完整 NVN 清点

以接入前的 `3d1622c` 为基线，D2R 固定源码共有 **202 个 typed 定义和 1 个 untyped 定义**。本项目当时登记 140 个入口，typed 交集 114 个；另 88 个是候选差距，不能直接解释为 88 个已经可工作的实现。清点另列出 9 个 `g_nops`，并识别 typed 定义中仍为空操作或原驱动策略的条目。

| 上游模块组 | typed 定义 | 接入前已登记 | 新 typed 候选 |
| --- | ---: | ---: | ---: |
| Device / MemoryPool / 描述符 | 29 | 24 | 5 |
| TextureBuilder / Texture / View | 35 | 20 | 15 |
| Sampler / Program | 17 | 12 | 5 |
| Sync / Event / GPU 完成 | 13 | 4 | 9 |
| Queue / CommandBuffer | 25 | 21 | 4 |
| Transfer | 8 | 1 | 7 |
| Render state / recording | 61 | 22 | 39 |
| Draw / Compute / Barrier / Present | 14 | 10 | 4 |

Texture 组的 untyped `GetRawStorageClass` 未计入 typed 合计，也不因名字存在而准入。完整逐项清单与可复算脚本保留于本地 `outputs/botw-nvn-reuse-audit/`，只读取公开固定源码，不需要游戏文件。

## 必须补证据的差异

- **NVN 对象布局**：两份上游的 Texture、TextureView、Program 与 Sampler 对象大小存在差异。按当前原 SDK 的实际读写上界验证；不套用 1.9 的较小对象或 D2R 的较大对象。
- **Shader scratch**：两份上游的 setter 都是 `g_nops`，本项目没有照搬。零容量 pool 保留设备、身份、代际、flags 与 token，不解引用 token、不建立空 MTLBuffer；非空用途拒绝，Finalize 等待真实 GPU 完成。`ed9f354` 已将实际 Rust SPIR-V 1.4 → 原工具链 raw MSL → 完整后处理 → 最终 MSL 与 scratch 证明逐字节闭环；独立 Ryujinx 路径只保留为历史交叉验证。iPhone 12 的 `4fb5b23`、`3e9c786` 已装载 direct-v2 bundle，37 份来源均核验；这仍不是原游戏实际绘制的证明。
- **着色器环境**：当前主程序与资源中的 control 版本并不统一。上游只支持的版本不能通过改版本号来冒充；缺少纹理/图像环境时，不能沿用默认 2D 解释而声称整条翻译链已经通过。
- **AudioRenderer**：固定 BOTW 上游没有完整 audren/mixer/执行实现。AudioOut 与原 SDK CPU/manual 探针都不等于持续处理真实 voice、PCM/ADPCM、混音和完成事件的渲染器。
- **D2R 定向优化**：bloom、freeze、ghost、skip-draw 等 hash 规则与 DMSH 事实表不进入 BOTW 通用管线；空回调、未知能力返回零、固定帧数回收也不移植为成功路径。

## 验收顺序

每批模块先做边界与回归测试，再用固定提交运行原程序；iOS 工件核对官方摘要、本地源码、签名和真机日志。最后必须看到真实游戏画面，验证触摸或手柄、持续运行、游戏实际保存与重开。旧 iPhone 15 Pro 的 `3d1622c` 停在 scratch 查询 `0x19`；用户改用的 iPhone 12 / iOS 18.1 在 `c13938e` 首次遇到真实初始堆 OOM，更新 `6272c09` 后已实际分配 1,904,214,016 字节、保持 32 MiB 根堆差额并进入 `nnMain`，随后在 `nvnBufferBuilderSetDevice` 停止。整组 Buffer 接入后，`4fb5b23` 真机越过该点，转为线程栈映射问题；`3e9c786` 已真机安装并越过该映射，进入原程序回溯与命令 22 退出路径，原始触发原因仍在取证。`8677910` 的真实容量适配已在新手机启动两个工作线程，转为 `GetInfo` id 8 停止点；`0f989ca` 已在新手机完成保留数据的更新与启动，真机及固定 Mac 原程序均越过此处并抵达 HID 命令 1（ActivateDebugPad）。该调试手柄共享内存采样生命周期仍待接通。完整资源、direct-v2 manifest 与原身份再次核验通过；以上可玩验收均未完成。
