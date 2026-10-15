# BOTW 原生图形桥

这部分把真实 NVN 对象和命令翻译为 Metal。`graphics.m` 已由人工夹具在本机真实 GPU 上验证缓冲拷贝、共享内存可见性、异步围栏、清屏、已登记着色器的管线绘制与像素读回。它目前覆盖 117 个 NVN 接口；完整游戏需要更多状态、资源和命令。

## 宿主接入

1. 在进入客体前调用 `botw_graphics_init(layer, error, validator)`。`layer` 是宿主持有的 `CAMetalLayer`，离屏测试可传空。`validator` 核验客体可读内存区间，着色器控制段及扩展到宿主页边界的内存池依赖它。
2. 强制将游戏的 `nvnBootstrapLoader` 导入/导出解析到 `botw_nvn_bootstrap_loader`，避免执行客体 NVN 驱动。BOTW 此入口定义在 `subsdk0`，不是 `sdk`。
3. 准确离线翻译后，使用 `botw_graphics_load_shader_bundle` 加载本地私有包，或用 `botw_graphics_register_shader_with_bindings` 登记原始控制段、GPU 数据、本地 MSL 和逐阶段 NVN→Metal 显式资源槽映射。无资源的着色器可用 `botw_graphics_register_shader`。运行时完整 SHA256 匹配两段原始数据；管线还用 `MTLBinding` 核验每个实际资源均有映射，并核对纹理维数、uniform 大小/对齐、顶点读取范围。
4. 编译 `graphics.m`、`shader_bundle.m` 时开启 ARC、blocks，链接 Foundation、Metal、QuartzCore。`tools/validate_msl.m` 是独立命令行工具，不要编入 App。

接口查询可返回尚未实现的公开 NVN 函数地址；真正调用时按名字报错并终止。未知名称返回空；布尔接口出错返回 0，不以无操作成功代替实现。`implemented.inc` 是实现表，`unsupported.inc` 是明确失败表。每个已实现接口与公开 NVN 原型有编译期签名断言。

纹理支持线性 RGBA8/BGRA8 的 buffer-backed 路径，以及 Tegra 块线性二维纹理的逐 mip 解排布和 GPU 上传；布局代码复用固定上游，保留 deko3d 原 zlib 许可。精确格式映射支持的颜色格式可用于采样，ASTC 走原生压缩采样；BC 解码、非恒等分量 swizzle、数组/立方体和多采样仍明确拒绝。Depth16/Depth32F 支持真实深度清除、比较和写入。uniform、纹理/采样器描述符池、显式资源槽、常见顶点格式/流步长已接通；storage buffer 支持逐个/成组绑定及显式 Metal 槽，真实 GPU 已验证同一 NVN 槽的 float4/uint4 别名读取和另一槽的写回。DrawArraysInstanced 已验证非零 first/baseInstance 下的相对顶点/实例编号与两块不同颜色的实际像素；实例属性 divisor>1 仍明确拒绝。模板、多颜色附件、混合、索引绘制、计算与嵌套命令尚待补齐。部分 scissor 的清除会明确失败，避免错误清空整个目标。

提交立即 commit；围栏只在 GPU 实际完成后放行。`shutdown` 使用全局提交完成组等待全部已提交命令后才释放零拷贝客体内存。异步 GPU/呈现回调只更新加锁状态，不调用宿主错误回调，避免跨线程 longjmp。内存池优先零拷贝共享映射；不能验证页范围的非对齐 uncached 池拒绝创建，cached 池的复制路径需要显式 flush/invalidate。

`CAMetalLayer` 路径在此实现真实纹理显示，使用独立的呈现管线完成格式转换及缩放；它不会替代游戏的绘制着色器。`botw_graphics_presented_frame_count` 仅在新着色器绘制对应的 GPU 工作与 drawable 实际呈现都完成后增加，清屏或离屏工作保持 0。开启 `botw_graphics_set_capture_enabled(1)` 后，`botw_graphics_copy_latest_frame` 提供最近完成屏幕帧的 RGBA 像素、尺寸及帧号；读取失败不会触发宿主错误回调。真机交换链及显示时序尚需在游戏实际调用到该路径后验收。

117 个公开 NVN 入口以保留完整 ABI 类型的 wrapper 串行操作对象表、句柄表、命令记录、资源及提交状态；初始化、登记、读回和 shutdown 使用同一保护。错误先在图形实现内部展开 ARC 作用域，释放全部图形锁后才在调用线程触发宿主 error callback，允许受控 siglongjmp；异步 GPU 回调不进入该路径。错误消息按线程保存，嵌套的 GetProcAddress→Bootstrap 查询只由最外层入口回报一次。8 个 pthread 各完成 2000 次独立 Device/CommandBuffer/handle 创建、录制、销毁的人工夹具已通过，并验证 4 种错误回调跳出后均能 shutdown 和重新初始化。

## 本地着色器准备

