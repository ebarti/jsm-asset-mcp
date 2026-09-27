import json
import importlib.util
import unittest
from pathlib import Path


class GeminiExtensionManifestTests(unittest.TestCase):
    def test_uv_invocation_installs_every_advertised_provider_runtime(self) -> None:
        manifest_path = Path(__file__).resolve().parents[1] / "gemini-extension.json"
        manifest = json.loads(manifest_path.read_text())

        server = manifest["mcpServers"]["jsm-asset-mcp"]

        self.assertEqual(server["command"], "uv")
        self.assertEqual(server["args"][0:2], ["run", "--all-extras"])
        description = next(setting["description"] for setting in manifest["settings"]
                           if setting["envVar"] == "LLM_PROVIDER")
        for provider in ("anthropic", "anthropic-vertex", "anthropic-bedrock",
                         "gemini", "codex", "antigravity"):
            self.assertIn(provider, description)
        for module in ("claude_agent_sdk", "openai_codex", "google.antigravity"):
            with self.subTest(runtime=module):
                self.assertIsNotNone(importlib.util.find_spec(module))

    def test_mcp_env_uses_supported_gemini_variable_syntax(self) -> None:
        manifest_path = Path(__file__).resolve().parents[1] / "gemini-extension.json"
        manifest = json.loads(manifest_path.read_text())

        server_env = manifest["mcpServers"]["jsm-asset-mcp"]["env"]

        for env_var, value in server_env.items():
            self.assertEqual(value, f"${{{env_var}}}")
            self.assertNotIn("settings.", value)

    def test_manifest_exposes_cloud_id_setting_supported_by_server(self) -> None:
        manifest_path = Path(__file__).resolve().parents[1] / "gemini-extension.json"
        manifest = json.loads(manifest_path.read_text())

        setting_env_vars = {setting["envVar"] for setting in manifest["settings"]}

        self.assertIn("JIRA_CLOUD_ID", setting_env_vars)

    def test_manifest_exposes_llm_provider_and_gemini_key(self) -> None:
        manifest_path = Path(__file__).resolve().parents[1] / "gemini-extension.json"
        manifest = json.loads(manifest_path.read_text())

        server_env = manifest["mcpServers"]["jsm-asset-mcp"]["env"]
        setting_env_vars = {setting["envVar"] for setting in manifest["settings"]}

        self.assertIn("LLM_PROVIDER", server_env)
        self.assertIn("GEMINI_API_KEY", server_env)
        self.assertIn("OPENAI_API_KEY", server_env)
        self.assertIn("LLM_MODEL", server_env)
        self.assertNotIn("ANTHROPIC_PROVIDER", server_env)
        self.assertIn("LLM_PROVIDER", setting_env_vars)
        self.assertIn("GEMINI_API_KEY", setting_env_vars)
        self.assertIn("OPENAI_API_KEY", setting_env_vars)
        self.assertIn("LLM_MODEL", setting_env_vars)
