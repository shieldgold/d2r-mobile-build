# BOTW 原生图形桥

这部分把真实 NVN 对象和命令翻译为 Metal。`graphics.m` 已由人工夹具在本机真实 GPU 上验证缓冲拷贝、共享内存可见性、异步围栏、清屏、已登记着色器的管线绘制与像素读回。它目前覆盖 77 个 NVN 接口；完整游戏需要更多状态、资源和命令。

## 宿主接入

1. 在进入客体前调用 `botw_graphics_init(layer, error, validator)`。`layer` 是宿主持有的 `CAMetalLayer`，离屏测试可传空。`validator` 核验客体可读内存区间，着色器控制段及扩展到宿主页边界的内存池依赖它。
2. 强制将游戏的 `nvnBootstrapLoader` 导入/导出解析到 `botw_nvn_bootstrap_loader`，避免执行客体 NVN 驱动。BOTW 此入口定义在 `subsdk0`，不是 `sdk`。
3. 准确离线翻译并验证资源绑定后，调用 `botw_graphics_register_shader` 登记原始控制段、GPU 数据和本地 MSL。运行时对两段原始数据进行完整 SHA256 匹配，没有匹配则 `nvnProgramSetShaders` 返回失败。
4. 编译 `graphics.m` 时开启 ARC、blocks，链接 Foundation、Metal、QuartzCore。`tools/validate_msl.m` 是独立命令行工具，不要编入 App。

接口查询可返回尚未实现的公开 NVN 函数地址；真正调用时按名字报错并终止。未知名称返回空；布尔接口出错返回 0，不以无操作成功代替实现。`implemented.inc` 是实现表，`unsupported.inc` 是明确失败表。每个已实现接口与公开 NVN 原型有编译期签名断言。

当前纹理实现仅支持带线性标志的单级 RGBA8/BGRA8 二维颜色纹理，直接使用 Metal buffer-backed texture。未实现块线性/压缩纹理、深度模板、多颜色附件、顶点输入布局、纹理/采样器和 uniform 绑定、混合、索引绘制、计算与嵌套命令。提交立即 commit；围栏只在 GPU 实际完成后放行。内存池优先零拷贝共享映射；不能验证页范围的非对齐 uncached 池拒绝创建，cached 池的复制路径需要显式 flush/invalidate。

`CAMetalLayer` 路径在此实现真实纹理显示，使用独立的呈现管线完成格式转换及缩放；它不会替代游戏的绘制着色器。真机交换链及显示时序尚需在游戏实际调用到该路径后验收。当前接口要求单个宿主图形所有者线程；并发录制必须先补对象表与资源同步。

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

真实本地 BOTW base `main` 的两个内嵌 BNSH 包含 34 个变体、68 个阶段记录和 37 个去重 GPU 程序；37 个均已成功转换且通过本机 Metal 源码编译。该证据只证明语法链可用。外部编译器环境仍默认 `ReadCbufValue=0`、二维纹理类型等，NVN 资源槽、c1 常量、顶点布局与原点/深度约定没有经过等价验证，不能据此声称游戏画面正确，生成结果不会自动登记到运行时。

## 验证

`python3 -m unittest discover -s tests -p test_botw_graphics.py -v`

测试夹具全部人工生成，包含 BNSH 恶意边界、局部提取、真实 Metal 拷贝/清屏/绘制/围栏和未实现调用失败。游戏输入、派生 shader、密钥和本地报告都不进入 Git。

上游通用 ABI、对象边界和 Metal 内存/完成时序可复用；D2R 的 `shader_map.py` 依赖 Switch/PC D2R 对应包的 DXBC 顺序映射，`nvn_mtl.m` 含大量 D2R 专用着色器和 UI 特例，因此这里没有复制其游戏身份表或游戏特例。参考固定上游 [e2a0c9a](https://github.com/jhaizhou-ops/d2r-mobile/tree/e2a0c9a9b5d65f6cd505e967e4760e2745e90ca8/dm/gfx) 与 [BNSH 格式来源](https://github.com/maierfelix/bnsh-decoder/blob/master/BNSH.ksy)。
