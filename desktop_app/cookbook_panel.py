"""Which models this machine can run, and what to do about the ones it can't.

A page in the navigation stack rather than a window, and reached from the top
bar rather than the rail. Both are about the same thing: it is a destination
like any other, it scrolls and resizes with the rest of the app, and opening a
second window for it put a form somewhere the rest of the interface could not
reach.

Everything it shows is decided by ``desktop_app.cookbook``, which holds no Qt.
This module draws conclusions and does no arithmetic, so there is one place to
be wrong about a recommendation and it is not here.

Nothing leaves the machine until Refresh is pressed. The Resources page says
this device's readings are sent nowhere, and a Cookbook that quietly called
Hugging Face on open would make that untrue. The only request this page makes
is the one a person asked for by pressing the button, and the button says what
it sends before it is pressed.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLineEdit, QVBoxLayout,
                             QWidget)

from .cookbook import (EXPLANATIONS, FITTING, LIKELY, NOT_ENOUGH_PUBLISHED,
                       POSSIBLE, PREVIOUS_NAME, VERIFIED,
                       CatalogError, TASKS, describe_changes, detect_hardware,
                       empty_reason, human_bytes, kv_cache_bytes, load_cache,
                       load_catalog, ollama_commands, parse_model, recommend,
                       rollback_catalog, save_catalog)
from .cookbook_hub import check_base
from .widgets import Select, action, label

# The name of the badge beside each model, and the label style it wears.
#
# "errorMessage" is the Qt objectName, not the theme token: there is no
# ``QLabel#error`` rule, so a badge given "error" matched nothing and came out
# in the default text colour. A model this device should not run then looked
# the same as one it should, which is the one thing a badge exists to prevent.
# It went unnoticed in a dialog and would not in a page, where the surrounding
# stylesheet is in reach.
TONE = {
    "likely to run": "verdictOk",
    "verified compatible": "verdictOk",
    "possible with compromises": "verdictWarn",
    "not recommended": "verdictBad",
    "unknown": "verdictUnknown",
}

# The card each verdict is drawn on, which is what carries the colour.
#
# A coloured left border rather than a badge, because that is what this app
# already does for a card whose status matters: #joinCard for success and
# #createCard for accent. A pill would have been a second visual vocabulary
# for a thing the interface already expresses.
#
# "unknown" and "possible with compromises" used to share one grey, which is
# not a small difference: one says we do not know, the other says it runs but
# slowly. A reader who cannot see the difference is being told nothing.
CARD = {
    "likely to run": "modelCardOk",
    "verified compatible": "modelCardOk",
    "possible with compromises": "modelCardWarn",
    "not recommended": "modelCardBad",
    "unknown": "modelCardUnknown",
}

# How many model cards one page holds.
#
# Ten is not a layout preference. A card is a dozen lines of wrapped text, and a
# catalogue of any size rendered at once turns the page into a single undifferentiated
# column that nobody scrolls through -- which is the same failure as having no
# scrollbar at all, reached from the other direction. Ten is about two screens,
# enough to compare against each other and few enough that the next page is
# something you expect to exist.
PAGE_SIZE = 10

# One card's padding, for every card. Left, top, right, bottom.
CARD_PADDING = (20, 17, 20, 17)

# How many builds one card's table shows at once.
#
# Eight, and then it says how many it is not showing. A repository with twenty
# builds produced a card over a thousand pixels tall, which is the clustering
# problem arriving from the other direction.
#
# The table is smallest first, so what falls past the cap is the larger builds
# -- which makes the truncation drop the ones that were going to be least
# useful on this device anyway. That is a consequence of the ordering, not a
# claim about what the device can run: on a machine with no graphics card and
# memory to spare, a build past the eighth may run perfectly well.
BUILDS_SHOWN = 8


class _StillPainting:
    """Hold a widget's painting still for the length of a rebuild.

    Not a context manager on the panel, because two places need it and neither
    owns the other. The ``finally`` matters more than the shape: a rebuild that
    raised with updates switched off would leave the list unable to repaint for
    the rest of the session, which is a far worse fault than a flash.
    """

    def __init__(self, widget):
        self.widget = widget
        self.was = False

    def __enter__(self):
        self.was = self.widget.updatesEnabled()
        self.widget.setUpdatesEnabled(False)
        return self.widget

    def __exit__(self, *exc):
        if self.was:
            self.widget.setUpdatesEnabled(True)
        return False


# What a refresh may not overwrite, because the Hub does not carry it.
#
# fetch_entry returns parameter counts, geometry, licences and file sizes --
# every figure this page does its arithmetic on. It returns nothing at all
# about what a model is *for*, and parse_model then defaults those fields,
# which meant a refresh silently undid every one of them:
#
#   tasks      -> empty, so the model vanished from every task filter while
#                 still appearing under "Everything"
#   name       -> the repository's last path segment, "Qwen2.5-0.5B-Instruct"
#                 in place of the curated "Qwen2.5 0.5B"
#   deprecated -> False, so a model its publisher had withdrawn came back
#                 as recommendable
#
# The tests missed it because their stub responses carried a "tasks" key. The
# Hub's own shape does not, and running one through is what the check below
# does.
CURATED_FIELDS = ("tasks", "name", "vision", "deprecated", "deprecated_reason")


def carry_over(model, result):
    """A refreshed record with the curated fields put back on top.

    Only for a model already in the catalogue. A response naming something
    nobody asked about still merges nothing, so what gets refreshed is what
    was requested.
    """
    merged = dict(result) if isinstance(result, dict) else {}
    if model is None:
        return merged
    for field in CURATED_FIELDS:
        merged[field] = getattr(model, field)
    return merged


class CookbookPanel(QWidget):
    """A list of models sized against the machine they are being read on."""

    def __init__(self, window, scroll_page):
        super().__init__()
        self.window = window
        self._busy = False
        self._cancelled = False
        self._page = 0
        # Set while a change to "Looking for" is waiting for its turn of the
        # event loop, so repeated presses collapse into one rebuild.
        self._task_pending = False
        # The build each card is showing, by repository. Not by position, and
        # not reset on every rebuild: switching the filter to Coding and back
        # should not quietly put a reader back on the build they rejected.
        # A repository that no longer has it -- a refresh, a rollback -- simply
        # falls back to the engine's choice, because every read goes through
        # _chosen_entry rather than trusting what is here.
        self._chosen = {}
        # Which cards have their build table open, by repository, for the same
        # reason. Choosing a build re-draws the card, and the reader was in the
        # middle of a list when they did it; a table that closes the instant it
        # is used is a table people stop trusting.
        self._open = set()
        # The model card currently on screen for each repository, so choosing a
        # build can change that one card instead of rebuilding the page.
        self._cards = {}
        # The parts of each card that change when the reader picks a different
        # build, by repository. The card itself does not change: of its 35
        # labels, three do -- the figures, the reason and the command -- and
        # the badge joins them when the verdict moves. The rest, the title and
        # all eight build rows among them, say the same thing whichever build
        # is being shown, and rebuilding the card to redraw three lines meant
        # repainting thirty-two that had not moved.
        self._parts = {}
        # Where a cached catalogue lives, and the host a refresh reads from.
        # Both are per workspace: two workspaces can be looking at different
        # catalogues, and a shared cache would have them overwrite each other.
        self.directory = getattr(window.storage, "directory", None)
        self.setAccessibleName("Cookbook: models this device can run")

        page, layout = scroll_page()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(page)
        page.setAccessibleName("Cookbook contents")
        # Kept, because the page is rebuilt from scratch on any change and the
        # scroll bar is the one thing a rebuild silently resets. Held as
        # ``scroller`` rather than ``scroll``, which is QWidget's own method.
        self.scroller = page

        # Everything below goes into ``layout`` -- the scroll area's own content
        # layout -- and not into ``outer``. ``outer`` holds the scroll area and
        # nothing else.
        #
        # Adding the cards beside the scroll area instead of inside it leaves
        # the scroll area empty, and an empty scroll area is one that measures
        # its content as nothing: its scrollbar range stays at zero so it never
        # draws, and the page grows taller than the window with everything past
        # the bottom edge clipped and no way to reach it. That is the whole
        # reason this page was unreadable, and it hid behind a test that only
        # checked a scroll area existed.
        #
        # ``layout`` is also where the shared 30/27 margins and 22px spacing come
        # from, so the page matches every other one in the stack instead of the
        # 0 margins and default spacing it was using.

        layout.addWidget(label("WHICH MODELS FIT THIS MACHINE", "eyebrow"))
        layout.addWidget(label("Cookbook", "title"))

        self.hardware = detect_hardware(
            window.storage.directory if hasattr(window, "storage") else None)
        card, column = self._card()
        column.addWidget(label("This device", "heading"))
        self.hardware_label = label(self.hardware.describe(), "muted", True)
        self.hardware_label.setWordWrap(True)
        column.addWidget(self.hardware_label)
        unknown = [name for name, origin in self.hardware.sources.items()
                   if origin not in ("detected", "manual")]
        self.unknown_label = label(
            ("Not detected here: " + ", ".join(sorted(unknown)) + ". Where an "
             "adapter shares system memory instead of having its own, models "
             "are sized against RAM, which is what the card would use anyway.")
            if unknown else self.hardware.graphics_note(),
            "muted", True)
        self.unknown_label.setWordWrap(True)
        # Parented first, then shown, for the same reason the build table is:
        # showing a widget that has no parent makes it a window of its own.
        column.addWidget(self.unknown_label)
        self.unknown_label.setVisible(bool(unknown))
        layout.addWidget(card)

        self.catalog_note = label("", "muted", True)
        self.catalog_note.setWordWrap(True)
        layout.addWidget(self.catalog_note)

        filters = QHBoxLayout()
        filters.addWidget(label("Looking for", "muted"))
        self.task_picker = Select()
        self.task_picker.setToolTip(
            "Narrow the list to one kind of work. Nothing is filtered by "
            "popularity or benchmark score, only by what fits.")
        # Everything first, and selected. Defaulting to a particular kind of
        # work hid most of the catalogue the moment the page opened, which is
        # the wrong first impression: someone who came to see what their
        # machine can hold is not asking about code yet.
        self.task_picker.addItem("Everything", None)
        for task, title in TASKS.items():
            self.task_picker.addItem(title, task)
        self.task_picker.currentIndexChanged.connect(self.task_changed)
        filters.addWidget(self.task_picker, 1)
        layout.addLayout(filters)

        self.results = QVBoxLayout()
        self.results.setSpacing(12)
        # Zeroed, because a layout on a widget gets Qt's 9px default. That
        # inset was the last thing keeping the cards off the page's left edge:
        # the device card sits directly in the page layout at 30px while the
        # model cards were pushed a further 9px in by this one, so their text
        # started at 51 and 60 -- three different edges, and the cards were 18px
        # narrower than the card above them for no reason anybody chose.
        self.results.setContentsMargins(0, 0, 0, 0)
        self.body = QWidget()
        self.body.setLayout(self.results)
        layout.addWidget(self.body)

        # Paging sits directly under the list and above the actions, so it is
        # read as part of the list it moves rather than as another toolbar.
        # Both controls are built here and hidden rather than removed when the
        # catalogue fits on one page: a button that appears and disappears
        # between filter choices makes the row jump under the pointer.
        self.previous = action("Previous", lambda: self.go_to_page(self._page - 1),
                               name="ghost")
        self.previous.setAccessibleName("Previous page of models")
        self.page_label = label("", "muted")
        self.page_label.setAlignment(Qt.AlignCenter)
        self.next = action("Next", lambda: self.go_to_page(self._page + 1), name="ghost")
        self.next.setAccessibleName("Next page of models")
        pager = QHBoxLayout()
        pager.addStretch()
        pager.addWidget(self.previous)
        pager.addWidget(self.page_label, 1)
        pager.addWidget(self.next)
        layout.addLayout(pager)

        # Where a refresh reads from. Reading the setting without ever offering
        # a way to change it left the mirror documented and unreachable, and
        # "set cookbook_source in the JSON" is not something a person can do.
        self.mirror = QLineEdit()
        self.mirror.setPlaceholderText("https://huggingface.co")
        # Shown rather than left as a placeholder, so a mirror someone set
        # months ago is visible on this page rather than being a fact only the
        # code knows.
        self.mirror.setText(self.base or "")
        self.mirror.setAccessibleName("Model catalogue mirror host")
        self.mirror.setToolTip(
            "Leave empty to read from huggingface.co. A mirror is used for "
            "every model on every refresh, and must be an https:// origin.")
        self.mirror.editingFinished.connect(self.save_mirror)
        self.use_mirror = action("Use this host", self.save_mirror, name="ghost")
        self.use_mirror.setAccessibleName("Use this mirror host")
        # A widget rather than a bare layout, so the whole row can be hidden
        # together. A layout has no visibility of its own and hiding the two
        # widgets inside it separately would leave the label's gap behind.
        self.mirror_box = QWidget()
        self.mirror_row = QHBoxLayout(self.mirror_box)
        self.mirror_row.setContentsMargins(0, 0, 0, 0)
        self.mirror_row.addWidget(label("Read from", "muted"))
        self.mirror_row.addWidget(self.mirror, 1)
        self.mirror_row.addWidget(self.use_mirror)
        layout.addWidget(self.mirror_box)

        self.refresh = action(f"Refresh from {self.host_name()}", self.ask_to_refresh,
                           name="ghost")
        self.refresh.setToolTip(
            f"Re-read model details from {self.host_name()}. Sends your network "
            "address and the model names being shown. Nothing is requested "
            "until you press this.")
        self.restore = action("Restore previous catalogue", self.do_restore, name="ghost")
        self.restore.setToolTip(
            "Put back the catalogue that was in use before the last refresh. "
            "Useful when a refresh brought in something that does not fit.")
        self.restore_mirror = action("Use the default host", self.reset_mirror,
                                     name="ghost")
        self.restore_mirror.setToolTip(
            "Go back to reading from huggingface.co. Only appears once a "
            "mirror has been set.")
        # Takes Refresh's place while a refresh runs. A refresh is up to three
        # requests per model at fifteen seconds each, so four models is a
        # minute and a half with no way to stop it.
        self.stop_refresh = action("Stop", self.cancel_refresh, name="ghost")
        self.stop_refresh.setToolTip("Stop after the model currently being read.")
        self.stop_refresh.setVisible(False)
        footer = QHBoxLayout()
        footer.addWidget(self.refresh)
        footer.addWidget(self.stop_refresh)
        footer.addWidget(self.restore)
        footer.addWidget(self.restore_mirror)
        footer.addStretch()
        # A Close box on a page is a lie: there is nothing to close. It was
        # there because this was a dialog, and it was the clearest sign the
        # widget had stopped being what it claimed. Leaving the page is the
        # rail's job.
        layout.addLayout(footer)

        self.mirror_error = label("", "errorMessage", True)
        self.mirror_error.setWordWrap(True)
        self.mirror_error.setVisible(False)
        layout.addWidget(self.mirror_error)
        self.mirror_box.setVisible(bool(self.base))
        self.restore_mirror.setVisible(bool(self.base))

        # A trailing stretch, and the last thing in this layout.
        #
        # Without it the page looked like a different application depending on
        # whether anything was in the list. The scroll area resizes its content
        # to the viewport, so a short page had space to spare, and a vertical
        # box with no stretch hands that spare space to every row that will take
        # it rather than pooling it at the end. Measured with the list empty
        # and no other change: the eyebrow 14px -> 104px, the title 42 -> 105,
        # the device card 86 -> 104, the catalogue note 18 -> 104. Ninety pixels
        # of dead space above and below every element, which is why an empty
        # filter read as a different page rather than as the same one with
        # nothing on it. One stretch puts the slack in one place and both states
        # render identically.
        layout.addStretch()

        self.load()

    def showEvent(self, event):
        """Re-read the mirror and this device's memory each time the page is shown.

        A setting changed while this page was not on screen should not leave it
        describing a host it has stopped using -- the box, the button and the
        disclosure all name where the next request will go, and a stale name on
        those is the page making a claim it cannot keep.

        The hardware is re-read for the same reason, and it matters more here.
        The page ranks models by how much memory is free, and it read that once,
        at construction, and never again -- so the reader could open a browser
        after the app started and every "likely to run" on the page would still
        be sized against whatever happened to be free at launch. It is a page
        making a claim about the machine that has quietly stopped being true.

        Only when the figure has actually moved, because re-detecting rebuilds
        every card and re-classifies the catalogue, and arriving on a page
        should not visibly redraw it.

        Skipped while the box has focus, so that arriving on the page does not
        overwrite what somebody is in the middle of typing.
        """
        super().showEvent(event)
        if self.mirror.hasFocus():
            return
        self.mirror.setText(self.base or "")
        self.mirror_box.setVisible(bool(self.base))
        self.restore_mirror.setVisible(bool(self.base))
        self.refresh.setText(f"Refresh from {self.host_name()}")
        self.refresh.setToolTip(
            f"Re-read model details from {self.host_name()}. Sends your network "
            "address and the model names being shown. Nothing is requested "
            "until you press this.")
        self._reread_hardware()

    def _reread_hardware(self, force=False):
        """Re-detect this device, and redraw only if the memory has moved.

        A percentage rather than an absolute figure, because "1% of 32GB" is a
        rounding difference and "20% of 32GB" is a laptop having closed four
        things. Two per cent keeps a slowly drifting background process from
        repainting the page on every visit.
        """
        fresh = detect_hardware(
            self.window.storage.directory if hasattr(self.window, "storage") else None)
        before, after = self.hardware.memory_available, fresh.memory_available
        if not force and before and after:
            if abs(after - before) < before * 0.02:
                return
        self.hardware = fresh
        self.hardware_label.setText(self.hardware.describe())
        self.render_models()

    @property
    def base(self):
        """Where a refresh reads from, read live rather than remembered.

        Cached at construction it went stale the moment the setting was changed
        anywhere else -- the page would go on labelling its own button with a
        host it had stopped using, which is the worst way for a control to
        lie, since the button is what says where the request is about to go.
        """
        return self.window.storage.settings.get("cookbook_source") or None

    def host_name(self):
        """Where a refresh reads from, as a person would name it."""
        return self.base or "huggingface.co"

    def host_origin(self):
        """The full origin a request will go to, scheme included.

        Separate from :meth:`host_name` because the button reads better saying
        "huggingface.co" and the disclosure must not. A dialog asking for
        permission to make twelve requests should show the reader the actual
        address, and showing it without its scheme would mean the default host
        was named one way and a mirror another, which is exactly the sort of
        thing that makes the address hard to check.
        """
        return check_base(self.base)

    def save_mirror(self):
        """Remember the mirror host, or say why the one typed is not usable.

        The check is the one the request itself uses, so a value accepted here
        cannot fail later at the point where the button says it is about to ask
        somewhere. An empty box is not an error: it is the way back to the
        default, which is also what the button beside it does.
        """
        typed = self.mirror.text().strip()
        try:
            host = check_base(typed or None)
        except ValueError as error:
            self.mirror_error.setText(str(error))
            self.mirror_error.setVisible(True)
            return
        self.mirror_error.setVisible(False)

        previous = dict(self.window.storage.settings)
        self.window.storage.update(cookbook_source=None if not typed else host)
        try:
            self.window.storage.save()
        except Exception as error:
            # Put the setting back rather than leaving the box showing a host
            # that was never saved, which would survive a restart and read as
            # though it had taken.
            self.window.storage.settings = previous
            self.mirror_error.setText(f"That could not be saved: {error}")
            self.mirror_error.setVisible(True)
            return

        self.refresh.setText(f"Refresh from {self.host_name()}")
        self.refresh.setToolTip(
            f"Re-read model details from {self.host_name()}. Sends your network "
            "address and the model names being shown. Nothing is requested "
            "until you press this.")
        self.mirror_box.setVisible(bool(self.base))
        self.restore_mirror.setVisible(bool(self.base))
        self.catalog_note.setText(
            f"Refreshes will be read from {self.host_name()}."
            if self.base else
            "Refreshes will be read from huggingface.co.")

    def reset_mirror(self):
        """Empty the box and go back to the public catalogue."""
        self.mirror.clear()
        self.save_mirror()

    def _card(self, name="card"):
        from .window import frame
        widget, column = frame(name)
        # Every card, whatever it is for. It used to be set on the model card
        # alone, so the device card, the withdrawn ones and the empty state all
        # fell back to Qt's 9px default and their text started 11px outboard of
        # the model cards beside them -- four different left edges on one page,
        # measured at 30, 39, 40 and 62. This is the padding the rest of the app
        # uses for a card, so one number covers all of them.
        padding = CARD_PADDING
        if name.startswith("modelCard"):
            # Two pixels back, because a model card carries a 3px left border
            # where the others carry 1px, and the extra two push its text out
            # of line with everything else on the page.
            padding = (CARD_PADDING[0] - 2,) + CARD_PADDING[1:]
        column.setContentsMargins(*padding)
        return widget, column

    def load(self):
        """The cached catalogue if there is one, otherwise the bundled one.

        Never touches the network. A cache that cannot be read is not an error
        worth stopping for: the bundled catalogue is always there, which is the
        reason it ships, and a line saying so is more use than a failure.
        """
        self.cached = load_cache(self.directory) if self.directory else None
        try:
            bundled = load_catalog()
        except CatalogError as error:
            if not self.cached:
                self.catalog = []
                self.catalog_note.setText(f"The bundled catalogue could not be read: {error}")
                self.render_models()
                return
            bundled = []
        self.catalog = self.cached if self.cached is not None else bundled
        if self.cached is None:
            self.catalog_note.setText(
                "Showing the catalogue that shipped with the app.")
        else:
            self.catalog_note.setText("Showing the catalogue saved by your last refresh.")
        self.refresh_controls()
        self.render_models()

    def refresh_controls(self):
        """Enable the actions that have something to act on."""
        self.restore.setEnabled(bool(self.directory and self.directory.joinpath(
            PREVIOUS_NAME).is_file()))

    def _set_busy(self, busy):
        """Lock the page while a refresh is in flight, and swap Refresh for Stop.

        Restore and the mirror box stayed live throughout a refresh before, which
        is how a restore could shorten the catalogue underneath the request in
        progress. They are controls that change what the refresh is reading, so
        they are disabled for its duration rather than trusted to behave.
        """
        self._busy = busy
        self.refresh.setEnabled(not busy)
        self.refresh.setVisible(not busy)
        self.stop_refresh.setVisible(busy)
        # The button carries the progress while it runs, so putting it back is
        # part of ending the run rather than something the caller has to
        # remember to do on each path out.
        if not busy:
            self.refresh.setText(f"Refresh from {self.host_name()}")
        self.restore.setEnabled(not busy and bool(self.directory and
                                                 self.directory.joinpath(PREVIOUS_NAME).is_file()))
        self.restore_mirror.setEnabled(not busy)
        self.task_picker.setEnabled(not busy)

    def cancel_refresh(self):
        """Ask the refresh to stop after the model it is reading.

        The request in flight is not abandoned -- there is nothing to abandon
        it with -- but no further ones are issued, and whatever has already
        come back is kept.
        """
        self._cancelled = True
        self.stop_refresh.setEnabled(False)
        self.stop_refresh.setText("Stopping.")

    def render_models(self):
        """Rebuild the list from the first page.

        Every reason the list changes -- a different task, a refresh, a
        rollback, a first load -- goes through here, and every one of them
        returns to page one. Carrying the page number across would land the
        reader on an empty page whenever a filter or a refresh changed how many
        models there are, and an empty page reads as "nothing fits this
        device", which is a claim about their hardware that they never made.
        """
        self._page = 0
        self.render_page()

    def go_to_page(self, number):
        """Show a different page of the same list."""
        self._page = max(0, int(number))
        self.render_page()

    def page_entries(self):
        """Everything the current filter matches, as one ordered list of cards.

        Recommended models first and withdrawn after them, in a single sequence
        rather than two sections, so the page limit can be applied to the whole
        thing. Withdrawn are not ranked with the rest: a withdrawn model cannot
        be recommended, and putting it among the ones that can would be an
        argument for downloading it. They are listed so somebody who already has
        one can find out why nothing recommends it.
        """
        task = self.task_picker.currentData()
        if not self.catalog:
            return []
        offered = recommend(self.catalog, self.hardware, task=task)
        withdrawn = [model for model in self.catalog
                     if model.deprecated and (not task or task in model.tasks)]
        return [("model", row) for row in offered] + [("withdrawn", model)
                                                      for model in withdrawn]

    def render_page(self):
        """Draw one page of cards, and the controls that move between them.

        Painting is held still for the whole of it. This tears every card down
        before building any, so for the length of the call the page genuinely is
        empty -- measured: content 1984px down to the viewport's 827px, scroll
        range 1157 down to 0, and 86% of the viewport showing nothing but
        background. Qt normally merges the updates so no hole is ever presented,
        which is why this reads as an occasional flash rather than a blank page
        every time. Holding the painting still makes it not a race.
        """
        with self._still():
            self._draw_page()

    def _draw_page(self):
        while self.results.count():
            item = self.results.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is None:
                continue
            # hidden() then deleteLater(), which is what window.clear_layout
            # does everywhere else in this app and is the only recipe here that
            # does not put a stray window on screen.
            #
            # setParent(None) was here instead, to detach cards before the event
            # loop got round to them. It does that, and it also promotes the
            # card to a top-level window -- measured: isWindow() goes from
            # False to True -- which is what a small white rectangle appearing
            # over the app is. Each re-render did it for every card on screen.
            widget.hide()
            widget.deleteLater()

        entries = self.page_entries()
        pages = max(1, -(-len(entries) // PAGE_SIZE))
        self._page = min(max(0, self._page), pages - 1)
        start = self._page * PAGE_SIZE
        window = entries[start:start + PAGE_SIZE]

        withdrawn_header_drawn = False
        self._cards = {}
        # With them, so parts never outlive the cards they describe -- a
        # deleted card holding a live label would leave _update_card writing
        # into something no longer on the page.
        self._parts = {}
        for kind, entry in window:
            if kind == "model":
                card = self._model_card(entry)
                self._cards[entry["model"].repo] = card
                self.results.addWidget(card)
                continue
            if not withdrawn_header_drawn:
                withdrawn_header_drawn = True
                if start:
                    # Only separated from the recommended models when it is not
                    # already the top of the page.
                    self.results.addSpacing(6)
                # Unpacked, because _card returns the frame and its layout and
                # this used to bind the pair to one name and add to the tuple.
                # Any page with a withdrawn model on it raised
                # AttributeError and drew nothing at all.
                header, column = self._card()
                column.addWidget(label("Withdrawn by their publishers", "heading"))
                column.addWidget(label(
                    "Still listed so you can tell what happened to one you have. "
                    "They are not recommended for download.", "muted", True))
                self.results.addWidget(header)
            self.results.addWidget(self._withdrawn_card(entry))

        if window:
            self.results.addStretch()
        else:
            self._empty_card()

        self.update_pager(len(entries), pages)

    def update_pager(self, total, pages):
        """Say how much of the list is on screen, and which page this is."""
        paging = pages > 1
        for control in (self.previous, self.next, self.page_label):
            control.setVisible(paging)
        if not paging:
            self.page_label.setText("")
            return
        self.page_label.setText(
            f"Page {self._page + 1} of {pages} · {total} models")
        self.page_label.setAccessibleName(
            f"Page {self._page + 1} of {pages}, showing up to {PAGE_SIZE} of "
            f"{total} models")
        self.previous.setEnabled(self._page > 0)
        self.next.setEnabled(self._page < pages - 1)

    def _withdrawn_card(self, model):
        """One model that its publisher has taken back."""
        card, column = self._card()
        column.addWidget(label(model.display, "heading", True))
        note = label(
            "Withdrawn by its publisher." + (
                f" {model.deprecated_reason}" if model.deprecated_reason else ""),
            "errorMessage", True)
        note.setWordWrap(True)
        column.addWidget(note)
        column.addWidget(label(
            f"{model.size_class} · {model.license or 'licence unstated'} · "
            f"read {model.last_verified or 'unknown'}", "muted", True))
        return card

    def _empty_card(self):
        """Draw the one card that says there is nothing, saying which nothing.

        ``recommend`` returns an empty list for four unrelated reasons and the
        page used to report all of them as "Nothing in the catalogue fits this
        device". Choosing *Image understanding* produced exactly that line
        while all four models in the catalogue fit comfortably -- none of them
        is simply tagged ``vision``. A gap in a four-model catalogue was being
        reported as a verdict on the reader's hardware, with advice about
        freeing memory that had nothing to do with the problem.

        So the reason is worked out where there are no widgets, and drawn as
        itself. A filter that hid the list offers its way back, because that is
        the one case where the reader can do something about it right now.
        """
        reason = empty_reason(self.catalog, self.hardware,
                              task=self.task_picker.currentData())
        card, column = self._card()
        column.addWidget(label(reason.heading, "heading", True))
        detail = label(reason.detail, "muted", True)
        detail.setWordWrap(True)
        column.addWidget(detail)

        if reason.can_widen:
            column.addSpacing(4)
            column.addWidget(action("Show every model", self.show_everything,
                                    name="ghost"))
        elif reason.kind == NOT_ENOUGH_PUBLISHED:
            column.addSpacing(4)
            # ask_to_refresh, not do_refresh. Every button on this page that
            # leaves the machine goes through the disclosure, and this one did
            # not: it was wired straight to the method the disclosure exists to
            # sit in front of. A reader who filtered to an empty list could
            # therefore trigger a dozen requests to a host they had never been
            # told about, without being asked.
            #
            # No catalogue means no button, rather than one routed through the
            # gate -- ask_to_refresh returns at once when there is nothing to
            # refresh, and a control that always works is worse than one that
            # does not. The card already says what is wrong, and the footer
            # already offers Restore previous catalogue, which is the thing
            # that can actually help when the catalogue is missing.
            column.addWidget(action(
                f"Refresh from {self.host_name()}", self.ask_to_refresh,
                name="ghost"))
        self.results.addWidget(card)

    def task_changed(self):
        """A change to "Looking for", deferred to the next turn of the loop.

        Every way of changing this list -- the picker, the "Show every model"
        button, a refresh -- throws the whole page away and builds it again,
        and the picker is being dismissed from its own dropdown at the moment
        the signal arrives. So the rebuild used to happen underneath a popup
        that had not closed yet, and the dropdown stayed up over a list it no
        longer described.

        Deferring to the next turn lets the dismissal finish first, and
        collapses a burst of changes into one rebuild rather than one per
        press, which matters for a dropdown you can hold Enter on.

        This is not what stopped the stray windows. That was the build table
        being shown before it had been put in a layout -- a widget with no
        parent is a window -- and it happened on every rebuild of every card,
        which is why the same window appeared here, on a build row, and on a
        refresh. The deferral is kept because the dropdown teardown and the
        coalescing are real problems of their own, not because it stopped that.
        """
        if self._task_pending:
            return
        self._task_pending = True
        QTimer.singleShot(0, self._task_changed_now)

    def _task_changed_now(self):
        """The deferred half of task_changed."""
        self._task_pending = False
        self.render_models()

    def show_everything(self):
        """Put the filter back to Everything, which is where models are.

        Nothing is rendered here. The picker's own signal does that, through
        task_changed, so there is exactly one rebuild per change and it happens
        after the interaction that caused it has finished.
        """
        index = self.task_picker.findData(None)
        if index < 0:
            return
        self.task_picker.setCurrentIndex(index)

    def _chosen_entry(self, row):
        """The build this card is showing: the reader's choice, or the best one.

        Always resolved rather than remembered. A catalogue that changed under
        the choice -- a refresh, a rollback, a filter over a different
        catalogue -- no longer has that build, and a remembered one would be a
        command naming a file that is not there.
        """
        builds = row.get("builds") or []
        wanted = self._chosen.get(row["model"].repo)
        for entry in builds:
            if wanted and entry["variant"].quantization == wanted:
                return entry
        return next((entry for entry in builds if entry["selected"]), None)

    def choose_build(self, repo, quantization):
        """Show a different build of this model, and re-draw the card for it.

        Deferred so the click finishes travelling before the card it sits in is
        replaced, and so the scroll position can be put back once the new
        layout has been measured.

        The deferral is not what stopped the stray windows -- that was the build
        table being shown before it was parented, on every rebuild of every
        card. This is kept because the swap changes the card's height, and the
        scroll bar needs the layout to have run before it will honour a
        setValue; restoring the position in the same turn as the swap clamps it
        to zero, which is the page snapping back to the top.
        """
        if self._chosen.get(repo) == quantization:
            return
        self._chosen[repo] = quantization
        where = self.scroller.verticalScrollBar().value()
        QTimer.singleShot(0, lambda: self._rebuild_at(where, repo))

    def _rebuild_at(self, where, repo):
        """The deferred half of choose_build: swap one card, keep the reader put.

        Built and inserted *before* the old card is destroyed, for the fallback
        path only. The usual path updates the card in place and destroys
        nothing.

        Falls back to a full rebuild for anything it cannot place or update, so
        a card that is not where it was thought to be costs a redraw rather
        than a page describing the wrong build.
        """
        # No painting guard here any more. It existed because the swap hid the
        # old card and left a gap before the replacement painted into it, and
        # the card is no longer replaced: nothing is hidden, nothing is built,
        # and four labels are re-set. render_page still tears the page down, so
        # it keeps its own.
        self._swap_card(repo)
        bar = self.scroller.verticalScrollBar()
        if where:
            bar.setValue(where)

    def _swap_card(self, repo):
        """Point this card at a different build, or rebuild it if we cannot.

        The update is the whole point: the card and every label on it stay put,
        and only the four that move are re-set. Rebuilding is the fallback for a
        card whose parts are missing or whose place in the layout we cannot
        find -- better a fresh card than one left describing the wrong build.
        """
        old = self._cards.get(repo)
        index = self.results.indexOf(old) if old is not None else -1
        entry = next((item for kind, item in self.page_entries()
                      if kind == "model" and item["model"].repo == repo), None)
        if old is None or index < 0 or entry is None:
            self.render_page()
            return
        if self._update_card(repo, entry):
            return
        fresh = self._model_card(entry)
        self.results.insertWidget(index, fresh)
        self._cards[repo] = fresh
        old.hide()
        old.deleteLater()

    def _still(self):
        """Repaint the list once at the end, rather than throughout.

        A rebuild hides what was on screen before it puts anything in its place,
        and between those two moments the compositor is free to present the hole.
        It is a sub-millisecond window that Qt usually closes for us by merging
        the updates, which is why this reads as an occasional flash rather than
        as a blank page every time.

        Suspending updates makes it not a race. ``body`` rather than the window,
        so the catalogue note and the pager -- which is what says the refresh
        worked -- still repaint as they change.
        """
        return _StillPainting(self.body)

    def toggle_builds(self, repo, opened):
        """Remember which cards are open, so a re-render can leave them that way."""
        if opened:
            self._open.add(repo)
        else:
            self._open.discard(repo)

    def _other_builds(self, column, row):
        """Every build of this model, as a table you can choose from.

        It used to be a list you could only read, and a list you could only
        read was not much use: the page said a 7B model was "possible with
        compromises" at four-bit, offered the two-bit build in the row below,
        and then gave you a command for the four-bit one. Three failures at
        once -- the alternative was unselectable, unrunnable, and seven copies
        of the same fifty-nine characters differing only in the tag.

        Choosing a build now changes the card: the verdict and its colour, the
        reason, the figures and the command all follow, because the verdict was
        always computed per build by ``build_rows`` and only the one on show
        was ever drawn. The engine's choice is the default rather than being
        excluded, so what the card is using is visible in the list instead of
        being something you have to infer.
        """
        builds = list(row.get("builds") or [])
        if len(builds) < 2:
            return
        chosen = self._chosen_entry(row)

        on_card = sum(1 for entry in builds
                      if entry["verdict"]["result"] in (VERIFIED, LIKELY))
        spilling = sum(1 for entry in builds if entry["spills"])
        # Counted separately and worded honestly. "Possible with compromises"
        # is technically a fitting verdict, so calling these "fits" would have
        # claimed six of seven run on the graphics card when one does.
        #
        # Kept short on the button itself: a ghost button sizes to its text and
        # does not wrap, so a long summary here forced the whole card wider
        # than the page and put a horizontal scrollbar under it. The counts go
        # in the tooltip and inside the section, both of which wrap.
        summary = [f"{len(builds)} builds"]
        if on_card:
            summary.append(f"{on_card} run on the graphics card")
        if spilling:
            summary.append(f"{spilling} spill to the CPU")
        detail_text = " · ".join(summary[1:]) or "all of them fit system memory"
        toggle = action(summary[0], None, name="ghost")
        toggle.setToolTip(f"{summary[0]} — {detail_text}")
        toggle.setAccessibleName(
            f"Show the {len(builds)} builds of {row['model'].display}, with "
            f"their size and what this device makes of each. {detail_text}.")
        toggle.setCheckable(True)
        toggle.setChecked(row["model"].repo in self._open)
        column.addWidget(toggle, 0, Qt.AlignmentFlag.AlignLeft)

        detail = QWidget()
        listing = QVBoxLayout(detail)
        listing.setContentsMargins(0, 6, 0, 0)
        listing.setSpacing(4)
        note = label(detail_text, "muted", True)
        note.setWordWrap(True)
        listing.addWidget(note)

        shown = builds[:BUILDS_SHOWN]
        for entry in shown:
            listing.addWidget(self._build_row(row["model"], entry, chosen))
        hidden = len(builds) - len(shown)
        if hidden > 0:
            # "not shown", and not "this device cannot run": the table is
            # smallest first, so what is past the cap really is the larger
            # builds, but nothing here has established that this device cannot
            # run them. On a machine with no graphics card and memory to spare
            # every one of them may be perfectly runnable, and the page would
            # be telling a reader their hardware cannot do something it can.
            listing.addWidget(label(
                f"and {hidden} larger build{'s' if hidden != 1 else ''} not "
                "shown", "muted", True))

        # Added to the layout *before* it is shown. A widget with no parent is
        # a window in Qt: showing one hands it the app icon and the minimise,
        # maximise and close buttons. This one was parentless for the few
        # microseconds between here and column.addWidget below, which is why
        # the stray window lasted milliseconds and why nothing that looked
        # afterwards ever found it -- it was gone before the first repaint.
        column.addWidget(detail)
        # Now that it is a child widget, showing it just reveals it.
        detail.setVisible(toggle.isChecked())
        toggle.toggled.connect(detail.setVisible)
        toggle.toggled.connect(
            lambda opened, repo=row["model"].repo: self.toggle_builds(repo, opened))

    def _build_row(self, model, entry, chosen):
        """One build, as a row you can choose.

        A checkable ghost button rather than a label with a click handler, so
        the choice is a real control -- reachable by keyboard, and drawn as
        chosen by the ``#ghost:checked`` rule the app already has. Its property
        marks it as a build row for the tests, since the objectName has to stay
        ``ghost`` for the styling to apply.
        """
        variant, verdict = entry["variant"], entry["verdict"]
        total = (entry["figures"] or {}).get("total")
        is_chosen = bool(chosen and variant.quantization == chosen["variant"].quantization)

        row = action("", lambda: self.choose_build(model.repo,
                                                   variant.quantization),
                     name="ghost")
        row.setCheckable(True)
        # Auto-exclusive, and the rows are all children of this card's table, so
        # the group is exactly one card's builds and no two cards interfere.
        #
        # This is what stops the card flickering. A checkable button toggles
        # itself as part of the click, before this handler runs, and the state
        # it toggles to is then overwritten by a rebuild a turn later -- so the
        # card was drawn twice, milliseconds apart, showing two different
        # choices: for a moment the build just clicked *and* the one it
        # replaced both drawn as chosen.
        #
        # Exclusive, the same click unchecks the old row and checks the new one
        # in a single step, so the picture the button draws is already the
        # picture the rebuild is about to draw. And an exclusive button cannot
        # be unchecked by clicking itself, which fixes the other half: without
        # it, clicking the build that is already chosen cleared it off the card
        # and left it cleared, because there was nothing to rebuild to correct
        # it.
        row.setAutoExclusive(True)
        row.setChecked(is_chosen)
        row.setProperty("buildRow", True)
        row.setProperty("buildVariant", variant.quantization)
        row.setCursor(Qt.CursorShape.PointingHandCursor)

        line = QHBoxLayout(row)
        line.setContentsMargins(10, 5, 10, 5)
        line.setSpacing(10)

        line.addWidget(label(variant.quantization or variant.name, "buildName"))

        size = f"{human_bytes(variant.size)} download"
        if total and total != variant.size:
            size += f" · {human_bytes(total)} in memory"
        line.addWidget(label(size, "muted", True), 1)

        if entry["selected"]:
            line.addWidget(label("recommended", "muted"))

        if entry["spills"]:
            mark, style = "spills to CPU", TONE[POSSIBLE]
        else:
            result = verdict["result"]
            # Short in the row, whole on hover. "possible with compromises" is
            # 24 characters of prose in a dense list, and it also forced the
            # card wider than the page.
            mark = {"verified compatible": "on the card",
                    "likely to run": "on the card",
                    "not recommended": "too big"}.get(result, "unknown")
            style = TONE.get(result, "muted")
        verdict_label = label(mark, style)
        gloss = EXPLANATIONS.get(verdict["result"])
        verdict_label.setToolTip(
            f"{verdict['result']} — {verdict['reason']}" + (
                f"\n\n{gloss}" if gloss else ""))
        # Carries the whole verdict too, so the short mark cannot be read
        # without the number that produced it. The row's own accessible name
        # says the same thing, because the row is now a control; this is what
        # a reader gets if they move onto the text itself.
        verdict_label.setAccessibleName(
            f"{verdict['result']}, {verdict['reason']}" + (
                f". {gloss}" if gloss else ""))
        line.addWidget(verdict_label)

        parts = [variant.quantization, size]
        if entry["selected"]:
            parts.append("the recommended build")
        parts.append(f"{verdict['result']}, {verdict['reason']}")
        if gloss:
            parts.append(gloss)
        row.setAccessibleName(". ".join(parts))
        row.setToolTip(verdict["reason"])
        return row

    def _model_card(self, row):
        """One model, in four groups: who it is, what it costs, how to run it,
        and where the figures came from.

        The groups are separated by more space than the lines inside them are,
        which is the whole change. Every child used to sit exactly 6px from the
        next, and six of them were the same grey at the same size, so a card
        read as one undifferentiated block: the verdict's own explanation was
        indistinguishable from the disclaimer three lines below it.
        """
        model = row["model"]
        # The build being shown, which is the reader's choice where they have
        # made one. Everything below -- the badge, its colour, the figures, the
        # command -- follows it, because the verdict was always worked out per
        # build and only one of them was ever drawn.
        entry = self._chosen_entry(row)
        variant = (entry or {}).get("variant", row["variant"])
        verdict = (entry or {}).get("verdict", row["verdict"])
        card, column = self._card(CARD[verdict["result"]])
        parts = {"verdict": verdict["result"]}

        # Group one: what this is.
        column.addWidget(label(model.display, "heading", True))
        # The verdict sits under the name as its own line rather than floating
        # at the far right of the heading row. Beside a 19px heading it read as
        # a second title, and "possible with compromises" is twenty-four
        # characters of prose competing with the model's name for attention.
        # The card's own left border carries the signal now; this says which.
        badge = label(verdict["result"], TONE.get(verdict["result"], "muted"))
        parts["badge"] = badge
        self._style_badge(badge, verdict["result"])
        column.addWidget(badge)

        # Group two: what it costs here.
        column.addSpacing(14)
        facts = (f"{model.size_class} · {human_bytes(variant.size)} download "
                 f"· {variant.quantization} · {model.license or 'licence unstated'}")
        # #meta rather than #muted: these are the figures the card exists to
        # give, and they were the same grey as the disclaimer.
        parts["facts"] = label(facts, "meta", True)
        column.addWidget(parts["facts"])
        # And the reason is body text, because it is the sentence the reader
        # came for. It was muted, which is the style every quiet line on the
        # card also used.
        reason = label(verdict["reason"], None, True)
        reason.setWordWrap(True)
        parts["reason"] = reason
        column.addWidget(reason)

        longer = self._longer_context_warning(model)
        if longer:
            warning = label(longer, "muted", True)
            warning.setWordWrap(True)
            column.addWidget(warning)

        # Group three: how to run it.
        commands = ollama_commands(model, variant)
        if commands:
            column.addSpacing(16)
            column.addWidget(label("Run it", "muted"))
            # One block holding all of them, monospaced. In the proportional
            # face these were in, a repository path is an unbroken run of
            # characters that wraps wherever it happens to land, which is the
            # worst place for something the reader is about to copy and paste
            # into a terminal and get exactly right.
            block = label("\n".join(entry["command"] for entry in commands),
                          "command", True)
            block.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
                | Qt.TextInteractionFlag.TextSelectableByKeyboard)
            block.setAccessibleName(
                f"{len(commands)} commands to run this model. Selectable, so "
                "it can be copied rather than typed.")
            parts["commands"] = block
            column.addWidget(block)

        # Group four: where these numbers came from. Both lines in #footer, at a
        # size and colour meant to be read last rather than scanned, and set
        # close together so they read as one quiet footer instead of two more
        # things competing with the figures above.
        column.addSpacing(16)
        provenance = label(
            f"From {model.repo} on {model.source} · builds by "
            f"{model.artifact_repo} · read {model.last_verified or 'unknown'}",
            "footer", True)
        provenance.setWordWrap(True)
        column.addWidget(provenance)

        self._other_builds(column, row)

        disclaimer = label(
            "Sized from published figures, not measured on this device.",
            "footer", True)
        disclaimer.setWordWrap(True)
        column.addWidget(disclaimer)
        self._parts[model.repo] = parts
        return card

    def _style_badge(self, badge, result):
        """Say what the verdict is, in words as well as colour.

        Written out in EXPLANATIONS and never rendered until the badges were
        given one: the verdict is an internal phrase, and a reader who cannot
        see the colour has nothing to go on.
        """
        badge.setObjectName(TONE.get(result, "muted"))
        # Load-bearing, and it looks like noise. Setting a stylesheet is what
        # makes Qt re-evaluate the selector, and an object name on its own does
        # not -- so without this line the badge would keep the colour of the
        # verdict it had just been told had changed. The card needs the explicit
        # unpolish/polish instead, in _update_card, because it has no such line.
        badge.setStyleSheet("")
        gloss = EXPLANATIONS.get(result)
        if gloss:
            badge.setToolTip(gloss)
            badge.setAccessibleName(f"{result}: {gloss}")
        else:
            badge.setToolTip("")
            badge.setAccessibleName(f"Assessment: {result}")

    def _update_card(self, repo, row):
        """Change which build this card is describing, without rebuilding it.

        Everything on a card is worked out per build, and _model_card drew the
        one being shown. Only four things move when the reader picks another:
        the figures, the reason, the command, and the badge when the verdict
        itself changes. Rebuilding the card to move them repainted thirty-two
        labels that had not changed, which is the whole of what the reader saw
        as a flash.

        The card's border colour is left alone unless the verdict moved. The
        object name is what the stylesheet keys the border off, and changing it
        re-polishes the card -- so it is only worth doing when there is
        genuinely a new verdict to show.
        """
        card = self._cards.get(repo)
        parts = self._parts.get(repo)
        if card is None or parts is None:
            return False

        model = row["model"]
        entry = self._chosen_entry(row)
        variant = (entry or {}).get("variant", row["variant"])
        verdict = (entry or {}).get("verdict", row["verdict"])

        parts["facts"].setText(
            f"{model.size_class} · {human_bytes(variant.size)} download · "
            f"{variant.quantization} · "
            f"{model.license or 'licence unstated'}")
        parts["reason"].setText(verdict["reason"])
        commands = ollama_commands(model, variant)
        if commands:
            parts["commands"].setText(
                "\n".join(item["command"] for item in commands))

        if verdict["result"] != parts.get("verdict"):
            self._style_badge(parts["badge"], verdict["result"])
            parts["badge"].setText(verdict["result"])
            card.setObjectName(CARD[verdict["result"]])
            # Qt does not re-match a stylesheet selector when only the object
            # name changes, so the card kept the border colour of the verdict it
            # had just been told was wrong -- green while named modelCardBad.
            # Measured before this: the border stayed #3ba55d across a move to
            # a build that classified as not recommended.
            #
            # Same three lines MainWindow.set_field_error uses for the same
            # reason. The badge needs none of this: _style_badge's empty
            # setStyleSheet already forces it to re-evaluate, which is why its
            # colour moved when this one did not.
            card.style().unpolish(card)
            card.style().polish(card)
            card.update()
            parts["verdict"] = verdict["result"]
        return True

    def _longer_context_warning(self, model):
        """Say what a longer conversation costs, when it would not fit.

        The verdict is for a 4k conversation. The model's own limit is often
        far higher, and using it is not free: the cache grows with every token,
        and at the full limit this model's is 1.75GiB. Stating that here is the
        difference between a reader choosing a context length and discovering
        one for themselves.
        """
        if not model.context or model.context <= 4096:
            return None
        full = kv_cache_bytes(model, model.context)
        if not full:
            return None
        current = kv_cache_bytes(model, 4096)
        extra = (full - (current or 0)) / 1024 ** 3
        if extra <= 0.25:
            return None
        return (f"At its full {model.context:,} token context this model needs "
                f"about {human_bytes(full)} of conversation memory, "
                f"{human_bytes(current or 0)} more than the figures above. "
                f"Raise the context length only if you need it.")

    def ask_to_refresh(self):
        """Say what pressing Refresh will do, then do it if that is welcome.

        The disclosure used to be a muted line above the list and a tooltip on
        the button -- a paragraph somebody has to go looking for, about twelve
        requests they are agreeing to. It is now asked for at the moment of the
        action, where the decision is actually being made.

        Remembered per host rather than once and forever. Pointing the Cookbook
        at a mirror means a different site sees the request, and agreeing to
        tell huggingface.co which models you run is not agreeing to tell
        somebody else.

        ``do_refresh`` is left ungated so it is the thing under test rather
        than a dialog: nothing else in the app calls it.
        """
        if self._busy or not self.catalog:
            return
        host = self.host_origin()
        if self.consented(host):
            self.do_refresh()
            return

        from .dialogs import RefreshDisclosure
        dialog = RefreshDisclosure(self.window, host, self.catalog,
                                   mirror=bool(self.base))
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if dialog.should_remember():
            self.remember_consent(host)
        self.do_refresh()

    def consented(self, host):
        """Whether this host has been agreed to before."""
        agreed = self.window.storage.settings.get("cookbook_refresh_ok")
        return isinstance(agreed, dict) and bool(agreed.get(host))

    def remember_consent(self, host):
        """Record the agreement, and refuse to keep it if it cannot be saved."""
        storage = self.window.storage
        previous = storage.settings.get("cookbook_refresh_ok")
        try:
            storage.change(lambda settings: settings.__setitem__(
                "cookbook_refresh_ok",
                {**(previous if isinstance(previous, dict) else {}), host: True}))
        except Exception as error:
            # Put it back rather than leaving a remembered agreement that did
            # not survive a restart: the reader would not be warned again this
            # session and would be warned again next time, which is worse than
            # not remembering at all. Guarded again, because this is a second
            # write to the dict another thread may be reading.
            try:
                if previous is None:
                    storage.discard("cookbook_refresh_ok")
                else:
                    storage.update(cookbook_refresh_ok=previous)
            except Exception:
                pass
            self.catalog_note.setText(f"That could not be saved: {error}")

    def do_refresh(self):
        """The one control that leaves the machine, and only when pressed.

        Deliberately not wired to a timer, an open, or a hover. The page makes
        no request on its own, so the Resources page's promise that nothing is
        sent anywhere stays true of this page too.

        One press re-reads the whole catalogue, not one model from it. It used
        to refresh ``catalog[0]``, which meant the list could not update as a
        list: the other models kept figures as old as the install, and the one
        that moved was whichever happened to be first. The requests run one at
        a time rather than together, because a burst of four from one button
        press is not something the person pressing it agreed to.

        Each record goes through the same validation the bundled catalogue
        goes through, so a response cannot smuggle a field past the checks by
        arriving over the network instead of from disk. One model being
        refused or unreachable does not stop the rest, and the note says which
        ones those were rather than reporting the press as a single success or
        a single failure.

        The result is saved once at the end, so the catalogue on disk is
        never a half-finished mixture of old and new records, and the one it
        replaces is kept so it can be put back.
        """
        if self._busy:
            return
        targets = list(self.catalog)
        if not targets:
            self.catalog_note.setText(
                "There is nothing to refresh: no models have been loaded.")
            return

        before = list(self.catalog)
        updated = {}
        refused = {}
        self._busy = True
        self._cancelled = False
        # targets is passed down rather than re-read from self.catalog at each
        # step. The step used to index the live list, and the Restore button
        # stayed enabled throughout a refresh, so a restore mid-refresh could
        # shorten the catalogue and leave the step out of range -- at which
        # point it fell back to a hard-coded Qwen repo and fetched, attributed
        # and merged a model nobody asked for.
        self._set_busy(True)
        self._refresh_step(0, targets, updated, refused, before)

    def _refresh_step(self, index, targets, updated, refused, before):
        """Re-read one model, then either finish or ask for the next.

        Sequential rather than concurrent, so the host sees one request at a
        time from a button press, and so the progress on the button means
        something: "2 of 4" is a fact about work already done.

        With no fallback branch, because there is no longer any way to reach
        this line without a model: targets is fixed when the press began, so
        index < len(targets) always holds.
        """
        total = len(targets)
        if self._cancelled:
            self._finish_refresh(index, updated, refused, before, stopped=True)
            return
        if index >= total:
            self._finish_refresh(total, updated, refused, before)
            return

        model = targets[index]
        repo = model.repo
        artifacts = model.artifact_repo

        def landed(result):
            try:
                record = parse_model(carry_over(model, result), 0)
            except CatalogError as error:
                refused[repo] = (f"{self.host_name()} sent something unusable: "
                                 f"{error}")
            else:
                updated[record.repo] = record
            self._refresh_step(index + 1, targets, updated, refused, before)

        def missed(reason):
            refused[repo] = reason
            self._refresh_step(index + 1, targets, updated, refused, before)

        self.refresh.setText(f"Refreshing {index + 1} of {total} from "
                             f"{self.host_name()}.")
        self.window.command("cookbook_refresh", repo, artifacts, self.base,
                            success=landed, failure=missed)

    def _finish_refresh(self, total, updated, refused, before, stopped=False):
        """Merge what came back, say what did not, and save it once.

        ``stopped`` means the reader pressed Stop, not that the host refused.
        Whatever had already arrived is kept and saved, because discarding a
        successful read because the reader changed their mind about the rest
        would throw away work that cost a request to obtain.
        """
        self._busy = False
        self._set_busy(False)

        if not updated:
            if stopped:
                self.catalog_note.setText("Refresh stopped before anything came back.")
                self.refresh_controls()
                return
            first = next(iter(refused.values()), "the request was refused")
            self.catalog_note.setText(
                f"{self.host_name()} could not be read: {first}")
            self.refresh_controls()
            return

        merged = [updated.get(model.repo, model) for model in before]
        # Anything that arrived that is not already in the catalogue is a
        # response naming a model this app did not ask about, which is not a
        # refresh result and is not merged in.
        note = describe_changes(before, merged)
        read = ", ".join(sorted(record.display for record in updated.values()))
        saved = ""
        if self.directory:
            newest = max((record.last_verified for record in updated.values()),
                         default="")
            try:
                kept = save_catalog(merged, self.directory, newest)
                saved = " The previous one is kept and can be restored." if kept else ""
            except (OSError, CatalogError) as error:
                # Shown, not raised: the models are already correct on
                # screen and losing them because they could not be written
                # down would be a worse outcome than a catalogue that has
                # to be read again next launch.
                saved = f" It could not be saved: {error}"

        self.catalog = merged
        self.catalog_note.setText(self._refresh_note(total, updated, refused,
                                                     read, note, saved, stopped))
        self.refresh_controls()
        self.render_models()

    def _refresh_note(self, total, updated, refused, read, note, saved, stopped=False):
        """One sentence saying what this press actually achieved.

        A partial refresh that reported plain success would be the wrong claim
        in the one case where it matters most -- when the host answered for
        some models and not others, and the reader would otherwise assume the
        whole list is current.
        """
        if stopped:
            return (f"Stopped after {len(updated)} of {total}. Read {read} from "
                    f"{self.host_name()}. {note}{saved}")
        if not refused:
            return (f"Read {read} from {self.host_name()}. {note}{saved}")
        problems = "; ".join(f"{repo}: {reason}" for repo, reason
                             in sorted(refused.items()))
        return (f"Read {read} from {self.host_name()}, but {len(refused)} of "
                f"{total} could not be: {problems}. {note}{saved}")

    def do_restore(self):
        """Put back the catalogue from before the last refresh."""
        if not self.directory:
            return
        restored = rollback_catalog(self.directory)
        if restored is None:
            self.catalog_note.setText(
                "There is no earlier catalogue to go back to.")
            self.refresh_controls()
            return
        self.catalog = restored
        self.catalog_note.setText(
            f"Restored the catalogue from before your last refresh, with "
            f"{len(restored)} models.")
        self.refresh_controls()
        self.render_models()