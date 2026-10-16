# 完整 REV1 native renderer 接口

此阶段把固定 Eden 的完整 renderer/ADSP 算法接入真实宿主端口，并提供正常 audren service 的 C ABI。已验证范围是本机 ARM64 macOS 的编译、全对象链接和自有 CPU 数据测试；尚未接入游戏、运行 guest/GPU、访问真实音频设备或验证 iPhoneOS。owned PCM 回调得到非零样本不代表 BOTW 游戏出声。

## 原件与依赖

`SOURCE.lock.json` 的 236 个原始 blob 保持不变。独立目标实际编译全部 67 个 renderer TU、2 个 ADSP renderer TU、原 render manager 和原 SinkStream；完整 voice/mix/effect/pool/update 算法保留。没有编译 emulator core、CPU emulator、JIT、SDL/Cubeb、其他 ADSP 应用或 Opus。`pool-memory.patch`、`native-host.patch`、`native-pcm-sink.patch` 仅在经 hash 核验的私有构建副本应用，vendor 原件不改。

`DEPENDENCIES.lock.json` 固定 Eden 原 cpmfile 指定的官方 fmt 12.1.0，tag commit `407c905e45ad75fc29bf0f9bb7c5c2fd3475976f`；15 个原始 fmt 文件、官方 archive SHA-512、Eden cpmfile 和 GPL-2.0/GPL-3.0 许可证逐字节锁定。fmt 使用完整 header-only 原模块，仍是 AOT/native 编译。逐文件 SPDX、版权和许可证完整保留。

`NATIVE.lock.json` 另锁全部 native/adapter/patch/test 代码及外部原 PCM、冻结 iOS 平台、canonical `audio-write-observed.patch`；配置时严格核验。`tools/prepare_eden.py` 同时核原 Eden、fmt、licenses 和补丁，构建缓存也校验每个实际文件。

## C ABI 与真实资源

纯 C 入口是 `native/audren_host.h`，ABI 2。configuration 必须带真实 `failed(failure_context,status)` producer；首次 fatal 只调用一次。此 callback 可能位于 worker，也可能位于 PCM 控制线程，只能 latch、唤醒独立宿主等待者或异步派发终端失败路径；不得重入 engine、同步 join/destroy 当前 worker、等待正在等待 worker ACK 的生命周期锁或抛异常。普通参数/IPC 错误可直接返回，不伪造 guest completion event。

- `host_create/destroy/set_active`：强配置、生命周期和真正暂停确认。destroy 有 live session 或注册 region 时返回 Busy。
- `register_region/unregister_region`：非零 process owner、逻辑范围、实际 backing、真实 retain/release。生命周期持续到最后 pin/loan。禁止虚拟或物理重叠注册；不能把 NoAccess 原指针冒称可读写 backing。caller 提供真实 VM alias/权限及 kernel owner，本组件不扫描 VM 或改变 VM 权限。
- `register_region_provenance`：启用 memory provider 时必须使用此入口注册 transfer，携带唯一 kernel 的稳定 backing identity/offset；不同 RW alias 指针不能代替源 backing identity。ABI2 的 configuration 尾部为 `memory_provider`，全零明确保持 registered-only、普通池 miss 返回原 MapFailed 状态。ARM64 configuration/provider/lease 的尺寸分别为 88/48/48 字节。
- `get_work_buffer_size/open`：仅接受正常 SDK 序列化的 `0x34` REV1/Auto wire，逐字段 little-endian 解析，拒绝 64 字节客户端对象。当前 native profile 为 48k/240 或原模块 32k/160，其余模式先拒。完整正常 `System::GetWorkBufferSize/Initialize` 执行后才发布 session。transfer 的逻辑地址、size 和真实 RW backing 都必须 4096 对齐，缺 owner/权限/资源失败，不复制成私有假共享 workbuffer。不同 session 的 transfer 不能重叠。
- `start/stop/update/get_info/set_rendering_time_limit/close`：保持原 InfoUpdater 的 Behavior→Pool→VoiceResource→Voice→Effect→Mix→Sink→Performance→Error 顺序。REV1 描述符长度/分段在任何原状态或 output 写入前检查。query 的 active 是 bool，SDK raw state 由整宿主依固定 service 原件映射。
- event 的 signal/clear/retain/release 必须全部存在。signal/clear 必须真正更新 nn::os 观察的宿主事件，零才成功。所有回调不得抛异常或重入同 Host/Event。调用 bridge 前须释放 IPC/kernel registry 锁。

`botw_audren_result` 按方法解释：GetSize/Open/Update 只有实际原模块返回 Result 时才有 `service_result_valid=1`，传递其原 raw Result。原 void control/value query 成功返回 `host_status=0, valid=0`，整宿主在动作/查询确实完成后按该 IPC 方法定义构造正常成功；不能把任意 valid0 当成 Nintendo 成功或全部 IPC 失败。所有 host 错误均非零并带诊断，原失败不吞为 Result0。

## 普通 voice/effect pool backing provider

provider 接在原 PoolMapper 使用的 RendererMemory pin 边界，不增加 Update parser。transfer attach 始终只认已显式注册的真实 workbuffer；普通 pool/span 先查完整注册区间，owner、权限、部分相交或越界错误立即返回。只有整个请求与现有注册/借用都不相交的 miss 才调用 `acquire(process_owner,source,size,access)`。provider 必须从整宿主唯一 kernel 的已提交 heap、真实源权限和 owner 获得共享 backing lease，不能绕过 NoAccess 洞或提供复制的假共享样本。

