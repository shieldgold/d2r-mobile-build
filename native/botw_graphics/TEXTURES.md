# Base-v0 纹理对象、视图与 CPU 存储

`texture_family.inc` 成组接入 TextureBuilder、TextureView、Texture 元数据与 CPU 读写，采样描述符、渲染目标和命令提交共同消费视图。CPU 存储算法来自固定 MIT 上游 `TommyLau/botw-mobile@5f673ae2ea4f90ad46403aa6fbe13537fb557271` 的 `hle_nvn.c`、`nvn_mtl.m` 及 `vendor/botw-texture` 整模块；后者保存原路径、SHA256、许可证和移植说明。已有 deko3d 布局及 block-linear 解排布算法继续复用，保留 zlib 来源。

## 原版 ABI 与范围

原版 base SDK 的 82 个相关入口中，78 个已有真实实现，其中两项 StorageClass 实现已确证的 pitch、普通未压缩 color 和有限单采样 depth 分支；现代头文件中另外两项 Strided 读写也有实际实现。以下四个 driver/sparse/ZCull 查询保留具名拒绝：

- `nvnTextureBuilderGetSparseTileLayout`、`nvnTextureGetSparseTileLayout`
- `nvnTextureBuilderGetZCullStorageSize`、`nvnTextureGetZCullStorageSize`

`texture_abi.h` 按本地原 SDK 可执行 oracle 定义 Builder 的实际 `0x64` 字节字段区和 View 的 `0x24` 字节布局，保留对象预算尾部及原版 padding。Builder Defaults 保留 device，尺寸和 samples 为 0、levels 为 1、swizzle 为 R/G/B/A；SetSize2D 将 depth 复位为 1。stride 是无符号 32 位截断值。Packaged layout 保存原版三个四位字段；只有已验证的 block height 会被 GPU 布局消费，未实现的其他字段严格拒绝。两个 MemoryPool getter 返回指针。

View levels 为无符号 8 位、layers 为无符号 16 位，depth/stencil mode 保留完整 32 位。未设置的 getter 仍写出存储值并返回 false。Compare 忽略未设置字段和 padding。采样和命令记录持有按值快照，随后改变 guest View 不能改变已登记的描述符或已录命令。

实际 GPU 路径限定为已验证的 2D/2D-array、单采样、有限 mip 链及可精确映射的 Metal 格式。数组显式单层视图可作为 2D render target；采样视图支持 mip/layer 子区间、0 表示余下范围、Metal 格式视图和 ZERO/ONE/RGBA swizzle。渲染目标使用选定视图的实际尺寸和 pixel format，包括 sRGB。未验证的 3D/cube/MSAA、数组 depth、组合 depth/stencil、BC 解码、GL 导入、packaged data 指针等保持受控拒绝，没有 fallback 纹理。

## 存储生命周期与一致性

每个纹理固定原 pool 指针、宿主 pool generation 和 device。pool finalize/同地址重新 initialize 后，旧纹理、描述符、render target、CPU 读写及实际提交拒绝 stale generation。Finalize 先检查 guest 对象可写，再撤销资源归属；失败时保留原对象和 GPU 数据。

block-linear 导入先通过公开 `vm_read_overwrite` 快照完整存储，再解排布上传。提交前的 hash、GPU-written 状态和 flush 证书保存在该 Metal command buffer 的独立 pending 表；只有真实 commit 后才发布。中途拒绝不会把未执行的上传缓存为完成。分配可能失败的归属准备发生在 completion group enter 之前，避免 shutdown 永等一个从未提交的 buffer。

CPU WriteTexels 使用完整上游 region/stride/block 检查和精确 GOB run 遍历。先快照全部源行，再预检整个目标跨度；通过公开 `vm_write` 写精确字节段，保留 padding、其他 texel、mip 和 layer。部分首次写入先导入未修改子资源。CPU ReadTexels 从实际 Metal 存储读回，行间填充保持不变。CPU_CACHED 非 coherent pool 的 flush/invalidate 也走受控 VM 拷贝。读写共用 `botw_host/protected_copy.h`：实际复制前校验完整 source READ 和 destination WRITE VM 范围，每次 Mach 调用同时受 4095 字节和两侧宿主页边界限制，保留数字 VA 重叠方向、Mach 错误及短读取检查；该策略不提供并发 remap 事务或跨版本 VM 不分裂保证。

