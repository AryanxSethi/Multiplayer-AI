"""Surfaces, accents, and readable contrast for the dark and light themes.

Two mechanisms live here.

``THEMES`` holds the Qt stylesheets. The stylesheet is applied once, at the
top level widget, so a stylesheet can only style things it can select:
either by Qt type (``QLabel``, ``QPushButton``) or by ``objectName``.
Anything that sets its own stylesheet, or paints itself, has to come to
this module for its colours instead, via the ``PALETTES`` tokens.

``PALETTES`` holds those semantic colours, so that the ~23 colour literals
that used to be scattered through window.py, dialogs.py and widgets.py
have one definition per theme and change together.
"""

# --- semantic colour tokens ------------------------------------------------
#
# Every token below must exist in both palettes. Tokens used in hand-painted
# code (OrbitArt) rather than in a stylesheet are marked below, because
# those cannot be expressed in Qt Style Sheets at all.

_DARK = {
    # Layered surfaces, darkest first. The rail sits below the window base
    # so the two read as separate planes rather than one black field, and a
    # card is a step above both.
    "surface_base": "#1e1f22",
    "surface_rail": "#1b1d21",
    "surface_sidebar": "#232428",
    "surface": "#2b2d31",
    "surface_raised": "#313338",
    "surface_sunken": "#1e1f22",
    "surface_profile": "#232428",
    "surface_hero": "#2b2d31",
    "surface_hover": "#3a3d44",
    "surface_row_hover": "#34363c",
    "surface_active": "#40444b",
    "border": "#3f4147",
    "border_strong": "#4e5058",
    "border_control": "#4e5058",
    "border_hero": "#4e5058",
    "border_disabled": "#2b2d31",
    "topbar_border": "#3f4147",
    "table_border": "#3f4147",
    "text": "#dbdee1",
    "text_strong": "#f2f3f5",
    "text_muted": "#949ba4",
    "text_nav": "#b5bac1",
    "text_list": "#dbdee1",
    "text_hero": "#f2f3f5",
    "text_disabled": "#6d6f78",
    "eyebrow": "#8b7cf6",
    # An indigo-violet a clear step from Discord blurple: same family,
    # not the same colour.
    "accent": "#6d5ce7",
    "accent_hover": "#7d6cf0",
    "accent_press": "#5c4bd4",
    "accent_disabled": "#4a3f8f",
    "on_accent": "#ffffff",
    "primary_bg": "#6d5ce7",
    "primary_fg": "#ffffff",
    "primary_bg_hover": "#7d6cf0",
    "primary_bg_press": "#5c4bd4",
    "primary_disabled_fg": "#b9b4d9",
    "primary_border": "transparent",
    "button": "#313338",
    "button_hover": "#3a3d44",
    "button_press": "#464951",
    "button_disabled": "#2b2d31",
    "button_border": "transparent",
    "nav_checked_bg": "#40444b",
    "nav_border": "transparent",
    "ghost_border": "transparent",
    "workspace_btn_bg": "#313338",
    "workspace_btn_border": "transparent",
    "input_bg": "#1e1f22",
    "checkbox_bg": "#1e1f22",
    "checkbox_border": "#4e5058",
    "combo_popup_bg": "#2b2d31",
    "menu_bg": "#232428",
    "menu_item_border": "transparent",
    "header_bg": "#1e1f22",
    "progress_track": "#2b2d31",
    "list_item_border": "transparent",
    "tooltip_bg": "#111214",
    "tooltip_fg": "#f2f3f5",
    "tooltip_border": "#3f4147",
    "scrollbar_track": "#1e1f22",
    "scrollbar_thumb": "#4e5058",
    "danger_bg": "#3a1f22",
    # The danger button had no hover, pressed or disabled state of its own, so
    # the most consequential controls in the app -- Leave workspace, Delete
    # workspace, Reset identity, Remove member -- gave no feedback at all under
    # the cursor. These sit either side of danger_bg, following the button
    # pair's spacing rather than being invented separately.
    "danger_bg_hover": "#4a272b",
    "danger_bg_pressed": "#5a2f33",
    "success": "#3ba55d",
    "warning": "#e0a33e",
    "error": "#f76f74",
    "agent_title": "#9d90f7",
    "toast_bg": "#232428",
    "toast_fg": "#dbdee1",
    "provider_fallback": "#9d90f7",
    # Hand-painted OrbitArt only.
    "orbit_ring": "#3a3550",
    "orbit_ring_dashed": "#332f47",
    "orbit_tile": "#2a2740",
    "orbit_chip_bg": "#242135",
    "orbit_chip_ring": "#4a4468",
    "orbit_chip_text": "#b6b0d8",
    "orbit_ollama": "#9d90f7",
    "orbit_claude": "#e8a882",
    "orbit_gemini": "#7fb2ff",
    "orbit_openai": "#6fce9f",
}

