# 内存池 ABI、页策略与 virtual registry

B 接入复用完整 registry，并以同一 resolver 将可消费范围绑定为真实 `MTLBuffer + offset`。新 physical/virtual 只支持 coherent physical backing、同一 physical 连续且 raw kind 相同的范围；登记成功不代表任意碎片范围可供 GPU 使用。普通池保留既有 copied CPU_CACHED 和 empty metadata 行为。

`0x100/raw0` 用于 buffer、代码、descriptor 和 linear texture；`0x1fe/rawFE` 仅用于原格式 traits 可确证、非 COMPRESSIBLE 的普通 color block-linear texture。physical backing 的 COMPRESSIBLE flag 不代表实现了压缩 texture、depth/stencil kind、fast clear 或硬件 page-table alias。

## 原 base-v0 ABI

`NVNmappingRequest` 是 40 字节：physicalPool+0、physicalOffset+8、virtualOffset+16、size+24、storageClass+32，最后4字节padding。原 subsdk0 的 virtual map 循环以0x28步进；storage class在0xc4694清bit0x100得到raw kind。

`nvnMemoryPoolBuilderSetDefaults` 原0xb4c04只写memory+8/size+16为0、flags+24为0x21，保留device和其他字节。桥先验证原20字节写入跨度及真实VM权限，再一次性写入，不读取device指针或要求其已初始化。只读/不可访问、逻辑验证拒绝和地址溢出均在写入前失败；不会因为builder尾部不在可写页就多写或读它。

静态来源固定原 NSO SHA256 `58b6791aff6e5cb509c5347c1dc41d01a32c0a298c7f5da0c88ea56fc8a6eb17`，initialized image SHA256 `065cfc8fc879b60d0ee4b08f169a64424b62ed57366f97d2ce053dd8b0c8448c`。没有执行原指令或检查运行中的游戏内存。

## 四种不同的地址/页合同

`pool_policy.h` 提供共同的纯策略值：

| 合同 | 当前值/来源 | 边界 |
|---|---|---|
| CPU backing最低对齐 | 4096 | 原0xb4e30..38；不等于Metal NoCopy宿主页 |
| ordinary pool size | 4096倍数 | 原0xb4e4c..98的ordinary分支；本批不改变既有生产Initialize行为 |
| NVN physical/virtual size与Map granularity、device query0x3A | 65536 | 两固定上游Tegra实测表与完整vpool的guest兼容策略 |
| logical token preferred窗口 | 小于1<<40 | 上游host reservation偏好，不是硬件GPU VA位宽；strict入口不接受above-limit fallback |

原query0x3A不是立即数：0xb353c读取device+0x78；DeviceInitialize经0xb6c64的`NvRmGpuAddressSpaceGetInfo`读取返回结构+0x14并保存到该属性。65536的数值证据来自固定D2R `9de307ecaff42b3c9d45e58992c848ee89b8f6a1` 与BOTW `5f673ae2ea4f90ad46403aa6fbe13537fb557271` 的Tegra实测表，以及BOTW vpool明确的页合同，不能描述成原NSO常量或本机A14实测。

Metal NoCopy还要求真实宿主页对齐、连续的单一VM allocation和正确borrowed lifetime；kernel的Host39属于CPU/HIPC地址策略。这两者均不由本页策略替代。NVN地址容器是uint64；上游preferred40bit和当前storage-address-low驱动字段也不能用来证明原GPU VA位宽。

## 完整模块与薄port

`vendor/botw-vpool/SOURCE.json` 固定来源和每项port，完整保留interval、批事务、lease、generation、tombstone及差分/并发测试。原baseline的class0/100和unmap忽略class/physicalOffset仍由baseline测试覆盖；native薄adapter的纯校验只接受100/raw0和1FE/rawFE，unmap的physicalOffset必须0且不消费storageClass。未知class不因为physical COMPRESSIBLE就放行。

