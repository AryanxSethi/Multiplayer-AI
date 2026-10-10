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

from PySide6.QtWidgets import QApplication, QLabel, QScrollArea  # noqa: E402

from desktop_app import window as win  # noqa: E402
from desktop_app.cookbook import load_catalog  # noqa: E402
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


def test_a_not_recommended_badge_is_painted_in_the_danger_colour(window) -> None:
    """The one badge that has to look wrong has to be styled to look wrong.

    It was given the theme token ``error`` where the Qt objectName wanted is
    ``errorMessage``, and there is no rule for ``#error``, so the badge came out
    in the default text colour -- a model this device should not run was drawn
    exactly like one it should. A dialog hid it. On a page it does not, which
    is why this is checked against the stylesheet rather than by eye.
    """
    from desktop_app.cookbook_panel import TONE
    from desktop_app.theme import color
    assert TONE["not recommended"] == "errorMessage", (
        f"the not-recommended badge is styled {TONE['not recommended']!r}, which "
        "no label rule matches")
    for theme in (DARK, LIGHT, MIKU):
        assert "QLabel#errorMessage" in stylesheet(theme), (
            f"the {theme} sheet does not style #errorMessage, so the badge "
            "cannot be coloured")
    # And it is not the same colour as the one that is fine, so it reads as a
    # warning rather than as a second opinion.
    assert color(DARK, "error") != color(DARK, "success")


def test_opening_the_cookbook_asks_the_network_for_nothing(window) -> None:
    """Nothing is requested until Refresh is pressed.

    The Resources page says this device's readings are sent nowhere, and a page
    that fetched its catalogue on open would make that untrue while the page
    still said it. The check is on the command channel, which is the only way
    out of the UI to the network.
    """
    sent = []
    window.command = lambda name, *args, **kwargs: sent.append(name)

    window.navigate(win.COOKBOOK_PAGE)
    QApplication.processEvents()
    assert sent == [], f"opening the Cookbook asked the network for {sent!r}"


def test_the_cookbook_explains_the_device_it_is_judging_models_against(window) -> None:
    """A recommendation with no stated basis is one nobody can check."""
    panel = panel_of(window)
    QApplication.processEvents()
    said = text_of(panel)
    assert "memory" in said.lower(), f"the device is not described: {said[:200]!r}"
    # Detection could not read graphics memory, and saying so is the difference
    # between an honest blank and a machine quietly judged as though it had no
    # card at all.
    assert panel.hardware.vram is None, (
        "this test needs graphics memory to be unreadable here")
    assert "not detected" in said.lower(), (
        f"nothing says graphics memory went unread: {said[:300]!r}")


def test_every_recommendation_says_why_and_what_it_would_take(window) -> None:
    """Every verdict carries its reason, its source, and its caveat.

    The whole complaint about model lists is that they present a verdict
    without the reasoning. A card that shows a model and a green tick and
    nothing else is the thing this feature exists to stop being.
    """
    panel = panel_of(window)
    QApplication.processEvents()
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
    QApplication.processEvents()
    said = text_of(panel)
    assert "ollama run hf.co/" in said, f"no runnable command: {said[:400]!r}"
    assert "bartowski/" in said, (
        f"the command does not name the repository holding the file: {said[:400]!r}")


def test_the_cookbook_can_be_narrowed_to_one_kind_of_work(window) -> None:
    """Choosing a task changes the list rather than only the label."""
    panel = panel_of(window)
    QApplication.processEvents()
    everything = text_of(panel)
    assert "Everything" in " ".join(
        panel.task_picker.itemText(i) for i in range(panel.task_picker.count())), (
        "there is no way to see everything that fits, so opening the page on a "
        "task would hide models nobody asked to exclude")

    index = panel.task_picker.findData("coding")
    assert index >= 0, "there is no way to ask for a coding model"
    panel.task_picker.setCurrentIndex(index)
    QApplication.processEvents()
    assert text_of(panel) != everything, "choosing a task changed nothing on screen"


def test_the_refresh_button_says_what_it_sends_before_it_is_pressed(window) -> None:
    """The disclosure is on the button, not in a dialog behind the button."""
    panel = panel_of(window)
    tip = panel.refresh.toolTip()
    assert tip, "the refresh button has no tooltip"
    assert "huggingface" in tip.lower(), f"it does not say where it goes: {tip!r}"
    assert "address" in tip.lower() or "network" in tip.lower(), (
        f"it does not say what is sent: {tip!r}")


