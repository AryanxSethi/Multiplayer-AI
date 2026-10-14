"""Tests for the theme palettes and stylesheets.

These deliberately avoid importing Qt. The theme module is strings and
dicts, so the parity rules that matter can be checked without a display,
and the desktop test suite has no skip logic to lean on.
"""

from __future__ import annotations

import re

import pytest

from desktop_app import theme


def selectors(sheet: str) -> set:
    """The set of ``QType#name`` selectors declared in a stylesheet.

    Pseudo-states and sub-control selectors are ignored: the comparison is
    about which widgets a theme actually covers, not about every rule it
    spells out.
    """
    found = set()
    for block in re.findall(r"([^{}]+)\{", sheet):
        for part in block.split(","):
            name = part.strip().split(":")[0].strip()
            if name.startswith("Q"):
                found.add(name)
    return found


# --- parity ---------------------------------------------------------------


def test_all_palettes_define_the_same_tokens() -> None:
    """No token may exist in one theme and be missing from another.

    A missing token falls back to the dark value, so the symptom would be a
    theme with dark accents rather than an error, which is easy to miss by
    eye.
    """
    reference = set(theme.PALETTES[theme.DARK])
    for name in theme.THEME_NAMES:
        missing = reference - set(theme.PALETTES[name])
        extra = set(theme.PALETTES[name]) - reference
        assert not missing, f"{name} is missing tokens {sorted(missing)}"
        assert not extra, f"{name} has tokens the others lack: {sorted(extra)}"


def test_palette_values_are_hex_colours() -> None:
    """Every token is a #rrggbb value, or the keyword ``transparent``.

    ``transparent`` is allowed for one reason: the stylesheet is generated
    from these tokens and always writes ``1px solid``, so a theme that
    wants no border states that by taking the keyword rather than being
    given a rule of its own. That is what keeps a single template serving
    three themes without a conditional in it.
    """
    for name, tokens in theme.PALETTES.items():
        for token, value in tokens.items():
            assert value == "transparent" or re.fullmatch(r"#[0-9a-fA-F]{6}", value), (
                f"{name}.{token}={value}")


def test_all_themes_cover_the_same_widgets() -> None:
    """Every theme styles every widget the reference theme does.

    This is the check that catches a new widget being added and themed in
    only some of the stylesheets. It used to catch the opposite failure too,
    back when each sheet was written by hand: dark was missing the button
    hover and pressed fills and every primary button state, and because the
    check only asked about selectors rather than rules, it stayed silent.
    """
    reference = selectors(theme.THEMES[theme.DARK])
    assert reference, "selector extraction found nothing in the reference theme"

    for name in theme.THEME_NAMES:
        found = selectors(theme.THEMES[name])
        missing = reference - found
        extra = found - reference
        assert not missing, f"{name} theme does not style {sorted(missing)}"
        assert not extra, f"{name} theme styles unknown widgets {sorted(extra)}"


def test_every_theme_gives_every_rule_the_same_properties() -> None:
    """A rule must set the same properties in all three themes.

    The sheets are generated from one template now, so this holds by
    construction. It is kept because it is the property that matters and it
    costs one pass, and because it would have caught the hand-written
    sheets diverging in shape: dark declared ``QPushButton#primary`` with
    no background, while light and miku declared a fill plus hover,
    pressed and disabled rules.

    It checks themes against each other, not against a required set: a rule
    deleted from the template disappears from all three at once and this
    would still pass. ``test_every_theme_declares_the_interaction_states``
    covers that half.
    """

    def properties(sheet: str):
        found = {}
        for line in sheet.splitlines():
            line = line.strip()
            if not line.endswith("}") or "{" not in line:
                continue
            selector, _, body = line.partition("{")
            names = [part.partition(":")[0].strip()
                     for part in body.rstrip("}").split(";") if part.strip()]
            found[selector.strip()] = frozenset(names)
        return found

    reference = properties(theme.THEMES[theme.DARK])
    for name in theme.THEME_NAMES:
        found = properties(theme.THEMES[name])
        for selector, expected in reference.items():
            assert found.get(selector) == expected, (
                f"{name} {selector} sets {sorted(found.get(selector, ()))} "
                f"but dark sets {sorted(expected)}")


