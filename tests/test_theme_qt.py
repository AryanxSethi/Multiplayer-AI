"""Theme switching exercised against the real widget tree.

Runs headlessly with the ``offscreen`` Qt platform plugin, so no display
is needed. These tests build a real MainWindow because the point is that
live switching reaches every widget: a stylesheet swap alone leaves
hand-styled widgets on the old colours, and only the assembled window can
show that.

The window is given a MemoryVault rather than a real one. DesktopRuntime
calls ``Storage.save_credentials`` during startup, which writes to the OS
credential store, so using the real Vault would leave a device token in
the developer's keyring every time the suite ran.
"""

from __future__ import annotations

import math

import pytest


pytest.importorskip("PySide6", reason="PySide6 is needed for the desktop theme tests")

from PySide6.QtGui import QPalette  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from desktop_app.storage import Storage  # noqa: E402
from desktop_app.theme import DARK, LIGHT, MIKU, color, stylesheet  # noqa: E402
from desktop_app.markdown import MarkdownMessage  # noqa: E402
from desktop_app.widgets import OrbitArt  # noqa: E402


class MemoryVault:
    """Stands in for the OS credential store."""

    def __init__(self):
        self.values = {}

    def get(self, name):
        return self.values.get(name)

    def set(self, name, value):
        self.values[name] = value


@pytest.fixture(scope="session")
def qt_app():
    """One QApplication for the session; Qt allows only one."""
    app = QApplication.instance() or QApplication([])
    yield app
    app.processEvents()


@pytest.fixture
def storage(tmp_path):
    return Storage(tmp_path, MemoryVault())


@pytest.fixture
def window(qt_app, storage, monkeypatch):
    """A real MainWindow, torn down after the test."""
    from desktop_app.window import MainWindow

    instance = MainWindow(storage)
    yield instance
    instance.network.shutdown()
    instance.network.wait(10000)


# --- resolution -----------------------------------------------------------


def test_window_uses_the_stored_theme(qt_app, storage) -> None:
    """A stored theme is applied when the window is built."""
    storage.settings["theme"] = LIGHT
    from desktop_app.window import MainWindow

    instance = MainWindow(storage)
    try:
        assert instance.theme == LIGHT
        assert instance.styleSheet() == stylesheet(LIGHT)
    finally:
        instance.network.shutdown()
        instance.network.wait(10000)


def test_window_applies_dark_by_default(qt_app, storage) -> None:
    """With no stored theme and no OS preference the window is dark."""
    from desktop_app.window import MainWindow

    instance = MainWindow(storage)
    try:
        # Headless Qt reports Unknown, so this exercises the fallback.
        assert instance.theme == DARK
    finally:
        instance.network.shutdown()
        instance.network.wait(10000)


# --- live switching -------------------------------------------------------


def test_apply_theme_replaces_the_stylesheet(window) -> None:
    """Switching themes changes the stylesheet on the live window."""
    window.apply_theme(LIGHT)
    assert window.styleSheet() == stylesheet(LIGHT)

    window.apply_theme(DARK)
    assert window.styleSheet() == stylesheet(DARK)


def test_apply_theme_switches_provider_logos(window) -> None:
    """Provider controls must remain readable when switching to light."""
    dark = window.provider_logos["openai"].pixmap().toImage()
    window.apply_theme(LIGHT)
    light = window.provider_logos["openai"].pixmap().toImage()
    assert light != dark
    assert not light.isNull()


def test_apply_theme_recolours_hand_styled_widgets(window) -> None:
    """Widgets carrying their own stylesheet pick up the new colours.

    These outrank the application stylesheet and would otherwise keep the
    dark-theme values after a switch. The avatar used to be one of them;
    it is painted now, so it is covered by its own test instead.
    """
    window.apply_theme(LIGHT)
    light_toast = window.toast_row.styleSheet()

    window.apply_theme(DARK)

    assert window.toast_row.styleSheet() != light_toast


# --- pieces ---------------------------------------------------------------


def test_orbit_art_accepts_a_theme(qt_app) -> None:
    """OrbitArt stores and repaints for the requested theme.

    Asks for qt_app rather than relying on another test having built the
    QApplication first. Without it, running this test on its own takes the
    whole process down instead of failing.
    """
    art = OrbitArt(theme=LIGHT)
    assert art._theme == LIGHT

    art.set_theme(DARK)
    assert art._theme == DARK


