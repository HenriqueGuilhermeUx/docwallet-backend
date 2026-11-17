"""Fail-closed staging guard tests; synthetic and never connected to Render."""
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
_SANDBOX = tempfile.TemporaryDirectory(prefix="docwallet-ios-qa-")
os.environ.update({
    "DOCWALLET_IOS_STAGING_ONLY": "1",
    "DOCWALLET_IOS_STAGING_KEY": "synthetic-qa-access-key-32-plus-chars-not-prod",
    "JWT_SECRET": "synthetic-qa-jwt-secret-32-plus-chars-not-prod",
    "DATABASE_URL": "sqlite:////tmp/docwallet-ios-qa-test-contract.sqlite3",
    "UPLOAD_DIR": "/tmp/docwallet-ios-qa-test-storage",
    "NEXA_SSO_ENABLED": "false",
    "NEXA_ECOSYSTEM_ATTENTION_ENABLED": "false",
    "NEXOFFICE_BRIDGE_ENABLED": "false",
    "DOCFLOW_EXTERNAL_INTEGRATIONS_ENABLED": "false",
    "DOCUMENT_INTELLIGENCE_ALLOW_EXTERNAL": "false",
})
from staging_ios_entry import app  # noqa: E402
from werkzeug.test import Client  # noqa: E402


class StagingGateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = Client(app)

    def test_health_discloses_only_isolated_status(self):
        result = self.client.get("/api/health")
        self.assertEqual(result.status_code, 200)
        self.assertIn(b'"isolated":true', result.data)

    def test_unprotected_api_is_404(self):
        result = self.client.get("/api/documents")
        self.assertEqual(result.status_code, 404)

    def test_wrong_staging_key_is_404(self):
        result = self.client.get("/api/documents", headers={
            "X-DocWallet-Staging-Key": "wrong-test-key",
        })
        self.assertEqual(result.status_code, 404)

    def test_valid_staging_key_reaches_bearer_auth_gate(self):
        result = self.client.get("/api/documents", headers={
            "X-DocWallet-Staging-Key": os.environ["DOCWALLET_IOS_STAGING_KEY"],
        })
        self.assertEqual(result.status_code, 401)


if __name__ == "__main__":
    unittest.main()
