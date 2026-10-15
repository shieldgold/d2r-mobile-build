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
| 纹理布局、格式映射、解平铺 | 已复用纯模块 | `vendor/nvn/nvn_tex.*` 与 `vendor/tex/` 已接入，继续复用整套布局算法；另核视图、mip、数组、块压缩与 CPU/GPU 所有权。 |
| 渲染状态与 Metal 管线 | 首批整组接入，需薄适配 | 复用上游 packed defaults/setters、blend factor/op、颜色写掩码、raster、depth 映射；适配本地命令录制、对象表、锁、缓存与编码器。未知枚举、尚未接入的 stencil/MSAA 继续明确拒绝。 |
| 纹理上传、dirty tracking、读回与 GPU copy | 可复用整组，需薄适配 | 保留上游上传和所有权算法；连接现有借用池、准确的字节范围、GPU 完成与资源存活，不能照搬全局纹理表或空 Flush。 |
| Shader capture、facts、反射与构建工具链 | 首批整组接入，需薄适配 | 复用原工具与其 Ryujinx 源码依赖，转换本地资源到 capture schema；未知驱动环境、缺少反射、翻译 Notes 或格式不匹配必须停下。现有 Rust 路线仍可用。 |
| NVN program 与 shader 事实表 | 通用装载可复用，游戏事实不可复用 | 只装载本地实际生成并核验身份的函数、绑定和反射；D2R 的 shader hash、常量缓冲区和贴图假设不适用于 BOTW。 |
| AudioOut PCM 队列、下混、AudioUnit 与完成通知 | 首批整模块接入，需薄适配 | 保留上游 SPSC/pull/notify 结构，适配 base-v0 buffer 布局、默认设备入口、受保护的客体复制、内核事件和前后台生命周期；创建或启动音频设备失败不退化成静音成功。 |
| 账户、只读资源、保存文件与 HID | 通用算法可复用，服务协议需薄适配 | 保留已验证的持久离线 UID、真实 FS/文件 IPC、HID shared memory 和事件；1.9 的 C++ 对象布局及直接函数接管不能覆盖 1.0 原 SDK。 |
| 同步、事件、GPU residency/cache 与队列生命周期 | 可复用算法，需薄适配 | 使用真实提交与完成信号管理借用资源，单独核对跨队列、缓存失效、关闭与后台恢复；上游单全局队列和固定帧数回收不作为本项目的完成证明。 |

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
- **Shader scratch**：两份上游的 setter 都是 `g_nops`。当前原程序仍会分配 pool 并记录 scratch 命令，不能只把查询返回零或跳过记录。Metal 的 thread-local 数组与 NVIDIA 物理 scratch 需要单独定义并验证合同。
- **着色器环境**：当前主程序与资源中的 control 版本并不统一。上游只支持的版本不能通过改版本号来冒充；缺少纹理/图像环境时，不能沿用默认 2D 解释而声称整条翻译链已经通过。
- **AudioRenderer**：固定 BOTW 上游没有完整 audren/mixer/执行实现。AudioOut 与原 SDK CPU/manual 探针都不等于持续处理真实 voice、PCM/ADPCM、混音和完成事件的渲染器。
- **D2R 定向优化**：bloom、freeze、ghost、skip-draw 等 hash 规则与 DMSH 事实表不进入 BOTW 通用管线；空回调、未知能力返回零、固定帧数回收也不移植为成功路径。

## 验收顺序

每批模块先做边界与回归测试，再用固定提交运行原程序；iOS 工件核对官方摘要、本地源码、签名和真机日志。最后必须看到真实游戏画面，验证触摸或手柄、持续运行、游戏实际保存与重开。当前 `3d1622c` 已在手机进入 `nnMain`，明确停在 scratch 能力查询 `0x19`，以上可玩验收均未完成。