def test_every_theme_declares_the_interaction_states() -> None:
    """Every state a control needs must exist in every theme.

    Dark shipped for years with no button hover fill, no button pressed
    fill, and no hover, pressed or disabled rule for its primary button at
    all, because nothing asserted they were there: the parity check only
    compared themes with each other, and a rule absent from all three looks
    perfectly consistent. So the states are named explicitly here.
    """
    required = (
        "QPushButton:hover",
        "QPushButton:pressed",
        "QPushButton:disabled",
        "QPushButton#primary",
        "QPushButton#primary:hover",
        "QPushButton#primary:pressed",
        "QPushButton#primary:disabled",
        "QPushButton#nav:hover",
        "QPushButton#nav:checked",
        "QPushButton#ghost:hover",
        "QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus",
        # Buttons and checkboxes. An application stylesheet replaces Qt's own
        # painting, so a widget the sheet says nothing about :focus for draws
        # no focus at all -- and the sheet covered inputs only. Tabbing the nav
        # rail or the top bar gave no indication of where the reader was.
        "QPushButton:focus",
        "QPushButton#nav:focus",
        "QPushButton#ghost:focus",
        "QPushButton#workspace:focus",
        "QCheckBox:focus::indicator",
        "QListWidget:focus",
        # The danger button, which carries the most consequential actions in
        # the app -- leave, delete, reset identity, remove member -- and had a
        # resting rule and nothing else.
        "QPushButton#danger:hover",
        "QPushButton#danger:pressed",
        "QPushButton#danger:disabled",
        "QListWidget::item:selected",
        "QListWidget::item:hover",
        "QMenu::item:selected",
    )
    for name in theme.THEME_NAMES:
        sheet = theme.THEMES[name]
        for selector in required:
            assert f"{selector} {{" in sheet, f"{name} has no rule for {selector}"


def test_a_hovered_button_changes_appearance() -> None:
    """A hover has to be visible, not merely declared.

    A rule that sets a background identical to the resting one leaves the
    control looking dead under the cursor, which is what dark's
    ``QPushButton:hover`` did: it moved only the border colour.
    """
    def fill(sheet: str, selector: str):
        for line in sheet.splitlines():
            line = line.strip()
            if not line.startswith(selector + " {"):
                continue
            for part in line.rstrip("}").split("{", 1)[1].split(";"):
                key, _, value = part.partition(":")
                if key.strip() == "background":
                    return value.strip().lower()
        return None

    for name in theme.THEME_NAMES:
        sheet = theme.THEMES[name]
        resting = fill(sheet, "QPushButton")
        hovered = fill(sheet, "QPushButton:hover")
        assert hovered is not None, f"{name} sets no hover background"
        assert hovered != resting, (
            f"{name} hovers a button from {resting} to {hovered}, which is no change")


def test_no_stylesheet_contains_a_hard_coded_colour() -> None:
    """Every colour in a sheet has to come from a palette.

    A hex typed straight into the template is a colour that no palette can
    change, which is the drift this arrangement exists to prevent.
    """
    values = {value.lower() for tokens in theme.PALETTES.values()
              for value in tokens.values()}
    for name in theme.THEME_NAMES:
        sheet = theme.THEMES[name].lower()
        for literal in set(re.findall(r"#[0-9a-f]{6}", sheet)):
            assert literal in values, f"{name} sheet hard-codes {literal}"


def test_a_token_the_sheet_needs_cannot_go_missing() -> None:
    """Reading a token the palette lacks must raise rather than fall back.

    ``color()`` already behaves this way for hand-painted widgets; the
    generated sheet has to as well, or a renamed token would quietly render
    with the wrong colour instead of stopping the build.
    """
    for name in theme.THEME_NAMES:
        broken = dict(theme.PALETTES[name])
        broken["surface_base"] = "#123456"
        del broken["border"]

        with pytest.raises(KeyError):
            theme._stylesheet(broken)


