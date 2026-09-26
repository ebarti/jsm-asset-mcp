import json
import unittest
from unittest.mock import patch

import httpx

from jsm_asset_mcp.config import Settings
from jsm_asset_mcp.server import create_server


class FakeClient:
    def __init__(self, settings: Settings) -> None:
        self.tag = settings.jira_domain
        self.closed = False

    def get(self, path: str, params=None) -> dict:
        return {"tag": self.tag, "path": path, "params": params}

    def post(self, path: str, payload=None, params=None) -> dict:
        return {"tag": self.tag, "path": path, "payload": payload, "params": params}

    def put(self, path: str, payload=None) -> dict:
        return {"tag": self.tag, "path": path, "payload": payload}

    def delete(self, path: str) -> dict:
        return {"tag": self.tag, "path": path}

    def close(self) -> None:
        self.closed = True


class FakeSchemaService:
    def __init__(self, client: FakeClient, cache) -> None:
        self.client = client
        self.cache = cache

    def build_summary(self) -> str:
        return f"schema:{self.client.tag}"


class CreateServerTests(unittest.IsolatedAsyncioTestCase):
    async def test_servers_keep_their_own_bound_dependencies(self) -> None:
        with (
            patch("jsm_asset_mcp.server.AssetsClient", FakeClient),
            patch("jsm_asset_mcp.server.SchemaService", FakeSchemaService),
        ):
            server_one = create_server(
                Settings(
                    jira_domain="one.example.atlassian.net",
                    jira_email="user@example.com",
                    jira_api_token="token",
                    jira_workspace_id="wid",
                    jira_cloud_id="cid",
                )
            )
            server_two = create_server(
                Settings(
                    jira_domain="two.example.atlassian.net",
                    jira_email="user@example.com",
                    jira_api_token="token",
                    jira_workspace_id="wid",
                    jira_cloud_id="cid",
                )
            )

            result_one = await server_one.call_tool(
                "get_object",
                {"object_id": "123"},
            )
            result_two = await server_two.call_tool(
                "get_object",
                {"object_id": "123"},
            )

        payload_one = json.loads(result_one[0].text)
        payload_two = json.loads(result_two[0].text)

        self.assertEqual(payload_one["tag"], "one.example.atlassian.net")
        self.assertEqual(payload_two["tag"], "two.example.atlassian.net")

    async def test_public_schema_tool_uses_real_http_and_schema_adapters(self) -> None:
        schemas = [{"id": str(i), "name": f"Schema {i}"} for i in range(26)]
        requests: list[httpx.Request] = []

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            start_at = int(request.url.params["startAt"])
            values = schemas[start_at:start_at + 25]
            return httpx.Response(200, json={
                "startAt": start_at, "maxResults": 25, "total": 26,
                "isLast": start_at + len(values) >= 26, "values": values,
            })

        transport = httpx.MockTransport(respond)
        real_client = httpx.Client
        created_clients: list[httpx.Client] = []

        def client_factory(*args, **kwargs) -> httpx.Client:
            client = real_client(*args, transport=transport, **kwargs)
            created_clients.append(client)
            return client

        try:
            with patch("jsm_asset_mcp.client.httpx.Client", side_effect=client_factory):
                server = create_server(Settings(
                    jira_cloud_id="cloud-123", jira_workspace_id="workspace-123",
                    jira_email="user@example.com", jira_api_token="test-token",
                ))
                result = await server.call_tool("list_object_schemas", {})
        finally:
            for client in created_clients:
                client.close()

        payload = json.loads(result[0].text)
        self.assertEqual(payload["values"], schemas)
        self.assertEqual(payload["total"], 26)
        self.assertEqual([request.url.params["startAt"] for request in requests], ["0", "25"])
        self.assertTrue(all(request.url.path.endswith("/v1/objectschema/list") for request in requests))
        self.assertTrue(all(request.headers["authorization"].startswith("Basic ") for request in requests))

    async def test_aql_tool_merges_real_http_page_responses(self) -> None:
        requests: list[httpx.Request] = []

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.url.path.endswith("/object/aql/totalcount"):
                return httpx.Response(200, json={"totalCount": 3})
            start_at = int(request.url.params["startAt"])
            attributes = [{"globalId": "workspace:1", "name": "Name"}]
            if start_at == 2:
                attributes.append({"globalId": "workspace:2", "name": "Status"})
            values = [{"id": str(i)} for i in range(start_at, min(start_at + 2, 3))]
            return httpx.Response(200, json={
                "startAt": start_at, "maxResults": 2, "total": 3,
                "isLast": start_at + len(values) >= 3,
                "values": values, "objectTypeAttributes": attributes,
            })

        real_client = httpx.Client
        created_clients: list[httpx.Client] = []

        def client_factory(*args, **kwargs) -> httpx.Client:
            client = real_client(*args, transport=httpx.MockTransport(respond), **kwargs)
            created_clients.append(client)
            return client

        try:
            with patch("jsm_asset_mcp.client.httpx.Client", side_effect=client_factory):
                server = create_server(Settings(
                    jira_cloud_id="cloud-123", jira_workspace_id="workspace-123",
                    jira_email="user@example.com", jira_api_token="test-token",
                ))
                result = await server.call_tool("execute_aql", {
                    "query": "objectType = Laptop", "max_results": 2, "fetch_all": True,
                })
        finally:
            for client in created_clients:
                client.close()

        payload = json.loads(result[0].text)
        self.assertEqual([value["id"] for value in payload["values"]], ["0", "1", "2"])
        self.assertEqual([attr["name"] for attr in payload["objectTypeAttributes"]], ["Name", "Status"])
        self.assertEqual(payload["_page_count"], 2)
        self.assertTrue(payload["_pagination_complete"])
        self.assertEqual(len(requests), 3)
