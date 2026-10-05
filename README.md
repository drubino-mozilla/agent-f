# Agent F

<p align="center"><img src="assets/red-panda-256.png" width="180" alt="Franklin the Firefox, Agent F's mascot: a red panda in a fedora"></p>

Agent F lets an AI agent work in the Firefox you're already using: your running profiles, your tabs and your signed-in sessions. It works in the background while you keep browsing, and it tells the agent whenever you or the page change something between its steps.

It has three parts:
- a local MCP server (the broker) that your agent connects to;
- a small native-messaging helper that Firefox starts;
- a Firefox add-on that uses only standard WebExtension APIs.

Firefox needs no special flags or restart, and sites see an ordinary browser.

The design and its reasoning are in [SPEC.md](SPEC.md).

## What an agent can do with it

- Tabs and windows: list, open, close, focus and unload tabs. It keeps its own tabs in an "Agent F" tab group and works across several Firefox profiles at once.
- Reading: accessibility-style snapshots with element refs, `find`, pages as Markdown, `find_in_page`, screenshots (with numbered marks) and raw HTML.
- Acting: click, type, press keys, hover, choose options, scroll, drag, upload files and run scripts, all in background tabs and minimized windows.
- Page events: answer alerts and confirms by policy, and capture network requests and console messages.
- Browser data: search history and bookmarks, add bookmarks, list downloads, and reopen recently closed tabs.

Every result starts with what changed since the agent's previous call: tabs you opened or closed, pages that navigated, a tab you switched to.

## Install

