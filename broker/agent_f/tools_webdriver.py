"""What Agent F does through Firefox's own WebDriver BiDi server, when the user has turned on remote control:
trusted input, Firefox's own windows and preferences, and pages extensions are kept out of."""

import asyncio
import json

from mcp.server.fastmcp import Context

from . import envelope, webdriver
from .bridge import BrowserError
from .toolkit import AgentFError, Call, Target, Toolkit

UNREACHABLE_HINT = webdriver.UNREACHABLE_HINT

FIND_ELEMENT = """(() => {
  const el = document.querySelector(%(selector)s);
  if (!el) return JSON.stringify({ missing: true });
  el.scrollIntoView({ block: "center", inline: "center", behavior: "instant" });
  if (%(focus)s) {
    el.focus();
    if (%(clear)s) {
      if (typeof el.select === "function") el.select();
      else if (el.isContentEditable) getSelection().selectAllChildren(el);
    }
  }
  const r = el.getBoundingClientRect();
  const name = (el.getAttribute("aria-label") || el.innerText || el.value || el.placeholder || "").trim().replace(/\\s+/g, " ");
  return JSON.stringify({ x: r.left + r.width / 2, y: r.top + r.height / 2, empty: !r.width && !r.height,
                          target: el.localName + (name ? ` "${name.slice(0, 60)}"` : "") });
})()"""

READ_TEXT = """JSON.stringify({ title: document.title, text: (document.body || document.documentElement).innerText })"""


def build_actions(action: str, prep: dict, full: dict) -> list[dict]:
    if action == "click":
        return webdriver.click_actions(prep["x"], prep["y"], full.get("button") or "left", full.get("count") or 1,
                                       full.get("modifiers"))
    if action == "hover":
        return webdriver.hover_actions(prep["x"], prep["y"])
    if action == "type":
        return webdriver.text_actions(full.get("text") or "", bool(full.get("submit")))
    return webdriver.key_actions(full.get("keys") or "")


def trusted(kit: Toolkit, action: str):
    """A perform step for kit.run that sends the input itself through WebDriver BiDi.

    The page script finds the point and watches what happens (trusted_begin); WebDriver sends the input to
    the matching document; trusted_end settles and reports, as for any other action.
    """
    async def perform(session: Call, target: Target, full: dict):
        try:
            prep, header, _ = await kit.call(session, target, "trusted_begin", {**full, "action": action})
        except BrowserError as err:
            if err.code != "unreachable_page":
                raise
            return await _unreachable_action(kit, session, target, action, full, err)
        try:
            wd, context = await webdriver.open_session({"url": prep.get("url"), "timeOrigin": prep.get("timeOrigin")})
            try:
                await webdriver.perform(wd, context, build_actions(action, prep, full))
            finally:
                await wd.end()
        except BaseException:
            try:
                await kit.call(session, target, "trusted_end", {"tab": target.tab, "token": prep["token"], "abort": True},
                               timeout=10)
            except (BrowserError, AgentFError):
                pass
            raise
        settle = (full.get("settle_ms") or 3000) / 1000
        return await kit.call(session, target, "trusted_end",
                              {"tab": target.tab, "token": prep["token"], "target": prep.get("target")},
                              timeout=settle + 30)
    return perform


async def tab_url(kit: Toolkit, session: Call, target: Target) -> tuple[str, str]:
    info, header, _ = await kit.call(session, target, "tab_info", {"tab": target.tab})
    return info.get("url") or "", header


async def _unreachable_action(kit: Toolkit, session: Call, target: Target, action: str, full: dict,
                              err: BrowserError):
    if not full.get("selector") or full.get("node") is not None:
        raise AgentFError("unreachable_page", err.message + UNREACHABLE_HINT)
    url, header = await tab_url(kit, session, target)
    wd, context = await webdriver.open_session({"url": url, "timeOrigin": None})
    effects = []
    try:
        value = None
        source = FIND_ELEMENT % {"selector": json.dumps(full["selector"]),
                                 "focus": json.dumps(action in ("type", "press_key")),
                                 "clear": json.dumps(action == "type" and full.get("clear", True) is not False)}
        prep = json.loads(await wd.evaluate(context, source))
        if prep.get("missing"):
            raise AgentFError("no_match", f"Nothing on the page matches {full['selector']!r}.")
        if prep.get("empty") and action in ("click", "hover"):
            raise AgentFError("not_visible", f"The {prep['target']} has no size on the page.")
        await webdriver.perform(wd, context, build_actions(action, prep, full))
        await asyncio.sleep(min(2.0, (full.get("settle_ms") or 3000) / 1000))
        contexts = {c["context"]: c for c in await wd.top_level_contexts()}
        after = contexts.get(context, {}).get("url")
        if after is None:
            effects.append("The tab closed.")
        elif after != url:
            effects.append(f"The tab navigated from {url} to {after}.")
        elif action == "type":
            value = await wd.evaluate(context, "document.activeElement && 'value' in document.activeElement "
                                               "? document.activeElement.value : null")
    finally:
        await wd.end()
    result = {"target": f"{prep.get('target') or 'the page'} (through WebDriver)"}
    if action == "type":
        result["value"] = value
    if action == "press_key":
        result["keys"] = [f"{k}: sent" for k in (full.get("keys") or "").split()]
    return result, header, effects


