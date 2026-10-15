"""Window sizing against different screens.

These are the plain-number helpers in desktop_app/layout.py, so every
screen shape can be checked without opening a window.
"""

from __future__ import annotations

import pytest

from desktop_app import layout


COMMON_SCREENS = [
    (1366, 768),    # the most common laptop panel
    (1536, 960),    # a 15-inch laptop
    (1920, 1080),   # desktop
    (2560, 1440),   # desktop
    (3840, 2160),   # 4K
    (1024, 768),    # very small
]


@pytest.mark.parametrize("screen", COMMON_SCREENS)
def test_default_size_fits_every_common_screen(screen) -> None:
    """The preferred size is clamped to fit, never opening clipped."""
    width, height = layout.window_size(None, {}, screen)

    assert width <= screen[0], f"width {width} exceeds {screen[0]}"
    assert height <= screen[1], f"height {height} exceeds {screen[1]}"


@pytest.mark.parametrize("screen", COMMON_SCREENS)
def test_size_leaves_room_for_decorations(screen) -> None:
    """A window may not claim the whole screen.

    This is the defect being fixed: a 910px window on a 768px-tall screen
    does not fit at all, and 910 of a 920px working area leaves nothing
    for the title bar and taskbar.
    """
    width, height = layout.window_size(None, {}, screen)

    assert height <= int(screen[1] * layout.MAX_AVAILABLE_FRACTION)
    assert width <= int(screen[0] * layout.MAX_AVAILABLE_FRACTION)


@pytest.mark.parametrize("screen", COMMON_SCREENS)
def test_minimum_never_exceeds_the_screen(screen) -> None:
    """A minimum larger than the screen makes the window unopenable."""
    minimum_width, minimum_height = layout.minimum_size(screen)

    assert minimum_width <= screen[0]
    assert minimum_height <= screen[1]


@pytest.mark.parametrize("screen", COMMON_SCREENS)
def test_minimum_is_never_above_the_chosen_size(screen) -> None:
    """A window must be able to shrink below its opening size."""
    width, height = layout.window_size(None, {}, screen)
    minimum_width, minimum_height = layout.minimum_size(screen)

    assert width >= minimum_width
    assert height >= minimum_height


@pytest.mark.parametrize("screen", COMMON_SCREENS)
def test_minimum_leaves_room_for_the_sidebar_to_collapse(screen) -> None:
    """The minimum width has to be below the collapse threshold.

    This is the defect being fixed: the minimum was capped at 1010 while the
    sidebar collapses at 1000, so a window could never be dragged narrow
    enough to collapse the sidebar and the collapsed layout was unreachable on
    every screen wide enough for the cap to apply. Qt enforces the minimum on
    resize, so the window simply refused to get smaller.
    """
    minimum_width, _ = layout.minimum_size(screen)

    assert minimum_width < layout.SIDEBAR_COLLAPSE_WIDTH, (
        f"the minimum width {minimum_width} on a {screen[0]}px screen is not "
        f"below the {layout.SIDEBAR_COLLAPSE_WIDTH}px collapse threshold, so "
        "the sidebar can never be collapsed by narrowing the window")


def test_small_laptop_is_the_case_that_matters() -> None:
    """1366x768 cannot fit the old fixed 910px height."""
    width, height = layout.window_size(None, {}, (1366, 768))

    assert height < 910, "the preferred height was not reduced for a small screen"
    assert height <= 768


def test_oversized_saved_size_is_clamped() -> None:
    """A size remembered on a large monitor is reduced on a small one."""
    # 2560x1440 saved, then opened on a laptop.
    width, height = layout.window_size((2560, 1440), {}, (1366, 768))

    assert width <= 1366
    assert height <= 768


def test_saved_size_is_reused_when_it_fits() -> None:
    """A remembered size on the same screen is honoured."""
    width, height = layout.window_size((1000, 700), {}, (1920, 1080))

    assert (width, height) == (1000, 700)


def test_size_read_from_settings_when_no_argument() -> None:
    """The stored preference is used when the caller passes nothing."""
    width, height = layout.window_size(None, {"window_size": [1100, 720]}, (1920, 1080))

    assert (width, height) == (1100, 720)


@pytest.mark.parametrize("stored", [None, (), "nonsense", [0, 0], [-5, 100], [100]])
def test_unusable_saved_sizes_fall_back_to_the_preference(stored) -> None:
    """Corrupt or absent stored values do not break startup."""
    width, height = layout.window_size(stored, {}, (1920, 1080))

    assert width > 0 and height > 0
    assert width <= 1920 and height <= 1080


def test_unknown_screen_size_uses_a_conservative_default() -> None:
    """With no screen information a common laptop is assumed."""
    assert layout.available_size(None) == (1366, 768)
    assert layout.available_size((0, 0)) == (1366, 768)


def test_sidebar_collapses_only_when_narrow() -> None:
    """The sidebar is dropped when there is no room for it."""
    assert layout.sidebar_should_collapse(900) is True
    assert layout.sidebar_should_collapse(layout.SIDEBAR_COLLAPSE_WIDTH - 1) is True
    assert layout.sidebar_should_collapse(layout.SIDEBAR_COLLAPSE_WIDTH) is False
    assert layout.sidebar_should_collapse(1600) is False
