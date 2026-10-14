## 必须完成的 Hook 信任检查

安装和项目启用不等于自动采集已获准。安装后在目标项目的终端启动 `codex`，输入 `/hooks`，审阅并信任 `blackbox@personal` 的 Hook。之后刷新 Blackbox 状态并执行一条真实命令验证记录到达。未信任时不会采集；不能用等待首条记录代替此检查。插件身份或 Hook 定义变化后需重新核查。安装器只检查，不自动信任。首次安装并信任 Hook 后，可能需要保存工作、完整退出并重新打开 Codex 才能生效，无需重启电脑；回到原会话继续验证，无需重复授权。

# Blackbox Plugin 安装与首次接入

本候选版本：0.4.0-alpha.6.42。默认用户已安装 Python 3.11+ 和 Codex；当前支持 Windows 上具备本地插件/MCP/Hook 能力的 Codex 桌面宿主。
最低版本与干净电脑的最终验收待完成；本机测试不能替代它。当前仓库为私有，Git 入口需要仓库访问权限。

## 下载 ZIP 自行安装

1. 获取完整 `blackbox-plugin-0.4.0-alpha.6.42-candidate.zip` 和同名 `.zip.sha256`。
   用 `Get-FileHash -Algorithm SHA256 <ZIP路径>` 比对校验值；校验值用于完整性核对，不等于发布者签名。
2. 解压到长期保留的目录，例如 `D:\Apps\Blackbox`。不要只提取 plugins 子目录，也不要在 ZIP 内运行。
3. 在解压目录打开 PowerShell，使用普通账户执行：

```powershell
.\install.ps1 -CheckOnly
.\install.ps1
```

若找不到 Codex，传入当前桌面宿主对应的 **codex.exe**，两条命令都使用相同参数：

```powershell
.\install.ps1 -Codex 'C:\实际路径\codex.exe' -CheckOnly
.\install.ps1 -Codex 'C:\实际路径\codex.exe'
```

脚本不会下载另一份 CLI。不要选其他账户或不相关 CLI。`CODEX_HOME` 若已设置必须属于目标桌面宿主；预检打印有效路径供核对。
完整预检会调用 Codex 的清单命令，宿主可能创建日志/缓存，但不登记插件或开启采集。
Python 可用 `-Python 'C:\实际路径\python.exe'` 指定；MCP 所用 PATH 中仍须有可运行的 Python。
脚本受当前系统执行策略约束；若下载标记或组织策略阻止执行，检查可信来源并按所在组织规定处理，脚本不会修改执行策略。

成功后显示运行配置成功、插件登记及文件校验通过。**宿主 MCP 启动、实际 Hook 投递仍待接入后验证。**
不自动打开阅读服务、不创建快捷方式、不自动选择项目；原先已启用的项目保持原状态。

## 在 Codex 中安装

在宿主插件目录添加本包解压目录对应的本地市场，或有权限访问的 Git 仓库市场
`https://github.com/zemekeznb/blackbox-plugin`，选择 Personal 中的 Blackbox Plugin。
Git 入口以远端实际版本为准，本地新候选不表示已经推送；当前测试优先使用同一 ZIP 的本地目录。

安装后运行插件的首次设置（`blackbox-setup`），完成依赖预检和运行配置。
若宿主未显示设置按钮，可直接请求运行该设置技能；也可在完整 ZIP 目录执行：

```powershell
.\install.ps1 -RuntimeOnly -CheckOnly
.\install.ps1 -RuntimeOnly
```

RuntimeOnly 不检查或更改插件登记，只初始化相同运行环境。首次设置按钮是否出现及完整交互留待干净电脑宿主验收。
图形与脚本入口共用 `blackbox@personal` 身份，不应另建一个同名插件。上传 ZIP 创建插件尚未验证；本包不能宣称支持该入口。

## 高级用户：等价逐条命令

在完整包根目录执行，先确认每一步成功再继续；路径变量按实际填写。

```powershell
$bbCodex = 'C:\实际路径\codex.exe'
$bbPython = 'python'
$bbRoot = (Get-Location).Path
$bbPlugin = Join-Path $bbRoot 'plugins/blackbox'
# 检查包、Python、状态目录；记录输出的 state_directory。
& $bbPython "$bbPlugin/scripts/blackbox_preflight.py" --plugin-root $bbPlugin --json
# 检查宿主能力、既有来源与重复身份；不登记。
& $bbPython "$bbPlugin/scripts/blackbox_install.py" --root $bbRoot --codex $bbCodex
# 使用第一步输出的准确目录，不能另选目录。
$bbState = '第一步输出的 state_directory'
& $bbPython "$bbPlugin/scripts/blackbox.py" --state-dir $bbState configure-runtime
# 官方 CLI 登记并回读核验；内部调用 marketplace add / plugin add / plugin list。
& $bbPython "$bbPlugin/scripts/blackbox_install.py" --root $bbRoot --codex $bbCodex --register
```

以上复用脚本的预检/登记逻辑，不需要额外模型调用。不要在前一步失败后继续执行。

## 升级、来源切换与失败恢复

