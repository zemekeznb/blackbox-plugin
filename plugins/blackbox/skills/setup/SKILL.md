---
name: blackbox-setup
description: Initialize Blackbox local runtime after Codex plugin installation, without enabling project recording.
---

# Blackbox first setup

Use only when the user requests Blackbox setup or invokes this onboarding workflow.
Resolve the installed plugin root from this skill's location (two directories above).
Do not use a source checkout or guess another installed version.

1. Locate an existing Python 3.11+ executable and verify it runs. Prefer a valid existing
   Blackbox host binding; otherwise use the host's runtime-location capability to locate
   an already provided Python, or another verified existing interpreter. A `python`
   Store alias or a missing PATH entry is not a reason to install another runtime.
   Run `"<absolute-python>" "<plugin-root>/scripts/blackbox_preflight.py" --plugin-root "<plugin-root>" --json`.
   Preserve and explain preflight warnings. If no usable interpreter exists or preflight
   fails, report the actual blocker. Never install Python, alter persistent or temporary
   PATH to make validation pass, change model/provider/credentials, or change execution policy.
2. After preflight, run `"<absolute-python>" "<plugin-root>/scripts/blackbox_install.py" --retire-legacy`.
   This verifies the installed Blackbox identity and retires only its legacy predecessor
   through the official CLI to prevent duplicate Hooks. On failure stop and show the
   error; do not enable recording. It preserves a disabled predecessor's setting.
3. Only after these checks succeed, use the preflight `python` and `state_directory` values:
   `"<python>" "<plugin-root>/scripts/blackbox.py" --state-dir "<state_directory>" configure-runtime --bind-host`.
   This stores the absolute interpreter and physical state directory in the current
   `CODEX_HOME/blackbox/runtime.json` for MCP, Hooks and later commands. Confirm the current
   desktop's CODEX_HOME; never select another account's directory. Check exit status and
   both `runtime_configured` and `host_bound` before reporting initialization success. Preserve history
   and project choices. Setup migrates the old default directory with a legacy path alias;
   custom state paths remain in place. If a reading service is active, ask the user to
   close that service and retry; do not move live data manually.
4. Verify the desktop host's actual `blackbox_reader` loading and native entry separately
   from runtime initialization. An independently launched MCP handshake, an installed/enabled
   flag, or opening an HTTP URL in Codex's browser does not prove the native sidebar works.
   Initializing runtime does not enable new projects or prove that records are arriving.
5. Explain: records stay on this computer by default; recording stores authorized visible
   tool inputs/results, which can contain project content or secrets; the reader shows a
   narrower set of recognizable execution errors. No anomaly is not proof of no failures.
   Current capture accepts only confirmed persistent desktop sessions in opted-in projects; terminal CLI sessions, including auxiliary installation terminals, are excluded. Existing history is not automatically deleted.
6. If the user wants recording, use the bundled `../blackbox-review/SKILL.md` project opt-in
   workflow. Ask for an explicit project and scope acceptance if absent. Never enable all
   projects, generate a failure, or read host conversation bodies just to finish setup.

After `codex plugin marketplace add` and `codex plugin add`, users can request
`blackbox-setup` in Codex. A setup button is optional: do not send users to download
archives or run PowerShell installers if it is absent. Complete initialization from
the actual installed cache using the steps above. When the user also asks to open
Blackbox, continue with `blackbox-review` after initialization; opening the page alone
does not authorize project recording. The graphical setup action still needs real-host
UI acceptance; do not claim otherwise.

The Codex-guided installation helper may already have configured runtime and returned
the verified installed_path. Use that actual cache and check the existing runtime;
do not reinstall or repeat a source switch as an onboarding step. `host_mcp: not_verified`
and `sidebar: not_verified` require separate desktop verification, even if `host_bound`
is true. Check status using the bound interpreter and the same state directory, check
Hook trust, then open Blackbox if requested. Current-chat plugin loading can require a
host reload; distinguish this from file installation and do not terminate the host.

## Failures and permissions

A missing host binding blocks native startup; initialize it through the setup steps
above. An invalid or unreadable binding must fail visibly, not trigger another data
directory or overwrite. Preserve existing state and report the command, exit
code, safe failure stage, exception type, and available errno/winerror. Do not expose
raw exception text containing paths, SQL, tokens or recorded content. If sandboxed
access is denied, use the host's normal per-operation permission mechanism only for
the necessary access. Do not disable the sandbox, change ACLs, or initialize a new
empty store to conceal the failure. If approval is unavailable or denied, report
that blocker and stop dependent steps.

If the previously bound interpreter is no longer available, explicitly verify another
existing interpreter and repair the binding to the same physical state directory through
setup. This is a repair action, not routine fallback. Do not change CODEX_HOME or select
a new empty store. MCP requires a usable binding before it can complete its handshake;
verify actual desktop loading after binding, refreshing or reloading as needed.

Clean-machine acceptance uses only the official Blackbox plugin, without Entry Probe
or Error Probe. Check native loading after a full desktop restart and opening from a
new conversation without temporary PATH or a manually substituted state directory.
Record these results separately; a test subprocess is not evidence of host loading.

## Hook trust and handoff

Check `"<absolute-python>" "<plugin-root>/scripts/blackbox_hooks.py"` before reporting initialization ready. Explain untrusted/disabled/missing/unknown separately; installation does not grant Hook trust. Follow the **Guided project onboarding** section of `../blackbox-review/SKILL.md` for official review assistance and existing consent, without enabling any project during setup alone. Never write trusted_hash, copy legacy trust or bypass review.

If the user requested full project onboarding, continue that workflow through a harmless real command and matching saved receipt; do not end at runtime initialization. If only setup was requested and binding succeeded, report **运行初始化完成，尚未接入项目**, plus the verified or unverified native-entry result and the next project-selection step. When a running desktop backend must be fully restarted, explain why and provide a continuation prompt; do not claim capture success or require a restart without evidence.
