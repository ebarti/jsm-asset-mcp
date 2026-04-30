import unittest
from typing import Any
from unittest.mock import patch

from claude_agent_sdk import ResultMessage

from jsm_asset_mcp.config import Settings
from jsm_asset_mcp.llm import (
    AQL_QUERY_SCHEMA,
    AQL_SYSTEM_PROMPT,
    SEARCH_PLAN_SCHEMA,
    SEARCH_PLAN_SYSTEM_PROMPT,
    SearchPlan,
    translate_to_aql,
    translate_to_search_plan,
)


def _result_message(structured_output: object, subtype: str = "success") -> ResultMessage:
    return ResultMessage(
        subtype=subtype,
        duration_ms=0,
        duration_api_ms=0,
        is_error=subtype != "success",
        num_turns=1,
        session_id="test-session",
        stop_reason=None,
        total_cost_usd=None,
        usage=None,
        result=None,
        structured_output=structured_output,
    )


class QueryRecorder:
    def __init__(self, structured_output: object, subtype: str = "success") -> None:
        self.structured_output = structured_output
        self.subtype = subtype
        self.calls: list[dict[str, Any]] = []

    def __call__(self, *, prompt: str, options: object):
        self.calls.append({"prompt": prompt, "options": options})

        async def messages():
            yield _result_message(self.structured_output, self.subtype)

        return messages()


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
            "If the user asks to count objects",
            '`result_type` to "count"',
            "If the user explicitly asks for a specific number of results",
            "If the user does not specify a result limit",
            "default max_results parameter",
        ]

        self.assertNotIn("Respond with ONLY the AQL query string", SEARCH_PLAN_SYSTEM_PROMPT)
        for fragment in required_fragments:
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, SEARCH_PLAN_SYSTEM_PROMPT)

    def test_search_plan_schema_enforces_required_output_shape(self) -> None:
        self.assertEqual(
            SEARCH_PLAN_SCHEMA["required"],
            ["aql", "max_results", "fetch_all", "result_type"],
        )
        self.assertFalse(SEARCH_PLAN_SCHEMA["additionalProperties"])
        self.assertEqual(SEARCH_PLAN_SCHEMA["properties"]["aql"]["type"], "string")
        self.assertEqual(SEARCH_PLAN_SCHEMA["properties"]["fetch_all"]["type"], "boolean")
        self.assertEqual(SEARCH_PLAN_SCHEMA["properties"]["result_type"]["enum"], ["objects", "count"])


class TranslateToAqlTests(unittest.TestCase):
    def test_translate_to_aql_uses_agent_sdk_structured_output(self) -> None:
        settings = Settings(anthropic_provider="anthropic", anthropic_api_key="test-key")
        recorder = QueryRecorder({"aql": 'objectType = "Laptop"'})

        with patch("jsm_asset_mcp.llm.query", recorder):
            aql = translate_to_aql("find laptops", "schema", settings)

        self.assertEqual(aql, 'objectType = "Laptop"')
        call = recorder.calls[0]
        self.assertIn("Translate this question to AQL", call["prompt"])
        self.assertEqual(call["options"].model, "claude-opus-4-7")
        self.assertEqual(call["options"].allowed_tools, [])
        self.assertEqual(
            call["options"].output_format,
            {"type": "json_schema", "schema": AQL_QUERY_SCHEMA},
        )
        self.assertEqual(call["options"].env["ANTHROPIC_API_KEY"], "test-key")

    def test_translate_to_search_plan_uses_agent_sdk_output_format(self) -> None:
        settings = Settings(anthropic_provider="anthropic", anthropic_api_key="test-key")
        recorder = QueryRecorder(
            {
                "aql": 'objectType = "Laptop"',
                "max_results": 10,
                "fetch_all": False,
                "result_type": "objects",
            }
        )

        with patch("jsm_asset_mcp.llm.query", recorder):
            plan = translate_to_search_plan("show ten laptops", "schema", settings)

        self.assertEqual(
            plan,
            SearchPlan(aql='objectType = "Laptop"', max_results=10, result_type="objects"),
        )
        call = recorder.calls[0]
        self.assertEqual(
            call["options"].output_format,
            {"type": "json_schema", "schema": SEARCH_PLAN_SCHEMA},
        )
        self.assertNotIn("output_config", call)

    def test_translate_to_search_plan_sets_vertex_environment(self) -> None:
        settings = Settings(
            anthropic_provider="vertex",
            anthropic_vertex_project_id="test-project",
            anthropic_vertex_region="global",
        )
        recorder = QueryRecorder(
            {
                "aql": 'objectType = "Laptop"',
                "max_results": None,
                "fetch_all": False,
                "result_type": "objects",
            }
        )

        with patch("jsm_asset_mcp.llm.query", recorder):
            plan = translate_to_search_plan("show laptops", "schema", settings)

        self.assertEqual(plan, SearchPlan(aql='objectType = "Laptop"'))
        self.assertEqual(
            recorder.calls[0]["options"].env,
            {
                "CLAUDE_CODE_USE_VERTEX": "1",
                "ANTHROPIC_VERTEX_PROJECT_ID": "test-project",
                "CLOUD_ML_REGION": "global",
            },
        )

    def test_translate_to_search_plan_parses_fetch_all_response(self) -> None:
        settings = Settings(anthropic_provider="anthropic", anthropic_api_key="test-key")
        recorder = QueryRecorder(
            {
                "aql": 'objectType = "Laptop"',
                "max_results": None,
                "fetch_all": True,
                "result_type": "objects",
            }
        )

        with patch("jsm_asset_mcp.llm.query", recorder):
            plan = translate_to_search_plan("show all laptops", "schema", settings)

        self.assertEqual(plan, SearchPlan(aql='objectType = "Laptop"', fetch_all=True))

    def test_translate_to_search_plan_parses_count_response(self) -> None:
        settings = Settings(anthropic_provider="anthropic", anthropic_api_key="test-key")
        recorder = QueryRecorder(
            {
                "aql": 'objectType = "Laptop"',
                "max_results": None,
                "fetch_all": False,
                "result_type": "count",
            }
        )

        with patch("jsm_asset_mcp.llm.query", recorder):
            plan = translate_to_search_plan("how many laptops", "schema", settings)

        self.assertEqual(plan, SearchPlan(aql='objectType = "Laptop"', result_type="count"))

    def test_translate_to_search_plan_rejects_extra_fields(self) -> None:
        settings = Settings(anthropic_provider="anthropic", anthropic_api_key="test-key")
        recorder = QueryRecorder(
            {
                "aql": 'objectType = "Laptop"',
                "max_results": None,
                "fetch_all": False,
                "result_type": "objects",
                "unexpected": "value",
            }
        )

        with patch("jsm_asset_mcp.llm.query", recorder):
            with self.assertRaisesRegex(ValueError, "unexpected search plan fields"):
                translate_to_search_plan("show laptops", "schema", settings)

    def test_translate_to_search_plan_requires_direct_api_key(self) -> None:
        settings = Settings(anthropic_provider="anthropic")

        with self.assertRaisesRegex(ValueError, "ANTHROPIC_API_KEY"):
            translate_to_search_plan("show laptops", "schema", settings)
