import unittest
from unittest.mock import Mock, patch

from jsm_asset_mcp.config import Settings


class SettingsDiscoveryTests(unittest.TestCase):
    def test_resolve_cloud_id_uses_timeout(self) -> None:
        response = Mock()
        response.json.return_value = {"cloudId": "cloud-123"}

        with patch("jsm_asset_mcp.config.httpx.get", return_value=response) as http_get:
            settings = Settings(jira_domain="example.atlassian.net")
            cloud_id = settings.resolve_cloud_id()

        self.assertEqual(cloud_id, "cloud-123")
        http_get.assert_called_once_with(
            "https://example.atlassian.net/_edge/tenant_info",
            timeout=30,
        )

    def test_resolve_workspace_id_uses_timeout(self) -> None:
        response = Mock()
        response.json.return_value = {"workspaceId": "workspace-123"}

        with patch("jsm_asset_mcp.config.httpx.get", return_value=response) as http_get:
            settings = Settings(
                jira_domain="example.atlassian.net",
                jira_email="user@example.com",
                jira_api_token="token",
            )
            workspace_id = settings.resolve_workspace_id()

        self.assertEqual(workspace_id, "workspace-123")
        http_get.assert_called_once_with(
            "https://example.atlassian.net/rest/servicedeskapi/assets/workspace",
            auth=("user@example.com", "token"),
            headers={"Accept": "application/json"},
            timeout=30,
        )
