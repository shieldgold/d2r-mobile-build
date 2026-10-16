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

- [ ] root核所有changed/untracked路径及native来源数量，精确stage、commit并push；不改100上限、不上传私有游戏、证书或keys。
- [ ] 精确提交普通Mac核binary和200私有输入后运行原游戏，完整退出后记录下一停止点/真实第一帧；不得把launcher exit0当game exit0。
- [ ] 对连贯功能候选跑固定全套和精确head iOS/Linux CI，核官方工件、本机组装签名，再保数据更新当前iPhone12；累计日志流式核前缀，不截断、不重传14GB资源。
- [ ] 真机真实画面、操作、音频、存档与重开全部验证后才可称能玩。安装、启动、测试或绘制fixture通过均不足以替代。

当前owner最终freeze2八路径 SHA256 `a7f4b013328a02b2216092343f22980139383b6b68652fe73520b879eafe6c9d`；第八路径仅澄清texture_transfer顶部注释，零逻辑差异。native98/100。CPU相关28/28完整通过13.913秒（新10、既有诊断2、transfer16），初8新方法真RED含6行为失败；实际device消费验证、mmap边界及原短路负例已补强。独审CPU/GPU与原游戏仍待闭合，不能据此称可玩。

独立CPU12/12通过9.352秒，freeze2八SHA稳定；global get、vendor、Initialize/Finalize及十项资源依赖原SHA保持。最终freeze3九路径SHA256 `128605449a74f889d4d081eec048f55dcbb5974d7f7275c84ec371e3664b3fd3`，只新增旧probe permission oracle两处地址迁移，前八项逐字节未改；新GPU与CPU不重复运行，旧probe按失败定点复验。

旧Buffer permission夹具迁移后owner1/1通过0.877秒、独立1/1通过0.867秒，实际MTLValidation启用，编译/运行exit0；与新2方法/4mode GPU合并独审CLOSED，GPU释放给原游戏。
