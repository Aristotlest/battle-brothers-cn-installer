# 战场兄弟 Steam 中文安装器

**v0.2.0-beta.1 修订版 · Windows x64 · 预发布，非稳定版。**

原 beta.1 附件已替换为阵亡记录修复版。此前下载过的用户请重新下载 EXE，并重新“一键安装”。适配游戏 1.5.2.3 的 `getObituaryInfo`，修正“斩首被强盗”为“被强盗斩首”。旧存档已有文字不追溯修改；原始转换日志保留，安装器另显示修复校验结果。

非官方 Steam《Battle Brothers / 战场兄弟》中文安装器。公开使用入口只有一个 GUI 文件 `BB-CN-Installer.exe`：无需安装 Python，无需解压安装器目录。单文件运行时会临时展开运行库；不等于完全离线安装。

项目：<https://github.com/Aristotlest/battle-brothers-cn-installer> · [预发布说明及验证边界](RELEASE-NOTES-v0.2.0-beta.1.md)

## 怎么用

1. 从本项目 Releases 下载 `BB-CN-Installer.exe`，双击运行。不用安装 Python。
2. 先退出游戏。只支持 Windows Steam 版 `1.5.2.3`，建议先备份存档。
3. 缺少启动器时，点“作者下载页”，下载 **Steam v1.2.2**，解压。回到安装器，选择里面的 `bb-launcher-steam.exe`，点“重新检测”。不要选 GOG 版。
4. 确认游戏目录正确，点“一键安装”。首次需要联网下载转换器；阅读警告后，自行决定是否继续。
5. 安装完成后看“Steam 启动项”的结果。默认不会自动修改 Steam：点“复制启动命令”，先保存 Steam 里原来的启动项，再到 Steam 库中右键游戏 > 属性 > 通用 > 启动选项，替换为复制的命令。
6. 从 Steam 库中启动原来的 Battle Brothers，不要添加非 Steam 游戏。

想自动设置启动项：先关闭所有游戏、保持 Steam 登录，再勾选“允许临时重启 Steam”后安装。这会临时重启 Steam；失败时按上面第 5 步手动设置，不要把“文件安装成功”当成“Steam 设置成功”。

卸载：退出游戏，打开安装器，确认目录后点“卸载还原”。手动设置过启动项的，需要自己恢复此前保存的原值；自动设置过的按界面提示授权恢复。不要提前删除游戏里的 `.bb-cn-installer` 备份目录。

遇到问题：打开“高级选项 / 日志”查看原因。提示目录或启动器未通过就重新选择；下载失败就检查网络；提示中断事务就先点“恢复中断事务”。不要反复强行覆盖，分享日志前检查是否含个人路径或参数。

注意：不是完全离线包。阵亡记录的中文拼接已有针对性修复及字节码执行验证，游戏内阵亡界面尚未验证；第三方 MOD 兼容性和实际成就解锁不作保证。下面是组件来源和详细说明，普通用户无需看开发命令。

## 支持范围与组件来源

| 组件 | 固定版本 / 来源 | 获取方式 |
| --- | --- | --- |
| 游戏 | Steam AppID `365360`，Windows FileVersion `1.5.2.3` | 用户合法持有的本机游戏 |
| 汉化文本 | shabbywu/Battle-Brothers-CN，提交 `b72af34749a31aee59139ba4e2455f65f924a5a7` | EXE 内置未修改的完整源 ZIP、GPLv3 全文和归属说明 |
| 转换器 | shabbywu/bb-translator `v2.0.0` | 首次从官方固定地址下载，SHA256 校验后缓存；不内置 |
| 中文渲染启动器 | awesome-battle-brothers **Steam v1.2.2** | 首次从作者页面下载、解压并导入 `bb-launcher-steam.exe`，必须匹配固定 SHA256；不内置 |

