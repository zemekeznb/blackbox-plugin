# 开发与验证

需要 Python 3.11+；前端测试使用 Node.js（CI 为 22）。运行时代码使用 Python 标准库，不要求用户安装 Node.js。

在仓库根目录运行：

```powershell
python -m unittest discover -s tests -p 'test_*.py'
$bbTests = Get-ChildItem tests -Filter 'test_*.cjs' | ForEach-Object FullName
node --test @bbTests
python tools/build_alpha.py
python tools/verify_alpha_candidate.py dist/blackbox-plugin-0.4.0-alpha.6.44-candidate.zip
```

候选构建使用文件白名单、逐文件 SHA256 和有限敏感模式检查；不包含本地数据库和 `.local`。包内安装说明为 `release/INSTALL.md`。校验工具核对摘要并运行隔离阅读器检查，不安装插件、不启用真实项目。

## CI 与交付

`.github/workflows/alpha.yml` 在 PR 和手动触发时运行 Windows/Python 3.11、3.13 的源码与解包测试；当前不在每次 main 推送时自动触发，也不发布制品。

版本来源为 `plugins/blackbox/.codex-plugin/plugin.json`。构建会检查引导工具的 VERSION 和 Codex 安装说明中的固定标签是否一致；不一致时停止构建。修改版本时还需同步首页与安装指南中的命令、引导工具 SHA256 和提示词链接。摘要取对应标签中的原始文件字节，不能取可能发生换行转换的工作副本。先测试并构建试用候选，再记录验收证据；已提交且工作树干净后可重建具有明确提交来源的候选包。

普通构建不代表发布许可。`--for-release` 要求已有 LICENSE 和干净提交，但仍不会发布或证明真实宿主采集通过。当前尚未确定开源许可证，没有自动 GitHub Release、市场发布或 npm 发布流程。

面向用户提供 README 中的两种入口：直接命令安装，或 Codex 会话引导。两者共用固定标签的 `tools/bootstrap_blackbox.py`：定位宿主、下载校验载荷、复用官方登记与首次设置逻辑。直接命令还校验引导工具的固定 SHA256；Codex 读取同一标签的 `release/INSTALL-CODEX.md` 并审阅工具。两者都不依赖 Codex CLI PATH 或 Git，不自动启用项目或写 Hook 信任。

根目录 `install.ps1` 与 Git 安装验证脚本保留为维护/兼容入口，不放到用户快速安装步骤中。没有发布 npm/PyPI 包，也没有官方公共插件目录条目；不要编造对应安装命令。

维护者先通过测试，在取得发布授权后提交代码并创建固定标签，从干净提交构建候选，将 ZIP/摘要/清单上传到同一版本 Release。引导工具只接受对应版本载荷；交付前应匿名验证公开引导文件与附件均可获取，并在移除 Codex/Git PATH 的隔离环境中原样执行 README 安装命令。不要移动旧标签或覆盖旧附件来伪装新版本。公开不等于开源许可，后续版本仍需授权发布。

NOTES-137 的入口整理复用已发布 .44，不修改安装工具或载荷，也不提升产品版本。更新工作区文档不等于 GitHub 首页已经同步；本轮新构建仅用于本地验证，不覆盖线上 .44。

以下旧 Git 路径集成工具仍供维护者检查宿主能力，不是客户安装入口。它在临时 CODEX_HOME 中执行真实 Codex CLI 获取/安装和缓存内首次设置，不调用模型、不改用户真实安装或信任。输出路径须为新文件：

```powershell
python tools/verify_git_install.py --ref v0.4.0-alpha.6.43 --version 0.4.0-alpha.6.43 --output .local/git-install-43.json
```

## 验证边界

单元测试和模拟 Hook 只验证代码路径。安装成功、Hook 已信任与真实事件收到必须分别验收；不能用离线测试替代新用户安装、Codex 重载、真实命令投递和界面体验。已知限制见 [当前状态](current-status.md)。

修改按 [AGENTS.md](../AGENTS.md) 和 `docs/tasks/` 执行，运行数据留在仓库外。报告问题时提供版本、步骤和脱敏后的现象，不直接上传完整数据库或凭据。

## 维护文档

[当前状态](current-status.md)记录当前事实和未验收项，[开发计划](development-plan.md)记录后续工作，[架构](architecture.md)说明实现边界。历史版本和失败证据留在 tasks、reports 与 history 中，不重新作为当前安装指引。
