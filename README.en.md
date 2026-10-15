<p align="center">
  <img src="docs/images/blackbox-mark.svg" width="72" height="72" alt="Blackbox">
</p>

<h1 align="center">Blackbox Plugin</h1>

<p align="center">A local execution error notebook for agents</p>

<!-- blackbox:published:start -->
<p align="center">
  <a href="https://github.com/zemekeznb/blackbox-plugin/releases/tag/v0.4.0-alpha.6.45"><img src="https://img.shields.io/badge/version-0.4.0--alpha.6.45-b6532f?style=flat-square" alt="Version 0.4.0-alpha.6.45"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-10b981?style=flat-square" alt="MIT license"></a>
  <a href="docs/en/README.md"><img src="https://img.shields.io/badge/platform-Windows-0078d4?style=flat-square" alt="Windows"></a>
  <a href="docs/en/README.md"><img src="https://img.shields.io/badge/python-3.11%2B-3776ab?style=flat-square&logo=python&logoColor=white" alt="Python 3.11+"></a>
</p>
<!-- blackbox:published:end -->

<p align="center">
  <a href="README.md">简体中文</a> ·
  <strong>English</strong> ·
  <a href="docs/en/README.md">User guide</a> ·
  <a href="docs/DEVELOPMENT.md">Development (中文)</a> ·
  <a href="https://github.com/zemekeznb/blackbox-plugin/releases">Releases</a>
</p>

Review execution errors, saved command results and follow-up calls by project and session. Export Markdown snapshots with their source evidence. Records stay on your computer by default, and recording starts only for projects you explicitly enable.

![Blackbox overview: projects, sessions, execution errors, commands and saved results](docs/images/overview.png)

*Current interface screenshots, with the host app border cropped and local usernames and paths obscured.*

## Quick start

<!-- blackbox:published:start -->
The current version is **0.4.0-alpha.6.45**. You need **Windows, the Codex desktop app, Python 3.11+ available to Codex as `python`**, and access to GitHub. Git, a GitHub login and a separate Codex CLI installation are not required.
<!-- blackbox:published:end -->

This entry point stays on the published version that has passed download verification.

**Let Codex install it.** Open a conversation in the Codex desktop app and send:

<!-- blackbox:published:start -->
```text
Please install and initialize Blackbox by following https://github.com/zemekeznb/blackbox-plugin/blob/v0.4.0-alpha.6.45/release/INSTALL-CODEX.md, then open its interface. Do not enable recording for any project yet, and do not change my model, account, or system environment configuration.
```
<!-- blackbox:published:end -->

Codex checks dependencies, downloads and verifies the plugin, installs it, initializes the local runtime and opens Blackbox. **Installation does not enable project recording.** If the plugin has not loaded, follow the instructions to start a new conversation or, when needed, save your work and fully restart Codex. See the [user guide](docs/en/README.md) for troubleshooting. The installation instructions read by Codex and the current interface are in Chinese.

## What it helps with

| Capability | Purpose |
| --- | --- |
| Browse by project and session | Find execution errors in received events that Blackbox can recognize |
| Compare commands and results | Inspect saved sources instead of reconstructing a failure from memory |
| Follow-up call links | See conservatively linked calls with identical input; no automatic claim that an issue was fixed |
| Filters and Markdown export | Narrow records by time or type and save a snapshot you can choose to share |
| Local management | Enable or disable recording per project; back up, restore and clean up records |

## Review and export

After installation and initialization, find and click **Blackbox** in the **Codex desktop sidebar** to open your records.

Choose a project, session and error to inspect saved commands, results and associated follow-up calls. Filter records and export a Markdown snapshot when needed.

Alternatively, send “Open Blackbox.” in a Codex desktop conversation and ask Codex to open it for you.

![Blackbox export preview: review the full Markdown content before saving to Downloads](docs/images/export-preview.png)

## Connect a project

- **First project:** Follow the initial Blackbox screen and click **接入第一个项目** (Connect your first project).
- **Additional projects:** At the bottom left, open **采集与隐私** (Recording and privacy) → **使用与排查** (Usage and troubleshooting) → expand **如何接入新项目？** (How do I connect a new project?) to find the setup prompt.

Read the recording scope in the prompt, then copy and send it to a **Codex desktop conversation in the target project**. Codex sets up the project, helps with the official Hook trust review and runs a harmless real command to check that its record arrives. Follow any restart and follow-up instructions.

**Project setup is complete only when the corresponding real record is received.** Each new project requires explicit setup; then continue working in that project as usual.

![Blackbox project setup: Recording and privacy, Usage and troubleshooting, and the new-project prompt](docs/images/connect-project.png)

## Data and supported scope

- Records stay local by default. Blackbox does not automatically upload them, require a Blackbox account or make additional model calls.
- Recording includes authorized visible inputs, replies, tool calls and results, **not just errors**. Limited redaction cannot guarantee that all sensitive information is removed.
- This release targets the **Codex desktop app on Windows**. Terminal CLI sessions, including auxiliary terminals opened during installation, are excluded. CLI support is deferred.
- Events the host does not deliver cannot be recovered. An empty error list does not prove that execution succeeded. Blackbox does not infer root causes or assign responsibility.
- Closing the page does not disable recording. Updates and ordinary uninstalls preserve existing data. See the [user guide](docs/en/README.md) for controls and limitations.

## Documentation

| Document | Contents |
| --- | --- |
| [English guide](docs/en/README.md) | Installation, project setup, usage, privacy, troubleshooting and uninstalling |
| [安装指南 / Installation](docs/INSTALL.md) (中文) | Conversational installation, project setup, updates and troubleshooting |
| [使用与隐私 / Usage and privacy](release/ALPHA-GUIDE.md) (中文) | Recording scope, backups, exports and limitations |
| [Development and releases](docs/DEVELOPMENT.md) (中文) | Local tests, CI, release drafts and installation entry updates after approved publication |
| [Architecture and boundaries](docs/architecture.md) (中文) | Session admission, storage, error recognition and data management |
| [Releases](https://github.com/zemekeznb/blackbox-plugin/releases) · [Issues](https://github.com/zemekeznb/blackbox-plugin/issues) | Published packages, updates and issue tracking |

## Feedback

Report problems in [Issues](https://github.com/zemekeznb/blackbox-plugin/issues) with the version, steps to reproduce and a redacted error message. Do not attach a complete database, conversation contents, secrets or account credentials.

## License

This project is licensed under the [MIT License](LICENSE). Third-party materials retain their original licenses; the Sora glyph attribution and SIL Open Font License used by the interface are preserved in [review.css](plugins/blackbox/assets/review.css#L311).
