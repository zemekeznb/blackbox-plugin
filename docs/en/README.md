# Blackbox user guide

[English overview](../../README.en.md) · [中文首页](../../README.md) · [中文安装指南](../../release/INSTALL.md) · [中文使用与隐私](../../release/ALPHA-GUIDE.md)

Blackbox records local execution errors for **Codex desktop on Windows**. It lets you review saved commands, results, and conservatively linked follow-up calls by project and conversation, then export a Markdown snapshot. Recording requires explicit approval for each project.

The current version is **0.4.0-alpha.6.45**. The interface currently uses Chinese; this guide keeps the original button labels alongside English explanations.

## Install through Codex

You need Windows, an installed and running Codex desktop app, Python **3.11 or later** available to Codex as `python`, and access to GitHub. You do not need Git, a GitHub login, or a separately installed Codex CLI. Terminal CLI conversations are excluded from recording.

Send this message in a **Codex desktop conversation**:

```text
Please install and initialize Blackbox by following https://github.com/zemekeznb/blackbox-plugin/blob/v0.4.0-alpha.6.45/release/INSTALL-CODEX.md, then open its interface. Do not enable recording for any project yet, and do not change my model, account, or system environment configuration.
```

The linked execution instructions are in Chinese and are intended for Codex to follow. Codex checks prerequisites, downloads and verifies the fixed release package, registers the plugin, initializes its local runtime, and opens Blackbox. You do not need to download or extract the package yourself. Installation and opening the interface are reported separately; follow any specific instructions if a prerequisite is missing or a step is blocked.

If the plugin has not loaded yet, refresh or start a new conversation as directed. If a full reload is needed, save your work, fully quit and reopen Codex, then send:

```text
Continue Blackbox's initial setup and open its interface. Do not enable recording for any project yet.
```

Installation does not enable new projects or grant Hook trust. Updating preserves existing project settings.

## Connect your first project

1. In Blackbox, select **接入第一个项目** (“Connect your first project”).
2. Review the project and the data to be saved. Copy the prompt provided by the page into that project's Codex desktop conversation. Use this page-generated prompt as the project authorization entry point.
3. Codex helps complete the official Hook review, enables the selected project, and runs a harmless real command to check that its corresponding record arrives. If Codex needs to restart, follow the supplied steps to resume verification; a computer restart is not required.

The recording scope includes visible prompts, replies, tool calls, and their results from the authorized project, which may contain project content or sensitive information. Data stays local by default. Existing authorization does not need to be given again.

**Installation, project enablement, Hook trust, and receipt of a real record are separate states. A project is connected only after the corresponding real record has been received.**

## Review and export

Continue working normally in the connected project's Codex desktop conversations. To review records, send:

```text
Open Blackbox.
```

Select a project and conversation, filter by time or error type, and open an error record to inspect its saved command, result, and associated follow-up calls. The page identifies supported error formats; it does not determine root cause, responsibility, or whether a later call fixed the problem.

Review the full Markdown preview and source fields before exporting. The export is a snapshot of a fixed scope and is not uploaded automatically. Check it for sensitive information before sharing.

In the Codex side panel, **保存到下载文件夹** (“Save to Downloads”) writes the previewed and verified content to your Windows Downloads folder. Existing filenames receive a numbered suffix. The local browser reading page uses the browser's download mechanism. Use the full path in the success message; after a timeout, check the destination before saving again.

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
| `python` is missing or too old | Install a complete Python 3.11+ distribution with its PATH option enabled, then reopen Codex. |
| Codex cannot be located, or several installations are found | Open the intended Codex desktop app and ask Codex to check the ambiguity. Do not install a separate CLI or arbitrarily choose an old installation. |
| GitHub access or package verification fails | Check connectivity and send the exact error back to the installing conversation. Retry using the fixed-version instructions; do not skip verification. |
| The plugin source conflicts with an existing source | Ask Codex to inspect Blackbox's installation source. Do not remove the whole `personal` source if it contains other plugins. |
| Installation finishes but Blackbox tools are unavailable | Follow the reload steps above instead of repeatedly reinstalling. |
| Recording is enabled but no record arrives | Check official Hook trust, runtime configuration, and the actual command receipt; reload if needed. Successful initialization alone does not verify recording. |
| The page shows no errors | Check whether the command record arrived and whether its output matches a supported error format. An empty error list does not prove that execution succeeded. |

## Current limits and verification

- This Alpha targets **Codex desktop on Windows**. CLI product support is deferred. The minimum Codex version, a clean Windows computer/account, and API-key login mode have not completed dedicated validation.
- Blackbox cannot reconstruct events the host never delivered. Mixed output, silent failures, and some non-Bash/MCP failures can be missed. Printed diagnostic text can also produce false positives; inspect the actual execution evidence.
- Installation checks, offline rule tests, read-only metadata checks, and real desktop Hook delivery are separate evidence. Local user feedback does not replace independent-user, clean-machine, or long-term validation. See the [current verification status](../current-status.md) (Chinese) for version-specific evidence.
- Updates during a long-running conversation may leave gaps. When a full reload is requested, save your work, quit and reopen Codex, and verify a real record again. Missing events are not fabricated or backfilled.

## Documentation and feedback

| Link | Contents |
| --- | --- |
| [Chinese installation guide](../../release/INSTALL.md) | Installation, first project, troubleshooting, updates, and removal |
| [Chinese usage and privacy guide](../../release/ALPHA-GUIDE.md) | Local records, exports, backup, restore, and limitations |
| [Instructions for Codex](../../release/INSTALL-CODEX.md) (Chinese) | The installation procedure followed by the assistant |
| [Development and validation](../DEVELOPMENT.md) (Chinese) | Tests, packaging, CI, and release procedures |
| [Architecture](../architecture.md) (Chinese) | Data flow, storage, permissions, and component boundaries |
| [Current status](../current-status.md) · [Development plan](../development-plan.md) (Chinese) | Evidence, remaining validation, and planned work |
| [GitHub Issues](https://github.com/zemekeznb/blackbox-plugin/issues) | Bug reports and feedback |

When reporting a problem, include the version, steps, and a redacted error message. Distinguish installation failure, a page that will not open, and an enabled project with no received record. Do not upload the full database, private conversation text, keys, or account credentials.

## License

The current repository source is licensed under the [MIT License](../../LICENSE). Third-party materials retain their original licenses; the interface's Sora glyph attribution and SIL Open Font License are preserved in [review.css](../../plugins/blackbox/assets/review.css#L311).

Blackbox remains an Alpha release. This documentation and licensing update does not rebuild or replace the previously published `v0.4.0-alpha.6.45` package, tag, or release attachments.
