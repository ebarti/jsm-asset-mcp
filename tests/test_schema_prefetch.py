"""Background prefetch and stale-while-revalidate for the schema summary."""

import threading
import unittest
from unittest.mock import patch

from jsm_asset_mcp.cache import TTLCache
from jsm_asset_mcp.config import Settings
from jsm_asset_mcp.schema import SchemaService
from jsm_asset_mcp.server import create_server


class SummaryClient:
    """One schema per entry in ``names``; every request can be held or failed."""

    def __init__(self) -> None:
        self.names = ["IT"]
        self.calls: list[str] = []
        self.release = threading.Event()
        self.release.set()
        self.fail = False
        self.closed = False

    def get(self, path: str, params=None):
        self.release.wait(timeout=5)
        if self.fail:
            raise RuntimeError("Assets API unavailable")
        self.calls.append(path)
        if path == "/objectschema/list":
            values = [{"id": str(i), "name": name} for i, name in enumerate(self.names)]
            return {"startAt": 0, "total": len(values), "isLast": True, "values": values}
        if path.endswith("/objecttypes/flat"):
            return []
        raise AssertionError(f"Unexpected request: {path}")

    def close(self) -> None:
        self.closed = True


class SummaryPrefetchTests(unittest.TestCase):
    def _service(self, client: SummaryClient, ttl: float = 600) -> SchemaService:
        return SchemaService(client, TTLCache(ttl=ttl), summary_ttl=ttl)

    def test_warm_does_not_block_and_first_call_reuses_prefetch(self) -> None:
        client = SummaryClient()
        client.release.clear()
        service = self._service(client)

        service.warm()  # returns while every request is still held
        self.assertEqual(client.calls, [])

        client.release.set()
        summary = service.build_summary()
        calls = len(client.calls)
        self.assertIn("## Schema: IT", summary)
        self.assertEqual(service.build_summary(), summary)
        self.assertEqual(len(client.calls), calls)  # no second crawl

    def test_warm_is_idempotent_while_running(self) -> None:
        client = SummaryClient()
        client.release.clear()
        service = self._service(client)
        service.warm()
        first = service._refresh_thread
        service.warm()
        self.assertIs(service._refresh_thread, first)
        client.release.set()
        first.join(timeout=5)

    def test_expired_summary_is_served_stale_while_refreshing(self) -> None:
        client = SummaryClient()
        service = self._service(client, ttl=60)
        with patch("jsm_asset_mcp.schema.time.monotonic", return_value=1000.0):
            original = service.build_summary()

        client.names.append("HR")
        service._cache.clear()  # the fetch cache expires with the summary
        with patch("jsm_asset_mcp.schema.time.monotonic", return_value=1061.0):
            stale = service.build_summary()
            service._refresh_thread.join(timeout=5)
            refreshed = service.build_summary()

        self.assertEqual(stale, original)
        self.assertNotIn("## Schema: HR", stale)
        self.assertIn("## Schema: HR", refreshed)

    def test_failed_prefetch_is_logged_and_next_call_raises(self) -> None:
        client = SummaryClient()
        client.fail = True
        service = self._service(client)
        with self.assertLogs("jsm_asset_mcp.schema", level="WARNING"):
            service.warm()
            service._refresh_thread.join(timeout=5)
        with self.assertRaisesRegex(RuntimeError, "unavailable"):
            service.build_summary()

    def test_failed_refresh_keeps_serving_last_good_summary(self) -> None:
        client = SummaryClient()
        service = self._service(client, ttl=60)
        with patch("jsm_asset_mcp.schema.time.monotonic", return_value=1000.0):
            good = service.build_summary()

        client.fail = True
        service._cache.clear()
        with patch("jsm_asset_mcp.schema.time.monotonic", return_value=1061.0):
            with self.assertLogs("jsm_asset_mcp.schema", level="WARNING"):
                self.assertEqual(service.build_summary(), good)
                service._refresh_thread.join(timeout=5)
            self.assertEqual(service.build_summary(), good)


class PrefetchSettingsTests(unittest.TestCase):
    def test_defaults(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            settings = Settings.from_env()
        self.assertEqual(settings.schema_cache_ttl, 600)
        self.assertTrue(settings.schema_prefetch)

    def test_values_from_env(self) -> None:
        env = {"JSM_SCHEMA_CACHE_TTL": "3600", "JSM_SCHEMA_PREFETCH": "false"}
        with patch.dict("os.environ", env, clear=True):
            settings = Settings.from_env()
        self.assertEqual(settings.schema_cache_ttl, 3600)
        self.assertFalse(settings.schema_prefetch)

    def test_invalid_values_are_rejected(self) -> None:
        for name, raw in (("JSM_SCHEMA_CACHE_TTL", "0"), ("JSM_SCHEMA_CACHE_TTL", "1h"), ("JSM_SCHEMA_PREFETCH", "maybe")):
            with self.subTest(name=name, raw=raw), patch.dict("os.environ", {name: raw}, clear=True):
                with self.assertRaisesRegex(ValueError, name):
                    Settings.from_env()


class LifespanPrefetchTests(unittest.IsolatedAsyncioTestCase):
    async def _run_lifespan(self, settings: Settings) -> SummaryClient:
        clients: list[SummaryClient] = []

        def make_client(_: Settings) -> SummaryClient:
            clients.append(SummaryClient())
            return clients[-1]

        with patch("jsm_asset_mcp.server.AssetsClient", make_client):
            server = create_server(settings)
        low_level = server._mcp_server
        async with low_level.lifespan(low_level):
            thread = next((t for t in threading.enumerate() if t.name == "schema-summary-refresh"), None)
            if thread is not None:
                thread.join(timeout=5)
        client = clients[0]
        self.assertTrue(client.closed)
        return client

    _CREDENTIALS = {"jira_domain": "example.atlassian.net", "jira_email": "me@example.com", "jira_api_token": "t"}

    async def test_lifespan_prefetches_by_default(self) -> None:
        client = await self._run_lifespan(Settings(**self._CREDENTIALS))
        self.assertIn("/objectschema/list", client.calls)

    async def test_prefetch_can_be_disabled(self) -> None:
        client = await self._run_lifespan(Settings(**self._CREDENTIALS, schema_prefetch=False))
        self.assertEqual(client.calls, [])

    async def test_no_prefetch_without_credentials(self) -> None:
        client = await self._run_lifespan(Settings())
        self.assertEqual(client.calls, [])


if __name__ == "__main__":
    unittest.main()