本版统一为 `blackbox@personal`、`blackbox-review` 技能、`plugins/blackbox` 及 `blackbox_*.py` 脚本。从旧版升级推荐使用完整脚本；它通过 Codex 移除旧 `tas-notes@personal` 安装再登记新插件，避免两套 Hook。旧插件缓存可由 Codex 删除，状态数据库不在该缓存内。

旧默认 `%LOCALAPPDATA%\TAS Notes` 由初始化步骤迁为 `%LOCALAPPDATA%\Blackbox`，文件内容保留，旧目录名保留为指向新目录的兼容别名。原阅读偏好、项目开关和记录不清空。旧 `TAS_NOTES_STATE_DIR` 仍可使用，新 `BLACKBOX_STATE_DIR` 优先；用户明确指定的自定义目录原地保留。

升级前停止旧阅读服务，暂停其他会话工作，完成后重新打开 Codex。正在使用的旧服务或两套独立目录会阻止自动迁移；不会擅自合并数据库。不承诺旧长回合热加载无损。图形安装必须完成 `blackbox-setup` 的旧插件退役步骤；只安装新插件而跳过设置可能保留旧插件。

若旧插件原本停用，脚本保留停用状态。安装失败时可能已退役旧插件；从同一新版包重试，不会删除历史数据。

- 保持解压目录稳定；升级时停止操作旧安装目录，使用新包完整文件替换包内容后重跑安装。不要删除状态数据库。
- `personal` 已指向另一目录时，默认中止；若确认需要从 Git/旧本地目录切换到本包，使用 `-ReplaceSource`（可先加 `-CheckOnly`）。只允许原市场可读且仅包含 blackbox；含其他插件或路径已丢失时，请在 Codex 中人工解决来源冲突，脚本不移除其他插件。
- 切换来源会通过官方 CLI 移除旧市场登记再登记新目录，不调用 plugin remove，不删除记录。若中途失败，保留当前状态并从同一新目录重试；无法保证切换事务原子性。
- 缺 Python、CLI 不兼容、配置目录不可访问：先修复提示项再重试。运行配置成功而登记失败会明确报部分完成，不宣称安装成功。
- 不覆盖模型 provider、base URL、凭据或代理配置；官方 CLI 会更新本插件和市场相关设置。
- `-StateDir` 仅供明确管理环境的用户；Codex 进程必须继承相同 `BLACKBOX_STATE_DIR`，否则宿主会使用另一状态目录。脚本不自动改变持久环境。

## 第一次接入与停用

在 Codex 中指定一个实际项目，请 Blackbox 为该项目开启记录，并确认采集范围。
默认本地保存；已授权工具输入/返回可能含源码、路径和敏感内容，异常页面展示范围小于本地保存范围。
只接收已启用项目中由宿主确认的持久用户会话；临时/内部/身份未确认来源不保存，不事后补回。
“没有异常”不代表执行始终成功；非 Bash 投递、无法识别的返回及长回合热更新存在覆盖限制。
授权后按宿主要求处理信任提示，在新会话真实工作，再检查收到记录与异常；模拟 Hook 不算宿主验收。
停用用现有采集设置或插件技能明确关闭该项目；仅关闭阅读页面/停止阅读服务不会关闭采集。停用保留历史。
卸载通过 Codex 插件管理，只移除 Blackbox 插件；历史数据、Python 与其他插件不自动删除。

## 另一台干净电脑验收清单

- 普通 Windows 账户、未装 Python：提示缺依赖，无运行状态或插件半登记；装 Python 后重新打开终端。
- 校验 ZIP，完整脚本安装；核对目标 Codex、版本、安装载荷，默认没有已授权项目。
- 独立验证图形入口与首次设置；手动命令复用流程。重复安装及两入口切换不新增第二套 Hook。
- 明确授权测试项目，在真实新会话产生记录/可识别错误；核对三个首次状态、多项目统一列表和独立详情。
- 导出、关闭重开、项目停用、重装后历史保留；窄窗口导航与桌面操作。
- 记录 Windows/Python/Codex 版本、入口、下载执行策略提示；真实宿主、最低支持环境未验即保留待验。

设置入口依据：[OpenAI 官方插件打包文档](https://developers.openai.com/plugins/build/plugins)。本机 CLI 行为另由隔离安装测试核对。

## 让 Codex 带你完成接入

在目标项目会话发送与页面一致的提示词：

> 请直接为当前项目完成 Blackbox 本地采集接入。我同意仅记录当前项目的可见输入、回复、工具调用及返回，内容可能包含项目资料或敏感信息，默认不上传。我授权你检查并配置运行环境，审阅已安装 Blackbox 的本地采集 Hook，确认其用途与上述范围一致后，通过 Codex 官方流程完成信任并启用当前项目，无需重复询问。随后执行一条无副作用的真实命令，核验对应记录确已收到。不要改动其他项目、清除历史或模拟记录；只有必须由我操作或超出上述授权时才暂停，并给出最少必要步骤。

Codex 应完成检查、必要授权协助及真实记录核验；不是让用户自行研究 Hook 命令。已有授权不重复询问。只有宿主仍使用安装/信任变更前的后台时，才引导完整退出并提供续验提示词。接入完成以对应真实事件入库为准。
