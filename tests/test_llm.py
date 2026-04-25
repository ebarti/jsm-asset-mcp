import unittest
from unittest.mock import patch

from jsm_asset_mcp.config import Settings
from jsm_asset_mcp.llm import get_client


class GetClientTests(unittest.TestCase):
    def test_direct_provider_uses_settings_api_key(self) -> None:
        settings = Settings(anthropic_provider="anthropic", anthropic_api_key="test-key")

        with patch("jsm_asset_mcp.llm.anthropic.Anthropic") as anthropic_client:
            get_client(settings)

        anthropic_client.assert_called_once_with(api_key="test-key")

    def test_direct_provider_requires_api_key(self) -> None:
        settings = Settings(anthropic_provider="anthropic")

        with self.assertRaisesRegex(ValueError, "ANTHROPIC_API_KEY"):
            get_client(settings)
