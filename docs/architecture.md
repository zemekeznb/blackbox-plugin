# Blackbox Plugin 系统架构

更新：2026-10-14，NOTES-140。本文以 **0.4.0-alpha.6.45** 源码为基线，描述当前实现；安装结果和真实宿主采集证据以[当前状态](current-status.md)为准。历史版本流水、早期接口及失败记录保留在[整理前原文](history/NOTES-137-architecture-before.md)及对应任务报告。

Blackbox 当前上线面向 **Windows 版 Codex 桌面端**，在用户明确启用的项目中保存获授权的可见事件，再按确定性规则呈现执行异常和保守关联的后续调用。NOTES-140 将会话准入收紧为持久桌面来源，拒收终端 CLI；CLI 产品适配与完整验收留待后续。没有中央服务、额外模型调用或自动上传；不控制 Agent loop，不读取隐藏推理，不推断根因、责任或修复成功。

## 接入与数据流

安装、初始化、项目授权、Hook 信任和真实记录送达是不同阶段。安装不会自动启用项目；官方 Hook 信任由 Codex 的审阅流程完成。首次安装或更新后可能需要用户完整重开 Codex，再以真实命令回执验证采集。

```text
Codex 桌面会话中的安装提示词
             ↓ 固定安装载荷与官方插件登记
Blackbox 插件 + Python 运行配置
             ↓ 用户明确启用目标项目，并完成官方 Hook 审阅
Codex 可见 Hook → launch.cmd → blackbox.py hook
             ↓ 项目授权 + 宿主会话身份准入
字段选择 / 有限脱敏 / 截断 / 去重 / 容量与缺口检查
             ↓
本机 SQLite 来源事件
             ↓ 固定来源截止 + 确定性识别与关联
Codex 内 MCP 页面 / 回环 HTTP 阅读器 → Markdown 快照
             └→ 用户明确操作的项目、会话与记录管理
```

Hook 配置包含 `SessionStart`、`UserPromptSubmit`、`PostToolUse`、`Stop`、`PreCompact`、`PostCompact`、`SessionEnd`、`Interrupt`。这表示配置的接收入口，不证明宿主在所有情况下都投递这些事件。宿主未送达的信息无法补回。

当前身份为插件 `blackbox@personal`、技能 `blackbox-setup` / `blackbox-review`、入口 `plugins/blackbox/scripts/blackbox.py`。旧名称只用于兼容输入和历史证据，迁移决定见 [ADR-N006](decisions/ADR-N006-blackbox-identity.md)。安装操作见[安装指南](../release/INSTALL.md)，发行与测试见[开发说明](DEVELOPMENT.md)。

## 模块索引

以下路径均位于 `plugins/blackbox/`：

