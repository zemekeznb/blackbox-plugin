# Blackbox Plugin

**给 Codex 的本地执行异常记录本。**

按项目和会话回看命令错误、保存的返回与后续操作，导出可复核的 Markdown 记录。默认保存在本机，只记录你明确接入的项目。

[安装指南](release/INSTALL.md) · [使用与隐私](release/ALPHA-GUIDE.md) · [问题反馈](https://github.com/zemekeznb/blackbox-plugin/issues) · [开发与验证](docs/DEVELOPMENT.md)

## 快速安装

当前公开试用版 **0.4.0-alpha.6.44**。需要 **Windows、Codex 桌面版、可通过 `python` 调用的 Python 3.11+**，以及能够访问 GitHub 的网络。安装前打开 Codex；无需 Git、GitHub 登录或单独安装 Codex CLI。

两种方式任选一种，安装的是同一版本。

### 方式一：直接运行命令

在 **PowerShell** 中复制运行：

```powershell
python -I -c "import hashlib,urllib.request; s=urllib.request.urlopen('https://raw.githubusercontent.com/zemekeznb/blackbox-plugin/v0.4.0-alpha.6.44/tools/bootstrap_blackbox.py',timeout=30).read(131073); assert hashlib.sha256(s).hexdigest()=='6ce1f21bf1bbd672ea56b7757d3061be272c3f55f42c68758b131f8afc02c616','Installer checksum mismatch'; exec(compile(s,'blackbox-install','exec'))"
```

命令校验固定安装工具后自动下载、校验并安装插件，完成本地初始化。输出中 `installed` 和 `runtime_configured` 均为 `true` 表示安装完成。随后在 Codex 中发送：

```text
打开 Blackbox。
```

### 方式二：交给 Codex 安装

在 **Codex 会话**中发送：

```text
请按 https://github.com/zemekeznb/blackbox-plugin/blob/v0.4.0-alpha.6.44/release/INSTALL-CODEX.md 为我安装并初始化 Blackbox，然后打开界面。暂不启用任何项目采集，不修改模型、账号或系统环境配置。
```

两种方式都不会自动启用项目。若 Codex 提示插件尚未加载，按提示新开会话；确需重载时，保存工作并完整退出重开 Codex。详细排查见[安装指南](release/INSTALL.md)。

## 接入项目与日常使用

1. 在 Blackbox 页面点击 **接入第一个项目**。
2. 阅读保存范围，把页面提供的提示词发送到目标项目的 Codex 会话。
3. Codex 完成项目设置、协助官方 Hook 信任审阅，并用一条无副作用的真实命令检查记录是否收到。需要重开时，按提示回来继续。

**收到对应真实记录，才表示项目接入成功。** 此后继续正常使用 Codex；要回看时发送“打开 Blackbox”，选择项目、会话和异常，查看或导出记录。

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
- 当前仅面向 **Codex + Windows**。宿主未投递的内容无法补回；“暂无异常”不等于执行没有失败，也不判断根因或责任。
- 关闭页面不会停用采集；更新和普通卸载会保留已有数据。操作说明见[使用与隐私](release/ALPHA-GUIDE.md)。

## 文档与反馈

| 文档 | 内容 |
| --- | --- |
| [安装指南](release/INSTALL.md) | 两种入口、首次接入、排查、更新与卸载 |
| [使用与隐私](release/ALPHA-GUIDE.md) | 保存范围、备份恢复、导出和已知限制 |
| [开发与验证](docs/DEVELOPMENT.md) | 测试、构建、CI、发行与当前进展 |

遇到问题请到 [Issues](https://github.com/zemekeznb/blackbox-plugin/issues) 提供版本、失败步骤及脱敏后的报错。不要附完整数据库、对话正文、密钥或账号凭据。

## 许可

仓库公开供查看和试用，尚未选定开源许可证。公开可访问不代表已授予开源许可。
