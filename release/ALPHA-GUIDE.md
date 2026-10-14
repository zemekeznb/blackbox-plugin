# Blackbox Plugin — 本地 Alpha 候选包

2026-10-13 更新：安装请先读 [当前完整安装说明](INSTALL.md)，包括图形首次设置、本地脚本和等价逐条命令。默认 install.ps1 现已登记并核验插件；下文旧运行配置流程用 `-RuntimeOnly` 保留。最终干净电脑与真实宿主验收仍待完成。

本文件为 2026-10-11 起的 Alpha 实现新写，内容以候选代码和实际测试为准。

## 发布状态与许可

此包仅供本地准备和验证，尚未公开发布。用户选择暂不授予开源许可；未提供开源许可证，不应公开分发或宣称获得开源授权。版本以 plugins/blackbox/.codex-plugin/plugin.json 为唯一来源。候选包清单记录文件摘要、来源提交和工作树状态；工作树有改动时不等同于可从该提交复现。

## 功能与边界

在 Windows 上，为明确启用的项目保存 Codex 送达的本地事件，回看可识别的执行错误，导出固定范围的 Markdown。安装、项目启用、曾收到事件、真实宿主验证是不同状态。没有异常记录不表示没有失败；宿主没有投递的事件无法补记。不会自动上传、发送记录、调用额外模型或启用其他项目。

当前保存范围包含普通输入、工具调用和返回、最终回复等，不仅是错误。只做有限脱敏，正文仍可能含密码、密钥及业务内容。默认目录为 %LOCALAPPDATA%\Blackbox；技术键 blackbox 和目录名称保持兼容。查看状态时应以输出的实际 state_directory 为准，Windows 打包应用可能有目录映射。

## 环境要求

- Windows；当前候选不承诺 macOS/Linux 的 Hook 集成。
- Python 3.11 或更新版本，标准库即可；当前本机验证使用 Python 3.13.2。最低版本兼容性仍须单独验收。
- Codex 桌面插件宿主。具体已通过的宿主版本与真实事件测试见随候选验收记录；未验证组合不在支持承诺内。
- MCP 进程通过 PATH 上的 python 启动。指定安装 Python 不等于宿主会使用同一解释器。
- Node.js 仅运行界面逻辑测试时需要，阅读和采集无需 Node。

## 安装前检查与本地配置

将包解压到稳定位置，在其根目录打开 PowerShell。以下路径须替换成自己的 Python 解释器：

```powershell
.\install.ps1 -Python 'C:\Path\To\Python\python.exe' -CheckOnly
.\install.ps1 -Python 'C:\Path\To\Python\python.exe'
```

若本机策略禁止运行脚本，请使用系统允许的执行方式或由管理员处理；不要求降低全局执行策略。默认不创建桌面快捷方式；确有需要时使用 -InstallShortcuts，要求同一 Python 目录有 pythonw.exe。安装器配置本地运行环境，并通过官方 CLI 登记插件；不启用新项目，不修改模型/凭据配置，不自动关闭已有阅读服务。只初始化运行环境用 -RuntimeOnly。

包中保留本地 marketplace 配置 .agents/plugins/marketplace.json，插件路径为 plugins/blackbox。脚本检查目标 Codex 能力，再登记并核对缓存文件；也可通过宿主图形市场安装后运行首次设置。阅读页能够打开不等于 Hook 已投递。若宿主没有相应入口，停止并反馈版本，不通过猜测配置格式绕过。

自定义 -StateDir 只影响本地命令和阅读器。若 Hook 宿主没有继承相同的 BLACKBOX_STATE_DIR 环境变量，仍会使用默认目录；请勿把自定义目录配置成功当作宿主采集成功。

## 命令入口

启用采集前须明确接受前述保存范围（包括普通事件及有限脱敏），并只指定要授权的项目：

```powershell
python .\plugins\blackbox\scripts\blackbox.py enable 'D:\YourProject' --accept-capture-scope
```

不带确认选项的 enable 只显示说明，不启用项目或创建数据库。随后在该项目的新 Codex 会话中让宿主执行 `python -c "raise RuntimeError('BLACKBOX_ALPHA_HOST_CHECK')"`，在阅读器中确认对应命令、返回及导出。该合成错误是主动验证样例；在外部终端执行或模拟 Hook 不构成真实宿主验收。新开会话后再做一次。主界面“曾收到事件”仅说明本机已有落盘，持续采集完整性仍未知。

```powershell
python .\plugins\blackbox\scripts\blackbox.py status
python .\plugins\blackbox\scripts\blackbox.py open
python .\plugins\blackbox\scripts\blackbox.py disable 'D:\YourProject'
python .\plugins\blackbox\scripts\blackbox.py stop
```

status 显示配置、事件元信息和容量，不读取会话正文。disable 只停用指定项目，保留历史；stop 只停止阅读服务，不会停用采集。新版本出现 reviewer_build_changed 时，先结束旧阅读服务，再从所需版本打开，不强行覆盖运行实例。

## 备份、恢复与退出

备份必须放在采集项目和 Git 仓库之外；目标文件必须不存在。恢复仅写入一个新的目录，恢复后的所有项目默认停用。

```powershell
python .\plugins\blackbox\scripts\blackbox.py backup 'D:\BlackboxBackups\snapshot.sqlite3'
python .\plugins\blackbox\scripts\blackbox.py restore 'D:\BlackboxBackups\snapshot.sqlite3' 'D:\BlackboxRestore'
python .\plugins\blackbox\scripts\blackbox.py --state-dir 'D:\BlackboxRestore' open
```

