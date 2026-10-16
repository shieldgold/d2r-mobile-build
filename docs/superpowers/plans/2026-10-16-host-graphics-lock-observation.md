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
- [x] 精确核冻结源码、commit并push（c31ef9adc8edc0d9313563707da52097e55b94f7）。

## Task 3：固定诊断运行与继续修复

- [x] 7ee随后自然结束，保存独立完整host_return=1；没有停止它或伪造终态。固定c31编译并核200私有输入后显式诊断运行。
- [x] 取得四个一致性held样本，generation均不同且本次持锁短；没有持续持锁证据。入口名不等于其内部耗时原因，也不是guest执行位置/死锁/游戏验收。
- [x] 手机7ee普通observation-3完整失败日志提供真实Init参数，转向完整3D适配计划；未以安装、测试、进程存活或诊断夹具代替可玩验收。

producer五路径freeze SHA256 `0e1a004b222317d9d06aba005d50ad743a7333e6232a3e257fe4d1826aaea3da`。新增15方法/22native模式owner4.818秒、最终作者4.480秒与独立kernel4.835秒均完整exit0，ASan/UBSan与ARC exceptions启用、选项unset，全fakeGPU；旧相关4CPU通过8.679秒。默认关闭不调用时钟/线程查询，observer不新增validator调用。四源机器逆剥及独立行级逆剥均逐字节恢复baseline，原资源/准入/错误body未变。root前端6CPU通过12.435秒、独立6CPU通过12.426秒；原CLI valid flag行为先真实RED（compile0/返回64），初import错误另存且不计RED。五SHA和前端四源码前后稳，native98/100，无新native文件；提交前尚未取得实际宿主持锁快照；固定c31的后续四次实测见下。

手机7ee的后续observation-3现已完整host_return=1，实际32³/target2/flags4/format25/packagedData非NULL的Init拒绝另转3D计划；不是由CPU静默或本诊断推断出的guest位置。

c31实际诊断已闭合。固定binary SHA `b945f91d756ef7ff945d2ef99de84726c9690d1fa3dd4bbec1130c95b940d69f`，PID63302，10/30/90/180秒四次均为w_nvnMemoryPoolMap，generation分别50886/282877/423024/628736，held分别43/27/546/636微秒，lifecycle1且同owner。generation变化排除一轮不间断持锁横跨四采样的解释，不证明其它入口或未采样区间没有阻塞。公开libproc独立5.005秒两点CPU4.863秒（97.166%单核）、归账磁盘IO0；没有推具体guest循环或缓存读取量。

四样本后仍无host_return/真实frame，先保存有界日志SHA和精确磁盘binary SHA、LC_UUID、proc_start身份及正常ps，再SIGTERM自有有限诊断；launcher随后完整exit0、目标进程消失。该结束明确为agent-stop，不是guest完成，未调用普通terminal recorder。checkpoint `outputs/botw-host39-mac-c31ef9a/host-lock-checkpoint.json` SHA `4a4b3195383d732fa30470e550ba073d682f58859054ff4a6ff3c8cf700079c7`，另存agent-stop及launcher回执，GPU已释放给3D自有夹具。桌面接口仍报告Mac锁屏，未取得屏幕核验，不据系统单一字段反推屏幕状态。


## iOS 后继：复用有限宿主观测

b7bd5a2已完整测试、双CI及工件核验后保数据更新iPhone12/PID1181。37.683秒与261.208秒两份完整boot采样均13线程/无host_return/error/frame；后续541.937、680.120、791.321、952.261、1106.370及1168.611秒只查公开文件metadata，大小51,587,549和mtime均同首份，没有新frame文件。1190.152秒进程查询仍见PID1181。用户直接反馈新版仍黑或灰。后续metadata不冒充重新读过boot内容，不以无日志断言具体guest循环、死锁或3D已通过。

固定b7的只读锁范围审查确认run_lock跨整次guest，但display/input/frame count不取它；lifecycle在DT_INIT/nnMain前已释放，input及VI就绪状态不等guest返回。仍有display持lifecycle同步取graphics、input链等可能等待路径；没有当前实际持锁状态证据。审计收据 `outputs/botw-lifecycle-display-lock-audit/b7-kernel-peer/receipt.json` SHA dce371e776064b9b4c40fb13e1bcbd62c0237a9bcaeb1d509fc293f7f37241ac。

当前运行继续保留，同时实施明确opt-in的iOS有限观测；它用于取得宿主状态，不宣称修复黑灰画面。方案收据 `outputs/botw-ios-lock-observation-plan-audit/graphics-owner/receipt.json` SHA458c7594ffc6999d1c270125841a8923c6fc9f54c926cedf72983c22d4e18675。

- [x] 先有可运行CPU行为RED，将既有mac完整consumer迁入现有BOTWEventLog.m/h中的独立C/GCD路径，两个前端复用单套cap4；不经过其UI NSLock/append/handler/hostEvent，不改graphics producer和资源行为。native仍98，上限/schema不变。
- [x] iOS仅精确argv token在UIApplicationMain前pre-start启用；无flag零producer/queue/read/output。boot stdout/stderr接通后首次pthread_create成功才start_once，scene/audio重试不重排；晚启拒绝不改变游戏返回/错误/回调。保原argv及默认启动。
- [x] Mac原consumer合同及全部链接调用点迁移，EventLog独立测试用host-only producer stubs；验证默认off、状态/时间边界、独立serialqueue、主线程阻塞仍首采、四次上限及重复start。iOS主函数/线程启动用明确投影的真实函数体与host stubs核顺序/失败路径，不将它等同实际UIKit或屏幕验收。
- [ ] 双独审冻结，root精确提交推送，固定完整测试、精确head双CI与官方工件后再保数据更新当前手机。继续保留旧普通运行直到下一次实际安装启动决定，所有旧证据不覆盖。
- [ ] 真机fresh launch核explicit argv/PID/唯一新bootstrap，取得至多4条普通boot中的host-only样本；默认off对照及c31原producer测试仍保。若出现新的实际fatal则回到该功能边界，不把观察结果当游戏完成。

采样仍仅静态NVN入口名、lock-free宿主原子及宿主时钟/线程标量，不读guest PC/栈/堆/对象或VM、不新增validator，不驱动display/VI/input/时钟，不取graphics/lifecycle锁。iOS挂起、调度或stdio可使点迟到/缺失；观察者无新增后台权限。当前PID不能中途补启，后续明确新进程启动才生效。

后继共享consumer已双独审CLOSED，freeze2 dd4d8828；owner18项/17PASS+1旧条件skip14.285秒，kernel6/6 0.498秒，linear12/12 14.069秒+3自有ASan/UBSan模式完整0。新增off-start后enable边界先真RED91，再只调整started封口顺序；原Mac核心逆剥时明确此额外delta。原producer/UI日志body不改，native98。现b7普通运行已有新首fatal：MemoryPoolInitialize Metal allocation failed（obs3报告c3eaa4a1…），转向两个失败分支的有限参数诊断。此观测实现先归档，不单独安装；下一连贯候选统一完整流水线后按需要显式启动，不把本计划当无帧原因证明。
