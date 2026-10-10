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

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from .cookbook import (PREVIOUS_NAME, CatalogError, TASKS, describe_changes,
                       detect_hardware, human_bytes, kv_cache_bytes,
                       load_cache, load_catalog, ollama_commands, parse_model,
                       recommend, rollback_catalog, save_catalog)
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


class CookbookPanel(QWidget):
    """A list of models sized against the machine they are being read on."""

    def __init__(self, window, scroll_page):
        super().__init__()
        self.window = window
        self._busy = False
        # Where a cached catalogue lives, and the host a refresh reads from.
        # Both are per workspace: two workspaces can be looking at different
        # catalogues, and a shared cache would have them overwrite each other.
        self.directory = getattr(window.storage, "directory", None)
        self.base = window.storage.settings.get("cookbook_source") or None
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
            ("Not detected here: " + ", ".join(sorted(unknown)) + ". Graphics "
             "memory needs a library this app does not have, so a machine with "
             "a card is judged on its system memory instead.") if unknown else "",
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

        self.refresh = action(f"Refresh from {self.host_name()}", self.do_refresh, name="ghost")
        self.refresh.setToolTip(
            f"Re-read model details from {self.host_name()}. Sends your network "
            "address and the model names being shown. Nothing is requested "
            "until you press this.")
        self.restore = action("Restore previous catalogue", self.do_restore, name="ghost")
        self.restore.setToolTip(
            "Put back the catalogue that was in use before the last refresh. "
            "Useful when a refresh brought in something that does not fit.")
        footer = QHBoxLayout()
        footer.addWidget(self.refresh)
        footer.addWidget(self.restore)
        footer.addStretch()
        # A Close box on a page is a lie: there is nothing to close. It was
        # there because this was a dialog, and it was the clearest sign the
        # widget had stopped being what it claimed. Leaving the page is the
        # rail's job.
        layout.addLayout(footer)

        self.load()

    def host_name(self):
        """Where a refresh reads from, as a person would name it."""
        return self.base or "huggingface.co"

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
        """Draw the recommendations for the chosen task.

        Withdrawn models are listed after the recommended ones rather than
        filtered out, so somebody who already has one can find out why nothing
        recommends it. They are not ranked with the rest: a withdrawn model
        cannot be recommended, and putting it among the ones that can would be
        an argument for downloading it.
        """
        while self.results.count():
            item = self.results.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        task = self.task_picker.currentData()
        offered = recommend(self.catalog, self.hardware, task=task) if self.catalog else []
        withdrawn = [model for model in self.catalog if model.deprecated
                     and (not task or task in model.tasks)]
        for row in offered:
            self.results.addWidget(self._model_card(row))
        if withdrawn:
            if offered:
                self.results.addSpacing(6)
                column = self._card()
                column.addWidget(label("Withdrawn by their publishers", "heading"))
                column.addWidget(label(
                    "Still listed so you can tell what happened to one you have. "
                    "They are not recommended for download.", "muted", True))
                self.results.addWidget(column)
            for model in withdrawn:
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
                self.results.addWidget(card)
        if offered or withdrawn:
            self.results.addStretch()
            return

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
            for entry in commands:
                column.addWidget(label(entry["command"], "muted", True))

        provenance = label(
            f"From {model.repo} on {model.source} · builds by "
            f"{model.artifact_repo} · read {model.last_verified or 'unknown'}",
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

        A successful refresh is saved, so it survives a restart, and the
        catalogue it replaces is kept so it can be put back. A record that
        fails the same validation the bundled one goes through is refused
        before anything is written, so a response cannot smuggle a field past
        the checks by arriving over the network instead of from disk.
        """
        if self._busy:
            return
        self._busy = True
        self.refresh.setEnabled(False)
        label = f"Asking {self.host_name()}."
        self.refresh.setText(label)
        first = self.catalog[0] if self.catalog else None
        model = first.repo if first else "Qwen/Qwen2.5-0.5B-Instruct"
        artifacts = first.artifact_repo if first else "bartowski/Qwen2.5-0.5B-Instruct-GGUF"
        before = list(self.catalog)

        def done(result):
            self._busy = False
            self.refresh.setEnabled(True)
            self.refresh.setText(f"Refresh from {self.host_name()}")
            try:
                record = parse_model(result, 0)
            except CatalogError as error:
                self.catalog_note.setText(f"{self.host_name()} sent something unusable: {error}")
                return
            updated = [record] + [m for m in self.catalog if m.repo != record.repo]
            note = describe_changes(before, updated)
            saved = ""
            if self.directory:
                try:
                    kept = save_catalog(updated, self.directory, record.last_verified)
                    saved = " The previous one is kept and can be restored." if kept else ""
                except (OSError, CatalogError) as error:
                    # Shown, not raised: the models are already correct on
                    # screen and losing them because they could not be written
                    # down would be a worse outcome than a catalogue that has
                    # to be read again next launch.
                    saved = f" It could not be saved: {error}"
            self.catalog = updated
            self.catalog_note.setText(
                f"Read {record.display} from {self.host_name()} at "
                f"{record.last_verified}. {note}{saved}")
            self.refresh_controls()
            self.render_models()

        def failed(reason):
            self._busy = False
            self.refresh.setEnabled(True)
            self.refresh.setText(f"Refresh from {self.host_name()}")
            self.catalog_note.setText(f"{self.host_name()} could not be read: {reason}")

        self.window.command("cookbook_refresh", model, artifacts, self.base,
                            success=done, failure=failed)

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