GPU 私有的最新内容建立 `texown` claim。别名纹理、buffer 地址以及同 command buffer 的 pending 写入不能把旧 CPU 字节当成最新 GPU 内容；无法证明的别名消费明确拒绝。资源 finalize 后 claim 仍以 retired 状态保留，仅完整 context 停止 guest、等待所有 GPU、成功退役所有 dirty 范围后才清空。InvalidateTexels 实际回写选定的整层各 mip，保留 padding；FlushTexels 忽略 region，按原缓存/层选择规则执行真实上传或验证完整证书。GPU 写后缺少证书、CPU 改写或改写后恢复原 hash 均不能伪装为 CLEAN。

宿主在 init 后、创建任何 pool 前调用 `botw_graphics_set_memory_tracking_registration(botw_host_register_memory_tracking)`。桥原子发布 process-lifetime fault/prepare pair；普通 VM 写入先执行 checked prepare，纯 validator 不变更内存保护。未配置桥时不设置页保护、不签 CLEAN；CPU 权威的内容仍可真实上传，无法证明的 GPU 写后 flush 明确拒绝。checked dirty retirement 失败时，pool/backing 保留，不能继续释放。caller 必须排除外部并发 remap，并停止新用户后再销毁 context。

## 回归证据

`tests/test_botw_texture_family.py` 运行合成 base ABI/canary/生命周期回归及真实 Metal 子进程：非零 mip/layer 采样和 RT、格式/swizzle、sRGB 编码、CPU region/stride、invalid source、实际只读/NONE 映射、stale/alias、未提交中途失败重试及 Finalize 拒绝后数据保留。tracked 子进程使用真实 ARM64 页 fault，覆盖 clean、改变后恢复原字节的拒绝及 GPU invalidate 回写。

`tests/test_botw_texture_modules.py` 保留上游 5,438 项算法检查，并在 ASan/UBSan 下验证 checked run callback、分配失败、真实 fault、共享页、并发、retirement、无桥、权限查询失败和 prepare/protect ABA。既有 60 项图形回归另行运行。原版本地纯 ABI oracle 有 640 断言；它不包含一般 driver 布局的证明。

这些是兼容层与合成 GPU 回归，不能替代原游戏首帧、输入、存档重开或 iOS 真机验收。

## Linear 存储与真实设备对齐

`linear_texture.h` 统一维护两项 stride capability、Builder StorageAlignment、默认 stride、容量规划与 Initialize 的契约。初始化设备时，仅对本桥已经准入的 43 个精确映射、恒等分量、非压缩 color 格式调用 Metal 的 `minimumLinearTextureAlignmentForPixelFormat:`；depth、stencil 和 compressed 格式不会进入这个 API。每项结果必须非零、为 2 的幂且能放入 NVN 的 `int`。经此验证，最大值就是这些对齐的最小公倍数；普通 linear 和 linear RT 的最终对齐分别至少保留 guest 的 32/128 字节约束。空格式集合或异常结果只使相关 linear 路径明确不可用。linear RT 还要求 Metal Apple GPU family，其他设备功能继续初始化。

`nvnDeviceGetInteger` 的 `0x38/0x39` 与有效 stride、存储容量和 offset 检查使用同一份设备策略。stride 为 0 时按该策略计算有效行距；Builder、Texture 的元数据 getter 继续返回原始字段，实际 Metal 存储与 CPU region 操作使用已解析的行距。普通 linear 只具有采样用途，作为 attachment 需要 `LINEAR_RENDER_TARGET`。此路径仅支持 2D、一个 mip、一个 sample、depth 和 arrayLength 均为 1 的普通 color 纹理；非法组合先受控拒绝，再调用 Metal。实际 buffer 的 offset/stride 仍逐格式验证 Metal 对齐，storageMode 与 buffer 保持相同。

本阶段 linear 只接受与 guest 内存共享同一存储的 coherent no-copy pool。需要复制到单独 Metal buffer 的 CPU_CACHED pool 明确拒绝，避免 GPU 写入后从陈旧 CPU 字节导入别名纹理。普通 coherent pool 的 CPU 写、GPU 清屏、实际采样、读回及 padding 保留均由真实 GPU 探针验证。只读和不可访问内存回归继续使用 coherent fixture；分离 pool 的拒绝另有测试。

同一 pool 可容纳多个不重叠纹理。不同 base 只要一方为 linear，其完整存储 span 重叠就拒绝 Initialize；已存在的 private GPU ownership 和 retired claim 也不能通过 linear 绕过。合法 view 继续持有同一 base。整批提交在编码、GPU counter 发布之前检查单 render pass 中的反馈：linear attachment 与采样、uniform/storage/vertex buffer 的重叠，以及采样 linear 与 storage buffer 的重叠均明确拒绝。命令队列原有同步合同继续适用。

