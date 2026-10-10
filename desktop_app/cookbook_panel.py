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

from .cookbook import (CatalogError, TASKS, detect_hardware, human_bytes,
                       kv_cache_bytes, load_catalog, ollama_commands,
                       parse_model, recommend)
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
        self.setAccessibleName("Cookbook: models this device can run")

        page, layout = scroll_page()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(page)

        outer.addWidget(label("WHICH MODELS FIT THIS MACHINE", "eyebrow"))
        outer.addWidget(label("Cookbook", "title"))
        outer.addWidget(label(
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
        outer.addWidget(card)

        self.catalog_note = label("", "muted", True)
        self.catalog_note.setWordWrap(True)
        outer.addWidget(self.catalog_note)

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
        outer.addLayout(filters)

        self.results = QVBoxLayout()
        self.results.setSpacing(12)
        self.body = QWidget()
        self.body.setLayout(self.results)
        outer.addWidget(self.body)

        self.refresh = action("Refresh from Hugging Face", self.do_refresh, name="ghost")
        self.refresh.setToolTip(
            "Re-read model details from huggingface.co. Sends your network "
            "address and the model names being shown. Nothing is requested "
            "until you press this.")
        footer = QHBoxLayout()
        footer.addWidget(self.refresh)
        footer.addStretch()
        # A Close box on a page is a lie: there is nothing to close. It was
        # there because this was a dialog, and it was the clearest sign the
        # widget had stopped being one. Leaving the page is the rail's job.
        outer.addLayout(footer)

        self.load()

    def _card(self):
        from .window import frame
        return frame("card")

    def load(self):
        """Read the catalogue that ships with the app. Never touches the network."""
        try:
            self.catalog = load_catalog()
        except CatalogError as error:
            self.catalog = []
            self.catalog_note.setText(f"The bundled catalogue could not be read: {error}")
        else:
            self.catalog_note.setText("")
        self.render_models()

    def render_models(self):
        """Draw the recommendations for the chosen task."""
        while self.results.count():
            item = self.results.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        task = self.task_picker.currentData()
        rows = recommend(self.catalog, self.hardware, task=task) if self.catalog else []
        if not rows:
            card, column = self._card()
            column.addWidget(label(
                "Nothing in the catalogue fits this device." if self.catalog
                else "No models are available to check.", "heading"))
            column.addWidget(label(
                "Try a smaller build, free some memory, or refresh the "
                "catalogue if a newer one is out." if self.catalog
                else "The catalogue could not be loaded.", "muted", True))
            self.results.addWidget(card)
            return

        for row in rows:
            self.results.addWidget(self._model_card(row))
        self.results.addStretch()

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
        """
        if self._busy:
            return
        self._busy = True
        self.refresh.setEnabled(False)
        self.refresh.setText("Asking Hugging Face.")
        first = self.catalog[0] if self.catalog else None
        model = first.repo if first else "Qwen/Qwen2.5-0.5B-Instruct"
        artifacts = first.artifact_repo if first else "bartowski/Qwen2.5-0.5B-Instruct-GGUF"

        def done(result):
            self._busy = False
            self.refresh.setEnabled(True)
            self.refresh.setText("Refresh from Hugging Face")
            # Checked by the same parser the bundled catalogue goes through,
            # so a response cannot smuggle a field past validation by arriving
            # from the network instead of from disk.
            try:
                record = parse_model(result, 0)
            except CatalogError as error:
                self.catalog_note.setText(f"Hugging Face sent something unusable: {error}")
                return
            self.catalog = [record] + [m for m in self.catalog if m.repo != record.repo]
            self.catalog_note.setText(
                f"Read {record.display} from huggingface.co at "
                f"{record.last_verified}. Shown for this session, not saved.")
            self.render_models()

        def failed(reason):
            self._busy = False
            self.refresh.setEnabled(True)
            self.refresh.setText("Refresh from Hugging Face")
            self.catalog_note.setText(f"Hugging Face could not be read: {reason}")

        self.window.command("cookbook_refresh", model, artifacts,
                            success=done, failure=failed)