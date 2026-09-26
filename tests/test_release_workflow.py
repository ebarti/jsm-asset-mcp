import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import textwrap
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

    def test_built_archive_has_manifest_at_root(self) -> None:
        root = Path(__file__).resolve().parents[1]
        build_step = self.workflow.split("      - name: Build extension archive\n", 1)[1]
        build_step = build_step.split("      - name: Verify extension archive\n", 1)[0]
        script = build_step.split("python - <<'PY'\n", 1)[1].split("\n          PY", 1)[0]

        with tempfile.TemporaryDirectory() as temp_dir:
            checkout = Path(temp_dir)
            (checkout / "dist").mkdir()
            for name in ("gemini-extension.json", "GEMINI.md", "README.md", "main.py", "pyproject.toml", "uv.lock"):
                shutil.copy2(root / name, checkout / name)
            shutil.copytree(root / "jsm_asset_mcp", checkout / "jsm_asset_mcp", ignore=shutil.ignore_patterns("__pycache__"))
            env = {**os.environ, "GITHUB_REF_NAME": "v-test"}
            subprocess.run(
                [sys.executable, "-c", textwrap.dedent(script)],
                cwd=checkout,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )

            with tarfile.open(checkout / "dist" / "jsm-asset-mcp-v-test.tar.gz", "r:gz") as archive:
                names = set(archive.getnames())

        self.assertIn("gemini-extension.json", names)
        self.assertIn("jsm_asset_mcp/server.py", names)
        self.assertNotIn("jsm-asset-mcp/gemini-extension.json", names)

    def test_release_workflow_does_not_use_node_packaging(self) -> None:
        self.assertNotIn("setup-node", self.workflow)
        self.assertNotIn("npm ", self.workflow)
        self.assertNotIn("my-tool", self.workflow)
