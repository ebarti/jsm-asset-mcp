"""Documentation examples against the registered MCP contract."""

import json
import re
import unittest
from pathlib import Path

from jsonschema import validate

from jsm_asset_mcp import create_server
from jsm_asset_mcp.config import Settings


ROOT = Path(__file__).resolve().parents[1]


class DocumentationTests(unittest.TestCase):
    def _recipe_calls(self) -> list[dict]:
        recipes = (ROOT / "docs/recipes.md").read_text()
        blocks = re.findall(r"```json\n(.*?)\n```", recipes, flags=re.DOTALL)
        return [json.loads(block) for block in blocks]

    def test_project_setup_and_supported_aql_operator(self) -> None:
        readme = (ROOT / "README.md").read_text()
        claude_code = readme.split("### Claude Code\n", 1)[1].split("### Codex\n", 1)[0]
        self.assertIn("`.mcp.json`", claude_code)
        self.assertNotIn(".claude/settings.json", claude_code)
        queries = [call["arguments"].get("query", "") for call in self._recipe_calls()]
        self.assertTrue(any('Name STARTSWITH "prod-"' in query for query in queries))
        self.assertFalse(any("STARTS WITH" in query for query in queries))

    def test_recipes_cover_every_registered_tool_with_valid_inputs(self) -> None:
        tools = create_server(Settings())._tool_manager._tools
        calls = self._recipe_calls()
        self.assertGreaterEqual(len(calls), 15)
        covered = set()
        for call in calls:
            name = call["tool"]
            self.assertIn(name, tools)
            validate(call["arguments"], tools[name].parameters)
            covered.add(name)
        self.assertEqual(covered, set(tools))

    def test_relative_documentation_links_resolve(self) -> None:
        for name in ("README.md", "GEMINI.md", "docs/tools.md", "docs/recipes.md"):
            source = ROOT / name
            for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", source.read_text()):
                if target.startswith(("https://", "http://", "#")):
                    continue
                path = target.split("#", 1)[0]
                with self.subTest(source=name, link=target):
                    self.assertTrue((source.parent / path).exists())


if __name__ == "__main__":
    unittest.main()
