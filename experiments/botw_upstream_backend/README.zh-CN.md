# 完整 BOTW21f 后端实验组件

这是可复现的源码、编译和宿主适配归档。它尚未接入普通前端，没有执行此完整后端，没有 iOS、GPU、原游戏或可玩验收。当前生产后端保持原样；本组件不是第二套活跃 registry。

`upstream/` 保留 TommyLau/botw-mobile 固定提交 `21f026e0c7fd9fee575a1bfa8d925553bb4db88e` 的58文件完整图形闭包、3个外部宿主声明头和2份许可证，全部原字节保存。MIT、bcdec 许可和 nvn_tex 的原 deko/zlib 声明保留。SOURCE.lock.json 绑定文件、编译单元、宿主外部声明、诊断补丁的前后 SHA 以及完整编译配置。

在 macOS Apple Silicon 上运行：

```sh
python3 experiments/botw_upstream_backend/scripts/build_compile_only.py
```

脚本先核对源码锁，复制到 ignored 输出目录，只在副本应用四个直接诊断入口的拒绝门和 command-audit 配置入口的关闭门；所有 C/ObjC 单元使用统一 `compat_policy.h`，25个单元按 `arm64-apple-macos15.0`、`DM_NVN_M3=1`、ARC/blocks 编译，然后与真实 host_adapter 和系统框架做最终链接。它不会执行 `complete-backend-LINK-ONLY-DO-NOT-RUN`。`runtime_binding.c` 只编译当前 runtime 的真实声明映射，没有把 runtime.c 链进后端，不能称运行时整合已完成。

脚本另编译并执行自有 CPU 合同探针，只有 host_adapter 和明确的自有服务回调，没有后端对象、Metal device 或原游戏。其十一个模式覆盖配置、manifest 验证失败、写准备与注册失败、旧 void 回调拒绝、诊断禁用、重复配置、并发只读宿主元数据，以及错误回调返回后必须终止的受控子进程。最后一项预期 SIGABRT，不当作成功返回。测试验证的是这些回调合同，不能替代真实 runtime/GPU。另一个 CPU 探针仅链接完整 command-audit 模块与 host_adapter：实际验证关闭审计时所有公开 audit 入口不读参数、dlsym 计数0，同时正常 header/record 检查和自有内存 safe-read 继续工作，不执行任何 renderer。

`DM_BOTW_SHADER_MANIFEST` 是运行时环境变量，不是 `-D`。编译进程记录预期绝对路径，但不制造有效 manifest，也不调用 runtime 选择器。真正激活前，宿主必须调用完整原 manifest 解析器验证该路径；不接受未设值掉入 legacy loader，也不把37个 UI/font shaders 当作完整游戏覆盖。

适配层实现真实宿主边界：单调时钟、CAMetalLayer 引用、宿主自有模块表、写准备失败透传、带返回值的 dirty 注册，以及资源/等待元数据回调登记。`runtime_binding.c` 映射现有 `botw_host_prepare_guest_write` 和 `botw_host_register_memory_tracking`。原21f仍使用的 void prepare 无法表达权限失败，因此原两个 legacy 注册函数明确拒绝；后续必须整接当前已审 checked dirty 模块端口，不能用永远返回0的转换吞错。

兼容策略明确关闭 capture/observation/guest stack trace、测试替代 shader、跳 draw、legacy 表和相关默认启用优化。不能把“getenv 返回 NULL”等同关闭：TBDR、ZSKIP、UNWRITTEN_DC、XV、DEFER、BLOOM_Q、RT_AB、LONGFRAME 等按原条件显式返回 `0`，PSO_MANIFEST 为 `off`；presence-only 的诊断开关返回 NULL。四个直接 screenshot/burst/observation 入口在任何参数内容读取之前统一拒绝。新增 `cmda_on` 在 pthread_once 前依据统一兼容策略返回 false，禁止其 frame-pointer/hash 诊断扫描和 dlsym guest thread-dump；保同模块用于正常录制解析的安全读取/record 检查。`mtl_wait_state_dump` 只含宿主等待计数，adapter 仅保存回调，不自动运行它。所有源码触发点及逐条件结论由 SOURCE.lock 的 policy_review 记录。

后续先核对用户合法提供的1.9资源能否与21f原版本匹配；目前尚未确认匹配，不能宣称可以直接运行1.9。现有base-v0 NVN52.32是当前baseline，与21f的55.15、80字节 textureBuilder、16字节 View不同；base52访问器作为保留此baseline时的备用适配，不预先重写整族。无论版本选择如何，整套对象值复制/serial/snapshot与命令payload、当前分段NoCopy/销毁ACK/vpool/authority，以及整批资源检查都必须同组闭合。旧 no-op 构造器和 dummy texture/sampler 策略仍不得激活。abort/exit 已改为 host error 边界，但在真实 guest 回调逃出时的锁、ARC 和清理尚未验证。资源、输入、帧、音频和存档验收均未由本组件完成。

普通生产源码计数仍98。本组件不进入当前 packager/SOURCES；将来激活时按完整闭包替换旧66个图形来源，再显式核来源预算，不以实验目录或动态 include 绕开上限。

旧5f的69文件基线及完整编译/link-only/CPU收据已保存在 ignored 历史归档；本次升级逐个核对官方 exact Git-tree blob，并重新生成补丁基准与98个环境调用条件。变更范围见 UPSTREAM_DIFF.zh-CN.md，不仅修改提交字符串。

在已安装 iPhoneOS SDK 的 CI 主机另执行 `python3 experiments/botw_upstream_backend/scripts/build_compile_only.py --sdk iphoneos --output outputs/botw-upstream-iphoneos`。该路径以arm64/iOS18.0部署目标、M3完整分支编译并链接，未保护的较新API可用性诊断按错误处理；不会执行任何生成的程序，也不会运行CPU探针。这个步骤不生成IPA，不替代真机或游戏验证。本地没有iPhoneOS SDK时须明确失败，不能当平台跳过成功。
