# Blackbox Plugin

**给 Codex 的本地执行异常记录本。**

在你明确启用的项目中，留下 Codex 执行命令时的错误返回与后续操作，按项目和会话回看，并导出可复核的 Markdown 记录。

[安装与首次接入](release/INSTALL.md) · [使用边界](release/ALPHA-GUIDE.md) · [开发与验证](docs/DEVELOPMENT.md) · [当前进展](docs/current-status.md)

## 快速安装

当前版本 **0.4.0-alpha.6.43**。需要 **Windows、Python 3.11+ 和支持本地插件、MCP、Hook 的 Codex 桌面版**。Python、Codex 和 Git 需要在终端可用。当前为私有仓库试用，需要相应 GitHub 访问权限；最低 Codex 版本及 API Key 登录模式尚未完成专项验收。

在终端依次执行，第一条成功后再执行第二条：

```powershell
codex plugin marketplace add zemekeznb/blackbox-plugin --ref v0.4.0-alpha.6.43
codex plugin add blackbox@personal
```

Codex 会获取插件并安装。完成后回到 Codex，发送：

> 请运行 blackbox-setup 完成 Blackbox 首次设置，然后用 blackbox-review 打开 Blackbox。暂不启用任何项目采集。

如果 Codex 尚未识别新插件，保存工作、完整退出并重新打开 Codex，再发送这句话。首次设置会检查依赖并配置本地运行环境。失败时按具体提示修复后重试，详见[安装指南](release/INSTALL.md)。

## 第一次使用

在已打开的 Blackbox 页面点击 **接入第一个项目**，把页面提供的接入提示词发到目标项目的 Codex 会话。发送前确认保存范围与授权；Codex 将检查运行配置、审阅 Hook、启用该项目，并执行真实命令核验记录。

**安装和启用项目不等于采集已生效。** Hook 需要通过 Codex 官方流程信任。首次安装并信任后，可能需要保存工作、完整退出并重新打开 Codex，无需重启电脑；回到原会话继续验证，无需重复授权。看到真实命令记录到达，才算接入完成。

日常继续在 Codex 中工作，需要复盘时打开 Blackbox：选择项目和会话，查看异常返回及关联操作，必要时导出记录。

## 它能帮你做什么

| 能力 | 用途 |
| --- | --- |
| 按项目、会话回看 | 找到某次工作中已收到并识别的执行异常 |
| 命令与返回对照 | 保留可查看的来源，减少凭记忆描述问题 |
| 后续操作关联 | 查看保守关联的重复调用与后续操作，不自动判定修复成功 |
| 时间和类型筛选 | 缩小要查看的异常范围 |
| Markdown 导出 | 保存固定范围快照，用于复盘或自行分享 |
| 本地管理 | 按项目启停采集，查看状态，备份、恢复和清理记录 |

## 数据与宿主边界

- 无 Blackbox 账号、订阅绑定或独立模型调用；依赖 Codex 的本地 Hook 与会话接口。
- 默认保存在本机，不自动上传。保存范围包括获授权的可见输入、回复、工具调用及返回，**不只异常**；有限脱敏不能保证去掉所有敏感信息。
- 默认目录为 `%LOCALAPPDATA%\Blackbox`。Windows 打包应用可能映射目录，以状态输出的实际路径为准。
- 安装不会自动选择项目；只接收已启用项目中经宿主确认的持久用户会话。临时、内部或身份未确认的事件不保存。
- 宿主未投递的记录不会补回；无法识别的异常可能漏记。“暂无异常”不等于执行没有失败，也不推断根因或责任。
- 当前面向 Codex + Windows。直接调用模型 API、DeepSeek Harness 和其他宿主尚未适配；API Key 登录 Codex 的完整链路尚未实测。
- 从旧 TAS Notes 升级时保留数据与配置兼容；旧名称仅作为兼容输入和历史证据保留。

## 卸载与备份

先使用 `blackbox-review` 备份本地数据，再从 Codex 插件管理卸载 Blackbox。普通卸载不自动删除历史数据，也不卸载 Python、Codex 或其他插件。停止阅读服务不等于关闭项目采集。完整操作见[安装指南](release/INSTALL.md)。

## 文档

| 文档 | 内容 |
| --- | --- |
| [安装与首次接入](release/INSTALL.md) | Codex 命令安装、首次设置、Hook 信任、升级与卸载 |
| [Alpha 使用说明](release/ALPHA-GUIDE.md) | 保存范围、限制、备份恢复与候选包边界 |
| [开发与验证](docs/DEVELOPMENT.md) | 测试、打包、CI 与发行规则 |
| [架构](docs/architecture.md) | 本地采集、存储和阅读实现 |
| [当前状态](docs/current-status.md) | 已验证结果与未完成事项 |
| [发行差距对照](docs/reports/NOTES-131-release-gaps.md) | 对照参考项目整理的后续工作 |

## 许可

目前尚未授予开源许可证；源码及候选包用于获授权的开发和试用，不应据此推定可公开分发。公开发行前需由维护者确定许可与分发渠道。
