# 战场兄弟 Steam 中文安装器

## 当前发布版本与支持范围

- 安装器发布版本：`v0.1.1`（Windows x64 onedir，GUI 与 CLI）。
- 支持游戏：Steam PC《Battle Brothers / 战场兄弟》，AppID `365360`。
- 支持游戏版本：`1.5.2.3`。安装动作会校验此版本；`1.5.2.4` 及其他版本不承诺安装兼容性。
- 汉化文本补丁：`Battle-Brothers-CN` 固定提交 `b72af34749a31aee59139ba4e2455f65f924a5a7`；该上游没有采用语义版本号，不能把此提交称为其他版本。
- 文本转换器：`bb-translator v2.0.0`（Windows）。
- Steam 启动器：作者 `awesome-battle-brothers` Steam `v1.2.2`；必须由用户从作者页面下载并导入，安装器不随包分发。
- 不支持：GOG 版本、非 Steam 版本、未列出的游戏版本，以及把其他汉化文本源混用到本安装器的准备流程。

发布包不包含游戏原始文件、存档、`zdata_cn.zip`、上游翻译源码归档、转换器二进制或作者启动器；只包含安装器本身及其运行库许可文件。

项目：<https://github.com/Aristotlest/battle-brothers-cn-installer>

**Windows / Steam AppID 365360 / 游戏 FileVersion 1.5.2.3 专用。** Python 标准库核心、中文 tkinter GUI 与 CLI。非官方项目，与 Overhype Studios、Steam、汉化作者无隶属关系。

## 自动设置 Steam 启动选项（可安全还原）

仍需用户下载作者启动器并确认写入：