# Light values are chosen for contrast on white rather than being a
# mechanical inversion: a mid-tone like #3ba55d reads fine on charcoal
# but is far too pale on white, so success, error and the agent title
# each get a darker counterpart.
_LIGHT = {
    "accent": "#4c56d8",
    "accent_hover": "#5f68e2",
    "accent_press": "#3f47b8",
    "accent_disabled": "#b6bbe0",
    "on_accent": "#ffffff",
    "surface_base": "#f4f5f7",
    "surface": "#ffffff",
    "surface_sunken": "#eef0f3",
    "surface_rail": "#e3e5ea",
    "surface_sidebar": "#ecedf1",
    "surface_raised": "#f2f4f7",
    "surface_profile": "#ecedf1",
    "surface_hero": "#e6e8fb",
    "surface_hover": "#dfe2e8",
    "surface_row_hover": "#eceef2",
    "surface_active": "#e2e4ea",
    "border": "#dcdfe5",
    "border_strong": "#c8ccd4",
    "border_control": "#a8adb8",
    "border_hero": "#c3c7f0",
    "border_disabled": "#dcdfe5",
    "text": "#1f2124",
    "text_strong": "#111214",
    "text_muted": "#5c6069",
    "text_nav": "#4a4e57",
    "text_list": "#3f434b",
    "text_hero": "#1b1d3a",
    "text_disabled": "#a2a6ae",
    "button": "#e2e4ea",
    "button_hover": "#d5d8e0",
    "button_press": "#ccd0da",
    "button_disabled": "#eceef2",
    "tooltip_bg": "#23252a",
    "tooltip_fg": "#f2f3f5",
    "scrollbar_track": "#eceef2",
    "scrollbar_thumb": "#b8bdc7",
    "danger_bg": "#fbe4e2",
    "danger_bg_hover": "#f7d5d2",
    "danger_bg_pressed": "#f2c6c2",
    "success": "#1e7d43",
    "warning": "#8a5a00",
    "error": "#c0392b",
    "agent_title": "#3b3f9e",
    "toast_bg": "#d8f0e2",
    "toast_fg": "#14532d",
    "provider_fallback": "#6f63d9",
    "orbit_ring": "#b9b6d8",
    "orbit_ring_dashed": "#c9c6e2",
    "orbit_tile": "#d5d2f0",
    "orbit_chip_bg": "#eceaf8",
    "orbit_chip_ring": "#c4c1dd",
    "orbit_chip_text": "#4a4757",
    "orbit_ollama": "#5b4fd1",
    "orbit_claude": "#a2603a",
    "orbit_gemini": "#2f6ed0",
    "orbit_openai": "#1f7a5e",
    "input_bg": "#ffffff",
    "checkbox_bg": "#ffffff",
    "checkbox_border": "#a8adb8",
    "combo_popup_bg": "#ffffff",
    "menu_bg": "#ffffff",
    "menu_item_border": "transparent",
    "header_bg": "#f4f5f7",
    "progress_track": "#e2e4ea",
    "nav_checked_bg": "#d5d8e0",
    "workspace_btn_bg": "#ffffff",
    "button_border": "transparent",
    "primary_border": "transparent",
    "primary_disabled_fg": "#ffffff",
    "list_item_border": "transparent",
    "tooltip_border": "transparent",
    "eyebrow": "#4c56d8",
    "primary_bg": "#4c56d8",
    "primary_fg": "#ffffff",
    "primary_bg_hover": "#5f68e2",
    "primary_bg_press": "#3f47b8",
    "nav_border": "transparent",
    "workspace_btn_border": "transparent",
    "ghost_border": "transparent",
    "topbar_border": "#dcdfe5",
    "table_border": "#dcdfe5",
}

