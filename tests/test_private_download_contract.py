"""Offline authorization regression for document downloads.

Runs only against an isolated temporary SQLite database and synthetic files.
Never connects to Render, PostgreSQL, or a deployed DocWallet environment.
"""
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest

_SANDBOX = tempfile.TemporaryDirectory(prefix="docwallet-download-contract-")
_ROOT = Path(_SANDBOX.name)
os.environ["DATABASE_URL"] = "sqlite:///" + str(_ROOT / "test.sqlite3")
os.environ["UPLOAD_DIR"] = str(_ROOT / "uploads")
os.environ["JWT_SECRET"] = "offline-ci-synthetic-secret-not-for-production"
os.environ["NEXA_SSO_ENABLED"] = "false"
os.environ["NEXA_ECOSYSTEM_ATTENTION_ENABLED"] = "false"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # prefer repository app.py over tests/app.py
from app import Document, User, app, create_token, db  # noqa: E402


class PrivateDownloadContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = app.test_client()
        cls.payload = b"%PDF-1.4 synthetic only\\n"
        path = _ROOT / "uploads" / "synthetic-document.pdf"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(cls.payload)
        with app.app_context():
            owner = User(name="Synthetic owner", email="owner@example.invalid", password_hash="dummy")
            other = User(name="Synthetic other", email="other@example.invalid", password_hash="dummy")
            db.session.add_all([owner, other])
            db.session.flush()
            record = Document(
                user_id=owner.id,
                name="Synthetic PDF",
                original_filename="synthetic-document.pdf",
                stored_filename="synthetic-document.pdf",
                file_path=str(path),
                file_type="application/pdf",
                file_size=len(cls.payload),
                file_hash=hashlib.sha256(cls.payload).hexdigest(),
                doc_type="rg",
                category="ids",
            )
            db.session.add(record)
            db.session.commit()
            cls.document_id = record.id
            cls.owner_token = create_token(owner)
            cls.other_token = create_token(other)
        cls.url = "/api/documents/" + cls.document_id + "/download"

    def test_bearer_owner_can_download_synthetic_pdf(self):
        res = self.client.get(self.url, headers={"Authorization": "Bearer " + self.owner_token})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data, self.payload)
        self.assertTrue(res.content_type.startswith("application/pdf"))

    def test_no_bearer_is_rejected(self):
        self.assertEqual(self.client.get(self.url).status_code, 401)

    def test_session_in_query_string_is_not_accepted(self):
        res = self.client.get(self.url, query_string={"s": self.owner_token})
        self.assertEqual(res.status_code, 401)

    def test_wrong_user_cannot_read_another_account_document(self):
        res = self.client.get(self.url, headers={"Authorization": "Bearer " + self.other_token})
        self.assertEqual(res.status_code, 404)

    def test_invalid_bearer_is_rejected(self):
        res = self.client.get(self.url, headers={"Authorization": "Bearer invalid.synthetic.token"})
        self.assertEqual(res.status_code, 401)


if __name__ == "__main__":
    unittest.main()
