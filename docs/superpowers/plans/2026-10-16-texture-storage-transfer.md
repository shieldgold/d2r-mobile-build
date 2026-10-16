# 纹理原址所有权迁移 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让固定 base-v0 的纹理按值转移完成后，宿主不再永久占用已经退栈的临时原址，同时保持纹理资源、生命周期和其他写入保护。

**Architecture:** 在现有导入解析中接入版本限定的 memcpy 薄适配。独立 ARM64 入口先传出正常 LR 和 w23；完整实际加载上下文、返回点、长度和 ownership 位均匹配后，执行图形层 preflight、锁外原 SDK copy、epoch/token commit。其他调用仍执行原 SDK memcpy；不修改 Guest 重写产物，不读取旧栈内容判断过期。

**Tech Stack:** C11、Apple ARM64 汇编、Objective-C ARC、Metal、Python unittest、固定 Git 源码快照和已有 iOS 构建/签名工具。

---

## 固定证据及边界

- 起点提交 `ca3cea3a3b7bebf9203016eae371ea0304758263`，Mac 实际错误为 SamplerInitialize 与 BWTexture 的192字节登记部分重叠。尚未证明具体现场指针属于下面的静态路径。
- 原 main `A37700` 的成功转移路径在 `A37824` 调用普通 memcpy，`A37828` 返回；现有正常参数为 dst、src、C0，w23 保存 ownership。调用者成功后跳过临时 TextureFinalize 并退栈。
- 原 TextureFinalize 可关闭 NvRmGpuMapping，不得改成 Sampler 的空 Finalize。
- 两个固定上游复用审计已完成；它们按值保存纹理，没有可直接引入并保留当前严格 raw-span 的迁移模块。
- 本批只迁移 host-owned 存储登记。纹理 identity、pool generation、descriptor 引用、GPU 内容与 private authority 不变。
- 不承诺原 memcpy 或 copy+登记的字节事务；不回滚 copy 已完成的字节。失败时不得提交登记迁移或释放纹理资源。
- 不持 graphics/lifecycle 锁执行可触发故障恢复的原 SDK copy。不新读 oldraw、dst+C4、调用者栈帧或扫描 VM 来推断所有权。

## 文件职责和并行分工

运行时 owner：

- 修改 `native/botw_host/runtime.c`：固定导入/实际加载上下文审计、正常旁路、局部故障恢复与 token 清理。
- 修改 `native/botw_host/entry.S`：只从正常寄存器取 LR/w23，保持标准 ARM64 ABI。
- 创建 `native/botw_host/texture_move_profile.h`：固定版本、原 memcpy 定义及加载上下文合同。
- 创建 `tests/test_botw_runtime_texture_move.py`：人工 ARM64 入口、普通旁路、错误上下文和故障清理验证。

图形 owner：

- 修改 `native/botw_graphics/graphics.h`：host-only preflight/commit/abort 与 opaque token 合同。
- 修改 `native/botw_graphics/graphics.m`，创建 `native/botw_graphics/texture_transfer.inc`：正常图形边界、64个有界 pending 记录和生命周期清理。
- 创建 `tests/test_botw_texture_transfer.py`：真实 BWTexture、精确 source primary span、目的区域检查、epoch 与单次 token。
- 更新 `native/botw_graphics/TEXTURES.md`、`README.zh-CN.md` 和 `THIRD_PARTY_LICENSE.txt`，保持其他 NVN 对象写入门禁不变。

双方已固定的 host-only 接口如下；返回1表示成功，0表示具名失败，不触发 NVN callback。pending 记录强持源对象；它不构成 range reservation。

```c
typedef struct { uint64_t opaque[8]; } botw_graphics_texture_move_token;
int botw_graphics_texture_move_preflight(const void *source, void *destination,
    size_t bytes, botw_graphics_texture_move_token *token, char *error, size_t capacity);
int botw_graphics_texture_move_commit(const botw_graphics_texture_move_token *token,
    char *error, size_t capacity);
int botw_graphics_texture_move_abort(const botw_graphics_texture_move_token *token,
    char *error, size_t capacity);
```

两个新增 production include/header 联合使 native95变为97，仍须实际枚举核对，不修改既有100上限或来源 schema。iOS 的 push/pull_request 各加精确 `tests/test_botw_texture_transfer.py`；runtime 测试已由原 `test_botw_runtime*.py` 覆盖。

root：维护本文及 `docs/BOTW_NATIVE.zh-CN.md`，核对新增测试的 CI paths、native 来源数量，固定提交后运行原游戏、全套检查并保数据更新手机。独立 reviewer 分别检查合同和实现质量，不与原游戏争用 GPU。

