# Blackbox user guide

[English overview](../../README.en.md) · [中文首页](../../README.md) · [中文安装指南](../INSTALL.md) · [中文使用与隐私](../../release/ALPHA-GUIDE.md)

Blackbox records local execution errors for **Codex desktop on Windows**. It lets you review saved commands, results, and conservatively linked follow-up calls by project and conversation, then export a Markdown snapshot. Recording requires explicit approval for each project.

<!-- blackbox:published:start -->
The current version is **0.4.0-alpha.6.46**. The interface currently uses Chinese; this guide keeps the original button labels alongside English explanations.
<!-- blackbox:published:end -->

The installation entry below stays on the published version that has passed download verification; preparing a draft does not switch it.

## Install through Codex

You need Windows, an installed and running Codex desktop app, an existing Python **3.11 or later** runtime (including the runtime provided by Codex; no system PATH change is required), and access to GitHub. You do not need Git, a GitHub login, or a separately installed Codex CLI. Terminal CLI conversations are excluded from recording.

Send this message in a **Codex desktop conversation**:

<!-- blackbox:published:start -->
```text
Please install and initialize Blackbox by following https://github.com/zemekeznb/blackbox-plugin/blob/v0.4.0-alpha.6.46/release/INSTALL-CODEX.md, then open its interface. Do not enable recording for any project yet, and do not change my model, account, or system environment configuration.
```
<!-- blackbox:published:end -->

The linked execution instructions are in Chinese and are intended for Codex to follow. Codex checks prerequisites, downloads and verifies the fixed release package, registers the plugin, initializes its local runtime, and tries to open Blackbox. You do not need to download or extract the package yourself. Registration, runtime initialization, the native entry, and opening a local web page must be reported separately.

**Installation validation:** .46 includes the host runtime-binding fix and starts the backend with a verified absolute Python path, without relying on Python in PATH. Sidebar discovery, restart and new-conversation behavior still need validation on a clean computer. Opening a web page alone does not verify the native entry. Keep access-denied errors for diagnosis; do not repeatedly reinstall or switch data directories.

If the plugin has not loaded, ask Codex to inspect the official `blackbox@personal` plugin's `blackbox_reader` startup status and error. Refresh or start a new conversation if that is the remaining step. If a full reload is needed, save your work, fully quit and reopen Codex, then send:

```text
Continue Blackbox's initial setup and open its interface. Do not enable recording for any project yet.
```

Installation does not enable new projects or grant Hook trust. Updating preserves existing project settings.

## Connect a project

- **First project:** Follow the initial Blackbox screen and select **接入第一个项目** (“Connect your first project”).
- **Additional projects:** At the bottom left, open **采集与隐私** (“Recording and privacy”) → **使用与排查** (“Usage and troubleshooting”) → expand **如何接入新项目？** (“How do I connect a new project?”).

Review the project and the data to be saved. Copy the prompt provided by the relevant entry point into a **Codex desktop conversation in the target project**. This page-provided prompt is the project authorization entry point; each new project requires explicit setup.

Codex helps complete the official Hook review, enables the selected project, and runs a harmless real command to check that its corresponding record arrives. If Codex needs to restart, follow the supplied steps to resume verification; a computer restart is not required.

The recording scope includes visible prompts, replies, tool calls, and their results from the authorized project, which may contain project content or sensitive information. Data stays local by default. Existing authorization does not need to be given again.

**Installation, project enablement, Hook trust, and receipt of a real record are separate states. A project is connected only after the corresponding real record has been received.**

## Review and export

After the page service loads successfully, click **Blackbox** in the **Codex desktop sidebar** to open your records. Alternatively, send “Open Blackbox.” in a Codex desktop conversation. Opening the local web page in Codex's browser is a separate result and does not confirm that the native entry works.

Continue working normally in the connected project's Codex desktop conversations. To review records, select a project and conversation in Blackbox, filter by time or error type, and open an error record to inspect its saved command, result, and associated follow-up calls. The page identifies supported error formats; it does not determine root cause, responsibility, or whether a later call fixed the problem.

Review the full Markdown preview and source fields before exporting. The export is a snapshot of a fixed scope and is not uploaded automatically. Check it for sensitive information before sharing.

On the Blackbox page within Codex, **保存到下载文件夹** (“Save to Downloads”) writes the previewed and verified content to your Windows Downloads folder. Existing filenames receive a numbered suffix. The local browser reading page uses the browser's download mechanism. Use the full path in the success message; after a timeout, check the destination before saving again.

Records that the official host lookup explicitly reports as unlinked are hidden from everyday navigation. This does not automatically delete their stored history, mean they were archived, or establish that they came from a subagent. A temporary name lookup failure is not a reason to delete a record.

## Stop recording, back up, and restore

Disable a selected project in the recording settings, or ask Codex to use `blackbox-review` to do so. Its history remains available. Closing the page or stopping the reading service does **not** disable recording.

You can ask Codex to use `blackbox-review` to create a backup in a new file outside all registered projects and Git repositories. A backup can contain sensitive information; it is not automatically encrypted or uploaded. The database snapshot does not include runtime commands, service tokens, or reading preferences.

Restore writes only to a new directory. All restored projects start disabled; restoring does not overwrite the active database or automatically resume recording.

Before clearing records, review the exact scope and the deletion preview. If you want to reset a test installation, first specify the cleanup scope, disable recording, stop the reading service, and verify a backup. Ordinary installation, updates, and uninstallation do not automatically remove history. Deleting records does not delete existing backups or exported files.

## Update or uninstall

