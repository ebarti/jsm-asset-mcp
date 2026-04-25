import unittest
from unittest.mock import patch

from jsm_asset_mcp.config import Settings
from jsm_asset_mcp.server import create_server


class FakeClient:
    def __init__(self, settings: Settings) -> None:
        self.tag = settings.jira_domain

    def get(self, path: str, params=None) -> dict:
        return {"tag": self.tag, "path": path, "params": params}

    def post(self, path: str, payload=None, params=None) -> dict:
        return {"tag": self.tag, "path": path, "payload": payload, "params": params}

    def put(self, path: str, payload=None) -> dict:
        return {"tag": self.tag, "path": path, "payload": payload}

    def delete(self, path: str) -> dict:
        return {"tag": self.tag, "path": path}


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

            result_one = await server_one._tool_manager.call_tool(
                "get_object",
                {"object_id": "123"},
            )
            result_two = await server_two._tool_manager.call_tool(
                "get_object",
                {"object_id": "123"},
            )

        self.assertEqual(result_one["tag"], "one.example.atlassian.net")
        self.assertEqual(result_two["tag"], "two.example.atlassian.net")