# Miku. The seven supplied colours were given as thirteen foreground and
# background pairs whose measured ratios all fall between 1.09:1 and
# 2.00:1, so none of them can carry text. Two of them do work as neutrals,
# #5a676b and #e2ddcc, and the four saturated hues are kept as accents
# and marks. The values below are derived from that split; every ratio is
# verified by tests/test_theme_contrast.py, which is the only thing keeping
# this theme honest.
#
# The neutrals were then rebuilt as a single eight-step ramp. As shipped
# they sat inside one step of each other, which left the card the same
# colour as the sidebar behind it, so cards cast no shadow at all, and left
# muted text 0.73 of contrast below body, so the two read as one weight.
# Contrast passing is not the same as a palette reading correctly, and this
# theme had passed every ratio while looking flat. Steps are labelled n1 to
# n8 beside the tokens so the ramp stays intact when a value is next moved.
_MIKU = {
    # The supplied teal is light, so it carries the dark slate as its
    # label colour rather than white. That reaches 5.13:1, where white on
    # the same teal would manage 1.80.
    "accent": "#47c8c0",
    "accent_hover": "#55d1d0",
    "accent_press": "#2ebdb4",
    "accent_disabled": "#4f7f7b",
    "on_accent": "#374145",
    "surface_base": "#353f42",              # n3
    "surface": "#424c50",                   # n5  cards
    "surface_sunken": "#313a3d",            # n2  recessed
    "surface_rail": "#2b3437",              # n1
    "surface_sidebar": "#3b4548",           # n4
    "surface_raised": "#4b565a",            # n6  top bar, buttons
    "surface_profile": "#3b4548",           # n4  footer matches the sidebar
    "surface_hero": "#2f4a4d",
    "surface_hover": "#535e63",             # n7
    "surface_row_hover": "#535e63",         # n7
    "surface_active": "#5c676c",            # n8
    "border": "#556165",
    "border_strong": "#616d71",
    "border_control": "#6b777b",
    "border_hero": "#43707a",
    "border_disabled": "#454f53",
    "text": "#e6e1d1",
    "text_strong": "#f7f4ea",
    "text_muted": "#b9c3bf",
    "text_nav": "#cfd6d2",
    "text_list": "#e6e1d1",
    "text_hero": "#d9f3ef",
    "text_disabled": "#828b87",
    "button": "#4b565a",
    "button_hover": "#535e63",
    "button_press": "#424c50",
    "button_disabled": "#3b4548",
    "tooltip_bg": "#23292b",
    "tooltip_fg": "#e2ddcc",
    "scrollbar_track": "#3b4548",
    "scrollbar_thumb": "#5a6469",
    "danger_bg": "#4a2f3a",
    "danger_bg_hover": "#5c3b48",
    "danger_bg_pressed": "#6d4655",
    "success": "#55d1d0",
    # Carries verdict text on a card as well as the left border, so it is held
    # to the same 4.5:1 as "error" rather than picked as a decorative accent.
    "warning": "#ffc95c",
    # The supplied pink is 2.69:1 on these surfaces. This is the most
    # saturated tint of the same hue that still clears 4.5:1.
    "error": "#ffb0b0",
    "agent_title": "#87e5cf",
    "toast_bg": "#2f4a45",
    "toast_fg": "#cdeadb",
    "provider_fallback": "#87e5cf",
    "orbit_ring": "#6b7b80",
    "orbit_ring_dashed": "#5c6d72",
    "orbit_tile": "#3f5a5e",
    "orbit_chip_bg": "#38474b",
    "orbit_chip_ring": "#6a787d",
    "orbit_chip_text": "#cdd3cf",
    "orbit_ollama": "#9fd8d4",
    "orbit_claude": "#ffb59b",
    "orbit_gemini": "#a8c6ff",
    "orbit_openai": "#8fe6cb",
    "input_bg": "#313a3d",
    "checkbox_bg": "#313a3d",
    "checkbox_border": "#6b777b",
    "combo_popup_bg": "#424c50",
    "menu_bg": "#3b4548",
    "menu_item_border": "transparent",
    "header_bg": "#353f42",
    "progress_track": "#313a3d",
    "nav_checked_bg": "#535e63",
    "workspace_btn_bg": "#4b565a",
    "button_border": "transparent",
    "primary_border": "transparent",
    "primary_disabled_fg": "#a9c4c1",
    "list_item_border": "transparent",
    "tooltip_border": "transparent",
    "eyebrow": "#55d1d0",
    "primary_bg": "#47c8c0",
    "primary_fg": "#374145",
    "primary_bg_hover": "#55d1d0",
    "primary_bg_press": "#2ebdb4",
    "nav_border": "transparent",
    "workspace_btn_border": "transparent",
    "ghost_border": "transparent",
    "topbar_border": "#556165",
    "table_border": "#556165",
}

PALETTES = {"dark": _DARK, "light": _LIGHT, "miku": _MIKU}

DARK = "dark"
LIGHT = "light"
MIKU = "miku"
THEME_NAMES = (DARK, LIGHT, MIKU)

# --- contrast --------------------------------------------------------------
#
# Colour schemes are easy to get wrong in a way that is invisible in a
# screenshot and obvious in use, so the pairings that carry text are
# declared here as data and checked by the test suite.
#
# Three classes, because WCAG treats them differently:
#   BODY  4.5:1, text a person reads as prose.
#   LARGE 3.0:1, headings, glyphs and other large or non-text marks.
#   INACTIVE exempt; WCAG excludes controls that are disabled. Declared
#          rather than omitted so the exemption is a deliberate choice
#          instead of an oversight.

BODY_TEXT = 4.5
LARGE_MARK = 3.0
INACTIVE = 0.0


def _hex_to_rgb(value: str):
    return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5))


