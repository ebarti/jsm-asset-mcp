import unittest
from types import SimpleNamespace
from unittest.mock import patch

from jsm_asset_mcp.config import Settings
from jsm_asset_mcp.llm import (
    AQL_SYSTEM_PROMPT,
    SEARCH_PLAN_SCHEMA,
    SEARCH_PLAN_SYSTEM_PROMPT,
    SearchPlan,
    get_client,
    translate_to_aql,
    translate_to_search_plan,
)


class FakeMessages:
    def __init__(self, content: list[object]) -> None:
        self.content = content
        self.calls: list[dict] = []

    def create(self, **kwargs) -> SimpleNamespace:
        self.calls.append(kwargs)
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


class AqlSystemPromptTests(unittest.TestCase):
    def test_prompt_covers_atlassian_aql_syntax_surface(self) -> None:
        required_fragments = [
            '<attribute-or-keyword> <operator> <value-or-function>',
            'Name = "15\\" Screen"',
            '"Belongs to Department".Name = "HR"',
            "objectSchemaId IN (1, 2)",
            "objectTypeId IN (1, 2)",
            'Key = "ITSM-1111"',
            "`==`: case-sensitive equality",
            "`STARTSWITH` / `ENDSWITH`",
            "`HAVING` / `NOT HAVING`",
            "currentUser()",
            "currentReporter()",
            'user("admin", "manager")',
            'group("jira-users")',
            "currentProject()",
            "inboundReferences(AQL)",
            "inR(AQL, refTypes)",
            "outboundReferences(AQL)",
            "outR(AQL, refTypes)",
            "connectedTickets(JQL query)",
            "connectedTickets()",
            "objectTypeAndChildren(Name)",
            "${MyCustomField${0}}",
            "ORDER BY <AttributeName|label> ASC|DESC",
            "Reference function AQL arguments can contain other reference functions",
            "Do not invent object schema IDs or object type IDs",
            "Only one order attribute is supported",
        ]

        for fragment in required_fragments:
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, AQL_SYSTEM_PROMPT)

    def test_search_plan_prompt_asks_llm_for_result_limit_semantics(self) -> None:
        required_fragments = [
            "If the user asks for all, every, complete, full, unlimited, or no-limit results",
            "If the user explicitly asks for a specific number of results",
            "If the user does not specify a result limit",
            "The caller will use its default max_results parameter",
        ]

        self.assertNotIn("Respond with ONLY the AQL query string", SEARCH_PLAN_SYSTEM_PROMPT)
        for fragment in required_fragments:
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, SEARCH_PLAN_SYSTEM_PROMPT)

    def test_search_plan_schema_enforces_required_output_shape(self) -> None:
        self.assertEqual(SEARCH_PLAN_SCHEMA["required"], ["aql", "max_results", "fetch_all"])
        self.assertFalse(SEARCH_PLAN_SCHEMA["additionalProperties"])
        self.assertEqual(SEARCH_PLAN_SCHEMA["properties"]["aql"]["type"], "string")
        self.assertEqual(SEARCH_PLAN_SCHEMA["properties"]["fetch_all"]["type"], "boolean")


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

    def test_translate_to_search_plan_parses_json_response(self) -> None:
        settings = Settings(anthropic_provider="anthropic", anthropic_api_key="test-key")
        messages = FakeMessages(
            [
                SimpleNamespace(
                    text='{"aql": "objectType = \\"Laptop\\"", "max_results": 10, "fetch_all": false}'
                )
            ]
        )
        client = SimpleNamespace(messages=messages)

        with patch("jsm_asset_mcp.llm.get_client", return_value=client):
            plan = translate_to_search_plan("show ten laptops", "schema", settings)

        self.assertEqual(plan, SearchPlan(aql='objectType = "Laptop"', max_results=10))
        self.assertEqual(
            messages.calls[0]["output_config"],
            {"format": {"type": "json_schema", "schema": SEARCH_PLAN_SCHEMA}},
        )

    def test_translate_to_search_plan_parses_fetch_all_response(self) -> None:
        settings = Settings(anthropic_provider="anthropic", anthropic_api_key="test-key")
        client = SimpleNamespace(
            messages=FakeMessages(
                [
                    SimpleNamespace(
                        text='```json\n{"aql": "objectType = \\"Laptop\\"", "max_results": null, "fetch_all": true}\n```'
                    )
                ]
            )
        )

        with patch("jsm_asset_mcp.llm.get_client", return_value=client):
            plan = translate_to_search_plan("show all laptops", "schema", settings)

        self.assertEqual(plan, SearchPlan(aql='objectType = "Laptop"', fetch_all=True))
