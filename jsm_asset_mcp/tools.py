"""MCP tool definitions.

Each tool is exposed as a bound method on :class:`Toolset`, which keeps
server instances isolated by carrying their dependencies explicitly.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from jsm_asset_mcp import llm
from jsm_asset_mcp.client import AssetsClient
from jsm_asset_mcp.config import Settings
from jsm_asset_mcp.schema import SchemaService


def _json_size(payload: object) -> int:
    return len(json.dumps(payload, separators=(",", ":"), default=str))


def _attribute_identity(attribute: dict) -> object:
    return attribute.get("globalId") or (
        (attribute.get("workspaceId"), attribute.get("id"))
        if attribute.get("id") is not None else json.dumps(attribute, sort_keys=True)
    )


_NUMERIC_ID_RE = re.compile(r"^[0-9]+$")


def _numeric_id(name: str, value: object) -> str:
    """Return *value* if it is a numeric Assets ID, else raise.

    IDs come from the model and are interpolated into API paths, so a value
    such as "1/../../objectschema/2" must not reach the client.
    """
    if not isinstance(value, str) or not _NUMERIC_ID_RE.fullmatch(value):
        raise ValueError(f'{name} must be a numeric Assets ID such as "123"; got {value!r}.')
    return value


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
    read_tools: list = field(init=False, repr=False)
    write_tools: list = field(init=False, repr=False)
    all_tools: list = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.read_tools = [
            self.execute_aql,
            self.get_object,
            self.get_object_attributes,
            self.get_object_reference_info,
            self.list_object_schemas,
            self.get_object_schema,
            self.list_object_types,
            self.get_object_type_attributes,
            self.get_schema_summary,
            self.list_status_types,
            self.list_reference_types,
            self.get_usage,
            self.search_assets,
            self.get_object_history,
            self.get_connected_tickets,
        ]
        self.write_tools = [
            self.create_object,
            self.update_object,
            self.delete_object,
        ]
        # In read-only mode the write tools are never registered, so a client
        # cannot call them even if its own permission rules would allow it.
        self.all_tools = list(self.read_tools)
        if not self.deps.settings.read_only:
            self.all_tools.extend(self.write_tools)

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
        self._check_paging(start_at, max_results)
        if fetch_all:
            return self._fetch_all_aql(query, start_at, max_results, include_attributes)

        page = self._fetch_aql_page(query, start_at, max_results, include_attributes)
        self._check_result_size(_json_size(page), len(page.get("values", [])))
        return page

    def get_object(self, object_id: str) -> dict:
        """Retrieve a single asset object by its ID.

        Args:
            object_id: The unique identifier of the asset object.
        """
        return self.deps.client.get(f"/object/{_numeric_id('object_id', object_id)}")

    def get_object_attributes(self, object_id: str) -> dict:
        """Retrieve all attributes of a specific object.

        Args:
            object_id: The unique identifier of the asset object.
        """
        return self.deps.client.get(f"/object/{_numeric_id('object_id', object_id)}/attributes")

    def _schema_of(self, resource: dict, what: str) -> str:
        schema_id = resource.get("objectSchemaId")
        if schema_id in (None, ""):
            # Fail closed: an unknown schema can never match the allow-list.
            raise PermissionError(f"Write refused: could not determine the object schema of {what}.")
        return str(schema_id)

    def _require_writable(self, schema_id: str, what: str) -> None:
        settings = self.deps.settings
        if settings.write_all_schemas or schema_id in settings.write_schema_ids:
            return
        raise PermissionError(
            f"Write refused: {what} belongs to object schema {schema_id}, which is not in "
            f"JSM_WRITE_SCHEMA_IDS (writes allowed on {settings.write_scope})."
        )

    def _check_object_type_writable(self, object_type_id: str) -> None:
        if self.deps.settings.write_all_schemas:
            return
        object_type = self.deps.client.get(f"/objecttype/{object_type_id}")
        what = f"object type {object_type_id}"
        self._require_writable(self._schema_of(object_type, what), what)

    def _check_object_writable(self, object_id: str) -> None:
        if self.deps.settings.write_all_schemas:
            return
        obj = self.deps.client.get(f"/object/{object_id}")
        what = f"object {obj.get('objectKey') or object_id}"
        self._require_writable(self._schema_of(obj.get("objectType") or {}, what), what)

    def create_object(self, object_type_id: str, attributes: list[dict]) -> dict:
        """Create a new object in JSM Assets.

        When JSM_WRITE_SCHEMA_IDS is set, only allowed in the listed object schemas.

        Args:
            object_type_id: The ID of the object type to create.
            attributes: Array of attribute objects. Each must have 'objectTypeAttributeId' and
                        'objectAttributeValues' (array with 'value' key).
                        Example: [{"objectTypeAttributeId": "123", "objectAttributeValues": [{"value": "My Server"}]}]
        """
        object_type_id = _numeric_id("object_type_id", object_type_id)
        self._check_object_type_writable(object_type_id)
        return self.deps.client.post("/object/create", payload={
            "objectTypeId": object_type_id,
            "attributes": attributes,
        })

    def update_object(self, object_id: str, object_type_id: str, attributes: list[dict]) -> dict:
        """Update an existing object in JSM Assets.

        When JSM_WRITE_SCHEMA_IDS is set, only allowed in the listed object schemas.

        Args:
            object_id: The ID of the object to update.
            object_type_id: The ID of the object type.
            attributes: Array of attribute objects to update. Each must have 'objectTypeAttributeId' and
                        'objectAttributeValues' (array with 'value' key).
        """
        object_id = _numeric_id("object_id", object_id)
        object_type_id = _numeric_id("object_type_id", object_type_id)
        # Both: the object's current schema and the type it is written as.
        self._check_object_writable(object_id)
        self._check_object_type_writable(object_type_id)
        return self.deps.client.put(f"/object/{object_id}", payload={
            "objectTypeId": object_type_id,
            "attributes": attributes,
        })

    def delete_object(self, object_id: str) -> dict:
        """Delete an object from JSM Assets.

        When JSM_WRITE_SCHEMA_IDS is set, only allowed in the listed object schemas.

        Args:
            object_id: The ID of the object to delete.
        """
        object_id = _numeric_id("object_id", object_id)
        self._check_object_writable(object_id)
        return self.deps.client.delete(f"/object/{object_id}")

    def get_object_reference_info(self, object_id: str) -> list[dict]:
        """Summarise the objects that reference a given object (inbound
        references), grouped by object type and reference type. Useful for
        impact analysis ("what depends on this server?").

        Returns counts, not the objects themselves. To list them, run
        execute_aql with e.g. `object HAVING outboundReferences(Key = "ITSM-123")`
        (objects pointing to ITSM-123), or `object HAVING
        inboundReferences(Key = "ITSM-123")` (objects ITSM-123 points to).

        Args:
            object_id: The unique identifier of the asset object.
        """
        return self.deps.client.get(f"/object/{_numeric_id('object_id', object_id)}/referenceinfo")

    # ── Schema introspection ────────────────────────────────────────────

    def list_object_schemas(self) -> dict:
        """List all object schemas available in the workspace."""
        return self.deps.schema.fetch_all_schemas_response()

    def get_object_schema(self, schema_id: str) -> dict:
        """Get details of a specific object schema.

        Args:
            schema_id: The ID of the object schema.
        """
        return self.deps.client.get(f"/objectschema/{_numeric_id('schema_id', schema_id)}")

    def list_object_types(self, schema_id: str) -> list[dict]:
        """List all object types in a schema (flat list).

        Args:
            schema_id: The ID of the object schema.
        """
        return self.deps.client.get(f"/objectschema/{_numeric_id('schema_id', schema_id)}/objecttypes/flat")

    def get_object_type_attributes(self, object_type_id: str) -> list[dict]:
        """Get all attribute definitions for an object type. Useful for understanding what
        fields are available before constructing AQL queries or creating/updating objects.

        Args:
            object_type_id: The ID of the object type.
        """
        return self.deps.client.get(f"/objecttype/{_numeric_id('object_type_id', object_type_id)}/attributes")

    def list_status_types(self, schema_id: str = "") -> list[dict]:
        """List status types, i.e. the valid values of Status attributes.

        Use the exact names in AQL, e.g. `Status = "In Use"`.

        Args:
            schema_id: Optional object schema ID. Empty returns only global
                status types; a schema ID returns global plus that schema's own.
        """
        if schema_id:
            schema_id = _numeric_id("schema_id", schema_id)
        statuses = self.deps.client.get("/config/statustype")
        if schema_id:
            statuses = statuses + self.deps.client.get(
                "/config/statustype", params={"objectSchemaId": schema_id}
            )
        return statuses

    def list_reference_types(self, schema_id: str = "") -> list[dict]:
        """List reference types, i.e. the labels of links between objects
        (e.g. "Installed", "Depends").

        Use the exact names in AQL reference functions, e.g.
        `object HAVING outR(objectType = "Host", refType IN ("Installed"))`.

        Args:
            schema_id: Optional object schema ID. Empty returns only global
                reference types; a schema ID returns global plus that schema's own.
        """
        if schema_id:
            schema_id = _numeric_id("schema_id", schema_id)
        if not schema_id:
            return self.deps.client.get("/config/referencetype")
        return self.deps.client.get(
            "/config/referencetype",
            params={"objectSchemaId": schema_id, "includeAll": "true"},
        )

    def get_usage(self) -> dict:
        """Get the total number of objects in the workspace and the object
        count per schema. Useful for inventory overviews and licence tracking.
        """
        return self.deps.client.get("/usage")

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

    def _check_paging(self, start_at: int, max_results: int) -> None:
        cap = self.deps.settings.fetch_all_max_objects
        if start_at < 0:
            raise ValueError(f"start_at must be 0 or more; got {start_at}.")
        if not 1 <= max_results <= cap:
            raise ValueError(
                f"max_results must be between 1 and {cap} (JSM_FETCH_ALL_MAX_OBJECTS); got {max_results}."
            )

    def _check_result_size(self, size: int, returned: int) -> None:
        limit = self.deps.settings.max_result_bytes
        if size > limit:
            raise ValueError(
                f"The AQL result reached {size} bytes after {returned} objects, above the "
                f"{limit}-byte limit (JSM_MAX_RESULT_BYTES). Narrow the query, set "
                "include_attributes=false, lower max_results, or ask for a count instead."
            )

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

        # Refuse before paginating: the total count is already known, so an
        # oversized "all" request costs one call instead of hundreds.
        cap = self.deps.settings.fetch_all_max_objects
        remaining = max(expected_total - start_at, 0)
        if remaining > cap:
            raise ValueError(
                f"The query matches {remaining} objects from start_at={start_at}, more than the "
                f"fetch_all limit of {cap} (JSM_FETCH_ALL_MAX_OBJECTS). Narrow the AQL, ask for "
                "a count, or page explicitly with start_at and max_results."
            )
        # Guard against an API whose totals and pages disagree.
        max_pages = -(-cap // max_results) + 1
        merged_values: list[dict] = []
        merged_attributes: list[dict] = []
        seen_attributes: set[object] = set()
        has_attributes = False

        while True:
            if len(pages) >= max_pages:
                raise ValueError(
                    f"fetch_all stopped after {len(pages)} pages without reaching the reported "
                    f"total of {expected_total}; the Assets API pagination looks inconsistent."
                )
            page = self._fetch_aql_page(query, next_start, max_results, include_attributes)
            values = page.get("values", [])
            merged_values.extend(values)
            if len(merged_values) > cap:
                raise ValueError(
                    f"Assets returned {len(merged_values)} objects, above the fetch_all limit of "
                    f"{cap} (JSM_FETCH_ALL_MAX_OBJECTS)."
                )
            if "objectTypeAttributes" in page:
                has_attributes = True
                unique_attributes = []
                for attribute in page["objectTypeAttributes"]:
                    identity = _attribute_identity(attribute)
                    if identity not in seen_attributes:
                        seen_attributes.add(identity)
                        unique_attributes.append(attribute)
                        merged_attributes.append(attribute)
                # Keep only definitions that can appear in the merged result.
                page = {**page, "objectTypeAttributes": unique_attributes}
            pages.append(page)
            # This is a lower bound on the returned JSON, so it can stop an
            # oversized crawl early without rejecting repeated page metadata.
            lower_bound = {"values": merged_values}
            if has_attributes:
                lower_bound["objectTypeAttributes"] = merged_attributes
            self._check_result_size(_json_size(lower_bound), len(merged_values))

            if _is_last_page(page):
                break
            if not values:
                break

            page_start = int(page.get("startAt", next_start))
            next_start = page_start + len(values)
            if next_start >= expected_total:
                break

        result = self._merge_aql_pages(pages, max_results, expected_total)
        self._check_result_size(_json_size(result), len(result.get("values", [])))
        return result

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
        attributes = []
        seen_attributes = set()
        for page in pages:
            values.extend(page.get("values", []))
            for attribute in page.get("objectTypeAttributes", []):
                identity = _attribute_identity(attribute)
                if identity not in seen_attributes:
                    seen_attributes.add(identity)
                    attributes.append(attribute)

        total = total_count if total_count is not None else len(values)
        complete = _is_last_page(pages[-1])
        complete = complete or len(values) >= total

        result["startAt"] = pages[0].get("startAt", 0)
        result["maxResults"] = len(values)
        result["total"] = total
        result["isLast"] = complete
        result["values"] = values
        if any("objectTypeAttributes" in page for page in pages):
            result["objectTypeAttributes"] = attributes
        if any("last" in page for page in pages):
            result["last"] = complete
        if "pageNumber" in result:
            result["pageNumber"] = 1
        if "pageSize" in result:
            result["pageSize"] = len(values)
        if "pageObjectSize" in result:
            result["pageObjectSize"] = len(values)
        if "startIndex" in result:
            result["toIndex"] = result["startIndex"] + len(values) - 1
        if "totalFilterCount" in result:
            result["totalFilterCount"] = total
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

        The configured translator decides whether the question asks for objects
        or an exact count, plus whether it asks for a result limit or all matching
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
        if plan.result_type != "count":
            self._check_paging(0, page_size)
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
        self._check_result_size(_json_size(result), len(result.get("values", [])))
        return result

    # ── Related data ─────────────────────────────────────────────────────

    def get_object_history(self, object_id: str) -> dict:
        """Get the change history of an object.

        Args:
            object_id: The unique identifier of the asset object.
        """
        return self.deps.client.get(f"/object/{_numeric_id('object_id', object_id)}/history")

    def get_connected_tickets(self, object_id: str) -> dict:
        """Get Jira tickets connected to an asset object.

        Args:
            object_id: The unique identifier of the asset object.
        """
        return self.deps.client.get(f"/objectconnectedtickets/{_numeric_id('object_id', object_id)}/tickets")
