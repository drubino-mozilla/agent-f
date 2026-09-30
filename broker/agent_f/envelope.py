"""The header, change list and body that every tool result shares."""

from .events import quote, short_url

PAGE_OPEN = "<<<page-content"
PAGE_CLOSE = "page-content>>>"


def page_content(text: str) -> str:
    return f"{PAGE_OPEN}\n{text}\n{PAGE_CLOSE}"


def browser_header(label: str) -> str:
    return f"[{label}]"


def tab_header(label: str, tab: dict, note: str | None = None) -> str:
    header = f"[{label}] tab {tab.get('id')} {quote(tab.get('title'))} {short_url(tab.get('url'))}"
    if note:
        header += f" ({note})"
    return header


def render(
    body: str,
    header: str | None = None,
    changes: list[str] | None = None,
    effects: list[str] | None = None,
) -> str:
    parts = []
    if header:
        parts.append(header)
    if changes:
        parts.append("Changes since your last call:\n" + "\n".join(f"- {c}" for c in changes))
    if effects:
        parts.append("Effects:\n" + "\n".join(f"- {e}" for e in effects))
    parts.append("Result:\n" + body)
    return "\n".join(parts)
