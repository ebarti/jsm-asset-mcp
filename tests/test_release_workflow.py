import unittest
from pathlib import Path


class ReleaseWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = (
            Path(__file__).resolve().parents[1] / ".github" / "workflows" / "release.yml"
        ).read_text()

    def test_release_workflow_runs_python_project_checks(self) -> None:
        self.assertIn("actions/setup-python@v5", self.workflow)
        self.assertIn("astral-sh/setup-uv@v5", self.workflow)
        self.assertIn("uv sync --all-extras --frozen", self.workflow)
        self.assertIn("uv run --all-extras python -m unittest discover -s tests", self.workflow)

    def test_release_workflow_builds_python_extension_archive(self) -> None:
        self.assertIn("gemini-extension.json", self.workflow)
        self.assertIn("jsm_asset_mcp", self.workflow)
        self.assertIn("tarfile.open", self.workflow)
        self.assertIn("dist/*.tar.gz", self.workflow)
        self.assertIn("fail_on_unmatched_files: true", self.workflow)

    def test_release_workflow_does_not_use_node_packaging(self) -> None:
        self.assertNotIn("setup-node", self.workflow)
        self.assertNotIn("npm ", self.workflow)
        self.assertNotIn("my-tool", self.workflow)
