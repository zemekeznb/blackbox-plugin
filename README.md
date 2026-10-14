# Blackbox Plugin

**给 Windows 版 Codex 桌面端的本地执行异常记录本。**

按项目和会话回看命令错误、保存的返回与后续操作，导出可复核的 Markdown 记录。默认保存在本机，只记录你明确接入的项目。

[安装指南](release/INSTALL.md) · [使用与隐私](release/ALPHA-GUIDE.md) · [问题反馈](https://github.com/zemekeznb/blackbox-plugin/issues) · [开发与验证](docs/DEVELOPMENT.md)

## 交给 Codex 安装

当前公开试用版 **0.4.0-alpha.6.45**。准备好 **Windows、Codex 桌面版、可在 Codex 中通过 `python` 调用的 Python 3.11+**，以及能够访问 GitHub 的网络。无需 Git、GitHub 登录或单独安装 Codex CLI。

打开 **Codex 桌面版**，在会话中复制发送：

```text
请按 https://github.com/zemekeznb/blackbox-plugin/blob/v0.4.0-alpha.6.45/release/INSTALL-CODEX.md 为我安装并初始化 Blackbox，然后打开界面。暂不启用任何项目采集，不修改模型、账号或系统环境配置。
```

Codex 会检查依赖、下载并校验插件、完成安装与初始化，然后打开 Blackbox。安装不会自动启用项目。若提示插件尚未加载，按提示新开会话；确需重载时，保存工作并完整退出重开 Codex。详细步骤与排查见[安装指南](release/INSTALL.md)。

## 接入第一个项目

1. 在 Blackbox 页面点击 **接入第一个项目**。
2. 阅读保存范围，把页面提供的提示词发送到目标项目的 Codex 桌面会话。
3. Codex 完成项目设置、协助官方 Hook 信任审阅，并用一条无副作用的真实命令检查记录是否收到。需要重开时，按提示回来继续。

**收到对应真实记录，才表示项目接入成功。** 此后继续在该项目中正常使用 Codex。

## 查看与导出

需要回看时，在 Codex 桌面会话中发送：

```text
打开 Blackbox。
```

在页面中选择项目、会话和异常，查看保存的命令、返回与后续操作；也可以筛选记录并导出 Markdown。

## 它能帮你做什么

| 能力 | 用途 |
| --- | --- |
| 按项目、会话回看 | 找到已收到并识别的执行异常 |
| 命令与返回对照 | 查看保存的来源，减少凭记忆描述问题 |
| 后续操作关联 | 查看保守关联的重复调用，不自动判定修复成功 |
| 筛选与 Markdown 导出 | 缩小范围，保存可自行分享的快照 |
| 本地管理 | 按项目启停采集，备份、恢复和清理记录 |

## 数据与适用范围

- 默认本地保存，不自动上传，不需要 Blackbox 账号，也不自动调用额外模型。
- 保存范围包含获授权的可见输入、回复、工具调用及返回，**不只异常**。有限脱敏不能保证去掉所有敏感信息。
- 本轮仅面向 **Windows 版 Codex 桌面端**；不采集终端 CLI 会话，包括安装时辅助打开的终端会话。CLI 支持留待后续补充。
- 宿主未投递的内容无法补回；“暂无异常”不等于执行没有失败，也不判断根因或责任。
- 关闭页面不会停用采集；更新和普通卸载会保留已有数据。操作说明见[使用与隐私](release/ALPHA-GUIDE.md)。

## 文档与反馈

| 文档 | 内容 |
| --- | --- |
| [安装指南](release/INSTALL.md) | 对话安装、首次接入、排查、更新与卸载 |
| [使用与隐私](release/ALPHA-GUIDE.md) | 保存范围、备份恢复、导出和已知限制 |
| [开发与验证](docs/DEVELOPMENT.md) | 测试、构建、CI、发行与当前进展 |

遇到问题请到 [Issues](https://github.com/zemekeznb/blackbox-plugin/issues) 提供版本、失败步骤及脱敏后的报错。不要附完整数据库、对话正文、密钥或账号凭据。

## 许可

仓库公开供查看和试用，尚未选定开源许可证。公开可访问不代表已授予开源许可。
