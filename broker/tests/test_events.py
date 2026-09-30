from agent_f.events import MAX_CHANGE_LINES, EventLog, render_changes


def add(log, kind, **kw):
    kw.setdefault("browser", "p1")
    kw.setdefault("label", "nightly-a")
    return log.add(kind=kind, **kw)


def test_external_changes_are_described():
    log = EventLog()
    add(log, "tab_created", tab=7, window=1, data={"url": "https://example.com/"})
    add(log, "tab_removed", tab=3, window=1, data={"title": "Old tab"})
    add(log, "tab_activated", tab=7, window=1, data={"title": "Example"})
    lines = render_changes(log.since(0), set(), show_label=False)
    assert lines == [
        "Tab 7 opened in window 1: https://example.com/",
        'Tab 3 "Old tab" was closed.',
        'Tab 7 "Example" became the active tab in window 1.',
    ]


def test_new_tab_reported_with_the_address_it_reached():
    log = EventLog()
    add(log, "tab_created", tab=2, window=1, data={"url": "about:blank"})
    add(log, "tab_updated", tab=2, data={"changed": ["url"], "url": "https://www.wikipedia.org/"})
    lines = render_changes(log.since(0), set(), show_label=False)
    assert lines == ["Tab 2 opened in window 1: https://www.wikipedia.org/"]


def test_events_caused_by_agent_f_calls_are_named():
    log = EventLog()
    add(log, "tab_created", tab=8, window=1, data={"url": "https://b/"}, cause="s2")
    lines = render_changes(log.since(0), set(), show_label=False)
    assert lines == ["Another Agent F call opened tab 8 in window 1: https://b/"]


def test_updates_only_for_touched_tabs_and_coalesced():
    log = EventLog()
    add(log, "tab_updated", tab=5, data={"changed": ["url"], "url": "https://one/"})
    add(log, "tab_updated", tab=5, data={"changed": ["url"], "url": "https://two/"})
    add(log, "tab_updated", tab=6, data={"changed": ["url"], "url": "https://other/"})
    lines = render_changes(log.since(0), {("p1", 5)}, show_label=False)
    assert lines == ["Tab 5 navigated to https://two/."]


def test_window_closing_collapses_tab_closures():
    log = EventLog()
    add(log, "tab_removed", tab=1, window=2, data={"isWindowClosing": True, "title": "x"})
    add(log, "tab_removed", tab=2, window=2, data={"isWindowClosing": True, "title": "y"})
    add(log, "window_removed", window=2, data={"tabs": 2})
    lines = render_changes(log.since(0), set(), show_label=False)
    assert lines == ["Window 2 was closed (2 tabs)."]


def test_unfocused_firefox_is_not_reported():
    log = EventLog()
    add(log, "window_focused", window=-1)
    assert render_changes(log.since(0), set(), show_label=False) == []


def test_labels_shown_with_several_browsers():
    log = EventLog()
    add(log, "tab_created", tab=7, window=1, data={"url": "https://a/"})
    assert render_changes(log.since(0), set(), show_label=True) == [
        "[nightly-a] Tab 7 opened in window 1: https://a/"]


def test_long_change_lists_are_capped():
    log = EventLog()
    for i in range(MAX_CHANGE_LINES + 5):
        add(log, "tab_created", tab=i, window=1, data={"url": f"https://{i}/"})
    lines = render_changes(log.since(0), set(), show_label=False)
    assert len(lines) == MAX_CHANGE_LINES + 1
    assert lines[0].startswith("(5 earlier changes not shown")
    assert lines[-1].endswith(f"https://{MAX_CHANGE_LINES + 4}/")
