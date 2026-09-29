"""AQL results returned to the host are bounded (JSM_FETCH_ALL_MAX_OBJECTS, JSM_MAX_RESULT_BYTES)."""

import unittest
from unittest.mock import patch

from jsm_asset_mcp.config import Settings
from jsm_asset_mcp.llm import SearchPlan
from jsm_asset_mcp.tools import Dependencies, Toolset, _json_size


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


class RepeatedMetadataClient(AqlClient):
    """The API repeats one large attribute definition on every object page."""

    def __init__(self) -> None:
        super().__init__(total=39, blob=0)
        self.pages: list[dict] = []
        self.attribute = {"globalId": "space:1", "name": "x" * 27_000}

    def post(self, path, payload=None, params=None):
        result = super().post(path, payload, params)
        if path == "/object/aql":
            result["objectTypeAttributes"] = [self.attribute]
            self.pages.append(result)
        return result


class UnderfilledClient(AqlClient):
    """Returns one object per advancing page regardless of requested page size."""

    def __init__(self, total: int, terminal_flag: bool = True, repeat_value: bool = False) -> None:
        super().__init__(total=total, blob=0)
        self.offsets: list[int] = []
        self.terminal_flag = terminal_flag
        self.repeat_value = repeat_value

    def post(self, path, payload=None, params=None):
        self.requests.append(path)
        if path == "/object/aql/totalcount":
            return {"totalCount": self.total}
        offset = params["startAt"]
        self.offsets.append(offset)
        return {
            "startAt": offset,
            "values": [{"id": "0" if self.repeat_value else str(offset)}],
            "isLast": self.terminal_flag and offset == self.total - 1,
        }


class PrematureLastClient(AqlClient):
    def __init__(self) -> None:
        super().__init__(total=12)

    def post(self, path, payload=None, params=None):
        self.requests.append(path)
        if path == "/object/aql/totalcount":
            return {"totalCount": 12}
        return {"startAt": 0, "values": [{"id": str(i)} for i in range(5)], "isLast": True}


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

    def test_repeated_page_metadata_is_measured_after_deduplication(self) -> None:
        client = RepeatedMetadataClient()
        result = _tools(client).execute_aql("q", max_results=1, fetch_all=True)
        self.assertGreater(sum(_json_size(page) for page in client.pages), 1_048_576)
        self.assertLess(_json_size(result), 1_048_576)
        self.assertEqual(result["objectTypeAttributes"], [client.attribute])
        self.assertEqual([value["id"] for value in result["values"]], [str(i) for i in range(39)])
        self.assertEqual((result["_returned_count"], result["_page_count"]), (39, 39))
        self.assertTrue(result["_pagination_complete"])

        final_size = _json_size(result)
        accepted = _tools(RepeatedMetadataClient(), max_result_bytes=final_size).execute_aql(
            "q", max_results=1, fetch_all=True
        )
        self.assertEqual(_json_size(accepted), final_size)
        with self.assertRaisesRegex(ValueError, "JSM_MAX_RESULT_BYTES"):
            _tools(RepeatedMetadataClient(), max_result_bytes=final_size - 1).execute_aql(
                "q", max_results=1, fetch_all=True
            )

    def test_fetch_all_refuses_an_api_page_above_the_object_cap(self) -> None:
        client = AqlClient(total=1)

        def oversized_post(path, payload=None, params=None):
            client.requests.append(path)
            if path == "/object/aql/totalcount":
                return {"totalCount": 1}
            return {"startAt": 0, "values": [{"id": str(i)} for i in range(501)], "isLast": True}

        with patch.object(client, "post", side_effect=oversized_post):
            with self.assertRaisesRegex(ValueError, "JSM_FETCH_ALL_MAX_OBJECTS"):
                _tools(client).execute_aql("q", fetch_all=True)
        self.assertEqual(client.requests, ["/object/aql/totalcount", "/object/aql"])

    def test_byte_limit_applies_to_a_single_page(self) -> None:
        with self.assertRaisesRegex(ValueError, "JSM_MAX_RESULT_BYTES"):
            _tools(AqlClient(total=100, blob=2_000), max_result_bytes=50_000).execute_aql("q", max_results=100)

    def test_inconsistent_pagination_is_bounded(self) -> None:
        client = AqlClient(total=10, stuck=True)  # never advances, never "last"
        with self.assertRaisesRegex(ValueError, "pagination looks inconsistent"):
            _tools(client).execute_aql("q", max_results=250, fetch_all=True)
        self.assertLessEqual(client.requests.count("/object/aql"), 3)

    def test_advancing_underfilled_pages_reach_true_end_at_object_cap(self) -> None:
        client = UnderfilledClient(total=10)
        result = _tools(client, fetch_all_max_objects=10).execute_aql(
            "q", max_results=10, fetch_all=True
        )
        self.assertEqual(client.offsets, list(range(10)))
        self.assertEqual([value["id"] for value in result["values"]], [str(i) for i in range(10)])
        self.assertEqual((result["total"], result["_returned_count"], result["_page_count"]), (10, 10, 10))
        self.assertTrue(result["_pagination_complete"])

    def test_offset_reaching_total_is_complete_without_last_flag(self) -> None:
        client = UnderfilledClient(total=12, terminal_flag=False)
        result = _tools(client, fetch_all_max_objects=10).execute_aql(
            "q", start_at=2, max_results=10, fetch_all=True
        )
        self.assertEqual(client.offsets, list(range(2, 12)))
        self.assertEqual((result["startAt"], result["total"], result["_returned_count"]), (2, 12, 10))
        self.assertTrue(result["_pagination_complete"])

    def test_repeated_page_values_are_rejected_even_when_offset_advances(self) -> None:
        client = UnderfilledClient(total=10, repeat_value=True)
        with self.assertRaisesRegex(ValueError, "pagination looks inconsistent"):
            _tools(client, fetch_all_max_objects=10).execute_aql("q", max_results=10, fetch_all=True)
        self.assertEqual(client.offsets, [0, 1])

    def test_premature_last_page_cannot_return_partial_result_as_complete(self) -> None:
        client = PrematureLastClient()
        with self.assertRaisesRegex(ValueError, "returned 5 of 12.*pagination looks inconsistent"):
            _tools(client).execute_aql("q", max_results=5, fetch_all=True)
        self.assertEqual(client.requests, ["/object/aql/totalcount", "/object/aql"])

    def test_paging_arguments_are_bounded(self) -> None:
        tools = _tools(AqlClient(total=5))
        for kwargs in ({"max_results": 0}, {"max_results": 501}, {"start_at": -1}):
            with self.subTest(**kwargs), self.assertRaises(ValueError):
                tools.execute_aql("q", **kwargs)


