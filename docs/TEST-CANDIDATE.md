# 测试 0.4.0-alpha.6.46 发布草稿

本页用于维护者在测试电脑上验证 **0.4.0-alpha.6.46** 候选包。草稿准备完成后才可执行；测试通过也不会自动公开发布。当前 [README](../README.md) 的安装提示词仍指向已发布 `.45`，**不要用它安装这次草稿**，也不要运行会下载公开 Release 的 `bootstrap_blackbox.py`。

## 1. 下载草稿附件

在测试电脑的浏览器中登录有权查看本仓库发布草稿的 GitHub 维护者账号，打开 [Releases](https://github.com/zemekeznb/blackbox-plugin/releases)，进入标记为 **Draft** 的 `v0.4.0-alpha.6.46`。从同一草稿下载以下三个附件，保留原文件名：

- `blackbox-plugin-0.4.0-alpha.6.46-candidate.zip`
- `blackbox-plugin-0.4.0-alpha.6.46-candidate.zip.sha256`
- `blackbox-plugin-0.4.0-alpha.6.46-candidate.release-manifest.json`

草稿附件不能匿名下载；登录只用于取得附件，不需要把账号凭据交给 Codex。不要下载 GitHub 自动生成的 “Source code” 压缩包代替候选 ZIP。记录草稿展示的来源提交，供后续核对清单；看不到草稿时先检查登录账号和草稿是否已准备完成。

本次只升级正式 **Blackbox Plugin**。无需卸载全部插件、清空数据库或重置 Codex。Entry Probe、Error Probe 不属于正式插件依赖；若测试电脑已有它们，保持原状，验收时不要使用其入口或调用错误探针。

## 2. 交给测试电脑的 Codex 安装

在测试电脑的 Codex 桌面会话中发送以下提示词。Codex 应先核验和备份，再执行升级；如果发现多个同名下载文件，先按内容与摘要辨认，不能任选一份。

```text
请帮我安装并测试 Blackbox 0.4.0-alpha.6.46 发布草稿。这次授权只升级正式 blackbox@personal，备份已有 Blackbox 数据及相关配置，初始化运行绑定并验证打开。不公开发布、不清理历史、不卸载其他插件、不启用项目或写入 Hook 信任，不修改模型、账号、PATH、ACL、沙箱和持久执行策略。

1. 从 Windows 实际下载目录定位以下三个确切附件：
blackbox-plugin-0.4.0-alpha.6.46-candidate.zip
blackbox-plugin-0.4.0-alpha.6.46-candidate.zip.sha256
blackbox-plugin-0.4.0-alpha.6.46-candidate.release-manifest.json
确认它们来自同一个草稿。先验证 SHA256 文件中的 ZIP 文件名与实际 SHA256 一致；外部 manifest 必须与 ZIP 内 release-manifest.json 逐字节一致，version 必须是 0.4.0-alpha.6.46，source 必须为干净提交，并与草稿来源提交相符。摘要只证明一致性，不冒充独立发布者签名。缺附件、版本或来源不符就停止，不下载 .45 代替。

2. 使用宿主提供的定位能力找到现有 Python 3.11+，包括 Codex 已提供的 bundled Python，验证该绝对路径可执行，不改 PATH、不安装新 Python。外层校验通过后，仅将 ZIP 内 tools/verify_alpha_candidate.py 作为文本读取、审阅并保存到临时目录；审阅确认后用该 Python 执行它的 --verify-only 检查。必须验证内部文件清单、逐文件大小与 SHA256、SHA256SUMS，并拒绝危险 ZIP 路径、重名或链接。校验失败不要执行其他包内代码。

3. 将已验证的载荷完整解压到当前用户的稳定独立目录，例如实际本地应用数据目录下 Blackbox-install/0.4.0-alpha.6.46；先确认它不在项目、Git 仓库、插件缓存或临时目录内。可使用已审阅校验器的 verify_archive 和 extract_verified 写入已验证字节。目标须为新目录；若已有目录，逐文件核对相同包后才能复用，不覆盖未知内容，也不删除旧 .45 安装源。此目录将作为插件源保留，不能安装后清掉。

4. 只读确认当前桌面宿主的 codex.exe、CODEX_HOME、正式 Blackbox 的实际安装缓存和版本。定位原 .45 的实际物理状态目录及阅读服务，结合原安装会话的 --state-dir、runtime.cmd、服务注册和状态输出核对；不要只凭逻辑 LOCALAPPDATA 猜测，也不要创建新库来试路径。用原安装缓存的 blackbox.py、已验证的 Python 绝对路径和明确的原状态目录检查。若无法确认原目录或访问被拒绝，保留现状并报告；必要时通过宿主正常单次权限机制申请本次访问，不关闭沙箱或调整 ACL。

5. 在升级前保存原数据。若原数据库存在，使用原版本支持的命令：
& $bbPython -B $bbOldEntry --state-dir $bbState backup $bbBackupFile
其中变量必须替换为已核实的实际路径；目标是项目和所有 Git 仓库之外、已存在备份目录中的新文件。要求退出码 0、verified=true，保留返回的摘要和计数。不要直接复制活动 SQLite 文件代替在线备份。另将已有的 Blackbox runtime.cmd、CODEX_HOME/blackbox/runtime.json 和 reader-selection.json 备份到同一私有备份目录（不存在则记录不存在），不得上传其内容。若没有原数据库，先明确核实并报告，不为了备份创建数据库；存在但不能读取时停止升级。
随后用同一原版本脚本及同一状态目录执行 stop，确认 stopped=true 或 reason=not_running；停止阅读服务不会停用项目采集。保留备份和原目录，不自动恢复或删除历史。其他停止失败需先解决，不能按 PID 强杀。

6. 审阅候选根目录的 install.ps1 及它调用的安装脚本，执行完整安装，不传 -RuntimeOnly。使用实际 Python 和目标桌面 codex.exe 的绝对路径，-StateDir 固定为上一步确认的物理状态目录；不得临时换成新空目录。先用相同参数加 -CheckOnly 预检，通过后去掉 -CheckOnly 安装：
& $bbInstaller -Python $bbPython -Codex $bbCodex -StateDir $bbState -CheckOnly
& $bbInstaller -Python $bbPython -Codex $bbCodex -StateDir $bbState
变量必须指向核验后的候选根目录 install.ps1、解释器、目标宿主及原状态目录。若 personal 指向旧 .45 源，只有确认旧源清单仅含正式 Blackbox（或其兼容旧名）、不含其他插件时，才可给两条命令添加 -ReplaceSource 切换到新稳定目录；已授权这项限定的来源切换。无法确认或含其他插件时停止，不删除整个源。若执行策略阻止脚本，报告具体阻断，不自行修改执行策略或绕过审阅。

7. 检查完整安装退出码、实际缓存版本 .46、载荷回读结果、runtime_configured 和 host_bound。核对当前 CODEX_HOME 中绑定的 Python 与原物理状态目录，并确认项目开关及历史未被重置。保留 warnings，Hook 未信任须如实报告，不在本次安装中授予信任。MCP 或侧边栏尚未验证时明确写未验证，不能只凭 installed/enabled 或 HTTP 页面打开宣布全部成功。

8. 检查实际桌面后台的 blackbox_reader 加载情况，尝试正式插件的原生入口。独立 MCP 子进程和内置浏览器中的本地 HTTP 页面分别报告，不用它们替代侧边栏验收。若需完整重开，让我保存工作并完整退出 Codex，再按后续提示继续；不要自动结束桌面进程。失败时返回命令、退出码、安全错误码、阶段及可取得的 errno/winerror，隐去个人路径、令牌和记录正文。
```

如果这台电脑此前根本没有安装或初始化过 Blackbox，可由 Codex 核实后在默认位置首次初始化；这不适用于已经有 `.45` 数据却无法访问的情况。已有库必须沿用，不能把新空页面当作升级成功。

## 3. 完整重开后，在新会话验收

保存工作，完整退出 Codex 后重新打开；关闭单个窗口未必退出后台。在**新的桌面会话**中发送：

```text
继续验证刚安装的 Blackbox 0.4.0-alpha.6.46 草稿。请先核对实际桌面后台已重启，以及正式 blackbox@personal 的安装缓存和当前 CODEX_HOME；不要重装、修改 PATH、重新配置运行时、指定另一数据目录或清理数据。

检查真实桌面后台的 blackbox_reader 是否加载成功，尝试从正式 Blackbox 侧边栏或会话原生入口打开页面。然后使用已绑定的 Python 和已安装缓存中的 blackbox.py 执行 status，以及在需要验证 HTTP 入口时执行 open --no-browser；不加 --state-dir，确认后续会话仍解析到升级前的同一物理状态目录，原项目设置及记录保留。没有原记录时如实报告，不能声称已验证记录迁移。

分别报告：后台重启证据、安装版本、运行绑定、实际桌面 MCP 加载、原生入口打开、新会话命令结果、HTTP 页面结果、数据与项目设置是否保持。未检查或无法取得的项写未验证。不要调用两个 Probe，不启用项目、不授予 Hook 信任、不生成错误或模拟 Hook；本轮尚不验收真实采集。

如果失败，保留具体命令、退出码、安全阶段/错误码和可取得的 errno/winerror。访问受限时按宿主单次权限机制处理并记录执行条件，不通过关闭沙箱、改 ACL、改目录或重复重启掩盖问题。分享结果前隐去个人路径和访问令牌，不附数据库、配置文件正文或对话内容。
```

两次会话的结果都保留。侧边栏和新会话打开通过后，再单独选择目标项目、确认保存范围及官方 Hook 信任，用一条无副作用的真实命令核验记录送达。**没有实际回执，不宣称采集通过；没有真实桌面证据，不宣称原生入口修复完成。**

备份与隐私说明见[使用与隐私](../release/ALPHA-GUIDE.md)，版本发布边界见[开发与验证](DEVELOPMENT.md)。测试后是否公开发布，仍由维护者明确确认。
