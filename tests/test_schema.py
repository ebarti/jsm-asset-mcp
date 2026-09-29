import unittest

from jsm_asset_mcp.cache import TTLCache
from jsm_asset_mcp.config import Settings
from jsm_asset_mcp.schema import SchemaService
from jsm_asset_mcp.tools import Dependencies, Toolset


class SchemaClient:
    def __init__(self, pages: list[dict]) -> None:
        self.pages = pages
        self.calls: list[dict] = []

    def get(self, path: str, params: dict | None = None) -> dict:
        self.calls.append({"path": path, "params": params})
        if path == "/objectschema/list":
            return self.pages.pop(0)
        if path.endswith("/objecttypes/flat"):
            return []
        if path in ("/config/statustype", "/config/referencetype"):
            return []
        raise AssertionError(f"Unexpected request: {path}")


class SchemaPaginationTests(unittest.TestCase):
    def test_shared_service_and_public_tool_return_all_26_schemas(self) -> None:
        schemas = [{"id": str(i), "name": f"Schema {i}"} for i in range(26)]
        client = SchemaClient([
            {"startAt": 0, "maxResults": 25, "total": 26, "isLast": "false", "values": schemas[:25]},
            {"startAt": 25, "maxResults": 25, "total": 26, "isLast": "true", "values": schemas[25:]},
        ])
        service = SchemaService(client, TTLCache())
        toolset = Toolset(Dependencies(Settings(), client, service))

        response = toolset.list_object_schemas()
        self.assertEqual(response["values"], schemas)
        self.assertEqual(response["total"], 26)
        self.assertEqual(response["maxResults"], 26)
        self.assertTrue(response["isLast"])
        self.assertEqual(service.fetch_all_schemas(), schemas)
        self.assertIn("Schema: Schema 25", service.build_summary())
        self.assertEqual(
            [call["params"]["startAt"] for call in client.calls if call["path"] == "/objectschema/list"],
            [0, 25],
        )

    def test_empty_nonterminal_page_is_not_cached(self) -> None:
        first = {"startAt": 0, "total": 26, "isLast": False, "values": [{"id": str(i)} for i in range(25)]}
        client = SchemaClient([first, {"startAt": 25, "total": 26, "isLast": False, "values": []}])
        service = SchemaService(client, TTLCache())

        with self.assertRaisesRegex(ValueError, "empty nonterminal"):
            service.fetch_all_schemas()

        client.pages = [first, {"startAt": 25, "total": 26, "isLast": True, "values": [{"id": "25"}]}]
        self.assertEqual(len(service.fetch_all_schemas()), 26)
        self.assertEqual(
            [call["params"]["startAt"] for call in client.calls if call["path"] == "/objectschema/list"],
            [0, 25, 0, 25],
        )

    def test_nonadvancing_page_is_rejected(self) -> None:
        client = SchemaClient([
            {"startAt": 0, "total": 26, "isLast": False, "values": [{"id": str(i)} for i in range(25)]},
            {"startAt": 0, "total": 26, "isLast": False, "values": [{"id": "25"}]},
        ])
        with self.assertRaisesRegex(ValueError, "did not advance"):
            SchemaService(client, TTLCache()).fetch_all_schemas()


if __name__ == "__main__":
    unittest.main()