- **Update:** Save your work and ask Blackbox in Codex to stop the reading service. Then send the installation prompt from the newer release's README. Existing history, reading preferences, and project settings are preserved. Follow any reload instructions; an ongoing long conversation is not guaranteed to survive a plugin update without recording gaps.
- **Uninstall:** Optionally ask `blackbox-review` to create a backup outside the repository, then remove Blackbox through Codex plugin management. Normal uninstallation preserves local history and does not remove Python, Codex, or other plugins.

## Data and privacy

Recording applies only to explicitly enabled projects and host-confirmed persistent desktop conversations. Terminal CLI conversations, including installation helper terminals, are excluded. A normal desktop conversation can still be recorded when unnamed or archived. Temporary, internal, and unconfirmed conversations do not have their bodies saved.

**The saved data includes more than errors:** authorized visible prompts, replies, tool calls, and results may be stored. The error page shows only recognized errors and conservatively associated follow-up calls. Blackbox does not automatically upload records, send them elsewhere, invoke additional models, or enable other projects. Limited redaction cannot guarantee removal of passwords, keys, or business information.

The default state directory is `%LOCALAPPDATA%\Blackbox`. Windows packaged apps may redirect that location; use the actual path reported by Blackbox's status output. Older data and configuration remain compatible.

Release .45 rechecks old conversation identity caches. Events whose identity cannot be confirmed during that check are not saved or replayed later. Desktop identities already verified under the new policy retain protection against temporary metadata transport failures. Updating does not automatically delete old records.

## Troubleshooting

| Symptom | Next step |
| --- | --- |
| `python` is missing, opens a Store prompt, or is too old | Ask Codex to distinguish the conversation's interpreter from the desktop backend's environment. A temporary PATH change does not repair .45's native entry. Do not automatically install Python or change the system PATH. |
| Codex cannot be located, or several installations are found | Open the intended Codex desktop app and ask Codex to check the ambiguity. Do not install a separate CLI or arbitrarily choose an old installation. |
| GitHub access or package verification fails | Check connectivity and send the exact error back to the installing conversation. Retry using the fixed-version instructions; do not skip verification. |
| The plugin source conflicts with an existing source | Ask Codex to inspect Blackbox's installation source. Do not remove the whole `personal` source if it contains other plugins. |
| Installation finishes but the sidebar entry or tools are unavailable | Check the actual MCP startup error as described above. A working local web page does not prove native loading; Probe plugins are not required. |
| `OSError`, access denied, or failure in another conversation | Keep the command, exit code, failure stage and available `errno` / `winerror`; compare the interpreter and state directory. Do not clear data, switch directories or disable the sandbox to conceal the failure. |
| Recording is enabled but no record arrives | Check official Hook trust, runtime configuration, and the actual command receipt; reload if needed. Successful initialization alone does not verify recording. |
| The page shows no errors | Check whether the command record arrived and whether its output matches a supported error format. An empty error list does not prove that execution succeeded. |

## Current limits and verification

- This Alpha targets **Codex desktop on Windows**. Codex CLI, Linux/macOS, and other Agent hosts have not completed product adaptation. Clean-machine testing has exposed desktop Python startup and conversation access failures; the full installation experience has not passed validation. The minimum Codex version and API-key login mode remain unverified. Fixes require the same candidate package to pass restart, new-conversation and real-record checks without Probe plugins.
- Blackbox cannot reconstruct events the host never delivered. Mixed output, silent failures, and some non-Bash/MCP failures can be missed. Printed diagnostic text can also produce false positives; inspect the actual execution evidence.
- Installation checks, offline rule tests, read-only metadata checks, and real desktop Hook delivery are separate evidence. Local user feedback does not replace independent-user, clean-machine, or long-term validation. Automated source checks are available in [GitHub Actions](https://github.com/zemekeznb/blackbox-plugin/actions/workflows/alpha.yml) for the corresponding commit; passing CI does not verify recording in your environment.
- Updates during a long-running conversation may leave gaps. When a full reload is requested, save your work, quit and reopen Codex, and verify a real record again. Missing events are not fabricated or backfilled.

## Documentation and feedback

<!-- blackbox:published:start -->
| Link | Contents |
| --- | --- |
| [Chinese installation guide](../INSTALL.md) | Installation, project setup, troubleshooting, updates, and removal |
| [Chinese usage and privacy guide](../../release/ALPHA-GUIDE.md) | Local records, exports, backup, restore, and limitations |
| [Instructions for Codex](https://github.com/zemekeznb/blackbox-plugin/blob/v0.4.0-alpha.6.46/release/INSTALL-CODEX.md) (Chinese) | The installation procedure followed by the assistant |
| [Development and validation](../DEVELOPMENT.md) (Chinese) | Tests, CI, release drafts, and installation entry updates after approved publication |
| [Architecture](../architecture.md) (Chinese) | Data flow, storage, permissions, and component boundaries |
| [GitHub Issues](https://github.com/zemekeznb/blackbox-plugin/issues) | Bug reports and feedback |
<!-- blackbox:published:end -->

When reporting a problem, include the version, steps, and a redacted error message. Distinguish installation failure, a page that will not open, and an enabled project with no received record. Do not upload the full database, private conversation text, keys, or account credentials.

## License

The current repository source is licensed under the [MIT License](../../LICENSE). Third-party materials retain their original licenses; the interface's Sora glyph attribution and SIL Open Font License are preserved in [review.css](../../plugins/blackbox/assets/review.css#L311).

Blackbox remains an Alpha release. The published `v0.4.0-alpha.6.45` installation package and its checksum are preserved. Repository documentation and history cleanup does not rebuild that package or change an already installed plugin; the release manifest retains its original build provenance. See [Development and validation](../DEVELOPMENT.md) for the distinction between published artifacts and cleaned source history.