def unreachable_fallback(kit: Toolkit, command: str, fallback):
    """A perform step that runs command as usual and, on a page extensions can't reach, falls back to WebDriver."""
    async def perform(session: Call, target: Target, full: dict):
        try:
            return await kit.call(session, target, command, full, timeout=(full.get("timeout") or 30) + 10)
        except BrowserError as err:
            if err.code != "unreachable_page":
                raise
            url, header = await tab_url(kit, session, target)
            try:
                wd, context = await webdriver.open_session({"url": url, "timeOrigin": None})
            except webdriver.WebDriverError as wd_err:
                if wd_err.code == "remote_control_off":
                    raise AgentFError("unreachable_page", err.message + UNREACHABLE_HINT) from wd_err
                raise
            try:
                result = await fallback(wd, context, full)
            finally:
                await wd.end()
            return result, header, ["Extensions can't reach this page, so Agent F used Firefox's WebDriver server."]
    return perform


async def read_fallback(wd: webdriver.Session, context: str, full: dict) -> dict:
    data = json.loads(await wd.evaluate(context, READ_TEXT))
    text = data.get("text") or ""
    limit = full.get("max_chars") or 20000
    return {"title": data.get("title"), "text": text[:limit], "total": len(text), "source": "page"}


async def eval_fallback(wd: webdriver.Session, context: str, full: dict) -> dict:
    value = await webdriver.run_code(wd, context, full.get("code") or "", timeout=full.get("timeout") or 10)
    return {"value": value[:20000], "total": len(value)}


async def screenshot_fallback(wd: webdriver.Session, context: str, full: dict) -> dict:
    if full.get("node") is not None or full.get("selector"):
        raise AgentFError("unreachable_page", "On pages extensions can't reach, screenshot captures the view or "
                                              "the whole page, not single elements.")
    png = full.get("format") == "png"
    result = await wd.command("browsingContext.captureScreenshot", {
        "context": context, "origin": "document" if full.get("full_page") else "viewport",
        "format": {"type": "image/png"} if png else {"type": "image/jpeg", "quality": 0.8}}, timeout=40)
    return {"image": result.get("data"), "mime": "image/png" if png else "image/jpeg"}


