"""Offline Blueprint regression gates: no production resource or committed secret."""
from pathlib import Path
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]


class IOSStagingBlueprintTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = (ROOT / "render-ios-qa.yaml").read_text(encoding="utf-8")
        cls.bp = yaml.safe_load(cls.raw)

    def test_single_isolated_free_service(self):
        services = self.bp.get("services", [])
        self.assertEqual(len(services), 1)
        self.assertNotIn("databases", self.bp)
        service = services[0]
        self.assertEqual(service["name"], "docwallet-ios-qa-isolated")
        self.assertEqual(service["plan"], "free")
        self.assertEqual(service["autoDeployTrigger"], "off")
        self.assertEqual(service["branch"], "test/ios-private-download-contract-20261117")
        self.assertIn("staging_ios_entry:app", service["startCommand"])

    def test_secrets_are_generated_not_committed(self):
        env = {x["key"]: x for x in self.bp["services"][0]["envVars"]}
        for secret in ("JWT_SECRET", "DOCWALLET_IOS_STAGING_KEY"):
            self.assertEqual(env[secret], {"key": secret, "generateValue": True})
        self.assertTrue(env["DATABASE_URL"]["value"].startswith("sqlite:////tmp/docwallet-ios-qa-"))
        self.assertTrue(env["UPLOAD_DIR"]["value"].startswith("/tmp/docwallet-ios-qa-"))
        self.assertEqual(env["DOCWALLET_IOS_STAGING_ONLY"]["value"], "1")

    def test_external_integrations_are_disabled(self):
        env = {x["key"]: x for x in self.bp["services"][0]["envVars"]}
        for disabled in ("NEXA_SSO_ENABLED", "NEXA_ECOSYSTEM_ATTENTION_ENABLED",
                         "NEXOFFICE_BRIDGE_ENABLED", "DOCFLOW_EXTERNAL_INTEGRATIONS_ENABLED",
                         "DOCUMENT_INTELLIGENCE_ALLOW_EXTERNAL"):
            self.assertEqual(env[disabled]["value"], "false")


if __name__ == "__main__":
    unittest.main()
