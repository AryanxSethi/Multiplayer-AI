"""Workspace names, invitations, membership, and deletion controls."""
import json

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QApplication, QDialog, QHBoxLayout, QHeaderView,
    QLineEdit, QMessageBox, QPlainTextEdit, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget)

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
        # Invitations already made, so one can be shown again. An invitation
        # used to be readable exactly once, in the dialog that made it: close
        # it without copying and that device could never join, and the only way
        # to recover was to reset this device's own identity, which destroys
        # every other identity in the workspace too. The token is read back
        # from the credential store on demand and is never held here.
        self.invitations_box = QWidget()
        invitations_column = QVBoxLayout(self.invitations_box)
        invitations_column.setContentsMargins(0, 10, 0, 0)
        invitations_column.setSpacing(8)
        invitations_column.addWidget(label("Invitations already created", "heading"))
        self.invitations_note = label("", "muted", True)
        invitations_column.addWidget(self.invitations_note)
        # Rows go in a layout of their own so a refresh can empty just them.
        # Clearing the whole box took the heading and the note with it, and the
        # note's text was then set on a widget that was no longer in the box,
        # so neither was ever seen.
        self.invitation_rows = QVBoxLayout()
        self.invitation_rows.setContentsMargins(0, 0, 0, 0)
        self.invitation_rows.setSpacing(8)
        invitations_column.addLayout(self.invitation_rows)
        self.invitations_box.setVisible(False)
        layout.addWidget(self.invitations_box)
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

    def show_invitation(self, agent_id, invitation):
        """Show one invitation again, read-only, with a copy button.

        The token is only ever in the dialog that is showing it, and never in
        the workspace settings.
        """
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Invitation for {agent_id}")
        dialog.setMinimumWidth(520)
        column = QVBoxLayout(dialog)
        column.setContentsMargins(26, 24, 26, 24)
        column.setSpacing(14)
        column.addWidget(label(f"INVITATION FOR {agent_id.upper()}", "eyebrow"))
        column.addWidget(label("Send this to the device", "title"))
        body = QPlainTextEdit(json.dumps(invitation, indent=2))
        body.setReadOnly(True)
        column.addWidget(body)
        row = QHBoxLayout()
        copy = action("Copy", lambda: QApplication.clipboard().setText(
            json.dumps(invitation, indent=2)))
        copy.setAccessibleName(f"Copy the invitation for {agent_id} to the clipboard")
        row.addWidget(copy)
        row.addStretch()
        row.addWidget(action("Done", dialog.accept))
        column.addLayout(row)
        dialog.exec()

    def reissue_invitation(self, agent_id):
        """Fetch a saved invitation and show it."""
        def done(invitation):
            self.show_invitation(agent_id, invitation)
        def failed(message):
            self.error.setText(message)
        self.window.command("invitation_for", agent_id, success=done, failure=failed)

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
        self.refresh_invitations()

    def refresh_invitations(self):
        """List the invitations this device can still issue, one row each.

        Only rebuilt when the list itself changes. refresh() calls this whenever
        the membership does, and a member going online or offline is not a
        change to the invitations -- but rebuilding anyway deleted the rows, and
        took the keyboard focus sitting on a "Show invitation" button with them.
        That is the defect the signature guard on refresh() exists to prevent,
        reintroduced one call down, so it is guarded here for the same reason.
        """
        names = list(getattr(self.window, "outstanding_invitations", ()) or ())
        if names == getattr(self, "rendered_invitations", None):
            return
        self.rendered_invitations = list(names)
        while self.invitation_rows.count():
            child = self.invitation_rows.takeAt(0).widget()
            if child is not None:
                # Detached before being scheduled for deletion: deleteLater
                # waits for the next event loop pass, and until then the row is
                # still a child of a visible box with no layout to hide it.
                child.setParent(None)
                child.deleteLater()
        if not names:
            self.invitations_box.setVisible(False)
            return
        self.invitations_box.setVisible(True)
        self.invitations_note.setText(
            "Shown again from this device's credential store. Closing an "
            "invitation without sending it no longer strands that device.")
        for agent_id in names:
            container = QWidget()
            row = QHBoxLayout(container)
            row.setContentsMargins(0, 0, 0, 0)
            row.addWidget(label(agent_id, "body"))
            row.addStretch()
            button = action("Show invitation", lambda checked=False, name=agent_id:
                            self.reissue_invitation(name), name="ghost")
            button.setAccessibleName(f"Show the invitation for {agent_id} again")
            button.setToolTip(f"Read the saved invitation for {agent_id} from the credential store")
            row.addWidget(button)
            self.invitation_rows.addWidget(container)

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
        self.window.reload_workspace_invitations(self)
        # Refreshed independently of refresh(): an invitation does not change
        # who is in the workspace, so that call returns early and the list of
        # invitations this device can reissue would keep showing what it had
        # before the reader made this one.
        self.refresh()

    def remove(self, identity):
        if QMessageBox.question(self, "Remove member", f"Remove {identity}? Their invitation will be revoked and their connection closed.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Yes:
            return

        def revoked(result):
            # The names this device can reissue come from a separate read, so
            # reloading them is not part of redrawing the members table.
            self.window.reload_workspace_invitations(self)
            self.refresh()

        self.window.command("remove_member", identity, success=revoked, failure=self.error.setText)

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
