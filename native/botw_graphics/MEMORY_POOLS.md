# 内存池 ABI、页策略与 virtual registry

本批只接入完整 registry 模块和纯 metadata policy，尚未把 registry 链接到生产 graphics。`nvnMemoryPoolMapVirtual` 继续具名 unsupported，physical/virtual pool 初始化仍拒绝；没有因为可登记 CPU interval 而声称建立了 GPU 映射。

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

port保留span的raw kind，使不同kind不能coalesce或被判断为同一个连续GPU消费范围。strict reservation失败不发布owner/token/generation，并清理未publish宿主reservation。旧入口仍覆盖原fallback行为；strict窗口属于拟验收的host策略，不能冒称已在A14运行。

CPU lease只保证当前映射和physical CPU字节寿命，同线程获取释放，不是GPU fence，也不是最新private texture内容证明。module没有Metal绑定、脏数据协调、GPU use pins或跨buffer单槽scatter；不允许把PROT_NONE token解引用或将STALE/hole回退成普通CPU指针。

## 后续生产接入门槛

下一批至少同时完成统一resolver、所有buffer/texture/descriptor/code consumers的mapping依赖、canonical physical alias权威、dirty同步、完整submission preflight、异步GPU资源保留和Finalize等待borrowed backing。初期只支持同一physical连续、kind一致的实际Metal范围；碎片消费、未知kind及尚未保全的private GPU权威明确拒绝。

程序code remap不能继续使用旧翻译；descriptor storage remap不能把旧host entry搬到新physical slot。现texown的retired claims会合并并丢部分来源，后续writeback/discharge不能简单按texture serial清除，更不能reset全局claims。

## 验证

新测试为`test_botw_memory_pool_policy.py`（纯flags/page/bounds/class及真实CPU入口、RO/NONE/guard-page/校验顺序）和`test_botw_vpool_modules.py`（完整原算法及kind/strict窗口/失败事务port）。前者的entrypoint夹具注册合成host device，只调用CPU metadata，不创建Metal device或提交GPU工作。

这些测试不证明Metal映射、手机运行或游戏可玩。实际GPU绑定与设备验收属于后续启用批，必须记录真实设备属性和数据读回。
