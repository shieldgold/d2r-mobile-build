# BOTW 组合纹理句柄编码合同修复

目标：恢复固定base-v0 `nvnDeviceGetTextureHandle` 的整数编码语义。`026265b` 普通运行已越过3D查询，随后Cube查询返回0并再次调用texture=-1/sampler257；不能据此认定两个事件的因果或该值可以直接用作GPU资源。固定原SDK、完整5f helper和当前消费者的只读审计已确认：原函数不读device、不验证索引，先按32位OR/移位截断，再符号扩展并置bit32。本桥已有编码四行正确，额外device/get和范围拒绝提前停止了正常哨兵构造。

架构：只修纯构造入口，复用当前准确编码；不改变NVN wrapper生命周期、消费者的低20/12位解码或任何descriptor pool bounds、注册语义、device/map/witness、整提交预检及private authority。未注册或越池的解析结果继续在真正使用前拒绝；不为哨兵分配dummy纹理或制造成功。原编码的溢出碰撞按原位值保留，不发明原始参数的旁路身份表。

技术栈与范围：现有 `graphics.m`、`tools/probe_texture_handle.c` 及所属Python测试；按需修改明确与本入口合同冲突的旧测试，保use-time负例。编码本身已正确，无需新vendor文件，native97/100与schema保持。root维护本文与主文档；graphics owner实现和GPU验证，kernel peer审整数/消费者合同，linear peer独立公开接口/GPU。按用户已授权的继续开发、安装执行，不等待重复许可。

## Task 1：固定证据与影响范围

- [x] 原SDK四条指令、完整5f helper及本桥全部已支持消费者已核验；8个自有header边界向量通过ASan/UBSan，不作为原字节执行。报告位于 `outputs/botw-texture-handle-audit/kernel-static-dc9/`。
- [x] `026265b` 普通Mac完整退出，Cube零查询与texture=-1/sampler257已分别保存；没有新增guest内容读取、VM扫描、栈诊断或调用者猜测。此批先恢复构造合同，Cube资源支持另据实际使用确定。
- [x] owner独立复核原SDK/5f完整helper及所有解码/预检消费者；四路径为graphics.m、probe_texture_handle.c、test_botw_graphics.py、TEXTURES.md，无新header或CI路径。

## Task 2：真实失败和最小实现

- [x] 自有CPU两个方法在旧源码编译成功后真实失败；覆盖-1、bit31、int32边界、溢出/OR碰撞和原device不读语义，RED日志保留。
- [x] 只去掉本构造入口额外device/range准入，保原低32 OR、符号扩展和bit32精确规则。没有清高位、替换sampler、把UINT64_MAX变空资源或改变消费者。
- [x] 289组golden及五种device参数通过，含自有PROT_NONE页；validator/VM/callback0，registry/serial/lastError及无Metal状态保持。旧构造负输入fatal假设按原合同修正，使用阶段负例保留。
- [x] 最终5方法8.190秒全通过；新增GPU4模式的整批拒绝经Finish/all-inflight drain后arena、copy/counter及RT像素不变，旧18模式另证最大注册槽UINT64_MAX仍可画；无特殊sentinel分支或dummy。

## Task 3：独审与固定提交

- [x] 独立CPU2/2通过3.968秒，GPU1方法4模式通过1.349秒、零跳过；四SHA及consumer依赖稳定，双CLOSED/GPU_RELEASED。原整数构造、合成像素和实际游戏分开记录。
- [ ] root核对所有changed/untracked路径及现有CI触发，精确stage、commit并push `botw-archive/codex/botw-native-preflight`。
- [ ] 普通Mac使用精确提交、核binary和200个私有输入后运行原游戏，记录是否出现真实第一帧或新的实际停止点；GPU窗口优先原游戏。

## Task 4：最终候选整套与手机更新

- [ ] 对连贯修复后的候选提交执行固定全套及精确head的iOS/Linux CI，核验官方通用工件后本机组装和签名；没有完整流水线的中间版不宣称手机已更新。
- [ ] 保数据更新当前iPhone12，核安装位置、普通启动、完整流式日志前缀、UID、RomFS大小/mtime及shader清单/权限；不重传14GB资源、不截断累计日志。
- [ ] 真实游戏画面、操作、音频、存档及重开均验收后才能称可玩。安装、启动或编码成功不替代这些结果。
