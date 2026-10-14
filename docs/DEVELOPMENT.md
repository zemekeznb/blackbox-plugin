# 开发与验证

需要 Python 3.11+；前端测试使用 Node.js（CI 为 22）。运行时代码使用 Python 标准库，不要求用户安装 Node.js。

在仓库根目录运行：

```powershell
python -m unittest discover -s tests -p 'test_*.py'
$bbTests = Get-ChildItem tests -Filter 'test_*.cjs' | ForEach-Object FullName
node --test @bbTests
python tools/build_alpha.py
python tools/verify_alpha_candidate.py dist/blackbox-plugin-0.4.0-alpha.6.42-candidate.zip
```

候选构建使用文件白名单、逐文件 SHA256 和有限敏感模式检查；不包含本地数据库和 `.local`。包内安装说明为 `release/INSTALL.md`。校验工具核对摘要并运行隔离阅读器检查，不安装插件、不启用真实项目。

## CI 与交付

`.github/workflows/alpha.yml` 在 PR 和手动触发时运行 Windows/Python 3.11、3.13 的源码与解包测试；当前不在每次 main 推送时自动触发，也不发布制品。

版本来源为 `plugins/blackbox/.codex-plugin/plugin.json`。修改版本时同步安装指南和首页。先测试并构建试用候选，再记录验收证据；已提交且工作树干净后可重建具有明确提交来源的候选包。

普通构建不代表发布许可。`--for-release` 要求已有 LICENSE 和干净提交，但仍不会发布或证明真实宿主采集通过。当前尚未确定开源许可证，没有自动 GitHub Release、市场发布或 npm 发布流程。

现有 .42 试用包已人工发布到私有仓库 [Releases](https://github.com/zemekeznb/blackbox-plugin/releases)。版本 tag 固定到构建来源提交，安装 ZIP、SHA256 文件与构建清单单独上传；回下载核对后才更新 README 的下载入口。GitHub 自动生成的源码归档不代替经过校验的安装候选包。后续版本仍需逐次授权发布，不自动生成版本或发布。

## 验证边界

单元测试和模拟 Hook 只验证代码路径。安装成功、Hook 已信任与真实事件收到必须分别验收；不能用离线测试替代新用户安装、Codex 重载、真实命令投递和界面体验。已知限制见 [当前状态](current-status.md)。

修改按 [AGENTS.md](../AGENTS.md) 和 `docs/tasks/` 执行，运行数据留在仓库外。报告问题时提供版本、步骤和脱敏后的现象，不直接上传完整数据库或凭据。