## Task 1：先固定运行时真实失败

- [x] 在旧源码构建自有 fixture：固定模块位置和 memcpy 符号，提供错误的加载上下文。
- [x] 执行下面命令，确认编译成功而行为失败；不能把链接或语法错误算成 RED。

```sh
PYTHONPATH=scripts:tests .tools/botw-venv/bin/python -m unittest -v \
  test_botw_runtime_texture_move.RuntimeTextureMoveTests.test_unknown_loaded_context_is_rejected_before_import_interposition
```

已有 RED 为退出91和 `MISSING_TEXTURE_MOVE_CONTEXT_REJECTION`，收据保存在本机 ignored 输出。

## Task 2：实现和验证寄存器及版本入口

- [x] 汇编入口把 x30、w23 在编译器使用 callee-saved 寄存器前作为附加参数传给 C 分派。
- [x] 审计固定 main 的完整 transfer 上下文以及原 SDK memcpy 的符号位置、大小和实际加载指令；不匹配时拒绝启用。
- [x] 仅精确返回点、C0 长度及 ownership bit1 匹配时进入迁移协议。普通调用原样委托原 SDK 函数并保留返回值。
- [x] 为非匹配 RA、长度、ownership、未知 profile 和缺 provider 分别写行为断言。自有 ARM64 fixture 核对 x19..x29、d8..d15 的 ABI 保留范围、栈对齐和返回值，不执行游戏字节；未把这些断言扩称为 FPCR/FPSR 验证。
- [x] 匹配 copy 使用现有局部 callback 恢复边界；故障返回后先 abort token 再报告失败。普通旁路保持现有 SDK 调用语义。
- [x] 用同一测试命令取得 GREEN，保存调用 exit、源码 SHA 与实际 ABI 输出。再检查既有 callback/worker shutdown 路径的相关回归。

## Task 3：图形层的原址迁移协议

- [x] preflight 仅接受 host 表中 exact base、192字节、BWTexture 的 source primary；拒绝相同地址、部分重叠、冲突目的、错误类型或过期生命周期。host-owned 地址范围和登记检查在 copy 前执行；图形协议不查询 VM 或读 guest 字节。原 copy 的访问故障由运行时局部恢复后 abort，不冒称 preflight 已保证字节复制一定成功。
- [x] token 绑定本次图形 epoch、source identity/span 和 destination；失败不改变原 object/claim。token 不存可能跨 longjmp 悬空的调用者栈指针。
- [x] copy 完成后 commit 再查 epoch、source 和 destination 合同，只迁移 storage span；单次消费，拒绝伪造、重复、旧 epoch token。
- [x] abort 及图形生命周期结束清理未完成 token，不改变纹理私有 authority 或资源寿命。
- [x] 人工 fixture 的关键顺序固定如下，使用实际公开 host API 和 NVN 类型，不以私有状态字符串代替结果：

```text
Initialize(source)
preflight(source, destination, 192)
original_copy(destination, source, 192)
commit()
Get(destination) == original_value
reuse(source_storage, sampler) succeeds
Finalize(destination) retires the original texture identity once
```

- [x] 给失败分支保存完整 before/after host 登记和资源身份，比较不变；自有原函数替身验证精确委托、字节内容和返回值。生产原 SDK 的实际执行留给 Task 5 原游戏检查，不把静态摘要核验算成原 SDK 执行。
- [x] 先在旧实现复现失败，再运行 CPU GREEN。真实 GPU 只验证转移后的公开 descriptor 采样、源槽重用与一次释放，开启 Metal validation。

```sh
PYTHONPATH=scripts:tests .tools/botw-venv/bin/python -m unittest -v \
  test_botw_runtime_texture_move test_botw_texture_transfer
```

## Task 4：独立审查和精确提交

- [x] reviewer 先核固定原控制流、普通旁路、来源/version gate、故障清理及资源所有权合同，再核代码可维护性、token 边界、线程和异常行为。
- [x] 用最终冻结来源 SHA 运行最窄独立 CPU/GPU 检查。审查报告区分 fake Metal、真实 Metal、原游戏和手机。
- [x] 核对 native 来源计数仍不超过100、来源 schema 保持，CI push/pull_request 均覆盖新增测试。
- [x] root 对实际 changed set（含未跟踪新文件）逐项核对；只 stage 两个 owner 和 root 的明确路径。单元验证及独审完成后提交并推送 `botw-archive/codex/botw-native-preflight`。实际提交为 `bfd9c8241f56020b41f4b4d0b3d4a2ba6415c5ac`，14个明确路径、11个 owner SHA 与 native97 已核对。