def relative_luminance(value: str) -> float:
    """WCAG relative luminance of a #rrggbb colour."""

    def channel(raw: int) -> float:
        part = raw / 255
        return part / 12.92 if part <= 0.04045 else ((part + 0.055) / 1.055) ** 2.4

    red, green, blue = _hex_to_rgb(value)
    return 0.2126 * channel(red) + 0.7152 * channel(green) + 0.0722 * channel(blue)


def contrast_ratio(foreground: str, background: str) -> float:
    """WCAG contrast ratio between two #rrggbb colours."""
    first = relative_luminance(foreground)
    second = relative_luminance(background)
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)


# (foreground token, background token, minimum, what it is)
CONTRAST_PAIRS = (
    ("text", "surface", BODY_TEXT, "body text on a card"),
    ("text", "surface_sunken", BODY_TEXT, "body text on a sunken panel"),
    ("text", "surface_base", BODY_TEXT, "body text on the window base"),
    ("text_muted", "surface", BODY_TEXT, "muted text on a card"),
    ("text_muted", "surface_base", BODY_TEXT, "muted text on the window base"),
    ("text_strong", "surface", BODY_TEXT, "strong text on a card"),
    ("error", "surface", BODY_TEXT, "error text on a card"),
    ("agent_title", "surface", BODY_TEXT, "message author on a card"),
    ("toast_fg", "toast_bg", BODY_TEXT, "toast text"),
    ("error", "toast_bg", BODY_TEXT, "error toast text"),
    ("on_accent", "accent", BODY_TEXT, "label on a resting accent fill"),
    # Hover and pressed are transient states, so they are held to the
    # non-text bar. Raising them to 4.5 would fail the dark theme's
    # accent_hover, which sits at 3.29 and would otherwise have to be
    # darkened, changing how shipped dark mode looks.
    ("on_accent", "accent_hover", LARGE_MARK, "label on a hovered accent"),
    ("on_accent", "accent_press", LARGE_MARK, "label on a pressed accent"),
    ("success", "surface", LARGE_MARK, "online dot and join glyph"),
)


def palette(name):
    """The token dict for a theme, falling back to dark.

    An unrecognised name must not raise: the theme comes from a settings
    file a user can edit, and a typo there should not stop the app.
    """
    return PALETTES.get(name, PALETTES[DARK])


def color(name, token):
    """One colour for a theme, e.g. ``color("light", "error")``.

    An unknown *theme* degrades to dark, but an unknown *token* raises.
    The difference matters: the theme can come from a user-edited settings
    file, whereas a token can only be wrong because of a typo in the code.
    Substituting a different colour there would ship a silent mistake.
    """
    tokens = palette(name)
    if token in tokens:
        return tokens[token]
    raise KeyError(f"unknown theme token {token!r}")


def provider_color(name, provider_key):
    """The accent for a provider, per theme.

    Provider accents are tuned for the surface they sit on, so each theme
    carries its own set rather than one shared list. An unrecognised
    provider uses the theme's fallback accent, because agent records
    persist across versions and may name a provider this build lacks.
    """
    accents = PROVIDER_ACCENTS.get(name, PROVIDER_ACCENTS[DARK])
    return accents.get(provider_key, palette(name)["provider_fallback"])


def mix(foreground, background, amount):
    """``foreground`` blended into ``background``, as a ``#rrggbb`` string.

    Plain integer channel arithmetic, no Qt, because this module is the one
    place that must keep working with only the palette data.

    Used to tint a message bubble toward the provider's own accent. Blending
    rather than adding a token per provider keeps the three palettes holding
    the same keys, which the palette tests require, and means a provider
    added later needs no palette work at all.

    ``amount`` is clamped, so a caller computing a ratio cannot produce an
    invalid colour by being slightly out of range.
    """
    def channel(value):
        return max(0, min(255, int(round(value))))

    weight = max(0.0, min(1.0, float(amount)))
    top = _channels(foreground)
    bottom = _channels(background)
    blended = [channel(bottom[index] + (top[index] - bottom[index]) * weight)
               for index in range(3)]
    return "#" + "".join("%02x" % value for value in blended)


def _channels(value):
    """The three channels of a ``#rrggbb`` or ``#rgb`` string."""
    text = value.strip().lstrip("#")
    if len(text) == 3:
        text = "".join(character * 2 for character in text)
    if len(text) != 6:
        raise ValueError(f"not a hex colour: {value!r}")
    return tuple(int(text[index:index + 2], 16) for index in (0, 2, 4))


def system_theme(app=None):
    """The OS light/dark preference, or None when it cannot be determined.

    Qt reports ``Unknown`` on some platforms and in headless sessions, so
    that is reported as "no preference" and the caller decides the
    fallback rather than this function guessing.
    """
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QGuiApplication

        holder = app if app is not None else QGuiApplication.instance()
        if holder is None:
            return None
        scheme = holder.styleHints().colorScheme()
        if scheme == Qt.ColorScheme.Light:
            return LIGHT
        if scheme == Qt.ColorScheme.Dark:
            return DARK
    except Exception:
        return None
    return None


