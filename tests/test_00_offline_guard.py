"""Install the offline guard before default unittest discovery imports tests."""

import importlib.util
import os
import socket
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


_GUARD_PATH = Path(__file__).with_name("sitecustomize.py").resolve()
_loaded = sys.modules.get("sitecustomize")
if _loaded is None or Path(getattr(_loaded, "__file__", "")).resolve() != _GUARD_PATH:
    # Discovery puts tests/ on sys.path only after Python startup. Load the
    # exact local guard even if another sitecustomize was already imported.
    _spec = importlib.util.spec_from_file_location("_jsm_offline_guard", _GUARD_PATH)
    _guard = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_guard)


class OfflineGuardTests(unittest.TestCase):
    def test_default_discovery_scrubs_inherited_credentials(self):
        env = dict(os.environ, JIRA_API_TOKEN="synthetic-sentinel")
        env.pop("PYTHONPATH", None)
        env.pop("PYTHON_DOTENV_DISABLED", None)
        probe = (
            "import os, unittest; "
            "unittest.defaultTestLoader.discover('tests', pattern='test_00_offline_guard.py'); "
            "print(os.environ.get('JIRA_API_TOKEN') == 'synthetic-sentinel', "
            "os.environ.get('PYTHON_DOTENV_DISABLED'))"
        )
        result = subprocess.run(
            [sys.executable, "-c", probe],
            cwd=Path(__file__).resolve().parents[1],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "False 1")

    def test_dotenv_stays_disabled_when_a_test_clears_environment(self):
        from jsm_asset_mcp.config import Settings

        with TemporaryDirectory() as temp:
            dotfile = Path(temp) / ".env"
            dotfile.write_text("JIRA_API_TOKEN=synthetic-from-dotenv\n")
            with patch("dotenv.main.find_dotenv", return_value=str(dotfile)), \
                    patch.dict(os.environ, {"LLM_PROVIDER": "gemini"}, clear=True):
                settings = Settings.from_env()
                self.assertEqual(settings.jira_api_token, "")
                self.assertNotIn("JIRA_API_TOKEN", os.environ)

    def test_external_connections_are_blocked_and_loopback_resolves(self):
        with self.assertRaisesRegex(OSError, "External network disabled"):
            socket.getaddrinfo("api.atlassian.com", 443)
        self.assertTrue(socket.getaddrinfo("127.0.0.1", 0))