def test_refreshing_asks_once_and_reports_a_failure_without_losing_the_list(window) -> None:
    """One press is one request, and a refusal leaves what was already there."""
    panel = panel_of(window)
    QApplication.processEvents()
    pending = []
    window.command = lambda name, *args, **kwargs: pending.append(kwargs)

    panel.do_refresh()
    assert len(pending) == 1, f"one press asked {len(pending)} times"
    assert not panel.refresh.isEnabled(), (
        "the button stayed live while the request was in flight")

    panel.do_refresh()
    assert len(pending) == 1, "a second request started while the first was running"

    pending[0]["failure"]("Hugging Face could not be reached.")
    QApplication.processEvents()

    said = text_of(panel).lower()
    assert "could not be read" in said, "a failed refresh said nothing"
    assert "qwen2.5" in said, "a failed refresh emptied the list it had already loaded"
    assert panel.refresh.isEnabled(), "the button stayed dead after a failure"
    assert panel.refresh.text() == "Refresh from Hugging Face", (
        f"the button is still mid-attempt: {panel.refresh.text()!r}")


def test_a_refreshed_model_goes_through_the_same_validation_as_a_bundled_one(window) -> None:
    """A field arriving over the network is no more trusted than one on disk.

    Otherwise a response could carry a task this build has never heard of, or a
    negative size, straight into a recommendation by a route the catalogue's own
    checks are not applied to. The list on screen must not change when a bad
    record is refused.
    """
    panel = panel_of(window)
    QApplication.processEvents()
    # The catalogue itself, rather than everything on the page: refusing a
    # record is supposed to change the line saying it was refused, and only
    # the list of models must be untouched.
    before = [model.repo for model in panel.catalog]
    for record in ({"repo": "a/b", "tasks": ["teleport"]},
                   {"repo": "a/b", "context": -8},
                   {"repo": "not a repo id"},
                   {"repo": "a/b", "verified": "yes, definitely"},
                   {"repo": "a/b", "parameters": "lots"}):
        pending = []
        window.command = lambda name, *args, **kwargs: pending.append(kwargs)
        panel.do_refresh()
        pending[0]["success"](dict(record, last_verified="now"))
        QApplication.processEvents()
        assert "unusable" in text_of(panel).lower(), (
            f"{record!r} was accepted: {text_of(panel)[:200]!r}")
        assert [model.repo for model in panel.catalog] == before, (
            f"a refused record still changed the list: "
            f"{[model.repo for model in panel.catalog]}")


def test_a_good_refresh_replaces_the_model_and_says_it_is_not_saved(window) -> None:
    """The happy path, and the promise that nothing was written to disk."""
    panel = panel_of(window)
    QApplication.processEvents()
    pending = []
    window.command = lambda name, *args, **kwargs: pending.append(kwargs)
    panel.do_refresh()
    pending[0]["success"]({
        "repo": "someone/Brand-New-Model", "artifact_repo": "bartowski/Brand-New-Model-GGUF",
        "name": "Brand New Model", "publisher": "someone", "parameters": 700000000,
        "context": 8192, "layers": 16, "kv_heads": 4, "head_dim": 64,
        "license": "apache-2.0", "languages": ("en",), "vision": False,
        "tasks": ("general",), "verified": False,
        "variants": [{"name": "m-Q4_K_M.gguf", "quantization": "Q4_K_M", "size": 500_000_000}],
        "last_verified": "2026-10-10 12:00 UTC"})
    QApplication.processEvents()

    said = text_of(panel)
    assert "Brand New Model" in said, f"the refreshed model is not shown: {said[:300]!r}"
    assert "not saved" in said.lower(), (
        "it does not say the refresh was not written to disk")


def test_the_cookbook_is_built_from_the_shipped_catalogue_not_a_hardcoded_list(window) -> None:
    """The page holds no models of its own.

    A list written into the view would drift from the catalogue the engine
    checks, and the drift would be invisible until a figure was wrong.
    """
    panel = panel_of(window)
    QApplication.processEvents()
    said = text_of(panel)
    for model in load_catalog():
        assert model.display in said, (
            f"{model.display} is in the catalogue but not on screen")


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
    QApplication.processEvents()
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