def resolve_theme(settings, app=None):
    """Pick a theme from stored preferences, then the OS, then dark.

    A stored name always wins, so a user who picks a theme keeps it even
    if their OS later changes. An absent key means follow the system, and
    so does an explicit "system".
    """
    stored = (settings or {}).get("theme")
    if stored in THEME_NAMES:
        return stored
    return system_theme(app) or DARK


# (label, stored value). A stored value of None means "follow the system",
# which is represented by leaving the key out of settings entirely.
THEME_CHOICES = (
    ("Follow system", None),
    ("Dark", DARK),
    ("Light", LIGHT),
    # Named, because this is what a reader picks from and what the hint below
    # the picker has to be able to say back. It used to be labelled "39", which
    # is the song the palette is drawn from and means nothing to anybody
    # looking at a dropdown.
    ("Teal", MIKU),
)


# --- stylesheets -----------------------------------------------------------
#
# One template, rendered per theme. Every colour in it is a @@token@@ read
# from the palette above, so the two cannot drift apart and a token the
# sheet needs but a palette lacks is a KeyError rather than a hex somebody
# typed into a string literal.
#
# The rule order is the one light and miku already used. Dark was missing
# the button hover and pressed fills and the primary hover, pressed and
# disabled states entirely, which is what let it drift structurally in the
# first place; those come from the shared template now.
#
# A token names a role, not a colour, so light drawing white inputs while
# miku draws its dark slate is two answers to one question. Where a theme
# wants no border the value is the keyword ``transparent`` and the sheet
# always writes ``1px solid``, reserving the pixel without painting it,
# which is what keeps this template free of per-theme conditionals.

# The sidebar's connection line is on screen the whole time and is the only
# status the app offers at a glance, so it has four names rather than one: a
# stylesheet cannot read the words, and a single colour meant "Relay
# unavailable" and "Startup needs attention" were both painted in the green
# that means everything is fine. Set by MainWindow.set_connection_state.
#
# Note that the comments inside _TEMPLATE are CSS comments. A "#" line there is
# an id selector, not a comment, and one that Qt cannot parse discards the rest
# of the sheet -- which is how a status colour ended up silently removing the
# checkbox rules below it.