`python3 -m scripts.botw_native.shaders --image <本地原始内存映像> --report outputs/shaders/report.json --extract-dir outputs/shaders/inputs`

解析器读取未重定位的 BNSH 2.x，验证容器长度、名称、变体/程序/阶段描述符、NVN 控制段和 GPU 代码边界。它仅提取验证后的代码/控制区间，按完整 SHA256 命名，不导出整个模块。目标目录必须新建，避免覆盖已有输入。

固定工具及补丁见 `tools/toolchain.json`、`tools/shader-compiler.Cargo.lock`、两个 `.patch` 文件。外部编译器仅用于本地离线转换，不链接进 App。原仓库有 MPL-2.0 声明，但部分 vendored 文件保留 GPL SPDX 标头，分发工具前需核清其许可；许可信息见 `THIRD_PARTY_LICENSE.txt`。

在 ignored `.tools` 下准备：

```sh
git clone https://github.com/DCNick3/shader-compiler-rs .tools/shader-compiler-rs
git -C .tools/shader-compiler-rs checkout f9f0fbc998279a663d0bc98937a0bde880efc8db
# 下载并核验 toolchain.json 所列 spirv_cross-0.23.1 crate，将其解包为
# .tools/spirv-cross-rust-0.23.1。然后应用两个仅工具侧补丁：
git -C .tools/shader-compiler-rs apply --unidiff-zero ../../native/botw_graphics/tools/shader-compiler-rs.patch
patch -d .tools/spirv-cross-rust-0.23.1 -p1 < native/botw_graphics/tools/spirv-cross-rust.patch
cp native/botw_graphics/tools/shader-compiler.Cargo.lock .tools/shader-compiler-rs/Cargo.lock
```

Apple ARM64 需使用原生 Rust 工具链与 GCC C++ 编译器。`shader-compiler-rs.patch` 删除仅适用 x86 MinGW 的参数，并加入 SPIR-V 和 iOS MSL 2.3 输出；SPIRV-Cross 补丁明确启用 iPhone 15 Pro 支持的 Apple A9+ base vertex/instance。示例环境：

```sh
env PATH="$HOME/.rustup/toolchains/stable-aarch64-apple-darwin/bin:$PATH" \
  RUSTC="$HOME/.rustup/toolchains/stable-aarch64-apple-darwin/bin/rustc" \
  CC=/opt/homebrew/bin/gcc-15 CXX=/opt/homebrew/bin/g++-15 \
  cargo rustc --manifest-path .tools/shader-compiler-rs/Cargo.toml \
  --locked --release -p shader-compiler-cli -- \
  -L native=/opt/homebrew/opt/gcc/lib/gcc/15 -l dylib=stdc++
```

然后在解析命令中加入 `--compiler .tools/shader-compiler-rs/target/release/shader-compiler-cli --compile-dir outputs/shaders/converted`，会为每个去重 GPU 程序生成 GLSL、SPIR-V、MSL 和日志，并逐项保存失败。独立验证本地 MSL：

```sh
xcrun clang -fobjc-arc -Wno-deprecated-declarations \
  native/botw_graphics/tools/validate_msl.m -framework Foundation -framework Metal \
  -o outputs/validate_msl
outputs/validate_msl outputs/shaders/converted > outputs/shaders/metal-validation.json
```

真实本地 BOTW base `main` 的两个内嵌 BNSH 包含 34 个变体、68 个阶段记录和 37 个去重 GPU 程序。解析器现读取真实资源字典及 NVN 槽位，保留 uniform、sampler、输入/输出和 storage 的名称/slot。审计逐 shader 记录编译器环境查询：24 个 shader 读取 c0 的 0x20/0x28（共 92 次），46 次纹理类型查询；其余 13 个没有常量查询。这两个偏移在全部相关 BNSH 变体中分别对应 uTexture0/1 的 NVN slot0/1。

新版工具对未知常量数据或纹理类型明确失败。默认无上下文时实测只转换 13/37。可额外使用 `--require-runtime-2d-textures`，按反射 sampler 槽建立明确的二维纹理约束；编译器直接消费类型约束，不伪造常量缓冲数值或句柄。该路径实际转换 37/37 并全部通过 Metal 源码编译，常量值查询为 0，类型约束解析为 46 次。转换报告保存每个 shader 的查询次数、参数与应用约束。运行时必须通过 Metal 反射检查实际 NVN 纹理类型，若不满足则拒绝绘制；约束不等于已经观察过游戏实际描述符。

转换报告保留 `nvn_bindings_validated=false`，表示仅完成翻译。后续打包器分别核验资源槽、静态 c1 常量及顶点反射；未知驱动常量、动态 c1 索引和 storage buffer 仍拒绝登记。语法编译成功不能当作正确游戏画面。

## 私有运行时包

从已有的提取结果和 MSL 输出创建新的 ignored 目录：

```sh
python3 -m scripts.botw_native.shaders \
  --bundle-inputs outputs/botw-shader-inputs-reflection \
  --bundle-compiled outputs/botw-shader-strict-runtime-2d-final \
  --bundle-dir outputs/botw-private-shader-bundle-ranges-v2
```

