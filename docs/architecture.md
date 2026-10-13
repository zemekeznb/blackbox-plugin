# Blackbox Plugin 系统架构

NOTES-099 / 0.4.0-alpha.6.24：生产 Hook 增加宿主 source/ephemeral 身份准入及有界元数据缓存；仅接收已启用项目中持久交互用户会话（含归档），未知正文不落盘。用户明确授权固定隐藏历史清理；不自动过期。详见 [报告](reports/NOTES-099-session-admission.md)。

当前进度以 NOTES-066 证据自审为准：HTTP 前置诊断已按用户要求暂停，属于未证实的排列假设，不再列为必做缺口。真实故障、协议夹具、外部依据与人工反例分别统计；产品仍为 0.3.38，无本轮安装。见 [审计](reports/NOTES-066-evidence-audit.md)。以下版本摘要为历史记录，不自动生成待办。

NOTES-065 入口核查完成：三个真实成功/失败/正文伪装对照的未知字段均为 model:str；原始 Hook 没有退出状态或独立 stdout/stderr，成功与失败正文相同。2 项诊断测试通过，原 runtime 已恢复、探针关闭，恢复后 #2794 投递通过。无产品改动、安装或重启，版本仍为 0.3.38；混合输出和前置格式证据缺口保留。见 [报告](reports/NOTES-065-hook-schema.md)。

NOTES-064 / 0.3.38：普通 cat/gc/type 的有限字面量读取按歧义排除日志诊断，6 条真实日志误报消除，绝对/简单相对缺失路径和显式重定义的错误保留。260 项 Python、74 项前端通过；14 个真实来源（6 错误/7 排除/1 漏记边界）及固定 #2736 安装版列表/详情/6 份导出一致，2358 条旧事件及授权不变。一次安装后的长回合探针截至 #2741 未送达，历史缺口保留；新用户回合探针 #2743 与 14 个新来源续验通过，固定 #2761 安装版列表/详情/导出一致，保护核对截至 #2762。本轮未重装或重启。所查保存样本无可靠状态/分通道字段；无结构混合输出仍漏记，CMD 实验为后置诊断，前置格式证据仍缺。服务已停止。见 [报告](reports/NOTES-064-ambiguous-reads.md)。

以下 NOTES-063 及更早版本说明为历史记录。

NOTES-063 / 0.3.37：支持同一命令显式绑定的读取别名与固定 curl 原生 JSON 尾部；限定路径的日志误报和部分响应漏记已改善，普通别名和无结构化信息的旧混合输出仍保留边界。255 项 Python、74 项前端、13 项真实来源及固定 #2642 安装版列表/详情/7 份导出通过；2276 条旧来源及授权未变。一次安装后的长回合探针截至 #2647 无来源，历史缺口保留；新用户回合探针 #2653 及 13 个新来源续验通过，固定 #2673 的安装版列表/详情与导出一致，保护核对截至 #2674。服务已停止，本轮未重装。前置顺序仍未得到真实格式证据。见 [报告](reports/NOTES-063-explicit-results.md)。

NOTES-062：0.3.36 收紧明确绝对路径 Get-Content 的外来 PowerShell 日志误报，补限定 pwsh 子进程单行读取错误和 GraphQL SSE 单操作完整序列识别。249 项 Python、74 项前端通过；23 个真实来源（7 明确错误/12 排除对照/3 误报风险/1 漏记边界）及固定 #2505 安装版列表/详情/10 份导出一致，2119 条旧来源及授权不变。一次安装后，新回合探针 #2508、另 23 个完整来源及固定 #2535 安装版读取续验通过；2119 条旧来源及授权不变，无额外重启或重装，服务已停止。首次截至 #2506 的热更新缺口保留。前置顺序仍字符交错，别名、自指日志、部分正文和自定义契约边界保留。见 [报告](reports/NOTES-062-batch-coverage.md)。

NOTES-061 / 0.3.35：observed-errors-14 在明确字面量 Get-Content 返回中跳过 Python traceback，不跳过 PowerShell 读取诊断；补请求绝对路径一致的完整英文单行 missing 格式。别名/包装不扩豁免，不读当前文件来解释历史。14 项真实来源及安装读取通过；新回合另 14 项完整采集及固定 #2432 安装版读取通过，首次热更新缺口保留，见 [报告](reports/NOTES-061-content-read-errors.md)。

