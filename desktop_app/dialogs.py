import json

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QDialog, QFormLayout, QHBoxLayout, QLineEdit, QPlainTextEdit, QScrollArea, QVBoxLayout, QWidget

from network_a2a.adapters import PROVIDERS
from network_a2a.orchestration import GENERAL_TARGET
from network_a2a.web_search import validate_search_settings, validate_search_url

from .theme import PROVIDER_NAMES, color
from .lan import lan_addresses
from .widgets import Select, TickCheckBox, action, label
from .model_purpose import ModelPurpose


def available_agent_name(provider, agents):
    base = provider + "-agent"
    existing = {agent["id"] for agent in agents}
    name, number = base, 2
    while name in existing:
        name = f"{base}-{number}"
        number += 1
    return name


def _style_error(widget, window):
    """Colour a dialog's error label for the window's current theme.

    Each dialog already holds its parent window, so it can read the live
    theme rather than being handed one. An error label carries its own
    stylesheet, which outranks the application sheet and would otherwise
    stay on the dark-theme colour after a light switch.
    """
    widget.setStyleSheet("color: " + color(window.theme, "error") + ";")


class AgentDialog(QDialog):
    def __init__(self, window, provider="ollama", profile=None):
        """Build the model connection form, restoring saved provider and search settings."""
        super().__init__(window)
        self.window = window
        self.profile = profile
        self.setWindowTitle(("Edit model" if profile else "Connect a model") + " · Multiplayer AI")
        self.setMinimumWidth(560)
        self.resize(640, 780)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(28, 26, 28, 26)
        self.layout.setSpacing(14)
        self.layout.addWidget(label("Edit model" if profile else "Connect a model", "title"))
        self.layout.addWidget(label("Your model runs on this device. Its key stays in your OS credential store.", "muted", True))
        scroll = QScrollArea()
        self.scroll = scroll
        scroll.setWidgetResizable(True)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 12, 0)
        body_layout.setSpacing(12)
        scroll.setWidget(body)
        self.layout.addWidget(scroll, 1)
        form = QFormLayout()
        form.setVerticalSpacing(12)
        self.name = QLineEdit()
        self.provider = Select()
        for key, info in PROVIDER_NAMES.items():
            self.provider.addItem(info[0], key)
        self.provider.setCurrentIndex(self.provider.findData(provider))
        self.model = Select()
        self.model.setEditable(True)
        self.model.lineEdit().setPlaceholderText("Model ID, e.g. llama3.2")
        self.models_button = action("Find models", self.find_models)
        model_row = QHBoxLayout()
        model_row.addWidget(self.model, 1)
        model_row.addWidget(self.models_button)
        self.base = QLineEdit()
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("API key · leave blank to keep a saved key")
        self.system = QPlainTextEdit()
        self.system.setMaximumHeight(90)
        self.system.setPlaceholderText("Optional instructions for this agent")
        self.autostart = TickCheckBox("Start this agent automatically when the app opens")
        self.autostart.setChecked(True)
        self.insecure = TickCheckBox("Allow a provider's HTTP endpoint on a trusted LAN")
        form.addRow("Agent name", self.name)
        form.addRow("Provider", self.provider)
        form.addRow("Model", model_row)
        form.addRow("API root", self.base)
        form.addRow("API key", self.key)
        form.addRow("Instructions", self.system)
        body_layout.addLayout(form)
        self.purpose_settings = ModelPurpose()
        body_layout.addWidget(self.purpose_settings)
        body_layout.addWidget(self.autostart, 0, Qt.AlignmentFlag.AlignLeft)
        body_layout.addWidget(self.insecure, 0, Qt.AlignmentFlag.AlignLeft)
        self.vision = TickCheckBox("Enable image support for this model")
        body_layout.addWidget(self.vision, 0, Qt.AlignmentFlag.AlignLeft)
        body_layout.addWidget(label("Select a vision-capable model to understand images and scanned PDFs. Text PDFs work with any model.", "muted", True))
        body_layout.addWidget(label("Internet access", "heading"))
        self.internet = TickCheckBox("Allow this model to search the web")
        body_layout.addWidget(self.internet, 0, Qt.AlignmentFlag.AlignLeft)
        self.search_settings = QWidget()
        search_layout = QVBoxLayout(self.search_settings)
        search_layout.setContentsMargins(0, 0, 0, 0)
        search_form = QFormLayout()
        self.search_form = search_form
        self.search_provider = Select()
        self.search_provider.addItem("Ollama web search · no server setup", "ollama")
        self.search_provider.addItem("SearXNG · use your own server", "searxng")
        self.search_mode = Select()
        self.search_mode.addItem("Automatic · when the model needs outside information", "auto")
        self.search_mode.addItem("Always · search for every question", "always")
        self.search_url = QLineEdit()
        self.search_url.setPlaceholderText("https://your-server.example.com or http://localhost:8888")
        self.search_key = QLineEdit()
        self.search_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.search_key.setPlaceholderText("Ollama account key · leave blank to keep a saved search key")
        search_form.addRow("Search provider", self.search_provider)
        search_form.addRow("Search mode", self.search_mode)
        search_form.addRow("Search API key", self.search_key)
        search_form.addRow("SearXNG server", self.search_url)
        search_layout.addLayout(search_form)
        self.search_insecure = TickCheckBox("Allow SearXNG over HTTP on a trusted LAN")
        search_layout.addWidget(self.search_insecure, 0, Qt.AlignmentFlag.AlignLeft)
        self.search_help = label("", "muted", True)
        self.search_help.setTextFormat(Qt.TextFormat.RichText)
        self.search_help.setOpenExternalLinks(True)
        search_layout.addWidget(self.search_help)
        self.search_test = action("Test web search", self.test_search)
        self.search_testing = False
        search_layout.addWidget(self.search_test)
        self.search_status = label("", "muted", True)
        search_layout.addWidget(self.search_status)
        search_layout.addWidget(label("Automatic mode lets the model request a search for current or uncertain facts. Some models may miss when a search is needed; choose Always to search before every answer. Queries go to the search provider you select, and source links are included in the answer.", "muted", True))
        body_layout.addWidget(self.search_settings)
        self.error = label("", "muted", True)
        self.error.setStyleSheet("color: #f38a8e;")
        self.layout.addWidget(self.error)
        row = QHBoxLayout()
        row.addWidget(action("Cancel", self.reject))
        self.save_button = action("Save changes" if profile else "Connect agent", self.save, True)
        row.addWidget(self.save_button)
        self.layout.addLayout(row)
        self.provider.currentIndexChanged.connect(self.change_provider)
        self.internet.toggled.connect(self.change_internet)
        self.search_provider.currentIndexChanged.connect(self.change_internet)
        for signal in (self.search_url.textChanged, self.search_key.textChanged, self.search_insecure.toggled,
                       self.search_provider.currentIndexChanged, self.internet.toggled):
            signal.connect(lambda *args: self.search_status.setText(""))
        self.change_provider()
        if profile:
            self.name.setText(profile["id"])
            self.name.setReadOnly(True)
            self.model.setCurrentText(profile["model"])
            self.base.setText(profile.get("base_url") or "")
            self.system.setPlainText(profile.get("system_prompt") or "")
            self.purpose_settings.load(profile)
            self.autostart.setChecked(profile.get("autostart", True))
            self.insecure.setChecked(profile.get("allow_insecure", False))
            self.vision.setChecked(profile.get("vision", False))
            self.search_provider.setCurrentIndex(max(0, self.search_provider.findData(profile.get("search_provider", "searxng"))))
            self.internet.setChecked(profile.get("web_search", "off") != "off")
            self.search_mode.setCurrentIndex(max(0, self.search_mode.findData(profile.get("web_search", "auto"))))
            self.search_url.setText(profile.get("searxng_url", ""))
            self.search_insecure.setChecked(profile.get("searxng_allow_insecure", False))
        if window.remote:
            self.name.setText(window.identity)
            self.name.setReadOnly(True)
        self.change_internet()

    def change_provider(self):
        key = self.provider.currentData()
        self.model.clear()
        self.model.clearEditText()
        spec = PROVIDERS[key]
        if not self.profile:
            self.name.setText(available_agent_name(key, self.window.agents))
        if self.window.remote:
            self.name.setText(self.window.identity)
        self.base.setText(spec.base_url or "")
        self.base.setPlaceholderText("https://your-deployment.example.com/v1")
        self.models_button.setVisible(key not in ("anthropic", "gemini"))
        self.key.setPlaceholderText("Optional for local Ollama" if not spec.key_required else "API key · leave blank to keep a saved key")

    def find_models(self):
        """Fetch the selected provider's model list without blocking the dialog."""
        self.models_button.setEnabled(False)
        selected = self.model.currentText()
        def success(models):
            if self.isVisible():
                self.models_button.setEnabled(True)
                self.model.clear()
                self.model.addItems(models)
                if selected and (self.provider.currentData() != "ollama" or selected in models):
                    self.model.setCurrentText(selected)
                elif not models:
                    self.model.clearEditText()
                if not models:
                    self.error.setText("No models found. Install a model in Ollama, then refresh the list."
                                       if self.provider.currentData() == "ollama" else
                                       "No models found. Enter your provider's model ID.")
        def fail(message):
            if self.isVisible():
                self.models_button.setEnabled(True)
                selected = self.model.currentText()
                self.model.clear()
                if self.provider.currentData() == "ollama":
                    self.model.clearEditText()
                else:
                    self.model.setCurrentText(selected)
                self.error.setText("Could not list models. Check the Ollama installation and API root."
                                   if self.provider.currentData() == "ollama" else
                                   "Could not list models. Check the API root and key, or enter the model ID directly.")
        self.window.command("provider_models", self.provider.currentData(), self.base.text().strip(),
                            self.key.text() or None, self.insecure.isChecked(),
                            self.profile["id"] if self.profile else None, success=success, failure=fail)

    def change_internet(self):
        """Show the selected search provider's fields and update test availability."""
        enabled = self.internet.isChecked()
        hosted = self.search_provider.currentData() == "ollama"
        self.search_settings.setVisible(enabled)
        self.search_form.setRowVisible(self.search_key, hosted)
        self.search_form.setRowVisible(self.search_url, not hosted)
        self.search_insecure.setVisible(not hosted)
        self.search_test.setEnabled(enabled and not self.search_testing)
        self.search_help.setText(
            f'Get a key from <a href="https://ollama.com/settings/keys" style="color: {color(self.window.theme, "accent")}">your Ollama account</a> and paste it into Search API key. '
            'It works with any connected model, including local Ollama. The key stays in your OS credential store.'
            if hosted else
            'SearXNG needs a running search server; it is separate from your model\'s API root. '
            'Enter its address above. The server owner must enable JSON search results. '
            'Choose Ollama web search if you do not have a server.')

    def focus_search(self):
        """Focus the relevant search credential or URL and keep it visible after layout."""
        widget = self.search_key if self.search_provider.currentData() == "ollama" else self.search_url
        self.scroll.ensureWidgetVisible(widget)
        widget.setFocus()
        # Showing the error can resize the viewport after this call.
        # Scroll again once Qt has laid out the visible settings and error.
        def reveal():
            """Scroll the visible search field into view after Qt applies the new layout."""
            if self.isVisible() and widget.isVisible():
                self.scroll.ensureWidgetVisible(widget, 0, 24)
        QTimer.singleShot(0, reveal)

    def search_state(self):
        """Return a settings snapshot used to discard stale search test responses."""
        return (self.internet.isChecked(), self.search_provider.currentData(), self.search_url.text().strip(),
                self.search_key.text().strip(), self.search_insecure.isChecked())

    def validate_search(self):
        """Return whether search settings are valid, displaying and focusing any error."""
        if not self.internet.isChecked():
            return True
        try:
            if self.search_provider.currentData() == "searxng":
                validate_search_url(self.search_url.text(), self.search_insecure.isChecked())
            elif self.search_key.text().strip() or not self.profile:
                validate_search_settings("ollama", api_key=self.search_key.text().strip())
        except ValueError as exc:
            self.error.setText(str(exc))
            self.search_status.setText(str(exc))
            self.focus_search()
            return False
        return True

    def test_search(self):
        """Test the configured search service and report results for the current settings."""
        self.error.setText("")
        if not self.validate_search():
            return
        self.search_testing = True
        self.change_internet()
        state = self.search_state()
        self.search_status.setText("Testing web search…")
        def success(count):
            """Restore test controls and show the result only if settings have not changed."""
            if self.isVisible():
                self.search_testing = False
                self.change_internet()
                if self.search_state() == state:
                    self.search_status.setText(f"Web search is reachable · {count} results returned" if count else
                                               "Connected to the search service, but no results were returned. Try again before relying on web search.")
        def failure(message):
            """Restore test controls and focus an error only for unchanged search settings."""
            if self.isVisible():
                self.search_testing = False
                self.change_internet()
                if self.search_state() == state:
                    self.search_status.setText(message)
                    self.focus_search()
        self.window.command("test_web_search", self.search_url.text().strip(), self.search_insecure.isChecked(),
                            self.search_provider.currentData(), self.search_key.text().strip() or None,
                            self.profile["id"] if self.profile else None,
                            success=success, failure=failure)

    def save(self):
        """Validate and save the model profile with separate model and search credentials."""
        self.error.setText("")
        if not self.validate_search():
            return
        profile = {"id": self.name.text().strip(), "provider": self.provider.currentData(),
                   "model": self.model.currentText().strip(), "base_url": self.base.text().strip(),
                   "system_prompt": self.system.toPlainText().strip(), "autostart": self.autostart.isChecked(),
                   "allow_insecure": self.insecure.isChecked(), "vision": self.vision.isChecked(),
                   "web_search": self.search_mode.currentData() if self.internet.isChecked() else "off",
                   "search_provider": self.search_provider.currentData(),
                   "searxng_url": self.search_url.text().strip(), "searxng_allow_insecure": self.search_insecure.isChecked(),
                   **self.purpose_settings.values()}
        self.save_button.setEnabled(False)
        def success(result):
            """Clear credential fields and close the dialog after a successful save."""
            self.key.clear()
            self.search_key.clear()
            self.accept()
            if not self.profile:
                self.window.select_agent(GENERAL_TARGET)
        def failure(message):
            """Display the save error and focus search settings when they caused the failure."""
            if self.isVisible():
                self.error.setText(message)
                self.save_button.setEnabled(True)
                if self.internet.isChecked() and ("search" in message.lower() or "searxng" in message.lower()):
                    self.focus_search()
        search_key = (self.search_key.text().strip() or None) if self.search_provider.currentData() == "ollama" else None
        self.window.command("save_agent", profile, self.key.text() or None, search_key,
                            success=success, failure=failure)


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


class JoinDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.setWindowTitle("Join a workspace")
        self.setMinimumWidth(500)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 26, 28, 26)
        layout.setSpacing(14)
        layout.addWidget(label("GOOD WORK TRAVELS", "eyebrow"))
        layout.addWidget(label("Join your team", "title"))
        layout.addWidget(label("Paste an invitation, or enter your relay address and private token.", "muted", True))
        self.invitation = QPlainTextEdit()
        self.invitation.setPlaceholderText("Paste your invitation JSON here")
        self.invitation.setMaximumHeight(100)
        layout.addWidget(self.invitation)
        self.url = QLineEdit()
        self.url.setPlaceholderText("wss://your-relay.example.com/connect")
        self.token = QLineEdit()
        self.token.setPlaceholderText("Your device's relay token")
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addWidget(label("Relay address", "muted"))
        layout.addWidget(self.url)
        layout.addWidget(label("Device token", "muted"))
        layout.addWidget(self.token)
        self.insecure = TickCheckBox("This is a trusted LAN connection (allow ws://)")
        layout.addWidget(self.insecure, 0, Qt.AlignmentFlag.AlignLeft)
        self.error = label("", "muted", True)
        self.error.setStyleSheet("color:#f38a8e")
        layout.addWidget(self.error)
        self.connect_button = action("Join workspace", self.join, True)
        layout.addWidget(self.connect_button)
        layout.addWidget(action("Cancel", self.reject))

    def join(self):
        self.error.setText("")
        try:
            conversation_id = None
            if self.invitation.toPlainText().strip():
                url, token, allow_insecure, conversation_id = parse_invitation(
                    self.invitation.toPlainText())
                self.url.setText(url)
                self.token.setText(token)
                self.insecure.setChecked(allow_insecure)
            if len(self.token.text().strip()) < 32:
                raise ValueError("Enter the device token from your invitation")
            self.connect_button.setEnabled(False)
            self.connect_button.setText("Connecting…")
            self.error.setText("Contacting the relay. Keep the host app open; this can take up to 30 seconds.")
            def failure(message):
                if self.isVisible():
                    self.connect_button.setEnabled(True)
                    self.connect_button.setText("Join workspace")
                    self.error.setText(message or "The connection failed. Check the host address, network, and firewall.")
            def success(result):
                self.token.clear()
                self.invitation.clear()
                self.accept()
                if conversation_id:
                    self.window.select_agent(conversation_id)
                    return
                peer = next((agent for agent in self.window.agents if agent["online"] and agent["id"] != self.window.identity), None)
                if peer:
                    self.window.select_agent(peer["id"])
                else:
                    self.window.navigate(1)
                    self.window.notice("Workspace joined. No other devices are online yet. Keep the host app open and connect an agent to begin.")
            self.window.command("join", self.url.text().strip(), self.token.text().strip(), self.insecure.isChecked(), True, conversation_id,
                                success=success, failure=failure)
        except (ValueError, KeyError, TypeError) as exc:
            self.connect_button.setEnabled(True)
            self.connect_button.setText("Join workspace")
            # parse_invitation already turns a malformed paste into a
            # sentence for the reader, so only a missing field is left.
            self.error.setText(
                str(exc) if isinstance(exc, ValueError)
                else "Paste the complete invitation JSON, including its url, token, and version fields.")


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
