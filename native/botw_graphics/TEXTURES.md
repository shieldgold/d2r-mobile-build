# Base-v0 纹理对象、视图与 CPU 存储

`texture_family.inc` 成组接入 TextureBuilder、TextureView、Texture 元数据与 CPU 读写，采样描述符、渲染目标和命令提交共同消费视图。CPU 存储算法来自固定 MIT 上游 `TommyLau/botw-mobile@5f673ae2ea4f90ad46403aa6fbe13537fb557271` 的 `hle_nvn.c`、`nvn_mtl.m` 及 `vendor/botw-texture` 整模块；后者保存原路径、SHA256、许可证和移植说明。已有 deko3d 布局及 block-linear 解排布算法继续复用，保留 zlib 来源。

## 原版 ABI 与范围

原版 base SDK 的 82 个相关入口中，76 个已有真实实现；现代头文件中另外两项 Strided 读写也有实际实现。以下六个 driver/sparse/ZCull 查询保留具名拒绝，不能将未经证明的 storage class 返回为成功：

- `nvnTextureBuilderGetSparseTileLayout`、`nvnTextureGetSparseTileLayout`
- `nvnTextureBuilderGetStorageClass`、`nvnTextureGetStorageClass`
- `nvnTextureBuilderGetZCullStorageSize`、`nvnTextureGetZCullStorageSize`

`texture_abi.h` 按本地原 SDK 可执行 oracle 定义 Builder 的实际 `0x64` 字节字段区和 View 的 `0x24` 字节布局，保留对象预算尾部及原版 padding。Builder Defaults 保留 device，尺寸和 samples 为 0、levels 为 1、swizzle 为 R/G/B/A；SetSize2D 将 depth 复位为 1。stride 是无符号 32 位截断值。Packaged layout 保存原版三个四位字段；只有已验证的 block height 会被 GPU 布局消费，未实现的其他字段严格拒绝。两个 MemoryPool getter 返回指针。

View levels 为无符号 8 位、layers 为无符号 16 位，depth/stencil mode 保留完整 32 位。未设置的 getter 仍写出存储值并返回 false。Compare 忽略未设置字段和 padding。采样和命令记录持有按值快照，随后改变 guest View 不能改变已登记的描述符或已录命令。

实际 GPU 路径限定为已验证的 2D/2D-array、单采样、有限 mip 链及可精确映射的 Metal 格式。数组显式单层视图可作为 2D render target；采样视图支持 mip/layer 子区间、0 表示余下范围、Metal 格式视图和 ZERO/ONE/RGBA swizzle。渲染目标使用选定视图的实际尺寸和 pixel format，包括 sRGB。未验证的 3D/cube/MSAA、数组 depth、组合 depth/stencil、BC 解码、GL 导入、packaged data 指针等保持受控拒绝，没有 fallback 纹理。

## 存储生命周期与一致性

每个纹理固定原 pool 指针、宿主 pool generation 和 device。pool finalize/同地址重新 initialize 后，旧纹理、描述符、render target、CPU 读写及实际提交拒绝 stale generation。Finalize 先检查 guest 对象可写，再撤销资源归属；失败时保留原对象和 GPU 数据。

block-linear 导入先通过公开 `vm_read_overwrite` 快照完整存储，再解排布上传。提交前的 hash、GPU-written 状态和 flush 证书保存在该 Metal command buffer 的独立 pending 表；只有真实 commit 后才发布。中途拒绝不会把未执行的上传缓存为完成。分配可能失败的归属准备发生在 completion group enter 之前，避免 shutdown 永等一个从未提交的 buffer。

CPU WriteTexels 使用完整上游 region/stride/block 检查和精确 GOB run 遍历。先快照全部源行，再预检整个目标跨度；通过公开 `vm_write` 写精确字节段，保留 padding、其他 texel、mip 和 layer。部分首次写入先导入未修改子资源。CPU ReadTexels 从实际 Metal 存储读回，行间填充保持不变。CPU_CACHED 非 coherent pool 的 flush/invalidate 也走受控 VM 拷贝。大块 `vm_write` 按 1 MiB 分段，避免 MIG 32 位计数截断。

GPU 私有的最新内容建立 `texown` claim。别名纹理、buffer 地址以及同 command buffer 的 pending 写入不能把旧 CPU 字节当成最新 GPU 内容；无法证明的别名消费明确拒绝。资源 finalize 后 claim 仍以 retired 状态保留，仅完整 context 停止 guest、等待所有 GPU、成功退役所有 dirty 范围后才清空。InvalidateTexels 实际回写选定的整层各 mip，保留 padding；FlushTexels 忽略 region，按原缓存/层选择规则执行真实上传或验证完整证书。GPU 写后缺少证书、CPU 改写或改写后恢复原 hash 均不能伪装为 CLEAN。

宿主在 init 后、创建任何 pool 前调用 `botw_graphics_set_memory_tracking_registration(botw_host_register_memory_tracking)`。桥原子发布 process-lifetime fault/prepare pair；普通 VM 写入先执行 checked prepare，纯 validator 不变更内存保护。未配置桥时不设置页保护、不签 CLEAN；CPU 权威的内容仍可真实上传，无法证明的 GPU 写后 flush 明确拒绝。checked dirty retirement 失败时，pool/backing 保留，不能继续释放。caller 必须排除外部并发 remap，并停止新用户后再销毁 context。

## 回归证据

`tests/test_botw_texture_family.py` 运行合成 base ABI/canary/生命周期回归及真实 Metal 子进程：非零 mip/layer 采样和 RT、格式/swizzle、sRGB 编码、CPU region/stride、invalid source、实际只读/NONE 映射、stale/alias、未提交中途失败重试及 Finalize 拒绝后数据保留。tracked 子进程使用真实 ARM64 页 fault，覆盖 clean、改变后恢复原字节的拒绝及 GPU invalidate 回写。

`tests/test_botw_texture_modules.py` 保留上游 5,438 项算法检查，并在 ASan/UBSan 下验证 checked run callback、分配失败、真实 fault、共享页、并发、retirement、无桥、权限查询失败和 prepare/protect ABA。既有 60 项图形回归另行运行。原版本地纯 ABI oracle 有 640 断言；它不包含一般 driver 布局的证明。

这些是兼容层与合成 GPU 回归，不能替代原游戏首帧、输入、存档重开或 iOS 真机验收。
