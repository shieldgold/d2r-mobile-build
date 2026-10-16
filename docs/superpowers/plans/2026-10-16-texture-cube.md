# 普通 Cube 纹理完整模块接入

继续用户已授权的原生移植、真机安装和可玩目标；不要求重复确认。

## 已证事实与限制

- 真实普通运行三次查询target8、4×4、rawDepth6、format25、flags0、单mip、samples0、无GL/pkg，当前GetStorageSize均0。固定原SDK constructor/layout对target8给dim2/layered1/固定6faces，每face512、总3072、align512；这是独立可修的查询合同缺口。
- 前cae完整结果只有TextureInitialize generic拒绝，没有其参数；到最后日志历时2345.5秒。6d已加同snapshot标量诊断并经双独审，普通运行前台空白/约单核100%，尚未触发该Init报告；root在保留非终态证据后精确SIGTERM自己的测试进程释放GPU。停止不是游戏完成，也不证明Cube导致长空转或generic错误。
- 原rawDepth getter与internal6faces分开，target9原构造和当前vendor CubeArray有depth乘6差异，不启用9。原Cube NULLregion默认六face需明确薄适配；不能把所有mip默认当一笔transfer。
- 复用完整vendor layout/deswizzle/texwrite/flush和既有array/mip staging；不新建codec/allocator/dummy或nil-to-zero成功，不新增native文件，保98/100。

## Task 1：原合同与可维护薄适配

- [x] kernel固定原SDK/core布局及ViewOffset=minFace*faceMipStride+mipOffset；linear完整两上游与32固定源/Apple Cube类型与MSL float3采样完成只读审查。generic因果仍未知。
- [x] owner用既有texture_family和完整模块接普通target8 square/exactcolor/flags0/samples0/rawDepth正值、无GL/pkg，CPU布局支持完整合法mipchain，rawDepth至少1和6原样getter且存储都固定6faces；其余原维度边界保留。
- [x] 真MTLTypeCube/arrayLength1、每face每mip真实staging、同维同format全六slice view；RT/window/virtual/非零pkg及其他未证旗标前拒，不把Cube当2DArray或depth6的3D。
- [x] Cube NULLregion以所选base mip的当前宽高构造独立hostregion并覆盖六face交完整planner；多mip transfer view语义未闭明确拒，不读取NULL；其他targets保持原行为。

## Task 2：证据与回归

- [x] CPU Size3072/Align512先真实RED，覆盖全合法mip/format边界、rawDepth1/6、getter/ViewOffset、selected mip NULLregion与零区域、不支持flag/pkg/target9。
- [x] linear独立GPU夹具先在固定旧生产真RED，最终六face×三mip float3真实采样、六slice view、反向readback/有界canary/unsupported域拒绝；CPU与GPU sanitizer边界分别记录。
- [x] 旧Cube negative若成为正域，只迁到仍unsupported target9并保原错误路径意图；不能删失败、默改预期或为测试加dummy。
- [x] 冻结生产/测试SHA，独立CPU/资源语义审查和必要GPU验证通过后交root；不读取guest栈堆/旧对象或新增VM诊断。

## Task 3：集成与游戏验收

- [x] root精确核7冻结SHA及index，连同3自有文档共10路径提交并push `7ee9a1acbbf97d9eff4993385fd32a682d9a62ed`。
- [ ] 普通Mac以固定提交/200私有输入运行，保留真实新停止点或第一帧；不等待长超时猜因果，不把受控agentstop当guest完成。当前binary e0c77e87…运行中无host_return，桌面接口锁定状态与ioreg不一致，仍待成功屏幕核验。
- [x] 7ee固定完整1004项/1003pass+1既有skip完整exit0（254.899秒），644项来源前后稳；iOS38873214176/Linux38873214174精确head成功，官方工件核后本机签名并保数据更新iPhone12/PID1175，UID/RomFS/shader保全。首40秒与后续295秒观察均13线程、无host_return/新fatal/frame，boot前缀保留且只一次新增bootstrap，非终态日志分样保留。
- [ ] 真实游戏画面、操作、音频、存档和重开都验收后才可称可玩。时间基准/锁调查作为独立只读证据线，未经证实不修改counter或引入显示时钟fallback。

最终7路径freeze SHA256 `6059d61d5901577ae3b574f8d4ccfef0d996cac372235b8154f180c794b1fa8a`，family `69d5de4232a3d7dff008eedbe8ef170e38ff51f3dac7f3c66751931f8b51194d`，native98/100。owner相关CPU36/36通过7.256秒；独立CPU8/8、12mode通过5.711秒，ASan/UBSan及ARC exceptions启用。新两GPU方法/七模式先固定旧生产真实RED，再在相同最终生产通过2.972秒，实测六face×三mip及rawDepth1 selected Cube；最终独立public Cube四模式、原generic target9负域与2DArray共3方法/15模式通过3.598秒，实际MetalValidation启用，无GPU sanitizer。全7文件与98项依赖SHA前后稳定，双独审CLOSED/GPU_RELEASED；完整原游戏和A14结果另记，不能从自有夹具通过推定可玩。CPU多mip对象明确要求单mip View，NULL region只支持完整六面，未宣称所有原View语义已等价。
