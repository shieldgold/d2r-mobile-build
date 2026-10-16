# 固定上游升级：5f → 21f

旧 pin：5f673ae2ea4f90ad46403aa6fbe13537fb557271。新 pin：21f026e0c7fd9fee575a1bfa8d925553bb4db88e。全部新来源按官方 exact git-tree 的每文件 Git blob SHA1 校验，再绑定本组件 SHA256；未执行下载源码。

旧69文件组件、原59文件及初/最终真实编译/link-only/CPU收据均在 ignored 历史中保留。新组件75文件：58 backend、3原宿主声明头、2许可证、4 adapter、1诊断补丁、3自有测试、1 driver、1 source lock、README和本差异说明。当前生产98来源与普通前端未改变。

新增完整 nvn_cmd_audit.c/h 和 nvn_dtex.c/h，生产TU由23到25。前者同时包含正常record/header校验与可选provenance诊断；后者保持延迟纹理句柄在提交时读取、每submit immutable overlay。对象和回放同组更新，包含 DrawArraysInstanced、DepthClamp、LineWidth、PointSize、Deferred texture records 等，不能把新payload交旧replay。

原59文件中15文件有变更，44文件相同：botw_shader.c、botw_shader_manifest.h/m、botw_shader_observation.h/c、hle_nvn.c、include/nvn.h、nvn_mtl.h/m、nvn_obj.h、nvn_protos.h、nvn_refl.h/m、nvn_texown.h/c。source lock逐文件记录新旧SHA，不引入上游shader_map/facts/c1_full等游戏专属表。

新6个环境字面key独立核过：DM_BOTW_SHADER_MISSING的NULL是默认stop而非skip；DM_NVN_ALIASLOG为presence gate返回NULL；CMD_AUDIT/TRACE同时由NULL及cmda_on首门封闭；TEXDUMP_SER为NULL使循环不执行；OBSERVATION_SHA256为NULL且output为NULL，父入口早退。共有98个getenv callsite逐表达式锁定。原默认on优化仍使用明确有效的0，不能以NULL代替。

诊断补丁按21f新基准重生：四个直接截图/burst/observation写入口先受控拒绝；cmda_on在pthread_once/dlsym之前返回false，既不走guest frame-pointer/hash诊断也不接svc_thread_dump，正常cmd_hdr_defect/cmd_rec_defect/cmd_entry_defect/cmd_safe_read不被删除。全模块原源码继续保存。

新pin仍是55.15及1.9对象ABI，textureBuilder80、View16，与当前base-v0 baseline不一致。优先核对用户合法1.9资源匹配，匹配尚未证明；保留base52访问器作为备用路线，不把重写旧版本适配当唯一方向。dirty仍旧void注册，普通pool仍整span NoCopy，故既有checked tracking、分段NoCopy/ACK、virtual rawkind/authority和wholebatch拒绝合同依然必须整接。新AudioRenderer是明确1.9/SDK20.5.22 native-CPU/manual+hostPCM桥；其20固定export/REV15并不是本base0的已证实现，本组件不把它纳入图形生产闭包或假称已接音频。

本组件只做编译、链接而不运行后端；自有CPU不创建Metal设备。没有iOS/原游戏/可玩验证，不能独立切换生产。真正激活时继续完整替换旧图形模型与整组shader链。
