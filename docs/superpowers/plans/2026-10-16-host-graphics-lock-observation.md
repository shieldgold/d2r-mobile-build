# 宿主图形锁有界观测

继续已授权的原生移植与当前手机可玩目标。Cube生产7ee已完整测试并安装，当前源码HEAD d673只补验收文档；此批为定位初始化长时间停留的纯宿主诊断，不增加支持域或宣称死锁。

## 证据与影响面

- 7ee手机PID1175首40秒及295秒两份普通观察都13线程、无host_return/新fatal/frame，boot内容完全相同；身份与资源保全。Mac固定binary e0c77e87…/PID52562同样无新日志，公开libproc五秒两点已绑定磁盘SHA、LC_UUID和进程start：CPU4.930539125秒/98.583%单核，归账diskIO读写均0；缓存读取或其它CPU工作未被排除。
- currentEntry/depth为TLS，depth在等锁前增加；平台包装缺少入口名。现有二进制无法安全跨线程定位实际持锁入口。主display callback有lifecycle→graphics锁链，因此不能把唯一观察器放入display callback；静态关系尚不证明实际死锁。
- 禁止读取运行guest PC/栈/堆/对象、VM诊断扫描、sample/lldb；新字段只来自宿主静态字符串、正常宿主线程ID与单调时钟。无guest参数或对象指针输出，不扩大权限、资源准入或池支持。
- 原pthread锁/阻塞、TLS错误上下文、回调顺序、BOOL及所有资源语义不改；默认关闭。现有native文件内实现，保持98/100及schema。

## Task 1：producer与CPU合同（owner）

- [x] 在既有graphics.m/h提供仅pre-start启用的host诊断及无锁快照API，默认关闭；全payload为已证明lock-free的原子标量，单纯seq+普通字段不允许。
- [x] 真实acquire后发布generation/owner/outer+active静态入口/单调起点；nested只更新active并恢复，不重置起点；释放前清held。覆盖NVN/unsupported/PLATFORM/checked及texture_transfer，command-memory callback锁外区间明确不计，reacquire新generation。
- [x] snapshot固定至多两次一致性读取，不等待、不自旋、不取graphics/lifecycle锁，不碰registry/GPU/object；不稳定、clock/threadID失败或generation饱和明确unavailable，不改变游戏错误/回调。
- [x] 自有CPU真实RED→GREEN，验证owner与waiter、nested恢复、同名多acquisition、callback释放/重取、生命周期、默认关闭、失败路径/原回调顺序、饱和/unstable，必要现有边界回归；无GPU/guest probe。

## Task 2：独立有限Mac观察（root）

- [x] mac_main仅显式 `--observe-graphics-lock` 启用；正常运行默认不创建观察通道。启动guest前开producer，在独立host队列有限采样四次（约10/30/90/180秒），只输出stderr宿主字段，不调用display/frame/objects或生命周期API。
- [x] 该timer仅采诊断，不驱动vsync/显示/输入、不成为display clock fallback；不会把observer默认带入手机路径。快照不可得就打印对应状态，不伪造owner或进度。
- [x] CPU夹具核默认关闭、四次上限及字段来源；Mac frontend编译/原行为适当回归，真实GCD在主线程阻塞时完成首采样，双独审CLOSED。实际窗口回归和游戏观察另记，没有以CPU夹具替代屏幕验收。
- [ ] 精确核冻结源码、commit并push。

## Task 3：固定诊断运行与继续修复

- [ ] 先保7ee非终态及公开CPU/IO证据，再在需要换候选时受控停止自有Mac测试；不伪造host_return。固定新commit编译、核200私有输入后显式诊断运行。
- [ ] 同generation、owner和起点在多个一致性采样间相同才支持持续持锁结论；idle/unstable不能证明无阻塞。入口名不等于其内部耗时原因，也不是guest执行位置/死锁/游戏验收。
- [ ] 手机7ee继续独立普通采样，新的实际Init参数、真实画面或宿主入口证据到来后按可证缺口修复；不以安装、测试、进程存活或诊断夹具代替可玩验收。

producer五路径freeze SHA256 `0e1a004b222317d9d06aba005d50ad743a7333e6232a3e257fe4d1826aaea3da`。新增15方法/22native模式owner4.818秒、最终作者4.480秒与独立kernel4.835秒均完整exit0，ASan/UBSan与ARC exceptions启用、选项unset，全fakeGPU；旧相关4CPU通过8.679秒。默认关闭不调用时钟/线程查询，observer不新增validator调用。四源机器逆剥及独立行级逆剥均逐字节恢复baseline，原资源/准入/错误body未变。root前端6CPU通过12.435秒、独立6CPU通过12.426秒；原CLI valid flag行为先真实RED（compile0/返回64），初import错误另存且不计RED。五SHA和前端四源码前后稳，native98/100，无新native文件；当前尚未取得实际宿主持锁快照。

手机7ee的后续observation-3现已完整host_return=1，实际32³/target2/flags4/format25/packagedData非NULL的Init拒绝另转3D计划；不是由CPU静默或本诊断推断出的guest位置。