备份文件仍含敏感数据。命令执行前先创建备份的父文件夹，恢复目标的父文件夹也必须存在。如果使用自定义状态目录，backup/status/disable/stop 都要在命令名前添加 `--state-dir '实际目录'`，否则会操作默认目录。备份是已验证的 SQLite 快照，只含数据库；运行配置、快捷方式、阅读偏好以及数据库外的诊断文件不会随备份恢复。恢复保守地标记记录连续性未知，不连接恢复边界两侧的操作。

容量达到上限时，新事件被拒收，旧记录仍可查看和导出。恢复同一备份不会缩小库或解除容量问题。若要保留旧库并从空库重新开始：

1. 在 status 中核对实际 state_directory 和所有已启用项目，逐个 disable，并从 Codex 插件管理停用插件。
2. 备份到项目和 Git 仓库之外的新文件，恢复到新的目录；打开恢复目录，检查所需记录并预览导出。失败时保留原库并停止操作。
3. 停止原目录和恢复目录的阅读服务，完全退出会使用该插件的 Codex 宿主，确保不再有 Hook 写入。仅停止阅读服务不够。
4. 在文件资源管理器核对完整路径，把原状态目录整体重命名为一个新的归档目录，保留整个目录；不要只移动或删除 notes.sqlite3，也不要操作包目录或项目目录。此步是人工选择，不由安装器自动执行。
5. 用相同 Python 和原来的状态目录路径重新运行 install.ps1（自定义目录使用 -StateDir）。空库不含项目授权。启动宿主并启用插件，明确重新授权所需项目，再做一次真实宿主检查。自定义目录仍须确认宿主继承 BLACKBOX_STATE_DIR；未确认时不可声称采集恢复。
6. 旧归档可用 `python .\plugins\blackbox\scripts\blackbox.py --state-dir '归档目录' open` 回看。归档目录中的项目配置可能仍保留之前的状态，请勿将宿主或新的 Hook 指向归档；本流程已要求在归档前逐个停用。

若只是退出使用：逐个停用项目，通过宿主卸载插件，停止阅读服务，然后人工删除自己安装的包和可选快捷方式。数据库和备份默认保留，不提供自动清理。确实不再需要历史时，先完成以上备份验证并关闭所有写入者，核对状态目录完整路径，再由用户在文件资源管理器删除该目录。删除不可撤销，不把卸载插件等同于删除历史。

## 预览与分享

在异常详情或旧归档中先选择导出，检查完整 Markdown 纯文本预览（包括来源字段），再点击保存或下载。本地浏览器下载使用当前预览的固定副本；侧边栏保存重新读取同一固定快照，并校验内容摘要与预览一致。切换记录或版本会使旧预览失效。这里只做有限脱敏，分享前仍须检查密码、密钥和业务内容。导出本身不会上传。

侧边栏提供“保存到下载文件夹”：明确点击后，插件把这份已预览的固定快照写到 Windows 实际配置的下载文件夹，显示已校验的字节数与完整路径。预览摘要与保存内容不符时拒绝写入；同名文件保留，新副本自动编号。此操作不启动阅读服务、不改事件或项目开关、不上传，不依赖宿主下载或打开浏览器的接口。本地浏览器阅读页仍使用浏览器下载。保存超时先检查下载文件夹，避免重复保存；磁盘或写入校验故障可能留下本次未完成的文件，不把失败提示当成功。

项目主要列表优先展示能关联的会话。官方查询明确未关联的记录保留在“尚未关联的本地记录”折叠区，仍可查看、预览和导出；这不表示记录已删除、归档或一定来自子代理。临时名称查询失败不会移入该分组。

## 离线回归与候选构建

```powershell
python -B -m unittest discover -s tests -p 'test_*.py'
node --test tests/test_anomalies_ui.cjs tests/test_mcp_reader_ui.cjs tests/test_review_ui.cjs
python tools/build_alpha.py
```

自动化回归使用隔离临时数据，不能替代真实 Codex 宿主事件、干净机器安装和目标用户试用。受限沙箱若无法使用 Windows 临时目录，记录环境限制，不将其算为产品功能失败。构建默认输出到 dist，不上传或发布。

## 尚未完成的放行条件

许可暂不授予；干净机器/账户安装、支持版本矩阵、至少两名独立用户按说明试用尚未验收。Windows CI 已配置，远程尚未运行。Alpha.1 已有本机当前/新会话及桌面重启后的真实采集、停用恢复、本地浏览器导出证据。Alpha.2 会话导航经用户确认；Alpha.3/4 侧边栏下载请求被拒绝（Alpha.4 返回 -32601），Alpha.4 打开浏览器虽收到应答但未打开页面。Alpha.5 改为插件直接保存到本机下载文件夹，真实面板结果单独记录。

在正在执行的长回合中更新插件，旧 Hook 路径可能失效；本轮已实际观察更新后的合成错误未落盘，不能声称热更新无损。更新后应开始新的桌面回合；若仍未采集则完整重启 Codex，再验证一次真实错误。不会补造更新期间未收到的事件。候选包不得标记为发布就绪。

测试数量、候选 ZIP 摘要、解包回归和实际宿主结果由本地验收记录单独给出。自动化覆盖失联会话、502 条正常事件之后的旧异常、固定预览、宿主下载响应/拒绝/超时和过期回复、生命周期指引。这些结果不证明真实宿主投递完整性，也不替代面板实际保存文件与视觉验收。
