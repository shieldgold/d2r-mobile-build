# BOTW vpool 完整模块

固定来源和每个原文件的 SHA-256 见 SOURCE.json；保留上游 MIT LICENSE。模块复用完整 interval 事务、later-request-wins、split/coalesce、writer-preferring registry lock、同线程嵌套 read lease、physical registration ID、generation 和永久 virtual tombstone。没有另写区间算法。

当前经 `modules.inc` 的内部名称隔离接入单一 graphics.m TU；生产 thin adapter 和统一 resolver 另行验证实际 GPU 消费。本模块不创建 Metal buffer、不更新真实 GPU 页表、没有 GPU fence，也不拥有 guest backing 的生命周期。lease 只保护同步 CPU 解析，不能跨线程释放或用于异步 GPU retention；调用者持 lease 时不得进入 writer，fault handler 不能调用本模块。内部 static 名称隔离没有复制或改写区间算法。

`nvn_vpool.h` 使用 tracked `pool_policy.h` 的唯一页策略：guest mapping page 为 64 KiB，physical CPU storage alignment 为 4 KiB；ordinary pool size 的独立 4 KiB 合同由 host adapter 处理。64 KiB 的数值证据是固定上游 Tegra 实测表和 vpool 兼容合同；原 base-v0 query 0x3A 读取 NVIDIA address-space driver info，本模块没有把上游数值说成原驱动立即数。preferred token bound 是宿主策略 `1<<40`，不代表硬件 GPU VA 位宽，更不等于 runtime 的 CPU/HIPC Host39。

两个分配入口共用同一 reserve/register 实现。原 `nvn_vpool_virt_register` 保留其上界以外的完整64位 fallback，并通过 above 参数报告；`nvn_vpool_virt_register_strict` 明确限制整个 reservation 在 preferred window 内，不符合时释放未发布 reservation，返回 NOMEM，base=0，objects/generation 不变。不截断 token，不用 MAP_FIXED 覆盖现有映射。成功 Finalize 的 virtual reservation 有意保留到进程退出作 tombstone，避免旧 token 复活；这不是失败路径泄漏。

class0 和 class100 仍兼容原完整模块，二者为 raw_kind0；class1FE 原样保存 raw_kindFE，新增精确 class101/raw1（D16）、class17B/raw7B（D32F）与class146/raw46（D24/D24S8的Map/CPU元数据）；其它未知或原生硬压缩 class 拒绝。类型解析复用 `pool_policy.h`，不是按低字节蒙过 class。每个 span 暴露 raw_kind；contiguous lease 的 raw_kind 只在 contiguous=true 时有效。即使 physical offsets 连续，不同 kind 的跨度也不得作为一个连续 GPU 绑定；它们仍可进行同步 CPU gather。kind、physical identity、generation 都参与各自适当的合并/缓存规则，不能从模块接受 class1FE 推出 Metal 支持所有格式或压缩。

**本模块不是 base-v0 公开 API adapter。** 固定原测试允许 class0 和 unmap 的 junk offset/class，此行为原样保留；生产 adapter 必须另行执行 base-v0 的 strict1xx、class100/1FE/101/17B/146、unmap physicalOffset=0、device/object lifetime、flags/page/bounds 和实际 GPU 能力检查。physical COMPRESSIBLE 标志仅表示 backing 资格，不是纹理硬件压缩支持。模块允许多个 physical registration 的 backing 重叠，桥可采用更窄且明确的拒绝策略。

测试运行：

```
.tools/botw-venv/bin/python -m unittest discover -s tests -p test_botw_vpool_modules.py -v
```

测试仅用合成自有内存，无忽略目录上游依赖、游戏内容、Metal 或手机。原完整测试保留 3000 随机 batch、20000 remap×4 readers、4 KiB biased storage、实际 VM PROT_NONE、later-request-wins、nested lease/writer 排队和 finalize 后立即 munmap backing 的压力。新增测试覆盖 raw-kind 边界与代际、真实高 token fallback/strict 清理、未知 class 后部失败原子性、初始/扩容注册两个 allocator 失败点、两块 Map scratch 分配失败和 reserve 重试部分失败。独立 production object 的符号检查确保没有测试控制导出。

只有定义 `NVN_VPOOL_TESTING` 才编译故障控制与计数；没有生产环境变量开关。countdown=-1 关闭，非负值表示先允许指定次数再持续失败；测试必须在使用者静止时设置。高地址 floor 仍调用真实 mmap hint，拒绝落在 floor 以下的实际结果，不伪造地址、不 MAP_FIXED。统计的 live allocations/reservations/capacities 用于静止点断言；成功 virtual 的永久 tombstone 与 registry 保留容量会计入，不能期望每次 finalize 回到零。分配失败不得留下额外 allocation/capacity 或 reservation。

现 graphics owner 已接统一 resolver、canonical physical alias、submission preflight、异步 pins/fences、dirty/texture authority、cache invalidation 和 Finalize/quarantine，合同见 `../../MEMORY_POOLS.md`。本模块的 CPU 测试只证明登记/事务/lease，不构成真实 GPU 映射、启动或可玩性验收；深度和颜色格式的 Metal 表示由 texture family 独立限制。

class146仅登记普通深度标签，不区分D24与D24S8，也不读取/转换payload。生产CPUidentity可保留raw46，但GPU resolver在任何NoCopy物化或segment绑定前拒绝raw46（包括expected=-1）。vendor同步lease/gather不是GPU格式、aspect或压缩器；完整kind/interval事务算法保持，混合raw46/raw1仍不可连续GPU绑定。