# --- the orbit's motion --------------------------------------------------
#
# Measured from the code that paints, through chip_position, rather than
# from a formula copied into the test. That is the point: a test with its
# own copy of the arithmetic would go on passing after the painting
# changed, which is how the original motion survived every run.


def orbit_samples(art, index, steps=400):
    """Every position of one chip across a whole cycle, in seconds."""
    cycle = art._CYCLE_MS / 1000.0
    return [(step / steps * cycle,
             art.chip_position(index, math.tau * step / steps)[0])
            for step in range(steps + 1)]


def test_the_chips_are_not_rounded_to_whole_pixels(qt_app) -> None:
    """Rounding is what made the motion look like an image still loading.

    A chip moving less than a pixel a frame could then only appear as
    still, still, jump. The coordinates have to keep their fractions or the
    antialiasing has nothing to soften.

    Checked by painting rather than by reading the numbers back: two phases
    a fraction of a pixel apart are rendered and compared. Snapped to the
    grid they land on the same pixels and the two pictures are identical;
    painted as floats the antialiasing resolves them differently.
    """
    art = OrbitArt(moving=False, theme=DARK)
    art.resize(260, 240)

    base = 1.0
    nudge = base + 1e-4
    moved = art.chip_position(0, nudge)[0] - art.chip_position(0, base)[0]
    assert 0 < abs(moved) < 1.0, (
        f"this check needs a shift smaller than a pixel, got {moved:.4f}px")

    art.set_phase(base)
    first = art.grab().toImage()
    art.set_phase(nudge)
    second = art.grab().toImage()

    assert first != second, (
        f"a shift of {abs(moved):.4f}px painted identically, so the chips are "
        f"being snapped to the pixel grid and the motion can only step")


def test_the_motion_is_visible_but_not_hurried(qt_app) -> None:
    """The orbit drifted 11px over twelve seconds, which read as a still image.

    Held between two bounds: enough travel that the movement is
    unmistakable, not so much that it pulls the eye off the words beside
    it. Both numbers are a judgement call, so they are written down rather
    than left to whatever the painting happened to produce.
    """
    art = OrbitArt(moving=False)
    art.resize(260, 240)
    cycle = art._CYCLE_MS / 1000.0

    for index in range(len(OrbitArt._CHIPS)):
        samples = orbit_samples(art, index)
        travel = max(x for _, x in samples) - min(x for _, x in samples)
        speeds = [abs(samples[n + 1][1] - samples[n][1]) / (cycle / len(samples))
                  for n in range(len(samples) - 1)]
        peak = max(speeds)

        assert travel >= 25.0, (
            f"chip {index} travels {travel:.1f}px across the cycle, too little "
            f"to read as motion rather than as a still image")
        assert travel <= 70.0, (
            f"chip {index} travels {travel:.1f}px, which is hurried for an "
            f"illustration sitting beside a paragraph")
        assert 3.0 <= peak <= 18.0, (
            f"chip {index} peaks at {peak:.1f}px per second; it should be "
            f"clearly moving but unhurried")


def test_the_chips_do_not_trace_the_same_path(qt_app) -> None:
    """No two chips may move alike, or one looks like a copy of another.

    This is a guard on the sway rather than on the phase. With the old
    0.07 amplitude the four paths landed within 7% of each other, which is
    the same motion four times: the chips were too still to tell apart, not
    marching in step. Raising the sway took the closest pair to 25%, which
    is the floor the geometry allows, since chips half a cycle apart are
    exactly opposed and an opposed pair on an ellipse is as close to
    tracing one path as it gets.

    It was checked whether giving each chip its own phase would help
    instead. At this sway it changes which pair is closest and leaves the
    minimum exactly where it was, so there is no offset in the painting.
    """
    art = OrbitArt(moving=False)
    art.resize(260, 240)
    steps = 400

    def velocity(index):
        xs = [art.chip_position(index, math.tau * step / steps)[0]
              for step in range(steps + 1)]
        return [xs[n + 1] - xs[n] for n in range(steps)]

    paths = [velocity(index) for index in range(len(OrbitArt._CHIPS))]
    for first in range(len(paths)):
        for second in range(first + 1, len(paths)):
            scale = max(abs(v) for v in paths[first])
            apart = max(abs(x - y) for x, y in zip(paths[first], paths[second]))
            assert apart > scale * 0.2, (
                f"chips {first} and {second} trace the same path, within "
                f"{apart / scale * 100:.0f}% of each other, so one looks "
                f"like a copy of the other")


