# 实际3D纹理初始化路径

继续已授权的完整模块原生移植与当前手机可玩目标。先完成独立宿主锁诊断的验证归档，随后围绕这条真实失败接入；在原合同闭合前不改texture_family支持域。

## 实际输入与边界

- 当前手机7ee/PID1175的独立observation-3完整host_return=1，13线程，首次实际Init拒绝明确target2/32×32×32、flags0x4、format0x25、levels1、samples0、stride0、pool非空、GL空、packagedData非NULL、layout未声明/bits0。
- fresh boot SHA `57efa1ca96d6d9be0327ede23533f5dfd277c2e904ffd04f95fba198615258ee`；报告SHA `e2b316db6bf98477dffed90ba32de1206e385f2b84ddd48d93581e7c81c48d82`。旧两份非终态保留，失败发生的精确时间未知。仍无游戏帧，不把Cube查询与本次Init连因果。
- 当前3D GPU准入只证depth1/level1/flags0/无packaged，实际域未实现。不能只删除拒绝条件或忽略packagedData；正常初始化必需的输入访问须先由固定原SDK及完整模块合同证明并有界验证，不增加诊断dump或读取运行guest PC/栈/堆/旧对象/VM内容。
- 继续复用完整上游layout/deswizzle/texwrite/flush及既有资源生命周期；不能用2D首slice、nil补零、dummy、宽高深32特判或放宽authority替代真实3D行为。其余未证域维持明确拒绝。

## Task 1：原合同和完整模块可复用性

- [ ] kernel核固定原SDK flags4的准确含义及其对size/layout/kind的实际影响，32³Size/Align/ViewOffset和深度GOB参数。
- [x] 固定base0 actual normal/rawFE路径已闭合：Setter/Get只保留opaque pointer；原Initialize及完整layout helper不读Builder+50、不拷此pointer入Texture，池回调取pool base+Texture.offset，没有payload上传。布局另在+58，不能混用。此结论不扩大到未证GL/packaged-layout等其它路径。
- [ ] owner/linear对照两个钉住的完整上游3D创建、上传、反向读取与writer，列出可薄接域及真实差异；不以自写平面算法冒称完整3D。

## Task 2：在证据域内实施和验证

- [ ] 合同闭合后root定最小通用域/影响面，owner先自有CPU真实RED再薄接；保builder原metadata、正确volume/mip/region边界、权限/溢出/empty及失败状态。
- [ ] 独立真实MTLType3D多深度采样/CPU反向读写与padding canary，覆盖跨GOB的z变化；所需IMAGE/packaged语义按原合同验收，未支持消费者仍受控拒绝。
- [ ] 默认旧2D/array/Cube/单slice3D与private authority/descriptor生命周期必要回归，双独审冻结后精确commit并push。

## Task 3：完整候选与真机

- [ ] 固定连贯提交完整测试、精确head双CI与官方无游戏工件核后本机签名，保数据更新当前iPhone12，继续核UID/RomFS/shader。
- [ ] 独立普通运行取得下一实际停止点或真实画面；通过操作、音频、存档及重开才可称可玩。

已核flag4为IMAGE，32³format25自动布局size131072/align512；精确GOB/offset及whole-volume fixture仍待独立闭合。既有完整vendor已有is3d/z0和volume apply/extract；桥当前upload硬拒slices!=1、CPU按arraySlice/availableLayers处理depth、Invalidate只读首slice，必须统一成真正3D z-volume与单allocation authority/certificate，不能只删Init拒绝。后端资源ShaderWrite与IMAGE消费者执行的支持需明确分界；未支持命令保持受控拒绝。
