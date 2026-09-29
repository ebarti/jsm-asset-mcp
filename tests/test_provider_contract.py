"""Offline contracts for each advertised LLM_PROVIDER through its real kit adapter.

Vendor SDK boundaries are fake; the application and agent-runtime-kit adapters
remain real. CI also runs each provider class independently via the CLI below.
"""

import argparse
import json
import os
import sys
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from claude_agent_sdk import AssistantMessage, ResultMessage, ToolUseBlock

from jsm_asset_mcp.config import Settings
from jsm_asset_mcp.llm import SearchPlan, translate_to_aql, translate_to_search_plan


PROVIDERS = (
    "anthropic",
    "anthropic-vertex",
    "anthropic-bedrock",
    "gemini",
    "antigravity",
    "codex",
)
AQL = 'objectType = "Laptop"'
PLAN = {"aql": AQL, "max_results": None, "fetch_all": False, "result_type": "objects"}


def _settings(provider):
    return Settings(
        llm_provider=provider,
        llm_model="synthetic-model",
        anthropic_api_key="synthetic-key",
        anthropic_vertex_project_id="synthetic-project",
        aws_region="eu-west-1",
        gemini_api_key="synthetic-key",
    )


def _claude_result(payload, *, error=False):
    return ResultMessage(
        subtype="error_during_execution" if error else "success",
        duration_ms=0,
        duration_api_ms=0,
        is_error=error,
        num_turns=1,
        session_id="synthetic-session",
        structured_output=payload,
    )


@contextmanager
def _fake_vendor(provider, payload, *, failure=False, tool_name="StructuredOutput"):
    """Replace only the SDK I/O boundary; capture actual adapter options."""
    observed = []
    if provider.startswith("anthropic"):
        async def query(*, prompt, options):
            observed.append((prompt, options))
            if not failure:
                # SDK output_schema replies use a built-in tool followed by a
                # parsed ResultMessage. A lone ResultMessage misses the bug.
                yield AssistantMessage(
                    content=[ToolUseBlock(id="structured-1", name=tool_name, input=payload or {})],
                    model="synthetic-model",
                )
            yield _claude_result(None if failure else payload, error=failure)

        with patch("claude_agent_sdk.query", query):
            yield observed
    elif provider in {"gemini", "antigravity"}:
        class FakeAgent:
            def __init__(self, config):
                observed.append(config)
                self.conversation_id = "synthetic-session"

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def chat(self, goal):
                if failure:
                    raise RuntimeError("synthetic provider failure")

                async def chunks():
                    if False:
                        yield None

                return SimpleNamespace(chunks=chunks(), structured_output=lambda: payload)

        with patch("google.antigravity.agent.Agent", FakeAgent):
            yield observed
    else:
        class FakeClient:
            async def thread_start(self, params):
                observed.append(params)
                return SimpleNamespace(thread=SimpleNamespace(id="synthetic-thread"))

        class FakeCodex:
            def __init__(self, config):
                observed.append(config)
                self._client = FakeClient()

            async def _ensure_initialized(self):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        async def fake_run(thread, goal, *, cwd=None, approval_mode=None, sandbox=None,
                           model=None, output_schema=None):
            return SimpleNamespace(
                final_response=None if failure else json.dumps(payload),
                status="failed" if failure else "completed",
                items=[],
                usage=None,
            )

        with patch.dict(os.environ, {"OPENAI_API_KEY": "synthetic-key"}), \
                patch("openai_codex.AsyncCodex", FakeCodex), \
                patch("openai_codex.api.AsyncThread.run", fake_run):
            yield observed


