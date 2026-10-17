# BOTW 1.9.0 作者原版基线

目标是在已登记的 iPhone 15 Pro 上复现作者原版，再分别验证画面与操作、声音、保存和重开。安装和启动不等于可玩。

## 固定源码与版本

- 上游：[seedds/nxnative](https://github.com/seedds/nxnative)，固定提交 `d1d68e80f8331ab85465f763d2cb457c3e2450d5`。
- 本地源码：当前工作树的 `outputs/nxnative-official-1.9-upstream`。不混入此前 `experiments/nxnative_base_v0`、Eden 或本地 1.0.0 宿主修改。
- 原版入口：`scripts/build_ipa_from_nsp.py`；档案：`dm/games/01007EF00011E000/game.tsv`。
- package / shader_versions 均为 `v1114112`，显示版本 1.9.0；模块为 `main subsdk0 sdk`。
- 原版会逐项检查以下 NSO build ID，文件名标有 1.9.0 不算通过：

| 模块 | 预期 build ID |
| --- | --- |
| main | cd57b23fa4bbad65803d9788c01821ee |
| subsdk0 | f39f4c431234c72eb520cfdd1001bbcf |
| sdk | 574284b40c06cf1821a44857939083be |

使用仓内 `dm/gfx/tools/dmsh_xlate_bin` 及对应冻结链，不重新发布或替换翻译器。

## 2026-10-17 实际准备结果

原始输入位于主项目的 `[塞尔达传说 旷野之息].The Legend of Zelda Breath of the Wild`，均保持原件：

| 输入 | 字节 | 当前验证边界 |
| --- | ---: | --- |
| 基础 v0 NSZ | 9,621,216,723 | PFS0 边界和目录通过，7 个成员 |
| 1.9.0 v1114112 更新 NSP | 991,994,208 | PFS0 边界和目录通过，6 个成员 |
| DLC 01007EF00011F001 v196608 NSZ | 1,059,002,315 | PFS0 边界和目录通过，5 个成员 |
| DLC 01007EF00011F002 v196608 NSZ | 56,570 | PFS0 边界和目录通过，5 个成员 |

尚未解密合并更新，未验证上述三个 1.9.0 模块。现有已解包 RomFS 的 `System/Version.txt` 为 1.0.0，不能充当原版 1.9.0 输入。元数据扫描未找到用户目录中的 `prod.keys` 或 `title.keys`；上游解包器从 NSP 的 ticket 读取 title key，只另需用户本地 `prod.keys`，不需要另找 `title.keys`。不输出密钥内容。

独立工作目录已建立在 `/Users/shield/Developer/GameInputs/BOTW-1.9.0`，使用 APFS clone 准备四份输入副本。原版脚本只扫描同层 `.nsp`，所以基础和两份 DLC 的 NSZ 须先用 [NSZ 工具](https://github.com/nicoboss/nsz) 转为 NSP；不能直接改扩展名。

工具准备：

- 复用 .NET SDK 10.0.402 和现有 SPIRV-Cross，工作目录 `tools/bin` 已链接。
- 原版 `nx_extract` 已编译至 `01007EF00011E000_build/tools/nx_extract`，0 警告、0 错误，并验证可启动。
- XcodeGen 2.46.0 已安装；`tools/venv` 已安装 lz4、capstone、fonttools、nsz，原版脚本 `--help` 和 Python 依赖导入通过。
- 本机仅有 `/Library/Developer/CommandLineTools`，尚无完整 Xcode。Apple 官方 Xcode 下载页已打开，当前要求本人登录。
- Mac 实读可用空间约 195 GiB，NAS 约 18 GiB，后者不适合本次完整中间产物。

本次本地审计报告为工作树 `outputs/botw-official-1.9-input-audit.json`；解包器构建日志位于独立工作目录 `01007EF00011E000_build/logs/nx_extract_build.log`。游戏内容、密钥和衍生产物不进入 Git。

## 输入补齐后的接续

1. 获取用户已有 `prod.keys` 的本地路径；也可使用完整合并后的 1.9.0 ExeFS + RomFS，按上游手动构建文档执行，并先核对三个模块身份。若采用完整已解包输入，DLC 需另有对应解包数据才能启用。
2. 安装完整 Xcode、完成必要组件准备，验证 iPhoneOS SDK 和 Metal 编译器可用。保留目前 CoreDevice 独立工具的设备控制能力。
3. 在独立工作目录内解压 NSZ。使用原版脚本、档案及翻译器，传入 `--keep-extracted` 保留输入，并使用独立 bundle ID `org.nxnative.01007ef00011e000`，避免覆盖旧实验容器。
4. 沿用用户已授权的 iPhone 15 Pro 签名和安装测试。先实读手机可用空间；若不足，先明确备份与核验目标再释放空间。旧资源、保存、Mac 完整备份保持可恢复。
5. 取得真实游戏帧和操作证据后，再测试声音、保存和重开。若原版出现问题，以其实际日志和模块身份判断，不回退到继续给 1.0.0 加补丁。

标准入口环境示例（`BOTW_KEYS` 须指向用户已有文件；完整 Xcode 与 NSZ 转换须先完成）：

```sh
BOTW_INPUT=/Users/shield/Developer/GameInputs/BOTW-1.9.0
BOTW_TREE=/Users/shield/.codex/worktrees/botw-native-preflight/d2r-mobile-build
export DOTNET_ROOT="$BOTW_TREE/.tools/dotnet-10.0.402"
export PATH="$BOTW_INPUT/tools/bin:$BOTW_INPUT/tools/venv/bin:$PATH"
python "$BOTW_TREE/outputs/nxnative-official-1.9-upstream/scripts/build_ipa_from_nsp.py" \
  "$BOTW_INPUT" -k "$BOTW_KEYS" --keep-extracted \
  --bundle-id org.nxnative.01007ef00011e000
```

此次仅记录原版路线与已核验准备条件，没有生成原版 IPA，也没有原版真机可玩结论。
