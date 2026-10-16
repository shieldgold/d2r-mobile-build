# 实际3D纹理初始化路径

继续已授权的完整模块原生移植与当前手机可玩目标。先完成独立宿主锁诊断的验证归档，随后围绕这条真实失败接入；在原合同闭合前不改texture_family支持域。

## 实际输入与边界

- 当前手机7ee/PID1175的独立observation-3完整host_return=1，13线程，首次实际Init拒绝明确target2/32×32×32、flags0x4、format0x25、levels1、samples0、stride0、pool非空、GL空、packagedData非NULL、layout未声明/bits0。
- fresh boot SHA `57efa1ca96d6d9be0327ede23533f5dfd277c2e904ffd04f95fba198615258ee`；报告SHA `e2b316db6bf98477dffed90ba32de1206e385f2b84ddd48d93581e7c81c48d82`。旧两份非终态保留，失败发生的精确时间未知。仍无游戏帧，不把Cube查询与本次Init连因果。
- 当前3D GPU准入只证depth1/level1/flags0/无packaged，实际域未实现。不能只删除拒绝条件或忽略packagedData；正常初始化必需的输入访问须先由固定原SDK及完整模块合同证明并有界验证，不增加诊断dump或读取运行guest PC/栈/堆/旧对象/VM内容。
- 继续复用完整上游layout/deswizzle/texwrite/flush及既有资源生命周期；不能用2D首slice、nil补零、dummy、宽高深32特判或放宽authority替代真实3D行为。其余未证域维持明确拒绝。

## Task 1：原合同和完整模块可复用性

- [x] kernel核固定原SDK flags4的准确含义及其对size/layout/kind的实际影响，32³Size/Align/ViewOffset和深度GOB参数。
- [x] 固定base0 actual normal/rawFE路径已闭合：Setter/Get只保留opaque pointer；原Initialize及完整layout helper不读Builder+50、不拷此pointer入Texture，池回调取pool base+Texture.offset，没有payload上传。布局另在+58，不能混用。此结论不扩大到未证GL/packaged-layout等其它路径。
- [x] owner/linear对照两个钉住的完整上游3D创建、上传、反向读取与writer，列出可薄接域及真实差异；不以自写平面算法冒称完整3D。

## Task 2：在证据域内实施和验证

- [x] 合同闭合后root定最小通用域/影响面，owner先自有CPU真实RED再薄接；保builder原metadata、正确volume/mip/region边界、权限/溢出/empty及失败状态。
- [x] 独立真实MTLType3D多深度采样/CPU反向读写与padding canary，覆盖跨GOB的z变化；所需IMAGE/packaged语义按原合同验收，未支持消费者仍受控拒绝。
- [ ] 默认旧2D/array/Cube/单slice3D与private authority/descriptor生命周期必要回归，双独审冻结后精确commit并push。

## Task 3：完整候选与真机

- [ ] 固定连贯提交完整测试、精确head双CI与官方无游戏工件核后本机签名，保数据更新当前iPhone12，继续核UID/RomFS/shader。
- [ ] 独立普通运行取得下一实际停止点或真实画面；通过操作、音频、存档及重开才可称可玩。

## Root 实施域决定（合同闭合后）

owner/kernel/linear三份只读收据已闭合。flag4为IMAGE；32³format25自动布局size131072/align512、tileH0/D4，z切片相对起点为floor(z/16)*65536+(z%16)*512。kernel用自有数据证明完整vendor体积地址双射、往返及局部写canary；此证据不冒充原游戏执行。

本批实施真正target3D、W/H/D各1..2048、合法完整mip链、rawsamples0、flags0或IMAGE4、exact且identity的普通非压缩color、无GL/无显式packagedLayout。普通池与既有FE虚拟解析域可复用；physical direct、未知kind、跨不支持的mapping/别名域继续拒绝。非NULL packagedData只按已证normal合同保存/返回opaque值，不访问其内容；不能扩大到其它target或layout/GL域。

完整vendor算法不改。桥薄接每mip完整volume staging、slice0/origin.z、CPU选中单mip的xyz区域、Invalidate全mipDepth，以及完整allocation (0,1)的coherence/authority。NULLregion沿已证完整单mip语义；多mip整笔CPU transfer、正3D layer view、跨维/异格式view、3D attachment/呈现等未证操作仍受控拒绝。保原对象/provider/边界验证顺序、empty无写、checked溢出及失败不半发布。

GetViewOffset纯metadata查询与创建GPU view分开：本批包含已独证的合法selected mip、z<mipDepth的正z查询，复用完整vendor非线性GOB地址；kernel六级63个offset黄金值独审。numLevels/numLayers等字段是否影响原query按固定原合同核准，不沿用GPU view的array-layer上界。GPU/descriptor与CPU transfer的View.minLayer>0继续拒绝；CPU region.zoffset仍可在选中mip的volume内取正值。

IMAGE资源可申请真实ShaderWrite usage；本批消费者只支持只读/采样。通过现有shared applyResources及wholebatch validateRenderSubmission，在counter/arena写入前拒绝3D typed write/read_write/未知access；实际encode复用同门。不能以usage或已成功Initialize宣称GPU image写入已实现。写消费者需要独立完整GPU写authority/别名/Invalidate闭合。

IMAGE真实资源域已按Apple官方Write表限定为Apple7及以上和36个标准R/RG/RGBA normalized/integer/float格式；sRGB/BGRA/packed等排除是桥的保守域，不是声称硬件全部不支持。新增whole-volume staging要求checked尺寸且不超过device.maxBufferLength。独审补核该已知容量资格须在MakeBase物化工厂前以及任何upload/Read/Write/Invalidate的大snapshot或dirty prepare前完成；虚拟首次draw的整批preflight由同物化门覆盖，不能先执行counter再在upload拒绝。空操作仍先按原对象/view/region验证并无payload返回，不借容量门改成额外内存访问。

测试必须先有新行为真实CPU RED，再覆盖真实Type3D、多z（跨z15/16与z31）、多mip、非2幂体积、局部xyz读写、GOB与staging padding、缓存Flush/Invalidate、opaque address1/自有PROT_NONE不解引用、虚拟映射epoch/别名/retirement，以及typed write整批前拒counter/copy/arena canary。旧2D/array/Cube/单slice3D必要回归；测试域改变须逐项解释负例迁移，不能删除保护。

owner持有graphics.m/texture_family.inc及既有相关test/probe/许可纹理说明；root持有主文档/计划/集成。kernel和linear独立审查/自有fixtures；原游戏和GPU运行由root串行分配。诊断c31使用固定提交，owner可在它之后实施3D，不混入诊断构建。
