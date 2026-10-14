---
name: blackbox-setup
description: Initialize Blackbox local runtime after Codex plugin installation, without enabling project recording.
---

# Blackbox first setup

Use only when the user requests Blackbox setup or invokes this onboarding workflow.
Resolve the installed plugin root from this skill's location (two directories above).
Do not use a source checkout or guess another installed version.

1. Run `python "<plugin-root>/scripts/blackbox_preflight.py" --plugin-root "<plugin-root>" --json`.
   If Python is missing or fails preflight, show the reported dependency and ask the user
   to install Python 3.11+ with PATH enabled, then reopen Codex. Never install Python,
   change model/provider/credential configuration, or change execution policy.
2. After preflight, run `python "<plugin-root>/scripts/blackbox_install.py" --retire-legacy`.
   This verifies the installed Blackbox identity and retires only its legacy predecessor
   through the official CLI to prevent duplicate Hooks. On failure stop and show the
   error; do not enable recording. It preserves a disabled predecessor's setting.
3. Only after these checks succeed, use the preflight `python` and `state_directory` values:
   `"<python>" "<plugin-root>/scripts/blackbox.py" --state-dir "<state_directory>" configure-runtime`.
   Check exit status and `runtime_configured` before reporting success. Preserve history
   and project choices. Setup migrates the old default directory with a legacy path alias;
   custom state paths remain in place. If a reading service is active, ask the user to
   close that service and retry; do not move live data manually.
4. Report runtime initialization separately from host MCP/Hook verification. Initializing
   runtime does not enable any new projects and does not prove that records are arriving.
5. Explain: records stay on this computer by default; recording stores authorized visible
   tool inputs/results, which can contain project content or secrets; the reader shows a
   narrower set of recognizable execution errors. No anomaly is not proof of no failures.
   Current capture accepts only confirmed persistent user sessions in opted-in projects.
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
do not reinstall or repeat a source switch as an onboarding step. Check status and
Hook trust, then open Blackbox if requested. Current-chat plugin loading can require a
host reload; distinguish this from file installation and do not terminate the host.

## Hook trust and handoff

Check `python "<plugin-root>/scripts/blackbox_hooks.py"` before reporting initialization ready. Explain untrusted/disabled/missing/unknown separately; installation does not grant Hook trust. Follow the **Guided project onboarding** section of `../blackbox-review/SKILL.md` for official review assistance and existing consent, without enabling any project during setup alone. Never write trusted_hash, copy legacy trust or bypass review.

If the user requested full project onboarding, continue that workflow through a harmless real command and matching saved receipt; do not end at runtime initialization. If only setup was requested, report **初始化完成，尚未接入项目** and give the next project-selection step. When a running desktop backend must be fully restarted, explain why and provide a continuation prompt; do not claim capture success or require a restart without evidence.