@pytest.mark.parametrize(
    "name",
    ["rail", "sidebar", "topbar", "profile", "card", "stat", "settings", "hero",
     "title", "heroTitle", "heading", "muted", "eyebrow", "statValue", "online",
     "primary", "nav", "workspace", "ghost", "danger"],
)
def test_named_widget_styled_in_both_themes(name: str) -> None:
    """Each objectName used in the UI is styled in both themes.

    Kept as an explicit list so adding a widget without theming it fails
    here rather than showing up as an unstyled box.
    """
    for theme_name, sheet in theme.THEMES.items():
        assert f"#{name}" in sheet, f"{theme_name} theme does not style #{name}"


def test_every_provider_has_an_accent_in_both_themes() -> None:
    """Provider accents exist per theme, so light does not borrow dark hues."""
    for theme_name in theme.THEME_NAMES:
        accents = theme.PROVIDER_ACCENTS[theme_name]
        assert set(accents) == set(theme.PROVIDER_NAMES)
        for key, value in accents.items():
            assert re.fullmatch(r"#[0-9a-fA-F]{6}", value), f"{theme_name}.{key}={value}"


# --- accessors ------------------------------------------------------------


def test_palette_falls_back_to_dark_for_unknown_theme() -> None:
    """A bad theme name must not raise.

    The value comes from a user-editable settings file, so a typo there
    should degrade to a working window rather than a crash on startup.
    """
    assert theme.palette("nonsense") == theme.PALETTES[theme.DARK]


def test_color_raises_on_an_unknown_token() -> None:
    """An unknown token raises rather than silently substituting a colour.

    A theme name can come from a user-edited settings file and so must
    degrade quietly. A token can only be wrong because of a typo, and
    quietly returning some other colour would ship an invisible mistake.
    """
    with pytest.raises(KeyError):
        theme.color(theme.LIGHT, "not_a_token")


def test_stylesheet_falls_back_to_dark() -> None:
    """An unknown theme name returns the dark stylesheet."""
    assert theme.stylesheet("nonsense") == theme.THEME


def test_each_theme_retunes_its_accent() -> None:
    """Accents are per theme, not copied from the reference.

    A single shared accent would make a theme look washed out, and nothing
    else in this file would catch that.
    """
    accents = {name: theme.color(name, "accent") for name in theme.THEME_NAMES}
    assert len(set(accents.values())) == len(theme.THEME_NAMES), accents


@pytest.mark.parametrize(
    "token", ["text", "text_muted", "success", "error", "agent_title"]
)
def test_each_theme_retunes_text_tokens(token: str) -> None:
    """Text-bearing tokens differ per theme, so contrast is retuned."""
    values = {name: theme.color(name, token) for name in theme.THEME_NAMES}
    assert len(set(values.values())) == len(theme.THEME_NAMES), values


# --- theme resolution -----------------------------------------------------


def test_resolve_theme_honours_an_explicit_choice() -> None:
    """A stored choice wins over the OS preference."""
    for name in theme.THEME_NAMES:
        assert theme.resolve_theme({"theme": name}, app=object()) == name


def test_resolve_theme_follows_the_system_when_unset(monkeypatch) -> None:
    """With no stored choice the OS preference is used."""
    monkeypatch.setattr(theme, "system_theme", lambda app=None: theme.LIGHT)
    assert theme.resolve_theme({}) == theme.LIGHT


def test_resolve_theme_falls_back_to_dark(monkeypatch) -> None:
    """An unknown stored value, or no OS preference, means dark."""
    monkeypatch.setattr(theme, "system_theme", lambda app=None: None)
    assert theme.resolve_theme({"theme": "chartreuse"}) == theme.DARK
    assert theme.resolve_theme({}) == theme.DARK