设备日志记录实际 Metal 来源、格式集合范围、最大格式对齐、最终两项 stride 对齐、Apple RT 能力和 coherent-only 范围。Mac 的结果只证明当台 Mac；A14 需要读取该手机实际启动日志和运行结果。本地合成测试的查询、真实 clear/draw/CPU 读写、padding 和整批拒绝证据不表示原游戏可玩。

此策略依据 [Apple linear alignment API](https://developer.apple.com/documentation/metal/mtldevice/minimumlineartexturealignment(for:))、[buffer-backed texture 契约](https://developer.apple.com/documentation/metal/mtlbuffer/maketexture(descriptor:offset:bytesperrow:))。固定上游的 Tegra 32/128 表用于保留 guest 最低约束；它不替代当前 Metal 设备测量。

## Base-v0 已验证的 storage class 元数据

BuilderGetStorageClass 与 TextureGetStorageClass 共用纯元数据规则：target 为 BUFFER 或 flags 含 `0x10/0x40` 任一位时返回 `0x100`。这来自原版 base-v0 SDK 的独立分支：raw kind 为 0，最终编码为 `0x100 | rawKind`。Builder 查询允许尚未 SetStorage；该分支与 format、维度、samples、device 和 pool 均无关，不因 Metal 尚不支持该格式而拒绝，也不会调用 Metal。它不代表 GPU 创建能力；BUFFER 的存储尺寸/类别可查，当前 GPU BUFFER texture 创建仍明确拒绝。

非 pitch 路径仅在 `COMPRESSIBLE` 位清除、format 属于原版有效普通颜色集合时返回 `0x1fe`。固定原版 `subsdk0` 的格式记录宽 `0x34`，`+0x30` 的有效字段非零且 `+0xe/+0xf` 的 depth/stencil 位数均为零者，在本桥声明的 `0x01..0x9c` 中恰有 113 项：`01..18`、`22..30`、`37..50`、`6d..9c`。37 个原记录有效字段为零的格式、6 个 depth/stencil 格式和越界枚举不进入普通 color 路径。准入来自原版 traits，独立于 Metal 格式映射，不会因为宿主尚不支持某个格式而改变 storage metadata。

此值由原调用链证明：`BuilderGetStorageClass` 的非压缩分支向 `b6900` 传压缩请求 0；普通颜色参数经 `NvRmGpuDeviceChooseKind` 到 HAL `31294`，选择 vtable `+0x68`。原版 RTTI 标明 GK100 与 GM200 两套 HAL 均将该槽指向 `316c0`，其完整函数只返回 `0xfe`；Builder 最后编码 `0x100 | rawKind`。该路径仍要求有效的 device provider；本桥先核 raw 地址精确对应 registry 中的 live BWDevice，再检查 header 可读且 serial 身份一致。null、未初始化、已 finalize、外来地址、不可读内存或复制了存活 serial 的伪对象均明确拒绝。pool 和 SetStorage 不属于此前置条件，getter 不调用 Metal。

该普通颜色结果不随 samples、尺寸、其余 target 或不相关 flags 改变；samples 和 fast-clear 虽先构造参数，最终常量槽不消费它们。BUFFER 和 linear 仍先走 pitch 规则；`DEPTH16_PREFER_FAST_CLEAR` 的类别选择作用属于 depth 路径。metadata 查询因此不会限制 GPU 创建范围：不支持的 MSAA、3D、BC 解码和 sparse 等仍由原 Initialize/映射入口独立拒绝。这里的未压缩指未请求 GPU 硬件压缩，和 BC/ASTC 数据块压缩格式不同。

Texture 查询从已初始化对象的不可变 Builder 快照取值，并沿用对象、pool generation 和 device 生命周期检查。随后改写 Builder 不会改变 Texture 的类别；已初始化普通 block-linear 对象的 `0x1fe` 与原 Texture getter 的已保存 raw kind `0xfe | 0x100` 一致。硬件压缩请求、下述已证子域之外的 depth/stencil、原无效或未知格式的原生类别继续明确拒绝；停止信息仅记录 target、flags、format、samples 等有限元数据。

两个现代 RawStorageClass、sparse 和 ZCull 仍未开放；后续 physical/virtual pool 与 MapVirtual 的有限接入范围见 [MEMORY_POOLS.md](MEMORY_POOLS.md)。公开固定上游的 RawStorageClass=1 没有本目标 ABI 证明；其 ordinary-pool `0x100` adapter 也明确属于实验策略。本桥不采用这些未经证明的替代答案。原字节合成 oracle 验证 288 组 pitch Builder、256 组 Texture 已保存 raw byte，以及 4 个需要原生 ChooseKind 的分支边界；这些仅证明元数据，不证明 driver 映射或游戏运行。

`tests/test_botw_storage_class.py` 将各类 metadata 与 GPU 能力分别验证：113 个原有效颜色格式覆盖 samples、target 和不相关 flags，每种可写/真实只读页面各 10,170 次，保留 canary；实际 Metal 私有/linear 纹理 getter 覆盖初始化快照和生命周期。原表无效格式、未支持的 depth samples/压缩请求及无效 device 都受控拒绝；metadata 成功后不支持的 GPU 创建仍拒绝。该验证不表示游戏已跨过下一停点或可玩。


## Base-v0 单采样 depth 的查询合同

新增的 `texture_storage_policy.h` 只适配原版 metadata。固定 `subsdk0` 的有效格式记录与 GK100/GM200 两个 HAL 的完整无压缩分支共同证明：原始 samples 字段为 0、没有 `COMPRESSIBLE` 时，`DEPTH16/DEPTH24/DEPTH32F/DEPTH24_STENCIL8/DEPTH32F_STENCIL8`（`0x32..0x36`）的 raw kind 分别为 `1/0x46/0x7b/0x46/0xc3`，编码 class 分别为 `0x101/0x146/0x17b/0x146/0x1c3`。原始 samples 为 1 或 2 已涉及原多采样布局，不能当作 samples 为 0 的同义值。S8（`0x31`）的两个 HAL 结果不同，StorageClass 仍拒绝；其旧 StorageSize/StorageAlignment 行为本批没有扩大或收窄。

BUFFER 或 flags `0x50` 的 pitch 分支仍优先，不读取 provider。其余五格式的 class 查询要求精确 live device。原 ChooseKind 的无压缩子分支不消费 target 或 fast-clear 参数；除了决定 pitch 的 `0x50`、决定压缩的 `0x8` 外，其余 flags 不改变此 metadata 结果，包括 `DEPTH16_PREFER_FAST_CLEAR`。这不放行相应的实际 GPU 创建。原 Builder Size 在 GL name 非零时先返回 0；新 depth Size 保留此前置短路，不读 provider 或计算 layout。原 Class/Alignment 不消费 GL name，仍按各自 provider 合同查询。packagedData 对这三项 metadata 是不解引用的 opaque 指针，不能据此创建或导入 GPU 资源。组合深度格式、GL/packaged GPU 导入、fast-clear GPU 创建和虚拟 depth 映射保留原明确拒绝。

这五格式的 StorageAlignment 与已实现的 device query `0x3a` 共用 `pool_policy.mappingPageBytes`（65536）。StorageSize 在既有完整上游 GOB 布局之后，检查溢出并将 allocation size 向该页取整；mip/layer extent、GOB payload、上传和别名范围继续使用原 layout，不把 allocation padding 当成 texel。Initialize 校验 pool-relative offset 的 64KiB 对齐，不要求 Host39 的 CPU 虚拟地址绝对 64KiB 对齐。成功初始化的 Texture 保存 64 位 size 快照；原 getter 的 `ldr w0,[x0,+0x78]` 则以有符号 int 返回低 32 位，桥使用显式可移植的位投影。后续修改 Builder 不改变 Texture 的 class 或 size，原 pool/device/generation 生命周期仍有效。

metadata class `0x101` 等不会开放 MapVirtual、vendor 或 GPU resolver 的 raw kind 集合。本批这些路径仍只接受已证 `0x100/0x1fe`。MapVirtual 正常 policy 拒绝信息追加已快照请求的 index 和 storageClass，失败不发布映射或代际。每轮图形生命周期前四个已成功 publish 的 physical pool 输出 `physical_pool_initialized=true kind=... flags=... bytes=... segments=...`，不含地址或内容、不追加 VM 查询；失败不计数，完整 shutdown/init 后重置。此 trace 才能独立证明对应 pool 的发布成功，旧错误消失本身不足以作此结论。

测试对五格式分别验证 class/alignment/size、真实只读 Builder/canary、raw samples/压缩边界、大小和多层/mip rounding、64 位 Builder size 与 int getter 的位投影、普通 DEPTH16/DEPTH32F 对象快照及 stale pool；真实 Metal NoCopy 的销毁 ACK 与图形重建验证 trace 上限和 reset。它们不证明原游戏首帧、当前手机已跨过下一停点或可玩。