def test_one_cycle_is_long_enough_to_breathe(qt_app) -> None:
    """A full breath, not a fidget: the chip rests at each end of its travel."""
    art = OrbitArt(moving=False)
    cycle_ms = art._CYCLE_MS
    assert 10000 <= cycle_ms <= 25000, (
        f"a {cycle_ms}ms cycle is {'too quick to settle' if cycle_ms < 10000 else 'too slow to notice'}")


def test_no_orbit_chip_is_painted_outside_its_box(qt_app) -> None:
    """The left and right edges were clipped while top and bottom were fine.

    The box was a flat setMinimumSize(260, 240), and the sway that was added
    later to make the motion perceptible pushed the outermost chip about three
    pixels past that width. The height happened to have room to spare, so it
    looked like a clipping problem on two sides only.

    Checked over a whole cycle, because the worst case is a phase rather than
    the resting one, and against the painted chip size rather than the radius.
    """
    art = OrbitArt(moving=False)
    width, height = OrbitArt.required_size()
    art.resize(width, height)

    worst = {"left": 0.0, "right": 0.0, "top": 0.0, "bottom": 0.0}
    for step in range(721):
        phase = math.tau * step / 720
        for index in range(len(OrbitArt._CHIPS)):
            x, y = art.chip_position(index, phase)
            worst["left"] = min(worst["left"], x - OrbitArt._CHIP_W / 2)
            worst["right"] = max(worst["right"], x + OrbitArt._CHIP_W / 2)
            worst["top"] = min(worst["top"], y - OrbitArt._CHIP_H / 2)
            worst["bottom"] = max(worst["bottom"], y + OrbitArt._CHIP_H / 2)

    assert worst["left"] >= 0, (
        f"a chip is cut off on the left by {-worst['left']:.1f}px")
    assert worst["right"] <= width, (
        f"a chip is cut off on the right by {worst['right'] - width:.1f}px")
    assert worst["top"] >= 0, (
        f"a chip is cut off at the top by {-worst['top']:.1f}px")
    assert worst["bottom"] <= height, (
        f"a chip is cut off at the bottom by {worst['bottom'] - height:.1f}px")


def test_the_orbit_box_follows_its_own_geometry(qt_app) -> None:
    """It must be worked out, or the next change to the motion clips again."""
    width, _ = OrbitArt.required_size()
    original = OrbitArt._CHIP_W
    try:
        OrbitArt._CHIP_W = original + 40
        wider, _ = OrbitArt.required_size()
        assert wider > width, (
            f"a wider chip did not ask for a wider box ({width} -> {wider})")
    finally:
        OrbitArt._CHIP_W = original

    art = OrbitArt(moving=False)
    assert art.minimumSize().width() >= width, (
        f"the widget accepts {art.minimumSize().width()}px but needs {width}px")


def test_the_orbit_box_is_wider_than_the_one_that_clipped(qt_app) -> None:
    """Guards the reported symptom against the old number coming back."""
    width, _ = OrbitArt.required_size()
    assert width > 260, (
        f"the orbit box is {width}px wide, which is the width that clipped")


def test_the_orbit_box_is_the_smallest_that_fits(qt_app) -> None:
    """It should be tight, not merely large enough.

    Adding the full orbit radius to half a chip and calling it a day gives a
    box that never clips but is about thirty pixels wider than it needs to
    be, which on the welcome panel is thirty pixels of illustration pushing
    the words along. The reach is not the radius either: no chip sits at
    angle zero, so the true extreme is only found by searching the sweep.
    """
    width, height = OrbitArt.required_size()

    left, right = 0.0, 0.0
    top, bottom = 0.0, 0.0
    for step in range(721):
        phase = math.tau * step / 720
        for index in range(len(OrbitArt._CHIPS)):
            angle = (index * math.pi / 2 - math.pi / 4
                     + math.sin(phase) * OrbitArt._SWAY)
            left = min(left, -abs(math.cos(angle)) * OrbitArt._CHIP_RX - OrbitArt._CHIP_W / 2)
            right = max(right, abs(math.cos(angle)) * OrbitArt._CHIP_RX + OrbitArt._CHIP_W / 2)
            top = min(top, -abs(math.sin(angle)) * OrbitArt._CHIP_RY - OrbitArt._CHIP_H / 2)
            bottom = max(bottom, abs(math.sin(angle)) * OrbitArt._CHIP_RY + OrbitArt._CHIP_H / 2)

    assert width == math.ceil(right * 2), (
        f"the box is {width}px wide but the chips only need "
        f"{math.ceil(right * 2)}px")
    assert height == math.ceil(bottom * 2), (
        f"the box is {height}px tall but the chips only need "
        f"{math.ceil(bottom * 2)}px")
    # And the nominal radius really would have been bigger, which is why
    # adding the two together is not the same answer.
    assert width < (OrbitArt._CHIP_RX + OrbitArt._CHIP_W / 2) * 2, (
        "the box is the nominal radius, so the extremes are not being searched")


