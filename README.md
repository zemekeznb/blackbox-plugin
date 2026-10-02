# TAS Notes

独立轻量产品：Codex + Windows + 本地工作记录 + Owner 人工审阅。

私有源码仓库：[zemekeznb/tas-notes](https://github.com/zemekeznb/tas-notes)。

用户于 2026-09-24 明确选择先实现、再通过实际使用验证价值。竞品存在是方向信号，不等于已验证本产品需求。

## 与旧 TAS 的划分

| 路线 | 位置 | 状态 |
| --- | --- | --- |
| 完整 Team Agent System | 父目录 `tas/`、`tests/`、`clients/`、根目录历史文档 | 保留 F2 Completed / F3 Not Ready；本次不继续中央平台开发 |
| TAS Notes | 本目录全部内容 | 真实试用已发现日志式正文问题；NOTES-006 新增可读概览与来源约束整理 |

以上位置描述源项目中的划分。独立仓库只包含本目录的快照，不携带旧 TAS 代码或历史；旧项目仍保留。用户已于 2026-09-24 要求新建私有仓库，见 NOTES-003。

## 入口

- [设计与范围](docs/architecture.md)
- [路线决定](docs/decisions/ADR-N001-codex-windows-local-plugin.md)
- [首个工程任务](docs/tasks/NOTES-001.md)
- [真实接入验收](docs/tasks/NOTES-002.md) / [诊断与阅读改进](docs/tasks/NOTES-004.md) / [下一入口](docs/tasks/NOTES-005.md)

## 现在能做什么

- 按项目选择是否记录；已接收的事件持久保存在本机 SQLite。
- 查看详细过程草稿和来源，包括失败尝试、Agent 最后陈述与用户后来反馈。
- 在本地网页编辑和保存人工版本；新事件不会覆盖人工文字。
- 导出选定人工版本为 Markdown，由用户沿现有渠道分享。
- 通过插件 Hook 自动记录；八种事件已在本机真实 Codex 的对应运行路径落盘。
- 在 CLI 和本地页面查看 Python 配置、项目启停、最近落盘与历史错误；这些信息不会冒充持续健康状态。
- 记录以首条输入作为默认名称，人工标题优先；草稿提供中文事件标题和次数概览。

**这仍是开发原型，需要 Python、正常 Hook 信任和按项目启用。** 本机自动采集主链路已验证，不代表完整记录或所有桌面/退出路径兼容。工具退出状态当前未知，部分工具输出可能已发生编码丢失，草稿会提示。自动概览仅摘录来源；用户通过审阅技能整理指定记录时，当前 Codex 可生成独立的有引用 Agent 草稿，会使用当前会话上下文与用量。插件不在后台调用模型，网页不能自行唤醒 Agent。新事件使旧总结过期，人工版本仍保留。见 [接入证据](docs/integration-evidence.md)。

## 目录

```text
tas-notes/
  .agents/plugins/marketplace.json  独立分发入口
  plugins/tas-notes/                完整插件包
    .codex-plugin/plugin.json
    hooks/                         自动事件配置（实验）
    skills/                        设置与审阅操作入口
    scripts/                       本地记录、启动器与 HTTP 服务
    assets/                        审阅网页
  tests/                           独立测试
  tools/                           合成演示、只读探测与显式真实宿主测试
  docs/                            设计、ADR、Task 与证据
```

## 先体验本地审阅

在本仓库根目录打开 PowerShell。下例假设 Python 3.10+ 可通过 `python` 运行；否则把它替换为你的 Python 绝对路径。源工作区开发时可使用 `C:\example\project\.venv\Scripts\python.exe`，这不是插件发行依赖。

```powershell
python '.\tools\demo.py'
```

打开终端打印的完整本地链接。演示只使用明确标注的合成材料，状态在被 Git 忽略的 `.cache/demo-state/`，不会为真实项目启用记录。按 Ctrl+C 停止页面服务。要演示“后来发现仍有问题”，另开终端运行：

```powershell
python '.\tools\demo.py' --add-correction
```

重新打开记录会看到新增材料提示，之前保存的人工版本仍保留。演示不是自动采集证据。

## 实验安装与运行

需要 Windows、Python 3.10+ 和支持插件 Hooks 的 Codex。当前实测版本为 `0.155.0-alpha.16`，测试进程显式启用 `features.hooks=true`；其他版本及桌面默认开关仍需核对。无需 npm、Docker、旧 TAS 服务或第三方 Python 包。

本目录是非默认 marketplace，本机已注册并安装。其他机器的本地安装命令如下；先检查 `codex plugin marketplace list` 是否已有同名 `personal`，冲突时解决名称或来源冲突，不覆盖已有目录：

```powershell
codex plugin marketplace add .
codex plugin add tas-notes@personal
```

GitHub 分发应把本目录作为独立仓库根，保留 `.agents/plugins/marketplace.json` 和 `plugins/`，然后通过该仓库注册 marketplace。不能把任意 GitHub 链接都当作可安装插件。私有仓库创建与上传状态记录在 NOTES-003；未经另行授权不公开发布。

本地手动运行方式（从本目录执行）：

```powershell
$notesPython = (Get-Command python -ErrorAction Stop).Source
$notesEntry = (Resolve-Path '.\plugins\tas-notes\scripts\tas_notes.py').Path
& $notesPython $notesEntry configure-runtime
# 用你明确选择的项目绝对路径替换下一行示例。
& $notesPython $notesEntry enable 'C:\example\project'
& $notesPython $notesEntry status
& $notesPython $notesEntry serve
# 停止后续采集；已有记录不会被删除。
& $notesPython $notesEntry disable 'C:\example\project'
```

`configure-runtime` 只配置本地 Python 启动路径。仍需在 Codex 正常流程中启用并信任插件 Hooks。本机已验证变量展开与触发；安装成功不能代替项目启用、运行配置与实际落盘检查。更新插件后使用新任务拾取新版本。

默认数据位于 `%LOCALAPPDATA%/TAS Notes/`，不能放在正在记录的项目内部；测试可通过 `--state-dir` 或 `TAS_NOTES_STATE_DIR` 选择隔离目录。Hook 启动器读取同目录的 `runtime.cmd`，无需改变 PowerShell 执行策略。Python 可执行文件需有 ASCII 路径或可用的 Windows 短路径；安装及状态路径含 `%` 暂不支持。

## 记录和隐私边界

- 启用范围按 Hook 的 `cwd` 精确匹配，不自动涵盖子目录、其他 worktree 或项目。
- 已启用项目的纯问答也会记录可取得的输入；首版没有“是否值得记录”的语义筛选。
- 不扫描会话文件、不采集隐藏推理。尚未送达的事件、异常中断前未落盘的数据、缺失的宿主字段不能恢复。
- 工具空返回不等于成功；本版不提取结构化退出状态。压缩/中断可能只有事件没有正文。正常 CLI 退出观察到 SessionEnd，但 app-server 取消订阅没有，不能由缺少结束事件推断任务仍运行。
- 保留字段白名单，有限遮蔽敏感键和 Bearer 串；不是全面脱敏。正文仍可能含秘密，导出前需人工检查。
- 数据是当前系统用户可读的本地明文，没有应用级加密、多用户隔离。项目外存储降低误提交风险，不阻止备份软件同步。
- “本地”指本插件的存储和审阅；不改变 Codex 原有模型的数据流。
- 有稳定事件 ID 时幂等；无稳定 ID 时保留重复投递，以免误丢真实重复操作。
- Hook 返回码不会因采集失败阻断任务；`status` 显示最近一次安全错误标记。该历史标记不代表当前仍失败，也不能发现宿主根本没投递事件。
- `status` 的 `runtime` 区分未配置、配置格式无效、目标不存在和路径已配置；只检查配置，不执行 Python 或核对宿主信任。每个项目的最近落盘是历史观察，不是当前在线证明。错误标记属于全局历史，不能自动归因到某个项目。
- 本机实测每次 Hook 约 0.6～0.94 秒，存在响应开销；慢机器和异常退出仍可能超时漏记。
- 单输入 512 KiB，单字段 32K 字符截断并标注，单会话约 10 MiB / 5000 事件上限。数据库约 256 MiB 停止新增检查不是严格磁盘配额。暂无保留期限、自动清理、备份和导入恢复。
- 审阅页面按需启动，监听 127.0.0.1 随机端口，使用临时访问令牌，无云端上传。关闭页面服务不会停止独立 Hook 采集。它不是公网 Web 服务。

## 开发验证

从本目录运行：

```powershell
python -m unittest discover -s tests -v
python '.\tools\probe_codex_hooks.py'
```

探测程序只请求本机 app-server 的初始化、`hooks/list` 和 `plugin/read`，结束即退出；不启动模型回合或改变信任。若沙箱不能访问 Codex 运行目录，应在获准的正常终端执行，不通过修改全局策略解决。

安装后可加 `--installed --cwd <空白项目绝对路径>` 只读核对安装版。`python tools/verify_live_codex.py --run --phase flow` 则会实际调用 Codex 模型，只用于显式授权的合成集成测试；另有 `smoke`、`lifecycle`、`exit` 阶段。临时数据只留本机，结束停用；lifecycle 的取消订阅结束事件断言在当前宿主预期失败，见接入证据，不将其伪装成全部通过。

开发者可用 `--phase shape --inspect-hook-shape` 在隔离合成测试状态内统计顶层字段名/类型；输入原样转发给已信任插件，不记录字段值，不修改全局运行配置。它不属于日常采集或安装步骤。

## 可读过程记录（NOTES-006）

正文优先展示人工版本或当前 Agent 草稿；没有语义草稿时明确显示自动摘录。工具 JSON 仅在折叠来源内查看，引用按钮定位到事件。编辑区域按需展开，导出仍只包含已保存人工版本。

对 Codex 说“整理这份 TAS Notes 记录”，或在页面点击“获取 Codex 整理指令”复制到 Codex。Skill 使用 `summary-source` 分页读取预览、`summary-event` 读取必要长来源、`summary-save` 存储独立草稿。每条总结校验来源编号和原文短引，快照变化时拒绝旧提交。引用匹配不保证总结正确，仍需人审。

当前未实现无人值守的每轮语义总结；缺失理由、图像内容、未读完的原文不能靠模型补成事实。自动采集不受整理是否运行影响。详见 ADR-N002 与 NOTES-006。

## 审阅页面连接中断

为用户打开页面时使用 `tas_notes.py open --no-browser`，它检测或启动独立后台服务并返回当前本地 URL 和 PID。前台 `serve` 模式仍需终端持续存活。后台模式不注册开机启动，重启电脑或进程退出后需重新打开；旧链接不会自动恢复。无法连接时先复制未保存编辑，再双击 TAS Notes 固定入口，或让 Codex 打开记录。关闭网页不停止记录采集。

## 固定打开入口（NOTES-008）

在插件目录运行 `scripts/install_shortcuts.ps1 -Python <现有Python路径>`，会为当前用户创建桌面和开始菜单中的 **TAS Notes**。双击后检查服务，正常则复用、失效则启动，再打开系统默认浏览器；无需先打开 Codex。只复制代码和页面，不复制运行数据库，不改变录制授权，也不设置开机启动。更新插件后再次运行安装器即可更新入口副本。

程序副本位于 `%LOCALAPPDATA%/TAS Notes/reviewer/<build>/`，不依赖插件缓存仍存在，但依赖安装时指定的Python解释器；若解释器被移除，需要重新安装入口。无Codex时可以阅读、手动编辑、保存与导出，不能自动新增Codex工作事件或生成模型总结。

Codex 内部打开统一使用 `tas_notes.py open --no-browser` 返回当前URL，外部直接运行 `tas_notes.py open` 打开默认浏览器。启动锁避免正常并发打开重复启动，鉴权健康检查核对应用标识、PID、协议和构建指纹。失效记录不是终止进程的指令；不同构建的旧服务不强杀，避免影响旧页面编辑。注册文件含本地令牌，不应分享。

采集、规则摘录、打开页面没有额外模型调用。当前Agent阅读来源并生成智能总结会产生输入/输出token，并占用会话上下文；具体账户计量取决于使用方式。没有自动背景总结费用。