class SearchAssetsLimitTests(unittest.TestCase):
    def _search(self, plan: SearchPlan, client: AqlClient, question: str = "question", **settings):
        with patch("jsm_asset_mcp.tools.llm.translate_to_search_plan", return_value=plan):
            return _tools(client, **settings).search_assets(question)

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

    def test_premature_last_page_cannot_return_partial_search(self) -> None:
        client = PrematureLastClient()
        with self.assertRaisesRegex(ValueError, "returned 5 of 12.*pagination looks inconsistent"):
            self._search(SearchPlan(aql="q", fetch_all=True), client)
        self.assertEqual(client.requests, ["/object/aql/totalcount", "/object/aql"])

    def test_final_search_metadata_counts_toward_byte_limit(self) -> None:
        plan = SearchPlan(aql="q")
        question = "q" * 400
        result = self._search(plan, AqlClient(total=1, blob=600), question)
        final_size = _json_size(result)
        self.assertGreater(final_size, 1_024)
        self.assertEqual(
            _json_size(self._search(plan, AqlClient(total=1, blob=600), question, max_result_bytes=final_size)),
            final_size,
        )
        with self.assertRaisesRegex(ValueError, "JSM_MAX_RESULT_BYTES"):
            self._search(plan, AqlClient(total=1, blob=600), question, max_result_bytes=final_size - 1)

    def test_fetch_all_search_metadata_counts_toward_byte_limit(self) -> None:
        plan = SearchPlan(aql="q", fetch_all=True)
        question = "q" * 400
        client = AqlClient(total=2)
        pre_metadata = _tools(client)._fetch_all_aql("q", 0, 1, include_attributes=True)
        pre_size = _json_size(pre_metadata)
        result = self._search(plan, AqlClient(total=2), question)
        self.assertGreater(_json_size(result), pre_size)
        with self.assertRaisesRegex(ValueError, "JSM_MAX_RESULT_BYTES"):
            self._search(plan, AqlClient(total=2), question, max_result_bytes=pre_size)

    def test_count_quantity_is_unbounded_but_final_json_has_byte_limit(self) -> None:
        plan = SearchPlan(aql="q", result_type="count")
        question = "q" * 400
        result = self._search(plan, AqlClient(total=10_000), question)
        self.assertEqual((result["totalCount"], result["values"]), (10_000, []))
        final_size = _json_size(result)
        self.assertEqual(
            _json_size(self._search(plan, AqlClient(total=10_000), question, max_result_bytes=final_size)),
            final_size,
        )
        with self.assertRaisesRegex(ValueError, "JSM_MAX_RESULT_BYTES"):
            self._search(plan, AqlClient(total=10_000), question, max_result_bytes=final_size - 1)


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