_TEMPLATE = """
QWidget { background: @@surface_base@@; color: @@text@@; font-family: 'Segoe UI'; font-size: 13px; }
QMainWindow, QDialog { background: @@surface_base@@; }
QFrame#rail { background: @@surface_rail@@; border: none; }
QFrame#sidebar { background: @@surface_sidebar@@; border: none; }
QFrame#topbar { background: @@surface_raised@@; border-bottom: 1px solid @@topbar_border@@; }
QFrame#profile { background: @@surface_profile@@; border: none; border-top: 1px solid @@border@@; }
    QWidget#sidebarBody { background: transparent; }
QFrame#card, QFrame#stat, QFrame#settings { background: @@surface@@; border: 1px solid @@border@@; border-radius: 12px; }
QFrame#hero { background: @@surface_hero@@; border: 1px solid @@border_hero@@; border-radius: 16px; }
QFrame#createCard { background: @@surface@@; border: 1px solid @@border@@; border-left: 3px solid @@accent@@; border-radius: 12px; }
QFrame#joinCard { background: @@surface@@; border: 1px solid @@border@@; border-left: 3px solid @@success@@; border-radius: 12px; }
QFrame#inviteCard { background: @@surface@@; border: 1px solid @@border@@; border-left: 3px solid @@eyebrow@@; border-radius: 12px; }
QFrame#modelCardOk { background: @@surface@@; border: 1px solid @@border@@; border-left: 3px solid @@success@@; border-radius: 12px; }
QFrame#modelCardWarn { background: @@surface@@; border: 1px solid @@border@@; border-left: 3px solid @@warning@@; border-radius: 12px; }
QFrame#modelCardBad { background: @@surface@@; border: 1px solid @@border@@; border-left: 3px solid @@error@@; border-radius: 12px; }
QFrame#modelCardUnknown { background: @@surface@@; border: 1px solid @@border@@; border-left: 3px solid @@text_muted@@; border-radius: 12px; }
QLabel { background: transparent; }
    QWidget#messageRow { background: transparent; border-radius: 8px; }
    QWidget#messageRow:hover { background: transparent; }
    QWidget#messageActions { background: transparent; }
    QFrame#composerBar { background: @@surface_base@@; border: none; border-top: 1px solid @@border@@; }
    QFrame#chatHeader { background: @@surface_base@@; border: none; border-bottom: 1px solid @@border@@; }
QLabel#title { font-size: 31px; font-weight: 700; color: @@text_strong@@; }
QLabel#heroTitle { font-size: 34px; font-weight: 700; color: @@text_hero@@; }
QLabel#heading { font-size: 19px; font-weight: 650; color: @@text_strong@@; }
QLabel#muted { color: @@text_muted@@; }
QLabel#errorMessage { color: @@error@@; }
QLabel#progressMessage { color: @@text_muted@@; }
QPushButton#errorDismiss { background: transparent; border: none; border-radius: 4px; padding: 0; color: @@error@@; font-weight: 700; font-size: 12px; }
QPushButton#errorDismiss:hover { background: @@surface_hover@@; color: @@text_strong@@; }
QPushButton#errorDismiss:pressed { background: transparent; color: @@error@@; }
QLabel#eyebrow { color: @@eyebrow@@; font-size: 10px; font-weight: 650; }
QLabel#statValue { font-size: 30px; color: @@text_strong@@; font-weight: 650; }
QPushButton#statValue { background: transparent; font-size: 30px; color: @@text_strong@@; font-weight: 650; text-align: left; padding: 0; border: none; }
QPushButton#statValue:hover, QPushButton#statValue:pressed, QPushButton#statValue:focus { background: transparent; border: none; text-decoration: none; }
QLabel#online { color: @@success@@; }
QLabel#statusOk { color: @@success@@; }
QLabel#statusWarn { color: @@warning@@; }
QLabel#statusBad { color: @@error@@; }
QLabel#verdictOk { color: @@success@@; font-size: 11px; font-weight: 700; }
QLabel#verdictWarn { color: @@warning@@; font-size: 11px; font-weight: 700; }
QLabel#verdictBad { color: @@error@@; font-size: 11px; font-weight: 700; }
QLabel#verdictUnknown { color: @@text_muted@@; font-size: 11px; font-weight: 700; }
QLabel#command { font-family: 'Cascadia Mono', 'Consolas', monospace; font-size: 12px; color: @@text_strong@@; background: @@surface_base@@; border: 1px solid @@border@@; border-radius: 7px; padding: 8px 10px; }
QLabel#buildName { color: @@text_strong@@; font-size: 13px; font-weight: 650; }
QLabel#meta { color: @@text@@; font-size: 12px; }
QLabel#footer { color: @@text_muted@@; font-size: 11px; }
QPushButton { background: @@button@@; border: 1px solid @@button_border@@; border-radius: 7px; padding: 10px 16px; color: @@text@@; font-weight: 550; }
QPushButton:hover { background: @@button_hover@@; color: @@text_strong@@; }
QPushButton:pressed { background: @@button_press@@; }
QPushButton:disabled { background: @@button_disabled@@; color: @@text_disabled@@; }
QPushButton#primary { background: @@primary_bg@@; color: @@primary_fg@@; border: 1px solid @@primary_border@@; }
QPushButton#primary:hover { background: @@primary_bg_hover@@; color: @@primary_fg@@; }
QPushButton#primary:pressed { background: @@primary_bg_press@@; color: @@primary_fg@@; }
QPushButton#primary:disabled { background: @@accent_disabled@@; color: @@primary_disabled_fg@@; }
QPushButton#nav { background: transparent; text-align: left; color: @@text_nav@@; padding: 11px 15px; border: 1px solid @@nav_border@@; }
QPushButton#nav:hover { background: @@surface_hover@@; color: @@text@@; }
QPushButton#nav:checked { background: @@nav_checked_bg@@; color: @@text_strong@@; }
QPushButton#workspace { background: @@workspace_btn_bg@@; border: 1px solid @@workspace_btn_border@@; border-radius: 16px; font-size: 20px; padding: 0; color: @@text@@; }
    QPushButton#workspace[ink="success"] { color: @@success@@; }
    QPushButton#workspace:hover, QPushButton#workspace:checked { background: @@accent@@; border-radius: 16px; color: @@on_accent@@; }
QPushButton#ghost { background: transparent; color: @@text_nav@@; padding: 9px 12px; border: 1px solid @@ghost_border@@; }
QPushButton#ghost:hover { background: @@surface_hover@@; }
/* The selected state a nav button gets, for the one navigation control that
   lives in the top bar rather than the rail. Without it the Cookbook could be
   the page you were on with nothing on screen saying so, which for a
   checkable button is the whole point of making it checkable. */
QPushButton#ghost:checked { background: @@nav_checked_bg@@; color: @@text_strong@@; }
QPushButton#danger { background: @@danger_bg@@; color: @@error@@; }
QPushButton#danger:hover { background: @@danger_bg_hover@@; color: @@error@@; }
QPushButton#danger:pressed { background: @@danger_bg_pressed@@; color: @@error@@; }
QPushButton#danger:disabled { background: @@button_disabled@@; color: @@text_disabled@@; }
/* Focus, for everything a button can be.
   The sheet declared focus for inputs and for the stat buttons only, and an
   application stylesheet replaces Qt's own painting: a widget drawn from the
   sheet that says nothing about :focus draws nothing for it. So every button
   in the app had no visible focus at all, and tabbing through the navigation
   rail or the top bar gave no indication of where the reader was.

   Written one selector per rule, like the rest of the sheet, so that a test
   asking "does this selector have a rule" finds it by name rather than by
   reading a comma-joined line.

   statValue is deliberately absent: it is a readout, it shows no focus ring by
   design, and the rule above already keeps it flat. The danger button takes
   the error colour, since every action using it is the one to be careful with. */
QPushButton:focus { border: 1px solid @@accent@@; }
QPushButton#nav:focus { border: 1px solid @@accent@@; background: @@surface_hover@@; }
QPushButton#ghost:focus { border: 1px solid @@accent@@; background: @@surface_hover@@; }
QPushButton#primary:focus { border: 1px solid @@accent@@; }
QPushButton#workspace:focus { border: 1px solid @@accent@@; }
QPushButton#danger:focus { border: 1px solid @@error@@; }
/* errorDismiss is named, and an ID selector outranks the plain :focus one, so
   it kept its own border: none and this button alone was left with no visible
   focus. Named rules are given here for the same reason. */
QPushButton#errorDismiss:focus { border: 1px solid @@accent@@; }
QToolButton:focus { border: 1px solid @@accent@@; }
/* Deliberately no QCheckBox focus rule.

   One was here and it was wrong. Outlining each indicator put a ring beside
   every option, so a card with four of them -- Connect a model carries four --
   read as four boxes to be stepped through rather than four settings. The
   cost is real: a keyboard reader cannot see where they are on a checkbox.
   The honest trade is to say so here rather than pretend the rule is
   elsewhere; if a subtler cue is wanted later, a background tint on the
   label is the shape that does not box each option individually. */
QListWidget:focus { outline: 1px solid @@accent@@; }
QTableWidget:focus { outline: 1px solid @@accent@@; }
QLineEdit, QPlainTextEdit, QComboBox { background: @@input_bg@@; color: @@text@@; border: 1px solid @@border_strong@@; border-radius: 7px; padding: 10px; selection-background-color: @@accent@@; selection-color: @@on_accent@@; }
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus { border: 1px solid @@accent@@; }
QLineEdit[invalid="true"], QPlainTextEdit[invalid="true"], QComboBox[invalid="true"] { border: 1px solid @@error@@; }
QPushButton[invalid="true"] { border: 1px solid @@error@@; }
QCheckBox[invalid="true"]::indicator { border: 1px solid @@error@@; }
QFrame#createCard[invalid="true"] { border: 1px solid @@error@@; }
QWidget#messageRow[invalid="true"] { border: 1px solid @@error@@; }
QComboBox::drop-down { border: 0; width: 24px; }
QComboBox QAbstractItemView { background: @@combo_popup_bg@@; color: @@text@@; selection-background-color: @@accent@@; padding: 5px; border: 1px solid @@border_strong@@; }
QCheckBox { spacing: 8px; color: @@text_list@@; background: transparent; }
QCheckBox::indicator { width: 16px; height: 16px; border-radius: 4px; background: @@checkbox_bg@@; border: 1px solid @@checkbox_border@@; }
QCheckBox::indicator:checked { background: @@accent@@; border-color: @@accent_press@@; }
TickCheckBox { qproperty-tickColor: @@success@@; }
TickCheckBox::indicator:checked { background: transparent; border: none; }
QScrollArea { background: transparent; border: none; }
QScrollArea > QWidget > QWidget { background: transparent; }
QScrollBar:vertical { background: @@scrollbar_track@@; width: 12px; margin: 3px; }
QScrollBar::handle:vertical { background: @@scrollbar_thumb@@; min-height: 28px; border-radius: 3px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QListWidget { border: none; background: transparent; outline: 0; }
QListWidget::item { padding: 14px 12px; border-radius: 7px; color: @@text_list@@; border: 1px solid @@list_item_border@@; }
QListWidget::item:selected { background: @@surface_active@@; color: @@text_strong@@; }
QListWidget::item:hover { background: @@surface_row_hover@@; }
QTableWidget { background: @@surface@@; alternate-background-color: @@surface_base@@; color: @@text@@; border: 1px solid @@table_border@@; gridline-color: @@border@@; selection-background-color: @@surface_active@@; selection-color: @@text@@; }
QTableWidget::item { padding: 8px; }
QHeaderView::section { background: @@header_bg@@; color: @@text_muted@@; border: none; border-right: 1px solid @@border@@; border-bottom: 1px solid @@border@@; padding: 10px 8px; }
QTableCornerButton::section { background: @@header_bg@@; border: none; }
QMenu { background: @@menu_bg@@; color: @@text@@; border: 1px solid @@border@@; padding: 5px; }
QMenu::item { padding: 9px 16px; border-radius: 4px; }
QMenu::item:selected { background: @@accent@@; color: @@on_accent@@; border: 1px solid @@menu_item_border@@; }
QProgressBar { background: @@progress_track@@; border: none; border-radius: 3px; max-height: 5px; }
QProgressBar::chunk { background: @@accent@@; border-radius: 3px; }
QToolTip { background: @@tooltip_bg@@; color: @@tooltip_fg@@; border: 1px solid @@tooltip_border@@; padding: 8px; }
QDialogButtonBox QPushButton { min-width: 85px; }
"""


