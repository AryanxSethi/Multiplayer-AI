import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QLineEdit, QPlainTextEdit, QVBoxLayout

from .lan import lan_addresses
from .widgets import Select, TickCheckBox, action, label


def available_agent_name(provider, agents):
    base = provider + "-agent"
    existing = {agent["id"] for agent in agents}
    name, number = base, 2
    while name in existing:
        name = f"{base}-{number}"
        number += 1
    return name


def parse_invitation(text):
    """Read an invitation object into the four values a join needs.

    Returns ``(url, token, allow_insecure, conversation_id)``. Raises
    ValueError carrying a sentence meant to be shown to the reader as-is.

    This lives on its own, not inside the dialog, because the Workspaces page
    and the dialog both have to agree on exactly what counts as an
    invitation. When the page opened the dialog instead of doing the work
    itself, the fields on the page were decoration and the rules had two
    homes; a rule stated once cannot disagree with itself.

    Every field is checked here rather than subscripted. A missing one raised
    KeyError, which the page rendered as ``str(exc)``, so a reader who pasted
    an invitation without a url was told ``'url'``. A wrongly typed one was
    worse: the page put the int into a QLineEdit and the reader saw a PySide6
    signature dump. ``allow_insecure`` had the quietest failure of the three,
    because ``bool("false")`` is True, so a paste saying ``"allow_insecure":
    "false"`` turned a wss:// requirement into a permitted ws:// one.
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            "Paste the complete invitation JSON, including its url, token, "
            "and version fields.") from exc
    if not isinstance(data, dict):
        raise ValueError("Paste the invitation object beginning with { and ending with }")
    # isinstance rather than != 1, because True == 1 and a paste saying
    # "version": true was accepted as version one.
    version = data.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version != 1:
        raise ValueError("Unsupported invitation version")

    url = data.get("url")
    if not isinstance(url, str) or not url.strip():
        raise ValueError("This invitation has no relay address in it")
    token = data.get("token")
    if not isinstance(token, str) or not token.strip():
        raise ValueError("This invitation has no device token in it")

    allow_insecure = data.get("allow_insecure", False)
    if not isinstance(allow_insecure, bool):
        raise ValueError("The invitation's allow_insecure field must be true or false")

    conversation_id = data.get("conversation_id")
    if conversation_id is not None and (not isinstance(conversation_id, str)
                                        or not conversation_id.startswith("conversation-")
                                        or not conversation_id.removeprefix("conversation-").isalnum()):
        raise ValueError("Use the complete shared conversation invitation")
    return (url, token, allow_insecure, conversation_id)


class InviteDialog(QDialog):
    def __init__(self, window, conversation_id=None, target=None, messages=None):
        super().__init__(window)
        self.window = window
        self.conversation_id = conversation_id
        self.target = target
        self.messages = messages
        self.setWindowTitle("Invite a device")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 26, 28, 26)
        layout.setSpacing(14)
        layout.addWidget(label("MAKE YOUR WORKSPACE MULTIPLAYER", "eyebrow"))
        layout.addWidget(label("Invite a device", "title"))
        layout.addWidget(label("Give each laptop or desktop its own identity. Share its invitation privately.", "muted", True))
        if conversation_id or target:
            self.setWindowTitle("Invite to conversation")
            layout.addWidget(label("This invitation shares the conversation's messages and AI replies with the other device.", "muted", True))
        self.name = QLineEdit()
        self.name.setPlaceholderText("e.g. alex-laptop")
        self.lan = TickCheckBox("Share this relay on my local network")
        self.lan.setChecked(True)
        self.url = QLineEdit()
        self.network_address = Select()
        for name, ip in lan_addresses():
            self.network_address.addItem(f"{name} · {ip}", ip)
        address = self.network_address.currentData() or "YOUR_LAN_IP"
        self.url.setText(f"ws://{address}:{window.port}/connect")
        layout.addWidget(label("New device identity", "muted"))
        layout.addWidget(self.name)
        layout.addWidget(self.lan, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(label("Host network · choose the Wi-Fi or hotspot connected to the other device", "muted", True))
        layout.addWidget(self.network_address)
        self.network_address.currentIndexChanged.connect(self.select_network)
        self.lan.toggled.connect(self.network_address.setEnabled)
        layout.addWidget(label("Address the other device can reach", "muted"))
        layout.addWidget(self.url)
        layout.addWidget(label("Keep the host app open and allow Multiplayer AI through its firewall. After changing Wi-Fi or hotspot, create a new invitation with the current address. Campus and guest Wi-Fi may block devices from reaching each other, even with the same Wi-Fi name. For those networks, use a reachable WSS relay or a network that allows device-to-device connections.", "muted", True))
        self.error = label("", "muted", True)
        self.error.setStyleSheet("color:#f38a8e")
        layout.addWidget(self.error)
        self.create_button = action("Create invitation", self.create, True)
        layout.addWidget(self.create_button)
        self.result = QPlainTextEdit()
        self.result.setReadOnly(True)
        self.result.setMaximumHeight(180)
        self.result.hide()
        layout.addWidget(self.result)
        self.copy_button = action("Copy private invitation", self.copy)
        self.copy_button.hide()
        layout.addWidget(self.copy_button)
        layout.addWidget(action("Done", self.accept))

    def select_network(self):
        address = self.network_address.currentData()
        if self.lan.isChecked() and address:
            self.url.setText(f"ws://{address}:{self.window.port}/connect")

    def create(self):
        self.create_button.setEnabled(False)
        def success(data):
            if self.isVisible():
                self.result.setPlainText(json.dumps(data, indent=2))
                self.result.show()
                self.copy_button.show()
                if data.get("conversation_id"):
                    self.conversation_id = data["conversation_id"]
                    self.window.select_agent(self.conversation_id)
        def failure(message):
            if self.isVisible():
                self.error.setText(message)
                self.create_button.setEnabled(True)
        self.window.command("invite", self.name.text().strip(), self.url.text().strip(), self.lan.isChecked(),
                            self.conversation_id, self.target, self.messages, success=success, failure=failure)

    def copy(self):
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(self.result.toPlainText())
        self.window.notice("Private invitation copied. Send it only to the intended device.")


# The three addresses one model's details are read from. Named here rather
# than described in prose so the dialog cannot drift from what is actually
# requested -- this list and cookbook_hub._urls have to be changed together.
COOKBOOK_PATHS = (
    "/api/models/<publisher>/<name>",
    "/<publisher>/<name>/raw/main/config.json",
    "/api/models/<builder>/<name>/tree/main",
)


class RefreshDisclosure(QDialog):
    """What pressing Refresh sends, where, and how much of it.

    The page used to carry this as one muted line above the list and a tooltip
    on the button. That is a disclosure somebody has to go looking for, about
    twelve HTTP requests they are agreeing to, and it is the only thing in the
    app that leaves the machine under its own power.

    So it is asked for at the moment of the action rather than stated in
    advance somewhere else on the page. Every part of it is checkable before
    the press: the exact origin, the exact paths, the count, and what is and
    is not in the request.

    The host is the one thing this cannot vouch for. A mirror is somebody
    else's server, and the point of the dialog is that the reader gets to see
    which one before agreeing -- not that this app has an opinion about it.
    """

    def __init__(self, window, host, models, mirror=False):
        super().__init__(window)
        self.window = window
        self.host = host
        self.models = list(models)
        self.setWindowTitle("Request model details from " + host)
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 26, 28, 26)
        layout.setSpacing(14)

        layout.addWidget(label("ABOUT TO GO ONLINE", "eyebrow"))
        layout.addWidget(label("Request details from " + host, "title"))

        count = len(self.models)
        plural = "s" if count != 1 else ""
        layout.addWidget(label(
            f"This will make {count * len(COOKBOOK_PATHS)} requests to "
            f"{host} -- {len(COOKBOOK_PATHS)} for each of the {count} "
            f"model{plural} in the catalogue, read one after another. Nothing "
            "is sent until you continue.", "muted", True))

        for entry in COOKBOOK_PATHS:
            line = label(f"{host}{entry}", "command")
            line.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse)
            line.setAccessibleName(f"Address requested: {host}{entry}")
            layout.addWidget(line)

        sent = label(
            "What this sends: your IP address, and the publisher and name of "
            "every model being looked up.", "muted", True)
        sent.setWordWrap(True)
        layout.addWidget(sent)

        not_sent = label(
            "What this does not send: no account, no token, no workspace data, "
            "no messages, and nothing about the machine it is running on beyond "
            "the IP address every request carries.", "muted", True)
        not_sent.setWordWrap(True)
        layout.addWidget(not_sent)

        if mirror:
            note = label(
                f"{host} is a mirror you set, not the public catalogue. "
                "Whatever runs it can see the list of models you looked up.",
                "muted", True)
            note.setWordWrap(True)
            layout.addWidget(note)

        self.remember = TickCheckBox(f"Don't ask again for {host}")
        self.remember.setAccessibleName(
            f"Do not show this warning again for {host}")
        self.remember.setToolTip(
            "Remembered for this host only. Point the Cookbook at a "
            "different host and you will be asked about that one too.")
        layout.addWidget(self.remember, 0, Qt.AlignmentFlag.AlignLeft)

        layout.addWidget(action("Continue", self.accept, True))
        layout.addWidget(action("Cancel", self.reject))

    def should_remember(self):
        return self.remember.isChecked()
