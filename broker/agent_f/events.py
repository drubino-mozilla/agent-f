"""Browser events, attribution, and the "changes since your last call" summary."""

import time
from collections import deque
from dataclasses import dataclass, field

MAX_CHANGE_LINES = 10


@dataclass
class Event:
    seq: int
    time: float
    browser: str
    label: str
    kind: str
    tab: int | None = None
    window: int | None = None
    data: dict = field(default_factory=dict)
    # The session whose command caused the event, or None for external changes.
    cause: str | None = None


class EventLog:
    def __init__(self, maxlen: int = 5000):
        self._events: deque[Event] = deque(maxlen=maxlen)
        self._seq = 0

    @property
    def seq(self) -> int:
        return self._seq

    @property
    def oldest_seq(self) -> int:
        return self._events[0].seq if self._events else self._seq + 1

    def add(self, **fields) -> Event:
        self._seq += 1
        event = Event(seq=self._seq, time=time.time(), **fields)
        self._events.append(event)
        return event

    def since(self, seq: int) -> list[Event]:
        return [e for e in self._events if e.seq > seq]


class TabCache:
    """Last known state of each tab in one browser, so closed tabs can still be named."""

    def __init__(self):
        self.tabs: dict[int, dict] = {}

    def update(self, info: dict) -> None:
        if "id" in info:
            self.tabs.setdefault(info["id"], {}).update(info)

    def replace(self, infos: list[dict]) -> None:
        self.tabs = {info["id"]: dict(info) for info in infos}

    def remove(self, tab_id: int) -> dict:
        return self.tabs.pop(tab_id, {})

    def remove_window(self, window_id: int) -> int:
        doomed = [tid for tid, info in self.tabs.items() if info.get("windowId") == window_id]
        for tid in doomed:
            del self.tabs[tid]
        return len(doomed)

    def get(self, tab_id: int) -> dict:
        return self.tabs.get(tab_id, {})


def quote(text: str | None, limit: int = 80) -> str:
    text = (text or "").replace("\n", " ").strip()
    if len(text) > limit:
        text = text[: limit - 1] + "\u2026"
    return f'"{text}"'


def short_url(url: str | None, limit: int = 120) -> str:
    url = url or ""
    return url if len(url) <= limit else url[: limit - 1] + "\u2026"


def _latest_urls(events: list[Event]) -> dict[tuple[str, int], str]:
    """The last URL each tab reached within this batch, so new tabs aren't reported as about:blank."""
    urls = {}
    for ev in events:
        if ev.kind in ("tab_created", "tab_updated") and ev.data.get("url"):
            urls[(ev.browser, ev.tab)] = ev.data["url"]
    return urls


def _describe(ev: Event, touched: set[tuple[str, int]], urls: dict[tuple[str, int], str]) -> tuple[tuple | None, str | None]:
    """Return a coalescing key and a sentence for one event, or (None, None) to skip it."""
    d = ev.data
    by_agent = ev.cause is not None
    b = ev.browser
    tab_touched = (b, ev.tab) in touched

    if ev.kind == "tab_created":
        where = f" in window {ev.window}" if ev.window is not None else ""
        what = short_url(urls.get((b, ev.tab)) or d.get("url")) or quote(d.get("title"))
        if by_agent:
            return ("tab", b, ev.tab, "created"), f"Another Agent F call opened tab {ev.tab}{where}: {what}"
        return ("tab", b, ev.tab, "created"), f"Tab {ev.tab} opened{where}: {what}"

    if ev.kind == "tab_removed":
        if d.get("isWindowClosing"):
            return None, None
        title = quote(d.get("title"))
        if by_agent:
            return ("tab", b, ev.tab, "removed"), f"Another Agent F call closed tab {ev.tab} {title}."
        return ("tab", b, ev.tab, "removed"), f"Tab {ev.tab} {title} was closed."

    if ev.kind == "tab_activated":
        title = quote(d.get("title"))
        return ("active", b, ev.window), f"Tab {ev.tab} {title} became the active tab in window {ev.window}."

    if ev.kind == "tab_updated":
        if not tab_touched:
            return None, None
        changed = d.get("changed") or []
        if "url" in changed:
            url = short_url(d.get("url"))
            if by_agent:
                return ("nav", b, ev.tab), f"Another Agent F call navigated tab {ev.tab} to {url}."
            return ("nav", b, ev.tab), f"Tab {ev.tab} navigated to {url}."
        if "discarded" in changed and d.get("discarded"):
            return ("discard", b, ev.tab), f"Tab {ev.tab} was unloaded by Firefox to save memory."
        return None, None

    if ev.kind == "tab_attached":
        if not tab_touched:
            return None, None
        return ("win", b, ev.tab), f"Tab {ev.tab} moved to window {ev.window}."

    if ev.kind == "user_input":
        verb = "typed" if d.get("kind") == "typed" else "clicked"
        return ("user", b, ev.tab, verb), f"You {verb} in tab {ev.tab}."

    if ev.kind == "window_created":
        return ("window", b, ev.window, "created"), f"Window {ev.window} opened."

    if ev.kind == "window_removed":
        count = d.get("tabs")
        suffix = f" ({count} tab{'s' if count != 1 else ''})" if count else ""
        return ("window", b, ev.window, "removed"), f"Window {ev.window} was closed{suffix}."

    if ev.kind == "window_focused":
        if ev.window is None or ev.window < 0:
            return None, None
        return ("focus", b), f"Window {ev.window} got focus."

    if ev.kind == "group_created":
        if not d.get("title"):
            return None, None
        return ("group", b, d.get("id"), "created"), f"Tab group {quote(d.get('title'))} was created in window {ev.window}."

    if ev.kind == "group_removed":
        return ("group", b, d.get("id"), "removed"), f"Tab group {quote(d.get('title'))} was removed."

    if ev.kind == "browser_connected":
        return ("browser", b), f"Browser {ev.label} connected."

    if ev.kind == "browser_disconnected":
        return ("browser", b), f"Browser {ev.label} disconnected."

    return None, None


def render_changes(
    events: list[Event],
    touched: set[tuple[str, int]],
    show_label: bool,
    exclude: str | None = None,
) -> list[str]:
    """Summarise events for an agent, leaving out those caused by the current call (exclude).

    Chats in Cursor share one MCP session, so events are attributed to calls, not sessions. A call's
    own effects are reported in its Effects block and happen before the since token it returns, so
    they reach only other chats' change lists, as "Another Agent F call ...".
    """
    latest: dict[tuple, tuple[int, str]] = {}
    urls = _latest_urls(events)
    for ev in events:
        if exclude and ev.cause == exclude:
            continue
        key, text = _describe(ev, touched, urls)
        if text is None:
            continue
        if show_label and not ev.kind.startswith("browser_"):
            text = f"[{ev.label}] {text}"
        latest[key] = (ev.seq, text)
    lines = [text for _, text in sorted(latest.values())]
    if len(lines) > MAX_CHANGE_LINES:
        earlier = len(lines) - MAX_CHANGE_LINES
        lines = [f"({earlier} earlier changes not shown; call list_tabs for the current state.)"] + lines[-MAX_CHANGE_LINES:]
    return lines
