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

`patches/base-v0-signed-jump-table.patch` 处理 `subsdk3` 的两处实际带临时寄存器的 signed-offset 跳转表。新增路径复用 `scripts/botw_native/branch_tables.py` 的完整边界、控制流与只读表证明；既有 x18 路径保留。该补丁的函数级验证不等于六个模块已经完整重写。

原 main 中 SDK 堆与根堆参数保留原有字面值。新宿主使用线程保留槽传递分辨率，不能把旧宿主的堆参数 thunk 原样接到这些槽上。

## 完整上游编译边界

`tools/build_link_only.py --sdk iphoneos --baseline-only --output <新目录>` 用于验证完整上游 iPhoneOS 代码能编译和链接。它使用原 1.9.0 的公开 profile 元数据生成 header，不嵌入游戏内容；产物不是 1.0.0 配置，也不能安装。它不会运行编译出来的宿主、Guest 或 GPU 工作。

正常游戏入口、base-v0 ABI、六模块完整重写、新着色器包和资源装载仍须逐项接通。编译、输入校验、安装和进程存活均不代表可玩。最终验收要求真实游戏画面、按键与双摇杆、声音、保存及重开。
