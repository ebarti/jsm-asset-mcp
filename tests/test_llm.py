"""AQL translation contracts at the published kit/vendor adapter boundary."""

import asyncio
import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from agent_runtime_kit import AgentResult, AgentRuntimeKind, AgentTaskTimeoutError, ToolCallAudit
from claude_agent_sdk import ResultMessage
from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

from jsm_asset_mcp.config import Settings
from jsm_asset_mcp.llm import (
    AQL_QUERY_SCHEMA,
    AQL_SYSTEM_PROMPT,
    SEARCH_PLAN_SCHEMA,
    SEARCH_PLAN_SYSTEM_PROMPT,
    SearchPlan,
    _build_runtime,
    _query_structured_output,
    _to_gemini_schema,
    translate_to_aql,
    translate_to_search_plan,
)


AQL = 'objectType = "Laptop"'
PLAN = {"aql": AQL, "max_results": None, "fetch_all": False, "result_type": "objects"}


def _claude_message(payload, *, error=False):
    return ResultMessage(
        subtype="error_during_execution" if error else "success",
        duration_ms=0,
        duration_api_ms=0,
        is_error=error,
        num_turns=1,
        session_id="test-session",
        stop_reason=None,
        total_cost_usd=None,
        usage=None,
        result=None,
        structured_output=payload,
    )


class TranslationPromptTests(unittest.TestCase):
    def test_prompt_and_schema_keep_aql_and_count_contracts(self):
        for fragment in ('STARTSWITH', 'objectSchemaId IN (1, 2)', 'inboundReferences(AQL)', 'connectedTickets()'):
            self.assertIn(fragment, AQL_SYSTEM_PROMPT)
        self.assertIn('`result_type` to "count"', SEARCH_PLAN_SYSTEM_PROMPT)
        self.assertEqual(SEARCH_PLAN_SCHEMA["required"], ["aql", "max_results", "fetch_all", "result_type"])
        self.assertFalse(AQL_QUERY_SCHEMA["additionalProperties"])

    def test_google_response_schema_dialect(self):
        result = _to_gemini_schema(SEARCH_PLAN_SCHEMA)
        self.assertTrue(result["properties"]["max_results"]["nullable"])
        self.assertNotIn("additionalProperties", result)


class ClaudeAdapterTests(unittest.TestCase):
    def _run(self, settings, payload=PLAN, *, question="find laptops"):
        calls = []

        async def query(*, prompt, options):
            calls.append((prompt, options))
            yield _claude_message(payload)

        with patch("claude_agent_sdk.query", query):
            result = translate_to_search_plan(question, "untrusted schema", settings)
        return result, calls[0]

    def test_all_claude_auth_aliases_use_kit_with_no_local_capabilities(self):
        cases = [
            (Settings(llm_provider="anthropic", anthropic_api_key="test-key"), "ANTHROPIC_API_KEY"),
            (Settings(llm_provider="anthropic-vertex", anthropic_vertex_project_id="test-project"), "CLAUDE_CODE_USE_VERTEX"),
            (Settings(llm_provider="anthropic-bedrock", aws_region="eu-west-1"), "CLAUDE_CODE_USE_BEDROCK"),
        ]
        for settings, auth_key in cases:
            with self.subTest(provider=settings.active_llm_provider):
                plan, (prompt, options) = self._run(settings)
                self.assertEqual(plan, SearchPlan(aql=AQL))
                self.assertIn("untrusted schema", prompt)
                self.assertEqual(options.model, settings.model_name)
                self.assertIsNone(options.model)
                self.assertIn(auth_key, options.env)
                self.assertEqual(options.tools, [])
                self.assertEqual(options.allowed_tools, [])
                self.assertEqual(options.mcp_servers, {})
                self.assertEqual(options.setting_sources, [])
                self.assertEqual(options.skills, [])
                self.assertEqual(options.plugins, [])
                self.assertEqual(options.hooks, {})
                self.assertEqual(options.agents, {})
                self.assertTrue(options.strict_mcp_config)
                options.cli_path = "claude"
                command = SubprocessCLITransport("probe", options)._build_command()
                self.assertEqual(command[command.index("--tools") + 1], "")
                self.assertIn("--strict-mcp-config", command)
                self.assertIn("--setting-sources=", command)
                self.assertIn("--bare", command)
                self.assertNotIn("--mcp-config", command)
                self.assertNotIn("--plugin-dir", command)
                self.assertNotIn("--model", command)

    def test_custom_model_and_aql_result_for_all_claude_aliases(self):
        cases = [
            Settings(llm_provider="anthropic", anthropic_api_key="test-key", llm_model="claude-custom"),
            Settings(llm_provider="anthropic-vertex", anthropic_vertex_project_id="test-project", llm_model="claude-custom"),
            Settings(llm_provider="anthropic-bedrock", llm_model="bedrock-custom"),
        ]
        for settings in cases:
            with self.subTest(provider=settings.active_llm_provider):
                calls = []

                async def query(*, prompt, options):
                    calls.append(options)
                    yield _claude_message({"aql": AQL})

                with patch("claude_agent_sdk.query", query):
                    self.assertEqual(translate_to_aql("find laptops", "schema", settings), AQL)
                self.assertEqual(calls[0].model, settings.llm_model)
                calls[0].cli_path = "claude"
                command = SubprocessCLITransport("probe", calls[0])._build_command()
                self.assertEqual(command[command.index("--model") + 1], settings.llm_model)

    def test_count_and_fetch_all_plans_preserve_search_semantics(self):
        settings = Settings(llm_provider="anthropic", anthropic_api_key="test-key")
        count, _ = self._run(settings, {**PLAN, "result_type": "count"})
        all_objects, _ = self._run(settings, {**PLAN, "fetch_all": True})
        self.assertEqual(count, SearchPlan(aql=AQL, result_type="count"))
        self.assertEqual(all_objects, SearchPlan(aql=AQL, fetch_all=True))

    def test_invalid_payload_and_vendor_failure_rejected(self):
        for payload in (None, {"aql": ""}, {**PLAN, "extra": 1}, {**PLAN, "max_results": 0},
                        {**PLAN, "fetch_all": "true"}, {**PLAN, "result_type": "other"},
                        {**PLAN, "result_type": "count", "max_results": 2},
                        {**PLAN, "fetch_all": True, "max_results": 2}):
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    self._run(Settings(llm_provider="anthropic", anthropic_api_key="test-key"), payload)

        async def error_query(*, prompt, options):
            yield _claude_message(None, error=True)

        with patch("claude_agent_sdk.query", error_query):
            with self.assertRaisesRegex(ValueError, "translation failed"):
                translate_to_aql("find", "schema", Settings(llm_provider="anthropic", anthropic_api_key="test-key"))

    def test_missing_api_key_and_extra_fail_actionably(self):
        with self.assertRaisesRegex(ValueError, "ANTHROPIC_API_KEY"):
            translate_to_aql("find", "schema", Settings(llm_provider="anthropic"))
        with patch.dict("sys.modules", {"claude_agent_sdk": None}):
            with self.assertRaisesRegex(ImportError, "claude.*extra"):
                _build_runtime(Settings(llm_provider="anthropic", anthropic_api_key="test"), Path("/tmp"))


