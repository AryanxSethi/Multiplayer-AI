"""The launch screen: the app's first impression, and its first chance to lie.

A splash screen is the one piece of UI whose failure is invisible. If it
never goes away the app looks hung; if it leaves early you glimpse an
unpainted window; and if it stays up over an error it hides that error
behind a smiling logo. So three things are handled rather than hoped for:

* It waits for the runtime to report ready, but never for less than a
  floor. Startup here takes about a quarter of a second, so waiting only
  for readiness would flash a splash for a fifth of a second and read as a
  glitch rather than as an intention.
* A fatal error tears it down at once. Nothing should be able to leave a
  logo on screen over a broken app.
* It is skipped when the stored preferences say so, and when the
  reduce-motion setting is on, since an accessibility switch that let an
  animation play anyway would not be doing its job.

The mark and the orbit are the ones the welcome panel already uses, so the
app introduces itself with what the user is about to see rather than with
a second drawing of it.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (QApplication, QGraphicsOpacityEffect, QLabel,
                               QVBoxLayout, QWidget)

from .theme import DARK, color
from .widgets import OrbitArt, app_mark

# How long the card stays up whatever the runtime is doing. Startup is
# quick here, so this is mostly a deliberate hold rather than a wait, and
# the status line says so rather than pretending to be busy.
LAUNCH_FLOOR_MS = 2800
# If a signal never arrives the card still leaves, so a runtime that goes
# quiet cannot hold a splash over an app that is perfectly usable.
LAUNCH_CAP_MS = 1500
FADE_MS = 260

# Shown while the card is deliberately held, once there is nothing left to
# report. Short and plain, because a line of text that has stalled for two
# seconds starts to read as a hang.
HOLD_TEXT = "Getting things ready"

# The card, in one place so the lines can be told how much room they have.
CARD_WIDTH = 400
CARD_HEIGHT = 470
CARD_GUTTER = 24
CARD_TEXT_WIDTH = CARD_WIDTH - 2 * CARD_GUTTER


class StartupStatus:
    """One friendly line describing what the app is doing right now.

    A checklist was tried and read as a technical panel, which is the wrong
    voice for the first thing anybody sees. So each signal maps to a single
    plain line, and the longest the user waits on is one sentence.

    The lines are real. Everything here comes from a signal the runtime
    emits while starting, and a warning tone is used where something is
    genuinely wrong rather than merely still happening.
    """

    def __init__(self):
        self._line = "Starting your network"
        self._tone = "normal"
        self._settled = False
        self.facts: list[str] = []
        self.warnings: list[str] = []

    @property
    def line(self):
        return self._line

    @property
    def tone(self):
        return self._tone

    @property
    def settled(self):
        """Whether everything worth reporting has been reported."""
        return self._settled

    def begin_hold(self, line):
        """The last thing worth hearing has arrived; only the hold remains.

        ``line`` is what to say if something went wrong. With nothing to
        report the text becomes the holding line instead: a line sitting on
        an already-finished fact for two seconds reads as a hang rather
        than as an intention.

        The first thing that went wrong keeps the line. A cleanup still owed
        arriving before the agent list must not be replaced by how many
        agents turned up, which is the more important of the two to say.
        """
        self._settled = True
        if not self.warnings:
            self._line = HOLD_TEXT
            self._tone = "normal"
        elif self._tone != "warn":
            self._line = line
            self._tone = "warn"

    def observe(self, event, data):
        """Follow one runtime signal. Returns True if the display changed.

        The startup facts are recorded in ``detail`` but not shown. Each one
        arrived within about 200ms of the last, so putting them on screen
        meant three lines flickered past in the time it takes to read one,
        which looks like a fault rather than like progress. One line, held,
        then the holding text, is what reads as deliberate.
        """
        raw = data
        data = data if isinstance(data, dict) else {}
        before = (self._line, self._tone, self._settled)

        if event == "ready":
            self.facts.append(f"Network ready on port {data.get('port', '?')}")
        elif event == "workspace":
            name = data.get("name")
            self.facts.append(f"Opened {name}" if name else "Opened the workspace")
        elif event == "agents":
            # The agent list is the last thing the runtime reports, so
            # arriving here is what settles the startup. Without that the
            # card waited out its hard cap every single time.
            agents = data.get("agents") or []
            self.facts.append(f"{len(agents)} agent(s), " + ("connected" if data.get("connected") else "not connected"))
            if not data.get("connected"):
                self.warnings.append("the relay is not connected")
                self.begin_hold("Working without the relay")
            else:
                self.begin_hold(f"{len(agents)} agent{'s' if len(agents) != 1 else ''} available")
        elif event == "pending_cleanup":
            # Reported on every start, including when there is nothing owed.
            # Only the case with credentials behind it is worth saying.
            owed = data.get("credentials", 0) or 0
            self.facts.append(f"{owed} credential(s) owed")
            if owed:
                self.warnings.append(f"{owed} credential(s) still owed")
                self.begin_hold("Finishing an earlier cleanup")
        elif event == "fatal":
            # The payload here is a plain string, not one of the dicts the
            # other signals carry, so it is read before the coercion above.
            message = str(raw)[:90] if not isinstance(raw, dict) else str(raw.get("error", ""))[:90]
            self.warnings.append(message)
            self.facts.append("Fatal")
            self.begin_hold("Could not start")

        return (self._line, self._tone, self._settled) != before


class LaunchScreen(QWidget):
    """A frameless card holding the mark, the name, the orbit and a line."""

    def __init__(self, theme_name=DARK, moving=True, parent=None):
        super().__init__(parent)
        self._theme = theme_name
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint
                           | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAccessibleName("Multiplayer AI is starting")
        self.setFixedSize(CARD_WIDTH, CARD_HEIGHT)
        # The rounded card is painted rather than laid out, because the only
        # way to get rounded corners out of a frameless window is to paint
        # them, and because the fill has to be opaque: left transparent the
        # window underneath showed straight through and a loading screen
        # that shows the loaded window through it reads as an error rather
        # than as progress. See paintEvent.
        self._radius = 20.0

        column = QVBoxLayout(self)
        column.setContentsMargins(24, 30, 24, 26)
        column.setSpacing(8)

        # The app's own mark, letter and all, because that is the mark the
        # taskbar is about to show and the one the window is built from.
        # Not this device's initial: the identity is not known until the
        # runtime answers, and a mark that shows a placeholder and then
        # changes is worse than one that shows the app throughout.
        self.mark = QLabel()
        self.mark.setFixedSize(88, 88)
        self.mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.mark.setPixmap(app_mark(theme_name, "M", 88))
        self.mark.setAccessibleName("Multiplayer AI")
        column.addWidget(self.mark, 0, Qt.AlignmentFlag.AlignHCenter)

        self.caption = Line("Multiplayer AI", theme_name, size=17,
                            weight=QFont.Weight.DemiBold, token="text_strong",
                            max_width=CARD_TEXT_WIDTH)
        column.addWidget(self.caption, 0, Qt.AlignmentFlag.AlignHCenter)

        self.art = OrbitArt(theme=theme_name, moving=moving)
        column.addWidget(self.art, 1)

        # Hand-painted like the caption, so it follows the theme without the
        # splash needing the application stylesheet, which it never has.
        self.status = Line("Starting your network", theme_name, size=12,
                           max_width=CARD_TEXT_WIDTH)
        column.addWidget(self.status, 0, Qt.AlignmentFlag.AlignHCenter)

        self.shown_at = 0.0
        # Set by runtime_ready, which only the runtime's ready event calls.
        self._ready = False
        # The fade currently running, so a second one can stop it. It was
        # written and never read, which is why two of them could drive the
        # opacity at once.
        self._fade = None
        self.dismissed = False
        self._on_done = None
        self._status = None
        self.cancelled = False

    # -- lifecycle ------------------------------------------------------

    def begin(self, on_done=None):
        """Show it, start fading in, and remember who to hand back to.

        Readiness is not waited for here. It may already have happened by
        the time this is constructed, so the caller says so afterwards
        rather than this subscribing to an event that has been and gone.
        """
        self._on_done = on_done
        self._centre()
        self.show()
        self.raise_()
        self.shown_at = time.monotonic()
        self.fade_to(1.0, FADE_MS, QEasingCurve.Type.OutCubic)

    def runtime_ready(self):
        """The runtime has reported that it is up.

        Recorded rather than merely acted on. Normal dismissal now needs this
        as well as a settled status, because a settled status only says the
        last report arrived, and the report that matters is the one saying the
        network is usable. Standing in for it with whatever signal happened to
        be last is how a card hands over to a window whose controls are all
        disabled because the runtime never said it was ready.

        A runtime that was already up before this connected will never fire
        it, and that card waits out its hard cap instead. Measured startup
        here is about a quarter of a second against a window that is still
        being built, so that case is rare, and the cap is what covers it.
        """
        self._ready = True
        self.dismiss_when_floored()

    def set_status(self, status):
        """Adopt a StartupStatus, or None to keep the opening line."""
        self._status = status
        if status is not None:
            self.status.set(status.line, "error" if status.tone == "warn" else "text_muted")

    def dismiss_when_floored(self):
        """Leave once the floor has passed and the checks have settled.

        The cap matters more than it looks. A runtime that reports ready
        and then goes quiet would otherwise hold a splash over an app that
        is entirely usable, which is the one failure this screen cannot be
        allowed to have.
        """
        if self.dismissed:
            return
        waited = time.monotonic() - self.shown_at
        if self._settled() and waited >= LAUNCH_FLOOR_MS / 1000:
            self.dismiss()
            return
        if waited >= (LAUNCH_FLOOR_MS + LAUNCH_CAP_MS) / 1000:
            self.dismiss()
            return
        QTimer.singleShot(120, self.dismiss_when_floored)

    def _settled(self):
        """Whether the runtime is up and there is nothing left worth hearing.

        Both, deliberately. Settled on its own is the status having seen its
        last expected signal; ready on its own is the network being usable but
        the agent list still arriving. Either alone can leave a card covering
        an app that is not finished, which is the one failure this screen is
        not allowed to have.
        """
        if not self._ready:
            return False
        status = getattr(self, "_status", None)
        return status is None or status.settled

    def dismiss(self, on_done=None):
        """Fade out and close, then hand back.

        The opacity effect is taken off once the fade is over. Left
        attached it would put every later paint of this widget through Qt's
        effect machinery for nothing, which is the fault the page fade has
        in the window and is not worth repeating here.
        """
        if on_done is not None:
            self._on_done = on_done
        if self.dismissed or not self.isVisible():
            self.dismissed = True
            self._finish()
            return
        self.art.set_moving(False)
        self.dismissed = True
        self.fade_to(0.0, FADE_MS, QEasingCurve.Type.InCubic,
                     on_finished=self._close)

    def _close(self):
        if self.graphicsEffect() is not None:
            self.setGraphicsEffect(None)
        self.hide()
        self._finish()

    def _finish(self):
        if self._on_done is not None:
            hand_over, self._on_done = self._on_done, None
            hand_over()

    def closeEvent(self, event):
        """Closing the card cancels the launch rather than revealing the app.

        The card has no title bar, so Alt+F4 on it is the only way to
        dismiss it. Showing a window nobody asked to see because they
        dismissed its splash would be worse than quitting.
        """
        self.cancelled = True
        self.dismissed = True
        event.accept()
        QApplication.quit()

    # -- appearance -----------------------------------------------------

    def paintEvent(self, event):
        """Paint the card behind everything else.

        Frameless and translucent, so the corners can be rounded, which
        means nothing else will fill them. The fill is the card colour and
        the edge is the same hairline every other card in the app uses, so
        the splash reads as part of the app and follows the theme: dark by
        default, and light for somebody who has chosen light.
        """
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(color(self._theme, "border")), 1))
        painter.setBrush(QColor(color(self._theme, "surface")))
        painter.drawRoundedRect(QRectF(0.5, 0.5, self.width() - 1,
                                       self.height() - 1),
                                self._radius, self._radius)
        painter.end()

    def set_theme(self, name):
        self._theme = name
        self.mark.setPixmap(app_mark(name, "M", 88))
        self.caption.set_theme(name)
        self.art.set_theme(name)
        self.status.set_theme(name)
        self.update()

    def fade_to(self, opacity, duration, easing, on_finished=None):
        """Fade the whole card to ``opacity``, creating the effect if needed.

        This has never faded anything. It set the effect to the target, then
        animated from the target to the target, which is a no-op that writes
        the destination immediately and then writes it again for the duration.
        So the card appeared fully opaque and vanished at once, leaving the
        whole 260ms of empty desktop between it going and the window arriving.

        Three things have to be right for a fade to be a fade:

        * the current opacity is read before it is overwritten, or the start
          value is the target again;
        * a freshly created effect starts transparent, or there is nothing to
          fade up from;
        * any fade already running is stopped, or two animations drive the same
          property and fight. The fatal path can dismiss the card while the
          fade in is still going, which is exactly when that happens.
        """
        effect = self.graphicsEffect()
        if effect is None:
            effect = QGraphicsOpacityEffect(self)
            # Transparent, so a fade in starts at zero. Qt's own default is
            # neither zero nor reliably documented, and the animation below
            # sets the real value on its first tick regardless.
            effect.setOpacity(0.0)
            self.setGraphicsEffect(effect)
        start = effect.opacity()

        previous = self._fade
        if previous is not None:
            previous.stop()
            previous.deleteLater()

        animation = QPropertyAnimation(effect, b"opacity", self)
        animation.setStartValue(start)
        animation.setEndValue(opacity)
        animation.setDuration(duration)
        animation.setEasingCurve(easing)
        # Connected before the first tick, so a zero-duration or immediate
        # finish cannot be missed.
        if on_finished:
            animation.finished.connect(on_finished)
        animation.start()
        self._fade = animation

    def _centre(self):
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        self.move(area.center().x() - self.width() // 2,
                  area.center().y() - self.height() // 2)


class Line(QWidget):
    """A centred line of text, painted by hand and sized from its own font.

    Painted rather than a QLabel because the splash is its own window and
    never has the application stylesheet, so anything coloured has to be
    painted or styled here.

    Sized explicitly because this is the whole bug. A bare QWidget has no
    valid size hint, so a layout centring one gives it zero width, and a
    zero-width widget never paints. Both lines on the card were invisible
    for that reason, which is why they are one class now rather than two
    copies of the same mistake.
    """

    def __init__(self, text, theme_name, size=11, weight=None, token="text_muted",
                 max_width=None):
        super().__init__()
        self._theme = theme_name
        self._text = text
        self._token = token
        # A message longer than the card has to be elided rather than allowed
        # to push the layout around or run off both edges.
        self._max_width = max_width
        self.setFont(QFont("Segoe UI", size,
                           weight if weight is not None else QFont.Weight.Normal))
        self.setFixedHeight(round(size * 1.9))
        self.setAccessibleName(text)

    def set(self, text, token=None):
        self._text = text
        if token is not None:
            self._token = token
        self.setAccessibleName(text)
        # The hint is derived from the text, so a layout has to be asked
        # again or the new text is drawn into the old width.
        self.updateGeometry()
        self.update()

    def set_theme(self, name):
        self._theme = name
        self.update()

    def sizeHint(self):
        metrics = QFontMetrics(self.font())
        width = max(metrics.horizontalAdvance(self._text), 1) + 8
        if self._max_width is not None:
            width = min(width, self._max_width)
        return QSize(width, self.height())

    def minimumSizeHint(self):
        return self.sizeHint()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setFont(self.font())
        painter.setPen(QPen(QColor(color(self._theme, self._token)), 1))
        flags = (int(Qt.AlignmentFlag.AlignCenter.value)
                 | int(Qt.TextElideMode.ElideRight.value))
        painter.drawText(self.rect(), flags, self._text)
        painter.end()
