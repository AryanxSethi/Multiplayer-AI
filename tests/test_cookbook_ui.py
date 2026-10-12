"""The Cookbook page, against a real headless window like the other pages.

The point of these is the two promises the page makes. The first is that it
opens without touching the network, because the Resources page tells the user
this app sends nothing anywhere. The second is that it says which models fit
and why, rather than presenting a list a reader has to take on trust.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6", reason="PySide6 is needed for the desktop page tests")
pytest.importorskip("psutil", reason="psutil backs hardware detection")

from PySide6.QtCore import QEvent, Qt  # noqa: E402
from PySide6.QtWidgets import (QApplication, QDialog, QFrame, QLabel,  # noqa: E402
                             QPushButton, QScrollArea, QWidget)

from desktop_app import window as win  # noqa: E402
from desktop_app.cookbook import (human_bytes, load_catalog,  # noqa: E402
                                 pick_graphics_adapter, recommend)
from desktop_app.cookbook_panel import CARD  # noqa: E402

# Every card frame the page can put on screen.
CARD_NAMES = {"card", *(name for name in CARD.values())}
from desktop_app.storage import Storage  # noqa: E402
from desktop_app.theme import DARK, LIGHT, MIKU, stylesheet  # noqa: E402
from tests.test_desktop_runtime import MemoryVault  # noqa: E402


@pytest.fixture(scope="session")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app
    app.processEvents()


@pytest.fixture
def storage(tmp_path):
    return Storage(tmp_path, MemoryVault())


@pytest.fixture
def window(qt_app, storage):
    """A real MainWindow, torn down after the test.

    The vault is the in-memory one throughout, because the runtime saves
    credentials while starting up and a real vault would leave a device token
    in the developer's OS credential store on every run.
    """
    instance = win.MainWindow(storage)
    yield instance
    instance.network.shutdown()
    instance.network.wait(10000)


def panel_of(window):
    """The Cookbook panel, found by navigating rather than by attribute.

    Navigating is what a user does, so reading it back that way also proves
    the page is reachable and that navigating there is what puts it on screen.
    """
    from desktop_app.cookbook_panel import CookbookPanel
    window.navigate(win.COOKBOOK_PAGE)
    page = window.stack.widget(win.COOKBOOK_PAGE)
    # The panel is the page, not something inside it. It used to be a dialog,
    # and keeping it a widget in its own right is what lets it go in the stack
    # at all -- a QDialog cannot be a stack page, which is why this reads the
    # page itself rather than searching into it.
    assert isinstance(page, CookbookPanel), (
        f"index {win.COOKBOOK_PAGE} holds {type(page).__name__}, not the "
        "Cookbook panel, so the page and the nav list disagree")
    return page


def text_of(panel):
    """Everything the page is currently saying, as one blob of text."""
    return " ".join(widget.text() for widget in panel.findChildren(QLabel))


def use_a_stated_device(panel, vram_gb=4):
    """Pin the panel to a device the test writes down, not the one it runs on.

    Whether a model is recommended depends on how much memory was free when the
    page was built, so a test that asserts a particular model is on screen passes
    on an idle machine and fails on a busy one -- with "Nothing in the catalogue
    fits this device" as the reason, which is about the runner's memory rather
    than about the code.
    """
    from desktop_app.cookbook import Hardware
    panel.hardware = Hardware(
        platform="Linux 6.1", architecture="x86_64", cpu_cores=16,
        memory_total=32 * 1024 ** 3, memory_available=28 * 1024 ** 3,
        disk_free=500 * 1024 ** 3,
        vram=vram_gb * 1024 ** 3 if vram_gb else None,
        unified_memory=False, sources={})
    panel.render_models()
    settle()
    return panel.hardware


def settle():
    """Let queued deletions and layout actually happen before reading the tree."""
    QApplication.processEvents()
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    QApplication.processEvents()


def cards_on_page(panel):
    """The model cards the page is currently holding.

    Counted as the widgets in the results layout rather than by matching text,
    so that a header, a withdrawn notice or an empty-state card cannot be
    mistaken for a model.
    """
    found = []
    for index in range(panel.results.count()):
        item = panel.results.itemAt(index)
        widget = item.widget() if item is not None else None
        if widget is not None:
            found.append(widget)
    return found


def repopulate(panel, count):
    """Give the panel a catalogue of ``count`` real models to page through.

    Cloned from the shipped catalogue rather than invented, because the point
    of these tests is paging and not model arithmetic -- and a fabricated record
    would be refused by validation, which would look like paging working.
    """
    from dataclasses import replace

    shipped = load_catalog()
    panel.catalog = [
        replace(shipped[index % len(shipped)],
                repo=f"example/model-{index:02d}",
                name=f"Repeat Model {index:02d}")
        for index in range(count)
    ]
    panel.render_models()
    settle()
    return panel.catalog


def test_the_cookbook_is_a_page_in_the_navigation_stack(window) -> None:
    """It is a destination like any other, not a window that happens to float.

    Which means it scrolls and resizes with the app and leaves no second
    window behind it. The nav list, the stack and the titles have to agree on
    length; that is checked elsewhere, and this is the check that the page is
    actually the thing in the stack.
    """
    window.navigate(win.COOKBOOK_PAGE)
    assert window.stack.currentIndex() == win.COOKBOOK_PAGE
    assert window.page_title.text() == win.PAGE_TITLES[win.COOKBOOK_PAGE]

    panel = window.stack.widget(win.COOKBOOK_PAGE)
    scroll = panel.findChild(QScrollArea)
    assert scroll is not None, (
        "the Cookbook page has no scroll area, so a long list would be clipped "
        "with no way to reach the rest of it")
    assert scroll.widgetResizable() is True, (
        "the scroll area does not resize with the window, so the page would "
        "stay at one width regardless of the panel")


def test_the_cookbook_scrolls_its_cards_rather_than_clipping_them(window) -> None:
    """The scroll area has to contain the cards, not merely sit on the page.

    This is the check that was missing, and its absence is why the page shipped
    unreadable. The panel built a scroll area, put it on screen, and then added
    every card to the panel's own layout beside it -- so the scroll area was
    empty, measured its content as nothing, kept its scrollbar range at zero,
    and never drew a scrollbar. The page simply grew past the bottom of the
    window with the rest of it unreachable.

    A test asserting "a QScrollArea exists and is resizable" passes perfectly
    well against an empty one, which is exactly what the old assertion did. So
    these assert the content instead: that the cards live inside the scroll
    area, and that the content is genuinely taller than the viewport, which is
    the only condition under which a scrollbar means anything.
    """
    window.resize(1200, 900)
    window.show()
    panel = panel_of(window)
    settle()

    scroll = panel.findChild(QScrollArea)
    assert scroll is not None, "the Cookbook page has no scroll area at all"

    content = scroll.widget()
    assert content is not None and content is not panel, (
        "the scroll area has no content widget of its own, so nothing can be "
        "scrolled inside it")

    # Every label on the page has to be inside the scroll content. Comparing the
    # two sets is what distinguishes "the cards are in there" from "the cards
    # are next to it", which looks identical until the page is too tall.
    inside = {id(label) for label in content.findChildren(QLabel)}
    on_page = {id(label) for label in panel.findChildren(QLabel)}
    assert on_page, "the Cookbook page rendered no text at all"
    stray = on_page - inside
    assert not stray, (
        f"{len(stray)} of {len(on_page)} labels sit outside the scroll area, so "
        "they are clipped once the page is taller than the window")

    # And the content must actually overflow, or there is nothing to scroll to.
    assert content.height() > scroll.viewport().height(), (
        f"the content is {content.height()}px tall inside a "
        f"{scroll.viewport().height()}px viewport, so the scroll area is not "
        "under any pressure and this page cannot be exercising the bug")
    assert scroll.verticalScrollBar().maximum() > 0, (
        "the content overflows but the scrollbar range is still zero, so the "
        "rest of the page is unreachable")

    # Nothing should overflow sideways either: a horizontal scrollbar on a page
    # that reflows means some label refused to wrap.
    assert scroll.horizontalScrollBar().maximum() == 0, (
        "the page scrolls sideways, so at least one label is not wrapping")


def test_the_cookbook_is_reached_from_the_top_bar_and_not_the_rail(window) -> None:
    """Its navigation control is beside Invite and Connect, not down the side.

    It is the same button object the rail loop builds, so it checks and
    unchecks with the rest of them and Ctrl+7 reaches it. Only its position
    differs, which is what makes it a shortcut to the page rather than a
    separate kind of control.
    """
    button = window.cookbook_button
    assert button in window.nav_buttons, (
        "the top-bar Cookbook control is not one of the navigation buttons, so "
        "it cannot show which page you are on")
    assert window.nav_buttons[win.COOKBOOK_PAGE] is button, (
        "the nav list's entry for the Cookbook is not the button in the top bar")
    assert button not in window.sidebar.findChildren(type(button)), (
        "the Cookbook is in the rail as well as the top bar")

    button.setToolTip(win.PAGE_HELP["cookbook"])
    assert button.toolTip(), "the Cookbook button has no tooltip"
    assert "Cookbook" in button.accessibleName(), (
        "an icon-only control needs an accessible name or it does not exist to "
        "anyone navigating by keyboard or screen reader")

    window.navigate(win.OVERVIEW_PAGE)
    assert not button.isChecked(), "the Cookbook stayed selected on another page"
    button.click()
    assert window.stack.currentIndex() == win.COOKBOOK_PAGE
    assert button.isChecked(), (
        "the Cookbook button did not show as selected while its page was up")


def test_the_cookbook_icon_carries_no_emoji_or_font_glyph(window) -> None:
    """A character in the icon slot would defeat the reason for painting it.

    The navigation marks are drawn so they share one stroke weight regardless of
    what fonts are installed. This machine has no fonts at all, which is exactly
    the case that would show it: a glyph icon would come back blank here and
    look merely wrong on a normal one.
    """
    from desktop_app.icons import navigation_icon
    icon = navigation_icon("cookbook", "#ffffff")
    assert not icon.isNull()
    for size in (16, 24, 48):
        image = icon.pixmap(size, size).toImage()
        painted = sum(1 for x in range(image.width()) for y in range(image.height())
                      if image.pixelColor(x, y).alpha() > 0)
        assert painted > 0, f"the icon is blank at {size}px, so it needs a font"


def test_every_verdict_is_styled_and_no_two_meanings_look_alike(window) -> None:
    """A verdict has to be told apart from every other verdict, in every theme.

    This started as one narrow check on a name collision -- a badge given the
    theme token ``error`` where the Qt objectName wanted was ``errorMessage``,
    which matches no rule, so a model this device should not run was drawn
    exactly like one it should. A dialog hid it; a page does not.

    It now covers the whole mapping, because the same class of mistake is easy
    to repeat by reuse: ``unknown`` and ``possible with compromises`` were both
    sent to ``#muted``, so "we do not know" and "it runs but slowly" were drawn
    identically. Every verdict must have a rule that exists in the stylesheet,
    and the four distinct meanings must be visually distinct from each other.
    """
    from desktop_app.cookbook_panel import CARD, TONE

    assert set(TONE) == set(CARD), (
        f"a verdict has a label style but no card, or the reverse: "
        f"{set(TONE) ^ set(CARD)}")
    for verdict, style in TONE.items():
        assert CARD[verdict], f"{verdict!r} has no card to carry its colour"

    for theme in (DARK, LIGHT, MIKU):
        for style in set(TONE.values()):
            assert f"QLabel#{style}" in stylesheet(theme), (
                f"the {theme} sheet has no QLabel#{style} rule, so a {style!r} "
                "verdict renders as unstyled text")

    # The colour now lives on the card, so each meaning needs its own card too,
    # and no two meanings may collapse onto one.
    assert len(set(CARD.values())) == 4, (
        f"only {len(set(CARD.values()))} distinct cards for four meanings: "
        f"{CARD}")
    assert CARD["unknown"] != CARD["possible with compromises"], (
        "'we do not know' and 'it runs but slowly' are drawn the same way")
    assert CARD["not recommended"] != CARD["likely to run"]

    for card in set(CARD.values()):
        for theme in (DARK, LIGHT, MIKU):
            assert f"QFrame#{card}" in stylesheet(theme), (
                f"the {theme} sheet has no QFrame#{card} rule, so the verdict's "
                "colour is never shown")
    # And each card is drawn in the colour it claims, in every theme. The
    # stylesheet resolves tokens on the way out, so this compares against the
    # resolved palette value rather than against the @@token@@ that went in.
    from desktop_app.theme import color
    for card, token in (("modelCardOk", "success"),
                        ("modelCardWarn", "warning"),
                        ("modelCardBad", "error"),
                        ("modelCardUnknown", "text_muted")):
        for theme in (DARK, LIGHT, MIKU):
            rule = [line for line in stylesheet(theme).splitlines()
                    if line.startswith(f"QFrame#{card}")]
            assert rule, f"no rule for {card} in {theme}"
            assert color(theme, token) in rule[0], (
                f"{card} is not drawn in {token} in {theme}: {rule[0]!r}")


def test_the_verdict_carries_a_plain_english_gloss_for_hover_and_readers(window) -> None:
    """The verdict word is internal vocabulary; the colour is not the only cue.

    A colour is no use at all to somebody who cannot see it, and "possible
    with compromises" means nothing on first reading. ``EXPLANATIONS`` holds a
    sentence for each verdict and was never rendered anywhere -- this is what
    it was written for.

    Checked by looking for one of the prepared sentences rather than by
    matching the label's visible text, because the alternative-build rows show
    a short mark ("on the card") and carry the whole verdict on hover.
    """
    import re

    from desktop_app.cookbook import EXPLANATIONS

    panel = panel_of(window)
    settle()
    badges = [widget for widget in panel.findChildren(QLabel)
              if widget.objectName().startswith("verdict")]
    assert badges, "no verdict badge was drawn at all"

    prepared = set(EXPLANATIONS.values())
    for badge in badges:
        hover = badge.toolTip() or ""
        name = badge.accessibleName() or ""
        assert any(gloss in hover for gloss in prepared), (
            f"{badge.text()!r} has no prepared explanation on hover: {hover!r}")
        assert any(gloss in name for gloss in prepared), (
            f"{badge.text()!r} is not explained to a screen reader: {name!r}")
        # And it names a verdict that actually exists, rather than any sentence.
        assert any(re.search(r"\b" + re.escape(result), name)
                   for result in EXPLANATIONS), (
            f"{badge.text()!r} names no verdict in {name!r}")


def test_a_command_is_monospaced_selectable_and_not_body_text(window) -> None:
    """A command is to be copied, and a wrapped path cannot be.

    These rendered in Segoe UI, where a repository identifier is an unbroken
    run of characters that wraps wherever it happens to land -- the worst place
    for something the reader is about to paste into a terminal and get exactly
    right.
    """
    panel = panel_of(window)
    settle()
    blocks = [widget for widget in panel.findChildren(QLabel)
              if widget.objectName() == "command"]
    assert blocks, "no command block was drawn"
    for theme in (DARK, LIGHT, MIKU):
        rule = [line for line in stylesheet(theme).splitlines()
                if line.startswith("QLabel#command")]
        assert rule, f"the {theme} sheet has no QLabel#command rule"
        assert "monospace" in rule[0] or "Consolas" in rule[0], (
            f"commands are not monospaced in {theme}: {rule[0]!r}")

    block = blocks[0]
    assert "ollama run" in block.text()
    assert block.text().count("\n") >= 2, (
        "the commands are not held together in one block")
    assert block.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByMouse, (
        "a command cannot be selected, so it can only be typed by hand")
    assert block.accessibleName(), (
        "the command block is silent to a screen reader")


def test_opening_the_cookbook_asks_the_network_for_nothing(window) -> None:
    """Nothing is requested until Refresh is pressed.

    The Resources page says this device's readings are sent nowhere, and a page
    that fetched its catalogue on open would make that untrue while the page
    still said it. The check is on the command channel, which is the only way
    out of the UI to the network.

    The cache changes what "on open" means rather than removing it: a saved
    catalogue is read from disk, which is not a request, and re-reading the
    same numbers from a server on every launch would be a request nobody
    needed to make.
    """
    sent = []
    window.command = lambda name, *args, **kwargs: sent.append(name)

    window.navigate(win.COOKBOOK_PAGE)
    panel = panel_of(window)
    settle()
    assert sent == [], f"opening the Cookbook asked the network for {sent!r}"
    assert panel.catalog, "opening the page produced no catalogue at all"


def test_nothing_refreshes_on_its_own(window) -> None:
    """No timer, no launch check, no hover.

    A catalogue that updates itself is a phone-home with a schedule, and it
    would make the Resources page's promise untrue in the one place somebody
    was relying on it. The only trigger is the button, so this asserts there
    is no timer attached to the page at all rather than that one has not fired.
    """
    from PySide6.QtCore import QTimer
    panel = panel_of(window)
    for child in panel.findChildren(QTimer):
        assert not child.isActive(), (
            "something on the Cookbook page is running on a timer, so it will "
            "make a request without anybody asking")
    assert not hasattr(panel, "catalog_timer"), (
        "the page has a catalogue timer; a scheduled refresh contradicts the "
        "promise the Resources page makes")


def test_the_cookbook_explains_the_device_it_is_judging_models_against(window) -> None:
    """A recommendation with no stated basis is one nobody can check."""
    panel = panel_of(window)
    settle()
    said = text_of(panel)
    assert "memory" in said.lower(), f"the device is not described: {said[:200]!r}"

    # Graphics memory is read from the operating system now, so this has to
    # cover both outcomes. On a machine with a card the page must name it, or
    # the figure it judged against is a number nobody can check. On a machine
    # without one it must say why -- and the reason it gives must be true of
    # that machine rather than a blanket complaint about a missing library.
    note = panel.hardware.graphics_note()
    assert "Graphics:" in note, f"the page does not say what it judged against: {note!r}"
    if panel.hardware.vram is None:
        assert panel.unknown_label.text(), (
            "no graphics memory was detected and the page says nothing about it")
        assert panel.unknown_label.isVisible()
    else:
        picked = pick_graphics_adapter(panel.hardware.gpus)
        assert picked is not None and picked.name in note, (
            f"a {human_bytes(panel.hardware.vram)} figure is being judged "
            f"against but the page names no card: {note!r}")


def test_every_recommendation_says_why_and_what_it_would_take(window) -> None:
    """Every verdict carries its reason, its source, and its caveat.

    The whole complaint about model lists is that they present a verdict
    without the reasoning. A card that shows a model and a green tick and
    nothing else is the thing this feature exists to stop being.
    """
    panel = panel_of(window)
    use_a_stated_device(panel)
    said = text_of(panel)
    assert "Qwen2.5" in said, f"nothing was recommended: {said[:300]!r}"
    assert "GB" in said or "MB" in said, f"no size is shown: {said[:300]!r}"
    assert "memory" in said.lower(), "no reason is given"
    assert "hugging" in said.lower(), (
        f"the source is not attributed: {said[-300:]!r}")
    assert "not measured on this device" in said.lower(), (
        "nothing distinguishes a computed fit from a tested one")


def test_the_cookbook_shows_a_command_that_names_a_real_repository(window) -> None:
    """A command built from a guess is worse than no command.

    It has to name the repository that actually holds the file, which for a
    quantised build is the quantiser's and not the model's publisher.
    """
    panel = panel_of(window)
    use_a_stated_device(panel)
    said = text_of(panel)
    assert "ollama run hf.co/" in said, f"no runnable command: {said[:400]!r}"
    assert "bartowski/" in said, (
        f"the command does not name the repository holding the file: {said[:400]!r}")


def test_the_cookbook_can_be_narrowed_to_one_kind_of_work(window) -> None:
    """Choosing a task changes the list rather than only the label."""
    panel = panel_of(window)
    settle()
    everything = text_of(panel)
    assert "Everything" in " ".join(
        panel.task_picker.itemText(i) for i in range(panel.task_picker.count())), (
        "there is no way to see everything that fits, so opening the page on a "
        "task would hide models nobody asked to exclude")

    index = panel.task_picker.findData("coding")
    assert index >= 0, "there is no way to ask for a coding model"
    panel.task_picker.setCurrentIndex(index)
    settle()
    assert text_of(panel) != everything, "choosing a task changed nothing on screen"


def test_the_refresh_button_says_what_it_sends_before_it_is_pressed(window) -> None:
    """The disclosure is on the button, not in a dialog behind the button."""
    panel = panel_of(window)
    tip = panel.refresh.toolTip()
    assert tip, "the refresh button has no tooltip"
    assert "huggingface" in tip.lower(), f"it does not say where it goes: {tip!r}"
    assert "address" in tip.lower() or "network" in tip.lower(), (
        f"it does not say what is sent: {tip!r}")


def test_one_press_asks_once_per_model_and_says_so_when_none_answer(window) -> None:
    """One press, one request per model, and a refusal that loses nothing.

    The requests run in sequence rather than all at once: a burst of four from
    a single button press is not something the person pressing it agreed to,
    and the progress on the button only means something if it is counting work
    already done.
    """
    panel = panel_of(window)
    settle()
    pending = []
    window.command = lambda name, *args, **kwargs: pending.append(kwargs)

    panel.do_refresh()
    assert len(pending) == 1, f"one press asked {len(pending)} times at once"
    assert not panel.refresh.isEnabled(), (
        "the button stayed live while the request was in flight")
    assert "1 of" in panel.refresh.text(), (
        f"the button does not show progress: {panel.refresh.text()!r}")

    panel.do_refresh()
    assert len(pending) == 1, "a second press started another pass while one was running"

    # Answer every request with a refusal, in order.
    guard = 0
    while pending and guard < 20:
        pending.pop(0)["failure"]("huggingface.co could not be reached.")
        guard += 1
    settle()

    assert guard < 20, "the refresh looped instead of finishing"
    said = text_of(panel).lower()
    assert "could not be read" in said, "a failed refresh said nothing"
    assert "qwen2.5" in said, "a failed refresh emptied the list it had already loaded"
    assert panel.refresh.isEnabled(), "the button stayed dead after a failure"
    assert panel.refresh.text().startswith("Refresh from"), (
        f"the button is still mid-attempt: {panel.refresh.text()!r}")
    assert not panel._busy, "the page is still marked busy after a failed refresh"


def test_every_model_is_re_read_not_just_the_first(window) -> None:
    """A refresh has to update the list, not one entry in it.

    It used to refresh ``catalog[0]``, so the other three kept figures as old
    as the install and the one that moved was whichever happened to sort
    first. That is not a refresh of a catalogue.
    """
    panel = panel_of(window)
    settle()
    pending = []
    window.command = lambda name, *args, **kwargs: pending.append(kwargs)
    asked = []

    panel.do_refresh()
    guard = 0
    while pending and guard < 40:
        request = pending.pop(0)
        asked.append(request)
        request["failure"]("no")
        guard += 1
    settle()

    assert guard < 40, "the refresh looped instead of finishing"
    assert len(asked) == len(panel.catalog), (
        f"one press asked {len(asked)} times for {len(panel.catalog)} models")


def test_a_refreshed_model_goes_through_the_same_validation_as_a_bundled_one(window) -> None:
    """A field arriving over the network is no more trusted than one on disk.

    Otherwise a response could carry a task this build has never heard of, or a
    negative size, straight into a recommendation by a route the catalogue's own
    checks are not applied to. The list on screen must not change when a bad
    record is refused.
    """
    panel = panel_of(window)
    settle()
    # The catalogue itself, rather than everything on the page: refusing a
    # record is supposed to change the line saying it was refused, and only
    # the list of models must be untouched.
    before = [model.repo for model in panel.catalog]
    # "tasks" is deliberately absent from this list. A response's claim about
    # what a model is for is not read any more -- those fields come from the
    # catalogue -- so a response naming a task this build has never heard of is
    # no longer a way in. Everything below is a field the Hub genuinely sends.
    for record in ({"repo": "a/b", "context": -8},
                   {"repo": "not a repo id"},
                   {"repo": "a/b", "verified": "yes, definitely"},
                   {"repo": "a/b", "parameters": "lots"}):
        pending = []
        window.command = lambda name, *args, **kwargs: pending.append(kwargs)
        panel.do_refresh()
        # The bad record answers the first request; the rest fail, so the
        # refresh finishes without any of them changing the catalogue.
        guard = 0
        while pending and guard < 40:
            request = pending.pop(0)
            if not guard:
                request["success"](dict(record, last_verified="now"))
            else:
                request["failure"]("no")
            guard += 1
        settle()

        assert "unusable" in text_of(panel).lower(), (
            f"{record!r} was accepted: {text_of(panel)[:200]!r}")
        assert [model.repo for model in panel.catalog] == before, (
            "a refused record still changed the list: "
            f"{[model.repo for model in panel.catalog]}")


def test_a_good_refresh_replaces_every_model_and_saves_it_for_next_time(window) -> None:
    """The happy path, and that a refresh survives a restart.

    It used to be held in memory and the page said so. That was true and it was
    also the wrong behaviour: the numbers are the same on every launch, so
    re-reading them on every launch is a request nobody needed to make, and a
    user who refreshed once would find it forgotten the next morning.
    """
    from desktop_app.cookbook import load_cache
    panel = panel_of(window)
    settle()
    pending = []
    # The repo asked for is captured as well as the callbacks, because a real
    # host answers with the model that was requested. Replying with a different
    # one is a different case entirely, and has its own test below.
    window.command = lambda name, *args, **kwargs: pending.append(
        (args, kwargs))
    panel.do_refresh()

    def answer(request, index):
        repo = request[0][0]
        request[1]["success"]({
            "repo": repo, "artifact_repo": "bartowski/Brand-New-Model-GGUF",
            # A name the response chose on purpose, to prove it is not the one
            # that ends up on screen: the catalogue's is.
            "name": f"Name From The Hub {index}", "publisher": "someone",
            "parameters": 700000000, "context": 8192, "layers": 16,
            "kv_heads": 4, "head_dim": 64, "license": "apache-2.0",
            "languages": ("en",), "vision": False,
            "variants": [{"name": "m-Q4_K_M.gguf", "quantization": "Q4_K_M",
                          "size": 500_000_000}],
            "last_verified": "2026-10-10 12:00 UTC"})

    guard = 0
    index = 0
    while pending and guard < 40:
        answer(pending.pop(0), index)
        index += 1
        guard += 1
    settle()

    said = text_of(panel)
    assert index == len(panel.catalog), (
        f"the press answered {index} requests for {len(panel.catalog)} models")
    assert "Name From The Hub" not in said, (
        "a response's name replaced the curated one, so the heading changed to "
        f"whatever the repository happened to be called: {said[:300]!r}")
    for model in panel.catalog:
        assert model.display in said, (
            f"{model.display} is no longer on screen after its refresh")

    saved = load_cache(panel.directory)
    assert saved is not None, "the refresh was not saved, so the next launch forgets it"
    assert saved, "the saved catalogue is empty"
    assert len(saved) == len(panel.catalog), (
        f"the saved catalogue has {len(saved)} models, not {len(panel.catalog)}")
    assert all(model.last_verified == "2026-10-10 12:00 UTC" for model in saved), (
        "the saved catalogue kept the old figures, so nothing was refreshed")
    assert all(model.tasks for model in saved), (
        "a refreshed model came back with no tasks, so it would vanish from "
        "every filter")


def test_a_partial_refresh_says_which_models_it_missed(window) -> None:
    """Half a refresh must not be reported as a whole one.

    This is the case that matters: the host answered for some models and not
    others, and a reader who is told plainly that it worked will believe a
    stale list is current.
    """
    panel = panel_of(window)
    settle()
    model_first = panel.catalog[0]
    pending = []
    window.command = lambda name, *args, **kwargs: pending.append(kwargs)
    panel.do_refresh()

    # Answered for the first model's own repository, so the merged record is a
    # real refresh of something already in the catalogue rather than a stranger.
    good = {"repo": model_first.repo,
            "artifact_repo": model_first.artifact_repo,
            "name": "Name From The Hub", "publisher": "someone",
            "parameters": 700000000, "context": 8192, "layers": 16,
            "kv_heads": 4, "head_dim": 64, "license": "apache-2.0",
            "languages": ("en",), "vision": False,
            "variants": [{"name": "m-Q4_K_M.gguf", "quantization": "Q4_K_M",
                          "size": 500_000_000}],
            "last_verified": "2026-10-10 12:00 UTC"}

    guard = 0
    while pending and guard < 40:
        request = pending.pop(0)
        if guard == 0:
            request["success"](dict(good))
        else:
            request["failure"]("timed out")
        guard += 1
    settle()

    note = panel.catalog_note.text()
    # The curated name survives, so the model that answered is still shown --
    # under the name the catalogue gave it, not the one the response carried.
    assert "Name From The Hub" not in text_of(panel), (
        "a response's name was adopted, which carrying over exists to prevent")
    assert model_first.display in text_of(panel), (
        "the model that answered is not on screen under its own name")
    assert "could not be" in note.lower(), (
        f"a partial refresh reported itself as a whole one: {note!r}")
    assert "timed out" in note, f"the note does not say what went wrong: {note!r}"
    assert panel.refresh.isEnabled(), "the button stayed dead after a partial refresh"


def test_a_refresh_that_returns_an_unknown_model_does_not_add_it(window) -> None:
    """The response decides what is refreshed, not what is adopted.

    A host answering with a model nobody asked about is not a refresh result.
    Merging it in would let a mirror replace the catalogue with whatever it
    likes, which is the one thing the opt-in request was not agreeing to.
    """
    panel = panel_of(window)
    settle()
    before = [model.repo for model in panel.catalog]
    pending = []
    window.command = lambda name, *args, **kwargs: pending.append(kwargs)
    panel.do_refresh()

    guard = 0
    while pending and guard < 40:
        request = pending.pop(0)
        if not guard:
            request["success"]({
                "repo": "someone/Completely-Different", "artifact_repo": "x/y",
                "name": "Different", "publisher": "someone", "parameters": 700000000,
                "context": 8192, "layers": 16, "kv_heads": 4, "head_dim": 64,
                "license": "apache-2.0", "languages": ("en",), "vision": False,
                "tasks": ("general",), "verified": False,
                "variants": [{"name": "m-Q4_K_M.gguf", "quantization": "Q4_K_M",
                              "size": 500_000_000}],
                "last_verified": "2026-10-10 12:00 UTC"})
        else:
            request["failure"]("no")
        guard += 1
    settle()

    repos = [model.repo for model in panel.catalog]
    assert "someone/Completely-Different" not in repos, (
        "a model that was never asked about was adopted from the response")
    assert repos == before, f"the catalogue changed to {repos}"


def test_the_restore_button_only_works_once_there_is_something_behind_it(window) -> None:
    """It is enabled by the existence of the file it would restore.

    A rollback to nothing is not a rollback, and a button that always works and
    sometimes does nothing is worse than one that says so.
    """
    from desktop_app.cookbook import PREVIOUS_NAME, save_catalog
    panel = panel_of(window)
    # Stated device, because a restored 4GB model stops being recommended when
    # the runner happens to be short of memory, and this test is about the
    # rollback rather than about whether the model fits.
    use_a_stated_device(panel)
    assert not panel.restore.isEnabled(), (
        "restoring is offered before anything has been replaced")

    from tests.test_cookbook_cache import a_model
    save_catalog([a_model("a/b")], panel.directory)
    save_catalog([a_model("c/d")], panel.directory)
    assert (panel.directory / PREVIOUS_NAME).is_file()

    panel.refresh_controls()
    assert panel.restore.isEnabled(), (
        "the earlier catalogue is on disk but restoring is not offered")

    panel.do_restore()
    settle()
    assert "Restored" in text_of(panel), (
        f"restoring said nothing: {text_of(panel)[:200]!r}")
    assert "a/b" in text_of(panel), (
        "restoring did not put the earlier models back on screen")


def test_the_mirror_setting_can_be_reached_without_editing_the_json(window) -> None:
    """Reading a setting nobody can change leaves the feature half-built.

    ``cookbook_source`` was read by the page and documented in a commit
    message, and the only way to set it was to open the preferences file by
    hand. A mirror is the answer to a person who does not want the public host
    to see their requests, so it has to be settable from the page.
    """
    panel = panel_of(window)
    window.show()
    settle()
    assert panel.mirror.accessibleName(), (
        "the mirror box has no accessible name, so it does not exist to anyone "
        "navigating by keyboard or screen reader")

    panel.mirror.setText("https://hf-mirror.example.com")
    panel.use_mirror.click()
    settle()

    assert panel.base == "https://hf-mirror.example.com", (
        f"the mirror was not saved: {panel.base!r}")
    assert window.storage.settings["cookbook_source"] == "https://hf-mirror.example.com"
    # And every word on the page now names where a refresh would read from,
    # rather than still claiming the public host.
    assert "hf-mirror.example.com" in panel.refresh.text(), (
        f"the button still names the wrong host: {panel.refresh.text()!r}")
    assert "hf-mirror.example.com" in panel.refresh.toolTip(), (
        "the disclosure still names the wrong host")
    assert "huggingface.co" not in panel.catalog_note.text(), (
        f"the note still claims the public host: {panel.catalog_note.text()!r}")


def test_a_mirror_that_is_not_an_https_origin_is_refused_and_explained(window) -> None:
    """The check is the one the request itself uses, so it fails before Refresh.

    Accepting a value here that the request would later refuse would leave the
    button promising to ask somewhere it cannot reach.
    """
    panel = panel_of(window)
    # Shown, because isVisible() is false for anything in a window that is not
    # on screen, and "the refusal was not shown" has to mean not shown rather
    # than unshown.
    window.show()
    settle()
    before = window.storage.settings.get("cookbook_source")

    for bad in ("http://hf-mirror.example.com",
                "https://hf-mirror.example.com/hf",
                "https://hf-mirror.example.com?a=1",
                "ftp://hf-mirror.example.com",
                "not a host at all"):
        panel.mirror.setText(bad)
        panel.use_mirror.click()
        settle()

        assert panel.mirror_error.isVisible(), (
            f"{bad!r} was accepted without saying anything")
        assert panel.mirror_error.text(), (
            f"{bad!r} was refused but not explained")
        assert window.storage.settings.get("cookbook_source") == before, (
            f"{bad!r} was written to the settings anyway")


def test_clearing_the_mirror_goes_back_to_the_public_catalogue(window) -> None:
    """There has to be a way back, or a mirror is a one-way door."""
    panel = panel_of(window)
    settle()
    panel.mirror.setText("https://hf-mirror.example.com")
    panel.use_mirror.click()
    settle()
    assert panel.base == "https://hf-mirror.example.com"

    panel.restore_mirror.click()
    settle()

    assert not panel.base, f"the mirror is still in use: {panel.base!r}"
    assert window.storage.settings.get("cookbook_source") is None
    assert "huggingface.co" in panel.refresh.text(), (
        f"the button did not go back to the public host: {panel.refresh.text()!r}")


def test_an_empty_box_means_the_default_rather_than_an_error(window) -> None:
    """Emptying the field is how you go back, so it must not read as a mistake."""
    panel = panel_of(window)
    settle()
    panel.mirror.setText("https://hf-mirror.example.com")
    panel.use_mirror.click()
    settle()

    panel.mirror.setText("")
    panel.use_mirror.click()
    settle()

    assert not panel.mirror_error.isVisible(), (
        f"emptying the box was reported as an error: {panel.mirror_error.text()!r}")
    assert not panel.base


def test_a_saved_mirror_is_shown_on_the_page(window) -> None:
    """Whatever is in the setting is visible, not just held in a variable."""
    # The window's own storage, not a separate fixture's: the page reads
    # whatever the running window was built with.
    window.storage.settings["cookbook_source"] = "https://hf-mirror.example.com"
    window.storage.save()
    panel = panel_of(window)
    window.show()
    settle()
    assert panel.base == "https://hf-mirror.example.com"
    assert panel.mirror.text() == "https://hf-mirror.example.com", (
        "the page does not show the mirror it is about to use")
    assert panel.mirror_box.isVisible(), (
        "the mirror row is hidden while a mirror is set")


def test_a_filter_with_no_models_says_it_is_the_catalogue_not_the_hardware(window) -> None:
    """Choosing Image understanding must not tell somebody their PC is too small.

    It did. All four models in the catalogue fit this machine comfortably; none
    of them is simply tagged ``vision``. The page reported that as "Nothing in
    the catalogue fits this device" and suggested freeing memory, which is a
    confident statement about somebody's hardware standing in for a gap in a
    four-model catalogue.
    """
    panel = panel_of(window)
    window.show()
    settle()

    index = panel.task_picker.findData("vision")
    assert index >= 0, "there is no image filter to test"
    # The premise, checked rather than assumed: these models do fit this
    # device, and they are simply not tagged for the filter.
    assert recommend(panel.catalog, panel.hardware), (
        "the catalogue fits nothing here, so the gap is not being exercised")

    panel.task_picker.setCurrentIndex(index)
    settle()
    said = text_of(panel)

    assert "fits this device" not in said, (
        f"a catalogue gap was reported as a hardware limit: {said[-260:]!r}")
    assert "gap in the catalogue" in said, (
        f"the real cause is not named: {said[-260:]!r}")
    assert "none of the" in said.lower(), (
        f"it does not say that nothing is tagged for this: {said[-260:]!r}")
    # And the advice has to match the cause: no memory-freed nonsense here.
    for irrelevant in ("free some memory", "smaller build"):
        assert irrelevant not in said.lower(), (
            f"{irrelevant!r} is advice for a hardware limit, not a catalogue gap")


def test_an_empty_filter_offers_a_way_back_to_the_models_that_are_there(window) -> None:
    """A gap the reader cannot fill is still worth widening away from.

    The models they came for are not in the catalogue, but the ones that are
    may be exactly what their machine can hold -- so there has to be a button
    that takes the filter off, not just an explanation.
    """
    panel = panel_of(window)
    window.show()
    settle()
    panel.task_picker.setCurrentIndex(panel.task_picker.findData("vision"))
    settle()

    buttons = [button.text() for button in panel.findChildren(QPushButton)
               if button.isVisible()]
    assert any("every model" in text.lower() for text in buttons), (
        f"nothing here can undo the filter: {buttons}")

    for button in panel.findChildren(QPushButton):
        if button.isVisible() and "every model" in button.text().lower():
            button.click()
            break
    settle()

    assert panel.task_picker.currentData() is None, (
        f"the filter is still {panel.task_picker.currentText()!r}")
    cards = cards_on_page(panel)
    assert len(cards) >= 4, (
        f"widening the filter left {len(cards)} models, so it does not recover "
        "the list it was hiding")


def test_a_device_that_really_is_too_small_is_told_so_with_the_figures(window) -> None:
    """When the hardware genuinely is the limit, the page says so -- and shows why.

    The opposite error to the one above: having stopped blaming the machine for
    a catalogue gap, it would be just as wrong to stop telling somebody whose
    machine really is too small. The figure is what makes it checkable.
    """
    from desktop_app.cookbook import Hardware
    panel = panel_of(window)
    window.show()
    settle()

    starved = Hardware(platform="Linux 6.1", architecture="x86_64", cpu_cores=4,
                       memory_total=2 * 1024 ** 3, memory_available=int(0.4 * 1024 ** 3),
                       disk_free=50 * 1024 ** 3, vram=None, unified_memory=False,
                       sources={})
    panel.hardware = starved
    # Re-rendered explicitly: the filter is already on Everything, so setting
    # it to Everything raises no change and would leave the previous -- and
    # entirely healthy -- list on screen.
    panel.render_models()
    settle()

    said = text_of(panel)
    assert "fits this device" in said, (
        f"a device with 0.4GiB free was not told the truth: {said[-260:]!r}")
    assert human_bytes(starved.budget()) in said, (
        f"the reader is not shown what they have to work with: {said[-260:]!r}")
    # This one is worth widening out of, so the button must not be offered as
    # though it were the fix for a catalogue gap.
    buttons = [button.text() for button in panel.findChildren(QPushButton)
               if button.isVisible()]
    assert not any("every model" in text.lower() for text in buttons), (
        f"widening the filter is offered for a device limit: {buttons}")


def test_a_page_with_a_withdrawn_model_actually_draws(window) -> None:
    """Withdrawn models are still listed, and the list has to survive them.

    The section header binds ``_card()``'s return -- a ``(frame, layout)`` pair
    -- to a single name and then adds to it, so any page with a withdrawn model
    on it raised AttributeError and drew no cards at all. Latent while the
    bundled catalogue holds no withdrawn models, and live as soon as a refresh
    brings one back.
    """
    from dataclasses import replace

    panel = panel_of(window)
    window.show()
    settle()
    base = panel.catalog[0]
    withdrawn = replace(base, deprecated=True,
                        deprecated_reason="superseded by a newer release")
    panel.catalog = [withdrawn] + [m for m in panel.catalog
                                   if m.repo != base.repo]
    panel.render_models()
    settle()

    said = " ".join(text.text() for text in panel.findChildren(QLabel))
    assert "Withdrawn by their publishers" in said, (
        f"the withdrawn section is not on the page: {said[-220:]!r}")
    assert withdrawn.display in said, (
        "the withdrawn model is not on the page")


def test_the_empty_state_refresh_asks_before_it_reaches_the_network(
        window, monkeypatch) -> None:
    """Every button that leaves the machine goes through the disclosure.

    The empty card offered a "Refresh from <host>" of its own, and it was wired
    straight to ``do_refresh`` -- the method the disclosure exists to sit in
    front of. So a reader who filtered to a list with nothing in it could
    trigger a dozen requests to a host they had never been told about, and
    never be asked. The main button above it asked every time.

    Put into the "This device's memory could not be read" state, which is one of
    the three that offer the button and needs no catalogue surgery to reach.
    """
    from desktop_app.cookbook import Hardware
    from desktop_app.dialogs import RefreshDisclosure

    panel = panel_of(window)
    window.show()
    settle()
    panel.hardware = Hardware(
        platform="Linux 6.1", architecture="x86_64", cpu_cores=4,
        memory_total=0, memory_available=0, disk_free=100 * 1024 ** 3,
        vram=None, unified_memory=False, sources={})
    panel.render_models()
    settle()

    said = " ".join(text.text() for text in panel.findChildren(QLabel))
    assert "memory could not be read" in said, (
        f"the panel is not in the empty state this needs: {said[-200:]!r}")

    pending = []
    window.command = lambda name, *args, **kwargs: pending.append(kwargs)

    # The card, not the panel: the footer has its own Refresh button, wired the
    # right way, and finding that would make this pass for the wrong reason.
    card = next((panel.results.itemAt(i).widget()
                 for i in range(panel.results.count())
                 if panel.results.itemAt(i) is not None
                 and panel.results.itemAt(i).widget() is not None), None)
    assert card is not None, "the empty state drew no card"
    button = next(b for b in card.findChildren(QPushButton)
                  if "Refresh from" in b.text())

    # What had been sent by the time the dialog opened. That is the question:
    # a control that asks afterwards has already asked the question.
    on_open = []
    monkeypatch.setattr(
        RefreshDisclosure, "exec",
        lambda self: (on_open.append((list(pending), self.host)),
                      QDialog.DialogCode.Accepted)[1])

    button.click()

    assert len(on_open) == 1, "the empty state's refresh button did not ask"
    sent, host = on_open[0]
    assert not sent, (
        f"{len(sent)} requests had gone out before it asked: "
        f"{[c['repo'] for c in sent][:4]}")
    assert host == "https://huggingface.co", (
        f"the disclosure named the wrong host: {host!r}")


def test_the_empty_state_refresh_respects_a_refusal(window, monkeypatch) -> None:
    """Saying no means no requests, from this button as well as the main one."""
    from desktop_app.cookbook import Hardware
    from desktop_app.dialogs import RefreshDisclosure

    panel = panel_of(window)
    window.show()
    settle()
    panel.hardware = Hardware(
        platform="Linux 6.1", architecture="x86_64", cpu_cores=4,
        memory_total=0, memory_available=0, disk_free=100 * 1024 ** 3,
        vram=None, unified_memory=False, sources={})
    panel.render_models()
    settle()

    monkeypatch.setattr(
        RefreshDisclosure, "exec", lambda self: QDialog.DialogCode.Rejected)
    pending = []
    window.command = lambda name, *args, **kwargs: pending.append(kwargs)

    card = next((panel.results.itemAt(i).widget()
                 for i in range(panel.results.count())
                 if panel.results.itemAt(i) is not None
                 and panel.results.itemAt(i).widget() is not None), None)
    assert card is not None, "the empty state drew no card"
    next(b for b in card.findChildren(QPushButton)
         if "Refresh from" in b.text()).click()
    settle()

    assert not pending, "refusing the disclosure still sent requests"


def test_a_missing_catalogue_offers_no_refresh_button(window) -> None:
    """With nothing to refresh, a button labelled Refresh is a lie.

    Routed through ``ask_to_refresh``, which returns at once when the
    catalogue is empty, the button would have done nothing at all -- and a
    control that always works is worse than one that does not. The card says
    what is wrong and the footer offers Restore previous catalogue, which is
    the thing that can help.
    """
    panel = panel_of(window)
    window.show()
    settle()
    panel.catalog = []
    panel.render_models()
    settle()

    said = " ".join(text.text() for text in panel.findChildren(QLabel))
    assert "No models are available" in said, (
        f"the panel is not in the no-catalogue state: {said[-200:]!r}")

    # The card, not the panel. The footer's own Refresh button stays: it is the
    # control that asks, and it is correct for it to exist whether or not the
    # list has anything in it. It is the *card* that must not offer a refresh
    # it cannot carry out.
    card = next((panel.results.itemAt(i).widget()
                 for i in range(panel.results.count())
                 if panel.results.itemAt(i) is not None
                 and panel.results.itemAt(i).widget() is not None), None)
    assert card is not None, "the empty state drew no card"
    offered = [b.text() for b in card.findChildren(QPushButton)
               if "Refresh from" in b.text()]
    assert not offered, (
        f"there is nothing to refresh, but the card offers {offered}")

    # And what the page does offer instead is the thing that can help.
    restore = [b.text() for b in panel.findChildren(QPushButton)
               if "Restore previous" in b.text()]
    assert restore, (
        "the card stopped offering a refresh and offers no way back either")


def test_pressing_refresh_asks_before_it_reaches_the_network(window, monkeypatch) -> None:
    """The disclosure is asked for at the moment of the action, not found earlier.

    It used to be a muted line above the list and a tooltip on the button: a
    paragraph somebody has to go looking for, about twelve HTTP requests they
    are agreeing to, and the only thing in the app that leaves the machine
    under its own power.
    """
    from desktop_app.dialogs import COOKBOOK_PATHS, RefreshDisclosure

    panel = panel_of(window)
    window.show()
    settle()
    asked = []

    def answer(dialog):
        asked.append(dialog)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(RefreshDisclosure, "exec", lambda self: answer(self))

    pending = []
    window.command = lambda name, *args, **kwargs: pending.append(kwargs)
    panel.refresh.click()
    settle()

    assert len(asked) == 1, "pressing Refresh did not ask first"
    dialog = asked[0]
    said = " ".join(text.text() for text in dialog.findChildren(QLabel))

    # Every part of it checkable before the press.
    assert dialog.host == "https://huggingface.co"
    assert dialog.host in said, f"the dialog never names the host: {said[:300]!r}"
    assert str(len(panel.catalog) * len(COOKBOOK_PATHS)) in said, (
        f"the dialog does not say how many requests this makes: {said[:300]!r}")
    for path in COOKBOOK_PATHS:
        assert f"{dialog.host}{path}" in said, (
            f"the address {path} is never shown: {said[:400]!r}")
    assert "IP address" in said, f"it does not say what is sent: {said[:400]!r}"
    assert "no account" in said.lower(), (
        f"it does not say what is not sent: {said[:400]!r}")
    assert dialog.should_remember() is False, (
        "the box starts ticked, so agreeing once is agreeing for good")
    assert len(pending) == 1, (
        f"it asked {len(pending)} times, so the first request went out before "
        "the reader agreed to it")


def test_cancelling_the_disclosure_requests_nothing(window, monkeypatch) -> None:
    """Saying no has to actually mean no.

    A disclosure that asks and then sends anyway is worse than none: it is the
    same request, now with the app claiming the reader agreed to it.
    """
    from desktop_app.dialogs import RefreshDisclosure

    panel = panel_of(window)
    settle()
    monkeypatch.setattr(RefreshDisclosure, "exec",
                        lambda self: QDialog.DialogCode.Rejected)

    pending = []
    window.command = lambda name, *args, **kwargs: pending.append(kwargs)
    panel.refresh.click()
    settle()

    assert pending == [], f"declining still sent {len(pending)} requests"
    assert not panel._busy, "the page is left marked busy after a refusal"
    assert panel.refresh.isEnabled(), "the button stayed dead after a refusal"
    assert [model.repo for model in panel.catalog], "the list was disturbed"


def test_agreement_is_remembered_per_host_not_once_forever(window, monkeypatch) -> None:
    """A mirror is a different site, so it gets asked about separately.

    Agreeing to tell huggingface.co which models you run is not agreeing to
    tell somebody else, and the mirror is exactly the case where the reader
    most needs to be told which host they are about to hand the list to.
    """
    from desktop_app.dialogs import RefreshDisclosure

    panel = panel_of(window)
    settle()
    shown = []

    def remember_now(self):
        shown.append(self.host)
        self.remember.setChecked(True)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(RefreshDisclosure, "exec", remember_now)

    pending = []

    def refuse_all(name, *args, **kwargs):
        # Answered as it arrives, so the refresh finishes and the page is not
        # left busy -- a second press would otherwise be ignored and the test
        # would pass for the wrong reason.
        pending.append(kwargs)
        kwargs["failure"]("declined in test")

    window.command = refuse_all

    panel.refresh.click()
    settle()
    assert shown == ["https://huggingface.co"], f"asked about {shown}"
    assert panel.consented("https://huggingface.co")

    # Same host, no dialog at all -- that is the point of remembering.
    shown.clear()
    panel.refresh.click()
    settle()
    assert shown == [], f"the same host asked about twice: {shown}"

    # A different host is asked about afresh, even after the first agreement.
    panel.mirror.setText("https://hf-mirror.example.com")
    panel.save_mirror()
    settle()
    panel.refresh.click()
    settle()
    assert shown == ["https://hf-mirror.example.com"], (
        f"a new host was not disclosed: {shown}")
    assert panel.consented("https://hf-mirror.example.com")
    assert panel.consented("https://huggingface.co"), (
        "agreeing to a mirror un-agreed the public host")


def test_a_refused_consent_is_not_left_half_remembered(window, monkeypatch) -> None:
    """If the agreement cannot be saved, it must not look saved.

    Otherwise the reader is not warned again this session and is warned again
    next time, which is worse than not remembering at all.
    """
    from desktop_app.dialogs import RefreshDisclosure

    panel = panel_of(window)
    settle()
    monkeypatch.setattr(RefreshDisclosure, "exec", lambda self: (
        self.remember.setChecked(True),
        QDialog.DialogCode.Accepted)[1])

    def refuse_save():
        raise OSError("the preferences file is not writable")

    monkeypatch.setattr(window.storage, "save", refuse_save)
    window.command = lambda name, *args, **kwargs: None
    panel.refresh.click()
    settle()

    assert not panel.consented("https://huggingface.co"), (
        "an agreement that was never written down is being treated as given")
    assert "cookbook_refresh_ok" not in window.storage.settings
    assert "could not be saved" in panel.catalog_note.text(), (
        f"the reader was not told: {panel.catalog_note.text()!r}")


def test_a_mirror_is_named_as_somebody_elses_server(window, monkeypatch) -> None:
    """The dialog says when the host is not the public catalogue.

    This app cannot vouch for a mirror. What it can do is make sure the reader
    knows that is what they are about to hand the model list to.
    """
    from desktop_app.dialogs import RefreshDisclosure

    panel = panel_of(window)
    settle()
    panel.mirror.setText("https://hf-mirror.example.com")
    panel.save_mirror()
    settle()

    asked = []
    monkeypatch.setattr(RefreshDisclosure, "exec", lambda self: (
        asked.append(self), QDialog.DialogCode.Accepted)[1])
    window.command = lambda name, *args, **kwargs: None
    panel.refresh.click()
    settle()

    said = " ".join(t.text() for t in asked[0].findChildren(QLabel))
    assert "mirror" in said.lower(), (
        f"a mirror is being presented as the public catalogue: {said[:300]!r}")
    assert "hf-mirror.example.com" in asked[0].host


def build_toggles(panel):
    """The controls that reveal a card's builds.

    Matched on the count the label leads with rather than on its wording, so
    neither the phrase changing nor a summary being appended can silently stop
    the test from finding it.
    """
    found = []
    for card in cards_on_page(panel):
        for button in card.findChildren(QPushButton):
            head = button.text().split(" · ")[0]
            if head.endswith(" build") or head.endswith(" builds"):
                if not button.property("buildRow"):
                    found.append(button)
    return found


def build_rows_of(card):
    """The build rows on a card.

    Identified by the dynamic property rather than the objectName, because the
    rows have to keep ``ghost`` as their objectName for the checkable styling
    that marks the chosen one to apply.
    """
    return [child for child in card.findChildren(QPushButton)
            if child.property("buildRow") is True]


def test_every_build_is_reachable_from_its_card(window) -> None:
    """Thirty-one builds sat behind four cards with nothing that reached them.

    A machine with a 4GiB card was shown the 5.1GB four-bit build of a 7B model
    at "possible with compromises" while the 3.6GB two-bit build in the same
    catalogue would have fitted outright, and no control anywhere could have
    told anyone that.
    """
    panel = panel_of(window)
    window.show()
    settle()

    rows = recommend(panel.catalog, panel.hardware)
    assert rows, "nothing was recommended, so this proves nothing"
    expected = sum(len(row["model"].variants) for row in rows)
    assert expected > 0, "the catalogue holds no builds at all"

    toggles = build_toggles(panel)
    assert toggles, "no card offers its builds"
    offered = sum(int(text.split()[0]) for text in
                  (button.text() for button in toggles))
    assert offered == expected, (
        f"the cards offer {offered} builds and there are {expected}; some are "
        "still unreachable")

    # Collapsed until asked, so a list of what fits stays comparable.
    for button in toggles:
        assert not button.isChecked(), "the section starts open"
    card = toggles[0].parent()
    # The rows are built as soon as the card is, inside a hidden container --
    # so this is a question about visibility, not about which widgets exist.
    assert build_rows_of(card), "the card has no build rows in it at all"
    assert not any(row.isVisible() for row in build_rows_of(card)), (
        "the builds are on screen before anyone asked for them")

    toggles[0].click()
    settle()
    assert build_rows_of(card), "pressing the control did not show any build"


def test_choosing_a_build_changes_what_the_card_says_and_how_to_run_it(window) -> None:
    """The whole point: an offered build has to be usable.

    It used to be a list you could read and not take. The page said a 7B model
    was "possible with compromises" at four-bit, offered the two-bit build in
    the row below it, and then printed a command for the four-bit one.

    The verdict was always worked out per build by ``build_rows``; only one of
    them was ever drawn. So choosing one updates the badge, the figures and the
    command together, because they are all reading the same entry now.
    """
    panel = panel_of(window)
    window.show()
    settle()
    toggles = build_toggles(panel)
    assert toggles
    toggles[0].click()
    settle()

    card = toggles[0].parent()
    rows = {row.property("buildVariant"): row for row in build_rows_of(card)}
    assert len(rows) > 1, "the card offers no choice of build"

    def card_says():
        card = cards_on_page(panel)[0]
        badge = [label for label in card.findChildren(QLabel)
                 if label.objectName().startswith("verdict")]
        facts = [label for label in card.findChildren(QLabel)
                 if label.objectName() == "meta"]
        command = [label for label in card.findChildren(QLabel)
                   if label.objectName() == "command"]
        return (badge[0].text() if badge else "",
                card.objectName(),
                facts[0].text() if facts else "",
                command[0].text() if command else "")

    before = card_says()
    # Choose a build that is not the one currently on show. Read the current
    # build off the command rather than out of the figures prose, so this
    # keeps working if that line is reworded.
    current = before[3].splitlines()[0].rsplit(":", 1)[-1]
    target = next(row for row in rows.values()
                  if row.property("buildVariant") != current)
    target_name = target.property("buildVariant")
    target.click()
    settle()

    after = card_says()
    assert after[2] != before[2], f"the figures did not change: {after[2]!r}"
    assert target_name in after[2], (
        f"the card still describes another build: {after[2]!r}")
    assert f":{target_name}" in after[3].splitlines()[0], (
        f"the command is for a different build: {after[3]!r}")
    # Choosing a build re-draws the card, so the old widget is gone; the table
    # has to still be open afterwards, because the reader was standing in it.
    card = cards_on_page(panel)[0]
    checked = [row.property("buildVariant") for row in build_rows_of(card)
               if row.isChecked()]
    assert checked == [target_name], (
        f"more than one row is chosen, or the wrong one: {checked}")
    assert build_toggles(panel)[0].isChecked(), (
        "the build table closed the moment a build was chosen")


def test_the_build_the_card_is_using_is_shown_as_recommended(window) -> None:
    """It used to be excluded from the list, so the table never said what was in use.

    A reader expanding a card saw six or seven builds and no indication of which
    one the card was actually describing above them.
    """
    panel = panel_of(window)
    window.show()
    settle()
    toggles = build_toggles(panel)
    assert toggles
    toggles[0].click()
    settle()

    card = toggles[0].parent()
    rows = build_rows_of(card)
    assert rows
    marked = [row for row in rows
              if any(label.text() == "recommended"
                     for label in row.findChildren(QLabel))]
    assert len(marked) == 1, (
        f"expected exactly one row marked as the recommended build, found "
        f"{len(marked)}")

    # It is marked and it is the one chosen by default.
    chosen = [row.property("buildVariant") for row in rows if row.isChecked()]
    assert chosen == [marked[0].property("buildVariant")], (
        f"the marked build is {marked[0].property('buildVariant')} but the card "
        f"is showing {chosen}")

    command = [label for label in card.findChildren(QLabel)
               if label.objectName() == "command"][0].text()
    assert f":{chosen[0]}" in command.splitlines()[0], (
        f"the recommended row is not the build being run: {command!r}")


def test_a_long_build_list_is_capped_and_says_so(window) -> None:
    """Twenty-three builds made a card over a thousand pixels tall.

    Eight are shown and the rest are counted, not silently dropped. The ones
    beyond are the largest, because the table is smallest first, which is what
    makes the truncation drop the builds least likely to be wanted here.

    It says they are not shown rather than that this device cannot run them.
    The cap is a property of the table, and the two are not the same thing:
    a machine with no graphics card and memory to spare can run a build well
    past the eighth, and a page asserting otherwise is making a claim about
    somebody's hardware that nothing here checked.
    """
    from dataclasses import replace

    from desktop_app.cookbook_panel import BUILDS_SHOWN

    panel = panel_of(window)
    window.show()
    settle()

    # A catalogue with far more builds than the card can usefully show.
    base = panel.catalog[0]
    extra = tuple(base.variants) + tuple(
        replace(v, name=f"extra-{index}.gguf", size=(index + 1) * 100_000_000)
        for index, v in enumerate(base.variants))
    wide = replace(base, variants=extra)
    assert len(wide.variants) > BUILDS_SHOWN, "the fixture is not wide enough"
    panel.catalog = [wide]
    panel.render_models()
    settle()

    toggles = build_toggles(panel)
    toggles[0].click()
    settle()
    card = toggles[0].parent()

    rows = build_rows_of(card)
    assert len(rows) == BUILDS_SHOWN, (
        f"{len(rows)} rows shown, expected the cap of {BUILDS_SHOWN}")
    said = " ".join(label.text() for label in card.findChildren(QLabel))
    assert "and " in said and "larger build" in said, (
        f"the hidden builds are not counted: {said[-260:]!r}")
    assert "not shown" in said, (
        f"the note does not say the omitted builds are only not shown: "
        f"{said[-260:]!r}")
    assert "cannot run" not in said, (
        f"the note claims this device cannot run the omitted builds, which "
        f"nothing established: {said[-260:]!r}")


def test_a_build_chosen_then_forgotten_falls_back_rather_than_breaking(window) -> None:
    """The choice is resolved every time, never trusted from memory.

    A refresh or a rollback can replace the catalogue underneath, and a
    remembered build would then be a command naming a file that is not there.
    """
    panel = panel_of(window)
    window.show()
    settle()
    panel._chosen[panel.catalog[0].repo] = "Q8_0"
    panel.render_models()
    settle()

    # Ask about the same repository the choice was stored for. The engine
    # reorders the list, so catalog[0] is not the card on page one.
    row = next(r for r in recommend(panel.catalog, panel.hardware)
               if r["model"].repo == panel.catalog[0].repo)
    entry = panel._chosen_entry(row)
    assert entry is not None, "the card lost its build entirely"
    assert entry["variant"].quantization == "Q8_0"

    panel._chosen[panel.catalog[0].repo] = "not-a-build"
    entry = panel._chosen_entry(row)
    assert entry is not None
    assert entry["selected"], (
        "an unknown build was not replaced by the engine's recommendation")


def test_a_build_that_spills_is_marked_as_such_and_not_counted_as_on_the_card(window) -> None:
    """"Possible with compromises" fits, but it does not run on the card.

    Calling those builds "fits" claimed six of seven ran on the graphics card
    when one did -- which is the difference between a fast model and one running
    on the processor.

    Measured against a stated device rather than the one the test happens to
    run on. It used to use the panel's detected hardware, so which builds came
    out as spilling depended on how much memory was free when the page was
    built: when the machine was busier the budget fell, the big builds stopped
    merely spilling and went over budget altogether, and the fixture that was
    supposed to have spilling builds quietly had none.
    """
    from desktop_app.cookbook import Hardware, build_rows, load_catalog
    import re

    panel = panel_of(window)
    window.show()
    settle()
    # 4GiB card, plenty of RAM: so builds above the card's size fit system
    # memory and spill rather than being ruled out, which is the case here.
    panel.hardware = Hardware(
        platform="Linux 6.1", architecture="x86_64", cpu_cores=16,
        memory_total=32 * 1024 ** 3, memory_available=28 * 1024 ** 3,
        disk_free=500 * 1024 ** 3, vram=4 * 1024 ** 3, unified_memory=False,
        sources={})
    panel.render_models()
    settle()

    coder = next(model for model in load_catalog() if "Coder" in model.name)
    entries = build_rows(coder, panel.hardware)
    spilling = [entry for entry in entries if entry["spills"]]
    assert spilling, "this fixture needs builds that overrun the card"

    alternatives = [entry for entry in entries if not entry["selected"]]
    on_card = [entry for entry in alternatives
               if entry["verdict"]["result"] in ("likely to run",
                                                 "verified compatible")]
    assert on_card, "this fixture needs at least one build that fits the card"
    assert len(on_card) < len(alternatives), (
        "this fixture needs some builds that do not fit the card")

    # The counts are over every build the card shows, which is every build the
    # model has -- the recommended one is in the table now, not beside it.
    counted_on_card = [entry for entry in entries
                       if entry["verdict"]["result"] in ("likely to run",
                                                         "verified compatible")]

    # The number on that model's own control has to be the number that fits.
    coder_card = next(
        card for card in cards_on_page(panel)
        if coder.display in " ".join(label.text()
                                     for label in card.findChildren(QLabel)))
    toggle = next(button for button in coder_card.findChildren(QPushButton)
                  if re.fullmatch(r"\d+ builds", button.text()))
    found = re.search(r"(\d+) run on the graphics card", toggle.toolTip())
    assert found, (
        f"the card does not say how many builds run on the card: "
        f"{toggle.toolTip()!r}")
    assert int(found.group(1)) == len(counted_on_card), (
        f"the control claims {found.group(1)} builds run on the card, and "
        f"{len(counted_on_card)} do")
    assert f"{len(spilling)} spill to the CPU" in toggle.toolTip(), (
        f"the card does not admit which builds spill: {toggle.toolTip()!r}")


def test_the_builds_section_never_shows_builds_that_are_gone(window) -> None:
    """A refresh or a filter change replaces the catalogue underneath.

    What must not happen is a table left showing a set of builds that is no
    longer true. That used to be prevented by closing the section, which also
    threw away the reader's place every time the list was rebuilt.

    It is now prevented by keying the open section to a repository and
    regenerating its contents from whatever the catalogue holds at the moment
    of drawing. So this checks the thing that actually matters -- that what is
    on screen is what the catalogue says -- rather than that a button is in a
    particular position.
    """
    panel = panel_of(window)
    window.show()
    settle()
    toggles = build_toggles(panel)
    assert toggles
    toggles[0].click()
    settle()
    assert toggles[0].isChecked()

    panel.task_picker.setCurrentIndex(panel.task_picker.findData("coding"))
    settle()

    for model in panel.catalog:
        card = next((card for card in cards_on_page(panel)
                     if model.display in " ".join(label.text()
                                                  for label in
                                                  card.findChildren(QLabel))),
                    None)
        if card is None:
            continue
        listed = [row.property("buildVariant") for row in build_rows_of(card)]
        if not listed:
            continue
        current = [v.quantization or v.name for v in model.variants]
        assert set(listed) <= set(current), (
            f"{model.repo} lists {listed}, which the catalogue no longer "
            f"has: {current}")
        # And the table is drawn from the model it claims to be, smallest
        # first, rather than carried over from the previous catalogue.
        assert listed == [q for q in current if q in listed]


def test_rebuilding_the_page_never_puts_a_window_on_screen(window) -> None:
    """Tearing a card down must not promote it to a top-level window.

    It did. The teardown called ``setParent(None)`` before scheduling the
    deletion, which detaches a card from the panel -- and also makes it a
    window. Measured, ``isWindow()`` goes from False to True across a rebuild.
    A window is a rectangle with a title bar, which is what a small white box
    appearing over the app is, and every card on screen did it on every
    re-render.

    ``window.clear_layout`` hides and then schedules the deletion instead, and
    that is the only recipe used anywhere else in this app.
    """
    panel = panel_of(window)
    window.show()
    settle()

    old = cards_on_page(panel)
    assert old, "no cards to tear down"
    assert any(card.isVisible() for card in old), (
        "a check found no visible card, so this proves nothing")
    assert not any(card.isWindow() for card in old), (
        "the cards are already windows before anything is torn down")

    panel.render_page()

    promoted = [card.objectName() or type(card).__name__ for card in old
                if card.isWindow()]
    assert not promoted, (
        f"re-rendering promoted {promoted} to top-level windows")
    assert all(not card.isVisible() for card in old), (
        "a torn-down card is still on screen")


def test_emptying_the_filter_draws_the_page_once(window) -> None:
    """Widening the filter used to draw everything twice over.

    ``setCurrentIndex`` already fires ``currentIndexChanged``, which is wired
    to the render. Calling it as well meant every card was built and torn down
    twice per click, which is what multiplied the stray windows into more than
    one at a time.
    """
    panel = panel_of(window)
    window.show()
    settle()
    panel.task_picker.setCurrentIndex(panel.task_picker.findData("vision"))
    settle()

    calls = []
    original = panel.render_page
    panel.render_page = lambda: (calls.append(1), original())
    try:
        panel.show_everything()
        settle()
    finally:
        panel.render_page = original

    assert len(calls) == 1, (
        f"one press of Show every model drew the page {len(calls)} times")
    assert panel.task_picker.currentData() is None, (
        f"the filter is still {panel.task_picker.currentText()!r}")
    assert cards_on_page(panel), "widening the filter left the page empty"


def test_the_page_title_is_not_followed_by_a_paragraph(window) -> None:
    """Nothing sits between "Cookbook" and the device card.

    The subtitle used to say the page sizes against this device's memory and
    that Refresh asks first. Both are true, both are said again where they are
    acted on -- the device card above, and the dialog the Refresh control opens
    -- and neither earns the gap it put between the title and the first thing
    on the page that a reader actually came for.
    """
    panel = panel_of(window)
    window.show()
    settle()

    texts = [child.text() for child in panel.findChildren(QLabel) if child.text()]
    assert "WHICH MODELS FIT THIS MACHINE" in texts, (
        "the page lost its eyebrow")
    assert "Cookbook" in texts, "the page lost its title"
    assert not any("Sized against this device" in text for text in texts), (
        "the subtitle under the title is still there")


def test_changing_the_filter_does_not_rebuild_under_the_picker(window) -> None:
    """A change to "Looking for" rebuilds the page after the picker has let go.

    The picker is being dismissed from its own dropdown when ``currentIndex``
    fires, so rebuilding inside the handler drew the new list underneath a popup
    that had not closed yet -- and left that dropdown up, describing a list it
    had just been replaced by. The same deferral the "Show every model" button
    needs, for the same reason.
    """
    panel = panel_of(window)
    window.show()
    settle()
    index = panel.task_picker.findData("coding")
    assert index >= 0, "there is no Coding filter to pick"

    calls = []
    original = panel.render_page
    panel.render_page = lambda: (calls.append(1), original())
    try:
        panel.task_picker.setCurrentIndex(index)
        assert not calls, (
            "the page was rebuilt while the picker was still handling the change")
        settle()
        assert calls == [1], (
            f"one filter change drew the page {len(calls)} times")
    finally:
        panel.render_page = original

    assert panel.task_picker.currentData() == "coding", (
        "the filter did not take")


def test_choosing_a_build_waits_for_the_click_and_leaves_the_page_alone(
        window) -> None:
    """Picking a quantisation must not rebuild under the row, nor move the page.

    The row that was pressed is inside the card being replaced, so doing it
    inside the click is the same hazard as the "Show every model" button: a
    widget still processing its own click is torn down underneath itself. So the
    work waits a turn, which is the part that bites -- an earlier version of
    this rebuilt inside the click and this caught it.

    The scroll assertion is a guard rather than a reproduction. Rebuilding drops
    the scroll bar, and the builds are the last thing on a card, so somebody
    choosing between eight quantisations has scrolled down to do it and landing
    at the top again makes them scroll back for every one. Qt did happen to
    preserve the value in practice, so this is pinning behaviour that should not
    be left to Qt's discretion -- but it is not, on its own, a demonstration
    that anything was broken.
    """
    panel = panel_of(window)
    # Short enough that the list cannot all fit, which is the case that matters.
    window.resize(1200, 420)
    window.show()
    settle()

    toggles = build_toggles(panel)
    assert toggles, "no card offers its builds"
    toggles[0].click()
    settle()

    rows = [row for row in build_rows_of(toggles[0].parent())
            if not row.isChecked()]
    assert rows, "no build is offered as a choice"

    bar = panel.scroller.verticalScrollBar()
    assert bar.maximum() > 0, (
        "the page fits entirely, so this proves nothing about scrolling")
    bar.setValue(bar.maximum() // 2)
    settle()
    where = bar.value()
    assert where > 0, "could not scroll away from the top to test this"

    card = toggles[0].parent()
    repo = next(row["model"].repo
                for row in recommend(panel.catalog, panel.hardware))
    # Read before the click: the row is expected to survive it, and if it does
    # not we want a plain assertion saying so rather than a dead widget.
    wanted = rows[0].property("buildVariant")

    rows[0].click()
    # Still inside the click's own call stack: nothing has been touched yet.
    assert panel._cards.get(repo) is card, (
        "the card was replaced while the pressed row was still handling the "
        "click that chose it")
    assert card.parent() is not None, (
        "the card holding the pressed row was detached mid-click, which is "
        "what promotes it to a top-level window")
    assert not card.isWindow(), (
        "the card holding the pressed row became a window mid-click")
    settle()

    # And it is the same card afterwards, because the card does not change --
    # only the three lines of it that follow the build. Replacing it would
    # repaint all thirty-five labels to move three of them.
    assert panel._cards.get(repo) is card, (
        "the card was replaced, so the whole thing repaints")
    assert panel._chosen.get(repo) == wanted, (
        "the card still points at the build it was showing, so the choice did "
        f"not take: {panel._chosen}")
    assert bar.value() == where, (
        f"choosing a build moved the page from {where} to {bar.value()}; the "
        "reader has to scroll back down to try the next one")


def _nothing_fits(panel):
    """Put a device on the page that no catalogued model can run on.

    Used rather than a task filter, because which models fit depends on the
    machine the tests happen to be running on. An earlier version of these two
    tests emptied the list with the "vision" filter and passed for the wrong
    reason on a machine with memory to spare: every vision-capable model fit,
    the list never emptied, and it compared a page with itself.
    """
    from desktop_app.cookbook import Hardware

    panel.hardware = Hardware(
        platform="Linux 6.1", architecture="x86_64", cpu_cores=1,
        memory_total=256 * 1024 ** 2, memory_available=128 * 1024 ** 2,
        disk_free=16 * 1024 ** 2, vram=None, unified_memory=False, sources={})
    panel.render_models()
    settle()


def test_an_empty_list_looks_like_a_full_one(window) -> None:
    """Whether anything is in the list must not change what the page looks like.

    The page had no trailing stretch, so a short page -- which an empty list
    makes it -- had space to spare, and a vertical box with no stretch hands
    that spare space to every row rather than pooling it at the end. Measured
    with the list empty: the eyebrow 14px -> 104px, the title 42 -> 105, the
    device card 86 -> 104, the catalogue note 18 -> 104. Ninety pixels of dead
    space above and below every element, which made an empty filter read as a
    different page rather than the same one with nothing on it.
    """
    panel = panel_of(window)
    window.resize(1200, 900)
    window.show()
    settle()

    def heights():
        outer = panel.layout().itemAt(0).widget()
        page_layout = outer.widget().layout()
        # The eyebrow, the title and the device card are the first three things
        # in the page layout and are built once in __init__, so their position
        # is fixed. What changes with the list is whether the page has spare
        # room, and these are what absorbs it.
        return [page_layout.itemAt(i).widget().geometry().height()
                for i in range(3)]

    full = heights()

    _nothing_fits(panel)
    assert not panel._cards, "the list did not empty, so there is nothing to compare"
    empty = heights()

    assert empty == full, (
        "the page is laid out differently when the list is empty:\n"
        f"  with models: {full}\n  empty     : {empty}")

def test_the_empty_page_is_not_wider_than_a_scrolling_one(window) -> None:
    """Pins the sideways shift that reserving the scrollbar would fix.

    The scrollbar only takes its 7px when there is something to scroll, so the
    page is 7px wider with an empty list than with four models in it, and
    everything on it moves sideways as the list fills or empties.

    An always-on scroll bar reserves the width and removes the shift, and was
    tried. It is not what this page wants: with nothing to scroll, Qt paints the
    thumb filling the entire groove -- measured 1026px of solid #4e5058 down the
    side of a page that cannot scroll, where there was 7px of quiet track before.
    Trading a small shift for a permanent stripe down the page is the worse
    defect, so the shift stands until the thumb can be hidden independently of
    the track.

    If this ever starts failing it means the page got a gutter, and the two
    assertions below say what it cost.
    """
    panel = panel_of(window)
    window.resize(1200, 900)
    window.show()
    settle()

    bar = panel.scroller.verticalScrollBar()
    assert bar.maximum() > 0, "the page does not scroll, so there is no bar"
    content = panel.layout().itemAt(0).widget().widget()
    scrolling = content.width()

    _nothing_fits(panel)
    assert not panel._cards, "the list did not empty, so there is nothing to compare"
    empty = content.width()

    assert empty > scrolling, (
        f"the page is {scrolling}px with models and {empty}px empty; the gutter "
        "has been reserved, so check the thumb is not painted on an unscrolled "
        "page before keeping it")


def test_choosing_a_build_changes_one_card_not_the_page(window) -> None:
    """Choosing a build must not empty the list and refill it.

    Rebuilding the page destroys every card before building any, so the list
    collapses and refills on every click -- and the card being destroyed is the
    one the pointer is resting on, which is how a row that should be part of a
    card briefly becomes a window of its own. A parentless widget is a window
    in Qt, with the app icon and the minimise, maximise and close buttons.

    This checks the order the two happen in, not that the page still has
    content: a rebuild in the right order would satisfy anything less.
    """
    panel = panel_of(window)
    window.show()
    settle()
    toggles = build_toggles(panel)
    assert toggles, "no card offers its builds"
    toggles[0].click()
    settle()

    card = toggles[0].parent()
    repo = next(row["model"].repo
                for row in recommend(panel.catalog, panel.hardware))
    assert panel._cards.get(repo) is card, (
        "the panel is not tracking the card it drew, so it cannot change it")

    rows = [row for row in build_rows_of(card) if not row.isChecked()]
    assert rows, "no build is offered as a choice"

    calls = []
    original = panel.render_page
    panel.render_page = lambda: (calls.append(1), original())
    try:
        rows[0].click()
        settle()
    finally:
        panel.render_page = original

    assert not calls, (
        "choosing a build rebuilt the whole page, which empties the list before "
        "refilling it and destroys the card the pointer is resting on")
    kept = panel._cards.get(repo)
    assert kept is not None and kept is card, (
        "the card was replaced, so every label on it is a new widget and the "
        "whole thing repaints to show a change in three lines of text")
    assert panel.results.indexOf(kept) >= 0, "the card is not in the list"


def test_choosing_a_build_never_draws_two_different_cards(window) -> None:
    """The card flickered because it was drawn twice, showing two choices.

    A checkable button toggles itself as part of the click, before the handler
    that rebuilds the card gets to run. With the rows merely checkable, clicking
    a new build checked it while the one it replaced was *still* checked, and a
    turn later the card was redrawn with only the new one chosen. Two pictures
    of one card, milliseconds apart -- and only that card, because nothing else
    on the page was touched.

    Exclusive rows settle it in one step: the click unchecks the old row and
    checks the new one together, so what the button draws is already what the
    card is about to say.

    Checked by comparing the card as it stands immediately after the click
    against the card once it has settled, which is the comparison the flicker
    was.
    """
    panel = panel_of(window)
    window.show()
    settle()

    def card_with_rows():
        # Re-fetched every time: choosing a build may fall back to replacing the
        # card, and a reference held across a click would be a deleted widget.
        for card in cards_on_page(panel):
            if build_rows_of(card):
                return card
        return None

    card = card_with_rows()
    assert card, "no card shows a build table"
    next(b for b in card.findChildren(QPushButton)
         if b.property("buildRow") is None).click()
    settle()

    def chosen(wanted):
        return [row.property("buildVariant") for row in build_rows_of(wanted)
                if row.isChecked()]

    for target in ("Q2_K", "Q8_0", "Q4_K_M"):
        card = card_with_rows()
        row = next((r for r in build_rows_of(card)
                    if r.property("buildVariant") == target), None)
        if row is None:
            continue
        row.click()
        during = chosen(card)       # inside the click, before the card settles
        settle()
        assert during == chosen(card_with_rows()), (
            f"picking {target} drew {during} and then drew {chosen(card_with_rows())}"
            ", so the card was painted twice showing two different choices")
        assert during == [target], f"{during} is drawn after picking {target}"


def test_only_the_labels_that_changed_are_touched(window) -> None:
    """Three labels of thirty-five, and the other thirty-two are the same objects.

    The card used to be destroyed and rebuilt for a choice that alters three
    lines of it, so every label was a new widget and the whole card repainted.
    Measured on this catalogue: the figures line, the reason and the command
    change; the badge joins them when the verdict moves; the title, "Run it",
    the provenance, the disclaimer and all eight build rows do not.

    Checked by identity as well as by text, because identical text in a
    rebuilt card is not what the reader was spared.
    """
    panel = panel_of(window)
    window.show()
    settle()

    def card_with_rows():
        for card in cards_on_page(panel):
            if build_rows_of(card):
                return card
        return None

    card = card_with_rows()
    assert card, "no card shows a build table"
    next(b for b in card.findChildren(QPushButton)
         if b.property("buildRow") is None).click()
    settle()

    def snapshot(wanted):
        return [(l, l.objectName(), l.text())
                for l in wanted.findChildren(QLabel)]

    # Two builds of different sizes, so the figures really do move.
    targets = [r.property("buildVariant") for r in build_rows_of(card)]
    first, second = targets[0], targets[-1]

    card = card_with_rows()
    before = snapshot(card)
    next(r for r in build_rows_of(card)
         if r.property("buildVariant") == first).click()
    settle()
    card = card_with_rows()
    assert snapshot(card)[0][0] is before[0][0], "the card itself was replaced"

    settled = snapshot(card)
    next(r for r in build_rows_of(card)
         if r.property("buildVariant") == second).click()
    settle()
    after = snapshot(card_with_rows())

    assert [a[0] for a in settled] == [a[0] for a in after], (
        "the labels were replaced rather than re-set, so the card repainted "
        "in full")
    changed = [i for i in range(len(settled))
               if settled[i][1:] != after[i][1:]]
    assert len(changed) <= 4, (
        f"{len(changed)} of {len(settled)} labels changed for a change of "
        f"build; only the figures, reason and command should: "
        f"{[settled[i][1] or '(reason)' for i in changed]}")
    assert changed, "nothing changed at all, so this proves nothing"


def test_the_card_border_moves_only_when_the_verdict_does(window) -> None:
    """The border colour is the card's object name, and it is not cheap.

    Changing it re-polishes the card, which repaints all of it -- so it is only
    worth doing when the reader's assessment has genuinely moved. Two builds in
    the same verdict class must leave it alone; one in a different class must
    change it, because that is a real difference the reader needs to see.
    """
    panel = panel_of(window)
    window.show()
    settle()

    def card_with_rows():
        for card in cards_on_page(panel):
            if build_rows_of(card):
                return card
        return None

    card = card_with_rows()
    assert card, "no card shows a build table"
    repo = next(row["model"].repo for row in recommend(panel.catalog, panel.hardware))
    next(b for b in card.findChildren(QPushButton)
         if b.property("buildRow") is None).click()
    settle()

    entries = {entry["variant"].quantization: entry["verdict"]["result"]
               for row in recommend(panel.catalog, panel.hardware)
               if row["model"].repo == repo
               for entry in row["builds"]}
    same = next((q for q, v in entries.items()
                 if v == entries[[k for k in entries][0]]), None)
    other = next((q for q, v in entries.items() if v != entries[same]), None)
    assert same and other, (
        f"this catalogue cannot move the verdict, so it proves nothing: "
        f"{entries}")

    def choose(name):
        card = card_with_rows()
        next(r for r in build_rows_of(card)
             if r.property("buildVariant") == name).click()
        settle()
        return card_with_rows()

    def border(card):
        """The colour the card actually draws, not the name it was given.

        The two came apart for a while: setObjectName changed the name and Qt
        never re-matched the selector against it, so the card was called
        modelCardBad and still drew a green border. Asserting on the name alone
        passed the whole time the reader was being told the opposite.
        """
        image = card.grab().toImage()
        return [image.pixelColor(x, image.height() // 2).name() for x in range(4)]

    def badge_colour(card):
        return [label.palette().color(label.foregroundRole()).name()
                for label in card.findChildren(QLabel)
                if label.objectName().startswith("verdict")]

    first = choose(same)
    name_before = first.objectName()
    colour_before = border(first)
    badge_before = badge_colour(first)
    second = choose(other)
    assert second.objectName() != name_before, (
        f"the verdict moved to {entries[other]} but the card border stayed "
        f"{name_before}")
    assert border(second) != colour_before, (
        f"the card is now named {second.objectName()} and still draws the "
        f"border of {name_before}: {colour_before} -> {border(second)}")
    assert badge_colour(second) != badge_before, (
        f"the badge kept the colour of the verdict it had just been told had "
        f"changed: {badge_before} -> {badge_colour(second)}")

    back = choose(same)
    assert back.objectName() == name_before, (
        f"moving back to {entries[same]} left the border at "
        f"{back.objectName()}")
    assert border(back) == colour_before, (
        f"moving back to {entries[same]} left the border drawing {border(back)} "
        f"where it started at {colour_before}")


class _ShowSpy:
    """Context manager collecting parentless ``setVisible(True)`` calls.

    Patches the class rather than an instance, because the widget that was
    doing it -- the build table's detail container -- is a bare ``QWidget()``
    that exists only for the length of one call, so there is nothing to wrap.

    Checked against the argument rather than the resulting state: a parentless
    widget already reports ``isWindow()`` before it is ever shown, so the
    question is not "is this a window" but "was this made visible before
    anything adopted it".
    """

    def __init__(self):
        self.hits = []

    def __enter__(self):
        from PySide6.QtWidgets import QWidget

        self._original = QWidget.setVisible

        def spy(widget, visible):
            if visible and widget.parent() is None:
                try:
                    self.hits.append(type(widget).__name__)
                except RuntimeError:
                    pass
            return self._original(widget, visible)

        QWidget.setVisible = spy
        return self

    def __exit__(self, *exc):
        from PySide6.QtWidgets import QWidget

        QWidget.setVisible = self._original
        return False


def test_a_rebuild_never_shows_a_widget_that_has_no_parent(window) -> None:
    """This is the stray window: the build table, shown before it had a parent.

    ``detail`` is a bare ``QWidget()`` and it was shown before
    ``column.addWidget(detail)`` put it in a layout. A widget with no parent
    *is* a window in Qt, so showing one hands it the app icon and the
    minimise, maximise and close buttons -- which is exactly what was being
    reported, as a small window over the page for a few milliseconds every time
    the list was redrawn.

    It was unreproducible by looking, because the widget is parentless only
    between the two calls and is gone before anything repaints. Polling
    ``topLevelWidgets()`` afterwards finds nothing; the ``setVisible`` call
    itself is the only place the mistake is visible.

    It fires on every rebuild of every card, which is why the same window
    appeared over the filter, over a build row, and on a refresh -- and why it
    came only once per option: re-picking an option that is already active
    rebuilds nothing, so there is nothing to show a window for.
    """
    panel = panel_of(window)
    window.show()
    settle()

    toggles = build_toggles(panel)
    assert toggles, "no card offers its builds"

    # With nothing expanded there is nothing to catch, and the spy must be
    # trusted to say so.
    with _ShowSpy() as spy:
        panel.render_page()
        settle()
    assert spy.hits == [], (
        f"a rebuild with no table open showed {spy.hits} while parentless")

    toggles = build_toggles(panel)
    toggles[0].click()
    settle()
    assert panel._open, "the table did not register as open"

    with _ShowSpy() as spy:
        panel.render_page()
        settle()
    assert spy.hits == [], (
        "a rebuild showed these widgets before anything parented them, so "
        f"each was briefly a window of its own: {spy.hits}")


def test_a_rebuild_after_choosing_a_build_shows_no_window(window) -> None:
    """The build path builds nothing now, so this can only ever be empty.

    Kept as a cheap standing check that choosing a build has not quietly gone
    back to constructing a card: the card is updated in place, so no widget on
    it is ever parentless. If the optimisation is undone, this catches it
    alongside the card-identity test rather than on its own.
    """
    panel = panel_of(window)
    window.show()
    settle()
    toggles = build_toggles(panel)
    assert toggles, "no card offers its builds"
    toggles[0].click()
    settle()

    card = toggles[0].parent()
    repo = next(row["model"].repo for row in recommend(panel.catalog, panel.hardware))
    rows = [row for row in build_rows_of(card) if not row.isChecked()]
    assert rows, "no build is offered as a choice"

    with _ShowSpy() as spy:
        rows[0].click()
        settle()
    assert spy.hits == [], (
        "choosing a build built widgets that had no parent, so it is not "
        f"updating the card after all: {spy.hits}")
    assert panel._cards.get(repo) is card, (
        "the card was replaced, so the in-place update did not happen")


def test_a_change_of_filter_shows_no_window(window) -> None:
    """And on the path that rebuilds the whole page, with a table open."""
    panel = panel_of(window)
    window.show()
    settle()
    toggles = build_toggles(panel)
    toggles[0].click()
    settle()

    index = panel.task_picker.findData("coding")
    assert index >= 0, "there is no Coding filter to pick"
    with _ShowSpy() as spy:
        panel.task_picker.setCurrentIndex(index)
        settle()
    assert spy.hits == [], (
        "changing the filter showed these widgets while they had no parent: "
        f"{spy.hits}")


def test_choosing_a_build_never_draws_two_different_cards(window) -> None:
    """The card flickered because it was drawn twice, showing two choices.

    A checkable button toggles itself as part of the click, before the handler
    that rebuilds the card gets to run. With the rows merely checkable, clicking
    a new build checked it while the one it replaced was *still* checked, and a
    turn later the rebuild drew the card with only the new one chosen. Two
    pictures of one card, milliseconds apart -- and only that card, because
    nothing else on the page was touched.

    Exclusive rows settle it in one step: the click unchecks the old row and
    checks the new one together, so what the button draws is already what the
    rebuild is about to draw.

    Checked by comparing the card as it stands immediately after the click
    against the card after the rebuild, which is the comparison the flicker was.
    """
    panel = panel_of(window)
    window.show()
    settle()

    def card_with_rows():
        # Re-fetched every time: choosing a build replaces the card, so any
        # reference held across a click is a deleted widget.
        for card in cards_on_page(panel):
            if build_rows_of(card):
                return card
        return None

    card = card_with_rows()
    assert card, "no card shows a build table"
    toggle = next(b for b in card.findChildren(QPushButton)
                  if b.property("buildRow") is None)
    toggle.click()
    settle()

    def chosen(wanted):
        return [row.property("buildVariant") for row in build_rows_of(wanted)
                if row.isChecked()]

    for target in ("Q2_K", "Q8_0", "Q4_K_M"):
        card = card_with_rows()
        row = next((r for r in build_rows_of(card)
                    if r.property("buildVariant") == target), None)
        if row is None:
            continue
        row.click()
        during = chosen(card)     # inside the click, before the rebuild runs
        settle()
        after = chosen(card_with_rows())
        assert during == after, (
            f"picking {target} drew {during} and then drew {after}, so the "
            "card was painted twice showing two different choices")
        assert during == [target], f"{during} is drawn after picking {target}"


def test_choosing_the_build_already_shown_does_not_clear_it(window) -> None:
    """Clicking the row that is already chosen must leave it chosen.

    A checkable button clears itself when clicked, and this handler returns
    without rebuilding because the build is already the one being shown -- so
    the row went blank and stayed blank, with nothing to put it back. A reader
    pressing the selected row again to be sure was told, silently, that nothing
    was selected.

    Exclusive buttons cannot be unchecked by clicking them, which is what stops
    the button from contradicting the card it is drawn on.
    """
    panel = panel_of(window)
    window.show()
    settle()

    def card_with_rows():
        for card in cards_on_page(panel):
            if build_rows_of(card):
                return card
        return None

    card = card_with_rows()
    assert card, "no card shows a build table"
    next(b for b in card.findChildren(QPushButton)
         if b.property("buildRow") is None).click()
    settle()

    def chosen():
        card = card_with_rows()
        return [row.property("buildVariant") for row in build_rows_of(card)
                if row.isChecked()]

    start = chosen()
    assert len(start) == 1, f"the card starts with {start} chosen, expected one"

    for attempt in (1, 2, 3):
        card = card_with_rows()
        row = next(r for r in build_rows_of(card)
                   if r.property("buildVariant") == start[0])
        row.click()
        settle()
        assert chosen() == start, (
            f"press {attempt} on the build already shown left {chosen()} "
            f"chosen, so clicking it cleared the card")


def test_a_card_is_never_hidden_while_the_list_can_still_paint(window) -> None:
    """The chosen card flashed; nothing else on the page moved.

    Replacing a card hides the old one, which marks the region it occupied as
    needing a repaint, and the replacement does not paint into that region
    until the loop next runs. In between, that part of the page has nothing to
    show. Qt merges the two updates into one frame often enough to hide it --
    which is why it reads as an occasional flicker rather than a blank card every
    time -- and when it does not, the flash is confined to the card that was
    chosen, because nothing else was touched and the replacement card is the
    same height as the one it replaces.

    So the check is not "did a paint happen", which cannot be observed here; it
    is whether there is a gap left for one to happen in. Painting being held
    still across the swap means there is not.

    Asserted against both paths, since both hide a card: choosing a build, which
    replaces one, and a full redraw, which replaces all of them.
    """
    panel = panel_of(window)
    window.show()
    settle()
    toggles = build_toggles(panel)
    assert toggles, "no card offers its builds"
    toggles[0].click()
    settle()

    card = toggles[0].parent()
    rows = [row for row in build_rows_of(card) if not row.isChecked()]
    assert rows, "no build is offered as a choice"

    from PySide6.QtWidgets import QFrame

    gaps = []
    original = QFrame.hide

    def spy(frame):
        try:
            if frame.updatesEnabled():
                gaps.append(frame.objectName() or type(frame).__name__)
        except RuntimeError:
            pass
        return original(frame)

    QFrame.hide = spy
    try:
        rows[0].click()
        settle()
        first = len(gaps)

        panel.render_page()
        settle()
    finally:
        QFrame.hide = original

    assert not gaps, (
        "these cards were hidden while the list could still be painted, so "
        f"there is a gap for the compositor to show: {gaps[:6]}")
    assert first == 0, (
        f"choosing a build alone left {first} gaps")


def test_painting_is_held_still_and_always_given_back(window) -> None:
    """Updates off across a rebuild, and on again whatever happens.

    A list that cannot repaint is a far worse fault than a flash: the page is
    frozen for the rest of the session and nothing about it says why. So the
    restore is in a ``finally``, and this proves it -- an exception thrown
    inside the rebuild still has to leave the list able to paint.
    """
    panel = panel_of(window)
    window.show()
    settle()
    assert panel.body.updatesEnabled(), (
        "the list cannot repaint at all, before anything has been done to it")

    with panel._still():
        assert not panel.body.updatesEnabled(), (
            "the rebuild did not hold painting still, so the gap it closes is "
            "still there")
    assert panel.body.updatesEnabled(), "painting was not given back"

    with pytest.raises(RuntimeError):
        with panel._still():
            raise RuntimeError("the catalogue could not be drawn")
    assert panel.body.updatesEnabled(), (
        "the list is still unable to repaint after a failed redraw, which is a "
        "far worse fault than the flicker it was meant to prevent")

    panel.render_page()
    settle()
    assert cards_on_page(panel), "the list did not come back after a failed one"


def test_pressing_show_every_model_does_not_rebuild_under_its_own_click(
        window) -> None:
    """The button that widens the filter is drawn on the card being thrown away.

    "Show every model" sits inside the very card saying the catalogue has
    nothing to show, so rebuilding the page inside the click handler tears down
    the widget that is still processing that click.

    So the rebuild waits a turn of the event loop, and this asserts it has not
    even been attempted by the time the click returns.

    Not what stopped the stray windows -- that was a widget shown before it had
    a parent, on every rebuild, which this defers past as well but does not
    cause. See test_a_rebuild_never_shows_a_widget_that_has_no_parent, which is
    the one that failed before the ordering was corrected.
    """
    panel = panel_of(window)
    window.show()
    settle()
    panel.task_picker.setCurrentIndex(panel.task_picker.findData("vision"))
    settle()

    buttons = [button for button in panel.findChildren(QPushButton)
               if button.isVisible() and "every model" in button.text().lower()]
    assert buttons, "there is no Show every model button to press"
    button = buttons[0]
    card = button.parent()

    calls = []
    original = panel.render_page
    panel.render_page = lambda: (calls.append(1), original())
    try:
        button.click()
        # Still inside the click's own call stack here.
        assert not calls, (
            "the page was rebuilt while the click was still being handled")
        assert card.parent() is not None, (
            "the card holding the pressed button was detached mid-click, which "
            "is what promotes it to a top-level window")
        assert not card.isWindow(), (
            "the card holding the pressed button became a window mid-click")
        # And now let the deferred rebuild happen, still being counted.
        settle()
        assert calls == [1], (
            f"the page was drawn {len(calls)} times, expected once")
    finally:
        panel.render_page = original

    assert panel.task_picker.currentData() is None, (
        f"the filter is still {panel.task_picker.currentText()!r}")
    assert cards_on_page(panel), "widening the filter left the page empty"


def test_mashing_show_every_model_rebuilds_once(window) -> None:
    """A reader who leans on the button should not queue a render per press.

    Each press used to schedule its own rebuild, so a held-down or
    double-clicked button redrew the page several times over.
    """
    panel = panel_of(window)
    window.show()
    settle()
    panel.task_picker.setCurrentIndex(panel.task_picker.findData("vision"))
    settle()

    calls = []
    original = panel.render_page
    panel.render_page = lambda: (calls.append(1), original())
    try:
        for _ in range(5):
            panel.show_everything()
        settle()
    finally:
        panel.render_page = original

    assert len(calls) == 1, (
        f"five presses of Show every model drew the page {len(calls)} times")


def test_the_button_that_widens_the_filter_is_not_destroyed_by_its_own_click(window) -> None:
    """The control being pressed is inside the card that press tears down.

    Nothing is visibly wrong while it happens, because the click has already
    been delivered -- but it means the handler is running inside a widget that
    no longer exists, which is the kind of thing that works until it does not.
    """
    panel = panel_of(window)
    window.show()
    settle()
    panel.task_picker.setCurrentIndex(panel.task_picker.findData("vision"))
    settle()

    button = next(b for card in cards_on_page(panel)
                  for b in card.findChildren(QPushButton)
                  if "every model" in b.text().lower())

    survivors = []
    original = panel.render_page
    panel.render_page = lambda: (survivors.append(button), original())
    try:
        button.click()
        settle()
    finally:
        panel.render_page = original

    assert len(survivors) == 1, (
        f"the page was drawn {len(survivors)} times; the first teardown "
        "removes the button handling the click")


def test_every_card_on_the_page_is_padded_the_same_way(window) -> None:
    """One card style, so the page does not step in and out as you read it.

    The padding was set on the model card alone. Everything else -- the device
    card, the withdrawn cards, the empty state -- fell back to Qt's 9px
    default, and card text therefore started at four different places on one
    page: the title at 30, the device card at 40, a build's name at 39 and a
    model at 62. A model list is read by scanning down a column, and a column
    that steps is a column that is read one card at a time instead.
    """
    panel = panel_of(window)
    window.show()
    settle()

    cards = [card for card in panel.findChildren(QFrame)
             if card.objectName() in CARD_NAMES]
    assert len(cards) > 1, "not enough cards to compare"

    lefts = {}
    for card in cards:
        margins = card.layout().contentsMargins()
        lefts.setdefault(margins.left(), []).append(card.objectName())
    assert len(lefts) <= 2, (
        f"cards disagree about their own padding: {lefts}")

    widths = {card.width() for card in cards}
    assert len(widths) == 1, (
        f"cards on one page are different widths: {sorted(widths)}")

    # And the padding is the app's card padding, not Qt's default.
    from PySide6.QtWidgets import QVBoxLayout
    default = QVBoxLayout().contentsMargins().left()
    for card in cards:
        assert card.layout().contentsMargins().left() > default, (
            f"{card.objectName} is still using Qt's {default}px default")


def test_text_on_every_card_starts_on_the_same_line(window) -> None:
    """The headings line up, whatever produced the card.

    This is the one people actually see. A model card carries a 3px left border
    where the others carry 1px, so matching the padding alone still left its
    name two pixels outboard of the device card above it.
    """
    panel = panel_of(window)
    window.show()
    settle()

    def left_edge_of(widget, root):
        total = 0
        current = widget
        while current is not None and current is not root:
            total += current.x()
            current = current.parent()
        return total

    root = panel.findChild(QScrollArea).widget()
    found = []
    for card in panel.findChildren(QFrame):
        if card.objectName() not in CARD_NAMES:
            continue
        for label in card.findChildren(QLabel):
            if label.objectName() == "heading" and label.text():
                found.append((left_edge_of(label, root), card.objectName()))
                break

    edges = {edge for edge, _ in found}
    names = {name for _, name in found}
    assert len(names) >= 2, (
        f"expected headings on more than one kind of card, got {names}")
    assert len(edges) == 1, (
        f"card headings do not line up: "
        f"{sorted((edge, name) for edge, name in found)}")


def test_a_build_name_is_not_a_heading(window) -> None:
    """Q2_K was drawn at 19px, the same size as the model it belongs to.

    Besides looking wrong in a dense row, anything that reads the page by
    finding headings found twenty of them per page -- every quantisation on
    every model -- which buries the one thing that is actually a heading.
    """
    panel = panel_of(window)
    window.show()
    settle()
    for button in build_toggles(panel):
        button.click()
    settle()

    build_names = [label for label in panel.findChildren(QLabel)
                   if label.objectName() == "buildName"]
    assert build_names, "no build names were drawn"
    assert not [label for label in panel.findChildren(QLabel)
                if label.objectName() == "heading"
                and label.text() in {"Q2_K", "Q3_K_S", "Q4_0", "Q4_K_M",
                                     "Q5_K_M", "Q6_K", "Q8_0", "f16"}], (
        "a quantisation is still being drawn as a heading")

    for theme in (DARK, LIGHT, MIKU):
        rule = [line for line in stylesheet(theme).splitlines()
                if line.startswith("QLabel#buildName")]
        assert rule, f"the {theme} sheet has no QLabel#buildName rule"


def test_a_model_card_groups_its_lines_instead_of_stacking_them_at_one_gap(window) -> None:
    """The gaps between groups are wider than the gaps inside them.

    Every child of the card used to sit exactly 6px from the next, and six of
    them were the same grey at the same size, so the card read as one block:
    the verdict's own explanation was indistinguishable from the disclaimer
    three lines below it. Grouping is what separates "what it costs" from "how
    to run it" from "where these numbers came from".
    """
    panel = panel_of(window)
    window.show()
    settle()

    card = cards_on_page(panel)[0]
    column = card.layout()
    inside = column.spacing()

    groups, current = [], None
    for index in range(column.count()):
        item = column.itemAt(index)
        if item is None:
            continue
        widget = item.widget()
        if widget is None:
            # A spacer is how a group is declared.
            groups.append(current)
            current = []
            continue
        if current is None:
            current = []
        current.append(widget)

    assert len(groups) > 1, (
        "the card has no groups, so every line still sits the same distance "
        "from the next")

    for group in groups:
        assert group, "an empty group was left in the card"


def test_the_card_says_which_lines_are_the_figures_it_exists_for(window) -> None:
    """The reason is the sentence the reader came for.

    It was styled muted, which is the same rule as the disclaimer three lines
    below it and as every other quiet line on the card. The facts line -- the
    sizes the whole page is for -- was muted too.
    """
    panel = panel_of(window)
    window.show()
    settle()
    card = cards_on_page(panel)[0]

    styles = {}
    for label in card.findChildren(QLabel):
        if label.objectName() == "command" or label.objectName() == "buildName":
            continue
        if label.text():
            styles.setdefault(label.objectName() or "body", []).append(
                label.text())

    assert "meta" in styles, (
        f"the facts line is not distinguished: {sorted(styles)}")
    assert "body" in styles, (
        f"the verdict's own explanation is still muted: {sorted(styles)}")
    assert "footer" in styles, (
        f"the provenance is not set apart: {sorted(styles)}")
    assert styles["footer"], "the footer group is empty"

    for theme in (DARK, LIGHT, MIKU):
        for style in ("meta", "footer"):
            assert f"QLabel#{style}" in stylesheet(theme), (
                f"the {theme} sheet has no QLabel#{style} rule")


def test_a_refresh_keeps_what_a_person_curated_about_a_model(window) -> None:
    """The Hub carries every figure, and none of the judgement.

    ``fetch_entry`` returns parameter counts, geometry, licences and file
    sizes. It returns nothing about what a model is *for*, so ``parse_model``
    defaulted those fields and a refresh silently undid each one: the model's
    tasks emptied (so it vanished from every task filter while still appearing
    under "Everything"), its curated name was replaced by the repository's last
    path segment, and ``deprecated`` went back to False -- which brought a
    model its publisher had withdrawn back as recommendable.

    The stub responses in these tests all carried a "tasks" key, which is why
    this survived: nothing had ever run the Hub's actual shape through it.
    """
    from desktop_app.cookbook import parse_model
    from desktop_app.cookbook_panel import carry_over

    panel = panel_of(window)
    settle()
    model = panel.catalog[0]

    # Exactly what fetch_entry produces: no tasks, no vision, no deprecation,
    # and a name derived from the repository rather than chosen.
    hub = {
        "repo": model.repo, "name": model.repo.split("/")[-1],
        "publisher": "someone", "license": "apache-2.0",
        "languages": ("en",), "parameters": 999999,
        "context": model.context, "layers": model.layers,
        "kv_heads": model.kv_heads, "head_dim": model.head_dim,
        "artifact_repo": model.artifact_repo,
        "variants": [{"name": "m-Q4_K_M.gguf", "quantization": "Q4_K_M",
                      "size": 123456789}],
        "source": "Hugging Face", "last_verified": "2026-10-11 00:00 UTC",
        "verified": False,
    }

    plain = parse_model(hub, 0)
    assert plain.tasks == (), (
        "this fixture no longer matches what the Hub returns")
    assert plain.name != model.display

    kept = parse_model(carry_over(model, hub), 0)
    assert kept.tasks == model.tasks, (
        f"a refresh emptied the tasks, so the model drops out of every "
        f"filter: {kept.tasks}")
    assert kept.display == model.display, (
        f"the curated name was replaced by {kept.display!r}")
    assert kept.vision == model.vision

    # And the figures the Hub does carry still update, or carrying over would
    # have fixed the naming and broken the point of refreshing.
    assert kept.parameters == 999999, "the parameter count did not update"
    assert kept.last_verified == "2026-10-11 00:00 UTC", (
        "the read date did not update")
    assert kept.variants[0].size == 123456789, "the build size did not update"


def test_refreshing_never_un_withdraws_a_model(window) -> None:
    """A publisher's withdrawal is not something a fetch gets to reverse.

    The worst of the four: with ``deprecated`` defaulting to False, refreshing
    a withdrawn model made it recommendable again, which is sending somebody
    to download something their publisher has asked people to stop using.
    """
    from dataclasses import replace

    from desktop_app.cookbook import parse_model
    from desktop_app.cookbook_panel import carry_over

    panel = panel_of(window)
    settle()
    withdrawn = replace(panel.catalog[0], deprecated=True,
                        deprecated_reason="withdrawn by its publisher")
    hub = {"repo": withdrawn.repo, "name": withdrawn.repo.split("/")[-1],
           "parameters": 1, "context": 4096, "layers": 1, "kv_heads": 1,
           "head_dim": 64, "variants": [], "last_verified": "now"}

    after = parse_model(carry_over(withdrawn, hub), 0)
    assert after.deprecated is True, (
        "a refresh brought a withdrawn model back as recommendable")
    assert after.deprecated_reason == "withdrawn by its publisher"


def test_a_refresh_of_something_not_in_the_catalogue_carries_nothing_over(window) -> None:
    """Still only merges for a model already here.

    Otherwise a response could hand the page fields for a repository it never
    asked about, which is not a refresh.
    """
    from desktop_app.cookbook_panel import carry_over

    panel = panel_of(window)
    settle()
    hub = {"repo": "someone/Unknown", "name": "Unknown", "parameters": 1}
    merged = carry_over(None, hub)
    assert merged == hub, f"something was merged in: {merged}"


def test_the_cookbook_is_built_from_the_shipped_catalogue_not_a_hardcoded_list(window) -> None:
    """The page holds no models of its own.

    A list written into the view would drift from the catalogue the engine
    checks, and the drift would be invisible until a figure was wrong.

    Stated device, or a runner with less free memory than the 7B model needs
    would leave that model off screen and the test would report a catalogue gap.
    """
    panel = panel_of(window)
    use_a_stated_device(panel)
    said = text_of(panel)
    for model in load_catalog():
        assert model.display in said, (
            f"{model.display} is in the catalogue but not on screen")


def test_no_more_than_ten_models_are_shown_at_once(window) -> None:
    """A page of cards has a limit, and the limit is ten.

    Rendering a whole catalogue at once produces one undifferentiated column
    that nobody reads to the bottom -- the same failure as having no scrollbar,
    reached from the other side. Ten is about two screens: enough to compare
    models against each other, few enough that a next page is expected.
    """
    from desktop_app.cookbook_panel import PAGE_SIZE

    panel = panel_of(window)
    settle()
    repopulate(panel, 26)
    assert len(panel.page_entries()) == 26, (
        f"the fixture did not produce 26 models, so this proves nothing: "
        f"{len(panel.page_entries())}")

    for page in range(3):
        panel._page = page
        panel.render_page()
        settle()
        shown = len(cards_on_page(panel))
        assert shown <= PAGE_SIZE, (
            f"page {page + 1} drew {shown} cards, past the limit of {PAGE_SIZE}")


def test_every_model_is_reachable_across_the_pages(window) -> None:
    """Paging must not swallow anything.

    The failure this guards against is a list that is quietly truncated: page
    one looks right, the count on the pager says there are more, and the models
    on the later pages cannot be found by anyone. So this walks every page and
    requires the union to be exactly the catalogue, with nothing repeated.
    """
    panel = panel_of(window)
    settle()
    catalog = repopulate(panel, 26)

    seen = []
    while True:
        panel.render_page()
        settle()
        said = text_of(panel)
        for model in catalog:
            if model.display in said:
                seen.append(model.repo)
        if not panel.next.isEnabled():
            break
        panel.next.click()
        settle()

    assert sorted(seen) == sorted(model.repo for model in catalog), (
        f"paging lost or repeated models: saw {len(seen)} of {len(catalog)}")
    assert len(seen) == len(set(seen)), "a model was shown on two pages"


def test_the_page_controls_say_where_you_are(window) -> None:
    """A pager nobody can read is a pager nobody will press.

    Both ends of a run have to disable, and the position has to be stated in
    words as well as arrows: an icon-only pair of arrows leaves a screen-reader
    user with no way to know how much is left.
    """
    panel = panel_of(window)
    # Shown, because isVisible() is false for a widget whose window is not on
    # screen, and "the pager is hidden" has to mean hidden rather than unshown.
    window.show()
    settle()
    repopulate(panel, 26)

    assert panel.page_label.isVisible(), "the pager is hidden while there are pages"
    assert not panel.previous.isEnabled(), (
        "there is a previous page from the first page")
    assert panel.next.isEnabled(), "there are more pages but Next is dead"

    panel.next.click()
    settle()
    assert panel.previous.isEnabled(), "Previous is dead on the second page"
    assert "Page 2 of 3" in panel.page_label.text(), (
        f"the pager does not say where it is: {panel.page_label.text()!r}")
    assert "26" in panel.page_label.text(), (
        f"the pager does not say how many there are: {panel.page_label.text()!r}")
    assert panel.page_label.accessibleName(), (
        "the position is only in the visible text, so it does not exist to "
        "anyone navigating by keyboard or screen reader")

    while panel.next.isEnabled():
        panel.next.click()
        settle()
    assert not panel.next.isEnabled(), "Next is still live on the last page"
    assert "Page 3 of 3" in panel.page_label.text()


def test_a_catalogue_that_fits_is_not_given_a_pager(window) -> None:
    """One page of models needs no controls to move between pages.

    Offering "Next" over a list that fits is a control that does nothing when
    pressed, which is worse than not having it.
    """
    panel = panel_of(window)
    window.show()
    settle()
    repopulate(panel, 4)

    assert len(panel.page_entries()) == 4
    assert not panel.page_label.isVisible(), (
        "a pager is shown for a catalogue that fits on one page")
    assert not panel.next.isVisible(), "a dead Next button is on screen"
    assert len(cards_on_page(panel)) == 4


def test_changing_the_task_returns_to_the_first_page(window) -> None:
    """A filter must not strand the reader on a page that no longer exists.

    The empty page is the dangerous outcome: it renders the "nothing fits this
    device" card, which is a claim about somebody's hardware that a change of
    filter never established.
    """
    panel = panel_of(window)
    settle()
    repopulate(panel, 26)

    panel._page = 2
    panel.render_page()
    settle()
    assert panel.page_label.text().startswith("Page 3")

    index = panel.task_picker.findData("coding")
    if index >= 0:
        panel.task_picker.setCurrentIndex(index)
        settle()

    assert panel._page == 0, (
        f"choosing a task left the reader on page {panel._page + 1}")
    assert cards_on_page(panel), "the filter left an empty page behind"
    assert "Nothing in the catalogue fits" not in text_of(panel), (
        "filtering produced the empty-state card, which claims nothing fits")


def test_a_page_number_past_the_end_lands_somewhere_real(window) -> None:
    """The page is clamped rather than rendering nothing.

    Reached by a catalogue that shrinks under a reader who is already on a
    later page -- a refresh, or a rollback to a shorter one.
    """
    panel = panel_of(window)
    settle()
    repopulate(panel, 26)
    panel._page = 2
    panel.render_page()
    settle()

    repopulate(panel, 4)
    assert panel._page == 0, f"a shorter catalogue left the reader on page {panel._page + 1}"
    assert cards_on_page(panel), "a shorter catalogue left an empty page behind"

    # And a hand-set nonsense value is clamped rather than trusted.
    panel.go_to_page(99)
    settle()
    assert 0 <= panel._page < 2
    assert cards_on_page(panel)


def test_the_invite_button_goes_to_the_page_and_not_a_window(window, monkeypatch) -> None:
    """The top bar's Invite opened a dialog for a form that lives on a page.

    The Workspaces page carries the whole invitation card, and a test has said
    so for a while: the button used to call InviteDialog, so the description of
    what an invitation was for sat on the page while every field that made one
    was somewhere else. This was the last route to that window.
    """
    from PySide6.QtWidgets import QDialog
    opened = []
    monkeypatch.setattr(QDialog, "exec", lambda self, *a, **k: opened.append(self))
    window.ready = True
    window.remote = False
    # Shown, because a widget cannot take focus in a window that is not on
    # screen. The focus is the point of the change, so it is asserted rather
    # than assumed.
    window.show()
    settle()
    window.invite_button.setEnabled(True)
    before = window.stack.currentIndex()

    window.invite_button.click()

    assert not opened, f"the top bar opened a dialog instead of the page: {opened}"
    assert window.stack.currentIndex() == win.WORKSPACES_PAGE, (
        f"it navigated to {window.stack.currentIndex()}, not the Workspaces page")
    assert window.workspace_invite_name.hasFocus(), (
        "it did not put the cursor in the device-name field, so the user lands "
        "on a page with four cards and no indication of which one they wanted")


def test_a_member_is_offered_no_way_to_invite_a_device(window, monkeypatch) -> None:
    """You cannot invite a device into a workspace you do not host.

    The button is disabled, and pressing the method directly does nothing.
    Navigating anyway would show a card whose only action is refused by the
    runtime, which is a worse answer than no card at all.
    """
    from PySide6.QtWidgets import QDialog
    opened = []
    monkeypatch.setattr(QDialog, "exec", lambda self, *a, **k: opened.append(self))
    window.ready = True
    window.remote = True
    before = window.stack.currentIndex()

    window.invite_device()

    assert not opened, "a member opened the invitation dialog"
    assert window.stack.currentIndex() == before, (
        "a member was sent somewhere from an action that is not theirs to take")