def test_resolve_theme_tolerates_missing_settings() -> None:
    """None settings must not raise."""
    assert theme.resolve_theme(None) in theme.THEME_NAMES


# --- providers ------------------------------------------------------------


def test_provider_names_keeps_its_shape() -> None:
    """Each entry stays a 4-tuple, as the UI unpacks it positionally."""
    for key, entry in theme.PROVIDER_NAMES.items():
        assert isinstance(entry, tuple) and len(entry) == 4, key


def test_provider_entry_is_theme_aware() -> None:
    """Each theme exposes provider accents usable on its own surfaces.

    Themes are allowed to share an accent set: Miku reuses the dark one
    because its surfaces are also dark. What matters is legibility, which
    tests/test_theme_contrast.py checks against each theme's card colour.
    """
    for name in theme.THEME_NAMES:
        entry = theme.provider_entry(name, "openai")
        assert entry[3] == theme.PROVIDER_ACCENTS[name]["openai"], name
        # Name, blurb and glyph are theme-independent.
        assert entry[:3] == theme.provider_entry(name, "openai")[:3]


def test_unknown_provider_falls_back_without_raising() -> None:
    """An agent record naming a provider this build lacks still renders."""
    entry = theme.provider_entry(theme.LIGHT, "a-provider-from-the-future")

    assert entry[0] == "Connectivity agent"
    assert entry[2] == "⌘"  # preserved from the pre-theme agent list
    assert entry[3] == theme.color(theme.LIGHT, "provider_fallback")


def test_every_theme_is_offered_under_a_name_a_reader_can_use():
    """One of the choices was labelled "39".

    That is the song the palette was drawn from. It is not what anybody
    looking at a dropdown is meant to recognise it by, and the hint under the
    picker now has to say the name back.
    """
    for name, _ in theme.THEME_CHOICES:
        assert not name.isdigit(), (
            f"{name!r} is where the palette comes from, not a name for it")
        assert len(name) > 1, f"{name!r} is not something a reader can choose"


def test_every_button_reachable_by_keyboard_advertises_that_it_is_focused() -> None:
    """A focus rule the sheet does not declare is not drawn by Qt.

    An application stylesheet replaces the widget's own painting, so a control
    the sheet has no :focus rule for is painted exactly as it is at rest. The
    sheet covered text inputs and the stat readouts and nothing else, which
    left every button in the app with no visible focus -- and the nav rail,
    the top bar and the composers are all used without a mouse.
    """
    for name in theme.THEME_NAMES:
        sheet = theme.THEMES[name]
        assert "QPushButton:focus" in sheet, f"{name}: no focus rule for a button"
        assert "QPushButton#nav:focus" in sheet, f"{name}: the nav rail has no focus rule"
        assert "QPushButton#ghost:focus" in sheet, f"{name}: ghost buttons have no focus rule"
        assert "QCheckBox:focus::indicator" in sheet, f"{name}: a checkbox has no focus rule"
        assert "QListWidget:focus" in sheet, f"{name}: the activity list has no focus rule"


def test_the_danger_button_is_not_the_one_with_no_states() -> None:
    """Its resting rule was the whole of it.

    Leave workspace, Delete workspace, Reset local identity and every Remove
    member button use this style, so the buttons a reader is most likely to
    regret pressing gave no feedback at all under the cursor.
    """
    for name in theme.THEME_NAMES:
        sheet = theme.THEMES[name]
        resting = sheet.split("QPushButton#danger {")[1].split("}")[0].strip()
        for state in (":hover", ":pressed", ":disabled"):
            rule = "QPushButton#danger" + state + " {"
            assert rule in sheet, f"{name}: the danger button has no {state} rule"
            body = sheet.split(rule)[1].split("}")[0].strip()
            assert body != resting, (
                f"{name}: QPushButton#danger{state} paints exactly what it paints "
                f"at rest, so the state is invisible")
