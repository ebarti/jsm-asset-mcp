import os
import posixpath
import re
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
        self.assertIn("uv run --all-extras --frozen python -m unittest discover -s tests", self.workflow)

    def test_release_version_gate_accepts_matching_tag_and_rejects_mismatch(self) -> None:
        root = Path(__file__).resolve().parents[1]
        step = self.workflow.split("      - name: Verify release version\n", 1)[1]
        step = step.split("      - name: Build extension archive\n", 1)[0]
        script = step.split("python - <<'PY'\n", 1)[1].split("\n          PY", 1)[0]
        for tag, expected_returncode in (("v1.2.0", 0), ("v1.2.1", 1)):
            with self.subTest(tag=tag):
                result = subprocess.run(
                    [sys.executable, "-c", textwrap.dedent(script)],
                    cwd=root,
                    env={**os.environ, "GITHUB_REF_NAME": tag},
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, expected_returncode, result.stderr)

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
            for name in ("gemini-extension.json", "GEMINI.md", "README.md", "CHANGELOG.md", "LICENSE", "main.py", "pyproject.toml", "uv.lock"):
                shutil.copy2(root / name, checkout / name)
            shutil.copytree(root / "jsm_asset_mcp", checkout / "jsm_asset_mcp", ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copytree(root / "docs", checkout / "docs", ignore=shutil.ignore_patterns("__pycache__"))
            env = {**os.environ, "GITHUB_REF_NAME": "v1.2.0"}
            subprocess.run(
                [sys.executable, "-c", textwrap.dedent(script)],
                cwd=checkout,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )

            with tarfile.open(checkout / "dist" / "jsm-asset-mcp-v1.2.0.tar.gz", "r:gz") as archive:
                names = set(archive.getnames())
                markdown = {
                    name: archive.extractfile(name).read().decode()
                    for name in names if name.endswith(".md")
                }

        for name in ("gemini-extension.json", "LICENSE", "CHANGELOG.md", "docs/tools.md",
                     "docs/recipes.md", "docs/examples/stdio_client.py",
                     "docs/examples/.env.example", "jsm_asset_mcp/server.py"):
            self.assertIn(name, names)
        self.assertNotIn("jsm-asset-mcp/gemini-extension.json", names)
        for source, document in markdown.items():
            for link in re.findall(r"\[[^\]]+\]\(([^)]+)\)", document):
                if link.startswith(("https://", "http://", "#")):
                    continue
                target = posixpath.normpath(posixpath.join(
                    posixpath.dirname(source), link.split("#", 1)[0]
                ))
                with self.subTest(source=source, link=link):
                    self.assertIn(target, names)

    def test_release_workflow_does_not_use_node_packaging(self) -> None:
        self.assertNotIn("setup-node", self.workflow)
        self.assertNotIn("npm ", self.workflow)
        self.assertNotIn("my-tool", self.workflow)
