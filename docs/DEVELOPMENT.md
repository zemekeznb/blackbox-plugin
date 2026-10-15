# 开发与验证

需要 Python 3.11+；前端测试使用 Node.js（CI 为 22）。运行时代码使用 Python 标准库，不要求用户安装 Node.js。

在仓库根目录运行：

```powershell
python -m unittest discover -s tests -p 'test_*.py'
$bbTests = Get-ChildItem tests -Filter 'test_*.cjs' | ForEach-Object FullName
node --test @bbTests
python tools/build_alpha.py
$bbVersion = (Get-Content plugins/blackbox/.codex-plugin/plugin.json -Raw | ConvertFrom-Json).version
python tools/verify_alpha_candidate.py "dist/blackbox-plugin-$bbVersion-candidate.zip"
```

候选构建使用文件白名单、逐文件 SHA256 和有限敏感模式检查；不包含本地数据库和 `.local`。包内安装说明为 `release/INSTALL.md`，随源码候选版本更新；面向用户的[安装指南](INSTALL.md)保持在已发布版本。校验工具核对摘要并运行隔离阅读器检查，不安装插件、不启用真实项目。

交付流程自身的测试单独运行；它们只在源码中验证发布逻辑，不随用户候选包分发：

```powershell
python -m unittest discover -s tools/tests -p 'test_*.py'
```

## CI 与 CD

本项目采用持续交付（CD）：自动测试、打包和准备发布草稿，最终公开发布由维护者确认。草稿准备需要维护者明确启动；向 main 推送只自动执行 CI，不会每次生成一个版本，也不会更新用户已经安装的插件。

### 维护者操作速查

| 阶段 | 你或协作的 Codex 做什么 | 自动完成什么 |
| --- | --- | --- |
| 修改代码 | 明确改动范围，完成代码和相关测试 | 本地命令执行对应测试 |
| 提交到 main | 审阅改动；授权 Codex 提交推送，或自行推送 | main CI 检查源码、界面和候选包 |
| 决定准备版本 | 确定版本号、变更说明，同步候选版本文件；推送后确认 CI 通过 | 分别检查候选版本和公开安装入口的一致性 |
| 准备发布草稿 | 在 Actions → **Prepare release draft** → **Run workflow** 选择 main，输入新版本标签 | 重新完整测试，通过后创建新标签、构建并上传 ZIP、SHA256 文件和清单到草稿 |
| 验收并公开发布 | 用草稿中的同一份安装包完成真实 Windows/Codex 桌面验收；检查发布说明后点击 **Publish release** | 发布事件启动下载验证 |
| 发布后检查 | 查看验证结果，并检查 README 安装提示词的实际体验 | 匿名验证版本、来源与摘要；通过后更新公开版本记录和四份用户文档，再启动 main CI |

**不要提前手动创建版本标签。** 草稿流程负责创建标签；已有标签、草稿或 Release 会使它停止。普通代码提交不必升级插件版本，也不必运行草稿流程。

也可以直接在协作会话中提出“准备下一个版本，先生成草稿，暂不发布”，交由 Codex 完成版本整理、测试和启动流程；确认真实桌面验收后，再明确要求“发布这个草稿”。这两个指令对应不同授权阶段，前一个不包含最终公开发布。

源码候选版本与公开安装版本分开维护。准备草稿期间，README 和用户安装指南继续指向当前可下载版本；只有维护者确认公开发布、下载验证通过后，自动流程才切换入口。已安装插件由用户主动更新。

### 工作流与边界

| 工作流 | 触发方式 | 结果 |
| --- | --- | --- |
| [`alpha.yml`](../.github/workflows/alpha.yml) | main 推送、PR、手动运行，或由准备草稿流程调用 | Windows/Python 3.11、3.13 的源码与解包测试、界面测试、候选包完整性及隔离阅读器检查 |
| [`prepare-release.yml`](../.github/workflows/prepare-release.yml) | 维护者手动输入版本标签 | 检查版本与提交来源，复用完整 CI，将通过检查的 ZIP、SHA256 文件和清单上传到 Release 草稿 |
| [`verify-release.yml`](../.github/workflows/verify-release.yml) | Release 发布，或维护者手动输入已发布标签 | 匿名核对安装说明、引导脚本和三个附件；发布事件验证通过后更新公开安装入口，手动运行默认只验证 |

