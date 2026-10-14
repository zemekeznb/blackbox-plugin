# 安装与首次接入

当前公开试用版 **0.4.0-alpha.6.44**。先安装并打开 Windows 版 Codex 桌面应用，确保 PowerShell 和 Codex 都能通过 `python` 调用 Python 3.11+，并能访问 GitHub。无需 Git 或 GitHub 登录；不需要单独安装 Codex CLI。

## 1. 安装 Blackbox

任选一种方式，两者共用同一固定版本的安装工具和发行包。

### 直接命令

在 PowerShell 中复制运行以下整行：

```powershell
python -I -c "import hashlib,urllib.request; s=urllib.request.urlopen('https://raw.githubusercontent.com/zemekeznb/blackbox-plugin/v0.4.0-alpha.6.44/tools/bootstrap_blackbox.py',timeout=30).read(131073); assert hashlib.sha256(s).hexdigest()=='6ce1f21bf1bbd672ea56b7757d3061be272c3f55f42c68758b131f8afc02c616','Installer checksum mismatch'; exec(compile(s,'blackbox-install','exec'))"
```

命令从公开固定标签获取[安装工具](https://github.com/zemekeznb/blackbox-plugin/blob/v0.4.0-alpha.6.44/tools/bootstrap_blackbox.py)，先校验工具的固定 SHA256，再执行。安装工具会定位运行中的 Codex、下载并校验插件包、通过官方命令登记并完成本地初始化；不修改持久 PATH 或 PowerShell 执行策略。摘要是完整性校验，不是独立发布者签名。

进程成功退出且输出包含 `"installed": true` 和 `"runtime_configured": true` 时，文件安装与初始化完成。然后在 Codex 中发送：

```text
打开 Blackbox。
```

### Codex 引导

不想操作终端时，在 Codex 会话中发送：

```text
请按 https://github.com/zemekeznb/blackbox-plugin/blob/v0.4.0-alpha.6.44/release/INSTALL-CODEX.md 为我安装并初始化 Blackbox，然后打开界面。暂不启用任何项目采集，不修改模型、账号或系统环境配置。
```

Codex 会读取执行说明、检查依赖，并调用同一个安装工具。无需再运行上面的命令。

### 插件尚未加载时

按 Codex 提示刷新或新开会话。若仍需完整重载，先保存工作，完整退出并重新打开 Codex，然后发送：

```text
继续完成 Blackbox 的首次设置并打开页面，暂不启用任何项目采集。
```

安装只完成插件登记和运行初始化，不启用新项目、不授予 Hook 信任。更新时已有项目开关保持原状。

## 2. 接入一个项目

在 Blackbox 页面点击 **接入第一个项目**，阅读提示中的项目与保存范围，再把页面提供的接入提示词发到目标项目的 Codex 会话。页面提示词是接入授权的统一入口。

保存范围包括当前项目的可见输入、回复、工具调用及返回，可能含项目内容或敏感信息，默认不上传。Codex 会协助完成官方 Hook 审阅、启用该项目，并执行无副作用的真实命令检查记录。已有授权无需重复提供；需要重开时按给出的续验步骤继续，无需重启电脑。

**安装成功、项目已启用、真实记录已收到是三个不同状态。只有对应记录收到，项目接入才完成。**

## 遇到问题

将具体报错发回 Codex，或提交[问题反馈](https://github.com/zemekeznb/blackbox-plugin/issues)。说明使用哪种入口、当前版本和失败步骤，先移除密钥与私人内容。

| 现象 | 下一步 |
| --- | --- |
| `python` 不存在或版本不满足 | 安装完整 Python 3.11+ 并启用其 PATH 选项，再重新打开 PowerShell 与 Codex |
| 无法定位 Codex，或发现多个宿主 | 打开目标 Codex 桌面应用；仍有歧义时让 Codex 检查，不另装 CLI 或随意选旧版本 |
| 网络、工具或发行包摘要校验失败 | 检查 GitHub 连接，重新复制当前 README 命令；不要跳过校验 |
| 安装源冲突 | 让 Codex 检查 Blackbox 安装源；若同一个 `personal` 源含其他插件，不删除整个源 |
| 安装完成但没有 Blackbox 工具 | 按上面的“插件尚未加载时”继续；不要反复重装 |
| 项目已启用但没有记录 | 检查 Hook 信任、运行配置与实际回执，必要时重载；不要以初始化成功代替采集验证 |

## 更新、停用与卸载

- **更新：** 先保存工作，并在 Codex 中让 Blackbox 停止阅读服务，再使用新版 README 的任一安装入口。历史、阅读偏好和项目设置保留；需要重开时按提示继续。正在进行的长回合不保证无损热加载。
- **停用项目：** 在采集设置中停用指定项目，或让 `blackbox-review` 完成。关闭页面或停止阅读服务不会停用采集，停用不删除历史。
- **卸载：** 可先让 `blackbox-review` 将数据备份到仓库之外的新文件，再通过 Codex 插件管理卸载。普通卸载不删除本地历史、Python、Codex 或其他插件。

备份恢复、导出及隐私说明见[使用与隐私](ALPHA-GUIDE.md)。

## 已验证到哪一步

.44 已有公开下载校验、缺少 Codex/Git PATH 条件下的隔离安装、重复安装和初始化证据。实际用户的桌面打开、Hook 信任、重载与真实记录到达须分别验收；干净电脑、最低 Codex 版本及 API Key 登录模式尚未完成专项验收。最新证据见[当前状态](../docs/current-status.md)。

[Codex 执行说明](INSTALL-CODEX.md)供安装会话读取；[开发与验证](../docs/DEVELOPMENT.md)供维护者使用。