| 模块 | 现行职责 |
| --- | --- |
| [`scripts/blackbox_core.py`](../plugins/blackbox/scripts/blackbox_core.py) | CLI、项目启停、来源适配、SQLite 事务、去重、容量保护、状态与历史版本读取 |
| [`scripts/blackbox_admission.py`](../plugins/blackbox/scripts/blackbox_admission.py) | 生产 Hook 的宿主会话身份准入与有界元数据缓存 |
| [`scripts/blackbox_anomalies.py`](../plugins/blackbox/scripts/blackbox_anomalies.py) | 确定性异常识别、筛选、快照、重复调用关联与 Markdown 渲染；调用 shell、curl、HTTP、业务协议等专项解析模块 |
| [`scripts/blackbox_codex.py`](../plugins/blackbox/scripts/blackbox_codex.py) | 本机 Codex 官方接口的会话名称、身份及项目名称/根路径查询；不读取回合正文 |
| [`scripts/blackbox_mcp.py`](../plugins/blackbox/scripts/blackbox_mcp.py) | stdio MCP 页面、受限读取接口、明确范围的管理操作与快照导出 |
| [`scripts/blackbox_web.py`](../plugins/blackbox/scripts/blackbox_web.py) | 回环 HTTP 页面、鉴权 API、阅读偏好、管理操作与服务停止 |
| [`scripts/blackbox_launcher.py`](../plugins/blackbox/scripts/blackbox_launcher.py) / [`scripts/open_notes.py`](../plugins/blackbox/scripts/open_notes.py) | 物理状态目录、版本与构建身份、服务启动锁、健康检查、复用和停止 |
| [`scripts/blackbox_sessions.py`](../plugins/blackbox/scripts/blackbox_sessions.py) / [`scripts/blackbox_cleanup.py`](../plugins/blackbox/scripts/blackbox_cleanup.py) | 本地会话归档/恢复、确认后的删除、重投保护、删除边界与空间回收回执 |
| [`scripts/blackbox_backup.py`](../plugins/blackbox/scripts/blackbox_backup.py) | SQLite 在线备份验证、恢复到新的停用目录 |
| [`scripts/blackbox_install.py`](../plugins/blackbox/scripts/blackbox_install.py) / [`scripts/blackbox_preflight.py`](../plugins/blackbox/scripts/blackbox_preflight.py) | 官方 CLI 安装、载荷与环境检查；与项目采集授权分开 |
| [`scripts/blackbox_compat.py`](../plugins/blackbox/scripts/blackbox_compat.py) | 旧环境变量、默认目录与插件身份迁移兼容 |
| [`scripts/blackbox_hooks.py`](../plugins/blackbox/scripts/blackbox_hooks.py) / [`scripts/blackbox_diagnostics.py`](../plugins/blackbox/scripts/blackbox_diagnostics.py) | 只读 Hook 信任检查、运行配置与采集缺口诊断；不代替真实送达证据 |
| [`assets/`](../plugins/blackbox/assets/) | 两种阅读入口共用的页面、样式、交互脚本及 MCP 桥接 |

## 存储与授权

**保存范围大于异常展示范围。** 已启用项目中，经宿主确认的持久桌面会话可以保存普通输入、工具调用及返回、最后回复等可见事件；归档或未命名本身不构成拒收理由。终端 CLI、内部、临时及身份未确认的事件不保存正文，也不排队等待身份确认后补录。

生产 `hook` 命令强制执行会话准入：先查项目授权，再通过 `thread/read(includeTurns:false)` 检查 `source` / `ephemeral`。持久 `vscode` / `appServer` 接收，明确 `cli` / `exec` / 子代理及临时会话拒收；其他未确认身份不保存正文。

NOTES-140 使用 `desktop-v1` 策略参与缓存键计算，旧 allowed 不继承为新版许可，必须重新查询；只迁移旧 skipped 缺口标记，并移除该身份对应的旧键。全表仍最多 1,024 项。新策略下已验证桌面身份的传输暂时失败保护保留。首次重新验证若宿主不可用，该次事件不会保存或补回；后续确认只保存新事件并标记缺口。NOTES-099 的旧边界及固定历史清理记录保留；新版准入不自动删除任何历史，授权见 [NOTES-140](tasks/NOTES-140.md)。

`notes.sqlite3` 中，`projects` 保存项目开关，`investigations` 标识项目/会话，`events` 保存字段选择、有限脱敏和可能截断后的来源副本及摘要。它不是完整宿主日志；有限脱敏不能保证移除所有敏感内容。宿主稳定事件 ID 用于重投去重，同样文本的不同真实调用不因此合并。新事件不覆盖旧来源、总结或人工版本。

数据库保持 `user_version=1`，历史 `revisions`、`summaries` 仍可读取；不再新增总结或人工编辑版本。`capture_usage` 由事务内触发器维护容量计数；会话准入、阅读归档、删除回执和重投保护使用各自的辅助表。异常查询直接读取来源，不依赖历史派生异常索引表。

输入大小、字段、会话及数据库容量均有保护；达到上限会拒收相应新事件并尽力留下不含正文的诊断，不自行淘汰旧记录。具体容量以源码常量及状态接口为准。采集缺口写入后续来源的连续性标记，关联不会跨越已知缺口；数据库锁或诊断持久化失败时仍只能尽力处理，不能承诺持续健康。