转换器版本不是汉化文本版本。固定 URL、版本和 SHA256 见 [`dependencies.json`](dependencies.json)。不支持 GOG、非 Steam 版本；不承诺游戏 `1.5.2.4`、其他版本或混用其他文本源。安装有版本门禁；卸载 / 恢复允许在游戏更新后继续进行，但仍须通过状态、路径和哈希验证。

**不是完全离线包。** 内置文本不意味着内置转换器或启动器，也不代表已完成许可证完整的自建转换器。下载或校验失败会停止，不静默跳过固定值检查。

## 使用前必读

- **阵亡记录修复与原始日志分开显示。** 游戏新版把描述移至 `getObituaryInfo`，安装器已校验指令链并修正中文顺序；上游 `player / onDeath` 失败日志仍原样保留，另存 `obituary-repair.json`。不匹配即停止准备，不以用户接受警告代替修复。
- **第三方 MOD 兼容性未验证。** 仅从原版 `data/*.dat` 生成，不合并 `data/*.zip` 中的 MOD。
- **不保证 Steam 成就解锁。** 保留原版程序和 Steam API、从 Steam 原游戏入口运行是集成边界，不是实际成就触发证明。不要使用成就解锁器验证。
- 当前修订附件未重新进行游戏运行、读档或阵亡纪念册界面验收。旧附件的中文菜单、大地图、任务与 `0811` 加载属历史证据，不冒充本附件验证。旧存档已有名字及阵亡描述不追溯修改。

## GUI 安装

