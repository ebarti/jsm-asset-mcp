import unittest
from pathlib import Path


class ReadmeSetupTests(unittest.TestCase):
    def test_claude_code_project_setup_uses_mcp_file(self) -> None:
        readme = (Path(__file__).resolve().parents[1] / "README.md").read_text()
        claude_code = readme.split("### Claude Code (CLI)", 1)[1].split("### Gemini", 1)[0]
        self.assertIn("`.mcp.json`", claude_code)
        self.assertNotIn(".claude/settings.json", claude_code)

    def test_aql_prefix_example_uses_supported_operator(self) -> None:
        readme = (Path(__file__).resolve().parents[1] / "README.md").read_text()
        examples = readme.split("## AQL Reference", 1)[1]
        self.assertIn('Name STARTSWITH "prod-"', examples)
        self.assertNotIn("STARTS WITH", examples)


if __name__ == "__main__":
    unittest.main()
