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
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QVBoxLayout, QWidget

from .cookbook import (PREVIOUS_NAME, CatalogError, TASKS, describe_changes,
                       detect_hardware, human_bytes, kv_cache_bytes,
                       load_cache, load_catalog, ollama_commands, parse_model,
                       recommend, rollback_catalog, save_catalog)
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
    "likely to run": "online",
    "verified compatible": "online",
    "possible with compromises": "muted",
    "not recommended": "errorMessage",
    "unknown": "muted",
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
            "Sized against this device's memory. Nothing is sent anywhere "
            "unless you press Refresh.", "muted", True))

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

        self.refresh = action(f"Refresh from {self.host_name()}", self.do_refresh, name="ghost")
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

    def _card(self):
        from .window import frame
        return frame("card")

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
            # Detached before being scheduled for deletion, not just deleted.
            # deleteLater() alone leaves the card parented to the panel until
            # the event loop next runs, so the previous page's widgets are
            # still findable, still painted over, and still holding memory --
            # which is how a list grows on every page turn.
            widget.setParent(None)
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
            f"Page {self._page + 1} of {pages} Â· {total} models")
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
            f"{model.size_class} Â· {model.license or 'licence unstated'} Â· "
            f"read {model.last_verified or 'unknown'}", "muted", True))
        return card

    def _empty_card(self):
        # Nothing at all to show. Which sentence is true depends on why, and
        # "nothing fits this device" over a catalogue that has withdrawn
        # everything would be a confident falsehood about somebody's
        # hardware.
        card, column = self._card()
        column.addWidget(label(
            "Nothing in the catalogue fits this device." if self.catalog
            else "No models are available to check.", "heading"))
        column.addWidget(label(
            "Try a smaller build, free some memory, or refresh the "
            "catalogue if a newer one is out." if self.catalog
            else "The catalogue could not be loaded.", "muted", True))
        self.results.addWidget(card)

    def _model_card(self, row):
        model, variant, verdict = row["model"], row["variant"], row["verdict"]
        card, column = self._card()
        column.setContentsMargins(20, 17, 20, 17)

        top = QHBoxLayout()
        top.addWidget(label(model.display, "heading", True))
        badge = label(verdict["result"], TONE.get(verdict["result"], "muted"))
        badge.setAccessibleName(f"Assessment: {verdict['result']}")
        top.addWidget(badge)
        top.addStretch()
        column.addLayout(top)

        facts = (f"{model.size_class} Â· {human_bytes(variant.size)} download "
                 f"Â· {variant.quantization} Â· {model.license or 'licence unstated'}")
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
            for entry in commands:
                column.addWidget(label(entry["command"], "muted", True))

        provenance = label(
            f"From {model.repo} on {model.source} Â· builds by "
            f"{model.artifact_repo} Â· read {model.last_verified or 'unknown'}",
            "muted", True)
        provenance.setWordWrap(True)
        column.addWidget(provenance)

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