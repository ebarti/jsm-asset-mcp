"""MCP tool definitions.

Each tool is exposed as a bound method on :class:`Toolset`, which keeps
server instances isolated by carrying their dependencies explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from jsm_asset_mcp import llm
from jsm_asset_mcp.client import AssetsClient
from jsm_asset_mcp.config import Settings
from jsm_asset_mcp.schema import SchemaService


def _is_last_page(result: dict) -> bool:
    is_last = result.get("isLast")
    if isinstance(is_last, bool):
        return is_last
    if isinstance(is_last, str):
        return is_last.lower() == "true"
    return False


@dataclass
class Dependencies:
    """Runtime dependencies injected by the server factory."""

    settings: Settings
    client: AssetsClient
    schema: SchemaService


@dataclass
class Toolset:
    """Per-server collection of bound MCP tool callables."""

    deps: Dependencies
    all_tools: list = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.all_tools = [
            self.execute_aql,
            self.get_object,
            self.get_object_attributes,
            self.create_object,
            self.update_object,
            self.delete_object,
            self.list_object_schemas,
            self.get_object_schema,
            self.list_object_types,
            self.get_object_type_attributes,
            self.get_schema_summary,
            self.search_assets,
            self.get_object_history,
            self.get_connected_tickets,
        ]

    # ── Core CRUD ────────────────────────────────────────────────────────

    def execute_aql(
        self,
        query: str,
        start_at: int = 0,
        max_results: int = 25,
        include_attributes: bool = True,
        fetch_all: bool = False,
    ) -> dict:
        """Execute an AQL (Asset Query Language) query to search for objects.

        Args:
            query: The AQL query string (e.g. 'objectType = "Laptop"', 'Name LIKE "server"').
            start_at: Starting index for pagination (default: 0).
            max_results: Page size for each request (default: 25).
            include_attributes: Include object attributes in response (default: True).
            fetch_all: When true, paginate until all matching objects are returned.
        """
        if fetch_all:
            return self._fetch_all_aql(query, start_at, max_results, include_attributes)

        return self._fetch_aql_page(query, start_at, max_results, include_attributes)

    def get_object(self, object_id: str) -> dict:
        """Retrieve a single asset object by its ID.

        Args:
            object_id: The unique identifier of the asset object.
        """
        return self.deps.client.get(f"/object/{object_id}")

    def get_object_attributes(self, object_id: str) -> dict:
        """Retrieve all attributes of a specific object.

        Args:
            object_id: The unique identifier of the asset object.
        """
        return self.deps.client.get(f"/object/{object_id}/attributes")

    def create_object(self, object_type_id: str, attributes: list[dict]) -> dict:
        """Create a new object in JSM Assets.

        Args:
            object_type_id: The ID of the object type to create.
            attributes: Array of attribute objects. Each must have 'objectTypeAttributeId' and
                        'objectAttributeValues' (array with 'value' key).
                        Example: [{"objectTypeAttributeId": "123", "objectAttributeValues": [{"value": "My Server"}]}]
        """
        return self.deps.client.post("/object/create", payload={
            "objectTypeId": object_type_id,
            "attributes": attributes,
        })

    def update_object(self, object_id: str, object_type_id: str, attributes: list[dict]) -> dict:
        """Update an existing object in JSM Assets.

        Args:
            object_id: The ID of the object to update.
            object_type_id: The ID of the object type.
            attributes: Array of attribute objects to update. Each must have 'objectTypeAttributeId' and
                        'objectAttributeValues' (array with 'value' key).
        """
        return self.deps.client.put(f"/object/{object_id}", payload={
            "objectTypeId": object_type_id,
            "attributes": attributes,
        })

    def delete_object(self, object_id: str) -> dict:
        """Delete an object from JSM Assets.

        Args:
            object_id: The ID of the object to delete.
        """
        return self.deps.client.delete(f"/object/{object_id}")

    # ── Schema introspection ────────────────────────────────────────────

    def list_object_schemas(self) -> dict:
        """List all object schemas available in the workspace."""
        return self.deps.client.get("/objectschema/list")

    def get_object_schema(self, schema_id: str) -> dict:
        """Get details of a specific object schema.

        Args:
            schema_id: The ID of the object schema.
        """
        return self.deps.client.get(f"/objectschema/{schema_id}")

    def list_object_types(self, schema_id: str) -> list[dict]:
        """List all object types in a schema (flat list).

        Args:
            schema_id: The ID of the object schema.
        """
        return self.deps.client.get(f"/objectschema/{schema_id}/objecttypes/flat")

    def get_object_type_attributes(self, object_type_id: str) -> list[dict]:
        """Get all attribute definitions for an object type. Useful for understanding what
        fields are available before constructing AQL queries or creating/updating objects.

        Args:
            object_type_id: The ID of the object type.
        """
        return self.deps.client.get(f"/objecttype/{object_type_id}/attributes")

    def get_schema_summary(self) -> str:
        """Get a human-readable summary of all object schemas, object types, and their
        attributes in the workspace. Useful for understanding the data model before
        constructing AQL queries.
        """
        return self.deps.schema.build_summary()

    # ── Natural language search ──────────────────────────────────────────

    def _translate_question(self, question: str) -> llm.SearchPlan:
        schema_summary = self.deps.schema.build_summary()
        return llm.translate_to_search_plan(question, schema_summary, self.deps.settings)

    def _fetch_aql_page(
        self,
        query: str,
        start_at: int,
        max_results: int,
        include_attributes: bool,
    ) -> dict:
        params = {
            "startAt": start_at,
            "maxResults": max_results,
            "includeAttributes": str(include_attributes).lower(),
        }
        return self.deps.client.post("/object/aql", payload={"qlQuery": query}, params=params)

    def _fetch_aql_total_count(self, query: str) -> int:
        result = self.deps.client.post("/object/aql/totalcount", payload={"qlQuery": query})
        total_count = result.get("totalCount")
        if not isinstance(total_count, int):
            raise ValueError("Assets total-count response did not contain an integer totalCount.")
        return total_count

    def _fetch_all_aql(
        self,
        query: str,
        start_at: int,
        max_results: int,
        include_attributes: bool,
        total_count: int | None = None,
    ) -> dict:
        pages = []
        next_start = start_at
        expected_total = total_count
        if expected_total is None:
            expected_total = self._fetch_aql_total_count(query)

        while True:
            page = self._fetch_aql_page(query, next_start, max_results, include_attributes)
            pages.append(page)

            values = page.get("values", [])
            if _is_last_page(page):
                break
            if not values:
                break

            page_start = int(page.get("startAt", next_start))
            next_start = page_start + len(values)
            if next_start >= expected_total:
                break

        return self._merge_aql_pages(pages, max_results, expected_total)

    def _merge_aql_pages(
        self,
        pages: list[dict],
        page_size: int,
        total_count: int | None = None,
    ) -> dict:
        if not pages:
            return {
                "startAt": 0,
                "maxResults": 0,
                "total": 0,
                "isLast": True,
                "values": [],
                "_page_size": page_size,
                "_page_count": 0,
                "_returned_count": 0,
                "_pagination_complete": True,
            }

        result = dict(pages[0])
        values = []
        for page in pages:
            values.extend(page.get("values", []))

        total = total_count if total_count is not None else len(values)
        complete = _is_last_page(pages[-1])
        complete = complete or len(values) >= total

        result["startAt"] = pages[0].get("startAt", 0)
        result["maxResults"] = len(values)
        result["total"] = total
        result["isLast"] = complete
        result["values"] = values
        result["_page_size"] = page_size
        result["_page_count"] = len(pages)
        result["_returned_count"] = len(values)
        result["_total_count"] = total
        result["_pagination_complete"] = complete
        return result

    def search_assets(self, question: str, max_results: int = 25, fetch_all: bool = False) -> dict:
        """Search assets using natural language. The server translates your question into an
        AQL query by first inspecting the schema to understand available object types and
        attributes, then constructing the appropriate query.

        Claude decides whether the user's question asks for objects or an exact
        count, plus whether it asks for an explicit result limit or all matching
        objects. Count requests use the Assets total-count endpoint.

        Examples:
            - "Find all laptops assigned to John"
            - "Show the first 10 MacBook laptops"
            - "Show servers in the Sydney data center"
            - "List all software licenses expiring this month"
            - "Which network switches have status Active?"

        Args:
            question: A natural language description of the assets you want to find.
            max_results: Default result limit/page size when the question does not
                specify a limit (default: 25).
            fetch_all: When true, paginate until all matching objects are returned.
        """
        plan = self._translate_question(question)
        page_size = plan.max_results or max_results
        total_count = self._fetch_aql_total_count(plan.aql)

        if plan.result_type == "count":
            result = {
                "totalCount": total_count,
                "total": total_count,
                "startAt": 0,
                "maxResults": 0,
                "isLast": True,
                "values": [],
                "_page_size": page_size,
                "_page_count": 0,
                "_returned_count": 0,
                "_total_count": total_count,
                "_pagination_complete": True,
            }
        elif fetch_all or plan.fetch_all:
            result = self._fetch_all_aql(
                plan.aql,
                0,
                page_size,
                include_attributes=True,
                total_count=total_count,
            )
        else:
            result = self._fetch_aql_page(plan.aql, 0, page_size, include_attributes=True)
            result["total"] = total_count
            result["_total_count"] = total_count

        result["_generated_aql"] = plan.aql
        result["_original_question"] = question
        result["_llm_max_results"] = plan.max_results
        result["_llm_fetch_all"] = plan.fetch_all
        result["_result_type"] = plan.result_type
        return result

    # ── Related data ─────────────────────────────────────────────────────

    def get_object_history(self, object_id: str) -> dict:
        """Get the change history of an object.

        Args:
            object_id: The unique identifier of the asset object.
        """
        return self.deps.client.get(f"/object/{object_id}/history")

    def get_connected_tickets(self, object_id: str) -> dict:
        """Get Jira tickets connected to an asset object.

        Args:
            object_id: The unique identifier of the asset object.
        """
        return self.deps.client.get(f"/objectconnectedtickets/{object_id}/tickets")
