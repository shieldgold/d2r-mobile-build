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
- [x] root精确7路径提交并推送 `b41cf07312013e73fbcc28b652c80542f7c2b421`，native97/100及CI路径保持。
- [x] 普通Mac核binary及200私有输入后完整运行，原GetHandle构造错误消失；Cube零查询3次，随后新停点BufferBuilderSetDevice的storage overlap，未出现真实第一帧。

## Task 4：最终候选整套与手机更新

- [x] b41全套及精确head的iOS/Linux CI均通过，核验官方通用工件后本机组装签名，IPA SHA2aa09f4e…。
- [x] 保数据更新iPhone12，PID1120/安装位置匹配；首采样保留为启动中间态，observation-2完整host_return=1，34→35bootstrap/累计45,054,162字节，前缀、UID、RomFS大小/mtime、shader及权限均保，不重传/截断。新停点同Mac Builder overlap，无真实帧。
- [ ] 真实游戏画面、操作、音频、存档及重开均验收后才能称可玩。安装、启动或编码成功不替代这些结果。

固定b41全套已完整通过：981项中980通过、1项既有跳过，230.304秒；641 tracked及manifest `7ec2972e13d5a7c23e40c74932b0795855577389230f444063df32cff8828413` 稳定。iOS/Linux精确head CI成功，手机已更新并取得完整运行证据；真实游戏验收仍未完成。

## 后继构建器存储分型

- [x] 诊断320a0fc已精确五路径commit+push并普通Mac完整运行；实际BWTexture192/primary=true/epoch1/relative+48，原首8B写落[48,56)，不是仅padding相交；不推旧资源死亡。
- [x] owner纯诊断三路径已冻结，原谓词/返回/回调不变，CPU2方法/10场景真RED到GREEN4.459秒；独审CPU4.168秒及另一静态审查双CLOSED，三SHA稳定、逆剥恢复b41，无新增guest读取/VM。root精确提交该诊断后以普通Mac核实际class/offset。
- [ ] kernel/linear只读核原Buffer字段写足迹、POD按值与资源快照，结合实际范围区分padding交叠和真正写入交叠；评估现永久exclusive span的额外假设及真实资源门依赖，不泛化取消arena/非值对象/权限/authority/witness保护。
- [ ] 实际分型后才制定最窄功能修复与回归，遵循原完整模块合同，避免无证据的生命周期推断。