## Task 5：固定提交原游戏与手机验收

- [x] 从精确提交构建普通 Mac 前端，验证 binary、200个本地输入 SHA 和原 RomFS 链接后运行原游戏；完整收进程退出，再读取保存日志。bfd 实际触发一次所有权迁移，旧重叠消失；下一个零大小查询为一维数组，随后停止在描述符索引检查，没有可玩画面，继续主线。
- [ ] 若出现第一帧，先核验真实图像、操作、声音和存档重开，不让全套 GPU 测试争用设备。若仍有 fatal，记录新的实际边界并继续主线。
- [x] 执行固定源码全套检查和精确 head 的 iOS/Linux CI；只使用官方核验通用工件，本机加入固定私有 Guest 后签名。bfd固定957项（956通过、1硬件条件跳过）及两CI均成功。
- [x] 对当前连接的 iPhone12/iOS18.1 原应用保数据更新。分别核验安装位置、普通启动、新日志前缀、UID、RomFS 大小/mtime、shader 清单和权限。bfd普通启动PID1093，真机同样实际完成一次转移，随后停止在描述符索引检查。
- [x] 继续使用分块日志证据工具，不截断现有累计日志。安装、启动、合成测试和合成像素都不作为可玩验收。bfd累计日志38,532,611字节完整核验，资源保留；继续一维数组及后续实际停止点主线。

按用户既有“继续开发并安装到新手机”的授权在本会话执行，不另开用户任务或等待重复许可。步骤完成后更新复核记录；本计划不替代实际结果。

## 后续实际停止点

`28c0b30` 的单层一维数组支持已固定并普通复跑原游戏。一次纹理迁移成功后，下一次 preflight 因目的地与登记 NVN storage 重叠而拒绝；本次没有旧的一维数组零大小查询或描述符索引错误，仍无游戏画面。原 main 的目标替换路径是否调用 TextureFinalize 取决于旧引用计数及 ownership，不能预设所有覆盖前都会 Finalize；也不能据此假定这次实际冲突允许覆盖。先给既有失败分支增加有界 host-owned 对象类别、跨度与精确匹配诊断，保持全部门禁、不得读取旧 guest 内容或扫描 VM。取得普通原游戏实际冲突证据后，再审计最窄生命周期适配与回归范围。

## Task 6：已迁移的完整目的槽替换

- [x] `190d33c` 已精确提交、推送并普通运行。原拒绝保留，有限上下文确认 actual 为 exact192、owned BWTexture primary、move epoch1、relative offset0；不把它称为旧资源已死亡。
- [x] 原 main 的 refcount/ownership 替换分支与运行时完整 profile/LR/nC0/ownership bit0 gate 已独立核验。无需改 runtime、Guest 或已有 raw get/Finalize。
- [x] 只允许唯一、不同于 source、此前已迁移且 epoch>0 的 exact192 owned BWTexture primary 目的；其他类、部分覆盖、未迁移、selected alias、源目标交叠仍拒绝。
- [x] pending host record 强持旧目的及其 key/span/epoch，64字节 token ABI 保持。commit 重核 source、目的、epoch、lifecycle 和其他冲突；clone 表内完成可抛的删除/写入，单次发布登记，abort/失败不改当前全局登记。双方epoch在发布后推进，MAX前拒，其他pending source检查不扩成range reservation。
- [x] 只交接 primary span，旧 objects/value、descriptor/recorded 引用、MTL资源、texown/flush与private authority保持；旧 alias 的原 Finalize 不能删除新 key 的目的 span。没有原 Finalize 的旧资源可能积累至 shutdown，不能拿64个 pending token上限冒称资源上限。
- [x] 新功能先在旧实现编译成功后3项真实失败，最终18项通过（6.791秒，CPU16、GPU2方法6实际模式），覆盖成功替换、旧新公开 descriptor 像素、private authority、旧 alias Finalize、部分/错误类拒绝、epoch变更、clone异常与abort。真实GPU、CPU/fake Metal和原游戏分别记录。
- [x] 三文件freeze3独立审查 CLOSED：CPU16项4.607秒、GPU2方法6模式2.266秒，全部通过，SHA前后稳定，native97保持。
- [ ] 精确提交推送；普通原游戏确定下一实际边界，固定全套与CI通过后保数据更新已连接手机。仍需真实画面、操作、声音、存档重开验收。
