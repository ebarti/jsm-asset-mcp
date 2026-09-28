"""Reference, status, reference-type and usage reads, and the enriched summary."""

import unittest

from jsm_asset_mcp.cache import TTLCache
from jsm_asset_mcp.config import Settings
from jsm_asset_mcp.schema import SchemaService
from jsm_asset_mcp.tools import Dependencies, Toolset


class WorkspaceClient:
    """Two schemas, each with one object type and three attributes."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict | None]] = []

    def get(self, path: str, params=None):
        self.calls.append((path, params))
        if path == "/objectschema/list":
            return {
                "startAt": 0,
                "total": 2,
                "isLast": True,
                "values": [
                    {"id": "1", "name": "IT", "objectSchemaKey": "IT"},
                    {"id": "2", "name": "Vendors", "objectSchemaKey": "VM"},
                ],
            }
        if path == "/config/statustype":
            if params is None:
                return [{"id": "2", "name": "Active"}]
            return [{"id": "30", "name": f"In Use {params['objectSchemaId']}"}]
        if path == "/config/referencetype":
            if params is None:
                return [{"id": "4", "name": "Reference"}]
            if params.get("includeAll") == "true":
                return [{"id": "4", "name": "Reference"}, {"id": "48", "name": "Installed"}]
            return [{"id": "48", "name": "Installed"}]
        if path.endswith("/objecttypes/flat"):
            schema_id = path.split("/")[2]
            return [{"id": f"ot{schema_id}", "name": f"Host{schema_id}"}]
        if path.endswith("/attributes"):
            return [
                {"name": "Name", "type": 0, "defaultType": {"name": "Text"}},
                {"name": "Application", "type": 1, "referenceObjectType": {"id": "309", "name": "Application"}},
                {"name": "Space", "type": 3},
            ]
        if path == "/usage":
            return {"totalObjectsCount": 3, "perSchemaUsageInfo": []}
        if path.endswith("/referenceinfo"):
            return [{"objectType": {"name": "Application"}, "numberOfReferencedObjects": 1}]
        raise AssertionError(f"Unexpected request: {path}")


class EnrichedSummaryTests(unittest.TestCase):
    def test_summary_lists_statuses_reference_types_and_reference_targets(self) -> None:
        summary = SchemaService(WorkspaceClient(), TTLCache()).build_summary()

        self.assertIn('Global status types (all schemas): "Active"', summary)
        self.assertIn('Global reference types (all schemas): "Reference"', summary)
        self.assertIn('Status types: "In Use 1"', summary)
        self.assertIn('Status types: "In Use 2"', summary)
        self.assertIn('Reference types: "Installed"', summary)
        self.assertIn("Application: Object Reference -> Application", summary)
        self.assertIn("Space: Confluence", summary)

    def test_status_and_reference_types_are_cached(self) -> None:
        client = WorkspaceClient()
        service = SchemaService(client, TTLCache())
        service.build_summary()
        service._cache.set("schema_summary", None)  # force a second build from the lower caches
        before = len(client.calls)
        service.build_summary()
        config_calls = [p for p, _ in client.calls[before:] if p.startswith("/config/")]
        self.assertEqual(config_calls, [])


class NewReadToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = WorkspaceClient()
        self.tools = Toolset(Dependencies(Settings(), self.client, SchemaService(self.client, TTLCache())))

    def test_list_status_types_merges_global_and_schema_statuses(self) -> None:
        self.assertEqual([s["name"] for s in self.tools.list_status_types()], ["Active"])
        self.assertEqual([s["name"] for s in self.tools.list_status_types("5")], ["Active", "In Use 5"])

    def test_list_reference_types_includes_global_when_schema_given(self) -> None:
        self.assertEqual([r["name"] for r in self.tools.list_reference_types()], ["Reference"])
        self.assertEqual([r["name"] for r in self.tools.list_reference_types("5")], ["Reference", "Installed"])
        self.assertEqual(
            self.client.calls[-1],
            ("/config/referencetype", {"objectSchemaId": "5", "includeAll": "true"}),
        )

    def test_get_usage_and_reference_info_hit_expected_endpoints(self) -> None:
        self.assertEqual(self.tools.get_usage()["totalObjectsCount"], 3)
        self.tools.get_object_reference_info("42")
        self.assertEqual(self.client.calls[-1], ("/object/42/referenceinfo", None))


if __name__ == "__main__":
    unittest.main()
