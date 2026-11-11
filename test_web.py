"""로고·PDF 미리보기 회귀 점검. 외부 API 호출 없이 실행한다."""
import hashlib
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient

import pymupdf
from fastapi import HTTPException

import app


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.rid = "a" * 32
        (self.root / self.rid).mkdir()
        self.pdf = self.root / self.rid / "input.pdf"
        with pymupdf.open() as doc:
            doc.new_page(width=2400, height=3200).insert_text((30, 30), "Preview regression test")
            doc.save(self.pdf)
        self.runs = patch.object(app, "RUNS", self.root)
        self.runs.start()

    def tearDown(self):
        self.runs.stop()
        self.temp.cleanup()

    def test_preview_is_bounded_png_and_preserves_source(self):
        before = hashlib.sha256(self.pdf.read_bytes()).digest()
        response = app.preview_run(self.rid, "input.pdf")
        self.assertEqual(response.media_type, "image/png")
        self.assertTrue(response.body.startswith(b"\x89PNG\r\n\x1a\n"))
        width, height = struct.unpack(">II", response.body[16:24])
        self.assertLessEqual(width, 1000)
        self.assertLessEqual(height, 1400)
        self.assertEqual(before, hashlib.sha256(self.pdf.read_bytes()).digest())

    def test_preview_rejects_invalid_run_names_and_missing_files(self):
        for rid, name in [("../", "input.pdf"), (self.rid, "../.env"), (self.rid, "other.pdf"), ("b" * 32, "input.pdf")]:
            with self.subTest(rid=rid, name=name), self.assertRaises(HTTPException) as error:
                app.preview_run(rid, name)
            self.assertEqual(error.exception.status_code, 404)

    def test_unreadable_pdf_has_useful_error(self):
        self.pdf.write_bytes(b"%PDF-broken")
        with self.assertRaises(HTTPException) as error:
            app.preview_run(self.rid, "input.pdf")
        self.assertEqual(error.exception.status_code, 422)


class UploadValidationTests(unittest.TestCase):
    def test_unreadable_and_encrypted_pdfs_never_reach_upstage(self):
        with patch.object(app, 'AGENT_GRADE', 'grade'),patch.object(app, 'AGENT_SPLIT', 'split'),patch.object(app, 'client') as external:
            web=TestClient(app.app)
            for data,expected in [(b'not a PDF',400),(b'%PDF-broken',422)]:
                response=web.post('/api/process',files={'file':('bad.pdf',data,'application/pdf')})
                self.assertEqual(response.status_code,expected)
                self.assertIn('PDF',response.json()['detail'])
            with pymupdf.open() as doc:
                doc.new_page()
                locked=doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256,owner_pw='owner',user_pw='user')
            response=web.post('/api/process',files={'file':('locked.pdf',locked,'application/pdf')})
            self.assertEqual(response.status_code,422)
            self.assertIn('암호',response.json()['detail'])
            external.assert_not_called()


if __name__ == "__main__":
    unittest.main()