返回 lease 精确暴露请求视图，并持有真实 opaque owner；kernel 内部 host-page 包络也必须全部满足同 owner/源权限。发布前再次检查完整逻辑范围、实际 backing 指针范围和稳定 backing identity/offset 的物理重叠。并发获取同一合法区间共享已发布 pin，未采用的真实 lease 在锁外释放；竞争期间出现的新注册权限错误保持原样。最后一个 MemoryView/命令引用退出后才释放借用，provider context 的一次 retain 持续到所有 backing lease 结束。

acquire/release 和 fatal producer 均在 MemorySpace/RendererMemory mutex 外执行，不得抛异常或重入同 audren Host。release 非零保留真实 owner、元数据和 alias，进入 quarantine 并通知一次 terminal failure；Close/Destroy 的正常清理路径会实际重试，失败继续保留 owner。第一次调用的释放错误不会被并发成功重试覆盖。若 acquire 违约地在失败时仍返回 owner，该 owner 同样保留、显式报 SystemError，不能丢弃。若 release 违约地改变 owner 却报失败或保留 owner 却报成功，归属已经不确定，永久保留该 quarantine，避免再次解引用可能已释放的 owner。

普通池权限/映射拒绝沿用原 PoolMapper 的 OutStatus；若 Update 期间发生 terminal backing failure，则 C API 返回 host 错误、valid0，并由真实 failed producer 终止宿主路径，不把原整体 Result0 当完成通知。

## worker、暂停与关闭

manager/ADSP 线程捕获真实类型失败、锁存 Host fatal、取消 fatal mailbox 等待并唤醒宿主 producer；异常不越过线程边界。显式 Stop 的实际 PCM 错误透传；析构继续实际 join/关闭并记录错误。system 发布、移除和列表遍历用同一锁，启动失败撤销发布；Finalize 先取消发布和 quiesce，再解绑映射。

暂停先阻止新 render round、取消该轮真实 PCM WaitFreeSpace；ADSP 保留尚未处理的命令并返回该轮响应，manager 完整 round 离开 PCM 后才确认。没有消费回调也能完成 ACK。恢复建立新取消 epoch。整宿主应先调用 `host_set_active(0)` 等 ACK，再关闭真实 PCM/AVAudioSession gate；恢复先成功恢复平台 gate，再 `host_set_active(1)`。暂停不声称线程被系统 suspend，也不生成虚假 Resume。

Close 分阶段执行真实 Finalize→map/pin 清理→TransferPool detach→event close/release。失败保留 session owner 供 retry，不能卸载活 worker 的 Guest/backing。成功清空 `*session` 后，该 session 的 event retain、TransferView 和 mapping pins 已释放；无其他借用时 `unregister_region` 可成功，随后才能释放真实 kernel backing/VM alias。close 不能证明原 void PCM Dispose 成功；原实现失败时保留的 callback storage 不由此组件释放或恢复。

原 SinkStream 音量/downmix/upmix 算法保留，Native 仅承接已转换 PCM；FIFO/tag 退休只认原回调 consumed/dropped，write 成功不算播放。canonical observed-write 在同一真实平台锁内获取 before counters 并提交，避免外部 flush/resume 错归旧 tag 完成。render callback 不取 adapter/registry 锁。

## 构建与 owned 验证

生产目标 `botw_audio_renderer_engine` 必须由父 CMake 提供真实已存在的 `BOTW_AUDIO_RENDERER_NATIVE_PCM_TARGET` 和实际 patched `botw_pcm.h` 目录 `BOTW_AUDIO_RENDERER_NATIVE_PCM_INCLUDE`，缺失直接拒绝。外部原 PCM 必须按 ios-audio-output→audio-write-observed 顺序应用 canonical patches。iOS 由真实 SDK/整宿主工程接入，此目录不安装壳或设备构建。

本机 owned fixture 构建命令：

```sh
cmake -S experiments/botw_audio_renderer/native -B outputs/botw-audio-owned-native \
  -DBOTW_AUDIO_RENDERER_OWNED_UPDATE_FIXTURE=ON \
  -DBOTW_AUDIO_RENDERER_NATIVE_SANITIZERS=ON -DCMAKE_BUILD_TYPE=Debug
cmake --build outputs/botw-audio-owned-native --parallel 3
ctest --test-dir outputs/botw-audio-owned-native --output-on-failure
BOTW_AUDIO_RENDERER_SANITIZERS=1 python3 -m unittest discover \
  -s experiments/botw_audio_renderer/tests -p 'test_*.py' -v
```

owned fixture 把完整原 PCM 与冻结平台链接到自有 mock AudioUnit，绝不访问设备。每个 executable 用 WHOLE_ARCHIVE 强载全部 native 模块对象，避免只链接被调用的小部分 archive。验证覆盖完整 REV1 Update/短描述符写入前拒绝、真正 manager/ADSP worker 故障与 join、C ABI 资源/两 session/真实 Stop 错误和清理重试、实际满 FIFO 无 callback 的 pause ACK/恢复及非零 DeviceSink→PCM 回调、wire 边界。普通 pool provider 另验证锁外获取、权限优先、跨 alias 的 source identity 重叠、并发发布/回收、失败 owner quarantine、并发清理不抹首次错误、完整原 Update/Finalize 的 lease 与 Close retry。平台与 NativeSink 的独立合同也保留。ASan/UBSan/float-cast-overflow 只说明这些自有 fixture 的已执行路径。

正常 base-v0 SDK→单一 kernel g_h→SM/audren IPC、transfer 的真实共享 alias/权限和 runtime terminal-failure producer 由整宿主接续。原 1.9 native CPU guard 不删、不强接 REV15，不把任何链接或测试描述为游戏已出声/可玩。