class AntigravityAdapterTests(unittest.TestCase):
    def _run(self, provider, payload=PLAN, *, model=""):
        configs = []
        exits = []

        class FakeAgent:
            def __init__(self, config):
                configs.append(config)
                self.conversation_id = "synthetic-session"

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                exits.append(True)

            async def chat(self, goal):
                async def chunks():
                    if False:
                        yield None

                return SimpleNamespace(chunks=chunks(), structured_output=lambda: payload)

        with patch("google.antigravity.agent.Agent", FakeAgent):
            plan = translate_to_search_plan("find laptops", "untrusted schema", Settings(
                llm_provider=provider, gemini_api_key="test-key", llm_model=model
            ))
        return plan, configs[0], exits

    def test_gemini_and_antigravity_use_tool_free_vendor_config_and_cleanup(self):
        for provider in ("gemini", "antigravity"):
            with self.subTest(provider=provider):
                plan, config, exits = self._run(provider)
                self.assertEqual(plan, SearchPlan(aql=AQL))
                self.assertEqual(config.capabilities.enabled_tools, [])
                self.assertFalse(config.capabilities.enable_subagents)
                self.assertEqual(config.mcp_servers, [])
                self.assertEqual(config.workspaces, [])
                self.assertEqual(config.tools, [])
                self.assertEqual(config.hooks, [])
                self.assertEqual(config.triggers, [])
                self.assertEqual(config.skills_paths, [])
                self.assertEqual(config.api_key, "test-key")
                self.assertIsNone(config.model)
                self.assertEqual(exits, [True])
                self.assertTrue(json.loads(config.response_schema)["properties"]["max_results"]["nullable"])

    def test_custom_model_and_bad_structured_result(self):
        _, config, _ = self._run("antigravity", model="gemini-custom")
        self.assertEqual(config.model, "gemini-custom")
        with self.assertRaises(ValueError):
            self._run("gemini", {"aql": "", "max_results": None, "fetch_all": False, "result_type": "objects"})

    def test_missing_key_and_extra_fail_actionably(self):
        with self.assertRaisesRegex(ValueError, "GEMINI_API_KEY"):
            translate_to_aql("find", "schema", Settings(llm_provider="gemini"))
        with patch.dict("sys.modules", {"google.antigravity": None}):
            with self.assertRaisesRegex(ImportError, "gemini.*extra"):
                _build_runtime(Settings(llm_provider="gemini", gemini_api_key="test"), Path("/tmp"))


