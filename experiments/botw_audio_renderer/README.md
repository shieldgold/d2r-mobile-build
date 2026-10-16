# REV1 原生音频模块

第一阶段提供真实宿主平台合同，尚未把 Eden renderer 接入 BOTW。当前构建不会加载游戏、执行 renderer、访问音频设备或使用 GPU；测试只驱动自有 CPU 内存、真实宿主条件变量/时钟和原 PCM 的 mock AudioUnit 接缝。通过测试或链接不代表游戏出声。

## 源码与许可证

`vendor/eden` 是官方 [Eden 仓库](https://git.eden-emu.dev/eden-emu/eden) 固定提交 `d8c6c59b679867d2595fdc0953df033475e23380` 的原始文件。`SOURCE.lock.json` 记录仓库、commit、root/audio Git tree，以及每个文件的路径、Git blob SHA-1、SHA-256 和字节数。236 个 blob 包括完整 `audio_core` 快照（其中 renderer 149 文件、ADSP renderer 5 文件、audio-common 5 个头文件）、23 个已审依赖头文件、上游根 CMake 和许可证。`tools/vendor_from_receipts.py` 在写入前校验全部来源；它需要此前隔离审计的原始 receipt，不访问网络。

上游 `LICENSE.txt` 和逐文件 SPDX/版权原样保存。上游文件存在 GPL-2.0-or-later 与 GPL-3.0-or-later 声明；本组件新增平台文件使用 GPL-3.0-or-later。分发时须保留相应源码、许可证和通知。上游 CMake 仅作来源记录，当前不会执行，亦未引入 emulator `core`、Opus、SDL/Cubeb 或 CPU/JIT。

## 已实现的平台 API

命名空间为 `botw::audio`，错误使用 `Status` 明确报告。

- `MemorySpace::register_region/allocate/pin/read/write/unregister_region`：显式非零 owner；完整单区域边界与权限校验；拒绝虚拟或物理重叠注册；禁止地址溢出。外部 backing 要求真实 `shared_ptr` 生命周期，`get()` 等于起始字节。`MemoryView` 保留 backing，借用存在时拒绝撤销。owner 负责同步内容访问，注册锁只保护映射与生命周期；不改变 VM 权限、不扫描地址空间。
- `TransferPool::attach/view/detach`：虚拟范围和实际宿主 backing 都必须 4096 对齐，attach 真正持有内存 pin，`TransferView` 仍存活时 detach 返回 `Busy`。无 identity-map 成功占位。
- `HostEvent::signal/clear/wait_for/close`：manual/auto-reset 事件，真实互斥锁与条件变量；超时和关闭分别报告 `TimedOut/Closed`。最大纳秒时长表示无限等待，负数拒绝；close 唤醒所有等待者。
- `MonotonicClock::now_ns`：实际 `clock_gettime(CLOCK_MONOTONIC)`，失败不写出伪造时间。
- `validate_platform_options`：拒绝 audio command dump 和 guest stack inspection。后续 renderer 创建必须先经过此入口。
- `PcmOutput::open/set_active/write/set_volume/counters/close`：仅调用既有 `botw_pcm_open(1)`；原 PCM 保留 ring、gain、stop/flush、callback quiescence 和 disposal 语义。返回实际 accepted frames，回调消费另由原 counters 报告。stop/start 失败透传；不降级到 muted sink。`close` 沿用原 `void` 接口，只表示该 producer 已退休，不能证明 AudioUnit Dispose 成功；原实现失败时保留的 callback storage 不由本封装释放或恢复。

锁顺序为 PCM adapter mutex → 既有音频平台锁 → 原 PCM 控制。渲染回调不获取 adapter/registry 锁。内存 registry/pool 不在锁内调用最后 backing deleter；宿主事件不调用 guest。

## 构建与验证

平台目标 `botw_audio_renderer_platform` 使用 C++23，独立于 emulator core：

```sh
cmake -S experiments/botw_audio_renderer -B /tmp/botw-audio-platform -DBOTW_AUDIO_RENDERER_BUILD_OWNED_TESTS=ON
cmake --build /tmp/botw-audio-platform
ctest --test-dir /tmp/botw-audio-platform --output-on-failure
BOTW_AUDIO_RENDERER_SANITIZERS=1 python3 -m unittest discover -s experiments/botw_audio_renderer/tests -p 'test_*.py' -v
```

真实 PCM target 由现有完整宿主提供；启用 `BOTW_AUDIO_RENDERER_ENABLE_PCM` 必须指定真实 `BOTW_AUDIO_RENDERER_PCM_TARGET` 和原头文件目录 `BOTW_AUDIO_RENDERER_PCM_INCLUDE_DIR`，缺失即 CMake 失败。Python owned PCM 测试只读复制既有原 `botw_pcm.c/.h` 到临时目录，应用既有冻结 `ios-audio-output.patch`，链接既有 `whole_audio_platform.m` 与自有 AudioUnit mock，未改冻结代码或访问硬件。

已验证的工具链是本机 Apple clang 21 / macOS ARM64；尚无本机 iPhoneOS SDK 编译证据。

## 接续边界

原 69 个 renderer/ADSP TU 的 12 个 Core 平台依赖尚待薄 patch 接入这些真实端口；完整 Update、voice、mix、effect、pool 序列仍在原字节 vendor 中。当前 CMake 不把未移植 TU 或无操作平台实现伪装成完整 renderer。正常 audren service、REV1 Update fixture、完整 renderer 链接及 iOS 验证继续进行。

base-v0 客户端参数 64 字节与 Eden wire 参数 `0x34` 不是相同布局；本组件不做跨布局 `memcpy`。正常 SDK 序列化及 audren IPC 桥由整宿主接续提供。未知 IPC 或未实现平台行为必须失败；原 1.9 native CPU renderer 的版本检查仍保留。