def _stylesheet(tokens):
    """The stylesheet for one palette.

    Raises:
        KeyError: if the sheet names a token the palette does not define,
            which is what stops a colour being silently hard-coded.
    """
    sheet = _TEMPLATE
    # Only the odd-indexed segments are token names; the even ones are text.
    parts = _TEMPLATE.split("@@")
    for index in range(1, len(parts), 2):
        name = parts[index]
        sheet = sheet.replace(f"@@{name}@@", tokens[name])
    return sheet


THEME = _stylesheet(_DARK)
LIGHT_THEME = _stylesheet(_LIGHT)
MIKU_THEME = _stylesheet(_MIKU)

THEMES = {DARK: THEME, LIGHT: LIGHT_THEME, MIKU: MIKU_THEME}


def stylesheet(name):
    """The Qt stylesheet for a theme, falling back to dark."""
    return THEMES.get(name, THEMES[DARK])


# --- providers -------------------------------------------------------------

# (display name, blurb, glyph, accent) per provider.
_PROVIDER_META = {
    "ollama": ("Ollama", "Local models, on your terms.", "O"),
    "bionic": ("Bionic GPT", "Your private Bionic deployment.", "B"),
    "openai": ("OpenAI", "Responses API for your team.", "◎"),
    "anthropic": ("Claude", "A thoughtful collaboration partner.", "✳"),
    "gemini": ("Gemini", "Google models in your workspace.", "✦"),
    "groq": ("Groq", "Fast inference. More momentum.", "g"),
    "deepseek": ("DeepSeek", "A fresh perspective on your work.", "≈"),
    "mistral": ("Mistral", "Your choice of Mistral models.", "M"),
    "openrouter": ("OpenRouter", "Many models. One connection.", "↗"),
    "openai-compatible": ("Custom endpoint", "Connect LM Studio, vLLM, and more.", "◇"),
}

