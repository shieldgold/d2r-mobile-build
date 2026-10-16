# BOTW 完整上游后端接入

用户要求继续原生移植、整体复用现有接口并在当前 iPhone 上实际游玩。实施单位改为完整后端及其消费者闭包；不再把单个停止点修正、安装成功或人工 GPU 测试当作接近可玩的证明。

## 已核实的差距

- 当前 graphics 核心的对象、命令录制和 Metal 回放仍由本项目实现，整模块复用主要集中在布局、解排布、格式、纹理脏区/所有权和虚拟池等局部。
- 当前手机实际 shader manifest SHA `21c9219383e66c9cdbe90d1159d032681340f21a4115a4a54e8b749f61385366`，37 个唯一着色器（8 vertex、29 fragment）只来自内置 UI/字体两容器；不是游戏场景的完整集合。active loader 还缺 compute 和其它纹理类型接入。
- kernel/IPC/HID 是设计派生及本地实现，不能统称整模块移植。AudioOut 已整核心接入，AudioRenderer 尚未实现。
- 固定 D2R 上游是特定 D2R 版本的完成实现；固定 BOTW 上游 `5f673ae2ea4f90ad46403aa6fbe13537fb557271` 本身仍无游戏画面/可玩验收。整模块复用可减少重复实现，但没有现成 BOTW iOS 成品可直接替换。
- 当前3a真机首错为CPU_CACHED池snapshot分配失败，发生在Metal工厂之前。此前b7的首错为Metal池分配失败；并发首错变化不能证明旧问题解决。诊断候选与后继功能开发分别留证，不把首错归因于尚未调用的功能缺口。

## Task 1：完整源码闭包与宿主边界

- [x] 固定 BOTW5f 整组 `hle_nvn.c`、`nvn_obj.h`、`nvn_mtl.m`、槽表/入口汇编、完整 shader manifest/runtime/observation，以及它们依赖的 tex/dirty/own/flush/vpool；逐文件保存来源、SHA 和 MIT 许可。
- [x] 在 ignored staging 完整编译各生产 TU并核依赖：23/23 Mac15/M3 编译成功；54源码、3原host声明头、2许可证共59项SHA稳定。真实外部host接口9项；尚未真实链接/运行或编译iOS，不将声明夹具当真实实现。
- [ ] 选择 iOS18 对应旧 Metal 路径，显式启用 BOTW shader manifest；不引入 D2R 专用 shader 表、游戏地址或静默替代着色器。
- [ ] 形成原对象、命令及回放同组闭包，禁止只换回放文件却保留另一套对象/record ABI。

## Task 2：版本对齐与完整资源链

- [ ] 优先验证本地1.9更新与合并资源，对齐上游1.9/SDK20.5.22/NVN55.15，再重新离线改写与签名Guest.framework；现base-v0保留为基线，不默认再造一套52.32/REV1后端。禁止把更新包文件名当已验证版本，或将1.9偏移套入现base-v0。
- [ ] 接回已验证的多 VM leaf NoCopy 分段、销毁 ACK、虚拟池及资源所有权规则。禁止丢弃已有正确修复、放宽未映射访问或伪造成功。
- [ ] 静态清点自有 RomFS 的全部候选 shader 资源，保留压缩/容器来源链和界限；将游戏场景、compute、绑定/格式事实纳入同一离线构建清单。
- [ ] 整组接入上游离线翻译、完整 manifest 与 runtime 加载器；未知版本/资源事实明确失败，不能以 UI/字体集合或人工 shader 替代游戏覆盖。

## Task 3：整体接入与验证

- [ ] 以完整生产闭包替换旧核心，保当前宿主公开入口、生命周期、错误回调与资源数据。重新核实际 provenance 文件集合及预算，不在旧核心上叠加第二套活跃对象模型。
- [ ] 按完整模块合同迁移必要夹具，核对象/record ABI、初始化失败不半发布、回调与清理、真实 buffer/texture/compute/indexed draw、shader 资源映射及呈现。
- [ ] 独立审查、精确冻结、Git 提交并推送；固定完整回归、精确 head 双 CI 和官方来源核验后组装/签名，保数据更新当前 iPhone。
- [ ] 保存真实游戏画面并检查内容，再验游戏输入、声音、游戏存档和重开；未验证的项目继续明确未完成。

## 并行边界

线程/同步完整 `hle_thread.c` 的适配先作闭包设计；它包含当前尚缺的 AddressArbiter 族，但没有证据将其作为当前 Metal pool 失败或启动耗时的原因。保留已经工作的堆、VM、RomFS、UID 和存档路径，避免为形式上的统一引入旁线重写。AudioRenderer 另按完整后端选择与接口合同推进，不能用静音成功填补。

当前事实与审计保存在 ignored outputs，游戏、派生程序、私有 shader、密钥和设备日志不提交仓库。