port保留span的raw kind，使不同kind不能coalesce或被判断为同一个连续GPU消费范围。strict reservation失败不发布owner/token/generation，并清理未publish宿主reservation。旧入口仍覆盖原fallback行为；strict窗口属于桥接 host 策略，不是 A14 GPU VA 位宽实测。

CPU lease只保证当前映射和physical CPU字节寿命，同线程获取释放，不是GPU fence，也不是最新private texture内容证明。module没有Metal绑定、脏数据协调、GPU use pins或跨buffer单槽scatter；不允许把PROT_NONE token解引用或将STALE/hole回退成普通CPU指针。

## 生产消费者与错误事务

| 消费者 | 合同 |
|---|---|
| buffer slice、copy、vertex、uniform、storage | 录制保存 logical pool/代际；submit 解析当前 raw0 范围，实际传入 physical Metal buffer/offset；physical 不能直接作为 GPU address |
| texture/base/selected view | virtual Initialize 可只建 metadata；实际使用时解析当前 kind/physicalID/覆盖 range generation，重建真实资源并保留 view selection |
| shader code | 保存初始化 device、完整 shaderData/control 快照及 code 来源；submit 重核当前 physical identity/hash/device；active Program 的任何 GPU code 写及受影响 Map 均拒绝，非代码页 remap 不受无关全局 generation 影响 |
| texture/sampler descriptor | canonical physicalID/offset/domain 槽保存语义及32B witness；virtual alias 共享同槽；P→Q 查 Q 注册语义，不能搬 P entry；原始 CPU 变更或 GPU 写使旧语义失效，须 Register，未实现 raw descriptor 解码 |
| command/control memory、counter | 需要真实 ordinary CPU 可访问后备；不得将 virtual token 当 CPU pointer。counter 还要求 coherent、16B对齐、真实 timestamp capability |
| scratch | Metal-private lowering 不访问外部 NVN scratch bytes；保留 device、logical bounds/代际与独立 lowering proof 检查 |

所有 submission 先在独立 replay snapshot 检查当前映射、canonical alias/私有权威、当前及历史 write intent、code/descriptor 冲突、反射 size/alignment/type 与 render state，然后写 CPU arena，再封存同键范围快照并 encode。counter 的分段 commit 不会跳过后半批静态错误。未知 raw kind、碎片 GPU 消费、FE raw buffer、virtual depth/压缩纹理及丢失的 private GPU 权威均具名拒绝。

ordinary copied cache 仍需要既有 Flush/Invalidate 同步。代码匹配和提交校验读取实际 GPU buffer 中的字节；SetShaders 先等待先前 GPU producer，不自动刷新或失效 guest CPU cache。新 physical 必须满足实际宿主页范围、单个 RW VM region、全范围逻辑访问及宿主 `maxBufferLength`；virtual 只保留 logical token，不分配同尺寸 Metal buffer，也不受该 buffer 长度限制。

旧 ordinary copied pool 的 private texture 导入仍读取 canonical CPU cache，不提供 GPU producer 后无 Invalidate 的自动导入同步。counter 分段会清除当前 pending buffer write 列表，不能声称 copy→counter→copied-private import 一律前置拒绝；本批 coherent physical/virtual 的验证不涵盖这项旧缓存限制。

Map 请求先验证 count/全数组可读性再分配并读取一次快照；整个 batch 校验后才执行 interval 事务。桥接每批上限4096请求，两份40B快照共320KiB；该上限不是 NVN 硬件限制。count0不读数组，unknown class、unaligned/bounds/unmap-offset错误或分配失败不发布 span/generation。

## 受保护的宿主复制

runtime 与 graphics 共用 `botw_host/protected_copy.h` 的 Mach 复制原语。图形的 private upload snapshot、通用读取、通用写入与 block-linear run 写入保留原 logical、authority、dirty preparation 和已有 snapshot 顺序；实际复制先校验完整 source READ/destination WRITE VM 跨度，再按4095字节上限及两侧宿主页边界分块。数值 VA 重叠选择 memmove 方向，所有 Mach 错误和短读取仍受控返回；没有新增整幅 snapshot 或裸指针复制。

