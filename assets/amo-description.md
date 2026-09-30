Agent F lets an AI agent work in the Firefox you're already using: your running profiles, your tabs and your signed-in sessions. It works in background tabs while you keep browsing, and never takes focus.

It connects any MCP client (Cursor, Claude and others) to Firefox. The agent can:

- open, read and close tabs, keeping its own tabs in an "Agent F" tab group;
- read pages as accessibility-style snapshots, Markdown or screenshots;
- click, type, scroll, choose options, drag, upload files and run scripts, even in background tabs and minimized windows;
- answer alerts, and capture network requests and console messages;
- search history and bookmarks, and reopen recently closed tabs.

Every result tells the agent what changed since its last step: a tab you closed, a page that navigated, a tab you switched to.

**It needs a small local helper.** This add-on is one of three parts, with a local MCP server and a native-messaging helper. Install all three from GitHub with one command: https://github.com/drubino-mozilla/agent-f

**You stay in control.** Everything stays on your computer: the helper listens only on 127.0.0.1 and needs a secret token, and web pages can't reach it. The toolbar button shows what agents are doing and has a Pause switch, and every call is written to a local audit log. Agent F doesn't use Marionette or WebDriver, so sites see an ordinary Firefox.

The mascot is a red panda in a fedora; the red panda is the animal Firefox is named after. Agent F is Franklin the Firefox, a nod to Perry the Platypus, whose secret identity is Agent P.

Source code, documentation and issues: https://github.com/drubino-mozilla/agent-f (MPL-2.0).
