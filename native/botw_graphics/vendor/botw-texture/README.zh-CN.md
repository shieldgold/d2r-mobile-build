# 纹理 CPU 存储与 dirty tracking 模块

源自 [TommyLau/botw-mobile](https://github.com/TommyLau/botw-mobile/tree/5f673ae2ea4f90ad46403aa6fbe13537fb557271)，固定提交 `5f673ae2ea4f90ad46403aa6fbe13537fb557271`。目录内 MIT 许可证及 `SOURCE.json` 保存原始路径与 SHA256。`nvn_texwrite`、`nvn_texown`、`nvn_texflush`、`nvn_dirty` 和 `nvn_hmap` 成组复用；已有 texture layout/deswizzle 模块不重复复制。

## 宿主接入

图形宿主只需包含 `modules.inc` 一次。它依次包含四个 C 实现，并以局部宏隔离私有类型、函数与全局变量；不要再将相同 C 文件单独链接。公开接口仍在各自头文件中，纯 C 独立编译也受支持。layout/deswizzle 依赖使用现有 `vendor/nvn/nvn_tex.*` 与 `vendor/tex/nvn_deswizzle.*`。

`nvn_dirty_configure_registration(registration)` 接收宿主注册函数，签名如下：

```c
int registration(int (*fault)(void *, int, void *),
                 int (*prepare)(void *, size_t, char *, size_t));
```

宿主返回 `0` 表示整个 callback pair 已发布。vendor 发布的函数为 `nvn_dirty_handle_fault` 与 `nvn_dirty_prepare`，连同其全局状态均保持到进程结束；同一个注册函数重复配置幂等，成功后不能替换。vendor 不安装全局信号处理器。未配置或配置失败时，不能登记范围、设置页保护或签发 CLEAN 证书；普通资源路径可保守视为 untracked。

新的宿主代码使用 checked API：

- `nvn_dirty_add_range_checked` / `nvn_dirty_remove_range_checked`：`0` 成功，`-1` 失败并填充 bounded error。登记不会接受不可读写的新页。退役失败保留 retiring 元数据，调用者必须保留 backing，恢复映射后重试。
- `nvn_dirty_prepare`：空写返回 `0`；非空非法范围、未配置桥或 owned 页解保护失败返回 `-1`。untracked 页保持原状态，调用者仍须独立验证实际内存权限并检查实际写入结果。
- `nvn_texown_claim_checked`：`0` 成功，`-1` 拒绝非法范围或内存不足。`claim_fresh` 保留 `1` 新 claim / `0` overlap / `-1` 失败的合同，包含分配失败。
- `nvn_texflush_note_checked`：`0` 已处理，`-1` 非法参数或分配失败；分配失败清除该 serial 的旧证书。`verify` 分配失败返回 UNTRACKED。旧 void 包装仅兼容已有调用者。

归属 claim 记录 GPU 独有的最新内容，不能用旧 pool 字节覆盖它。纹理或 pool finalize 后，retired claim 仍保留。仅在**整个图形 context 已停止所有用户、等待所有 GPU 工作完成、将丢弃其 Metal 存储**后，才能调用 `nvn_texown_reset_quiescent()` 和 `nvn_texflush_reset_quiescent()`。这两个函数不解除 dirty 登记；context 销毁必须逐一成功退役 dirty 范围后再释放 backing。

## dirty 薄适配

保留上游 canonical host page、共享 owner、generation、两 bank reader drain 和保护/退役状态机。去掉其私有 `dm.h`、自动 crash hook、stdio 错误退出；正常错误均受控返回。

修正上游 `PG_NONE && gen != 0` 无条件吞 fault：页曾被解保护不能证明现在仍可写。Darwin 路径查询实际 Mach VM protection，仅在 owned READ 已确实恢复 RW，或另一个线程已将同一页恢复可写时返回 handled；查询失败、PROT_NONE、无归属与读 fault 返回未处理，保留宿主现有信号处置。BUSY 和重新保护竞态会重新核对 canonical state；PG_NONE 的权限查询在 CAS 占有 BUSY 期间完成，防止一次完整的 PROT→NONE ABA 使合法 prepare 被误拒。callback 本身不调用 allocator、stdio 或用户态锁；使用 Darwin VM/protection API，不能将此行为当作跨平台 POSIX signal 合同。

caller 仍须排除外部 remap/protection 变更，并在释放前停止新用户、等待已有 upload / 系统调用 / GPU 使用完成。dirty reader drain 只保护跟踪元数据，不会替调用者 pin 一整个 GPU 操作。

## 验证

永久入口：`python -m unittest discover -s tests -p test_botw_texture_modules.py -v`。纯 C 算法在可用 clang 上运行；真实保护页测试需要 Darwin。所有 fixture 使用合成描述符、合成字节及独立 mmap，不依赖未跟踪的上游 checkout 或游戏数据。

- 上游 texwrite 373、texown 43、texflush 5,022 项检查；保持原断言主体。
- 上游共享页/退役/两线程竞争写入测试，每轮至少 20,000 次真实写入。
- 真实 ARM64 owned fault、两个已 fault 的 writer、PROT_NONE / 只读 / 读 fault / 未登记页的原处置。
- bridge 缺失/失败/重复配置、checked prepare、分配、保护与退役失败、retirement drain、共享和重新登记失效，以及确定性的 prepare/protect ABA 时序。
- 所有 ownership / certificate / hash-table 扩容分配点的失败注入、旧证书失效和完整 context 地址复用。
- ASan/UBSan、严格编译告警，以及与生产相同的 Objective-C 单翻译单元聚合。

这些是宿主存储和保护协议的验证，不代表游戏首帧、输入或存档已通过验收。