NOTES-060 / 0.3.34：observed-errors-13 在 detect 中隔离明确直接 curl.exe 的返回，不将未命中的 HTTP 正文回退解释为 Python/PowerShell 诊断，保留宿主进程启动失败。采集/存储不变；真实误报已复现并修复，安装版读取和重复关联通过；新版新回合 17 个新来源及固定 #2364 读取/关联续验通过，首次热更新缺口保留。完整打印诊断仍为边界，见 [报告](reports/NOTES-060-quality-errors.md)。

NOTES-059：沿用 0.3.33，显式 HTTP CONNECT 后超时、重置、空回复及 103 后超时的真实采集和安装读取通过；诊断前置三次实际尝试均字符交错，仍仅合成规则测试。无产品/采集架构变化，见 [报告](reports/NOTES-059-proxy-and-order.md)。

NOTES-058 / 0.3.33：observed-errors-12 在原 http_error 路径新增 mixed_error，只接受响应前完整诊断或合法中间/跟随重定向/显式 CONNECT 头链后的完整诊断。最终正文不扫描、不按声明长度切割。16 个真实来源和安装版读取通过，2 个部分正文错误仍明确漏记；前置诊断和代理中断仅规则测试。采集/存储不变；新版新回合另 16 个来源及固定 #2262 安装版读取通过，首次热更新缺口保留，见 [报告](reports/NOTES-058-mixed-http-errors.md)。

NOTES-057 / 0.3.32：新增 tas_notes_transport_errors.py，observed-errors-11 在受限 curl 命令上下文识别指定完整独立 (18)/(35)/(47)/(52)/(56)/(60) 诊断，不搜索正文。11 个真实 Bash 来源和安装版读取通过，无采集/存储变更；0.3.32 新回合另 11 个新来源及固定 #2183 安装版列表/详情/导出通过，首次热更新缺口保留，见 [报告](reports/NOTES-057-transport-errors.md)。

NOTES-056 / 0.3.31：列表新增按同筛选/快照计算的 total_count，批量只读扫描所有候选事件；卡片 PAGE_SIZE=100 不变。前端不再用已加载数量充当总数，reader-v18 兼容旧 URI。无采集或规则变更，见 [报告](reports/NOTES-056-anomaly-count.md)。

NOTES-055 / 0.3.30：observed-errors-10 扩展受限 curl 命令上下文与连续 HTTP 头部链，支持 fail 选项、重定向、1xx/显式 CONNECT，保留原文且每来源最多一条。--stderr - 单流支持避免本轮实测输出交错，但不修补历史；25 个实际来源及安装版读取通过；0.3.30 新回合另 25 项真实采集与读取续验通过，未要求额外重启或重装。无采集或存储范围变化，见 [报告](reports/NOTES-055-http-chains.md)。

NOTES-054 / 0.3.29：新增 `tas_notes_http_errors.py`，observed-errors-9 在明确业务协议规则之后兜底识别指定 curl 响应的 4xx/5xx 状态，保留原文，不解释业务责任；不新增采集入口或存储字段。17 个真实来源及安装版读取通过；0.3.29 新回合另 17 个来源与安装版读取续验通过，首次热更新缺口保留，见 [报告](reports/NOTES-054-http-status.md)。

NOTES-053 / 0.3.28：新增 `tas_notes_curl_errors.py`，observed-errors-8 在 Bash 分支按直接调用身份、受限输出参数与完整独立诊断识别 curl (6)/(7)/(28)。不从码值推断超时阶段，不扫描任意 HTTP 正文；旧来源只读重新识别，无存储或采集范围变化。4 个真实错误、4 个对照及安装版列表/详情/导出通过；0.3.28 新回合的 8 个来源和安装版读取续验通过，首次热更新未送达证据保留，见 [NOTES-053](reports/NOTES-053-curl-errors.md)。

