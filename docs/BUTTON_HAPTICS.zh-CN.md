# 暗黑触屏按键震动反馈

适用于本次已安装版本的上游源码 `120fe55c02a5633476998a44f8c3c3c809fa48b0`（1.0.18）。本地修改采用小范围前端补丁和两个新增 UIKit 源文件，游戏程序、资源、着色器链及原有高价值掉落震动保持原实现。

## 行为

- 游戏内攻击、技能、回城、药水、功能、地图、技能组切换和难度选择等触屏控件，在有效的新触摸落下时触发轻反馈。
- 空技能槽进入绑定操作时也触发一次。摇杆、世界空白处、拖动、松手、取消及长按产生的重复操作不额外触发。
- 使用缓存的 `UIImpactFeedbackGenerator`，强度 0.55；50 毫秒内多个按键合并一次反馈，不改变实际游戏按键输入。
- 只在前台、主线程、支持该游戏的 iPhone 上调用反馈。iPad 本机没有触感马达，因此正常接受触控，但不会产生本机震动。参见 [Apple 的硬件支持说明](https://developer.apple.com/documentation/corehaptics/preparing-your-app-to-play-haptics)。
- 主菜单及原游戏面板中的普通指针点击不在此次游戏触控按键反馈范围内。

## 在对应源码上应用

以下操作针对干净的、版本完全匹配的 `upstream`；不要在含其他未提交改动的源码上强制覆盖。

```sh
test "$(git -C upstream rev-parse HEAD)" = 120fe55c02a5633476998a44f8c3c3c809fa48b0
test ! -e upstream/dm/ios/Sources/DmButtonHaptics.m
git -C upstream apply --check ../source-patches/0001-button-haptics.patch
git -C upstream apply ../source-patches/0001-button-haptics.patch
cp source-overrides/dm/ios/Sources/DmButtonHaptics.* upstream/dm/ios/Sources/
```

按项目原有源码构建流程生成 App。上游 XcodeGen 工程包含整个 `Sources` 目录，无需修改着色器、游戏资源或引入额外框架。安装时保持 `org.d2rmobile.app` 标识并覆盖安装，保留已有资源和存档。

## 验证

`python3 -m unittest discover -s tests -q` 在 macOS 上执行真实 Objective-C 反馈实现，以可观测的 UIKit 替身验证 iPad 不分配生成器、非前台/非主线程不触发、多指限频、恢复前台、异常时间、缓存复用和反馈强度合法性。该测试不等于实机振动感受验证；非 macOS 环境明确跳过 Foundation 运行测试。

原版触控模块另外验证：

```sh
make -C upstream/dm/ios/tests t_touch_controls t_skill_aim t_joy_travel
upstream/dm/ios/tests/t_touch_controls
upstream/dm/ios/tests/t_skill_aim
upstream/dm/ios/tests/t_joy_travel
```

2026-10-18：37 项构建工具/反馈测试通过，原版触控 48 个场景 / 4,463 项检查、技能拖动 82 项和摇杆 48 项检查通过；新增实现亦已用真实 iPhoneOS SDK 编译。使用 Xcode 27.1 / iPhoneOS SDK 27.1 完整构建通过，包版本为 1.0.18 / 1018.1，最低系统 26.0，支持 iPhone 和 iPad。构建器完成资源、Guest 及着色器链校验。后续安装及设备注册状态以本地安装记录为准，不由这些测试推断。
