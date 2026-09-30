# Agent F

<p align="center"><img src="assets/red-panda-256.png" width="180" alt="Franklin the Firefox, Agent F's mascot: a red panda in a fedora"></p>

Agent F lets an AI agent work in the Firefox you're already using: your running profiles, your tabs and your signed-in sessions. It works in the background while you keep browsing, and it tells the agent whenever you or the page change something between its steps.

It has three parts:
- a local MCP server (the broker) that your agent connects to;
- a small native-messaging helper that Firefox starts;
- a Firefox add-on that uses only standard WebExtension APIs.

It doesn't use Marionette or WebDriver, so Firefox needs no special flags and sites see an ordinary browser.

The design and its reasoning are in [SPEC.md](SPEC.md).

## What an agent can do with it

- Tabs and windows: list, open, close, focus and unload tabs. It keeps its own tabs in an "Agent F" tab group and works across several Firefox profiles at once.
- Reading: accessibility-style snapshots with element refs, `find`, pages as Markdown, `find_in_page`, screenshots (with numbered marks) and raw HTML.
- Acting: click, type, press keys, hover, choose options, scroll, drag, upload files and run scripts, all in background tabs and minimized windows.
- Page events: answer alerts and confirms by policy, and capture network requests and console messages.
- Browser data: search history and bookmarks, add bookmarks, list downloads, and reopen recently closed tabs.

Every result starts with what changed since the agent's previous call: tabs you opened or closed, pages that navigated, a tab you switched to.

## Install

You need Firefox 140 or later (any channel), Python 3.11 or later, and git. [uv](https://docs.astral.sh/uv/) makes the install faster but isn't required. The easiest way is to ask your agent to do it: "Clone github.com/drubino-mozilla/agent-f and run its installer with --addon."

To do it yourself:

1. Clone this repo somewhere permanent. The broker runs from the clone, so updating is a `git pull`.
2. Run the installer:
   - Windows: double-click `install\Install Agent F.cmd`, or run `py -3 install/install.py --addon`.
   - macOS: double-click `install/Install Agent F.command`, or run `python3 install/install.py --addon`.
   - Linux: run `python3 install/install.py --addon`.
3. Restart Firefox. Its menu button shows a notice that Agent F was added; open it and enable the add-on.
4. Restart your MCP client. The installer adds an `agent-f` entry to Cursor's `~/.cursor/mcp.json`. For other clients, copy that entry: the URL is `http://127.0.0.1:47470/mcp`, with an `Authorization: Bearer <token>` header, and the token is in the `token` file in the data folder.

`--addon` puts the signed add-on into the profile each Firefox installation starts with. Use `--profile NAME` (repeatable) for other profiles, and `--list-profiles` to see their names. You can also install the add-on from the [latest release](https://github.com/drubino-mozilla/agent-f/releases/latest) by opening the `.xpi` file in Firefox.

The add-on updates itself. To update the broker, `git pull` and run the installer again.

Windows is where Agent F is used every day. The macOS and Linux installers are new and not yet tried on real machines, so please report what breaks. On Linux, the Snap and Flatpak builds of Firefox may not be able to start the helper.

## Things to know

- Agent F has full control of your web sessions, so treat it like any tool with your passwords' reach. Everything binds to `127.0.0.1` and needs a secret token, and web pages can't reach the broker. Every call is recorded in `logs/audit.log` in the data folder.
- The toolbar button shows what agents are doing, and its Pause switch stops them at once.
- Firefox keeps extensions out of `about:` pages and a few Mozilla sites (addons.mozilla.org, the Mozilla accounts sites, support.mozilla.org). Clearing `extensions.webextensions.restrictedDomains` in `about:config` opens most of those, but never addons.mozilla.org.
- In private windows it runs only if you allow it in `about:addons`.
- The data folder is `%LOCALAPPDATA%\agent-f` on Windows, `~/Library/Application Support/agent-f` on macOS, and `~/.local/share/agent-f` on Linux. Logs are in its `logs` folder.
- To remove it, run the installer with `--uninstall` (add `--purge` to delete the data folder), then remove the add-on in `about:addons`.

## Development

- Restart the broker after changing its code: `install/install.py --restart`. The broker is installed in editable mode, so it runs from the clone.
- Throwaway profiles that run the add-on from this clone: `py -3 tools/dev_profile.py create dev1`, then `py -3 tools/dev_profile.py launch dev1 https://example.com/`. After changing the add-on, `py -3 tools/dev_profile.py restart dev1` reloads it; in a connected browser the `reload_extension` tool does the same. (`dev_profile.py` is Windows-only for now.)
- Fixture pages: `py -3 -m http.server 47480 --bind 127.0.0.1` and the same on 47481 (the cross-origin frame), both from `testpages/`.
- Try tools by hand: run `tools/mcp_call.py --file calls.json` with the venv's Python, where `calls.json` is a list such as `[["list_tabs", {}], ["snapshot", {"tab": 1}], ["click", {"ref": "@ref:Sign in"}]]`. `@ref:TEXT` picks the ref of the matching line in the previous result.
- Tests: `uv venv broker/.venv`, `uv pip install --python broker/.venv/Scripts/python.exe -e "broker[test]"`, then `broker/.venv/Scripts/python.exe -m pytest broker` (use `bin/python` outside Windows).
- Release: bump the version in `extension/manifest.json`, commit, and run `py -3 tools/release.py`. It builds the add-on, signs it on addons.mozilla.org, publishes a GitHub release and updates `docs/updates.json`, which installed copies check for updates. Signing needs an addons.mozilla.org API key in `.local/amo-credentials.json`.

## Layout

- `broker/`: the Python MCP broker (`agent_f`) and its tests.
- `host/`: the native-messaging helper that Firefox launches.
- `extension/`: the WebExtension.
- `install/`: the installer; `platforms.py` holds what differs between Windows, macOS and Linux.
- `tools/`: development and release helpers.
- `docs/`: the GitHub Pages site that serves the add-on's update manifest.

## Licence

MPL-2.0. See [LICENSE](LICENSE).
