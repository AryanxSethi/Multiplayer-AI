"""Local downloads and running models for the native Cookbook page."""

import re

from PySide6.QtWidgets import QFrame, QHBoxLayout, QProgressBar, QVBoxLayout, QWidget

from .cookbook import human_bytes
from .cookbook_runtime import ACTIVE, LOCAL_OLLAMA
from .ollama_service import model_installed
from .widgets import action, label


class LocalModels(QFrame):
    def __init__(self, window, context=None):
        super().__init__()
        self.window = window
        self.context = context or (lambda: 4096)
        self.state = getattr(window, "cookbook_state", None) or {
            "installed": [], "running": [], "jobs": [], "error": ""}
        self.setObjectName("card")
        column = QVBoxLayout(self)
        column.setContentsMargins(20, 17, 20, 17)
        header = QHBoxLayout()
        header.addWidget(label("Local models", "heading"), 1)
        self.refresh = action("Refresh local models", self.refresh_status, name="ghost")
        header.addWidget(self.refresh)
        column.addLayout(header)
        column.addWidget(label(
            "Download a build, run it with Ollama, then use it in chat. "
            "Downloads stay on this device and continue while you switch pages or workspaces.",
            "muted", True))
        self.message = label("Press Refresh local models to check Ollama on this device.", "muted", True)
        column.addWidget(self.message)
        self.rows = QVBoxLayout()
        column.addLayout(self.rows)
        self._job_widgets = {}
        self.render()
        if getattr(window, "cookbook_state", None):
            self.update_state(self.state)

    def refresh_status(self):
        self.refresh.setEnabled(False)

        def done(state):
            self.refresh.setEnabled(True)
            self.update_state(state)

        def failed(message):
            self.refresh.setEnabled(True)
            self.message.setText(message)

        self.window.command("cookbook_status", success=done, failure=failed)

    def update_state(self, state):
        # Progress updates change labels in place, preserving focus and scroll.
        previous = self.state
        self.state = state
        self.window.cookbook_state = state
        def structure(state):
            return (state.get("installed"), state.get("running"),
                    [(j["name"], j["state"]) for j in state.get("jobs", [])])
        if structure(previous) != structure(state):
            self.render()
        else:
            self._update_progress()
        if state.get("error"):
            self.message.setText(state["error"])
        elif getattr(self.window, "cookbook_state", None):
            self.message.setText(f"{len(state['installed'])} installed · {len(state['running'])} running · Ollama on this device")

    def render(self):
        while self.rows.count():
            item = self.rows.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()
        self._job_widgets = {}
        running = {m["name"] for m in self.state.get("running", [])}
        jobs = {j["name"]: j for j in self.state.get("jobs", [])}
        for model in self.state.get("installed", []):
            name = model["name"]
            row = QWidget()
            row.setObjectName("cookbookControlsRow")
            layout = QVBoxLayout(row)
            layout.setContentsMargins(0, 6, 0, 6)
            layout.addWidget(label(name, "meta", True))
            details = f"{human_bytes(model.get('size', 0))} on disk"
            if name in running:
                loaded = next(m for m in self.state["running"] if m["name"] == name)
                details += f" · Running · {human_bytes(loaded.get('size_vram', 0))} on GPU"
            layout.addWidget(label(details, "muted", True))
            buttons = QHBoxLayout()
            operation = "stop" if name in running else "run"
            button = action("Stop" if operation == "stop" else "Run",
                            lambda checked=False, n=name, op=operation: self.start(n, op), name="ghost")
            button.setEnabled(jobs.get(name, {}).get("state") not in ACTIVE)
            buttons.addWidget(button)
            buttons.addWidget(action("Use in chat", lambda checked=False, n=name: self.use_in_chat(n), name="ghost"))
            buttons.addStretch()
            layout.addLayout(buttons)
            self.rows.addWidget(row)
        for job in self.state.get("jobs", []):
            row = QWidget()
            row.setObjectName("cookbookControlsRow")
            layout = QVBoxLayout(row)
            layout.setContentsMargins(0, 6, 0, 6)
            text = label("", "muted", True)
            layout.addWidget(text)
            bar = QProgressBar()
            bar.setTextVisible(False)
            bar.setAccessibleName(f"Download progress for {job['name']}")
            layout.addWidget(bar)
            if job["operation"] == "download" and job["state"] in ACTIVE:
                layout.addWidget(action("Cancel download",
                    lambda checked=False, n=job["name"]: self.cancel(n), name="ghost"))
            elif job["state"] in {"failed", "cancelled"}:
                layout.addWidget(action("Retry",
                    lambda checked=False, n=job["name"], op=job["operation"]: self.start(n, op), name="ghost"))
            self._job_widgets[job["name"]] = (text, bar)
            self.rows.addWidget(row)
        self._update_progress()

    def _update_progress(self):
        for job in self.state.get("jobs", []):
            text, bar = self._job_widgets[job["name"]]
            total = job.get("total", 0)
            suffix = (f" · {human_bytes(job.get('completed', 0))} / {human_bytes(total)}"
                      if total else "")
            text.setText(f"{job['name']} · {job['message']}{suffix}")
            bar.setVisible(job["state"] in ACTIVE and job["operation"] == "download")
            bar.setRange(0, 100 if total else 0)
            if total:
                bar.setValue(min(100, int(job.get("completed", 0) * 100 / total)))

    def start(self, name, operation="download", context=None):
        self.window.command("cookbook_start", name, operation,
                            self.context() if context is None else context,
                            success=self.update_state, failure=self.message.setText)

    def cancel(self, name):
        self.window.command("cookbook_cancel", name,
                            success=self.update_state, failure=self.message.setText)

    def use_in_chat(self, name, vision=False):
        if not self.window.ready:
            self.message.setText("Your network is still starting. Please wait a moment.")
            return
        self.window.add_agent("ollama")
        self.window.model_base.setText(LOCAL_OLLAMA)
        self.window.model_id.setCurrentText(name)
        if not self.window.remote:
            identity = re.sub(r"[^A-Za-z0-9_-]", "-", name.rsplit("/", 1)[-1])[:54]
            existing = {a["id"] for a in self.window.agents}
            candidate, suffix = identity, 2
            while candidate in existing or candidate == self.window.identity:
                candidate = f"{identity}-{suffix}"
                suffix += 1
            self.window.model_name.setText(candidate)
        self.window.model_vision.setChecked(vision)

    def card_actions(self, name, vision, context):
        row = QWidget()
        row.setObjectName("cookbookControlsRow")
        buttons = QHBoxLayout(row)
        buttons.setContentsMargins(0, 0, 0, 0)
        download = action("Download", lambda: self.start(name(), "download", context()), primary=True)
        download.setToolTip("Download this build with Ollama. Ollama contacts Hugging Face for the model files.")
        run = action("Run", lambda: self.start(name(), "run", context()), name="ghost")
        chat = action("Use in chat", lambda: self.use_in_chat(name(), vision), name="ghost")
        for button in (download, run, chat):
            buttons.addWidget(button)
        buttons.addStretch()
        self.update_card_actions((download, run, chat), name())
        return row, (download, run, chat)

    def update_card_actions(self, buttons, name):
        installed = bool(name) and model_installed(name, [m["name"] for m in self.state.get("installed", [])])
        busy = any(j["name"] == name and j["state"] in ACTIVE for j in self.state.get("jobs", []))
        download, run, chat = buttons
        download.setText("Downloaded" if installed else "Download")
        download.setEnabled(bool(name) and not installed and not busy)
        run.setEnabled(installed and not busy)
        chat.setEnabled(installed)
