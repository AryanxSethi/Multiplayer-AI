"""The Cookbook window, against a real headless window like the other pages.

The point of these is the two promises the window makes. The first is that it
opens without touching the network, because the Resources page tells the user
this app sends nothing anywhere. The second is that it says which models fit
and why, rather than presenting a list a reader has to take on trust.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6", reason="PySide6 is needed for the desktop page tests")
pytest.importorskip("psutil", reason="psutil backs hardware detection")

from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from desktop_app import window as win  # noqa: E402
from desktop_app.cookbook import load_catalog  # noqa: E402
from desktop_app.cookbook_dialog import CookbookDialog  # noqa: E402
from desktop_app.storage import Storage  # noqa: E402
from desktop_app.theme import DARK, LIGHT, MIKU  # noqa: E402
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


def pump():
    """Let the dialog lay itself out before anything is read off it.

    The window is built in the constructor, so this is only about paint and
    layout settling, not about waiting for anything to arrive.
    """
    QApplication.processEvents()
    QApplication.processEvents()


def text_of(dialog):
    """Everything the window is currently saying, as one blob of text."""
    return " ".join(widget.text() for widget in dialog.findChildren(QLabel))


def test_the_cookbook_has_an_entry_in_the_top_bar_with_a_real_icon(window) -> None:
    """It sits beside the two controls a new user reaches for, and is findable.

    The button carries no text at all, so the tooltip and the accessible name
    are the only things that say what it is. An unlabelled control in a top
    bar is invisible to anyone navigating by keyboard or by screen reader, and
    this app has a test for exactly that elsewhere in the shell.
    """
    button = window.cookbook_button
    assert button.isEnabled() or not window.ready, (
        "the Cookbook is unreachable before the runtime is up")
    assert not button.text().strip(), (
        f"the button carries text and is no longer icon-only: {button.text()!r}")
    assert button.toolTip(), "the Cookbook button has no tooltip"
    assert "Cookbook" in button.toolTip(), (
        f"the tooltip does not name it: {button.tooltip()!r}")
    assert button.toolTip() != button.text().strip()
    assert "Cookbook" in button.accessibleName()

    # An icon, painted rather than a character. A glyph would come out of
    # whatever font resolved and would not match the marks beside it.
    icon = button.icon()
    assert not icon.isNull(), "the Cookbook button has no icon at all"
    assert not icon.pixmap(24, 24).isNull(), "the Cookbook icon draws nothing at button size"

    # And it is the same mark the shell's other icons are made by.
    from desktop_app.icons import navigation_icon
    assert not navigation_icon("cookbook", "#ffffff").isNull()
    assert not navigation_icon("cookbook", "#ffffff").pixmap(24, 24).isNull()


def test_the_cookbook_icon_carries_no_emoji_or_font_glyph(window) -> None:
    """A character in the icon slot would defeat the reason for painting it.

    The navigation marks are drawn so they share one stroke weight regardless
    of what fonts are installed. This machine has no fonts at all, which is
    exactly the case that would show it: a glyph icon would come back blank
    here and look merely wrong on a normal one.
    """
    from desktop_app.icons import navigation_icon
    icon = navigation_icon("cookbook", "#ffffff")
    for size in (16, 24, 48):
        image = icon.pixmap(size, size).toImage()
        painted = sum(1 for x in range(image.width()) for y in range(image.height())
                      if image.pixelColor(x, y).alpha() > 0)
        assert painted > 0, f"the icon is blank at {size}px, so it needs a font"


def test_opening_the_cookbook_asks_the_network_for_nothing(window) -> None:
    """Nothing is requested until Refresh is pressed.

    The Resources page says this device's readings are sent nowhere, and a
    window that fetched its catalogue on open would make that untrue while the
    page still said it. The check is on the command channel, which is the only
    way out of the UI to the network.
    """
    sent = []
    window.command = lambda name, *args, **kwargs: sent.append(name)

    dialog = window.open_cookbook()
    try:
        qt_events = 3
        for _ in range(qt_events):
            pump()
        assert sent == [], f"opening the Cookbook asked the network for {sent!r}"
        assert dialog.isVisible()
    finally:
        dialog.close()


def test_the_cookbook_explains_the_device_it_is_judging_models_against(window) -> None:
    """A recommendation with no stated basis is one nobody can check."""
    dialog = window.open_cookbook()
    try:
        pump()
        said = text_of(dialog)
        assert "memory" in said.lower(), f"the device is not described: {said[:200]!r}"
        # Detection could not read graphics memory, and saying so is the
        # difference between an honest blank and a machine quietly judged as
        # though it had no card at all.
        assert dialog.hardware.vram is None, (
            "this test needs graphics memory to be unreadable here")
        assert "not detected" in said.lower(), (
            f"nothing says graphics memory went unread: {said[:300]!r}")
    finally:
        dialog.close()


def test_every_recommendation_says_why_and_what_it_would_take(window) -> None:
    """Every verdict carries its reason, its source, and its caveat.

    The brief's whole complaint about model lists is that they present a
    verdict without the reasoning. A card that shows a model and a green tick
    and nothing else is the thing this feature exists to stop being.
    """
    dialog = window.open_cookbook()
    try:
        pump()
        said = text_of(dialog)
        assert "Qwen2.5" in said, f"nothing was recommended: {said[:300]!r}"
        assert "GB" in said or "MB" in said, f"no size is shown: {said[:300]!r}"
        assert "memory" in said.lower(), "no reason is given"
        assert "hugging" in said.lower(), (
            f"the source is not attributed: {said[-300:]!r}")
        assert "not measured on this device" in said.lower(), (
            "nothing distinguishes a computed fit from a tested one")
    finally:
        dialog.close()


def test_the_cookbook_shows_a_command_that_names_a_real_repository(window) -> None:
    """A command built from a guess is worse than no command.

    It has to name the repository that actually holds the file, which for a
    quantised build is the quantiser's and not the model's publisher.
    """
    dialog = window.open_cookbook()
    try:
        pump()
        said = text_of(dialog)
        assert "ollama run hf.co/" in said, f"no runnable command: {said[:400]!r}"
        assert "bartowski/" in said, (
            f"the command does not name the repository holding the file: {said[:400]!r}")
    finally:
        dialog.close()


def test_the_cookbook_can_be_narrowed_to_one_kind_of_work(window) -> None:
    """Choosing a task changes the list rather than only the label."""
    dialog = window.open_cookbook()
    try:
        pump()
        all_tasks = text_of(dialog)

        index = dialog.task_picker.findData("coding")
        assert index >= 0, "there is no way to ask for a coding model"
        dialog.task_picker.setCurrentIndex(index)
        pump()
        coding = text_of(dialog)

        assert coding != all_tasks, "choosing a task changed nothing on screen"
        assert "Coding" not in coding or "Coder" in coding or "Phi" in coding, (
            f"the coding list does not look like coding models: {coding[:200]!r}")
    finally:
        dialog.close()


def test_the_refresh_button_says_what_it_sends_before_it_is_pressed(window) -> None:
    """The disclosure is on the button, not in a dialog behind the button."""
    dialog = window.open_cookbook()
    try:
        tip = dialog.refresh.toolTip()
        assert tip, "the refresh button has no tooltip"
        assert "huggingface" in tip.lower(), f"it does not say where it goes: {tip!r}"
        assert "address" in tip.lower() or "network" in tip.lower(), (
            f"it does not say what is sent: {tip!r}")
    finally:
        dialog.close()


def test_refreshing_asks_once_and_reports_a_failure_without_losing_the_list(window) -> None:
    """One press is one request, and a refusal leaves what was already there."""
    dialog = window.open_cookbook()
    try:
        pump()
        pending = []
        window.command = lambda name, *args, **kwargs: pending.append(kwargs)

        dialog.do_refresh()
        assert len(pending) == 1, f"one press asked {len(pending)} times"
        assert not dialog.refresh.isEnabled(), (
            "the button stayed live while the request was in flight")

        # A second press must not start a second request.
        dialog.do_refresh()
        assert len(pending) == 1, "a second request started while the first was running"

        pending[0]["failure"]("Hugging Face could not be reached.")
        pump()

        said = text_of(dialog).lower()
        assert "could not be read" in said, "a failed refresh said nothing"
        assert "qwen2.5" in said, "a failed refresh emptied the list it had already loaded"
        assert dialog.refresh.isEnabled(), "the button stayed dead after a failure"
        assert dialog.refresh.text() == "Refresh from Hugging Face", (
            f"the button is still mid-attempt: {dialog.refresh.text()!r}")
    finally:
        dialog.close()


def test_a_refreshed_model_goes_through_the_same_validation_as_a_bundled_one(window) -> None:
    """A field arriving over the network is no more trusted than one on disk.

    Otherwise a response could carry a task this build has never heard of, or
    a negative size, straight into a recommendation by a route the catalogue's
    own checks are not applied to. The list on screen must not change when a
    bad record is refused.
    """
    dialog = window.open_cookbook()
    try:
        pump()
        before = text_of(dialog)
        for record in ({"repo": "a/b", "tasks": ["teleport"]},
                       {"repo": "a/b", "context": -8},
                       {"repo": "not a repo id"},
                       {"repo": "a/b", "verified": "yes, definitely"},
                       {"repo": "a/b", "parameters": "lots"}):
            pending = []
            window.command = lambda name, *args, **kwargs: pending.append(kwargs)
            dialog.do_refresh()
            pending[0]["success"](dict(record, last_verified="now"))
            pump()
            assert "unusable" in text_of(dialog).lower(), (
                f"{record!r} was accepted: {text_of(dialog)[:200]!r}")
            assert text_of(dialog) == before or "qwen2.5" in text_of(dialog).lower(), (
                "a refused record still changed the list")
    finally:
        dialog.close()


def test_a_good_refresh_replaces_the_model_and_says_it_is_not_saved(window) -> None:
    """The happy path, and the promise that nothing was written to disk."""
    dialog = window.open_cookbook()
    try:
        pump()
        pending = []
        window.command = lambda name, *args, **kwargs: pending.append(kwargs)
        dialog.do_refresh()
        pending[0]["success"]({
            "repo": "someone/Brand-New-Model", "artifact_repo": "bartowski/Brand-New-Model-GGUF",
            "name": "Brand New Model", "publisher": "someone", "parameters": 700000000,
            "context": 8192, "layers": 16, "kv_heads": 4, "head_dim": 64,
            "license": "apache-2.0", "languages": ("en",), "vision": False,
            "tasks": ("general",), "verified": False,
            "variants": [{"name": "m-Q4_K_M.gguf", "quantization": "Q4_K_M", "size": 500_000_000}],
            "last_verified": "2026-10-10 12:00 UTC"})
        pump()

        said = text_of(dialog)
        assert "Brand New Model" in said, f"the refreshed model is not shown: {said[:300]!r}"
        assert "not saved" in said.lower(), (
            "it does not say the refresh was not written to disk")
    finally:
        dialog.close()


def test_the_cookbook_opens_once_and_follows_a_theme_change(window) -> None:
    """A second press raises the same window, and the theme reaches it.

    Two windows would mean two copies of the same list, and the hand-painted
    bits inside would keep the colours they were built with while the rest of
    the app changed underneath them.
    """
    first = window.open_cookbook()
    second = window.open_cookbook()
    try:
        assert first is second, "a second press opened a second window"
        first.set_theme(LIGHT)
        assert first._theme == LIGHT
        first.set_theme(MIKU)
        first.set_theme(DARK)
        assert "Qwen2.5" in text_of(first), "the window emptied when the theme changed"
    finally:
        first.close()


def test_the_cookbook_is_built_from_the_shipped_catalogue_not_a_hardcoded_list(window) -> None:
    """The window holds no models of its own.

    A list written into the view would drift from the catalogue the engine
    checks, and the drift would be invisible until a figure was wrong.
    """
    dialog = window.open_cookbook()
    try:
        pump()
        said = text_of(dialog)
        for model in load_catalog():
            assert model.display in said, (
                f"{model.display} is in the catalogue but not on screen")
    finally:
        dialog.close()