# BOTW 手机输入桥

`input.c` 将实际宿主输入写入 BOTW 基础版旧 SDK 读取的 HID 共享内存。
`BOTWInputOverlay.m` 提供 UIKit 多点触控双摇杆、Switch 常用按键、已连接的 Apple GameController 手柄输入与 60 Hz 采样。触控 A/B/X/Y 按任天堂的东/南/北/西位置；Apple 标准手柄 API 的 `buttonA` 代表南侧，转换为 Switch B。

无界面连接时不宣告控制器连接；UI attach 后存在真实虚拟触控控制器，无操作时所有按键/轴均为零。触屏和手柄的按键按位合并，每个轴选择绝对值较大的实际来源。设备断开、触点取消、应用失焦会释放输入。没有自动游戏按键、D2R 游戏操作、随机输入或配对更改。

## 接入

1. 创建一次 `botw_input_create()`，保持到前端与 guest 停止后再销毁。
2. HID 服务创建 kernel 共享内存（大小 `0x40000`，guest 只读），将同一宿主 backing 传给 `botw_input_bind_shared_memory`。绑定必须在 guest 开始读共享内存之前。输入桥不释放 kernel backing。
3. `hid` 的 supported style/ID 配置传入 `botw_input_configure`；style 与 ID 应由服务保存后一起传入。支持 Horizon style mask 位 0..4，但单独的左右 Joy-Con 不会被虚报连接；实际提供 FullKey、Handheld 或 JoyDual。
4. 在主线程调用 `[BOTWInputOverlay attachToView:metalContainer input:input]`，保持返回对象。overlay 自带显示节奏采样；空白区域 hit-test 透传。
5. 所有 guest 线程停止且服务销毁后、kernel 共享内存释放前，调用 `botw_input_unbind_shared_memory(input)`；它与采样互斥，返回后显示回调只写输入桥自有缓冲区，原 backing 可安全释放。
6. UI 销毁前先 `[overlay invalidate]`，再停止 guest/服务，并最后销毁 input/kernel。若使用其他前端，它必须调用 `botw_input_set_source` 和 `botw_input_sample`，采样时间使用 `botw_input_monotonic_ticks()` 的真实 19.2 MHz tick。

这只是输入桥的实现与 C 行为验证，不代表 BOTW 已成功进入游戏或已在真机验证玩法。

## 已核验 ABI

本地 `outputs/botw-native-base/ExeFS/sdk` 反汇编确认以下偏移（相对于解压后的模块内存，不是压缩 NSO 文件偏移）：

| SDK 指令地址 | 证据 |
| --- | --- |
| `0x2288e4` 开始 | Npad 0 指针为 shared memory + `0x9a00`，后续 slot + `0x5000`，Handheld 是第 8 个 slot |
| `0x225a8c` / `0x225aa8` | FullKey 可用样本计数位于 Npad + `0x40`，最新索引 + `0x38` |
| `0x225af4` | 环容量位于 Npad + `0x30`，索引用此容量取模 |
| `0x225b10` 至 `0x225b28` | entry stride `0x30`，原子序号位于 + `0x48`，状态位于 + `0x50`；读取前后检查序号一致 |
| `0x225d18` / `0x225da0` | Handheld 使用相同结构，ring 位于 Npad + `0x378` |
| `0x226230` / `0x2262b8` | JoyLeft 相同结构，ring 位于 Npad + `0xa18` |

共享内存 Npad header `0x28`、每种布局 ring `0x350`，7 个 ring：FullKey / Handheld / JoyDual / JoyLeft / JoyRight / System / SystemExt。每环 17 个槽位，最多公开 16 个历史样本，留一槽供写入；先发布完整 sample，再用 release 操作更新最新索引/计数。源状态和所有生产者调用由 mutex 保持一致。

独立公开交叉来源：[switchbrew/libnx v1.6.0 的 hid.h](https://github.com/switchbrew/libnx/blob/07cd134d245ca3138da6cb73a05d637ea88342eb/nx/include/switch/services/hid.h) 中的 `HidControllerHeader`、`HidControllerLayout`、`HidSharedMemory`。旧格式已与此 BOTW SDK 实际读取对照，未使用后期固件变更的共享内存布局。

通用 `NpadState`、按键与连接语义参考 MIT [d2r-mobile 固定版本 hle_hid.c](https://github.com/jhaizhou-ops/d2r-mobile/blob/e2a0c9a9b5d65f6cd505e967e4760e2745e90ca8/dm/host/hle_hid.c)。保留 MIT 许可于 `THIRD_PARTY_LICENSE.txt`，不使用其中 D2R 专属动作与脚本。

## 验证

`python3 -m unittest discover -s tests -p test_botw_input.py -v`

编译真实 C 核心的夹具验证：默认松开、按下/释放、轴钳位、方向按键位、触屏/手柄合并与断开、失焦清键、支持 ID/style 配置、外部共享内存绑定与所有权、并发解绑后释放 backing 及连续采样、17 槽环多次回绕/顺序、真实单调时钟与并发一致性。UIKit 与 GameController 部分需 iOS SDK 编译，并在 iPhone 检验实际触控与手柄输入。
