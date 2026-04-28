import unittest
from unittest.mock import patch

from jsm_asset_mcp.config import Settings
from jsm_asset_mcp.llm import SearchPlan
from jsm_asset_mcp.tools import Dependencies, Toolset


class RecordingClient:
    def __init__(self) -> None:
        self.posts: list[dict] = []
        self.values = ["laptop-1", "laptop-2", "laptop-3", "laptop-4", "laptop-5"]

    def post(self, path: str, payload=None, params=None) -> dict:
        call = {"path": path, "payload": payload, "params": params}
        self.posts.append(call)

        start_at = params["startAt"]
        max_results = params["maxResults"]
        values = self.values[start_at:start_at + max_results]
        next_start = start_at + len(values)
        return {
            "startAt": start_at,
            "maxResults": max_results,
            "total": len(self.values),
            "isLast": str(next_start >= len(self.values)).lower(),
            "values": values,
        }


class StaticSchemaService:
    def build_summary(self) -> str:
        return "Object type: Laptop\nAttributes: Name, Model"


class SearchAssetsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = RecordingClient()
        self.toolset = Toolset(
            Dependencies(
                settings=Settings(),
                client=self.client,
                schema=StaticSchemaService(),
            )
        )

    def test_search_assets_all_question_paginates_until_last_page(self) -> None:
        plan = SearchPlan(aql='objectType = "Laptop"', fetch_all=True)

        with patch("jsm_asset_mcp.tools.llm.translate_to_search_plan", return_value=plan):
            result = self.toolset.search_assets("Get all MacBook laptops", max_results=2)

        self.assertEqual([call["path"] for call in self.client.posts], ["/object/aql"] * 3)
        self.assertEqual([call["params"]["startAt"] for call in self.client.posts], [0, 2, 4])
        self.assertEqual([call["params"]["maxResults"] for call in self.client.posts], [2, 2, 2])
        self.assertEqual(result["values"], self.client.values)
        self.assertEqual(result["maxResults"], 5)
        self.assertEqual(result["_page_size"], 2)
        self.assertEqual(result["_page_count"], 3)
        self.assertEqual(result["_returned_count"], 5)
        self.assertTrue(result["_pagination_complete"])
        self.assertEqual(result["_result_type"], "objects")
        self.assertEqual(result["_generated_aql"], 'objectType = "Laptop"')
        self.assertIsNone(result["_llm_max_results"])
        self.assertTrue(result["_llm_fetch_all"])

    def test_search_assets_uses_default_max_results_when_llm_does_not_specify_limit(self) -> None:
        plan = SearchPlan(aql='objectType = "Laptop"')

        with patch("jsm_asset_mcp.tools.llm.translate_to_search_plan", return_value=plan):
            result = self.toolset.search_assets("Show me MacBook laptops", max_results=10)

        self.assertEqual(self.client.posts[0]["path"], "/object/aql")
        self.assertEqual(
            self.client.posts[0]["params"],
            {"startAt": 0, "maxResults": 10, "includeAttributes": "true"},
        )
        self.assertEqual(result["maxResults"], 10)
        self.assertEqual(result["_result_type"], "objects")
        self.assertIsNone(result["_llm_max_results"])
        self.assertFalse(result["_llm_fetch_all"])

    def test_search_assets_uses_llm_max_results_when_specified(self) -> None:
        plan = SearchPlan(aql='objectType = "Laptop"', max_results=3)

        with patch("jsm_asset_mcp.tools.llm.translate_to_search_plan", return_value=plan):
            result = self.toolset.search_assets("Show me three MacBook laptops", max_results=10)

        self.assertEqual(self.client.posts[0]["params"]["maxResults"], 3)
        self.assertEqual(result["_llm_max_results"], 3)
        self.assertFalse(result["_llm_fetch_all"])

    def test_search_assets_fetch_all_parameter_paginates_even_when_llm_does_not(self) -> None:
        plan = SearchPlan(aql='objectType = "Laptop"')

        with patch("jsm_asset_mcp.tools.llm.translate_to_search_plan", return_value=plan):
            result = self.toolset.search_assets("Show MacBook laptops", max_results=3, fetch_all=True)

        self.assertEqual([call["params"]["startAt"] for call in self.client.posts], [0, 3])
        self.assertEqual(result["values"], self.client.values)

    def test_execute_aql_fetch_all_paginates_until_last_page(self) -> None:
        result = self.toolset.execute_aql('objectType = "Laptop"', max_results=2, fetch_all=True)

        self.assertEqual([call["params"]["startAt"] for call in self.client.posts], [0, 2, 4])
        self.assertEqual(result["values"], self.client.values)
        self.assertEqual(result["_returned_count"], 5)
