import unittest
from unittest.mock import Mock, patch

import httpx

from jsm_asset_mcp.config import Settings


class SettingsDiscoveryTests(unittest.TestCase):
    def test_default_model_names_use_claude_opus_4_7_only(self) -> None:
        self.assertEqual(Settings(llm_provider="anthropic").model_name, "claude-opus-4-7")
        self.assertEqual(Settings(llm_provider="anthropic-vertex").model_name, "claude-opus-4-7")
        self.assertEqual(
            Settings(llm_provider="anthropic-bedrock").model_name,
            "anthropic.claude-opus-4-7",
        )

    def test_gemini_provider_model_defaults_to_gemini_2_5_pro(self) -> None:
        self.assertEqual(Settings(llm_provider="gemini").model_name, "gemini-2.5-pro")

    def test_unknown_provider_model_falls_back_to_claude_opus_4_7(self) -> None:
        self.assertEqual(Settings(llm_provider="unknown").model_name, "claude-opus-4-7")

    def test_vertex_region_defaults_to_global(self) -> None:
        self.assertEqual(Settings().anthropic_vertex_region, "global")

    def test_from_env_reads_llm_provider_and_gemini_api_key(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "LLM_PROVIDER": "GEMINI",
                "GEMINI_API_KEY": "test-gemini-key",
            },
            clear=True,
        ):
            settings = Settings.from_env()

        self.assertEqual(settings.llm_provider, "gemini")
        self.assertEqual(settings.active_llm_provider, "gemini")
        self.assertEqual(settings.gemini_api_key, "test-gemini-key")


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

    def test_resolve_workspace_id_uses_cloud_gateway(self) -> None:
        response = Mock()
        response.json.return_value = {"workspaceId": "workspace-123"}

        with patch("jsm_asset_mcp.config.httpx.get", return_value=response) as http_get:
            settings = Settings(
                jira_cloud_id="cloud-123",
                jira_email="user@example.com",
                jira_api_token="token",
            )
            workspace_id = settings.resolve_workspace_id()

        self.assertEqual(workspace_id, "workspace-123")
        http_get.assert_called_once_with(
            "https://api.atlassian.com/ex/jira/cloud-123/rest/servicedeskapi/assets/workspace",
            auth=("user@example.com", "token"),
            headers={"Accept": "application/json"},
            timeout=30,
        )

    def test_workspace_id_bypasses_discovery_when_explicit(self) -> None:
        with patch("jsm_asset_mcp.config.httpx.get") as http_get:
            settings = Settings(jira_workspace_id="workspace-123")
            self.assertEqual(settings.resolve_workspace_id(), "workspace-123")
        http_get.assert_not_called()

    def test_legacy_site_route_remains_available_for_unscoped_tokens(self) -> None:
        request = httpx.Request(
            "GET", "https://api.atlassian.com/ex/jira/cloud-123/rest/servicedeskapi/assets/workspace"
        )
        gateway_response = httpx.Response(401, request=request)
        legacy_response = Mock()
        legacy_response.json.return_value = {"values": [{"workspaceId": "workspace-123"}]}

        with patch("jsm_asset_mcp.config.httpx.get", side_effect=[gateway_response, legacy_response]) as http_get:
            settings = Settings(
                jira_domain="example.atlassian.net",
                jira_cloud_id="cloud-123",
                jira_email="user@example.com",
                jira_api_token="token",
            )
            self.assertEqual(settings.resolve_workspace_id(), "workspace-123")

        self.assertEqual(http_get.call_count, 2)
        self.assertEqual(
            http_get.call_args_list[1].args[0],
            "https://example.atlassian.net/rest/servicedeskapi/assets/workspace",
        )