class ProviderContract:
    provider: str

    def _translate(self, payload, *, plan=True, failure=False, tool_name="StructuredOutput"):
        settings = _settings(self.provider)
        with _fake_vendor(self.provider, payload, failure=failure, tool_name=tool_name) as observed:
            result = (
                translate_to_search_plan("find laptops", "synthetic schema", settings)
                if plan else translate_to_aql("find laptops", "synthetic schema", settings)
            )
        return result, observed

    def test_aql_count_and_fetch_all_through_adapter(self):
        aql, _ = self._translate({"aql": AQL}, plan=False)
        self.assertEqual(aql, AQL)
        for payload, expected in (
            (PLAN, SearchPlan(aql=AQL)),
            ({**PLAN, "result_type": "count"}, SearchPlan(aql=AQL, result_type="count")),
            ({**PLAN, "fetch_all": True}, SearchPlan(aql=AQL, fetch_all=True)),
            ({**PLAN, "max_results": 7}, SearchPlan(aql=AQL, max_results=7)),
        ):
            with self.subTest(payload=payload):
                actual, _ = self._translate(payload)
                self.assertEqual(actual, expected)

    def test_invalid_schema_and_provider_failure(self):
        with self.assertRaisesRegex(ValueError, "non-empty"):
            self._translate({**PLAN, "aql": ""})
        with self.assertRaisesRegex(ValueError, "fetch_all plan cannot also set a result limit"):
            self._translate({**PLAN, "fetch_all": True, "max_results": 2})
        if self.provider == "gemini" or self.provider == "antigravity":
            with self.assertRaisesRegex(RuntimeError, "synthetic provider failure"):
                self._translate(PLAN, failure=True)
        else:
            with self.assertRaisesRegex(ValueError, "translation failed"):
                self._translate(PLAN, failure=True)

    def test_auth_model_and_no_local_tools(self):
        plan, observed = self._translate(PLAN)
        self.assertEqual(plan, SearchPlan(aql=AQL))
        if self.provider.startswith("anthropic"):
            prompt, options = observed[0]
            self.assertIn("synthetic schema", prompt)
            self.assertEqual(options.model, "synthetic-model")
            auth_key = {
                "anthropic": "ANTHROPIC_API_KEY",
                "anthropic-vertex": "CLAUDE_CODE_USE_VERTEX",
                "anthropic-bedrock": "CLAUDE_CODE_USE_BEDROCK",
            }[self.provider]
            self.assertIn(auth_key, options.env)
            self.assertEqual(options.tools, [])
            self.assertEqual(options.mcp_servers, {})
            self.assertEqual(options.plugins, [])
            self.assertEqual(options.hooks, {})
        elif self.provider in {"gemini", "antigravity"}:
            config = observed[0]
            self.assertEqual(config.model, "synthetic-model")
            self.assertEqual(config.api_key, "synthetic-key")
            self.assertEqual(config.capabilities.enabled_tools, [])
            self.assertFalse(config.capabilities.enable_subagents)
            self.assertEqual(config.tools, [])
            self.assertEqual(config.mcp_servers, [])
        else:
            config, thread = observed
            self.assertEqual(thread["model"], "synthetic-model")
            self.assertEqual(thread["environments"], [])
            self.assertEqual(thread["sandbox"], "read-only")
            self.assertIn("features.shell_tool=false", config.config_overrides)
            self.assertIn("web_search=disabled", config.config_overrides)

def _reject_unadvertised_claude_tool(self):
    with self.assertRaisesRegex(ValueError, "unexpectedly reported tool use"):
        self._translate(PLAN, tool_name="Read")


TEST_CLASSES = {}
for _provider in PROVIDERS:
    _name = "Provider_" + _provider.replace("-", "_")
    _attributes = {
        "provider": _provider,
        "__module__": __name__,
    }
    if _provider.startswith("anthropic"):
        _attributes["test_unadvertised_claude_tool_is_rejected"] = _reject_unadvertised_claude_tool
    _class = type(_name, (ProviderContract, unittest.TestCase), _attributes)
    globals()[_name] = _class
    TEST_CLASSES[_provider] = _class
del _provider, _name, _class, _attributes


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run one offline LLM provider contract")
    parser.add_argument("--provider", required=True, choices=PROVIDERS)
    args = parser.parse_args()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TEST_CLASSES[args.provider])
    print(f"Selected LLM_PROVIDER={args.provider}: {suite.countTestCases()} tests", flush=True)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(not result.wasSuccessful())