@pytest.mark.parametrize("name", [LIGHT, MIKU, DARK])
def test_model_controls_use_the_stored_theme_on_first_paint(
    qt_app, storage, name: str
) -> None:
    """The palette must reach controls at construction, before any switch."""
    storage.settings["theme"] = name
    from desktop_app.window import MainWindow

    instance = MainWindow(storage)
    try:
        image = instance.provider_logos["openai"].pixmap().toImage()
        pixels = [image.pixelColor(x, y) for x in range(image.width()) for y in range(image.height())
                  if image.pixelColor(x, y).alpha() > 128]
        assert pixels, "The real logo must be visible on first paint"
        brightness = sum(pixel.lightness() for pixel in pixels) / len(pixels)
        assert (brightness > 128) == (name != LIGHT), "Use a contrasting brand mark for each surface"
        assert instance.palette().color(QPalette.ColorRole.PlaceholderText).name() == color(name, "text_muted")
        # The orbit is hand-painted, so the stylesheet never reaches it and
        # apply_theme() does not run during startup. It used to be asserted
        # absent, which is how it stayed unmounted for so long: a later
        # commit replaced the hero panel it lived in and the assertion was
        # written to match what was left rather than to what was meant.
        assert instance.findChildren(OrbitArt), "the orbit art is not in the window"
        assert instance.welcome_art._theme == name, (
            "the orbit art was built with the wrong palette, so the welcome "
            "panel shows another theme's colours until the theme is switched")
    finally:
        instance.network.shutdown()
        instance.network.wait(10000)


def test_orbit_art_defaults_to_dark_for_a_caller_that_forgets(qt_app) -> None:
    """The fallback stays, so a future hand-painted widget cannot crash.

    Dark is the palette every token has a value for, so a missing
    argument degrades to a readable widget instead of an AttributeError
    during startup.
    """
    assert OrbitArt()._theme == DARK


def test_app_icon_differs_between_themes(qt_app) -> None:
    """The painted window icon follows the accent colour."""
    from desktop_app.window import app_icon

    dark = app_icon(DARK).pixmap(64, 64).toImage().pixelColor(32, 32)
    light = app_icon(LIGHT).pixmap(64, 64).toImage().pixelColor(32, 32)

    assert dark.name() != light.name()


@pytest.mark.parametrize("name", [LIGHT, MIKU, DARK])
def test_theme_switch_preserves_markdown_links_and_chat_history(window, name) -> None:
    """A saved reply follows the theme without changing its content."""
    reply = "[Website](https://example.test) and **a saved reply**"
    window.selected = "model"
    window.chats = {"model": {"messages": [("assistant", reply)], "history": []}}
    window.apply_theme(name)
    body = window.messages.itemAt(0).widget().findChild(MarkdownMessage)
    assert body is not None
    assert not body.viewport().autoFillBackground()
    assert body.palette().color(QPalette.ColorRole.Text).name() == color(name, "text")
    # The Base role no longer has to be the surface. A reply is now drawn on
    # the bubble behind it, so it paints nothing of its own; what has to hold
    # across a theme switch is that it is still told to paint nothing. The
    # palette is not the thing that decides this: the app-level QWidget rule
    # resolves Base back to an opaque colour under this stylesheet whatever
    # the widget sets, so the rule on the reply itself is the one to check.
    assert "background: transparent" in body.styleSheet(), (
        "the reply lost its transparency across a theme switch, so it paints "
        "an opaque slab over the bubble again")
    link_format = body.document().find("Website").charFormat()
    assert link_format.anchorHref() == "https://example.test"
    assert link_format.foreground().color().name() == color(name, "agent_title")
    assert window.chats["model"]["messages"] == [("assistant", reply)]
