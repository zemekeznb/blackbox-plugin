# Blackbox Plugin 系统架构

本文描述 NOTES-029 后的 0.3.5 源码实现；最新项目/会话导航、名称与本地更新见 [NOTES-029](reports/NOTES-029-session-navigation.md)，卡片导航见 [NOTES-027](reports/NOTES-027-reading-experience.md)，采集规则仍沿用 NOTES-026。当前桌面路径此前限定验收见 [NOTES-022 桌面报告](reports/NOTES-022-desktop-acceptance.md)。独立 CLI 受阻结果保留在[原安装报告](reports/NOTES-022-installation-acceptance.md)，本轮未调查。历史设计不作为现行接口规范。

NOTES-029 将默认阅读改为项目展开会话、选中会话后显示异常卡片。鉴权只读 `/api/anomaly-contexts?project_path=` 仅返回有事件的项目与选中项目全部有事件会话的标识、首末记录时间，不加载事件正文。精确 `project_path` / `investigation` 筛选及旧 project 子串接口兼容；限定上下文后继续有界扫描，并固定全局事件截止。导航元数据不冻结，列表/详情/导出快照不因此变化。真实名称用于网页导航和详情；导出仍使用已保存标识，不受宿主改名影响。时间预设按浏览器本地日历转换 UTC 起止。来源默认折叠为技术详情，不改变较广的原始事件保存范围，无项目会话仍不自动采集。

`GET /api/session-names` 每批最多接受 20 个已保存且有事件的 investigation ID，服务再解析对应宿主 session ID；不能用此接口查询任意宿主会话。`tas_notes_codex.py` 通过本机 `codex app-server --stdio` 的 `thread/read`、`includeTurns:false` 读取 `thread.name`，不使用 preview 或回合正文。进程隐藏窗口、输出有界、查询限时 6 秒并清理自有子进程；最多一个查询子进程并发，服务内 512 项/60 秒缓存，失败不阻断记录阅读。名称不写入 SQLite 或导出。

`GET/POST /api/reader-selection` 使用相同鉴权，POST 只接受已保存且有事件的 investigation ID、请求体最多 1024 字节，原子写入独立的 `reader-selection.json`。读取时从数据库推导项目，缺失或过时偏好降级为最近会话。此文件不包含名称或正文，不属于 SQLite 备份/恢复内容。当前页面用最多 10 份列表视图缓存保留会话的筛选、DOM、分页快照与滚动，不写浏览器长期存储；重载只恢复所选会话。

## 数据流

```text
明确启用项目的 Codex 可见 Hook
             ↓
launch.cmd → Python 适配、脱敏、预算检查 → SQLite events
                                               ↓ 只读、固定快照
桌面入口 → 回环 HTTP 服务 → 有界错误格式扫描 → 列表 / 详情 / Markdown
                              └────────────→ 历史来源、总结、人工版本只读
```

八种 Hook 为 SessionStart、UserPromptSubmit、PostToolUse、Stop、PreCompact、PostCompact、SessionEnd、Interrupt。按明确授权的项目绝对路径接收，不扫描宿主会话文件、不控制 Agent loop、不读取隐藏推理。宿主未送达的信息无法恢复。

## 职责与保存模型

| 模块 | 职责 |
| --- | --- |
| `tas_notes_core.py` | CLI、项目授权、来源适配、事件事务、容量保护、状态与历史读取 |
| `tas_notes_anomalies.py` | 错误格式解析、有界快照查询、相同输入关联、Markdown 快照 |
| `tas_notes_web.py` | 鉴权 HTTP、静态页面、只读查询、阅读偏好与受控服务停止 |
| `tas_notes_codex.py` | 有界本机会话名称查询与缓存，不读取回合正文 |
| `tas_notes_launcher.py` / `open_notes.py` | 状态路径、构建身份、启动锁、健康检查、复用与自有子进程清理 |
| `tas_notes_backup.py` | 在线备份验证、恢复到新的停用目录 |
| `tas_notes_diagnostics.py` | 运行配置与已知采集缺口诊断，不承诺持续健康 |

SQLite `events` 保存经过字段选择、脱敏和可能截断的来源副本；不是完整原始宿主记录。适配器版本 2 保留事件/调用/回合标识、候选状态值、首尾片段、未知字段数量。状态值尚未解释为新的可靠检测契约。历史来源不重写，旧稳定 ID 重投仍兼容去重；同样文本的真实重复调用不合并。

保留 `user_version=1` 与历史 `revisions`、`summaries` 数据表。`capture_usage` 及事务内触发器维护会话累计大小和条数，避免每个 Hook 重扫全会话。旧的 `anomalies` / `anomaly_index_state` 派生表若已存在会留在库中，但当前查询不创建、更新或依赖它们。

整条 Hook 输入最多读取 512 KiB 加一字节，超过上限在 JSON 解码前拒收；不保存正文或从前缀猜身份。普通采集预算为每会话 10 MiB / 5,000 条，格式命中的错误可使用额外 512 KiB / 128 条余量；全库约 256 MiB 触发采集保护。达到预算不会自动删除历史或阻断已存内容的查询。

