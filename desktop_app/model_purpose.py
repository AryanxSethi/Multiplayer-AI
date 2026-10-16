"""Shared model-purpose controls for the connection page and legacy dialog."""
from PySide6.QtWidgets import QGridLayout, QPlainTextEdit, QVBoxLayout, QWidget

from network_a2a.orchestration import TASK_TYPES

from .widgets import TickCheckBox, label


class ModelPurpose(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(label("Specialist routing (optional)", "heading"))
        self.purpose = QPlainTextEdit()
        self.purpose.setMaximumHeight(80)
        self.purpose.setPlaceholderText("Describe its strengths and your preferences, e.g. Python coding and debugging; avoid creative writing.")
        layout.addWidget(self.purpose)
        layout.addWidget(label("Permitted tasks", "muted"))
        grid = QGridLayout()
        self.tasks = {}
        self._legacy_general = False
        tasks = [(key, title) for key, title in TASK_TYPES.items() if key != "general"]
        for index, (key, title) in enumerate(tasks):
            checkbox = TickCheckBox(title)
            self.tasks[key] = checkbox
            grid.addWidget(checkbox, index // 2, index % 2)
        layout.addLayout(grid)
        self.enabled = TickCheckBox("Allow automatic delegation to this model")
        layout.addWidget(self.enabled)
        layout.addWidget(label("The first imported model is your Head. You can also assign roles in chat, e.g. /set @agent as Coder. Type /help for commands. These preferences are shared with your workspace.", "muted", True))

    def values(self):
        return {"purpose": self.purpose.toPlainText().strip(),
                "tasks": (["general"] if self._legacy_general else []) +
                         [key for key, checkbox in self.tasks.items() if checkbox.isChecked()],
                "delegation_enabled": self.enabled.isChecked()}

    def load(self, profile):
        # Preserve older permissions on edit without offering General chat as a task.
        self._legacy_general = "general" in profile.get("tasks", [])
        self.purpose.setPlainText(profile.get("purpose", ""))
        for key, checkbox in self.tasks.items():
            checkbox.setChecked(key in profile.get("tasks", []))
        self.enabled.setChecked(profile.get("delegation_enabled", False))
