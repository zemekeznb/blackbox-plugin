# Codex 执行的 Blackbox 安装说明

适用于用户在 Codex 中明确要求从 zemekeznb/blackbox-plugin 安装并初始化 Blackbox。固定版本 **0.4.0-alpha.6.44**。用户只需 Codex 桌面版、Python 3.11+ 与正常网络；不要求外部终端可运行 codex、Git、GitHub 登录或手工下载归档。

## 安装授权和范围

用户发送安装提示词授权安装本插件、初始化本地运行环境并打开阅读页面，不授权采集任何项目或授予 Hook 信任。已有历史、项目开关、其他插件及模型/provider/凭据配置保持原状态。不要更改持久 PATH、PowerShell 执行策略或另装 CLI。

## 执行

1. 确认当前是用户目标 Windows 桌面宿主。使用已有 Python 3.11+；缺依赖时准确报告，不下载另一份宿主或自行选择其他账号的 CODEX_HOME。
2. 将公开固定版本的引导工具下载到临时目录，不放在用户项目内：
   `https://raw.githubusercontent.com/zemekeznb/blackbox-plugin/v0.4.0-alpha.6.44/tools/bootstrap_blackbox.py`。
   先阅读该文件，确认 VERSION 与本说明一致、下载目标仅为本仓库、实际操作符合用户授权后执行。这个工具是安装过程的内部步骤，不要求客户去终端操作。
3. 用已有 Python 执行临时文件。工具自动定位当前运行桌面的 codex.exe；无运行进程时再检查已配置路径、PATH 与通用用户安装目录，发现歧义即停止，不按个人绝对路径或“最新目录”猜测。
4. 工具从本仓库 .44 Release 下载 ZIP 及摘要，校验外层 SHA256、载荷清单和路径后保存到当前用户 Blackbox-install/0.4.0-alpha.6.44。它不依赖 Git，客户无需手动下载或解压。校验用于完整性，不是独立发布者签名。冲突目录、异常网络或摘要失败不覆盖原件或执行载荷。
5. 工具复用已打包的预检、configure-runtime 和官方 Codex 插件登记/回读校验。来源迁移只允许替换已确认仅含 Blackbox 的旧源；其他插件源的冲突应停止并说明，不擅自删除。运行配置后登记失败可能部分完成，必须按返回结果报告，不将它描述成安装成功。
6. 仅在退出码为 0 且 JSON 的 installed、runtime_configured 为 true 时，使用返回的实际 installed_path 读取 `skills/setup/SKILL.md`，核查初始化结果，再按 `skills/blackbox-review/SKILL.md` 打开 Blackbox。不可用源码路径冒充已安装目录。
7. 如果当前会话尚未加载新的插件工具，先尝试宿主支持的插件刷新；需要完整重开时，请用户保存工作后完整退出重开 Codex，并给出一句续验指令：**继续完成 Blackbox 的首次设置并打开页面，暂不启用任何项目采集。** 不自动终止宿主，不把成功写入安装文件当成热加载成功。

## 报告结果

区分安装文件/登记已验证、运行初始化、界面已打开与真实采集。只有实际打开页面才报告界面已打开；否则给出具体阻断或必要的重开步骤。默认不启用项目，用户从页面点击“接入第一个项目”后，沿用 blackbox-review 的范围授权、官方 Hook 审阅和真实记录核验流程。禁止模拟 Hook、写入测试事件或扫描宿主正文来假装接入完成。
