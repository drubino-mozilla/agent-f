"""Per-session defaults: the current browser, the current tab in each browser, and an event cursor.

Cursor gives all its chats one MCP session, so these are conveniences, not isolation.
"""

import time
from dataclasses import dataclass, field

from .events import EventLog


@dataclass
class Session:
    id: str
    cursor: int
    browser: str | None = None
    # Current tab per browser (profile id -> tab id).
    tabs: dict[str, int] = field(default_factory=dict)
    touched: set[tuple[str, int]] = field(default_factory=set)
    created: float = field(default_factory=time.time)
    last_call: float = 0.0

    @property
    def tab(self) -> int | None:
        return self.tabs.get(self.browser) if self.browser else None

    def tab_in(self, browser: str) -> int | None:
        return self.tabs.get(browser)

    def touch(self, browser: str, tab: int) -> None:
        self.touched.add((browser, tab))
        self.browser = browser
        self.tabs[browser] = tab


class Sessions:
    def __init__(self, log: EventLog):
        self._log = log
        self._sessions: dict[str, Session] = {}

    def get(self, session_id: str) -> Session:
        session = self._sessions.get(session_id)
        if session is None:
            session = Session(id=session_id, cursor=self._log.seq)
            self._sessions[session_id] = session
        return session

    def all(self) -> list[Session]:
        return list(self._sessions.values())