You need Firefox 140 or later, on any channel. Download the installer for your computer from the [landing page](https://drubino-mozilla.github.io/agent-f/) or the [latest release](https://github.com/drubino-mozilla/agent-f/releases/latest), and open it:

- Windows 10 or 11: `Agent-F-Windows.exe`. It installs for your Windows account only, without administrator rights.
- macOS 11 or later, Apple silicon or Intel: `Agent-F-macOS.pkg`. It installs into your home folder, without an administrator password.

Each installer carries its own Python, so there's nothing else to install. It sets up the broker and the helper, starts the broker at login, puts the signed add-on into the profile each Firefox installation starts with, and adds an `agent-f` entry to Cursor's `~/.cursor/mcp.json` if you have Cursor. Then:

1. Restart Firefox. Its menu button shows a notice that Agent F was added; open it and enable the add-on.
2. Restart your MCP client. For clients other than Cursor, add a server with the URL `http://127.0.0.1:47470/mcp` and an `Authorization: Bearer <token>` header. The token is in the `token` file in the data folder (see below).

The installers aren't signed by Microsoft or Apple yet, so the first time you open one, Windows shows "Windows protected your PC" (choose More info, then Run anyway), and macOS says it can't check the package (choose Done, then Open Anyway in System Settings > Privacy & Security).

To update, run the newest installer; it keeps your settings, and the add-on updates itself anyway. To remove Agent F, use Settings > Apps on Windows, or open Uninstall Agent F in the Applications folder in your home folder on a Mac. Either one asks whether to delete your settings and logs too. The add-on stays in Firefox until you remove it in `about:addons`.

Windows is where Agent F is used every day. The macOS installer is tested automatically on GitHub's Macs but not yet on anyone's own, so please report what breaks.

### From a clone (developers, and Linux)

You need Python 3.11 or later and git. [uv](https://docs.astral.sh/uv/) makes the install faster but isn't required. The easiest way is to ask your agent: "Clone github.com/drubino-mozilla/agent-f and run its installer with --addon."

1. Clone this repo somewhere permanent. The broker runs from the clone, so updating is a `git pull`.
2. Run the installer:
   - Windows: double-click `install\Install Agent F.cmd`, or run `py -3 install/install.py --addon`.
   - macOS: double-click `install/Install Agent F.command`, or run `python3 install/install.py --addon`.
   - Linux: run `python3 install/install.py --addon`.
3. Restart Firefox and your MCP client, as above.

`--addon` puts the signed add-on into the profile each Firefox installation starts with, unless it's already there. Use `--profile NAME` (repeatable) for other profiles, and `--list-profiles` to see their names. You can also install the add-on from the [latest release](https://github.com/drubino-mozilla/agent-f/releases/latest) by opening the `.xpi` file in Firefox.

A clone install and a packaged one share the data folder; whichever ran last is the one that runs. On Linux, the Snap and Flatpak builds of Firefox may not be able to start the helper.

## Things to know

- Agent F has full control of your web sessions, so treat it like any tool with your passwords' reach. Everything binds to `127.0.0.1` and needs a secret token, and web pages can't reach the broker. Every call is recorded in `logs/audit.log` in the data folder.
- The toolbar button shows what agents are doing, and its Pause switch stops them at once.
- Firefox keeps extensions out of `about:` pages and a few Mozilla sites (addons.mozilla.org, the Mozilla accounts sites, support.mozilla.org). Clearing `extensions.webextensions.restrictedDomains` in `about:config` opens most of those, but never addons.mozilla.org.
- In private windows it runs only if you allow it in `about:addons`.
- The data folder is `%LOCALAPPDATA%\agent-f` on Windows, `~/Library/Application Support/agent-f` on macOS, and `~/.local/share/agent-f` on Linux. Logs are in its `logs` folder.
- To remove a clone install, run `install/install.py --uninstall` (add `--purge` to delete the data folder), then remove the add-on in `about:addons`.

## Development

- Restart the broker after changing its code: `install/install.py --restart`. The broker is installed in editable mode, so it runs from the clone.
- Throwaway profiles that run the add-on from this clone: `py -3 tools/dev_profile.py create dev1`, then `py -3 tools/dev_profile.py launch dev1 https://example.com/`. After changing the add-on, `py -3 tools/dev_profile.py restart dev1` reloads it; in a connected browser the `reload_extension` tool does the same. (`dev_profile.py` is Windows-only for now.)
- Fixture pages: `py -3 -m http.server 47480 --bind 127.0.0.1` and the same on 47481 (the cross-origin frame), both from `testpages/`.
- Try tools by hand: run `tools/mcp_call.py --file calls.json` with the venv's Python, where `calls.json` is a list such as `[["list_tabs", {}], ["snapshot", {"tab": 1}], ["click", {"ref": "@ref:Sign in"}]]`. `@ref:TEXT` picks the ref of the matching line in the previous result.
- Tests: `uv venv broker/.venv`, `uv pip install --python broker/.venv/Scripts/python.exe -e "broker[test]"`, then `broker/.venv/Scripts/python.exe -m pytest broker` (use `bin/python` outside Windows).
- Release: bump the version in `extension/manifest.json`, commit, and run `py -3 tools/release.py`. It builds the add-on, signs it on addons.mozilla.org, publishes a GitHub release and updates `docs/updates.json`, which installed copies check for updates. Signing needs an addons.mozilla.org API key in `.local/amo-credentials.json`. Publishing the release starts the Installers workflow on GitHub Actions, which builds both installers around the signed add-on, tests them (install, a headless Firefox connecting, an update over a running Firefox, uninstall) and attaches them to the release a few minutes later.
- Installers: `tools/build_installer.py windows|macos --xpi <signed add-on>` builds one on its own platform, from the Python runtimes pinned in `packaging/runtimes.json` and the dependency locks `packaging/requirements-*.txt`. Regenerate the locks by running the Installers workflow by hand with "relock" and committing the files it attaches; `tools/lock_bundle.py` skips any release younger than seven days.

## Layout

- `broker/`: the Python MCP broker (`agent_f`) and its tests.
- `host/`: the native-messaging helper that Firefox launches.
- `extension/`: the WebExtension.
- `install/`: the installer script; `platforms.py` holds what differs between Windows, macOS and Linux. The packaged installers run it too.
- `packaging/`: the Windows (Inno Setup) and macOS (pkg) installers, their pinned runtimes and dependency locks, and their CI test.
- `tools/`: development and release helpers.
- `docs/`: the GitHub Pages site: the landing page and the add-on's update manifest.

## Licence

MPL-2.0. See [LICENSE](LICENSE).
