"""Workspace names, invitations, membership, and deletion controls."""
import json

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QHeaderView, QLineEdit,
    QMessageBox, QTableWidget, QTableWidgetItem, QVBoxLayout)

from .dialogs import InviteDialog
from .widgets import action, label


class WorkspaceDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.setWindowTitle("Workspace settings")
        self.setMinimumSize(590, 470)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 24, 26, 24)
        layout.setSpacing(14)
        layout.addWidget(label("WORKSPACE SETTINGS", "eyebrow"))
        layout.addWidget(label("Your workspace", "title"))
        self.name = QLineEdit(window.workspace_label.text())
        self.name.setMaxLength(80)
        self.name.setReadOnly(window.remote)
        name_row = QHBoxLayout()
        name_row.addWidget(self.name, 1)
        self.rename_button = action("Save name", self.rename, True)
        self.rename_button.setEnabled(not window.remote)
        name_row.addWidget(self.rename_button)
        layout.addLayout(name_row)
        layout.addWidget(label("Members", "heading"))
        self.members = QTableWidget(0, 4)
        self.members.setHorizontalHeaderLabels(("Device or agent", "Role", "Status", ""))
        self.members.verticalHeader().hide()
        self.members.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.members.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.members.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self.members, 1)
        self.invite_button = action("Invite a member", self.invite)
        self.invite_button.setEnabled(not window.remote)
        layout.addWidget(self.invite_button)
        if window.remote:
            layout.addWidget(label("The owner manages this workspace's name and members.", "muted", True))
        self.error = label("", "muted", True)
        layout.addWidget(self.error)
        footer = QHBoxLayout()
        self.delete_button = action("Leave workspace" if window.remote else "Delete workspace", self.delete, name="danger")
        footer.addWidget(self.delete_button)
        footer.addStretch()
        footer.addWidget(action("Done", self.accept))
        layout.addLayout(footer)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.refresh)
        # Hidden dialogs must not keep rebuilding their native table widgets.
        self.finished.connect(self.timer.stop)
        self.timer.start()
        # None rather than an empty string, so the first pass always draws.
        self.signature = None
        self.refresh()

    def refresh(self):
        """Redraw only when the membership has actually changed.

        This ran every second unconditionally, and each pass built a fresh
        Remove button per member and handed it to setCellWidget, which deletes
        the previous one. So any keyboard focus sitting on a Remove button was
        destroyed on the next tick and the dialog's per-row controls were
        effectively mouse-only -- tab to one, and it vanishes before you can
        act on it.

        AgentListDialog already guards its refresh on a signature of the list,
        which is the same problem with a smaller table; this follows it. The
        timer is still a timer: it polls for the change and then does nothing.
        """
        members = self.window.workspace_meta.get("members", [])
        signature = json.dumps(members, sort_keys=True)
        if signature == self.signature:
            return
        self.signature = signature
        self.members.setRowCount(len(members))
        for row, member in enumerate(members):
            for column, value in enumerate((member["id"], member["role"], "Online" if member["online"] else "Offline")):
                self.members.setItem(row, column, QTableWidgetItem(value))
            if not self.window.remote and member["id"] != self.window.identity:
                # Says which member, because a column of identical "Remove"
                # buttons cannot be told apart by a screen reader or by anyone
                # reading this dialog with their eyes rather than their hands.
                remove = action("Remove", lambda checked=False, identity=member["id"]: self.remove(identity), name="danger")
                remove.setAccessibleName(f"Remove {member['id']} from this workspace")
                remove.setToolTip(f"Revoke {member['id']}'s invitation and close its connection")
                self.members.setCellWidget(row, 3, remove)
            self.members.setRowHeight(row, 46)

    def rename(self):
        self.rename_button.setEnabled(False)
        def done(result):
            self.rename_button.setEnabled(True)
            self.error.setText("Workspace name saved.")
        def failed(message):
            self.rename_button.setEnabled(True)
            self.error.setText(message)
        self.window.command("rename_workspace", self.name.text(), success=done, failure=failed)

    def invite(self):
        InviteDialog(self.window).exec()
        self.refresh()

    def remove(self, identity):
        if QMessageBox.question(self, "Remove member", f"Remove {identity}? Their invitation will be revoked and their connection closed.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Yes:
            return
        self.window.command("remove_member", identity, success=lambda result: self.refresh(), failure=self.error.setText)

    def delete(self):
        if self.window.remote:
            # Only the local half is certain. What the relay does with this
            # device's membership is not, and two ordinary cases are not
            # either: an offline host cannot revoke anything -- delete_workspace
            # catches the error and forgets the token locally -- and a relay
            # whose operator manages membership accepts the leave while the
            # device stays a member. Saying "the host will remove this device"
            # promised away both of them.
            message = ("Leave this workspace? The conversations saved here are deleted from this device. "
                       "If the host is offline, the invitation is only forgotten here. "
                       "On a relay where someone else manages membership, they may need to remove this device.")
        else:
            message = "Delete this workspace and its saved conversations on this device? All members will be disconnected."
        if QMessageBox.question(self, "Leave workspace" if self.window.remote else "Delete workspace", message,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Yes:
            return
        self.delete_button.setEnabled(False)
        def failed(message):
            self.delete_button.setEnabled(True)
            self.error.setText(message)
        self.window.command("delete_workspace", success=lambda result: self.accept(), failure=failed)
