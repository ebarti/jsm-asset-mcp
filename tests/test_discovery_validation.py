"""JIRA_DOMAIN, cloud ID and workspace ID validation before authenticated requests."""

import unittest
from unittest.mock import Mock, patch

import httpx

from jsm_asset_mcp.config import Settings

CLOUD = "11111111-2222-3333-4444-555555555555"
WORKSPACE = "66666666-7777-8888-9999-aaaaaaaaaaaa"


class JiraDomainTests(unittest.TestCase):
    def test_valid_domain_is_normalised(self) -> None:
        with patch.dict("os.environ", {"JIRA_DOMAIN": " Example.Atlassian.NET "}, clear=True):
            self.assertEqual(Settings.from_env().jira_domain, "example.atlassian.net")

    def test_invalid_domains_are_rejected_at_startup(self) -> None:
        for raw in (
            "evil.example.com",
            "example.atlassian.net.evil.com",
            "https://example.atlassian.net",
            "example.atlassian.net/rest",
            "example.atlassian.net:8443",
            "user@example.atlassian.net",
            "atlassian.net",
            "-bad.atlassian.net",
        ):
            with self.subTest(raw=raw), patch.dict("os.environ", {"JIRA_DOMAIN": raw}, clear=True):
                with self.assertRaisesRegex(ValueError, "JIRA_DOMAIN"):
                    Settings.from_env()

    def test_cloud_id_discovery_refuses_invalid_domain_without_request(self) -> None:
        settings = Settings(jira_domain="evil.example.com")
        with patch("jsm_asset_mcp.config.httpx.get") as http_get:
            with self.assertRaisesRegex(ValueError, "JIRA_DOMAIN"):
                settings.resolve_cloud_id()
        http_get.assert_not_called()

    def test_legacy_fallback_never_sends_credentials_to_invalid_domain(self) -> None:
        # Settings built directly bypass from_env; the site fallback must still refuse.
        denied = httpx.Response(401, request=httpx.Request("GET", "https://api.atlassian.com/x"))
        gateway = Mock()
        gateway.raise_for_status.side_effect = httpx.HTTPStatusError("401", request=denied.request, response=denied)
        settings = Settings(
            jira_domain="evil.example.com", jira_email="me@example.com", jira_api_token="t", jira_cloud_id=CLOUD
        )
        with patch("jsm_asset_mcp.config.httpx.get", return_value=gateway) as http_get:
            with self.assertRaisesRegex(ValueError, "JIRA_DOMAIN"):
                settings.resolve_workspace_id()
        self.assertEqual(http_get.call_count, 1)  # only the gateway, never the invalid host
        self.assertTrue(http_get.call_args.args[0].startswith("https://api.atlassian.com/"))


class DiscoveryIdTests(unittest.TestCase):
    def test_ids_from_environment_must_be_uuids(self) -> None:
        for name in ("JIRA_CLOUD_ID", "JIRA_WORKSPACE_ID"):
            for raw in ("abc", f"{CLOUD}/../x", f"{CLOUD}?q=1"):
                with self.subTest(name=name, raw=raw), patch.dict("os.environ", {name: raw}, clear=True):
                    with self.assertRaisesRegex(ValueError, name):
                        Settings.from_env()
        with patch.dict("os.environ", {"JIRA_CLOUD_ID": CLOUD, "JIRA_WORKSPACE_ID": WORKSPACE}, clear=True):
            settings = Settings.from_env()
        self.assertEqual((settings.jira_cloud_id, settings.jira_workspace_id), (CLOUD, WORKSPACE))

    def test_discovered_cloud_id_must_be_a_uuid_before_the_gateway_call(self) -> None:
        response = Mock()
        response.json.return_value = {"cloudId": "x/../../other"}
        settings = Settings(jira_domain="example.atlassian.net", jira_email="me@example.com", jira_api_token="t")
        with patch("jsm_asset_mcp.config.httpx.get", return_value=response) as http_get:
            with self.assertRaisesRegex(ValueError, "cloudId"):
                settings.resolve_workspace_id()
        self.assertEqual(http_get.call_count, 1)  # tenant_info only; no authenticated call
        self.assertEqual(settings.jira_cloud_id, "")

    def test_discovered_workspace_id_must_be_a_uuid(self) -> None:
        response = Mock()
        response.json.return_value = {"values": [{"workspaceId": "ws?evil=1"}]}
        settings = Settings(jira_email="me@example.com", jira_api_token="t", jira_cloud_id=CLOUD)
        with patch("jsm_asset_mcp.config.httpx.get", return_value=response):
            with self.assertRaisesRegex(ValueError, "workspaceId"):
                settings.resolve_workspace_id()
        self.assertEqual(settings.jira_workspace_id, "")


if __name__ == "__main__":
    unittest.main()