1. 退出游戏，双击 `BB-CN-Installer.exe`。启动时只读发现 Steam 库和受支持游戏，检查现有安装位置及下载目录中的指定启动器文件。只有唯一且校验通过的游戏才自动选中；未找到或存在多个目录时，点击“更改…”选择，不猜测写入目标。
2. 缺少启动器时，点击“作者下载页”，从[作者 Nexus Files 页面](https://www.nexusmods.com/battlebrothers/mods/719?tab=files)取得 **Steam v1.2.2**，解压后选择 `bb-launcher-steam.exe`，再“重新检测”。不是 GOG 版本。需要登录时请在作者网站自行完成；安装器不绕过登录、不收集凭据。
3. 核对游戏目录、启动器和校验状态。**“允许临时重启 Steam…”默认关闭**；保持关闭时，一键安装不会修改 Steam，完成后须手动粘贴启动项。
4. 点击“一键安装”，确认目标写入。程序下载并校验转换器，复制原版 `.dat` 到游戏之外的隔离缓存，在本机生成汉化包。准备阶段不写入游戏目标文件。
5. 阅读转换警告：默认保守摘要，可切换到完整原始输出并查看日志路径，窗口可滚动。接受才继续；取消 / Escape / 关闭窗口均不接受警告。摘要不会把已知兼容失败隐藏为普通进度。
6. 分别查看“游戏文件”和“Steam 启动项”结果。文件校验通过不等于 Steam 已设置；Steam 可选步骤失败时，汉化文件可能已安装，须按提示核对，不能视为整项成功。
7. 默认流程下点击“复制启动命令”，先保存 Steam 当前启动项，再到原游戏 → 属性 → 常规 → 启动选项核对并手动替换。**复制成功不代表设置成功。** 编辑路径会使旧校验结果和复制资格失效。

启动命令形如（由安装器生成实际绝对路径）：

```text
"游戏绝对路径\win32\bb-launcher-steam.exe" %command%
```

不要盲目拼接旧参数，不要添加非 Steam 快捷方式；保持 Steam 正在运行，从原游戏 AppID `365360` 入口启动。游戏可能显示 Mod warning；该提示本身不能证明成就是否可用。

“高级选项 / 日志”保留手动路径、缓存、分步准备 / 安装、检查 / 诊断、恢复中断事务和 Steam 设置 / 恢复。已有未知同名文件默认拒绝覆盖；只有明确勾选“备份并替换”才允许继续。

## 可选：授权临时重启 Steam 后自动设置

此步骤独立于文件安装，**默认不授权**。勾选后仍需确认；请先退出全部游戏，保持同一账户登录，操作期间不要编辑启动项或切换账户。

授权路线正常退出 Steam，临时启用仅本机调试，通过 CDP 连接到已验证归属的 Steam 上下文，仅针对 AppID `365360` 使用原生 `SteamClient.Apps` 接口：先持久备份原值，写入前比较当前精确值，再设置并通过新订阅读回；结束后恢复普通启动。**从不直接编辑 `localconfig.vdf` 或其他 Steam VDF 配置，不用坐标猜测输入框。** 原生 API 读回不是属性页关闭 / 重开的验证。

兼容性证据仅限本机含 **Millennium** 的 Steam；内部接口并非稳定公开 API，不保证其他客户端、无 Millennium 环境或未来版本兼容，也不要求用户为此安装 Millennium。未授权重启时，高级选项中的语义 UI 路线也只会在能验证控件身份时操作；不支持的 CEF 语义树会被拒绝，不能当作通用自动填写保证。

### 精确备份与恢复

自动设置前在游戏 `.bb-cn-installer/launch-options.json` 持久保存原字符串 `original`、目标字符串 `applied`、各自 UTF-8 SHA256、固定 AppID、游戏路径及阶段。重复设置不覆盖活动记录的首次原值；不会将旧参数拼接到目标命令。

恢复仅在当前值**精确等于 `applied`** 时写回 `original`；原值为空则清空。当前值已是原值时只确认恢复。检测到用户 / 第三方改动、记录不可信或无法确认读回时拒绝覆盖，保留备份。不要删除记录来绕过检查，也不要公开上传它：其中可能包含本机路径和敏感的旧参数。

## 文件安全、卸载与中断恢复

仅安装两个游戏文件：

- `data/zdata_cn.zip`
- `win32/bb-launcher-steam.exe`

在游戏 `.bb-cn-installer` 保存原文件备份、SHA256、状态、暂存和写前事务日志。不会修改 `BattleBrothers.exe`、`steam_api.dll` 或存档，也不会删除整个 `awesome-battle-brothers` 目录。不可信链接 / 路径、运行中的游戏 / 启动器、版本或清单不符、文件校验失败时拒绝相关修改，不强杀游戏进程。

安装 / 卸载采用同卷暂存、逐文件原子替换与持久事务日志，普通异常尝试回滚；重启后有未完成日志时先用“恢复中断事务”。恢复前验证日志、目标和备份。它不是文件系统级多文件原子事务，也不保证断电时设备已落盘；操作中不要运行游戏或手动改目标文件。

“卸载还原”先恢复有自动管理记录的 Steam 启动项，再恢复原有目标文件或删除本次新增文件。若文件已被外部修改，或 Steam 原值恢复未确认，会停止并保留备份供核对 / 重试；有记录时可能需再次授权同一 Steam 路线。可单独用“恢复 Steam 原启动选项”。**没有自动记录的手动启动项必须自行恢复，不会猜测或清空。**

Steam 与游戏文件不是跨系统原子事务：Steam 已恢复而后续文件卸载失败时，文件事务需独立恢复后重试。恢复成功后也保留 Steam 原值证据；不要提前删除 `.bb-cn-installer`。

默认持久缓存为 `%LOCALAPPDATA%\BB-CN-Installer`，须预留原版 `.dat` 副本和转换空间。完整 stdout / stderr 在 `prepared-*/translator.log`；日志含本机路径，分享前脱敏。隔离副本和生成的 `zdata_cn.zip` 含游戏内容，**不得上传、入库或再分发**。不再需要时可清理缓存，但不要把缓存与游戏内卸载备份混淆。

## 当前验证证据与边界

本版修订附件 `BB-CN-Installer.exe`：20643157 字节，SHA256 `9955d0b6befcc36754fb68cbd1e8cf628f212cf3f03b75794101d1ecb0fb66da`。

- 源码测试 201 项通过，无跳过。
- `ondeath-fix/gui/result.json`：完整默认 GUI 验收通过，26 项断言与 59 份证据哈希已核对。明确副本目标、全文警告核对、取消无写、新一键接受安装、独立健康校验、复制精确、GUI 卸载原始文件还原、正常退出 0，Steam 重启权限关闭。卸载留下两个内部 undo 文件，无活动 state / journal。
- `ondeath-fix/frozen-cli-final/result.json`：当前 EXE 的 9 个参数场景符合预期，包括旧未修复包拒绝及明确替换 / 还原。
- 当前生成 ZIP 2359 条目 CRC 通过；与旧转换相比只有 player.cnut 内容变化，其余 2358 条逐字节一致。实际 getObituaryInfo 字节码在独立 Squirrel VM 的 9 种死亡分支执行通过；游戏 API 为隔离替身，不是游戏内界面验收。
- 当前 EXE 未重新运行游戏、加载存档、触发成就或重新实测 Steam 自动设置。旧附件的中文 / 读档 / Steam 自动设置证据仅属旧哈希 `8263f30c...`，不继承为新附件实测。
- **Steam 原入口与当前生成包同次联合运行此前选择跳过，仍未验证。游戏内阵亡纪念册显示、第三方 MOD、实际成就触发未验证。**

原始证据仅本机保留，不公开游戏、存档、真实日志或 Steam 备份。参数入口仅内部验收，不另发 CLI / onedir。详见[修订说明](RELEASE-NOTES-v0.2.0-beta.1.md)。

## 从源码运行、测试与单文件构建

源码运行需带 tkinter 的 Windows Python；已验证构建环境使用 Python 3.14。运行期仅用标准库；Steam 桥另调用 Windows PowerShell / .NET。以下命令在仓库根目录的 **Windows PowerShell** 执行，使用独立 venv，不依赖某个开发者机器的路径：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
$env:BBCN_TEST_SCRATCH = Join-Path $PWD '.test-scratch'
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe bb_cn_installer.py
```

构建必须使用固定 [`requirements-build.txt`](requirements-build.txt)（PyInstaller `6.22.3`）。明确指定固定文本归档的本机输入 / 下载缓存位置：已有文件必须匹配 `dependencies.json`，缺少时构建脚本从固定官方地址下载。

```powershell
New-Item -ItemType Directory -Force build-inputs | Out-Null
$env:BBCN_TEXT_SOURCE = Join-Path $PWD 'build-inputs\translation-source.zip'
.\.venv\Scripts\python.exe build_onefile.py
```

`build_onefile.py` 先跑测试、收集 Python / Tcl / Tk / PyInstaller 许可，校验文本源并嵌入，输出 **`dist-onefile/BB-CN-Installer.exe`**。旧 `build.py` 的 GUI / CLI onedir 是历史内部工具，不是本版公开交付路径。不要将构建缓存、真实组件缓存、测试日志、隔离游戏副本或生成补丁混入源码 / 发布包；源码参数入口仅供开发诊断，先读 `--help`，涉及写入仍须显式授权。

## 许可与分发边界

安装器原创代码为 MIT，见 [`LICENSE`](LICENSE)；汉化文本保留 GPLv3。单 EXE 将完整未修改文本源 ZIP、GPLv3 全文及 `TRANSLATION-NOTICE.txt` 作为独立资源内置，并包含项目及运行库许可。第三方内容不改按 MIT 授权，详见 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)。

不随包发布转换器 / 作者启动器二进制、游戏原始文件、存档或生成游戏脚本。上游源码许可证不自动解决其另行发布二进制包中所有依赖 / 字体的再分发条件；本版采用官方获取 / 用户导入，不声称已确认这些完整二进制包的再分发许可。项目与 Overhype Studios、Valve / Steam、汉化作者无隶属关系。
