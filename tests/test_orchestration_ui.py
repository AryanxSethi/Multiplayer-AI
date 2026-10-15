"""Connect a model, define its purpose, choose a coordinator, and send through the native UI."""
import json
import os
import re
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtCore import QPoint
from PySide6.QtGui import QColor, QImage, QPainter, QRegion
from PySide6.QtWidgets import QApplication, QLabel, QStyle, QStyleOptionButton
from starlette.responses import JSONResponse
from starlette.routing import Route

from desktop_app.storage import Storage
from desktop_app.model_purpose import ModelPurpose
from desktop_app.theme import DARK, LIGHT, MIKU, THEME_NAMES, color, stylesheet
from desktop_app.widgets import TickCheckBox
from desktop_app.window import CONVERSATIONS_PAGE, MainWindow
from network_a2a.orchestration import GENERAL_TARGET
from tests.test_desktop_runtime import MemoryVault


class OrchestrationUITests(unittest.TestCase):
    def setUp(self):
        startup = patch("desktop_app.ollama_service.OllamaService._start",
                        side_effect=RuntimeError("Ollama startup is mocked in UI tests"))
        startup.start()
        self.addCleanup(startup.stop)

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def wait(self, predicate):
        deadline = time.monotonic() + 15
        while not predicate():
            self.app.processEvents()
            if time.monotonic() > deadline:
                self.fail("Native orchestration UI condition timed out")
            time.sleep(.01)
        self.app.processEvents()

    def test_import_form_has_specialist_tasks_and_visible_ticks_in_every_theme(self):
        form = ModelPurpose()
        try:
            self.assertNotIn("general", form.tasks)
            self.assertEqual(set(form.tasks), {"coding", "debugging", "reasoning", "research", "writing"})
            self.assertIsInstance(form.enabled, TickCheckBox)
            checkbox = form.tasks["coding"]
            for theme in (DARK, LIGHT, MIKU):
                with self.subTest(theme=theme):
                    form.setStyleSheet(stylesheet(theme))
                    form.show()
                    checkbox.setChecked(False)
                    self.app.processEvents()
                    option = QStyleOptionButton()
                    checkbox.initStyleOption(option)
                    rect = checkbox.style().subElementRect(QStyle.SubElement.SE_CheckBoxIndicator, option, checkbox)
                    QTest.mouseClick(checkbox, Qt.MouseButton.LeftButton, pos=rect.center())
                    self.assertTrue(checkbox.isChecked())
                    self.app.processEvents()
                    image = checkbox.grab().toImage().copy(rect)
                    tick = color(theme, "success")
                    pixels = sum(image.pixelColor(x, y).name() == tick
                                 for x in range(image.width()) for y in range(image.height()))
                    self.assertGreater(pixels, 10, "The checked indicator must contain a painted tick")
                    self.assertLess(pixels, image.width() * image.height() // 2,
                                    "The checked indicator must not be a filled box")
                    corner = image.pixelColor(1, 1)
                    self.assertTrue(corner.alpha() == 0 or corner.name() == color(theme, "surface_base"),
                                    "The tick's background must stay transparent")
                    checkbox.setFocus()
                    QTest.keyClick(checkbox, Qt.Key.Key_Space)
                    self.assertFalse(checkbox.isChecked())
        finally:
            form.close()

    def test_the_enabled_box_draws_its_tick_too_and_fades_when_disabled(self):
        """The delegation switch is the box a user is most likely to need to read.

        It is the same widget as the task boxes, but it is the one that turns
        the whole automatic behaviour on, and it sits on its own row where the
        eye lands last. So its tick is painted and checked like the others, and
        a disabled one is dimmed rather than left at full strength pretending
        to be available.
        """
        form = ModelPurpose()
        try:
            enabled = form.enabled
            self.assertIsInstance(enabled, TickCheckBox)
            for theme in (DARK, LIGHT):
                with self.subTest(theme=theme):
                    form.setStyleSheet(stylesheet(theme))
                    form.show()
                    enabled.setChecked(False)
                    self.app.processEvents()
                    enabled.setChecked(True)
                    self.app.processEvents()
                    option = QStyleOptionButton()
                    enabled.initStyleOption(option)
                    enabled.style().subElementRect(
                        QStyle.SubElement.SE_CheckBoxIndicator, option, enabled)

                    # Rendered rather than grabbed, and that is the whole fix.
                    #
                    # The indicator's background is transparent, so grab()
                    # snapshots this widget over whatever is under it rather
                    # than over a background, and the half-opacity pen
                    # composites with a leftover tick instead: the disabled
                    # grab came back with pixels of #3ba65c and #3aa55c, which
                    # is the pure tick colour and not a blend of anything. The
                    # old count of "pixels exactly the tick colour" was
                    # therefore counting a rendering artefact, and comparing it
                    # with the enabled count proved nothing -- a control drawn at
                    # full strength and one drawn not at all both give zero.
                    #
                    # Rendering onto a chosen background makes the arithmetic
                    # available. Half of the tick over half of what is behind it
                    # is a colour that can be written down and checked for, and
                    # that is the only thing that can tell a half-faded tick
                    # from an absent one.
                    background = QColor("#101010")
                    tick = color(theme, "success")

                    def painted_at(strength):
                        enabled.setEnabled(strength)
                        self.app.processEvents()
                        image = QImage(enabled.width(), enabled.height(),
                                       QImage.Format.Format_RGB32)
                        image.fill(background)
                        painter = QPainter(image)
                        # The source region is given explicitly. Rendering the
                        # whole widget without one crashed this process outright
                        # on the offscreen platform.
                        enabled.render(painter, QPoint(0, 0),
                                       QRegion(0, 0, enabled.width(),
                                               enabled.height()))
                        painter.end()
                        return image

                    def distance(image, other):
                        return min(
                            sum(abs(image.pixelColor(x, y).getRgb()[i]
                                    - other.getRgb()[i]) for i in range(3)) / 3
                            for x in range(image.width())
                            for y in range(image.height()))

                    solid = painted_at(True)
                    exact = sum(solid.pixelColor(x, y).name() == tick
                                for x in range(solid.width())
                                for y in range(solid.height()))
                    self.assertGreater(exact, 10, (
                        "the delegation switch shows no tick when it is on"))

                    faded = painted_at(False)
                    self.assertEqual(
                        sum(faded.pixelColor(x, y).name() == tick
                            for x in range(faded.width())
                            for y in range(faded.height())), 0, (
                        "a disabled switch still paints some of the tick at "
                        "full strength, so it reads as though it could be "
                        "turned on"))
                    # What the half-opacity pen should have produced.
                    blend = QColor(*(round(0.5 * QColor(tick).getRgb()[i]
                                            + 0.5 * background.getRgb()[i])
                                     for i in range(3)))
                    self.assertLessEqual(distance(faded, blend), 8, (
                        f"the disabled tick is not half faded: the closest "
                        f"pixel is {distance(faded, blend):.0f} from the blend "
                        f"it should be"))
                    # And it is genuinely faded, not merely gone: the same
                    # pixel is nowhere near the full-strength colour.
                    self.assertGreaterEqual(distance(faded, QColor(tick)), 15, (
                        "the disabled tick is indistinguishable from a solid "
                        "one, so fading it does nothing"))
                    enabled.setEnabled(True)
        finally:
            form.close()

    def test_the_tick_takes_its_colour_from_the_property_not_the_palette(self):
        """A tick coloured once at construction goes wrong on a theme change.

        The colour is read from the palette the widget was built with, which
        is the default one, and so is invisible on a dark sheet where the
        default highlighted text is light on light. It is exposed as a Qt
        property so the stylesheet can set it per theme, and that is the only
        thing keeping the ticks visible.
        """
        box = TickCheckBox("a box")
        try:
            box.set_tick_color(color(LIGHT, "success"))
            self.assertEqual(box.tickColor.name(), color(LIGHT, "success"),
                             "set_tick_color did not take")
            box.setStyleSheet("TickCheckBox { qproperty-tickColor: %s; }" % color(DARK, "success"))
            box.style().polish(box)
            self.assertEqual(box.tickColor.name(), color(DARK, "success"),
                             "the stylesheet could not set the tick colour, so a "
                             "theme change leaves the ticks the old colour")
        finally:
            box.deleteLater()

    def test_every_theme_sheet_sets_the_tick_colour_it_declares(self):
        """Each theme has to name a tick colour, and it has to be a real one.

        The tick is drawn by hand on top of the native indicator, so nothing
        in the sheet reaches it except this property. A theme that omits the
        rule leaves every tick on that theme at whatever colour the widget was
        built with, which is the default palette's and is not guaranteed to
        contrast with the sheet it is painted on. Checking the sheets against
        each other would not catch it, because a missing rule is equally
        missing from all three.
        """
        for theme in THEME_NAMES:
            with self.subTest(theme=theme):
                sheet = stylesheet(theme)
                self.assertIn("qproperty-tickColor", sheet,
                              f"the {theme} sheet never sets the tick colour")
                declared = re.findall(r"qproperty-tickColor:\s*(#[0-9a-zA-Z]+)", sheet)
                self.assertTrue(declared,
                                f"the {theme} sheet never sets the tick colour")
                for value in declared:
                    self.assertRegex(value, r"^#[0-9a-fA-F]{6}$", (
                        f"the {theme} sheet declares the tick colour as {value!r}, "
                        "which is not a colour"))

    def test_an_unchecked_box_is_left_to_the_style(self):
        """No tick is drawn when there is nothing to tick.

        The paint overrides the native indicator to add a visible tick, and
        drawing one regardless would put a mark on every empty box in the
        form, including the ones that are off. The native box has to show
        through untouched, so an empty indicator keeps no painted pixels of
        the tick colour.
        """
        box = TickCheckBox("a box")
        try:
            box.set_tick_color(color(DARK, "success"))
            box.show()
            box.setChecked(False)
            self.app.processEvents()
            option = QStyleOptionButton()
            box.initStyleOption(option)
            rect = box.style().subElementRect(QStyle.SubElement.SE_CheckBoxIndicator, option, box)
            image = box.grab().toImage().copy(rect)
            tick = color(DARK, "success")
            painted = sum(image.pixelColor(x, y).name() == tick
                          for x in range(image.width()) for y in range(image.height()))
            self.assertEqual(painted, 0, "an unchecked box is painted as though it were on")
        finally:
            box.close()

    def test_editing_legacy_general_permissions_preserves_them_and_new_import_resets(self):
        form = ModelPurpose()
        profile = {"purpose": "Conversation", "tasks": ["general"], "delegation_enabled": True}
        form.load(profile)
        self.assertEqual(form.values(), profile)
        self.assertNotIn("general", form.tasks)
        form.load({})
        self.assertEqual(form.values(), {"purpose": "", "tasks": [], "delegation_enabled": False})

    def test_default_ollama_import_with_saved_empty_coordinator_can_send_immediately(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(directory, MemoryVault())
            storage.settings["coordinator"] = None
            storage.save()
            window = MainWindow(storage)
            window.show()
            try:
                self.wait(lambda: window.ready and window.agents)
                seen = []
                answer = 'print("Hello, world!")'

                async def provider(request):
                    seen.append(await request.json())
                    return JSONResponse({"message": {"content": answer}, "done": True, "done_reason": "stop"})

                window.network.runtime.app.router.routes.append(Route("/mock/api/chat", provider, methods=["POST"]))
                ollama_available = True
                async def model_catalog(request):
                    if not ollama_available:
                        return JSONResponse({"error": "Ollama unavailable"}, 503)
                    return JSONResponse({"models": [{"name": "first-model-id"}, {"name": "second-model-id"}]})
                window.network.runtime.app.router.routes.append(Route("/mock/api/tags", model_catalog))
                for model in ("first-model-id", "second-model-id"):
                    window.add_agent("ollama")
                    name = window.model_name.text()
                    self.assertEqual(name, "ollama-agent" if model == "first-model-id" else "ollama-agent-2")
                    window.model_id.setCurrentText(model)
                    window.model_base.setText(f"http://127.0.0.1:{window.port}/mock")
                    self.wait(lambda: window.model_id.count() == 2)
                    self.assertEqual({window.model_id.itemText(index) for index in range(window.model_id.count())},
                                     {"first-model-id", "second-model-id"})
                    self.assertEqual(window.model_id.currentText(), model)
                    self.assertEqual(window.model_purpose.values(), {"purpose": "", "tasks": [], "delegation_enabled": False})
                    QTest.mouseClick(window.model_save_button, Qt.MouseButton.LeftButton)
                    self.wait(lambda: window.selected == GENERAL_TARGET and window.stack.currentIndex() == CONVERSATIONS_PAGE
                              and window.send_button.isEnabled() and any(agent["id"] == name for agent in window.model_agents()))
                    self.assertEqual(window.coordinator_picker.currentData(), "ollama-agent")
                    self.assertEqual(window.chat_title.text(), "Chat")
                    self.assertEqual(window.chat_agent_count.text(), "1 agent")
                    self.assertIn("Ready to chat", window.chat_subtitle.text())
                    prompt = "Make a hello world program" if model == "first-model-id" else "Now use a function"
                    window.composer.setPlainText(prompt)
                    QTest.mouseClick(window.send_button, Qt.MouseButton.LeftButton)
                    expected = 1 if model == "first-model-id" else 2
                    self.wait(lambda: len(seen) == expected and not window.chats[GENERAL_TARGET]["pending"])
                    self.assertEqual(seen[-1]["model"], "first-model-id")
                    self.assertEqual(seen[-1]["messages"][-1]["content"], prompt)
                    self.assertEqual(window.chats[GENERAL_TARGET]["messages"][-1], ("assistant", answer))
                    self.assertEqual(window.chats[GENERAL_TARGET]["responders"][str(expected * 2 - 1)],
                                     {"agent_id": "ollama-agent", "model": "first-model-id", "provider": "ollama"})
                    self.assertTrue(any(child.text() == "first-model-id" for child in window.messages_widget.findChildren(QLabel)))
                self.assertEqual(len(window.model_agents()), 2)
                self.assertEqual(len(seen[-1]["messages"]), 3)
                self.assertEqual(seen[-1]["messages"][0]["content"], "Make a hello world program")
                self.assertEqual(window.chats[GENERAL_TARGET]["routing"]["mode"], "direct")
                window.command("stop_agent", "ollama-agent")
                self.wait(lambda: any(agent["id"] == "ollama-agent" and not agent["online"]
                                      for agent in window.model_agents()) and window.send_button.isEnabled())
                self.assertIn("Ready to chat with ollama-agent-2", window.chat_subtitle.text())
                window.composer.setPlainText("Add error handling to the function")
                QTest.mouseClick(window.send_button, Qt.MouseButton.LeftButton)
                self.wait(lambda: len(seen) == 3 and not window.chats[GENERAL_TARGET]["pending"])
                self.assertEqual(seen[-1]["model"], "second-model-id")
                self.assertEqual(seen[-1]["messages"][-1]["content"], "Add error handling to the function")
                self.assertEqual(window.chats[GENERAL_TARGET]["routing"]["mode"], "direct")
                self.assertEqual(window.chats[GENERAL_TARGET]["routing"]["assignments"], [])
                self.assertEqual(window.coordinator_picker.currentData(), "ollama-agent-2")
                self.assertEqual(window.coordinator_picker.findData("ollama-agent"), -1)
                self.assertEqual(window.workspace_meta["coordinator"], "ollama-agent")
                ollama_available = False
                self.wait(lambda: not window.send_button.isEnabled()
                          and all(not agent["online"] for agent in window.model_agents()))
                self.assertEqual(window.coordinator_picker.count(), 1)
                self.assertEqual(window.coordinator_picker.currentText(), "No active models")
                self.assertFalse(window.coordinator_save.isEnabled())
                ollama_available = True
                self.wait(lambda: window.send_button.isEnabled()
                          and window.coordinator_picker.currentData() == "ollama-agent-2")
                self.assertEqual(len(seen), 3, "Health checks must never replay chat requests")
            finally:
                window.close()
                self.wait(lambda: not window.network.isRunning())

    def test_model_purpose_coordinator_general_chat_and_edit_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            window = MainWindow(Storage(directory, MemoryVault()))
            window.show()
            try:
                self.wait(lambda: window.ready and window.agents)
                seen = []

                async def provider(request):
                    body = await request.json()
                    seen.append(body)
                    if body["messages"][-1]["content"].startswith("You are the task coordinator"):
                        answer = json.dumps({"tasks": [{"agent_id": "python-model", "task_type": "debugging",
                                                         "instruction": "Debug the original code"}], "reason": "Python specialist", "reply": ""})
                    else:
                        answer = "The bug is fixed."
                    return JSONResponse({"choices": [{"message": {"content": answer}, "finish_reason": "stop"}]})

                window.network.runtime.app.router.routes.append(Route("/mock/v1/chat/completions", provider, methods=["POST"]))
                window.add_agent("bionic")
                window.model_name.setText("python-model")
                window.model_id.setCurrentText("any-model-id")
                window.model_base.setText(f"http://127.0.0.1:{window.port}/mock/v1")
                window.model_key.setText("mock-key")
                window.model_vision.setChecked(True)
                window.model_purpose.purpose.setPlainText("Python coding and debugging; avoid writing")
                window.model_purpose.tasks["debugging"].setChecked(True)
                window.model_purpose.enabled.setChecked(True)
                QTest.mouseClick(window.model_save_button, Qt.MouseButton.LeftButton)
                self.wait(lambda: window.selected == GENERAL_TARGET and window.workspace_meta.get("coordinator") == "python-model"
                          and window.send_button.isEnabled() and window.model_profile_id is None)
                self.assertEqual(window.stack.currentIndex(), CONVERSATIONS_PAGE)
                self.assertEqual(window.chat_title.text(), "Chat")
                self.assertEqual(window.coordinator_picker.currentData(), "python-model")
                image = QImage(20, 20, QImage.Format.Format_RGB32)
                image.fill(0xff000000)
                image_path = Path(directory) / "bug.png"
                self.assertTrue(image.save(str(image_path)))
                window.prepare_files([str(image_path)])
                self.wait(lambda: not window.preparing_files)
                self.assertEqual(len(window.chats[GENERAL_TARGET].get("draft_attachments", [])), 1,
                                 window.toast.text())
                code = "Debug y\n```python\ndef f(): return 1 / 0\n```"
                window.composer.setPlainText(code)
                QTest.mouseClick(window.send_button, Qt.MouseButton.LeftButton)
                self.wait(lambda: ("assistant", "The bug is fixed.") in window.chats[GENERAL_TARGET]["messages"])
                worker_content = seen[1]["messages"][0]["content"]
                self.assertEqual(worker_content[0]["text"], code)
                self.assertEqual(worker_content[1]["type"], "image_url")
                self.assertEqual(window.chats[GENERAL_TARGET]["history"][0]["content"][0]["text"], code)
                self.assertEqual(window.chat_title.text(), "Chat")
                self.assertTrue(any("Python specialist" in child.text() for child in window.messages_widget.findChildren(QLabel)))
                self.assertTrue(any(child.text() == "any-model-id" for child in window.messages_widget.findChildren(QLabel)))
                self.assertFalse(any(child.text() == "Jev" for child in window.messages_widget.findChildren(QLabel)))

                chat = window.chats[GENERAL_TARGET]
                saved_history = list(chat["history"])
                window.add_agent("bionic")
                window.model_name.setText("second-model")
                window.model_id.setCurrentText("second-model-id")
                window.model_base.setText(f"http://127.0.0.1:{window.port}/mock/v1")
                window.model_key.setText("second-mock-key")
                window.model_purpose.purpose.setPlainText("Debugging specialist")
                window.model_purpose.tasks["debugging"].setChecked(True)
                window.model_purpose.enabled.setChecked(True)
                QTest.mouseClick(window.model_save_button, Qt.MouseButton.LeftButton)
                self.wait(lambda: len(window.model_agents()) == 2 and window.selected == GENERAL_TARGET
                          and window.stack.currentIndex() == CONVERSATIONS_PAGE)
                self.assertEqual(window.workspace_meta["coordinator"], "python-model")
                self.assertIs(window.chats[GENERAL_TARGET], chat)
                self.assertEqual(chat["history"], saved_history)
                self.assertEqual(window.conversation_picker.count(), 1)
                self.assertEqual(window.conversation_picker.currentData(), GENERAL_TARGET)
                self.assertTrue(window.conversation_picker.isHidden())

                agent = next(agent for agent in window.agents if agent["id"] == "python-model")
                window.edit_agent(agent)
                self.assertEqual(window.model_purpose.purpose.toPlainText(), "Python coding and debugging; avoid writing")
                self.assertTrue(window.model_purpose.tasks["debugging"].isChecked())
                self.assertFalse(window.model_purpose.tasks["coding"].isChecked())
                self.assertTrue(window.model_purpose.enabled.isChecked())
                window.add_agent("ollama")
                self.assertFalse(window.model_purpose.enabled.isChecked())
                self.assertEqual(window.model_purpose.purpose.toPlainText(), "")
                self.assertFalse(any(checkbox.isChecked() for checkbox in window.model_purpose.tasks.values()))
                window.select_agent(GENERAL_TARGET)
                previous_calls = len(seen)
                window.composer.setPlainText("Good morning!")
                QTest.mouseClick(window.send_button, Qt.MouseButton.LeftButton)
                self.wait(lambda: len(seen) == previous_calls + 1 and not window.chats[GENERAL_TARGET]["pending"])
                self.assertEqual(seen[-1]["messages"][-1]["content"], "Good morning!")
                self.assertEqual(window.chats[GENERAL_TARGET]["routing"]["mode"], "conversation")
                self.assertEqual(window.chats[GENERAL_TARGET]["routing"]["assignments"], [])
            finally:
                window.close()
                self.wait(lambda: not window.network.isRunning())