阶段证据：`3aaca29` 已保数据安装到当前iPhone12，PID1215，显式argv仅 `--observe-graphics-lock`。固定本机1055项/1054通过+1跳过，完整296.256秒；iOS38890493895成功1006项/45跳过，Linux38890493879成功。四次宿主样本分别TextureWriteTexels held2.570ms、idle、MemoryPoolMap held0.138ms、MemoryPoolMap held0.059ms，generation变化，不支持同一轮持续锁横跨四点；217.463秒查询仍在、13线程、无return/真实帧。不是可玩验收。诊断报告SHA `0973a75ad7e6b17928cf4ebdd1f7a2ece88660abce886839b5170d388f3760f3`，完整后端phase1收据SHA `53143f6bb18729e26c9b8017a67100143623f8ff96ad3cb4963e7c411ffdc3a9`。

全RomFS头部清点39678文件；Bootup_Graphics完整有界展开70513648字节，得到22BNSH/13539programs/27078stage records及8SHARCB/8041stage records。BNSH控制头版本9与SHARCB archive v9内实际控制头版本8分别记录，禁止混同或补零。输入清点不等同所有场景shader已翻译/可执行，资源事实仍需完整适配。

## 最新上游核验与基线升级

2026-10-16 live核验：TommyLau/botw-mobile的不可变HEAD `21f026e0c7fd9fee575a1bfa8d925553bb4db88e`（06:34:22Z），较5f领先98提交。官方tarball SHA `9fe52ebcd0524aab1d714fadd7bae36b4195b2c74620d6d6f8ab9f147e05cdc4`，1153文件逐一核Git blob身份；未执行其宿主。README的部分待办已经落后源码，不据该待办推断模块不存在。

最新完整源码已有extract/probe/derive/build链、更多NVN命令与deferred-bind快照，以及AudioRenderer/PCM/音频设备模块。下一复用pin选择21f，暂停对旧5f扩展并保留原实验源码/测试收据。新增实现不能无条件视为base-v0可用：仍有55.15对象ABI、单NoCopy池、1.9/SDK20.5.22/REV15及固定偏移等版本限定；derive的assumption/unresolved/difference也不等于权威事实。

- [x] 完整实验backend升级21f并核63原文件、环境与公开诊断入口；75文件独立归档，25TU、宿主adapter、真实runtime声明映射编译和link-only通过。只运行宿主自有CPU合同，不执行backend/guest；当前生产98项未切换。iPhoneOS编译已纳CI，结果以精确head CI为准。
- [x] 完整shader工具vendor升级21f：374原文件逐Git blob锁定，完整extract/derive/build及Ryujinx保原算法；增加薄严格发布准入。25组件测试和61上游测试完成（60通过、1原条件跳过），自有CPU翻译链通过。真实场景facts仍不足，未发布可运行场景包，base-v0严格审查不冒充1.9权威事实。
- [x] 静态核AudioRenderer版本边界：现base-v0参数64字节/REV1，新上游56字节/REV15且字段偏移不同。优先对齐1.9后整复用，不删除guard或套固定偏移。

## 整体host接入边界

选择21f完整host及其消费者闭包，避免gfx-only方案持续维护两套IPC、线程和VI。上游29host+25gfx共54个TU；iOS排除AppKit的`botw_front.m`后，候选闭包53TU、递归97源文件。该数字不是最终生产manifest，不能与旧98项叠加，也不能隐藏include规避预算。

上游`DM_SHELL`已有签名Guest.framework节装载和remap机制，`DM_NVN_M3=1`保有iOS18图形分支。保完整实现，只在UIKit/CADisplayLink事件、RemoteIO/AVAudioSession、持久UID、RomFS输入和事务存档根做薄接入。wholehost另有计数stub的RW→RX及guest dump入口，须封闭后才能运行；现实验图形政策不能被误认为已经覆盖wholehost。

本地1.9 NSP已核PFS0与四个加密NCA，但未取得可用自有密钥或已导出目录，尚未解密CNMT、确认版本和生成新版程序。正规解包工具已从固定源码编译，仅处理私有新输出；不改原包、不输出密钥、不获取第三方密钥。现root上游setup/extract仍含D2R固定TitleID，不能直接运行当BOTW安装脚本。

`c94f800`已提交推送，固定本机1070测试（1069通过、1条件跳过）及Linux完整CI成功。首轮真实iPhoneOS CI在第三个TU因Mac专属`mach_vm.h`失败，前两TU成功，未链接、未执行后续测试；原始日志与官方工件保留。后继平台补丁只替换三个原文件的include与四处正常VM调用，iOS走公开`vm_read_overwrite`，Mac保原入口，转换/区间溢出、错误与短读由自有CPU夹具核定。该扩展使隔离组件为78文件，仍不改变上游63原件和当前生产98项；iOS通过与否继续以修正提交的真实SDK CI为准。

旧5f实验69文件已完成23TU编译/链接和11自有CPU模式，阶段源码冻结；这些是旧基线证据，不冒称最新生产或可玩完成。

3a真机observation-4已完整返回host_return=1，实际首错在CPU_CACHED内存池snapshot分配（NSMutableData，尚未进入该分支Metal factory），没有真实frame；其后focus边界错误正常保存。报告SHA `7eaeb1ecfbddd358e3c2b2e958118b803287b3a365d3b019f3a5ae96580897df`，资源fresh再核保全。该记录只定位此次分支，不推请求bytes/连续VM或OOM，也不认定b7的Metal nil已解决。继续完整pool-provider和后端接入，不再为每个近邻失败增设诊断安装轮次。
