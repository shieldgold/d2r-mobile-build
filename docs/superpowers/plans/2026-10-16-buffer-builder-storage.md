# BufferBuilder 值写与存储占用

目标：忠实执行原 BufferBuilder 字段写，解除已证按值迁移纹理对临时 raw 槽的永久独占假设，保留旧资源与所有真实使用检查。用户已批准原生架构并要求持续开发、更新当前手机；本计划不再要求重复确认。

## 已确认边界

- 固定 `320a0fc96eb1c97f084e86d924b27944220d119b` 普通 Mac 完整退出，实际命中 BWTexture、192B、primary=true、move epoch1、相对+48；SetDevice 的原8B写落[48,56)，不是只碰未写padding。证据 `outputs/botw-host39-mac-320a0fc/buffer-conflict-evidence.json`。
- 原 base-v0 SetDevice 只写 device+0..7，SetDefaults/SetStorage 写+8..31。64B是公开opaque容量，不是每次访问范围；Setter只存参数，非法device/pool在Initialize或真正使用时继续严格验证。
- `objectSpans` 的primary存储占用与 `objects` 的host value/资源寿命分开。退占用不调用Finalize、不删objects、不释放mapping/texown/flush/descriptor/recorded refs。首8token仍在的旧raw沿现get可作为同value alias，但不再是move primary；不宣称原被覆盖POD仍完整或旧对象死亡。
- 首批仅接 BufferBuilder 三个字段写入口，接受已审moved BWTexture primary（完整192B、key=identity、!base、epoch>0且未耗尽）；其余class、selected/unmoved/不一致记录继续拒。不硬编码实际+48、地址或调用点，不全局取消span检查，也不新建地址黑名单或修改get。

## Task 1：统一写事务与原字段薄适配

- [x] owner最终七路径方案与root/kernel合同一致：新metadata_write.inc、graphics接线、三个Buffer writer、CPU/resource GPU测试、TEXTURES及LICENSES；native97→98，原100上限保持。先边界/权限/arena/busy/pending全部source+dest范围及moved-primary闭集，私有clone/remove完成后dirty/受保护固定写，成功单发布span+epoch。
- [x] 用一个可复用的metadata值写入口，按真实字段范围做溢出、logical权限、真实写准备与VM检查；保queue arena、command memory、active callback、pending transfer、epoch上界和错误锁恢复。
- [x] 所有分配、clone/remove及会失败的检查在写入前完成；写后只做不可抛的span单发布与epoch更新。前置失败保guest字节、global span/epoch、objects及资源引用，不发布占用退役；既有dirty/权限preparer的准备副作用不冒称可回滚，并发权限变化下不新增字节原子性承诺。
- [x] 保完整vendor buffer模块及base-v0字段适配，BufferInitialize、Finalize、Map/address/cache与其它family语义不混改；SetDevice仅存pointer，不提前解引用provider。

## Task 2：原合同与错误回归

- [x] 自有CPU夹具先真实RED，验证SetDevice 8B、Defaults/Storage 24B写足迹、device/tail canary、NULL/foreign/PROT_NONE参数只存值，初始化仍受控拒坏provider。
- [x] 实际partial/exact/multiple旧claim退役，合法旧alias和值保持；epoch/clone nil/真正clone异常、pending source/target、stateful/arena/busy、权限/只读/NONE/跨页/overflow均有界验证。诊断负例迁到仍拒域，不能保已成功域为错误预期。
- [x] 新增两GPU方法/四模式在旧生产真实RED（编译成功，MetalValidation启用，均精确+48拒），最终冻结source独立GREEN1.944秒，旧descriptor/recorded六色、private clear与live/retired authority保留；合成像素不等于原游戏。旧Buffer方法的permission oracle从&builder迁到Defaults实际+8再恢复GetSize整读，原FAIL保留，未改生产来满足旧错误预期。
- [x] kernel独立CPU12/12 9.352秒、linear新GPU2/4mode 1.944秒及旧Buffer定点1/1 0.867秒双CLOSED/GPU_RELEASED；九SHA稳定，旧probe两地址逆剥等原。无新guest内容或诊断VM读取。

## Task 3：提交、实际游戏与手机