class CodexAdapterTests(unittest.TestCase):
    def test_raw_pinned_thread_start_disables_environments_and_closes(self):
        starts = []
        exits = []
        configs = []

        class FakeClient:
            async def thread_start(self, params):
                starts.append(params)
                return SimpleNamespace(thread=SimpleNamespace(id="synthetic-thread"))

        class FakeCodex:
            def __init__(self, config):
                configs.append(config)
                self._client = FakeClient()

            async def _ensure_initialized(self):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                exits.append(True)

        async def fake_run(thread, goal, *, cwd=None, approval_mode=None, sandbox=None,
                           model=None, output_schema=None):
            return SimpleNamespace(final_response=json.dumps(PLAN), status="completed", items=[], usage=None)

        with patch.dict("os.environ", {"OPENAI_API_KEY": "synthetic-key"}), \
                patch("openai_codex.AsyncCodex", FakeCodex), \
                patch("openai_codex.api.AsyncThread.run", fake_run):
            plan = translate_to_search_plan("find laptops", "untrusted schema", Settings(llm_provider="codex", llm_model="gpt-custom"))
        self.assertEqual(plan, SearchPlan(aql=AQL))
        self.assertEqual(exits, [True])
        self.assertEqual(starts[0]["environments"], [])
        self.assertTrue(starts[0]["ephemeral"])
        self.assertEqual(starts[0]["model"], "gpt-custom")
        self.assertEqual(starts[0]["sandbox"], "read-only")
        self.assertEqual(configs[0].experimental_api, True)
        self.assertIn("features.plugins=false", configs[0].config_overrides)
        self.assertIn("web_search=disabled", configs[0].config_overrides)
        self.assertIn("CODEX_HOME", configs[0].env)
        self.assertFalse(Path(configs[0].env["CODEX_HOME"]).exists())

    def test_pinned_sdk_required(self):
        with patch.dict("os.environ", {"OPENAI_API_KEY": "synthetic-key"}), \
                patch("jsm_asset_mcp.llm.importlib.metadata.version", return_value="0.155.0"):
            with TemporaryDirectory() as temp:
                with self.assertRaisesRegex(RuntimeError, "0.154.0"):
                    _build_runtime(Settings(llm_provider="codex"), Path(temp))

    def test_missing_extra_is_actionable(self):
        with patch.dict("os.environ", {"OPENAI_API_KEY": "synthetic-key"}), \
                patch.dict("sys.modules", {"openai_codex": None}):
            with TemporaryDirectory() as temp:
                with self.assertRaisesRegex(ImportError, "codex.*extra"):
                    _build_runtime(Settings(llm_provider="codex"), Path(temp))

    def test_isolated_codex_requires_explicit_api_key(self):
        with patch.dict("os.environ", {"OPENAI_API_KEY": ""}):
            with TemporaryDirectory() as temp:
                with self.assertRaisesRegex(ValueError, "OPENAI_API_KEY"):
                    _build_runtime(Settings(llm_provider="codex"), Path(temp))

    def test_bundled_codex_native_request_has_no_tools_and_rejects_injected_command(self):
        # Drive the real kit adapter, SDK, and bundled app-server against a local
        # Responses API. A synthetic unadvertised command must not execute.
        requests = []
        with TemporaryDirectory() as ambient_home:
            marker = Path(ambient_home) / "command-ran"
            (Path(ambient_home) / "AGENTS.md").write_text("INHERITED-INSTRUCTION-MARKER")

            class Handler(BaseHTTPRequestHandler):
                def do_CONNECT(self):
                    self.send_error(502)

                def do_POST(self):
                    request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                    requests.append(request)
                    response_id = f"resp-{len(requests)}"
                    if len(requests) == 1:
                        item = {
                            "type": "function_call",
                            "call_id": "attempt-local-command",
                            "name": "exec_command",
                            "arguments": json.dumps({"cmd": f"touch {marker}"}),
                        }
                    else:
                        item = {
                            "type": "message", "role": "assistant", "id": "msg-final",
                            "content": [{"type": "output_text", "text": json.dumps({"aql": AQL})}],
                        }
                    events = [
                        {"type": "response.created", "response": {"id": response_id}},
                        {"type": "response.output_item.done", "item": item},
                        {"type": "response.completed", "response": {
                            "id": response_id,
                            "usage": {"input_tokens": 0, "input_tokens_details": None,
                                      "output_tokens": 0, "output_tokens_details": None,
                                      "total_tokens": 0},
                        }},
                    ]
                    body = "".join(
                        f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"
                        for event in events
                    ).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

                def log_message(self, *args):
                    pass

            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base_url = f"http://127.0.0.1:{server.server_port}/v1"
            original_builder = _build_runtime

            def synthetic_builder(settings, data_dir):
                runtime = original_builder(settings, data_dir)
                runtime._config_overrides += (
                    'model_provider="synthetic"',
                    'model_providers.synthetic.name="Synthetic"',
                    f'model_providers.synthetic.base_url="{base_url}"',
                    'model_providers.synthetic.wire_api="responses"',
                    'model_providers.synthetic.env_key="OPENAI_API_KEY"',
                    "model_providers.synthetic.supports_websockets=false",
                )
                return runtime

            proxy = f"http://127.0.0.1:{server.server_port}"
            try:
                with patch.dict(os.environ, {
                    "OPENAI_API_KEY": "synthetic-only-key",
                    "CODEX_HOME": ambient_home,
                    "HTTPS_PROXY": proxy,
                    "HTTP_PROXY": proxy,
                    "ALL_PROXY": proxy,
                }), patch("jsm_asset_mcp.llm._build_runtime", synthetic_builder), \
                        patch("jsm_asset_mcp.llm._TRANSLATION_TIMEOUT_SECONDS", 10):
                    aql = translate_to_aql(
                        "find a synthetic laptop", "Synthetic schema: Laptop Name",
                        Settings(llm_provider="codex", llm_model="gpt-5.1"),
                    )
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

            self.assertEqual(aql, AQL)
            self.assertGreaterEqual(len(requests), 2)
            self.assertTrue(all(request.get("tools") == [] for request in requests))
            self.assertTrue(all("INHERITED-INSTRUCTION-MARKER" not in json.dumps(request)
                                for request in requests))
            self.assertFalse(marker.exists())


