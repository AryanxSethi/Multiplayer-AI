import unittest

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices, QFont, QTextTable
from PySide6.QtWidgets import QApplication

from desktop_app.markdown import MarkdownMessage
from desktop_app.theme import DARK, color


class MarkdownMessageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_markdown_formats_text_lists_tables_and_literal_code(self):
        text = ('## Heading\n\nSome **bold text** and *emphasis* with `inline_code`.\n\n'
                '- First\n- Second\n\n```python\nprint("hello")\n## literal code\n```\n\n'
                '| Name | Value |\n| --- | --- |\n| Example | 42 |\n\n'
                '[Website](https://example.test) and [another link](https://other.test)')
        widget = MarkdownMessage(text)
        self.addCleanup(widget.deleteLater)
        document = widget.document()
        self.assertEqual(document.begin().blockFormat().headingLevel(), 2)
        self.assertEqual(document.find("bold text").charFormat().fontWeight(), QFont.Weight.Bold.value)
        self.assertTrue(document.find("emphasis").charFormat().fontItalic())
        self.assertTrue(document.find("inline_code").charFormat().fontFixedPitch())
        self.assertIn("monospace", document.find('print("hello")').charFormat().fontFamilies())
        self.assertIsNotNone(document.find("First").block().textList())
        self.assertEqual(document.find("## literal code").block().blockFormat().headingLevel(), 0)
        self.assertNotIn("**", widget.toPlainText())
        self.assertNotIn("```", widget.toPlainText())
        table = next(frame for frame in document.rootFrame().childFrames() if isinstance(frame, QTextTable))
        self.assertEqual((table.rows(), table.columns()), (2, 2))
        for text, url in (("Website", "https://example.test"), ("another link", "https://other.test")):
            link_format = document.find(text).charFormat()
            self.assertEqual(link_format.anchorHref(), url)
            # Read from the palette rather than naming a colour: a link takes
            # the agent_title token, and hard-coding its value here made this
            # test fail every time that token was retuned.
            self.assertEqual(link_format.foreground().color().name().lower(),
                             color(DARK, "agent_title").lower())

    def test_long_replies_resize_without_clipping_and_remain_selectable(self):
        widget = MarkdownMessage("## Long reply\n\n" + "A sentence that needs to wrap. " * 100)
        self.addCleanup(widget.deleteLater)
        widget.resize(640, 100)
        widget.show()
        for _ in range(3):
            self.app.processEvents()
        wide_height = widget.height()
        widget.resize(320, wide_height)
        for _ in range(3):
            self.app.processEvents()
        self.assertGreater(widget.height(), wide_height)
        self.assertGreaterEqual(widget.viewport().height(), widget.document().size().height())
        self.assertEqual(widget.verticalScrollBar().maximum(), 0)
        self.assertTrue(widget.isReadOnly())
        widget.selectAll()
        self.assertIn("Long reply", widget.textCursor().selectedText())


# --- what a reply is allowed to reach --------------------------------------
#
# A model reply is untrusted text, so these two methods are the boundary
# between it and the device. Neither had any test.


class UntrustedReplyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.opened = []
        self.original = QDesktopServices.openUrl
        QDesktopServices.openUrl = lambda url: self.opened.append(url.toString())

    def tearDown(self):
        QDesktopServices.openUrl = self.original

    def test_only_web_and_mail_links_are_followed(self):
        message = MarkdownMessage("")
        for url in ("https://example.com/x", "http://example.com", "mailto:a@example.com"):
            message.open_link(QUrl(url))
        self.assertEqual(len(self.opened), 3, "the three web schemes should be allowed")

    def test_dangerous_link_schemes_are_ignored(self):
        # Anything reaching QDesktopServices here came from a model's
        # output, so this allowlist is all that separates a reply from the
        # user's filesystem or a script engine.
        message = MarkdownMessage("")
        for url in ("file:///C:/Users/test/secret.txt",
                    "javascript:alert(1)",
                    "data:text/html,<script>alert(1)</script>",
                    "ftp://example.com/x",
                    "ms-msdt:/id"):
            message.open_link(QUrl(url))
        self.assertEqual(self.opened, [], "a reply opened something it should not have")

    def test_replies_cannot_load_images_or_files(self):
        message = MarkdownMessage("")
        self.assertEqual(bytes(message.loadResource(1, QUrl("https://example.com/p.png"))), b"")
        self.assertEqual(bytes(message.loadResource(2, QUrl("file:///C:/Users/test/secret.txt"))), b"")

    def test_an_image_in_a_reply_loads_nothing(self):
        message = MarkdownMessage("![pixel](https://example.com/pixel.png)")
        document = message.document()
        self.assertEqual(bytes(document.resource(1, QUrl("https://example.com/pixel.png"))), b"")