包内保留每个原始 control/code/stage 的完整 SHA256 身份、68 个阶段引用、实际 BNSH 资源反射、明确的 NVN→Metal 槽映射、MSL 入口与纹理/顶点约束。NVN uniform slot r 对应 Maxwell c[3+r]，纹理 slot r 对应真实反射已确认的 c0[0x20+8r]；无法据此核实的项带 `unsupported` 及原因，保留身份供诊断。登记数只表明这些 shader 可以准确匹配及绑定；数组纹理、其他 NVN 命令和游戏实际呈现仍有各自的运行时检查。

固定上游 `dm/gfx/tools/dmsh_build.py` 记录 control+0x700/+0x704 为原始 code 内的 c1 起止偏移。本地 37 项全部核实该边界，8 项含真实 256-byte c1，其余为空。打包器只对固定且有界的 uint/float 数据读取作特化，MSL 通过 `as_type<float>(0x原始位模式u)` 精确保留浮点输入；不创建补零的常量缓冲。清单记录 bank、偏移、位模式、标量类型和出现次数，loader 对原始 control/code 和实际 MSL 表达式重新核验。6 个片元程序共 14 处固定读取已因此接通；2 个字体顶点程序的动态索引及 c0/SSBO 仍明确拒绝。

转换器原本将 uniform 数组统一声明为 64 KiB。打包器对完全固定的索引逐资源复制类型并缩小数组，67 个绑定别名的准确范围为 4–560 bytes；实际 GPU 测试已验证缩小后的 16-byte 绑定。5 个含动态 uint2 索引的顶点绑定继续要求声明的完整 64 KiB，清单标记 `declared_dynamic`，不猜测游戏属性或 uniform 值。loader 重新核验数组元素步长、读索引和范围证明，实际绘制还按 Metal 反射验证 NVN 绑定长度。

编译器额外记录 IR storage descriptor 的序号、cbuf bank/offset、数量及写访问标志。真实 37 项中仅两个字体 VS 使用该资源，均为 bank0、0x110、count1、只读；与 BNSH `PerCharacterParamBlock` storage slot0 以及 MSL buffer4/5/6 的 uint/uint2/uint4 别名一致。两个程序的 c0 唯一读取是 data[68]，即 slot0 地址低32位。清单可保留这项 `storage_audit` 证据，同时明确 `access_bounds_validated=false`；动态 c1/SSBO 访问尚未获准登记。

宿主在 `botw_graphics_init` 后、客体所有者线程调用：

```c
botw_graphics_shader_bundle_result result;
char error[1024];
int ok = botw_graphics_load_shader_bundle(directory, &result, error, sizeof error);
```

`directory` 在手机为用户私有 `Documents/botw-shaders`。loader 只读有界相对文件名，拒绝 symlink 和路径穿越，核验文件长度/摘要、NVN 原始头、入口阶段及全部阶段引用。所有 ready 项先完成 Metal 编译预检，逐项从实际 MSL 声明反推资源槽，再与清单和 BNSH 反射交叉核对。成功后 `declared_entries` 是 unique 身份总数，`registered_entries` 是已登记数量，`unsupported_entries` 是仅诊断数量；每个未支持项都在 stderr 记录完整身份和原因。遇到未知 shader 的 `ProgramSetShaders` 仍明确失败。

本机严格编译并实际 Metal 加载该私有包的结果为 `REGISTERED=35 UNSUPPORTED=2 TOTAL=37`。这些文件包含游戏原始区间和派生 MSL，仅保存在用户本地 ignored outputs，不能随源码提交或上传云端。

## 验证

`python3 -m unittest discover -s tests -p test_botw_graphics.py -v`

测试夹具全部人工生成，包含 BNSH 恶意边界、局部提取、真实 Metal 拷贝/清屏/绘制/围栏，以及手工 GOB 地址的彩色采样、顶点流、显式 uniform/texture/sampler 映射、深度遮挡和过短绑定范围拒绝。静态 c1 测试覆盖原始 0.25 常量的实际 GPU 像素、动态/越界索引拒绝，以及 loader 对位模式与次数篡改的拒绝。游戏输入、派生 shader、密钥和本地报告都不进入 Git。

上游通用 ABI、对象边界和 Metal 内存/完成时序可复用；D2R 的 `shader_map.py` 依赖 Switch/PC D2R 对应包的 DXBC 顺序映射，`nvn_mtl.m` 含大量 D2R 专用着色器和 UI 特例，因此这里没有复制其游戏身份表或游戏特例。参考固定上游 [e2a0c9a](https://github.com/jhaizhou-ops/d2r-mobile/tree/e2a0c9a9b5d65f6cd505e967e4760e2745e90ca8/dm/gfx) 与 [BNSH 格式来源](https://github.com/maierfelix/bnsh-decoder/blob/master/BNSH.ksy)。
