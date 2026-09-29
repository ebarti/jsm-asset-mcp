"""Reference, status, reference-type and usage reads, and the enriched summary."""

import unittest
from unittest.mock import patch

import httpx

from jsm_asset_mcp.cache import TTLCache
from jsm_asset_mcp.config import Settings
from jsm_asset_mcp.llm import SearchPlan
from jsm_asset_mcp.schema import SchemaService
from jsm_asset_mcp.server import create_server
from jsm_asset_mcp.tools import Dependencies, Toolset


class WorkspaceClient:
    """Two schemas, each with one object type and three attributes."""

    def __init__(self, settings: Settings | None = None) -> None:
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

    def post(self, path: str, payload=None, params=None):
        self.calls.append((path, params))
        if path == "/object/aql/totalcount":
            return {"totalCount": 1}
        if path == "/object/aql":
            return {"values": [{"id": "42"}], "isLast": True}
        raise AssertionError(f"Unexpected request: {path}")


class DeniedConfigClient(WorkspaceClient):
    def __init__(self, denied: set[tuple[str, str | None]], status: int) -> None:
        super().__init__()
        self.denied = denied
        self.status = status

    def get(self, path: str, params=None):
        if (path, (params or {}).get("objectSchemaId")) in self.denied:
            self.calls.append((path, params))
            request = httpx.Request("GET", f"https://api.atlassian.com{path}")
            httpx.Response(self.status, request=request).raise_for_status()
        return super().get(path, params)


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
        before = len(client.calls)
        with patch("jsm_asset_mcp.schema.time.monotonic", return_value=service._summary_built_at + 601):
            service.build_summary()  # stale response schedules a refresh from the lower caches
            service._refresh_thread.join(timeout=5)
        config_calls = [p for p, _ in client.calls[before:] if p.startswith("/config/")]
        self.assertEqual(config_calls, [])

    def test_global_config_permission_denial_keeps_required_schema_context(self) -> None:
        client = DeniedConfigClient({
            ("/config/statustype", None), ("/config/referencetype", None),
        }, 401)
        with self.assertLogs("jsm_asset_mcp.schema", level="WARNING") as logs:
            summary = SchemaService(client, TTLCache()).build_summary()
        self.assertIn("## Schema: IT", summary)
        self.assertIn("Application: Object Reference -> Application", summary)
        self.assertIn('Status types: "In Use 1"', summary)
        self.assertNotIn("Global status types", summary)
        self.assertTrue(any("401" in message and "status" in message for message in logs.output))
        self.assertTrue(any("401" in message and "reference" in message for message in logs.output))

    def test_schema_config_permission_denial_keeps_summary_and_search(self) -> None:
        denied = {("/config/statustype", "1"), ("/config/referencetype", "1")}
        client = DeniedConfigClient(denied, 403)
        service = SchemaService(client, TTLCache())
        tools = Toolset(Dependencies(Settings(), client, service))
        with self.assertLogs("jsm_asset_mcp.schema", level="WARNING") as logs:
            with patch("jsm_asset_mcp.tools.llm.translate_to_search_plan", return_value=SearchPlan(aql='objectType = "Host1"')) as translate:
                result = tools.search_assets("find hosts")
        summary = translate.call_args.args[1]
        self.assertIn("## Schema: IT", summary)
        self.assertIn('Global status types (all schemas): "Active"', summary)
        self.assertIn('Status types: "In Use 2"', summary)
        self.assertNotIn('Status types: "In Use 1"', summary)
        self.assertEqual(result["values"], [{"id": "42"}])
        self.assertTrue(any("403" in message and "schema 1" in message for message in logs.output))

    def test_non_permission_config_error_and_required_schema_error_propagate(self) -> None:
        for denied, status in (
            ({("/config/statustype", None)}, 500),
            ({("/objectschema/list", None)}, 403),
        ):
            with self.subTest(denied=denied, status=status):
                with self.assertRaises(httpx.HTTPStatusError) as caught:
                    SchemaService(DeniedConfigClient(denied, status), TTLCache()).build_summary()
                self.assertEqual(caught.exception.response.status_code, status)


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

    def test_invalid_reference_and_schema_ids_make_no_request(self) -> None:
        for call in (
            lambda: self.tools.get_object_reference_info("42/../../usage"),
            lambda: self.tools.list_status_types("1?x=1"),
            lambda: self.tools.list_reference_types("１２"),
        ):
            with self.assertRaisesRegex(ValueError, "numeric Assets ID"):
                call()
        self.assertEqual(self.client.calls, [])

    def test_explicit_config_tools_preserve_permission_errors(self) -> None:
        client = DeniedConfigClient({
            ("/config/statustype", None), ("/config/referencetype", None),
        }, 403)
        tools = Toolset(Dependencies(Settings(), client, SchemaService(client, TTLCache())))
        for call in (tools.list_status_types, tools.list_reference_types):
            with self.assertRaises(httpx.HTTPStatusError) as caught:
                call()
            self.assertEqual(caught.exception.response.status_code, 403)


class NewReadRegistrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_four_new_reads_remain_registered_in_read_only_mode(self) -> None:
        with patch("jsm_asset_mcp.server.AssetsClient", WorkspaceClient):
            server = create_server(Settings(read_only=True))
        names = {tool.name for tool in await server.list_tools()}
        self.assertEqual(len(names), 15)
        self.assertTrue({"get_object_reference_info", "list_status_types", "list_reference_types", "get_usage"} <= names)
        self.assertFalse({"create_object", "update_object", "delete_object"} & names)


if __name__ == "__main__":
    unittest.main()