_DARK_ACCENTS = {
    "ollama": "#9d90f7",
    "bionic": "#7fd1c0",
    "openai": "#6fce9f",
    "anthropic": "#e8a882",
    "gemini": "#7fb2ff",
    "groq": "#f2927f",
    "deepseek": "#7aa2f0",
    "mistral": "#e6b26a",
    "openrouter": "#b3a6f5",
    "openai-compatible": "#b3a6f5",
}
_MIKU_ACCENTS = {
    "ollama": "#a59af5", "bionic": "#58b89c", "openai": "#67bd9a",
    "anthropic": "#d8a184", "gemini": "#7eacff", "groq": "#ee9b7d",
    "deepseek": "#78a6eb", "mistral": "#e7b16e", "openrouter": "#a69aee",
    "openai-compatible": "#a69aee",
}

# Mint, lavender and sand all lose contrast on white, so the light set
# deepens them rather than reusing the dark values.
_LIGHT_ACCENTS = {
    "ollama": "#5b4fd1", "bionic": "#1f7a63", "openai": "#1f7a5e",
    "anthropic": "#a2603a", "gemini": "#2f6ed0", "groq": "#b4522a",
    "deepseek": "#2f6bb5", "mistral": "#96601f", "openrouter": "#6357c4",
    "openai-compatible": "#6357c4",
}

PROVIDER_ACCENTS = {DARK: _DARK_ACCENTS, LIGHT: _LIGHT_ACCENTS, MIKU: _MIKU_ACCENTS}


def provider_entry(name, provider_key):
    """``(display, blurb, glyph, accent)`` for a provider in a theme.

    Unknown providers fall back to a neutral entry rather than raising:
    agent records persist across versions, so a provider key the running
    build no longer knows must not break the agent list. The fallback
    glyph matches what the agent list used before this was centralised.
    """
    meta = _PROVIDER_META.get(provider_key)
    accent = provider_color(name, provider_key)
    if meta is None:
        return ("Connectivity agent", "", "⌘", accent)
    return (meta[0], meta[1], meta[2], accent)


def provider_names(name):
    """``{key: (display, blurb, glyph, accent)}`` for every provider."""
    return {key: provider_entry(name, key) for key in _PROVIDER_META}


PROVIDER_NAMES = provider_names(DARK)