4095 是桥接的保守分块上限，依据已审公开 XNU small kernel-buffer 分支和自有映射实测，不是 OS 公共 API 的“不分裂 VM entry”保证，也不修复已有分裂。NoCopy 的单 VM region 硬检查继续有效；ordinary exact 的旧 RO/NONE metadata 准入不能作为满足 Apple 真实 NoCopy 合同的证据。每次复制仍要求调用方维持操作数所有权与寿命，不承诺并发 remap 的事务，也不对不同数值 VA 的任意 physical-alias 重叠提供无条件 memmove 语义。

## GPU 与借用后备寿命

CPU lease 同线程获取释放，GPU command 使用独立 resource snapshot/pins；每次 commit 在提交前登记，提交后不再分配登记对象。completion/deallocator 不获取 graphics/registry 锁。已完成和错误 command record 在串行维护点回收，错误继续报告，不能无限积累。

Finalize 先置 retiring，等待所有已提交 GPU 工作，完成 checked dirty retirement，再撤销全部 base/selected view、descriptor、Program、queue/sync/window 与历史 range 对 Metal 后备的引用。NoCopy deallocator 只确认实际 resource destruction，不释放 guest bytes；最多等待1秒回执，确认后才撤销 registry/live 状态。超时保留受管理的 quarantine，不能允许 guest 释放 borrowed allocation。未 publish 的初始化资源也在创建前建立强引用锚，失败时由同一 quarantine 和 Shutdown 管理；registry 撤销与实际回执均完成才移除锚。

宿主使用 `botw_graphics_shutdown_checked`：仅返回1代表全部静止并已回收；0保留未回收 tables/GPU，不触发 NVN callback 或 abort。嵌套调用不清外层错误状态。legacy void API 仍保留兼容。runtime 清理失败保留内核/映射/运行锁，本批没有公开 runtime retry 入口，需要重启进程，不能描述为前端可恢复。

所有生产 Objective-C `.m` 必须启用 `-fobjc-arc-exceptions`；真实异常恢复测试已经证明默认 ARC 可留下未提交 command 的强引用并阻止 NoCopy 回执。所有相关 render/blit encoder 用 `@finally` 关闭，实际 native encode 异常不会通过释放未关闭 encoder 触发 Metal validation abort。

现 texown 的 retired claims 会合并并丢部分来源；本批保留这些权威，受影响 remap 继续拒绝。后续 writeback/discharge 不得按单一 serial 清除 merged union，也不能 reset 全局 claims。

## 验证边界

`test_botw_memory_pool_policy.py` 保留纯 flags/page/bounds/class、Defaults/RO/NONE/顺序测试；完整 vendor baseline、kind、strict token 与失败事务由 `test_botw_vpool_modules.py` 覆盖。`test_botw_memory_pools.py` 使用自有 Mach VM 和 Metal 协议替身验证 CPU adapter、lease/代际、错误事务、quarantine 与回执，不能当作真实 GPU 证据。

`test_botw_virtual_pool_gpu.py` 与 `test_botw_virtual_texture.py` 在真实 Metal 下启用 ARC 异常清理、ASan/UBSan 和 Metal API Validation，使用自有非零图案检查 GPU copy、vertex/uniform/storage、两队列、纹理采样/RT/padding、record→submit remap、descriptor/code冲突和失败前置。共享事件明确控制在途 GPU 完成时点，Finalize/Map 的等待采用有界协作线程；失败批先 Finish/drain 再检查 sentinel。真实 encoder 异常另用协议代理在实际资源捕获后注入，验证 finally 与 checked cleanup；与可静态预检输入错误分开。

这些测试验证桥接合同，不证明手机已经执行此版本、音频/完整画面或游戏可玩。A14 验收须记录同源工件与真实普通启动，不以 M3 结果代替。