class ResultBoundaryTests(unittest.TestCase):
    def test_deadline_cancels_runtime_and_closes_it(self):
        class SlowRuntime:
            kind = AgentRuntimeKind.CLAUDE_AGENT_SDK

            def __init__(self):
                self.cancelled = False
                self.closed = False

            async def run(self, task):
                try:
                    await asyncio.sleep(1)
                except asyncio.CancelledError:
                    self.cancelled = True
                    raise

            async def aclose(self):
                self.closed = True

        runtime = SlowRuntime()
        with patch("jsm_asset_mcp.llm._build_runtime", return_value=runtime), \
                patch("jsm_asset_mcp.llm._TRANSLATION_TIMEOUT_SECONDS", 0.01):
            with self.assertRaises(AgentTaskTimeoutError):
                asyncio.run(_query_structured_output("question", "system", SEARCH_PLAN_SCHEMA, Settings(), 100))
        self.assertTrue(runtime.cancelled)
        self.assertTrue(runtime.closed)

    def test_claude_structured_output_tool_is_not_treated_as_tool_use(self):
        # The Claude Agent SDK returns output_schema results through its
        # built-in StructuredOutput tool, which the kit audits as a tool call.
        class FakeRuntime:
            kind = AgentRuntimeKind.CLAUDE_AGENT_SDK

            async def run(self, task):
                return AgentResult(
                    output="",
                    parsed_output=PLAN,
                    parsed_output_available=True,
                    tool_calls=(ToolCallAudit(tool_name="StructuredOutput"),),
                )

            async def aclose(self):
                pass

        with patch("jsm_asset_mcp.llm._build_runtime", return_value=FakeRuntime()):
            result = asyncio.run(_query_structured_output("question", "system", SEARCH_PLAN_SCHEMA, Settings(), 100))
        self.assertEqual(result, PLAN)

    def test_timeout_cleanup_and_tool_audit_fail_closed(self):
        class FakeRuntime:
            kind = AgentRuntimeKind.CLAUDE_AGENT_SDK

            def __init__(self, result):
                self.result = result
                self.closed = False

            async def run(self, task):
                return self.result

            async def aclose(self):
                self.closed = True

        for result in (
            AgentResult(output="", finish_reason="failed", error="provider error"),
            AgentResult(output=json.dumps(PLAN), tool_calls=(ToolCallAudit(tool_name="read_file"),)),
            # StructuredOutput without a parsed result is not the SDK's output channel.
            AgentResult(output=json.dumps(PLAN), tool_calls=(ToolCallAudit(tool_name="StructuredOutput"),)),
            AgentResult(
                output="",
                parsed_output=PLAN,
                parsed_output_available=True,
                tool_calls=(ToolCallAudit(tool_name="StructuredOutput"), ToolCallAudit(tool_name="read_file")),
            ),
            AgentResult(output="not json"),
        ):
            with self.subTest(result=result):
                fake = FakeRuntime(result)
                with patch("jsm_asset_mcp.llm._build_runtime", return_value=fake):
                    with self.assertRaises(ValueError):
                        asyncio.run(_query_structured_output("question", "system", SEARCH_PLAN_SCHEMA, Settings(), 100))
                self.assertTrue(fake.closed)
