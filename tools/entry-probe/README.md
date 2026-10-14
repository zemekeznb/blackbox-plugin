# NOTES-024 local entry probe

Independent static MCP App to test the host's global/thread entrypoints. It never imports the Blackbox product, reads its database, starts recording, uploads data, or invokes a model. Only the bundled HTML resource is readable. No network dependencies; Python standard library, JSON-lines stdio with a 64 KiB request ceiling. `python -I` uses the installed interpreter; plugin-relative cwd follows the local bundled Code Review plugin pattern.

Install with the normal CLI from this repository:

```powershell
codex plugin marketplace add './tools/entry-probe' --json
codex plugin add blackbox-entry-probe@blackbox-lab --json
```

The registered marketplace remains local. This does not update blackbox or its hooks. No secrets or runtime data belong here.

Validation:

```powershell
python -m unittest discover -s tests -p test_entry_probe.py -v
python tools/verify_entry_probe.py
```

The second command launches a short-lived official app-server, checks only this plugin's inventory and static UI resource, and terminates its own process. It does not create/resume a thread or start a turn. Inventory and resource success do **not** prove desktop entrypoint rendering or clicking. Actual UI must say host connection succeeded; its button verifies the local tool bridge separately.

Documented global entry deep link:

`codex://plugins/blackbox-entry-probe@blackbox-lab/app/open_probe`

This is a test entry, not the production record reader. If unused after testing, remove it through the normal plugin uninstall flow; removal is a separate explicit cleanup step. Source stays available for reproducibility. Do not patch the desktop app or enable private UI flags to make the probe appear.