运行结果以对应提交和标签的 [GitHub Actions 记录](https://github.com/zemekeznb/blackbox-plugin/actions)为准。创建草稿的作业需要 `contents: write`；公开入口更新由下载验证之后的独立作业执行，申请写入文档和启动 CI 所需的权限。测试和匿名下载验证作业保持只读。草稿创建不等于发布授权或真实桌面验收通过。

候选版本来源为 `plugins/blackbox/.codex-plugin/plugin.json`。修改版本时同步 `tools/bootstrap_blackbox.py` 的 VERSION、`release/INSTALL.md` 的提示词和 `release/INSTALL-CODEX.md` 的固定标签，构建与入口测试检查它们是否一致。先测试并构建试用候选，再记录验收证据；已提交且工作树干净后可重建具有明确提交来源的候选包。

公开安装版本记录在 [`release/published.json`](../release/published.json)。README 中的版本、徽章、固定标签安装提示词，以及 [`docs/INSTALL.md`](INSTALL.md)、[英文指南](en/README.md)中的对应内容由工具统一更新，和源码候选版本分别校验。准备新候选时，不手动升级这份记录或四份用户文档的公开版本块。块边界为 `<!-- blackbox:published:start -->` 和 `<!-- blackbox:published:end -->`；编辑说明文字时保留标记，历史版本说明不要放入这些块中。

项目采用 [MIT 许可证](../LICENSE)，构建器会将 LICENSE 纳入新候选包；第三方字体保留其[原有许可声明](../plugins/blackbox/assets/review.css)。`--for-release` 要求已有 LICENSE 和干净提交，但仍不会发布或证明真实宿主采集通过。准备草稿流程复用完整 CI 验证过的候选字节，不在上传时重新打包；不提供自动市场发布或 npm 发布。

本轮面向 Windows 版 Codex 桌面端，只提供 README 中的对话安装提示词。Codex 读取固定标签的 `release/INSTALL-CODEX.md`，审阅并执行同版本 `tools/bootstrap_blackbox.py`，完成宿主定位、载荷下载校验、官方登记与首次设置。不要求客户配置 Codex CLI PATH 或 Git，不自动启用项目或写 Hook 信任。Codex CLI 产品适配与完整验收留待后续，安装器内部调用官方 CLI 不代表已完成终端使用验收。

根目录 `install.ps1` 与 Git 安装验证脚本保留为维护/兼容入口，不放到用户快速安装步骤中。没有发布 npm/PyPI 包，也没有官方公共插件目录条目；不要编造对应安装命令。

### 准备与发布一个新版本

1. 同步插件版本、引导工具、包内 `release/INSTALL.md` 和 `release/INSTALL-CODEX.md` 中的固定标签，提交并推送，确认 main 的 CI 通过。保留 `release/published.json` 和用户文档的公开版本不变。新版本使用新的标签，不能重复发布当前 `.45` 或替换旧附件。
2. 在 Actions 中选择 main，手动运行 `prepare-release.yml`，输入 `v` 前缀的版本标签。标签须与该次运行提交中的插件版本一致。流程重新执行完整检查，两种 Python 环境均通过后，使用 Python 3.13 检查过的候选字节创建新标签及草稿，上传三个附件。已存在的标签、Release 或草稿会让流程停止，不覆盖已有内容。
3. 使用草稿中的同一份 ZIP 在 Windows 版 Codex 桌面端验收安装、页面打开和真实项目记录送达；安装与初始化还应检查移除 Codex/Git PATH 后的隔离场景。不要把测试夹具或隔离阅读器成功当作真实 Hook 投递证据。
4. 确认版本说明、附件和桌面验收结果后，由维护者在 GitHub Release 页面点击发布。Alpha 版本保留预发布标记。流程不会替维护者公开发布。
5. `verify-release.yml` 匿名检查固定标签的安装说明、引导脚本、ZIP、SHA256 文件和清单，核对版本、来源与摘要。只有通过后，独立作业才更新 `release/published.json`、`README.md`、`README.en.md`、`docs/INSTALL.md` 和 `docs/en/README.md`，提交并普通推送到 main，再显式启动 main CI。检查这两次工作流结果，并从更新后的安装提示词检查完整接入体验。

更新只涉及上述五个文件，不移动标签、不覆盖发行附件，也不改用户已安装的插件、项目开关或本地记录。旧版本不能覆盖较新的公开入口；相同版本重复运行保持幂等。验证失败或推送遇到并发提交时，旧入口保留，工作流报告失败；不会强制推送或自动回退版本。

下载验证订阅 GitHub 的 `release.published`，覆盖正式版以及从草稿发布的预发布版本。手动运行填写已发布标签，默认只读，不更新入口；需要恢复一次未完成的入口更新时，明确勾选 `promote`，重新通过验证后再尝试更新。若先前文档已经推送、只有后续 CI 启动失败，同版本恢复不会重复修改文档，仍会启动 CI。

入口自动提交使用 `GITHUB_TOKEN`，其推送通常不会再次触发工作流，因此流程显式启动 `alpha.yml`。如果以后通过其他自动化使用 `GITHUB_TOKEN` 发布 Release，也需显式触发下载验证；不能假定发布事件会继续串联。参见 GitHub 的[发布事件说明](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#release)和[工作流触发规则](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow#triggering-a-workflow-from-a-workflow)。

下载验证只读取公开文件，不执行下载的安装器、不安装或更新插件、不启用项目，不上传记录。验证失败时先保留具体失败信息，检查链接、附件与摘要；不要通过移动旧标签或覆盖旧附件掩盖问题。对已发布版本的修复仍应按授权范围交付新版本。引导工具只接受对应版本载荷，后续版本发布仍需维护者授权。

如果准备草稿流程在创建标签后失败，可能留下标签或不完整的草稿。先检查运行记录和远端状态；流程不会自动删除这些内容，也不会在重试时补传或替换已有附件。维护者确认清理范围后再处理，不能把部分成功当作可发布状态。

现有 .45 安装附件早于主分支新增 MIT 文件，其 ZIP、摘要与清单保留发布时的字节，`license_pending=true` 记录当时构建状态。公开历史清理保留版本标签名及安装所需的执行说明、引导脚本；清理后的标签指向经过整理的源码提交，附件清单仍保留原始构建来源，不能把该附件称为从清理后提交重新构建。已有安装不受源码文档清理影响。

手动下载验证也要求清单中的来源提交与公开标签一致；只有上述 `.45` 已知的两个完整提交 SHA 被明确列为历史例外，其他版本或其他 SHA 组合不会放行。发布事件触发的验证还会核对事件对应的提交，不能用手动运行绕过来源检查。

后续从含 LICENSE 的源码构建时，`license_pending` 自动变为 `false`，`publish_ready` 仍为 `false`，真实宿主验收仍单独记录。许可证已选定不等于旧安装附件被重新打包，也不自动构成正式发布就绪证明。当前操作入口以主分支 README 和安装指南为准。

以下旧 Git 路径集成工具仍供维护者检查宿主能力，不是客户安装入口。它在临时 CODEX_HOME 中执行真实 Codex CLI 获取/安装和缓存内首次设置，不调用模型、不改用户真实安装或信任。输出路径须为新文件：

```powershell
python tools/verify_git_install.py --ref v0.4.0-alpha.6.43 --version 0.4.0-alpha.6.43 --output .local/git-install-43.json
```

## 验证边界

单元测试和模拟 Hook 只验证代码路径。安装成功、Hook 已信任与真实事件收到必须分别验收；不能用离线测试替代新用户安装、Codex 重载、真实命令投递和界面体验。支持范围与已知限制见[使用与隐私](../release/ALPHA-GUIDE.md)。

公开保留 `tests/`，用于复现规则、权限和迁移边界，并检查候选包是否包含完整运行依赖。测试使用合成或匿名化输入；它们是可执行的行为约定，不是用户运行记录。

修改按 [AGENTS.md](../AGENTS.md) 执行，运行数据留在仓库外。报告问题时提供版本、步骤和脱敏后的现象，不直接上传完整数据库或凭据。

## 维护文档

[架构](architecture.md)说明数据流、权限、存储和兼容约定，[使用与隐私](../release/ALPHA-GUIDE.md)说明用户操作与支持范围。内部进展、计划、决策流水和原始验收材料不随公共文档发布；公开结论须能通过源码、可复现命令或对应提交的 CI 结果核对。