1. 从[作者 Nexus 页面](https://www.nexusmods.com/battlebrothers/mods/719?tab=files)下载 **Steam v1.2.2**，解压后选择 `bb-launcher-steam.exe`。不是 GOG 启动器。不绕过 Nexus 登录，不再分发作者启动器。
2. 安装完成后可点击“自动设置 Steam 启动选项”并确认。只有 Steam 提供可验证的 UI Automation 语义树时才允许自动操作：安装器打开固定 AppID 365360 的属性页，重新捕获控件，按语义验证属性页、常规标签和有标签的启动选项输入框；先持久备份原值，再精确替换并关闭 / 重开属性页读回确认。不会拼接旧参数。已有原值会完整保留，重复设置不会覆盖首次备份。

  自动化不是所有 Steam 客户端版本的保证。**必须保持 Steam 正在运行、已登录同一账户，操作期间不要编辑属性或切换账户。** 当前 Steam CEF 不提供可验证的网页 UIA 语义树时，程序会拒绝自动操作并提示手动路径；不会使用相对坐标、窗口标题或剪贴板回读来猜测输入框。属性窗口不唯一、输入框没有语义身份、回读不匹配或重新打开失败时都拒绝写入并提示“未能自动定位，未写入”。不直接编辑 `localconfig.vdf`，不把打开 URI 本身当成成功。

自动设置失败时 GUI 可另行确认“仅复制目标命令”，供手动打开游戏属性 → 常规 → 启动选项后粘贴；该操作不会被标记为自动设置成功。手动设置前必须自行保存原值，不要盲目拼接。若已生成自动备份，请同时核对 `.bb-cn-installer/launch-options.json` 的 `original`（原值）和 `applied`（目标值）。备份包含本机路径及可能敏感的既有启动参数，不要公开上传。

安装器不会直接改 `localconfig.vdf`，不会关闭 Steam，不会添加非 Steam 快捷方式。使用模式为：

```text
"游戏绝对路径\win32\bb-launcher-steam.exe" %command%
```

从 Steam 原游戏入口运行，并保持 Steam 正在运行。此方案旨在保留 Steam 集成，**不保证实际成就解锁**；只有真实游玩才能验证。游戏可能正常提示 Mod warning。旧存档已生成的英文人物、公司、城镇名不保证追溯翻译。

## GUI 使用

双击打包后的 `BB-CN-Installer.exe`，或在已带 tkinter 的 Python 3.12+ 上运行：

```console
python bb_cn_installer.py
```

1. 退出游戏。点击“发现 Steam 游戏”，或选择包含 `data` / `win32` 的游戏根目录。
2. 选择上述官方启动器 EXE。SHA256 必须匹配，不能自动跳过校验。
3. 点击“准备汉化”。下载校验官方转换器和固定提交的翻译文本，仅复制本机原版 `data/*.dat` 到游戏目录之外的隔离缓存，然后生成本机汉化包。准备阶段不写入游戏。
4. 阅读日志与警告，再点击“安装”。**已知转换器可能返回 0 但报告 `modify failed … player … onDeath`**。程序会显著提示；必须明确接受警告才允许安装。不能将此表述为完全验证或全部文本正确。
5. 如游戏已有同名未知汉化文件，默认拒绝覆盖。只有明确勾选“备份并替换”才能继续。
6. 点击“自动设置 Steam 启动选项”，确认设置并查看读回结果；自动定位失败时使用明确提示的手动方案，然后由 Steam 启动游戏。

可保存界面日志。转换器完整 stdout / stderr 保存在 `prepared-*/translator.log`。日志含本机路径，公开前请自行脱敏。

默认持久缓存为 `%LOCALAPPDATA%\BB-CN-Installer`，首次使用须联网并预留足够空间（含完整原版 .dat 副本）。隔离准备目录含用户游戏脚本，**不能上传、加入仓库或再分发**。可在不再需要时自行删除缓存；卸载备份则保存在游戏的 `.bb-cn-installer` 中，不能提前删除。

## 安全边界与卸载

只安装两个游戏文件：

- `data/zdata_cn.zip`
- `win32/bb-launcher-steam.exe`

另在 `.bb-cn-installer` 中保存固定路径状态、SHA256、原文件备份、暂存文件与事务日志。不会修改 `BattleBrothers.exe`、`steam_api.dll` 或任何存档，不会删除整个 `awesome-battle-brothers` 目录。版本或 Steam 清单不匹配、游戏 / 启动器仍运行、下载 / 文件校验失败时拒绝修改；不会杀进程。

安装 / 卸载采用同卷暂存、逐文件原子替换和写前持久日志。普通异常自动回滚；重启后如存在日志，先使用“恢复中断事务”。恢复会核验原 / 新目标哈希和备份。**这不是文件系统级的多文件原子事务，也不保证硬件突然断电时存储设备已落盘。** 不要在写入过程中运行游戏或手动改文件。Windows 命名互斥锁阻止并发安装器写入。拒绝符号链接、junction、reparse point、硬链接与不可信路径；这不是抵御同一账户主动并发篡改的安全沙箱。

点击“卸载还原”先通过 Steam 界面安全恢复自动设置前的启动选项，再恢复 / 删除本安装器管理的两个文件。仅当前值精确等于备份的应用值时才写回原值；原值为空则清空。当前值已是原值时只确认，不重复写入。**用户修改过启动选项则拒绝覆盖并停止卸载**；自动定位 / 保存验证失败也停止卸载，保留文件和备份供重试。可单独点击“恢复 Steam 原启动选项”，不卸载游戏文件。没有自动记录的手动设置项不会被猜测或清空，须自行核对恢复。

启动选项使用独立的严格 JSON 状态 `launch-options.json`（schema 1、固定 AppID、精确游戏路径、原值 / 应用值及各自 UTF-8 SHA256、pending / applied / restored 阶段）。写入前先持久保存 pending，启动选项恢复成功后保留 restored 记录，不删除原值证据。不可信 / 多余字段、重复键、哈希不符及链接会被拒绝。Steam UI 与游戏文件是两个系统，**不是跨系统原子事务**：Steam 恢复成功但后续文件卸载失败时，Steam 保持原值，文件事务独立恢复后可重试。不要提前删除 `.bb-cn-installer`。

第三方 MOD 兼容性**未验证**：本版只使用原版 `.dat`，不合并 `data/*.zip`。请先自行管理其他 MOD，不能声称任意 MOD 都兼容。

## CLI（无需管理员权限）

无参数打开 GUI。`--diagnose` 是只读：

```console
python bb_cn_installer.py --help
python bb_cn_installer.py --diagnose --game-dir "D:\SteamLibrary\steamapps\common\Battle Brothers"
python bb_cn_installer.py prepare --game-dir "D:\SteamLibrary\steamapps\common\Battle Brothers" --launcher "D:\Downloads\bb-launcher-steam.exe" --cache-dir "D:\BB-CN-Cache"
python bb_cn_installer.py install --game-dir "D:\SteamLibrary\steamapps\common\Battle Brothers" --launcher "D:\Downloads\bb-launcher-steam.exe" --prepared "D:\BB-CN-Cache\prepared-...\prepared.json" --accept-warnings --yes
python bb_cn_installer.py check --game-dir "D:\SteamLibrary\steamapps\common\Battle Brothers"
python bb_cn_installer.py steam-set --game-dir "D:\SteamLibrary\steamapps\common\Battle Brothers" --yes
python bb_cn_installer.py steam-restore --game-dir "D:\SteamLibrary\steamapps\common\Battle Brothers" --yes
python bb_cn_installer.py uninstall --game-dir "D:\SteamLibrary\steamapps\common\Battle Brothers" --yes
python bb_cn_installer.py recover --game-dir "D:\SteamLibrary\steamapps\common\Battle Brothers" --yes
```

只有读完警告后才传 `--accept-warnings`；未知既有文件另须 `--replace`。返回码 0 表示命令通过，2 表示拒绝 / 不健康 / 失败。CLI 输出 JSON。无需修改环境变量绕过版本；生产 CLI 不提供版本或哈希校验关闭开关。

可将已下载的官方 ZIP 放入指定缓存，程序仍会校验：

- `bb-translator-windows.zip`
- `translation-source.zip`

固定 URL、版本和 SHA256 全部在 `dependencies.json`；HTTP 失败可尝试官方 GitHub release asset API，哈希不符则停机。GitHub 重新打包同提交 archive 如改变字节，也会拒绝而不是静默接受。

## 测试与构建

运行期无 PyPI 依赖；Steam UI 桥使用系统 Windows PowerShell 5.1 / .NET UIAutomation，固定脚本通过 `-EncodedCommand` 运行，选项仅通过 UTF-8 JSON stdin 传入，不进行 shell 字符串拼接，也不收集凭据。Windows 测试仅使用临时假游戏和测试注入，不接触真实游戏或 Steam 配置；测试含真实复制 / 安装 / 检查 / 卸载 / 故障回滚 / 模拟中断恢复、junction / 硬链接拒绝、恶意状态 / ZIP、VDF、多库、Windows 版本 API、进程读取、Tk 构建和 CLI 子进程。Steam fake backend 覆盖非空 / 空原值、重复设置、用户修改、导航失败未写入、读回失败 / 回滚及严格 schema；PowerShell 自检只加载 .NET 和执行合成语义选择器测试，不打开 Steam。**这些测试不是实际 Steam 客户端自动化成功的证明。** 转换器行为测试使用注入子进程结果，不冒充真实游戏转换的证据。

```console
python -m unittest discover -s tests -v
```

测试默认目录 `~/AppData/Local/hermes/cache/scratch`，可用 `BBCN_TEST_SCRATCH` 指定自有测试目录，绝不自动查找真实游戏。`tdd-test-log.txt` 为本次逐纵切 RED/GREEN 执行记录（仅本机用户目录已替换为 `<USER_HOME>`，含预期失败，不是最终失败）。

在独立 venv 安装 `requirements-build.txt` 后：

```console
python build.py
```

构建脚本先跑全套测试，再调用 PyInstaller 6.22.3，输出 `dist/BB-CN-Installer/BB-CN-Installer.exe`（GUI）与 `dist/BB-CN-Installer-CLI/BB-CN-Installer-CLI.exe`（控制台）。不自动安装依赖，不要求管理员权限，不收集任何游戏 / 启动器 / 缓存。打包为 onedir，避免单文件自解压到系统临时目录；分发须包含整个对应输出目录。

本仓库测试和 GUI 构建不能证明真实游戏中文显示、旧存档加载或成就实际触发。发布验证应另外记录真实隔离转换、游戏 UI 和 Steam 集成观察；不能使用成就解锁器验证。

## 许可

本安装器自有代码为 MIT，见 `LICENSE`。第三方翻译、转换器、启动器、字体及游戏内容不受本项目 MIT 覆盖，见 `THIRD_PARTY_NOTICES.md`。本仓库 / 发布包不含官方启动器、转换器、翻译档案、游戏 .dat 或生成的 `zdata_cn.zip`。
