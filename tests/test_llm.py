import unittest
from types import SimpleNamespace
from unittest.mock import patch

from jsm_asset_mcp.config import Settings
from jsm_asset_mcp.llm import get_client, translate_to_aql


class FakeMessages:
    def __init__(self, content: list[object]) -> None:
        self.content = content

    def create(self, **_kwargs) -> SimpleNamespace:
        return SimpleNamespace(content=self.content)


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


class TranslateToAqlTests(unittest.TestCase):
    def test_strips_markdown_code_fence_from_response(self) -> None:
        settings = Settings(anthropic_provider="anthropic", anthropic_api_key="test-key")
        client = SimpleNamespace(
            messages=FakeMessages(
                [SimpleNamespace(text='```aql\nobjectType = "Laptop"\n```')]
            )
        )

        with patch("jsm_asset_mcp.llm.get_client", return_value=client):
            aql = translate_to_aql("find laptops", "schema", settings)

        self.assertEqual(aql, 'objectType = "Laptop"')

    def test_combines_multiple_text_blocks_before_parsing(self) -> None:
        settings = Settings(anthropic_provider="anthropic", anthropic_api_key="test-key")
        client = SimpleNamespace(
            messages=FakeMessages(
                [
                    SimpleNamespace(text='```aql\nName LIKE "prod"'),
                    SimpleNamespace(text="```"),
                ]
            )
        )

        with patch("jsm_asset_mcp.llm.get_client", return_value=client):
            aql = translate_to_aql("find prod assets", "schema", settings)

        self.assertEqual(aql, 'Name LIKE "prod"')
