# Codex 执行的 Blackbox 安装说明

适用于用户在 Codex 中明确要求从 zemekeznb/blackbox-plugin 安装并初始化 Blackbox。源码候选固定版本 **0.4.0-alpha.6.47**。需要 Windows Codex 桌面版、现有 Python 3.11+ 与正常网络；可使用宿主已提供的 Python 绝对路径，不要求 Python 或 codex 在 PATH 上，也不要求 Git、GitHub 登录或手工下载归档。

本执行契约随 .47 候选维护，固定下载入口须等维护者公开发布 .47 后才可使用。本版补充插件 Logo 与输入框图标，沿用 .46 运行绑定；已发布旧版本附件保持不变。

这是 Codex 对话安装入口的执行契约；用户操作步骤见 [安装指南](INSTALL.md)。如果当前目标版本已成功安装，不再下载或切换安装源：从官方插件清单确认实际已安装缓存，再按其中 setup/review 技能完成检查并打开页面。

## 安装授权和范围

用户发送安装提示词授权安装本插件、初始化本地运行环境并打开阅读页面，不授权采集任何项目或授予 Hook 信任。已有历史、项目开关、其他插件及模型/provider/凭据配置保持原状态。不要更改持久 PATH、PowerShell 执行策略或另装 CLI。

## 执行

1. 确认当前是用户目标 Windows 桌面宿主及其 CODEX_HOME。使用已有 Python 3.11+ 的绝对路径；若 `python` 命中 Store 别名或不存在，可通过宿主提供的运行时定位能力选择已存在的 Python。验证解释器可执行，不仅检查文件存在。缺依赖时准确报告，不自行安装 Python、下载另一份宿主或选择其他账号的 CODEX_HOME。
2. 将公开固定版本的引导工具下载到临时目录，不放在用户项目内：
   `https://raw.githubusercontent.com/zemekeznb/blackbox-plugin/v0.4.0-alpha.6.47/tools/bootstrap_blackbox.py`。
   先阅读该文件，确认 VERSION 与本说明一致、下载目标仅为本仓库、实际操作符合用户授权后执行。这个工具是安装过程的内部步骤，不要求客户去终端操作。
3. 用已有 Python 执行临时文件。工具自动定位当前运行桌面的 codex.exe；无运行进程时再检查已配置路径、PATH 与通用用户安装目录，发现歧义即停止，不按个人绝对路径或“最新目录”猜测。
4. 工具从本仓库 .46 Release 下载 ZIP 及摘要，校验外层 SHA256、载荷清单和路径后保存到当前用户 Blackbox-install/0.4.0-alpha.6.47。它不依赖 Git，客户无需手动下载或解压。校验用于完整性，不是独立发布者签名。冲突目录、异常网络或摘要失败不覆盖原件或执行载荷。
5. 工具复用已打包的预检、`configure-runtime --bind-host` 和官方 Codex 插件登记/回读校验。运行绑定将实际 Python 和物理状态目录保存到当前 `CODEX_HOME/blackbox/runtime.json`，供 MCP、Hook 与后续命令共用；不更改系统 PATH。来源迁移只允许替换已确认仅含 Blackbox 的旧源；其他插件源的冲突应停止并说明，不擅自删除。任何部分完成都按结果报告。
6. 仅在退出码为 0 且 JSON 的 `installed`、`runtime_configured`、`host_bound` 均为 true 时，使用返回的实际 `installed_path` 读取 `skills/setup/SKILL.md`，核查初始化结果，再按 `skills/blackbox-review/SKILL.md` 打开 Blackbox。保留并说明 `warnings`；`host_mcp: not_verified` / `sidebar: not_verified` 不能当作原生入口可用。不可用源码路径冒充已安装目录。
7. 检查桌面宿主是否成功加载正式 `blackbox_reader`。如果当前会话尚未加载新的插件工具，先尝试宿主支持的插件刷新；需要完整重开时，请用户保存工作后完整退出重开 Codex，并给出一句续验指令：**继续完成 Blackbox 的首次设置并验证侧边栏入口，暂不启用任何项目采集。** 不自动终止宿主，不把独立进程的 MCP 握手或本地 HTTP 页面成功当作桌面入口成功。

遇到访问拒绝时，报告实际失败阶段、异常类型和可取得的 `errno` / `winerror`，不输出私人路径、SQL 或记录正文。可通过宿主正常的单次权限审批机制请求本次操作必需的访问；这不是更改持久权限的授权。不要关闭沙箱、修改 ACL、改选空状态目录或静默忽略失效绑定来完成安装。若无法取得必需访问，保留现状并报告阻断。

## 报告结果

分别报告安装文件/登记、运行绑定、桌面 MCP 加载、原生侧边栏打开、本地 HTTP 页面打开、Hook 信任与项目采集。未验证的项标记未验证；不要笼统报告“全部安装成功”。正式产品不依赖 Entry Probe 或 Error Probe；测试插件的入口和结果不能替代正式插件验收。

默认不启用项目，用户从页面点击“接入第一个项目”后，沿用 blackbox-review 的范围授权、官方 Hook 审阅和真实记录核验流程。禁止模拟 Hook、写入测试事件或扫描宿主正文来假装接入完成。真实安装验收还须覆盖桌面冷启动与新会话，不依赖安装会话临时 PATH 或每次手工指定另一状态目录。