def register(tool, kit: Toolkit) -> None:

    async def browser_session(session: Call, browser: str | None) -> tuple[webdriver.Session, str]:
        conn = kit.resolve_browser(session, browser)
        identity = None
        if len(webdriver.find_endpoints()) > 1:
            try:
                identity = await conn.request("page_identity", {}, call=session.id)
            except BrowserError:
                pass
        wd, _ = await webdriver.open_session(identity if identity and identity.get("url") else None)
        return wd, envelope.browser_header(conn.label)

    async def run_chrome(ctx: Context, since: str | None, browser: str | None, code: str, timeout: float,
                         render) -> str:
        session = kit.session_for(ctx, since)
        header = None
        try:
            wd, header = await browser_session(session, browser)
            try:
                value = await webdriver.eval_chrome(wd, code, timeout)
            finally:
                await wd.end()
            return kit.finish(session, render(value), header=header)
        except (AgentFError, BrowserError, webdriver.WebDriverError) as err:
            raise kit.fail(session, err, header)

    @tool()
    async def eval_browser(ctx: Context, code: str, timeout: float = 10, browser: str | None = None,
                           since: str | None = None) -> str:
        """Run JavaScript in Firefox's own browser window, with full privileges, through Firefox's WebDriver server.

        For what pages and extensions can't reach: the toolbar, menus and panels, about: pages, Services.* and
        the browser's internals. `window`, `document` and `gBrowser` are the window the user used last. Pass an
        expression, or statements (the last expression's value is returned); promises are awaited. Needs
        Firefox's remote control on, and Firefox started with MOZ_REMOTE_ALLOW_SYSTEM_ACCESS=1. Ask the user
        before changing anything they would notice.

        Args:
            code: An expression, or statements.
            timeout: Seconds to wait for the result.
            browser: Browser label. Needed only when several browsers are connected.
            since: The since token from the end of your previous Agent F result.
        """
        def render(value: str) -> str:
            shown = value[:20000]
            body = "Returned:\n" + envelope.page_content(shown)
            if len(value) > len(shown):
                body += f"\n(Showing {len(shown)} of {len(value)} characters.)"
            return body
        return await run_chrome(ctx, since, browser, code, timeout, render)

    @tool()
    async def get_prefs(ctx: Context, names: list[str], browser: str | None = None, since: str | None = None) -> str:
        """Read Firefox preferences (about:config), through Firefox's WebDriver server.

        Args:
            names: Preference names, such as ["browser.startup.page"]. A name ending in "." lists every
                preference under it (up to 200).
            browser: Browser label. Needed only when several browsers are connected.
            since: The since token from the end of your previous Agent F result.
        """
        code = f"""
const out = [];
const read = name => {{
  const type = Services.prefs.getPrefType(name);
  if (type === Services.prefs.PREF_INVALID) return {{ name, missing: true }};
  const value = type === Services.prefs.PREF_BOOL ? Services.prefs.getBoolPref(name)
    : type === Services.prefs.PREF_INT ? Services.prefs.getIntPref(name) : Services.prefs.getStringPref(name);
  return {{ name, value, changed: Services.prefs.prefHasUserValue(name), locked: Services.prefs.prefIsLocked(name) }};
}};
for (const name of {json.dumps(names)}) {{
  if (name.endsWith(".")) {{
    const children = Services.prefs.getChildList(name).sort();
    for (const child of children.slice(0, 200)) out.push(read(child));
    if (children.length > 200) out.push({{ name, more: children.length - 200 }});
  }} else {{
    out.push(read(name));
  }}
}}
out"""

        def render(value: str) -> str:
            lines = []
            for p in json.loads(value):
                if p.get("missing"):
                    lines.append(f"{p['name']}: (not set)")
                elif p.get("more"):
                    lines.append(f"... and {p['more']} more under {p['name']}")
                else:
                    flags = (" [changed by the user]" if p.get("changed") else "") + (" [locked]" if p.get("locked") else "")
                    lines.append(f"{p['name']} = {json.dumps(p['value'])}{flags}")
            return "\n".join(lines) or "No preferences."
        return await run_chrome(ctx, since, browser, code, 10, render)

    @tool()
    async def set_prefs(ctx: Context, prefs: dict[str, str | int | bool | None], browser: str | None = None,
                        since: str | None = None) -> str:
        """Change Firefox preferences (about:config), through Firefox's WebDriver server. Ask the user first.

        Args:
            prefs: Names mapped to new values. A value of null resets the preference to its default.
            browser: Browser label. Needed only when several browsers are connected.
            since: The since token from the end of your previous Agent F result.
        """
        code = f"""
const out = [];
for (const [name, value] of Object.entries({json.dumps(prefs)})) {{
  const type = Services.prefs.getPrefType(name);
  try {{
    if (value === null) {{
      Services.prefs.clearUserPref(name);
      out.push(`${{name}}: reset to its default`);
      continue;
    }}
    if (typeof value === "boolean" && type !== Services.prefs.PREF_INT && type !== Services.prefs.PREF_STRING) {{
      Services.prefs.setBoolPref(name, value);
    }} else if (typeof value === "number" && Number.isInteger(value) && type !== Services.prefs.PREF_BOOL && type !== Services.prefs.PREF_STRING) {{
      Services.prefs.setIntPref(name, value);
    }} else if (typeof value === "string" && (type === Services.prefs.PREF_STRING || type === Services.prefs.PREF_INVALID)) {{
      Services.prefs.setStringPref(name, value);
    }} else {{
      out.push(`${{name}}: not changed, the value's type doesn't match the preference's`);
      continue;
    }}
    out.push(`${{name}} = ${{JSON.stringify(value)}}`);
  }} catch (e) {{
    out.push(`${{name}}: not changed (${{e.message}})`);
  }}
}}
out"""
        return await run_chrome(ctx, since, browser, code, 10, lambda v: "\n".join(json.loads(v)) or "Nothing to change.")
