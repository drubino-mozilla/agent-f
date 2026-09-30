"""Global element refs.

The content script tags each element it reports with a document id and a local number; the
background page adds the frame. Here those become short refs such as e57 that are unique across
every browser, tab and document for this broker run. A ref therefore says on its own which tab it
belongs to, and a ref from a page that has since navigated can never hit a different element.
"""

import re
from dataclasses import dataclass

TOKEN_RE = re.compile("\x01(-?\\d+):([0-9a-f]+):(\\d+)\x01")
STRAY_MARKS_RE = re.compile("[\x01\x02]")
REF_RE = re.compile(r"^e\d+$")


@dataclass(frozen=True)
class RefTarget:
    browser: str
    tab: int
    frame: int
    doc: str
    node: int


class Refs:
    def __init__(self):
        self._by_ref: dict[str, RefTarget] = {}
        self._by_target: dict[RefTarget, str] = {}
        self._next = 1

    def ref_for(self, target: RefTarget) -> str:
        ref = self._by_target.get(target)
        if ref is None:
            ref = f"e{self._next}"
            self._next += 1
            self._by_target[target] = ref
            self._by_ref[ref] = target
        return ref

    def get(self, ref: str) -> RefTarget | None:
        return self._by_ref.get(ref.strip())

    def translate(self, browser: str, tab: int, text: str) -> str:
        def replace(m: re.Match) -> str:
            return self.ref_for(RefTarget(browser, tab, int(m.group(1)), m.group(2), int(m.group(3))))
        return STRAY_MARKS_RE.sub("", TOKEN_RE.sub(replace, text))

    def translate_obj(self, browser: str, tab: int, value):
        if isinstance(value, str):
            return self.translate(browser, tab, value)
        if isinstance(value, list):
            return [self.translate_obj(browser, tab, v) for v in value]
        if isinstance(value, dict):
            return {k: self.translate_obj(browser, tab, v) for k, v in value.items()}
        return value


def looks_like_ref(value: str | None) -> bool:
    return bool(value and REF_RE.match(value.strip()))
