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

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLineEdit, QVBoxLayout,
                             QWidget)

from .cookbook import (EXPLANATIONS, FITTING, LIKELY, NOT_ENOUGH_PUBLISHED,
                       NO_CATALOGUE, POSSIBLE, PREVIOUS_NAME, VERIFIED,
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


class CookbookPanel(QWidget):
    """A list of models sized against the machine they are being read on."""

    def __init__(self, window, scroll_page):
        super().__init__()
        self.window = window
        self._busy = False
        self._page = 0
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
        layout.addWidget(label(
            "Sized against this device's memory. Pressing Refresh asks first, "
            "and says where it is about to read from.", "muted", True))

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
        self.unknown_label.setVisible(bool(unknown))
        column.addWidget(self.unknown_label)
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
        self.task_picker.currentIndexChanged.connect(self.render_models)
        filters.addWidget(self.task_picker, 1)
        layout.addLayout(filters)

        self.results = QVBoxLayout()
        self.results.setSpacing(12)
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
        footer = QHBoxLayout()
        footer.addWidget(self.refresh)
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

        self.load()

    def showEvent(self, event):
        """Re-read the mirror each time the page is shown.

        A setting changed while this page was not on screen should not leave it
        describing a host it has stopped using -- the box, the button and the
        disclosure all name where the next request will go, and a stale name on
        those is the page making a claim it cannot keep.

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
        self.window.storage.settings["cookbook_source"] = None if not typed else host
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
        return frame(name)

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
        """Draw one page of cards, and the controls that move between them."""
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
        for kind, entry in window:
            if kind == "model":
                self.results.addWidget(self._model_card(entry))
                continue
            if not withdrawn_header_drawn:
                withdrawn_header_drawn = True
                if start:
                    # Only separated from the recommended models when it is not
                    # already the top of the page.
                    self.results.addSpacing(6)
                column = self._card()
                column.addWidget(label("Withdrawn by their publishers", "heading"))
                column.addWidget(label(
                    "Still listed so you can tell what happened to one you have. "
                    "They are not recommended for download.", "muted", True))
                self.results.addWidget(column)
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
        elif reason.kind in (NO_CATALOGUE, NOT_ENOUGH_PUBLISHED):
            column.addSpacing(4)
            column.addWidget(action(
                f"Refresh from {self.host_name()}", self.do_refresh,
                name="ghost"))
        self.results.addWidget(card)

    def show_everything(self):
        """Put the filter back to Everything, which is where models are.

        Only the picker's own signal does the rendering. Calling render_models
        as well drew the whole page twice -- setCurrentIndex fires
        currentIndexChanged, which is already connected to it -- so every card
        was torn down and rebuilt twice over, which is where the stray windows
        were coming from. It also means the button this was pressed from is
        inside the first of those teardowns, so the click destroyed the control
        that was handling it.
        """
        index = self.task_picker.findData(None)
        if index < 0:
            return
        if index == self.task_picker.currentIndex():
            # Already on it, so no signal is coming.
            self.render_models()
        else:
            self.task_picker.setCurrentIndex(index)

    def _other_builds(self, column, row):
        """Every build of this model except the recommended one, behind a toggle.

        Thirty-one builds sat behind four cards with nothing on screen that
        reached the other twenty-seven. That is not a cosmetic gap: a machine
        with a 4GiB card was shown the 5.1GB four-bit build of a 7B model at
        "possible with compromises" while the 3.6GB two-bit build in the same
        catalogue would have fitted outright, and the only way to find that out
        was to read the source.

        Collapsed by default so the page stays comparable model to model, which
        is what somebody scanning a list of what fits is actually doing. The
        list is smallest first, so it reads as a ladder of trade-offs rather
        than a scramble, and each row carries its own command -- an alternative
        build is no use if the reader has to work out its repository tag.

        The state is not remembered across a re-render. A refresh or a filter
        change replaces the catalogue underneath, and reopening a section that
        was expanded against a different set of builds would be showing
        something that is no longer true.
        """
        builds = [entry for entry in row.get("builds") or []
                  if not entry["selected"]]
        if not builds:
            return

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
        summary = [f"{len(builds)} other builds"]
        if on_card:
            summary.append(f"{on_card} run on the graphics card")
        if spilling:
            summary.append(f"{spilling} spill to the CPU")
        detail_text = " · ".join(summary[1:]) or "all of them fit system memory"
        toggle = action(summary[0], None, name="ghost")
        toggle.setToolTip(f"{summary[0]} — {detail_text}")
        toggle.setAccessibleName(
            f"Show the other {len(builds)} builds of {row['model'].display}. "
            f"{detail_text}.")
        toggle.setAccessibleName(
            f"Show the other {len(builds)} builds of {row['model'].display}, "
            "with their size and what this device makes of each")
        toggle.setCheckable(True)
        toggle.setChecked(False)
        column.addWidget(toggle, 0, Qt.AlignmentFlag.AlignLeft)

        detail = QWidget()
        listing = QVBoxLayout(detail)
        listing.setContentsMargins(0, 6, 0, 0)
        listing.setSpacing(8)
        note = label(detail_text, "muted", True)
        note.setWordWrap(True)
        listing.addWidget(note)
        for entry in builds:
            listing.addWidget(self._build_row(row["model"], entry))
        detail.setVisible(False)
        toggle.toggled.connect(detail.setVisible)
        column.addWidget(detail)

    def _build_row(self, model, entry):
        """One alternative build: its size, its verdict, and how to run it."""
        variant, verdict = entry["variant"], entry["verdict"]
        total = (entry["figures"] or {}).get("total")

        block = QWidget()
        block.setObjectName("buildRow")
        line = QVBoxLayout(block)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(4)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(10)

        name = label(variant.quantization or variant.name, "heading")
        head.addWidget(name)

        size = f"{human_bytes(variant.size)} download"
        if total and total != variant.size:
            size += f" · {human_bytes(total)} in memory"
        measured = label(size, "muted", True)
        head.addWidget(measured, 1)

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
        verdict_label.setAccessibleName(
            f"{variant.quantization}, {size}, {verdict['result']}, "
            f"{verdict['reason']}" + (f". {gloss}" if gloss else ""))
        head.addWidget(verdict_label)
        # The whole reason is on the row too, so a verdict cannot be read
        # without the number that produced it.
        block.setToolTip(verdict["reason"])
        line.addLayout(head)

        commands = ollama_commands(model, variant)
        if commands:
            command = label(commands[0]["command"], "command", True)
            command.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
                | Qt.TextInteractionFlag.TextSelectableByKeyboard)
            command.setAccessibleName(
                f"Command to run the {variant.quantization} build")
            line.addWidget(command)
        return block

    def _model_card(self, row):
        model, variant, verdict = row["model"], row["variant"], row["verdict"]
        card, column = self._card(CARD[verdict["result"]])
        column.setContentsMargins(20, 17, 20, 17)

        column.addWidget(label(model.display, "heading", True))
        # The verdict sits under the name as its own line rather than floating
        # at the far right of the heading row. Beside a 19px heading it read as
        # a second title, and "possible with compromises" is twenty-four
        # characters of prose competing with the model's name for attention.
        # The card's own left border carries the signal now; this says which.
        badge = label(verdict["result"], TONE.get(verdict["result"], "muted"))
        gloss = EXPLANATIONS.get(verdict["result"])
        if gloss:
            # Written out in EXPLANATIONS and never rendered until now: the
            # verdict is an internal phrase, and a reader who cannot see the
            # colour has nothing to go on.
            badge.setToolTip(gloss)
            badge.setAccessibleName(f"{verdict['result']}: {gloss}")
        else:
            badge.setAccessibleName(f"Assessment: {verdict['result']}")
        column.addWidget(badge)

        facts = (f"{model.size_class} · {human_bytes(variant.size)} download "
                 f"· {variant.quantization} · {model.license or 'licence unstated'}")
        column.addWidget(label(facts, "muted", True))
        reason = label(verdict["reason"], "muted", True)
        reason.setWordWrap(True)
        column.addWidget(reason)

        longer = self._longer_context_warning(model)
        if longer:
            warning = label(longer, "muted", True)
            warning.setWordWrap(True)
            column.addWidget(warning)

        commands = ollama_commands(model, variant)
        if commands:
            column.addSpacing(6)
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
            column.addWidget(block)

        provenance = label(
            f"From {model.repo} on {model.source} · builds by "
            f"{model.artifact_repo} · read {model.last_verified or 'unknown'}",
            "muted", True)
        provenance.setWordWrap(True)
        column.addWidget(provenance)

        self._other_builds(column, row)

        disclaimer = label(
            "Sized from published figures, not measured on this device.", "muted", True)
        disclaimer.setWordWrap(True)
        column.addWidget(disclaimer)
        return card

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
        settings = self.window.storage.settings
        previous = settings.get("cookbook_refresh_ok")
        agreed = dict(previous) if isinstance(previous, dict) else {}
        agreed[host] = True
        settings["cookbook_refresh_ok"] = agreed
        try:
            self.window.storage.save()
        except Exception as error:
            # Put it back rather than leaving a remembered agreement that did
            # not survive a restart: the reader would not be warned again this
            # session and would be warned again next time, which is worse than
            # not remembering at all.
            if previous is None:
                settings.pop("cookbook_refresh_ok", None)
            else:
                settings["cookbook_refresh_ok"] = previous
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
        self.refresh.setEnabled(False)
        self._refresh_step(0, len(targets), updated, refused, before)

    def _refresh_step(self, index, total, updated, refused, before):
        """Re-read one model, then either finish or ask for the next.

        Sequential rather than concurrent, so the host sees one request at a
        time from a button press, and so the progress on the button means
        something: "2 of 4" is a fact about work already done.
        """
        if index >= total:
            self._finish_refresh(total, updated, refused, before)
            return

        model = self.catalog[index] if index < len(self.catalog) else None
        repo = model.repo if model else "Qwen/Qwen2.5-0.5B-Instruct"
        artifacts = (model.artifact_repo if model
                     else "bartowski/Qwen2.5-0.5B-Instruct-GGUF")

        def landed(result):
            try:
                record = parse_model(result, 0)
            except CatalogError as error:
                refused[repo] = (f"{self.host_name()} sent something unusable: "
                                 f"{error}")
            else:
                updated[record.repo] = record
            self._refresh_step(index + 1, total, updated, refused, before)

        def missed(reason):
            refused[repo] = reason
            self._refresh_step(index + 1, total, updated, refused, before)

        self.refresh.setText(f"Refreshing {index + 1} of {total} from "
                             f"{self.host_name()}.")
        self.window.command("cookbook_refresh", repo, artifacts, self.base,
                            success=landed, failure=missed)

    def _finish_refresh(self, total, updated, refused, before):
        """Merge what came back, say what did not, and save it once."""
        self._busy = False
        self.refresh.setEnabled(True)
        self.refresh.setText(f"Refresh from {self.host_name()}")

        if not updated:
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
                                                     read, note, saved))
        self.refresh_controls()
        self.render_models()

    def _refresh_note(self, total, updated, refused, read, note, saved):
        """One sentence saying what this press actually achieved.

        A partial refresh that reported plain success would be the wrong claim
        in the one case where it matters most -- when the host answered for
        some models and not others, and the reader would otherwise assume the
        whole list is current.
        """
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