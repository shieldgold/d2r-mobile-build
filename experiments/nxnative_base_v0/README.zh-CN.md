# nxnative 与 BOTW 1.0.0 接入

原样来源为 [seedds/nxnative](https://github.com/seedds/nxnative/tree/65fa9caab486768f70da98c51c2421aff76f676d) 的固定提交 `65fa9caab486768f70da98c51c2421aff76f676d`。`upstream/` 保留 953 个原文件、许可和 Git mode，根 tree 为 `5b1545c089970a2aca0325aa31fa71bce217813f`。本目录的配置、工具和补丁独立保存，不改写上游原件。

上游已声明 BOTW 1.9.0 在 iPhone 可玩，但其现成 profile 只有 1.9.0 的 main / subsdk0 / sdk，使用 iOS 26 与 Metal 4。当前任务是接入现有 1.0.0 六模块和 NVN 52.32，不能仅改版本号或直接沿用 1.9.0 的固定偏移、着色器表。目标设备已改为用户现有的 iOS 26+ 设备；不修改旧设备的系统。

## 来源与原始输入校验

从仓库根目录执行：

```sh
python3 experiments/nxnative_base_v0/tools/verify_source.py
python3 experiments/nxnative_base_v0/tools/preflight_profile.py \
  --exefs outputs/botw-native-base/ExeFS \
  --romfs outputs/botw-romfs-base/romfs.bin
```

第二条需要原来保留的输入与身份报告，仅在持有本地资源的环境运行。它核对七个原 NSO 的完整文件摘要与 32 字节 build ID，声明六个业务模块加载、rtld 不加载。RomFS 校验重读头和元数据；完整镜像 SHA256 仍注明来自既有 manifest，不冒充本次重新扫描全部 14 GB payload。

来源工具重算 Git tree，拒绝改动、额外或缺失文件、执行位不符、链接和越界路径。适配器与外部复用代码也由 `SOURCE.lock.json` 固定；更新它们后需要重验并更新锁。输入校验不执行或改写游戏，不生成假着色器表。

## 重写器差异

`patches/base-v0-signed-jump-table.patch` 处理 `subsdk3` 的两处实际带临时寄存器的 signed-offset 跳转表。新增路径复用 `scripts/botw_native/branch_tables.py` 的完整边界、控制流与只读表证明；既有 x18 路径保留。

`tools/rewrite_base_v0.py --exefs <原目录> --output <新目录>` 在完整来源与七模块身份校验后，仅以 `native` 规则重写六个业务模块。它使用独立工具副本、关闭私有门，不带旧 bootlist；输出检查包括布局、正常门、声明站点以外原字节保留，以及原堆参数。2026-10-17 已完成六模块实际离线重写，收据位于本地 `outputs/nxnative-base-v0-rewrite-65fa9ca-01/report.json`；输出没有执行，运行兼容性仍待验证。

原 main 中 SDK 堆与根堆参数保留原有字面值。新宿主使用线程保留槽传递分辨率，不能把旧宿主的堆参数 thunk 原样接到这些槽上。

## 完整上游编译边界

`tools/build_link_only.py --sdk iphoneos --baseline-only --output <新目录>` 用于验证完整上游 iPhoneOS 代码能编译和链接。它使用原 1.9.0 的公开 profile 元数据生成 header，不嵌入游戏内容；产物不是 1.0.0 配置，也不能安装。它不会运行编译出来的宿主、Guest 或 GPU 工作。

完整 126 个上游编译单元加独立入口已在 iPhoneOS SDK 26.5 中编译、强链接通过，见[固定提交的 CI](https://github.com/shieldgold/d2r-mobile-build/actions/runs/39012668489)。该结果只确认原始宿主的编译基线。

`tools/build_adapted_link_only.py --sdk iphoneos --adapters-only --output <新目录>` 在私有源码副本应用薄适配。原四补丁的 130 个单元已在 [iPhoneOS SDK 26.5 CI](https://github.com/shieldgold/d2r-mobile-build/actions/runs/39014320818) 编译、强链接通过，本地也核对了全部来源和下载产物。源码打包与静态装载合同这一批的 [CI](https://github.com/shieldgold/d2r-mobile-build/actions/runs/39015312170) 同样通过。

新增正常运行诊断封闭和完整条件着色器模块后的 136 个单元已在 [iPhoneOS CI](https://github.com/shieldgold/d2r-mobile-build/actions/runs/39016541153) 编译、强链接通过，本地重新核对了 1027 件来源及 576 份日志。诊断封闭保留正常线程、脏页、纹理上传、反馈快照和 PCM 输出；条件模块在实际绘制时选择模式、核对完整着色器身份并提交。独立复核发现的窗口原点切换竞争已修复：最终检查与锁定前移至 viewport 编码之前，保持到 draw；实际代码回归覆盖切换、并发 setter 与提前返回。这些 link-only 构建仍使用原 1.9 元数据与仅返回的独立入口，不能安装或启动游戏。

`tools/build_base_v0_runtime.py --host-only --sdk iphoneos --output <新目录>` 改用专属 `profile/base_v0_runtime.tsv` 和原完整 `game.header`，生成 1.0.0 六模块、NVN52.32 与完整双摇杆布局的元数据；未知的 sampler／SSBO 表保持 -1。它去掉仅返回入口，保留真实 UIKit `_main`，后台宿主为 `dm_main`，并接入完整逻辑内存与正常账户／显示层 API，共 139 单元。`--stage-only` 仅准备来源与 header，完整 iPhoneOS 编译仍须实际执行。音频服务及本地装载合同尚待接入，所有产物明确不可安装、不可执行。

着色器身份桥 `tools/shader_key_bridge.py` 已核对现有导出的 7908 个捕获，并保留其中 3173 个的四模式条件合同引用。它不生成着色器事实，也不声明整个游戏的目录完整。六模块框架由 `tools/pack_guest.py --rewrite <离线重写目录> --output <新目录>` 复用现有完整 Mach-O 生成器构建；本地框架已逐节还原验证，未签名或执行。

`tools/package_shader_sources.py --source <本地条件发布目录> --bridge <已核身份桥报告> --output <新目录>` 完整复用既有条件发布验证器和源码构建模块。它在新私有目录核对并加载来源代码及完整 374 文件工具链，再逐项验证四模式合同、原始分析与 MSL 摘要。输出仅保留原发布的允许文件、完整身份桥和连接两者的摘要索引；捕获字节和工具链不进入包。实际 3173 个捕获／12692 个变体已生成 38085 件文件，并复核了精确文件集合和每份源工件摘要。手机上的 Metal 编译及实际绘制尚待验证。

正常资源目录已用既有 `RomFSImage.export` 从本地镜像完整导出，39678 个文件的每份 payload 均与固定 manifest 摘要一致。这项导出和核验不执行游戏。

`tools/generate_base_v0_contract.py` 核对六模块的 MOD0、动态符号与两类重定位，绑定实际宿主的完整 force registry。原 130 单元宿主有 614 个登记项，六模块实际选用 236 项、283 处按名引用；这不是后续正常运行宿主的固定条数。新增接口或更换音频接法后必须重新生成合同。六模块正常门共 230 个，其中 main 93、sdk 137；不能把 sdk 的 137 当成总数。

专属内存适配完整复用既有逻辑内存与地址空间模块，保留原 H/R，失败时不发布虚假的分配成功。账户选择、显示层缩放和 NVN52 同步返回也有独立的正常 API 适配。它们尚须与专属 1.0 元数据、真实 UIKit 入口和完整音频服务一起进入正常宿主。

音频的版本差异已有静态证据：1.0 SDK 没有新上游 CPU renderer 所需的完整导出，现有参数转换还会覆盖 REV1。正在接入项目既有完整 Eden renderer 服务端，并复用当前宿主的消息、事件和映射；不以静音或空返回冒充正常输出。

编译、输入校验、安装和进程存活均不代表可玩。最终验收要求真实游戏画面、按键与双摇杆、声音、保存及重开。
