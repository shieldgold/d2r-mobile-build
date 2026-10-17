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

`tools/build_adapted_link_only.py --sdk iphoneos --adapters-only --output <新目录>` 进一步在私有源码副本应用正常入口、NVN52 TextureView、HLE／异常处理和地址唤醒四个补丁，编译 130 个单元。它仍使用原 1.9 元数据，仅用于编译验证；GPU、线程及 memstat 的诊断路径尚未全部封闭，不能据此运行游戏。

着色器身份桥 `tools/shader_key_bridge.py` 已核对现有导出的 7908 个捕获，并保留其中 3173 个的四模式条件合同引用。它不生成着色器事实，也不声明整个游戏的目录完整。六模块框架由 `tools/pack_guest.py --rewrite <离线重写目录> --output <新目录>` 复用现有完整 Mach-O 生成器构建；本地框架已逐节还原验证，未签名或执行。

正常游戏入口、base-v0 ABI、六模块完整重写、新着色器包和资源装载仍须逐项接通。编译、输入校验、安装和进程存活均不代表可玩。最终验收要求真实游戏画面、按键与双摇杆、声音、保存及重开。