- [x] root核9冻结SHA与index精确一致，连同4自有文档精确13路径提交并push `cae045f9f79ae4144131581bfcbf6f2f10e2aafd`；native98/100，不上传私有输入。
- [x] cae精确binary及200私有输入核后普通Mac完整退出host_return=1，旧BufferBuilder错误不再出现；新首fatal为TextureInitialize generic拒绝，尚不知实际Init参数，不把前3次Cube零query当其因果。无真实第一帧。
- [x] 固定cae全套995项/994pass+1skip完整exit0（254.925秒），643 tracked及manifest7127c3aa…前后稳；iOS/Linux精确head CI与官方工件核验后本机组装签名，保数据更新iPhone12/PID1160。首采样为运行中无host_return，无帧；UID、RomFS大小/mtime、shader与权限保留，继续采后续证据。
- [ ] 真机真实画面、操作、音频、存档与重开全部验证后才可称能玩。安装、启动、测试或绘制fixture通过均不足以替代。

当前owner最终freeze2八路径 SHA256 `a7f4b013328a02b2216092343f22980139383b6b68652fe73520b879eafe6c9d`；第八路径仅澄清texture_transfer顶部注释，零逻辑差异。native98/100。CPU相关28/28完整通过13.913秒（新10、既有诊断2、transfer16），初8新方法真RED含6行为失败；实际device消费验证、mmap边界及原短路负例已补强。独审CPU/GPU与原游戏仍待闭合，不能据此称可玩。

独立CPU12/12通过9.352秒，freeze2八SHA稳定；global get、vendor、Initialize/Finalize及十项资源依赖原SHA保持。最终freeze3九路径SHA256 `128605449a74f889d4d081eec048f55dcbb5974d7f7275c84ec371e3664b3fd3`，只新增旧probe permission oracle两处地址迁移，前八项逐字节未改；新GPU与CPU不重复运行，旧probe按失败定点复验。

旧Buffer permission夹具迁移后owner1/1通过0.877秒、独立1/1通过0.867秒，实际MTLValidation启用，编译/运行exit0；与新2方法/4mode GPU合并独审CLOSED，GPU释放给原游戏。

cae普通Mac binary `bc995ad14674a0cc03600ddf20ac13b8ae47e066445db8db46abb869a75154a6` 与200私有输入核验后已启动；live检查时Mac锁屏、无完整host_return/真实帧，未观察旧fatal不足以证明越过。`outputs/botw-host39-mac-cae045f/locked-live-checkpoint.json` 保留非终态，已请求用户手动解锁，不能改显示时钟来绕过。Linux精确head CI成功，iOS CI仍运行。

精确cae的iOS CI38853804418与Linux CI38853804430均已完整success。官方无游戏三文件shell已独立下载核验，98项生产来源逐一匹配Git blob，shell SHA256 `cabcfcac6a4bedb060cd08b972474a0ad10d3be7821b1f9accec14cc08a58c5d`；暂存于 `outputs/botw-ci-cae045f-stage/verified-shell/`。后续固定全套OUT建好后可复用此工件，不需重复下载。Mac仍锁屏待用户解锁，普通运行会话未退出，local fixed full尚未启动；手机保持已完整核验的b41，不宣称cae已装。

继续时已live核Mac IOConsoleLocked=No，原cae launcher会话完整exit0、进程不在，完整日志含mac_host_return=1及process_exit=1。ordinary-admission-evidence.json已保存；新TextureInitialize generic缺少实际snapshot字段，后继只追加原失败分支正常API已有scalar并保原前缀/BOOL/回调/调用次数，不读取运行时旧对象或新增VM诊断。固定cae全套现在执行，官方工件已复用到phone OUT，手机连接通道已主动核验。

后继纹理Init纯诊断三路径已freeze：`outputs/botw-texture-init-layout-context-audit/owner-freeze.json` SHA `b9cbd0dbd392caa9d87a913b4249550fbe2f32172b34eec9afe92b054e936a15`。只generic失败原report一行追加同validated快照，不增native98，不变条件/短路/BOOL/回调或textureBytes次数。owner4CPU/11mode4.739秒、真实RED为缺字段4subfail（compile0），原validator应2非3的旧fixture假设单独保留不算RED；独立静态逆剥全文恢复679d1d6d…且最坏runtime消息407<512，独立CPU4/4与11mode通过4.644秒，双独审CLOSED、三SHA前后稳定，最坏407B及原单行逆剥匹配，已具备精确提交和普通Mac分型条件。

后续状态：cae真机observation-3完整取得host_return=1、13线程及相同TextureInitialize generic拒绝，无游戏帧；原前缀保留且仅一次新增bootstrap，报告SHA a86859fc3806f2d563a2279a972d3385a5cd171c4fafbdefd3ff46e195a83275。诊断6d6caaa已push，其普通Mac没有触发该报告，前台灰窗和CPU繁忙只保留为非终态；root停止自己的进程释放GPU，不伪造完成记录。独立时钟/锁审查未证混频或死锁。已证Cube8查询布局缺口转入独立texture-cube计划，不把查询与generic拒绝直接连因果。