状态目录优先级为显式 `--state-dir`、`BLACKBOX_STATE_DIR`、兼容变量 `TAS_NOTES_STATE_DIR`、默认 `%LOCALAPPDATA%\Blackbox`。仅有旧默认目录时继续兼容；显式初始化可按 ADR-N006 迁移并保留旧路径别名，不合并两个库、不搬迁自定义目录。Windows 打包应用可能重定向路径，运行时以实际文件/目录句柄解析结果为准。数据目录不得位于已启用的项目内。

## 阅读、快照与管理

MCP 与 HTTP 阅读器共用 Store、识别规则和页面交互。项目/会话名称来自有界的官方只读元数据查询，不扫描宿主会话文件或回合正文；名称不写入来源或导出。阅读选择保存在独立 `reader-selection.json`，本地归档状态保存在数据库，不改变 Codex 会话。

异常列表以 `through` 固定来源截止、`before` 翻页，按项目、会话、时间、工具和类型筛选。内部逐批释放读锁再解析，显示总数可继续扫描同一截止内的候选事件；不能把单批上限当作整个请求的扫描上限。详情和导出复用同一来源截止及规则版本，规则不一致要求重新打开记录。

当前规则为 `observed-errors-17`，覆盖受限格式的 Python、PowerShell、Git、Node.js、curl/HTTP、JSON-RPC、GraphQL 等返回，以及**已送达并保存的完整 MCP 结果对象**中明确的 `isError=true`。这不是任意文本或全部工具错误识别；详细格式和排除条件以解析模块为准。截断首尾片段分别解析，不跨缺口拼接。

后续调用关联只接受同一会话、相同工具和完整一致的输入，并在新用户输入、回复结束、中断、会话结束、回合变化、采集/删除缺口等边界停止。历史来源缺少连续性契约时不自动扩展关联。格式命中与相同输入再次调用均不证明故障原因、处理意图或修复成功。

MCP 工具声明为页面专用，记录数据放在 `_meta.blackbox`，不放入模型可见的结果字段。读取使用只读 Store；项目启停、会话管理、清理和导出使用各自受限接口，因此 MCP 入口并非整体只读。快照保存到本机下载目录前核对预览摘要，不覆盖已有文件；导出不会自动上传。

清理支持异常选择、会话、项目和日期范围。先生成含数量、范围及版本影响的预览，再以确认令牌重新核对后删除；范围变化时要求重新预览。整会话/项目清理可能包含历史总结和人工版本，预览会列出影响；局部来源清理保留的历史版本会提示来源缺失。删除保留不含原正文的重投保护和关联边界，空间回收失败可按回执重试。归档仅影响本地阅读，不等于删除或停用项目采集；清理不操作宿主会话、既有备份或已导出文件。

固定截止隔离后来新增的事件，不承诺在用户明确删除来源后仍能重建旧快照。需长期保留的版本应自行导出或备份。

## 本地运行与恢复

HTTP 只监听 `127.0.0.1`，使用随机令牌、Host/Origin 校验、CSP 和文本渲染。启动器按状态目录、构建和服务身份核验实例；启动锁串行化复用与发布，不根据不可信 registry PID 强杀进程。同目录存在不同构建时报告冲突，由受控流程停止旧服务。关闭页面或停止阅读服务不等于关闭 Hook 采集。

`backup` 使用 SQLite 在线快照并校验完整性、引用与计数，不覆盖已有文件；`restore` 仅恢复到新目录，所有项目默认停用，不自动接管当前运行时。恢复会建立并核验 `RestoreContinuityUnknown` 标记，避免跨恢复边界关联。备份不包含独立阅读偏好或全部运行时文件；备份和恢复目标须位于 Git 仓库及已登记项目之外。

## 已知限制与证据边界

- 实际可记录范围取决于宿主投递。静默失败、混合输出、内部重试及所测非 Bash/MCP 失败有漏记边界；打印的完整诊断也可能误报。“暂无异常”不代表没有失败。
- 安装校验、协议夹具、模拟 Hook 和既往版本的验收，不能代替当前版本在新用户桌面会话中的真实采集回执。更新中的长回合不保证热加载无损。
- 当前上线范围为 Windows 版 Codex 桌面端，CLI 支持后续另行验收。最低宿主版本、干净 Windows 环境、API Key 登录的完整链路及其他暂停事项见[当前状态](current-status.md)和[开发计划](development-plan.md)。
