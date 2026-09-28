"""Read-only mode and the write allow-list (JSM_READ_ONLY, JSM_WRITE_SCHEMA_IDS)."""

import unittest
from unittest.mock import patch

from mcp.server.fastmcp.exceptions import ToolError

from jsm_asset_mcp.config import Settings
from jsm_asset_mcp.server import create_server
from jsm_asset_mcp.tools import Dependencies, Toolset

WRITE_TOOLS = {"create_object", "update_object", "delete_object"}


class CatalogClient:
    """Two schemas: 5 and 85. Object 300 has no schema ID in its payload."""

    objects = {
        "100": {"objectKey": "LAB-100", "objectType": {"id": "86", "objectSchemaId": "5"}},
        "200": {"objectKey": "FIN-200", "objectType": {"id": "900", "objectSchemaId": "85"}},
        "300": {"objectKey": "X-300", "objectType": {"id": "1"}},
    }
    object_types = {"86": {"objectSchemaId": "5"}, "900": {"objectSchemaId": "85"}}

    def __init__(self, settings: Settings | None = None) -> None:
        self.reads: list[str] = []
        self.writes: list[tuple[str, str]] = []

    def get(self, path: str, params=None) -> dict:
        self.reads.append(path)
        kind, ident = path.strip("/").split("/")
        return (self.objects if kind == "object" else self.object_types)[ident]

    def post(self, path: str, payload=None, params=None) -> dict:
        self.writes.append(("POST", path))
        return {"ok": True}

    def put(self, path: str, payload=None) -> dict:
        self.writes.append(("PUT", path))
        return {"ok": True}

    def delete(self, path: str) -> dict:
        self.writes.append(("DELETE", path))
        return {"status": "deleted"}

    def close(self) -> None:
        pass