已知项目/会话拒收保留无正文错误码；身份未确定的拒收另存 `capture-unknown.json`。正常情况下，诊断在 SQLite writer lock 内发布，后续事件读取代次，在每个会话首条成功来源标记保守缺口；重复投递不消耗边界，连续新来源可恢复关联。歧义身份、诊断不可读和两份 marker 部分写入不一致均保守处理。新来源保存 `capture_boundary_version=1`，旧来源不改写。无法取得锁或持久化诊断时仍只能尽力保证，不能承诺持续健康。

## 查询、关联与兼容接口

NOTES-027 的异常页使用原生 JS 在卡片列表与独立详情之间切换，History API/地址片段承载记录和截止位置；令牌继续仅在片段和鉴权头中使用。当前页面内保留列表 DOM、筛选、分页、快照、滚动及焦点，返回时使旧详情请求失效；不向 localStorage/sessionStorage 写入记录。整页重载时重新取得可用列表，直接详情仍使用地址里的截止位置。新样式限定 `.anomaly-app`，两个旧历史导航已移除，兼容页面及 API 不变。采集与隐私说明折叠展示，事件保存范围未缩减。

默认 `/` 为异常列表，`/history` 为历史只读页。`/api/anomalies` 以 `through` 固定事件上界、`before` 向旧记录翻页，每次按序号倒序扫描最多 500 个工具事件、返回最多 100 条命中。返回快照、下一游标和是否有新事件；零命中页仍可能存在下一游标。刷新建立新快照，分页期间新事件不插入当前页序列。

读取事务先物化有界来源，再释放锁进行解析；不需要先写索引，满容量仍可读。检测目前仅针对 `Bash.tool_response`：完整 Python traceback 语法或指定 PowerShell 程序启动错误格式。首尾片段各自解析，不跨缺口拼接。当前桌面真实样本验证了 TypeError、ModuleNotFoundError、重复与回合边界；静默非零退出仍不命中，合并输出破坏 traceback 格式时也可能漏记。此证据不扩大到全部工具或独立 CLI 路径。

详情要求同会话、相同工具、未脱敏或截断的完整输入一致。新用户输入、助手结束、回合变化或身份缺失、中断/结束、已知采集缺口终止关联；下一次匹配错误也终止，并独立保留该错误。最多 20 次再次调用，输出明确不证明处理意图或修复。`observed-errors-3` 对缺少完整采集边界契约的旧来源停止自动附加后续调用，来源仍可读导出；读取不依赖实时变化的诊断文件，固定快照保持稳定。

`/api/anomaly/<seq>?through=` 返回固定来源截止详情，`/api/anomaly-export/<seq>?through=&rule=` 导出同一规则和快照。规则不一致拒绝并要求重开记录。来源序号与摘要可核对保存副本，不能证明现实事实。

历史来源、Agent 总结和人工版本支持只读；人工版本可选择并逐版本导出。人工版本写入 API 返回历史只读状态，CLI 总结写入和旧编辑界面移除。常规产品流程不自动删除历史。NOTES-025 按单独授权一次性清理了本机旧 Anything 试验数据；备份、当前试用和迁移归档保留。

## 运行、安全与恢复

只监听 `127.0.0.1`，随机 token、Host/Origin 校验、CSP 和文本渲染限制来源执行。`POST /api/shutdown` 要求鉴权和空请求体；启动器验证 URL、服务、PID、build 和可用的状态目录身份后请求停止，不按不可信 registry PID 杀进程。

启动锁串行化复用与发布；健康检查短暂重试，registry 原子替换。同目录已有其他 build 时明确提示冲突，不悄悄多开。新启动失败时优先鉴权停止；仅对持有 `Popen` 句柄的本次自有子进程保留终止兜底。旧版不支持停止协议时不自动强杀。

路径优先级为显式参数、`TAS_NOTES_STATE_DIR`、`LOCALAPPDATA/TAS Notes`。Windows 安装入口保存数据目录的物理路径，不从应用副本推导数据位置。已有非符号链接数据库优先按文件句柄解析物理父目录，避免 MSIX 文件重定向与目录句柄结果不同；空目录仍按目录句柄解析。当前服务公开版本、构建和数据位置；源码与已安装副本必须分开验收。产品更名不改变兼容 ID，见 ADR-N005。

`backup DEST` 使用 SQLite 在线备份，校验库完整性、引用和计数，禁止覆盖现有文件。`restore SOURCE NEWDIR` 校验后恢复到新目录，所有项目停用，不替换当前库，不自动配置运行时或启动采集。因为 SQLite 快照不包括尚未消费的外部诊断，恢复必须建立并核验 `RestoreContinuityUnknown` 边界，失败则不返回恢复成功。该边界表示恢复后连续性未知，不声称发生过采集错误。备份与恢复目标不得进入任何 Git 仓库或已登记项目；文件可能含敏感信息，同盘备份不等于独立灾备。

没有中央服务、额外模型调用、自动上传或开机自启。长期存储缩减仍为 NOTES-017 待办，当前无自动历史清理。
