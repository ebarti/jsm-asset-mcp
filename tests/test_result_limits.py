"""AQL results returned to the host are bounded (JSM_FETCH_ALL_MAX_OBJECTS, JSM_MAX_RESULT_BYTES)."""

import unittest
from unittest.mock import patch

from jsm_asset_mcp.config import Settings
from jsm_asset_mcp.llm import SearchPlan
from jsm_asset_mcp.tools import Dependencies, Toolset


class AqlClient:
    """Serves ``total`` objects of ``blob`` bytes each; records every request."""

    def __init__(self, total: int, blob: int = 10, stuck: bool = False) -> None:
        self.total, self.blob, self.stuck = total, blob, stuck
        self.requests: list[str] = []

    def post(self, path, payload=None, params=None):
        self.requests.append(path)
        if path == "/object/aql/totalcount":
            return {"totalCount": self.total}
        start = 0 if self.stuck else params["startAt"]
        size = 1 if self.stuck else params["maxResults"]
        values = [{"id": str(i), "pad": "x" * self.blob} for i in range(start, min(start + size, self.total))]
        return {"startAt": start, "values": values, "isLast": (not self.stuck) and start + len(values) >= self.total}


class StaticSchema:
    def build_summary(self) -> str:
        return "Object type: Laptop"


def _tools(client, **settings) -> Toolset:
    return Toolset(Dependencies(settings=Settings(**settings), client=client, schema=StaticSchema()))


class FetchAllLimitTests(unittest.TestCase):
    def test_oversized_fetch_all_is_refused_with_one_request(self) -> None:
        client = AqlClient(total=10_000)
        with self.assertRaisesRegex(ValueError, "matches 10000 objects.*JSM_FETCH_ALL_MAX_OBJECTS"):
            _tools(client).execute_aql('objectType = "Laptop"', max_results=25, fetch_all=True)
        self.assertEqual(client.requests, ["/object/aql/totalcount"])

    def test_fetch_all_within_limits_returns_everything(self) -> None:
        result = _tools(AqlClient(total=120)).execute_aql("q", max_results=50, fetch_all=True)
        self.assertEqual(result["_returned_count"], 120)
        self.assertTrue(result["_pagination_complete"])

    def test_start_at_counts_against_the_limit(self) -> None:
        result = _tools(AqlClient(total=600)).execute_aql("q", start_at=200, max_results=100, fetch_all=True)
        self.assertEqual(result["_returned_count"], 400)

    def test_byte_limit_stops_pagination(self) -> None:
        client = AqlClient(total=300, blob=1_000)
        with self.assertRaisesRegex(ValueError, "JSM_MAX_RESULT_BYTES"):
            _tools(client, max_result_bytes=60_000).execute_aql("q", max_results=25, fetch_all=True)
        self.assertLess(client.requests.count("/object/aql"), 12)

    def test_byte_limit_applies_to_a_single_page(self) -> None:
        with self.assertRaisesRegex(ValueError, "JSM_MAX_RESULT_BYTES"):
            _tools(AqlClient(total=100, blob=2_000), max_result_bytes=50_000).execute_aql("q", max_results=100)

    def test_inconsistent_pagination_is_bounded(self) -> None:
        client = AqlClient(total=10, stuck=True)  # never advances, never "last"
        with self.assertRaisesRegex(ValueError, "pagination looks inconsistent"):
            _tools(client).execute_aql("q", max_results=250, fetch_all=True)
        self.assertLessEqual(client.requests.count("/object/aql"), 3)

    def test_paging_arguments_are_bounded(self) -> None:
        tools = _tools(AqlClient(total=5))
        for kwargs in ({"max_results": 0}, {"max_results": 501}, {"start_at": -1}):
            with self.subTest(**kwargs), self.assertRaises(ValueError):
                tools.execute_aql("q", **kwargs)


class SearchAssetsLimitTests(unittest.TestCase):
    def _search(self, plan: SearchPlan, client: AqlClient):
        with patch("jsm_asset_mcp.tools.llm.translate_to_search_plan", return_value=plan):
            return _tools(client).search_assets("question")

    def test_model_chosen_fetch_all_is_bounded(self) -> None:
        client = AqlClient(total=5_000)
        with self.assertRaisesRegex(ValueError, "JSM_FETCH_ALL_MAX_OBJECTS"):
            self._search(SearchPlan(aql="q", fetch_all=True), client)
        self.assertEqual(client.requests, ["/object/aql/totalcount"])

    def test_count_is_not_limited(self) -> None:
        self.assertEqual(self._search(SearchPlan(aql="q", result_type="count"), AqlClient(total=10_000))["totalCount"], 10_000)

    def test_model_page_size_is_bounded(self) -> None:
        with self.assertRaisesRegex(ValueError, "max_results must be between 1 and 500"):
            self._search(SearchPlan(aql="q", max_results=5_000), AqlClient(total=5_000))


class LimitSettingsTests(unittest.TestCase):
    def test_defaults(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            s = Settings.from_env()
        self.assertEqual((s.fetch_all_max_objects, s.max_result_bytes), (500, 1_048_576))

    def test_values_from_env_and_validation(self) -> None:
        env = {"JSM_FETCH_ALL_MAX_OBJECTS": "50", "JSM_MAX_RESULT_BYTES": "2000"}
        with patch.dict("os.environ", env, clear=True):
            s = Settings.from_env()
        self.assertEqual((s.fetch_all_max_objects, s.max_result_bytes), (50, 2000))
        for name in env:
            for raw in ("0", "-1", "lots"):
                with self.subTest(name=name, raw=raw), patch.dict("os.environ", {name: raw}, clear=True):
                    with self.assertRaisesRegex(ValueError, name):
                        Settings.from_env()


if __name__ == "__main__":
    unittest.main()