NOTES-052 / 0.3.27：规则 observed-errors-7 扩充完整 HTTP 2xx/4xx/5xx JSON-RPC 单项及批量错误；批量按唯一类型 ID 完整匹配，允许乱序、通知无响应和成功兄弟项。GraphQL 普通 JSON 限 HTTP 2xx 且请求明确 Accept 协议，非 2xx 需专用响应媒体类型。仍只检查保存的 Bash/curl 来源，无新增采集入口、字段或数据库迁移。更新前和新回合各 24 个真实来源及安装版列表/详情/导出通过；首次热更新未送达证据保留。受控 SSE 正文已保存但未实现流式错误规则，curl 连接失败文本仍是已观察漏记，见 [NOTES-052](reports/NOTES-052-business-protocols.md)。

NOTES-051 / 0.3.26：新增 `tas_notes_business_errors.py`；observed-errors-6 在 Bash 分支只读识别指定直接 curl.exe JSON POST、完整 HTTP 2xx 响应中的 JSON-RPC/GraphQL 错误。保留 HTTP 原文，GraphQL 部分数据单独展示，任意 success:false 不解释。复用字面量 tokenizer，既有诊断规则保持。升级前和升级后新回合分别完成 20 个真实来源、6 错误/14 对照及安装版列表/详情/导出核对；新版新事件投递已通过，首次长回合热更新缺口保留。无新采集入口/字段/数据库迁移，见 [NOTES-051](reports/NOTES-051-business-errors.md)。

NOTES-050 / 0.3.25：新增 `tas_notes_shell_errors.py`，规则 observed-errors-5 在 Bash 分支支持指定 Git、Node.js、PowerShell 诊断；Git/Node 需要直接字面量调用，头尾片段不拼接，保留原始正文。采集来源和范围未改。实现、回归、安装及 12 个错误/6 个对照的真实桌面数据链路通过；首次热更新未送达记录保留，见 [NOTES-050](reports/NOTES-050-shell-formats.md)。

NOTES-049：沿用 0.3.24 的 Hook 数据流与检测规则，补齐文件、JSON、子进程/网络超时、文件共享拒绝、PowerShell 启动、HTTP、DNS 等 Bash 桌面测试矩阵。18 个错误真实采集及安装版阅读器导出通过，没有新接入或产品实现修改，见 [验收报告](reports/NOTES-049-bash-coverage.md)。

NOTES-047/048：当前版本 0.3.24。限时入口诊断证实测试 MCP 失败未进入插件；官方替代接口调查仅通过只读元数据，明细 API 实测未实现，未接入新来源。产品仍使用原 Hook 数据流，诊断默认关闭。替代来源的身份、去重、隐私边界见 [NOTES-048 设计记录](design/NOTES-048-result-source.md)。下段 NOTES-046 的“待刷新”已被真实续验取代，非 Bash 错误采集仍未通过。

NOTES-046 / 0.3.23 更新：检测规则 observed-errors-4 在保留 Bash 格式检测的基础上，条件支持已保存完整 MCP 结果的 `tool_response.isError is True`；不解析字符串 JSON、嵌套/顶层同名字段，不把正常返回中的 traceback 当作 MCP 失败。非 Bash 输入保留全部参数，无参数 MCP 的空对象可参与既有保守关联。采集事件种类、字段和授权未改。当前受控应用 MCP 失败未落盘，独立 MCP 探针待宿主刷新后续验，不能把合成规则测试当作送达通过。详见 [NOTES-046](reports/NOTES-046-tool-error-coverage.md)。下文包含早期架构描述，现行进度以 current-status 为准。

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
| `tas_notes_business_errors.py` | 指定 curl 请求/HTTP 返回的 JSON-RPC、GraphQL 明确错误协议，不执行请求或推断任意业务语义 |
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


## NOTES-024 MCP 阅读入口（0.3.6）

`tas_notes_mcp.py` 通过 stdio 注册 global/thread 阅读入口；资源将既有 CSS、页面、脚本与 mcp-reader.js 内联，不使用外部网络。页面复用既有接口形状，通过 app-only read_view 和 select_session 工具调用相同 Store/异常规则/名称/阅读偏好；数据库 read_only，缺失库不创建。记录结果仅置于 `_meta.blackbox`，不置于模型可见字段，不主动发送上下文或消息。入口工具只返回固定无数据文案。读取仅允许既有阅读端点，不能发起 shutdown、历史写入或任意 URL/文件读取。独立阅读偏好是唯一写入；HTTP 鉴权服务原样保留为桌面/开始菜单路径。正式侧栏显示已获用户确认；宿主下载和往返等续验见 NOTES-024 报告。
