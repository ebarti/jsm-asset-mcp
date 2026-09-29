"""Model-supplied IDs and API paths are validated before any request."""

import unittest
from unittest.mock import Mock, patch

from jsm_asset_mcp.client import AssetsClient
from jsm_asset_mcp.config import Settings
from jsm_asset_mcp.tools import Dependencies, Toolset


class RecordingClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def get(self, path, params=None):
        self.calls.append(("GET", path))
        return {}

    def post(self, path, payload=None, params=None):
        self.calls.append(("POST", path))
        return {}

    def put(self, path, payload=None):
        self.calls.append(("PUT", path))
        return {}

    def delete(self, path):
        self.calls.append(("DELETE", path))
        return {}


class ToolIdTests(unittest.TestCase):
    BAD_IDS = ("1/../../objectschema/2", "1?includeAttributes=true", "../x", "", " 1", "1a", "-1", "１２")

    def setUp(self) -> None:
        self.client = RecordingClient()
        self.tools = Toolset(Dependencies(settings=Settings(), client=self.client, schema=None))

    def _calls(self):
        t = self.tools
        return {
            "get_object": lambda v: t.get_object(v),
            "get_object_attributes": lambda v: t.get_object_attributes(v),
            "get_object_history": lambda v: t.get_object_history(v),
            "get_connected_tickets": lambda v: t.get_connected_tickets(v),
            "get_object_schema": lambda v: t.get_object_schema(v),
            "list_object_types": lambda v: t.list_object_types(v),
            "get_object_type_attributes": lambda v: t.get_object_type_attributes(v),
            "create_object": lambda v: t.create_object(v, []),
            "update_object (object)": lambda v: t.update_object(v, "1", []),
            "update_object (type)": lambda v: t.update_object("1", v, []),
            "delete_object": lambda v: t.delete_object(v),
        }

    def test_non_numeric_ids_are_refused_before_any_request(self) -> None:
        for tool, call in self._calls().items():
            for bad in self.BAD_IDS:
                with self.subTest(tool=tool, value=bad):
                    with self.assertRaisesRegex(ValueError, "numeric Assets ID"):
                        call(bad)
        self.assertEqual(self.client.calls, [])

    def test_numeric_ids_reach_the_expected_route(self) -> None:
        for call in self._calls().values():
            call("42")
        self.assertIn(("GET", "/object/42/history"), self.client.calls)
        self.assertIn(("DELETE", "/object/42"), self.client.calls)
        self.assertIn(("GET", "/objectschema/42/objecttypes/flat"), self.client.calls)


class ClientPathTests(unittest.TestCase):
    def test_invalid_path_is_rejected_before_discovery_for_every_verb(self) -> None:
        settings = Settings(
            jira_domain="example.atlassian.net",
            jira_email="me@example.com",
            jira_api_token="t",
        )
        with patch("jsm_asset_mcp.client.httpx.Client") as http_client, patch(
            "jsm_asset_mcp.config.httpx.get"
        ) as discover:
            client = AssetsClient(settings)
            calls = {
                "get": lambda: client.get("/object/1?x=1"),
                "post": lambda: client.post("/object/1?x=1"),
                "put": lambda: client.put("/object/1?x=1", {}),
                "delete": lambda: client.delete("/object/1?x=1"),
            }
            for verb, call in calls.items():
                with self.subTest(verb=verb):
                    with self.assertRaisesRegex(ValueError, "unexpected Assets API path"):
                        call()
            discover.assert_not_called()
            for verb in calls:
                getattr(http_client.return_value, verb).assert_not_called()

    def test_client_refuses_unexpected_paths(self) -> None:
        settings = Settings(
            jira_email="me@example.com",
            jira_api_token="t",
            jira_cloud_id="11111111-2222-3333-4444-555555555555",
            jira_workspace_id="66666666-7777-8888-9999-aaaaaaaaaaaa",
        )
        client = AssetsClient(settings)
        client._http = Mock()
        for path in ("/object/1/../../x", "/object/1?x=1", "/object/1#frag", "object/1", "/object//1", "/object/%2e%2e"):
            with self.subTest(path=path):
                with self.assertRaisesRegex(ValueError, "unexpected Assets API path"):
                    client.get(path)
        client._http.get.assert_not_called()
        client._http.get.return_value = Mock(status_code=200, json=Mock(return_value={}))
        client.get("/object/aql/totalcount")
        self.assertTrue(client._http.get.call_args.args[0].endswith("/v1/object/aql/totalcount"))


if __name__ == "__main__":
    unittest.main()
