# 完整 BOTW 上游宿主接入

固定复用 TommyLau/botw-mobile 的 `21f026e0c7fd9fee575a1bfa8d925553bb4db88e`。`upstream/` 保存原始字节；所有平台或版本修改只通过锁定补丁应用于独立构建输出，不改原件。完整 iOS 闭包为 53 个上游编译单元，macOS 另编译原 AppKit 前端。

`scripts/build_link_only.py` 校验组件完整清单、补丁结果与真实编译器依赖，以 macOS 15.0 或 iOS 18.0 为最低目标逐单元编译并真实链接。每条命令的 stdout/stderr、退出码和结果均保存；失败也保留收据。实际 SDK 版本单独记录，不把最低目标称为同版本 SDK。

```sh
python3 experiments/botw_upstream_host/scripts/build_link_only.py \
  --sdk macosx --output outputs/botw-whole-host-macos-COMMIT
python3 experiments/botw_upstream_host/scripts/build_link_only.py \
  --sdk iphoneos --output outputs/botw-whole-host-iphoneos-COMMIT
```

两种构建均不执行最终宿主、游戏或 GPU，不下载游戏、密钥或 shader，不安装手机程序。iOS 前端默认缺少配置时显示明确错误并阻止启动；只有提供真实 `whole_runtime_configuration()` 且显式启用配置宏，才能进入准备、生命周期和宿主入口。这个默认链接结果不是安装候选。

完整宿主使用自己的模块、线程、时钟和 dirty-page 实现，不链接旧图形实验的 `host_adapter.c`，也不叠加旧生产 runtime。`whole_policy` 阻断原始诊断、采样和计数代码生成，保留正常 dirty-page fault 路径；原图形的诊断门禁与公开 iOS VM 调用补丁一并应用。

base-v0 补丁将 TextureView 的完整 14 接口与内部消费者统一到静态程序核验的 36 字节布局，包括真实 u8/u16 截断、零扩展、六组设置状态、完整 swizzle/depth/target 字段和 Compare。编译与链接包含真实 `base_v0_abi.c`；该单元不代表其他版本差异已完成。

固定 base-v0 接口配置先验证六个模块的实际加载顺序、不可变映像字节和重写布局，再将原 127 条接管记录与实际模块重定位逐项匹配。当前固定程序有 92 条接管、133 次按模块引用；未知映像、记录或引用均拒绝，原严格命中校验保留。构建后还会静态解析该目标真实链接文件的 Mach-O chained fixups，核验接口表，不用 macOS 的接口表推断 iOS。

旧重写程序的 KRES0/KRES1 是堆与根 arena 的容量参数。接点复用原容量规划函数，查询唯一 SVC 分配器和 iOS 当前可用内存，为每个线程安装同一组容量；初次实际扩堆重新检查余量，权限失败不发布成功。正常宿主写入与 dirty-page 注册也传播真实失败，资源发布前完成校验；并发 fault 等待结束后重新确认页已成功解保护。

MemoryPoolBuilder 的七个已核接口保留原字段与边界：Defaults 仅写 `[8,28)`、默认 flags 为 `0x21`，查询接口返回实际字段。正常读写复用完整 `protected_copy.h`，仅为锁定的 builder 与普通 backing 编译单元准许操作数范围的 VM 权限校验；其他诊断单元仍被阻断。

普通内存池复用完整既有 NoCopy 几何、真实 VM leaf 权限与逐资源释放 ACK 算法，仍由原 `g_pools/g_keep` 保存唯一对象目录。CPU_CACHED 的可读后备路径才允许快照，受保护的精确页范围先保存元数据，在真实消费者需要时建立合法 backing。发布失败回滚，缺少真实 deallocator ACK 时保留对象与借用。原图形入口的读写准入等待翻译、上传、显示 CPU 借用与实际 GPU 完成后才退役原 serial/alias/descriptor/显示缓存和 residency；后台暂停也等待已经取出、尚未提交的显示帧。physical/virtual/MapVirtual 仍明确拒绝。本地测试使用自有 CPU 映射与协议替身，完整构建只编译链接，不将这些结果称为 GPU 或游戏验证。

服务接点直接导出原 `hle_thread.c` 的唯一句柄表和等待队列。IPC transport 以稳定私有 owner token 验证分配与释放，事件双端先检查完整容量再发布；原事件消费者读取同一 signal 状态。音频生产者独立保留实际事件对象，句柄关闭、编号复用不会将通知转移到另一个事件。正常 SVC 以独立薄桥调用完整既有 HIPC/CMIF 服务模块：命名口使用检查后的读副本，普通发送使用当前线程 TLR 的首 `0x100` 字节，显式缓冲发送保留实际容量边界；Close 按同一内核表分流。解绑先等待实际调用退还借用，再由调用方完成服务销毁。运行配置与音频服务仍需接通，未配置时明确失败。

资源接入编译完整既有 `romfs.c`，在原 FS 的同一挂载和句柄表上读取 RomFS.bin，保留目录、只读、代际和卸载合同。账户只加载原私有 UID 与对应已有存档目录；原事务 FS 直接使用已绑定目录，不接受环境变量改到另一个账户。音频保留完整 PCM 算法，iOS 改用 RemoteIO，并让前后台操作真实暂停或恢复所有已开输出流。新增写入接点在同一生产者锁内先读取旧 FIFO 计数再发布新帧，避免暂停清空与新帧消费混淆；实时回调继续无锁。Applet 接点保存真实测量状态和通知，未配置或缺少所需暂停实现时明确失败。

完整音频算法固定为 Eden `d8c6c59b679867d2595fdc0953df033475e23380` 的全部 67 个 renderer、2 个 ADSP renderer、原 manager 与 SinkStream，另编译薄端口，合计 79 个音频单元。构建驱动核验独立组件的完整清单、原件与补丁输出，并把所有对象直接加入同一宿主链接，避免 archive 未引用对象被遗漏；实际编译器依赖仍逐项追溯到原件及确定的补丁结果。宿主仅编译一份原 PCM。正常 audren 服务使用原 HIPC parser 的精确 Open/Update 扩展，保留原模块 Result，后台调用借用期间禁止关闭或释放对应 session/object/arena；其余服务不放宽输入句柄或输出缓冲限制。真实内核 transfer、普通音频池借用及运行配置继续由后续接点完成，链接成功不代表设备出声。

旧版 shader 控制头以独立、精确的 v8/2000 字节 profile 准入，版本和原始 hash 不变；同样补丁在完整离线工具的私有副本上应用。真实场景的未知资源事实仍被拒绝，不能用该准入或自有 EXIT 翻译测试冒称完整游戏 shader 已就绪。

当前已有提取数据为 base-v0/1.0.0。完整上游的 SDK/NVN/音频版本限定仍需真实适配，不能套用 1.9 偏移、重标版本、忽略缺失 shader 或用空实现补链接。完成这些接点后才能替换生产闭包并保数据安装。可玩验收必须包含真实游戏画面、输入、声音、存档和重新打开。
