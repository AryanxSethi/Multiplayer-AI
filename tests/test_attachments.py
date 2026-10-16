"""Read real PDF text and page images using the libraries shipped in the app."""
import base64
import tempfile
import unittest
from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtPdf")
from PySide6.QtCore import QRect
from PySide6.QtGui import QImage, QPainter, QPdfWriter
from PySide6.QtWidgets import QApplication

from desktop_app.attachments import prepare_attachments
from network_a2a.content import validate_content


def make_files(directory):
    image = QImage(200, 120, QImage.Format.Format_RGB32)
    image.fill(0xff000000)
    image_path = directory / "diagram.png"
    assert image.save(str(image_path))
    pdf_path = directory / "report.pdf"
    # A standard PDF text stream avoids depending on fonts installed for Qt's
    # offscreen plugin, which can otherwise draw missing-glyph paths as shapes.
    stream = b"BT /F1 16 Tf 50 730 Td (Project Aurora has 123 active agents.) Tj ET"
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"]
    raw, offsets = bytearray(b"%PDF-1.4\n"), [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(raw))
        raw.extend(f"{index} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(raw)
    raw.extend(b"xref\n0 6\n0000000000 65535 f \n")
    for offset in offsets[1:]:
        raw.extend(f"{offset:010d} 00000 n \n".encode())
    raw.extend(f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    pdf_path.write_bytes(raw)
    scanned_path = directory / "scan.pdf"
    writer = QPdfWriter(str(scanned_path))
    painter = QPainter(writer)
    painter.drawImage(QRect(100, 100, 2000, 1200), image)
    painter.end()
    del writer
    return image_path, pdf_path, scanned_path


class AttachmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.files = make_files(Path(self.directory.name))

    def tearDown(self):
        self.directory.cleanup()

    def test_text_pdf_is_readable_without_vision(self):
        files = prepare_attachments([str(self.files[1])])
        self.assertIn("Aurora", files[0]["content"][0]["text"])
        self.assertEqual(files[0]["content"][0]["type"], "document")
        validate_content(files[0]["content"])

    def test_images_and_pdf_pages_contain_real_decodable_pixels(self):
        files = prepare_attachments([str(path) for path in self.files], vision=True)
        self.assertEqual(len(files), 3)
        self.assertIn("Aurora", files[1]["content"][0]["text"])
        self.assertEqual([part["type"] for part in files[1]["content"]], ["document", "image"])
        self.assertEqual(files[2]["content"][0]["type"], "image")
        for item in files:
            validate_content(item["content"])
            for part in item["content"]:
                if part["type"] == "image":
                    image = QImage.fromData(base64.b64decode(part["data"]))
                    self.assertFalse(image.isNull())
                    self.assertLessEqual(max(image.width(), image.height()), 1600)

    def test_text_model_rejects_images_and_scanned_pdfs_explicitly(self):
        for path in (self.files[0], self.files[2]):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "image support"):
                prepare_attachments([str(path)])

    def test_invalid_pdf_and_too_many_files_are_rejected(self):
        invalid = Path(self.directory.name) / "broken.pdf"
        invalid.write_text("not a PDF", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Could not open"):
            prepare_attachments([str(invalid)])
        with self.assertRaisesRegex(ValueError, "up to 8"):
            prepare_attachments([str(self.files[1])] * 9)