class ReadOnlySettingsTests(unittest.TestCase):
    def test_defaults_keep_writes_enabled_on_every_schema(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            settings = Settings.from_env()
        self.assertFalse(settings.read_only)
        self.assertTrue(settings.write_all_schemas)
        self.assertEqual(settings.write_scope, "all object schemas")

    def test_read_only_values(self) -> None:
        cases = [("true", True), ("1", True), ("yes", True), (" on ", True),
                 ("false", False), ("0", False), ("no", False), ("off", False), ("", False)]
        for raw, expected in cases:
            with self.subTest(raw=raw), patch.dict("os.environ", {"JSM_READ_ONLY": raw}, clear=True):
                self.assertEqual(Settings.from_env().read_only, expected)

    def test_unrecognised_read_only_value_is_rejected(self) -> None:
        with patch.dict("os.environ", {"JSM_READ_ONLY": "ture"}, clear=True):
            with self.assertRaisesRegex(ValueError, "JSM_READ_ONLY"):
                Settings.from_env()

    def test_write_schema_ids_are_parsed(self) -> None:
        with patch.dict("os.environ", {"JSM_WRITE_SCHEMA_IDS": " 5, 18 ,,"}, clear=True):
            settings = Settings.from_env()
        self.assertEqual(settings.write_schema_ids, frozenset({"5", "18"}))
        self.assertFalse(settings.write_all_schemas)
        self.assertEqual(settings.write_scope, "object schemas 5, 18")

    def test_star_allows_every_schema(self) -> None:
        with patch.dict("os.environ", {"JSM_WRITE_SCHEMA_IDS": "*"}, clear=True):
            self.assertTrue(Settings.from_env().write_all_schemas)

    def test_invalid_write_schema_ids_are_rejected(self) -> None:
        for raw in ("LAB", "5,*", "5;18", "IT Assets"):
            with self.subTest(raw=raw), patch.dict("os.environ", {"JSM_WRITE_SCHEMA_IDS": raw}, clear=True):
                with self.assertRaisesRegex(ValueError, "JSM_WRITE_SCHEMA_IDS"):
                    Settings.from_env()


class ReadOnlyServerTests(unittest.IsolatedAsyncioTestCase):
    async def _tool_names(self, **settings) -> set[str]:
        with patch("jsm_asset_mcp.server.AssetsClient", CatalogClient):
            server = create_server(Settings(jira_domain="example.atlassian.net", **settings))
        return {tool.name for tool in await server.list_tools()}

    async def test_write_tools_registered_by_default(self) -> None:
        names = await self._tool_names()
        self.assertTrue(WRITE_TOOLS <= names)

    async def test_read_only_mode_hides_write_tools(self) -> None:
        default_names = await self._tool_names()
        names = await self._tool_names(read_only=True)
        self.assertEqual(names & WRITE_TOOLS, set())
        self.assertEqual(names, default_names - WRITE_TOOLS)

    async def test_read_only_mode_rejects_direct_write_calls(self) -> None:
        clients: list[CatalogClient] = []

        def make_client(settings: Settings) -> CatalogClient:
            clients.append(CatalogClient(settings))
            return clients[-1]

        with patch("jsm_asset_mcp.server.AssetsClient", make_client):
            server = create_server(Settings(jira_domain="example.atlassian.net", read_only=True))
            with self.assertRaises(ToolError):
                await server.call_tool("delete_object", {"object_id": "100"})
        self.assertEqual(clients[0].writes, [])

    async def test_write_outside_allow_list_is_refused_end_to_end(self) -> None:
        clients: list[CatalogClient] = []

        def make_client(settings: Settings) -> CatalogClient:
            clients.append(CatalogClient(settings))
            return clients[-1]

        settings = Settings(
            jira_domain="example.atlassian.net",
            write_schema_ids=frozenset({"5"}),
            write_all_schemas=False,
        )
        with patch("jsm_asset_mcp.server.AssetsClient", make_client):
            server = create_server(settings)
            with self.assertRaisesRegex(ToolError, "schema 85"):
                await server.call_tool("delete_object", {"object_id": "200"})
        self.assertEqual(clients[0].writes, [])


class WriteAllowListTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = CatalogClient()
        self.tools = self._toolset(write_schema_ids=frozenset({"5"}), write_all_schemas=False)

    def _toolset(self, **settings) -> Toolset:
        return Toolset(Dependencies(settings=Settings(**settings), client=self.client, schema=None))

    def test_writes_in_allowed_schema_go_through(self) -> None:
        self.tools.create_object("86", [])
        self.tools.update_object("100", "86", [])
        self.tools.delete_object("100")
        self.assertEqual(
            self.client.writes,
            [("POST", "/object/create"), ("PUT", "/object/100"), ("DELETE", "/object/100")],
        )

    def test_create_in_other_schema_is_refused(self) -> None:
        with self.assertRaisesRegex(PermissionError, "object type 900 belongs to object schema 85"):
            self.tools.create_object("900", [])
        self.assertEqual(self.client.writes, [])

    def test_delete_in_other_schema_is_refused(self) -> None:
        with self.assertRaisesRegex(PermissionError, "FIN-200 belongs to object schema 85"):
            self.tools.delete_object("200")
        self.assertEqual(self.client.writes, [])

    def test_update_checks_object_and_target_type(self) -> None:
        with self.assertRaisesRegex(PermissionError, "schema 85"):
            self.tools.update_object("200", "86", [])
        with self.assertRaisesRegex(PermissionError, "object type 900"):
            self.tools.update_object("100", "900", [])
        self.assertEqual(self.client.writes, [])

    def test_unknown_schema_fails_closed(self) -> None:
        with self.assertRaisesRegex(PermissionError, "could not determine the object schema"):
            self.tools.delete_object("300")
        self.assertEqual(self.client.writes, [])

    def test_no_allow_list_makes_no_lookup(self) -> None:
        tools = self._toolset()
        tools.delete_object("200")
        self.assertEqual(self.client.reads, [])
        self.assertEqual(self.client.writes, [("DELETE", "/object/200")])


if __name__ == "__main__":
    unittest.main()
