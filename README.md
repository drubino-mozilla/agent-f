# Agent F

Agent F lets an AI agent work in the Firefox you're already using: your running profiles, your tabs and your signed-in sessions. It works in the background while you keep browsing, and it tells the agent whenever you or the page change something between its steps. The pieces are an always-on local MCP broker, a native-messaging helper, and a WebExtension that uses only standard APIs.

The design is in [SPEC.md](SPEC.md).

## Status

Milestones M0 (plumbing), M1 (everyday browsing), M2 (dialogs, network and console capture, file uploads, drag, browser data, the toolbar panel with a pause switch, and an audit log) and M3 (minimized windows, unloaded tabs, and the switch-over from `firefox-browser-mcp`) are done. Agent F is in daily use in the author's Nightly. Several Firefox profiles can be connected at once, and every result reports what changed since the agent's previous call.

The tools cover these areas:
- tabs and windows;
- navigation;
- accessibility-style snapshots with refs, and `find`;
- reading pages as Markdown, and `find_in_page`;
- screenshots, including numbered marks;
- clicking, typing, keys, hover, selects and scrolling;
- `wait_for`, `eval_page` and `get_html`.

Windows only for now.

## Layout

- `broker/`: the Python MCP broker (`agent_f`) and its tests.
- `host/`: the native-messaging helper that Firefox launches.
- `extension/`: the WebExtension.
- `install/`: the Windows installer.
- `tools/`: development helpers, such as throwaway Firefox profiles that load the extension from this clone.

## Development

These are normally run by an agent on your behalf.

- Install or update everything: `py -3 install/install.py`. It sets up `%LOCALAPPDATA%\agent-f`, registers the helper with Firefox, starts the broker at logon, and adds `agent-f` to `~/.cursor/mcp.json`.
- Restart the broker after changing its code: `py -3 install/install.py --restart`.
- Create and launch a development profile: `py -3 tools/dev_profile.py create dev1`, then `py -3 tools/dev_profile.py launch dev1 https://example.com/`. After changing the extension, `py -3 tools/dev_profile.py restart dev1` reloads it.
- Serve the fixture pages: `py -3 -m http.server 47480 --bind 127.0.0.1` and the same on 47481 (the cross-origin frame), both from `testpages/`.
- Try tools by hand: `%LOCALAPPDATA%\agent-f\venv\Scripts\python.exe tools/mcp_call.py --file calls.json`, where `calls.json` is a list such as `[["list_tabs", {}], ["snapshot", {"tab": 1}], ["click", {"ref": "@ref:Sign in"}]]`. `@ref:TEXT` picks the ref of the matching line in the previous result.
- Run the tests: create `broker/.venv` with `uv venv broker/.venv` and `uv pip install --python broker/.venv/Scripts/python.exe -e "broker[test]"`, then run `broker/.venv/Scripts/python.exe -m pytest broker`.
- Logs: `%LOCALAPPDATA%\agent-f\logs\broker.log` and `helper.log`.
- Remove everything: `py -3 install/install.py --uninstall`, adding `--purge` to delete the data folder too.

## Licence

MPL-2.0. See [LICENSE](LICENSE).
