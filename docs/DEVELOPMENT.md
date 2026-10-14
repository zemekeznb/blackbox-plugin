# 开发与验证

需要 Python 3.11+；前端测试使用 Node.js（CI 为 22）。运行时代码使用 Python 标准库，不要求用户安装 Node.js。

在仓库根目录运行：

```powershell
python -m unittest discover -s tests -p 'test_*.py'
$bbTests = Get-ChildItem tests -Filter 'test_*.cjs' | ForEach-Object FullName
node --test @bbTests
python tools/build_alpha.py
python tools/verify_alpha_candidate.py dist/blackbox-plugin-0.4.0-alpha.6.43-candidate.zip
```

候选构建使用文件白名单、逐文件 SHA256 和有限敏感模式检查；不包含本地数据库和 `.local`。包内安装说明为 `release/INSTALL.md`。校验工具核对摘要并运行隔离阅读器检查，不安装插件、不启用真实项目。

## CI 与交付

`.github/workflows/alpha.yml` 在 PR 和手动触发时运行 Windows/Python 3.11、3.13 的源码与解包测试；当前不在每次 main 推送时自动触发，也不发布制品。

版本来源为 `plugins/blackbox/.codex-plugin/plugin.json`。修改版本时同步安装指南和首页。先测试并构建试用候选，再记录验收证据；已提交且工作树干净后可重建具有明确提交来源的候选包。

普通构建不代表发布许可。`--for-release` 要求已有 LICENSE 和干净提交，但仍不会发布或证明真实宿主采集通过。当前尚未确定开源许可证，没有自动 GitHub Release、市场发布或 npm 发布流程。

面向用户只提供 README 中的 Codex 命令安装；.43 起标签固定到安装来源提交，由 blackbox-setup 完成初始化，不再引导下载 ZIP 或运行根目录安装脚本。历史 .42 附件和源码中的兼容安装脚本保留，构建工具仍可用于维护者的离线回归。版本以公开仓库 [Releases](https://github.com/zemekeznb/blackbox-plugin/releases) 为准，后续版本需逐次授权发布，不自动生成版本或发布。

可运行以下集成检查，公开仓库无需 GitHub 认证。它在临时 CODEX_HOME 中执行真实 Codex CLI 获取/安装和缓存内首次设置，不调用模型、不改用户真实安装或信任。输出路径须为新文件：

```powershell
python tools/verify_git_install.py --ref v0.4.0-alpha.6.43 --version 0.4.0-alpha.6.43 --output .local/git-install-43.json
```

## 验证边界

单元测试和模拟 Hook 只验证代码路径。安装成功、Hook 已信任与真实事件收到必须分别验收；不能用离线测试替代新用户安装、Codex 重载、真实命令投递和界面体验。已知限制见 [当前状态](current-status.md)。

修改按 [AGENTS.md](../AGENTS.md) 和 `docs/tasks/` 执行，运行数据留在仓库外。报告问题时提供版本、步骤和脱敏后的现象，不直接上传完整数据库或凭据。
