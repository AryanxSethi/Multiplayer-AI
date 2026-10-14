import math

from PySide6.QtCore import (Property, QAbstractAnimation, QEasingCurve, QPointF, QRectF,
                            QPropertyAnimation, Qt, Signal)
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton,
                             QPlainTextEdit, QSizePolicy, QStyle, QStyleOptionButton,
                             QVBoxLayout, QWidget)

from .icons import provider_pixmap
from .theme import DARK, color, mix


def app_mark(theme_name=DARK, initial="M", size=64):
    """The rounded accent tile the app is recognised by.

    Painted rather than themed, so the accent is read from the palette
    instead of hardcoded, and drawn at whatever size is asked for so the
    window icon, the sidebar avatar and the launch screen stay the same
    mark rather than becoming hand-built approximations of each other.
    """
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(color(theme_name, "accent")))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(QRectF(0, 0, size, size), size * 20 / 64, size * 20 / 64)
    painter.setPen(QColor(color(theme_name, "on_accent")))
    painter.setFont(QFont("Segoe UI", max(1, round(size * 27 / 64)), QFont.Weight.Bold))
    painter.drawText(QRectF(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, initial)
    painter.end()
    return pixmap


def label(text="", name=None, wrap=False):
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    if name:
        widget.setObjectName(name)
    widget.setWordWrap(wrap)
    return widget


def action(text, callback=None, primary=False, name=None):
    widget = QPushButton(text)
    widget.setObjectName(name or ("primary" if primary else ""))
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    if callback:
        widget.clicked.connect(callback)
    return widget


class TickCheckBox(QCheckBox):
    """Keep native checkbox interaction and draw a visible tick when selected."""

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self._tick_color = self.palette().highlightedText().color()

    def get_tick_color(self):
        return self._tick_color

    def set_tick_color(self, ink):
        self._tick_color = QColor(ink)
        self.update()

    tickColor = Property(QColor, get_tick_color, set_tick_color)

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.isChecked():
            return
        option = QStyleOptionButton()
        self.initStyleOption(option)
        rect = self.style().subElementRect(QStyle.SubElement.SE_CheckBoxIndicator, option, self)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setOpacity(1 if self.isEnabled() else .5)
        painter.setPen(QPen(self._tick_color, 2.2, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        path = QPainterPath(QPointF(rect.x() + rect.width() * .16, rect.y() + rect.height() * .52))
        path.lineTo(rect.x() + rect.width() * .4, rect.y() + rect.height() * .76)
        path.lineTo(rect.x() + rect.width() * .86, rect.y() + rect.height() * .24)
        painter.drawPath(path)
        painter.end()


class ErrorLine(QWidget):
    """One card's own message, with a way to dismiss it.

    Every message on a card belongs to that card and is shown here rather
    than in a toast that has gone by the time it is read, or in one line
    shared between cards, where a failure in the join form would appear in
    the create form and be erased by the wrong keystroke.

    The message lives in the window's state, not in this widget, because
    cards are rebuilt on every poll and a message held by a widget would be
    destroyed by the next render. This is only the view of it.

    Hidden outright when empty, so a card with nothing to say does not carry
    a blank band.
    """

    def __init__(self, on_dismiss, tone="error", theme=DARK):
        super().__init__()
        self._theme = theme
        self._tone = tone
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self.message = label("", wrap=True)
        self.message.setObjectName("errorMessage")
        self.message.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Minimum)
        row.addWidget(self.message, 1)
        self.dismiss = action("×", lambda: on_dismiss(self), name="ghost")
        self.dismiss.setObjectName("errorDismiss")
        # Was 20x20. That is the only way to clear a card error, it sits in a
        # row spaced 6px, and it is small enough to be missed by a pointer and
        # hard to reach with one. 28 is the smallest square that is still
        # comfortable to hit without it looking like a button that matters
        # more than the message it dismisses.
        self.dismiss.setFixedSize(28, 28)
        self.dismiss.setCursor(Qt.CursorShape.PointingHandCursor)
        self.dismiss.setToolTip("Dismiss")
        self.dismiss.setAccessibleName("Dismiss message")
        row.addWidget(self.dismiss, 0, Qt.AlignmentFlag.AlignTop)
        self.hide()

    def set_tone(self, tone):
        """Restyle the message for its kind, and repaint in the live theme.

        The colour is applied here rather than through the application
        stylesheet because a page message has to be right even where that
        stylesheet does not reach, and because the tone changes on the same
        line as the text rather than at construction.
        """
        self._tone = tone
        token = {"progress": "text_muted"}.get(tone, "error")
        self.message.setStyleSheet("color:" + color(self._theme, token))
        self.dismiss.setStyleSheet("color:" + color(self._theme, token))
        self.update()

    def show_message(self, message):
        self.message.setText(message)
        self.setVisible(bool(message))

    def set_theme(self, theme):
        self._theme = theme
        self.set_tone(self._tone)

    def clear(self):
        self.message.setText("")
        self.hide()


def _lighten(colour):
    """A fill slightly lighter than ``colour``, for the bubble's hairline.

    Derived rather than tokenised so it follows the fill, which for an agent
    bubble is already derived from that provider's accent. A border the same
    value as the fill would be invisible, which is what made the old slab look
    unfinished.
    """
    return mix("#ffffff", colour, 0.10)


class MessageBubble(QFrame):
    """One message's fill: the title, the body, and the background behind them.

    A child of HoverRow rather than a replacement for it. The row spans the
    panel and keeps the hover action slot, so revealing the actions cannot
    change the row's width; the bubble inside it hugs its own text instead.

    Horizontal size policy is Maximum, which is what makes it hug: it takes
    its size hint up to a ceiling rather than filling whatever it is given.
    Without that a bubble either runs the full width of the panel as a slab,
    or the layout has to be told the width by hand.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("bubble")
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Minimum)
        self.column = QVBoxLayout(self)
        self.column.setContentsMargins(14, 10, 14, 10)
        self.column.setSpacing(3)
        self.set_background("#2b2d31")

    def set_background(self, colour):
        """Fill and outline the bubble.

        Set per widget rather than through the application sheet, because the
        agent fill is derived from that provider's accent and so is not one of
        the three themes' tokens.
        """
        self.setStyleSheet(
            "QFrame#bubble { background: " + colour + "; border: 1px solid "
            + _lighten(colour) + "; border-radius: 14px; }")

    def add_content(self, widget):
        self.column.addWidget(widget)
        return widget

    def preferred_width(self):
        """How wide this bubble would like to be, before the panel's ceiling.

        The widest child, not the layout's own hint, because a Markdown reply
        reports the width it is currently laid out at rather than the width of
        its text. Without this a long reply is measured against the width of
        the first short one and never grows to fill the space it should have.

        A child that offers ``natural_width`` is measured that way; anything
        else is measured by its ``sizeHint``, and one whose measurement raises
        falls back to its hint too. Margins are added once at the end rather
        than once per child, so a bubble holding a name, a subtitle and a copy
        button is padded once rather than three times.
        """
        margins = self.column.contentsMargins()
        width = 0
        for index in range(self.column.count()):
            child = self.column.itemAt(index).widget()
            if child is None:
                continue
            measure = getattr(child, "natural_width", None)
            try:
                natural = int(measure()) if callable(measure) else child.sizeHint().width()
            except (RuntimeError, ValueError):
                natural = child.sizeHint().width()
            width = max(width, natural)
        return width + margins.left() + margins.right()


class Select(QComboBox):
    """A dropdown that only changes when it is actually used.

    Qt's combo box advances on any wheel notch over it, so scrolling the
    Settings page silently rewrote the stored theme, and kept rewriting it
    for as long as the scroll continued. A wheel is not a deliberate pick
    out of a closed list; the popup is opened with a click or the keyboard
    and that is the only way to move.

    Subclassed rather than filtered, so there is no event filter to keep
    installed and remove again, and nothing to leak if the widget outlives
    whoever installed it.
    """

    def wheelEvent(self, event):
        event.ignore()


class Composer(QPlainTextEdit):
    submitted = Signal()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.submitted.emit()
            event.accept()
        else:
            super().keyPressEvent(event)


class OrbitArt(QWidget):
    """Quiet orbital motion in the welcome panel; no synthetic network metrics.

    Painted directly rather than styled, so it cannot be reached by a Qt
    Style Sheet. Its colours come from the theme palette and it repaints
    itself when the theme changes.

    The chips used to move 0.03 of a pixel per frame and had their
    coordinates rounded to whole pixels, so a movement smaller than a
    pixel could only appear as still, still, jump: roughly one visible
    one-pixel step every half second. It read as an image that had stopped
    loading rather than as slow motion. The coordinates are now floats,
    which the antialiasing render hint resolves to a soft edge, and the
    travel and cycle are set so the movement is perceptible without being
    hurried.
    """
    # (label, provider) for the chips orbiting the centre mark. The mark is the
    # provider's own bundled artwork rather than a glyph, so the same brand is
    # seen here, on the agent cards and in the connect form. The glyphs this
    # replaced were typed into the font that happened to resolve, so the mark
    # looked different per machine and none of them matched the real logo.
    _CHIPS = (("Ollama", "ollama"), ("Claude", "anthropic"),
              ("Gemini", "gemini"), ("OpenAI", "openai"))
    # The ellipse the chips travel, the sway either side of their resting
    # place in radians, and how long one full breath takes. At this sway a
    # chip drifts about 40px across eight seconds. The sway is also what
    # keeps the four chips from tracing near-identical paths: a chip that
    # barely moves cannot be told from its neighbours however its phase is
    # arranged.
    _CHIP_RX = 111.0
    _CHIP_RY = 98.0
    _SWAY = 0.26
    _CYCLE_MS = 16000
    # The chip's own painted size, from the rounded rect in paintEvent. The
    # box this widget needs is these radii plus half a chip, so the two are
    # stated here rather than being retyped into a minimum size.
    _CHIP_W = 74.0
    _CHIP_H = 66.0
    # The side the brand mark is drawn at inside the chip. Square, because
    # every bundled mark is a square logo, and drawn in the upper part of the
    # chip with the name beneath it, so the chip's own box and therefore
    # ``required_size`` are unchanged by this. Small enough that the 9px name
    # below it keeps its clearance and the four marks do not compete with the
    # centre tile for attention.
    _CHIP_MARK = 26.0

    @classmethod
    def required_size(cls):
        """The smallest box that contains every chip at every phase.

        Worked out from the geometry rather than guessed. It was a flat
        ``setMinimumSize(260, 240)``, and the sway that was added later to
        make the chips perceptible pushed the outermost one about three
        pixels past that number, so the left and right edges were clipped
        while the top and bottom, which had room to spare, looked fine.

        The sway is why the extremes have to be searched for rather than
        taken from the nominal radius: no chip sits at angle zero, so the
        radius is never the true horizontal reach.
        """
        half_width = half_height = 0.0
        for step in range(361):
            phase = math.tau * step / 360
            for index in range(len(cls._CHIPS)):
                angle = (index * math.pi / 2 - math.pi / 4
                         + math.sin(phase) * cls._SWAY)
                half_width = max(half_width,
                                 abs(math.cos(angle)) * cls._CHIP_RX + cls._CHIP_W / 2)
                half_height = max(half_height,
                                  abs(math.sin(angle)) * cls._CHIP_RY + cls._CHIP_H / 2)
        return (int(math.ceil(half_width * 2)), int(math.ceil(half_height * 2)))

    def __init__(self, parent=None, theme=DARK, moving=True):
        super().__init__(parent)
        self.setMinimumSize(*self.required_size())
        self._phase = 0.0
        self._theme = theme
        # Loaded once per theme and held, because this paints on every frame
        # of the animation and ``provider_pixmap`` reads a 640x640 PNG from
        # disk each time it is called. Four chips at sixty frames a second is
        # the difference between a quiet orbit and a busy disk.
        self._marks = {}
        self.animation = QPropertyAnimation(self, b"phase", self)
        self.animation.setStartValue(0.0)
        self.animation.setEndValue(math.tau)
        self.animation.setDuration(self._CYCLE_MS)
        self.animation.setLoopCount(-1)
        if moving:
            self.animation.start()

    def chip_angle(self, index, phase):
        """Where chip ``index`` sits, in radians, at ``phase``.

        Every chip shares one phase. That looks like it should make them
        move as one, and it was worth measuring: with the old 0.07 sway it
        did, the four paths landing within 7% of each other, because at
        that amplitude all four were nearly stationary rather than because
        of the phase. Raising the sway is what separated them, and it
        leaves the chips as far apart at 0.26 as a phase offset between
        neighbours did. So the offset is not here: one less term, and
        nothing measurable given up for it.
        """
        return index * math.pi / 2 - math.pi / 4 + math.sin(phase) * self._SWAY

    def chip_position(self, index, phase):
        """The centre of chip ``index`` at ``phase``, as floats.

        Left unrounded on purpose. Snapping these to whole pixels is what
        made the motion step, and it is kept in one place so a test can
        measure the travel that is actually painted rather than a copy of
        the formula.
        """
        angle = self.chip_angle(index, phase)
        return (self.width() / 2 + math.cos(angle) * self._CHIP_RX,
                self.height() / 2 + math.sin(angle) * self._CHIP_RY)

    def chip_mark(self, provider):
        """The provider's brand mark for this theme, loaded once and kept.

        Cached against the theme name rather than for the lifetime of the
        widget, because each theme ships its own light or dark mark and a
        widget that outlived a theme change would otherwise keep drawing the
        old one's, which on the light theme is a mark that is too light to see.
        A provider with no bundled mark still returns a connection symbol,
        which is what the agent cards show for a custom endpoint.
        """
        cached = self._marks.get(provider)
        if cached is None:
            cached = self._marks[provider] = provider_pixmap(
                provider, self._theme, round(self._CHIP_MARK))
        return cached

    def set_theme(self, name):
        """Adopt another theme and repaint with its colours."""
        if name != self._theme:
            self._marks.clear()
        self._theme = name
        self.update()

    def set_moving(self, moving):
        """Hold the orbit still, or let it turn again.

        Paused rather than stopped, which would snap the chips back to
        their start value. A held illustration is still a whole one, since
        the chips are placed by index rather than by where they ended up.
        """
        if moving:
            if self.animation.state() != QAbstractAnimation.State.Running:
                self.animation.start()
        else:
            self.animation.pause()

    @property
    def moving(self):
        return self.animation.state() == QAbstractAnimation.State.Running

    def get_phase(self):
        return self._phase

    def set_phase(self, value):
        self._phase = value
        self.update()

    phase = Property(float, get_phase, set_phase)

    def paintEvent(self, event):
        accent = lambda token: QColor(color(self._theme, token))  # noqa: E731
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx, cy = self.width() / 2, self.height() / 2
        painter.setPen(QPen(accent("orbit_ring"), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(QRectF(cx - 72, cy - 72, 144, 144), 72, 72)
        painter.setPen(QPen(accent("orbit_ring_dashed"), 1, Qt.PenStyle.DashLine))
        painter.drawRoundedRect(QRectF(cx - 112, cy - 112, 224, 224), 112, 112)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(accent("orbit_tile"))
        painter.drawRoundedRect(QRectF(cx - 41, cy - 41, 82, 82), 23, 23)
        painter.setBrush(accent("accent"))
        painter.drawRoundedRect(QRectF(cx - 31, cy - 31, 62, 62), 18, 18)
        painter.setFont(QFont("Segoe UI", 25, QFont.Weight.Bold))
        painter.setPen(accent("on_accent"))
        painter.drawText(QRectF(cx - 31, cy - 31, 62, 62),
                         Qt.AlignmentFlag.AlignCenter, "M")
        for index, (name, provider) in enumerate(self._CHIPS):
            x, y = self.chip_position(index, self._phase)
            painter.setPen(QPen(accent("orbit_chip_ring"), 1))
            painter.setBrush(accent("orbit_chip_bg"))
            painter.drawRoundedRect(QRectF(x - 37, y - 33, 74, 66), 12, 12)
            # The mark is centred in the chip's upper half rather than in the
            # chip, so the name below it stays on the chip's own centre line
            # instead of riding up under the logo.
            side = self._CHIP_MARK
            mark = self.chip_mark(provider)
            # Four arguments, with the source rect as well as the target,
            # because PySide6's two-argument overload only takes a QRect and
            # rounding to whole pixels here would put the mark back on the grid
            # that every other part of this paint deliberately leaves it off.
            # The mark arrives with a device pixel ratio of 2, which the
            # explicit source rect makes irrelevant.
            painter.drawPixmap(QRectF(x - side / 2, y - 31 + (36 - side) / 2,
                                      side, side), mark,
                               QRectF(0, 0, mark.width(), mark.height()))
            painter.setPen(accent("orbit_chip_text"))
            painter.setFont(QFont("Segoe UI", 9))
            painter.drawText(QRectF(x - 35, y + 6, 70, 20),
                             Qt.AlignmentFlag.AlignCenter, name)


class HoverRow(QWidget):
    """One message: its speaker's mark, its bubble, and the actions beneath it.

    The actions are always there and sit under the bubble, the way a browser
    assistant keeps a copy button under every reply. They used to occupy a
    slot beside the mark and appear only on hover, which was wrong twice
    over: it read as a pop-up, and it moved the message, because a hidden
    widget hands its space back to the layout, so revealing the actions took
    about 90px off the bubble and re-wrapped the text under the pointer.

    ``mine`` is the reader's own message. It puts the bubble and the mark on
    the right of the panel and the stretch on the left, rather than the other
    way round, so both sides of the conversation are built from one row.

    The row is a widget rather than a layout because ``:hover`` in a
    stylesheet matches widgets, and a bare layout has nothing to match on.
    """

    def __init__(self, parent=None, mine=False):
        super().__init__(parent)
        self.mine = mine
        self.setObjectName("messageRow")
        self.outer = QHBoxLayout(self)
        self.outer.setContentsMargins(0, 0, 0, 0)
        self.outer.setSpacing(0)
        self.content = QHBoxLayout()
        self.content.setSpacing(12)
        self.outer.addLayout(self.content, 1)
        # The bubble and the strip under it are stacked, so the strip can sit
        # beneath the bubble and still line up with the bubble's own edge
        # instead of floating off against the panel.
        self.message = QVBoxLayout()
        self.message.setContentsMargins(0, 0, 0, 0)
        self.message.setSpacing(4)
        self.actions = QWidget()
        self.actions.setObjectName("messageActions")
        self._actions_layout = QHBoxLayout(self.actions)
        self._actions_layout.setContentsMargins(0, 0, 0, 0)
        self._actions_layout.setSpacing(4)
        self._actions_shown = False
        if mine:
            self.content.addStretch(1)
        # Stretch 1, and the bubble aligned inside it rather than filling it.
        # Left to size itself the stack sometimes matched the panel exactly
        # and Qt centred the row, so a grouped reply and the header above it
        # could start at different x; which rows centred depended on whether
        # they happened to carry an action, because that changed the stack's
        # hint. Filling the row and hugging inside it is the same every time.
        self.content.addLayout(self.message, 1)

    def add_mark(self, widget):
        """Put the speaker's mark on that speaker's side of the row."""
        if self.mine:
            self.content.addWidget(widget, 0, Qt.AlignmentFlag.AlignTop)
        else:
            self.content.insertWidget(0, widget, 0, Qt.AlignmentFlag.AlignTop)
        return widget

    def set_message(self, widget):
        self.message.addWidget(widget, 0, Qt.AlignmentFlag.AlignTop
                               | (Qt.AlignmentFlag.AlignRight if self.mine
                                  else Qt.AlignmentFlag.AlignLeft))
        return widget

    def add_action(self, widget):
        """Add an action under the bubble.

        The strip is only parented into the row once there is something to put
        in it, so a row with no actions carries no empty gap underneath.
        """
        if not self._actions_shown:
            self._actions_shown = True
            self.message.addWidget(self.actions, 0, Qt.AlignmentFlag.AlignLeft)
        self._actions_layout.addWidget(widget)
        return widget

    @property
    def has_actions(self):
        return self._actions_shown


class WorkspaceButton(QPushButton):
    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setFixedSize(48, 48)
        self.setObjectName("workspace")
        self._radius = 23.0
        self.animation = QPropertyAnimation(self, b"cornerRadius", self)
        self.animation.setDuration(160)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)

    def set_ink(self, token):
        """Colour this button from a palette token, by property.

        It used to take a widget stylesheet, which the hover animation
        then overwrote: set_radius writes its own sheet to change the
        corner, and that left the button stripped of everything the sheet
        had been carrying for the rest of the hover. A property is read by
        the application stylesheet instead, so the two cannot collide and
        the colour survives a theme change as well.
        """
        self.setProperty("ink", token)
        self.style().unpolish(self)
        self.style().polish(self)

    def radius(self):
        return self._radius

    def set_radius(self, value):
        self._radius = value
        self.setStyleSheet(f"QPushButton {{ border-radius: {int(value)}px; }}")

    cornerRadius = Property(float, radius, set_radius)

    def enterEvent(self, event):
        self.animation.stop()
        self.animation.setStartValue(self._radius)
        self.animation.setEndValue(15.0)
        self.animation.start()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.animation.stop()
        self.animation.setStartValue(self._radius)
        self.animation.setEndValue(23.0)
        self.animation.start()
        super().leaveEvent(event)
