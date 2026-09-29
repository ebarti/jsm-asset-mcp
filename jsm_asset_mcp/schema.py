"""Schema introspection and human-readable summary builder.

Fetches object schemas, object types, and attribute definitions from the
JSM Assets API and assembles them into a text summary used as LLM context
for natural-language → AQL translation.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import httpx

from jsm_asset_mcp.cache import TTLCache
from jsm_asset_mcp.client import AssetsClient

logger = logging.getLogger(__name__)

# Type-code → human-readable label mapping
_TYPE_LABELS: dict[int, str] = {
    0: "Default",
    1: "Object Reference",
    2: "User",
    3: "Confluence",
    4: "Group",
    5: "Version",
    6: "Project",
    7: "Status",
}


def _names(items: list[dict]) -> str:
    return ", ".join(f'"{item.get("name", "?")}"' for item in items)


class SchemaService:
    """Cacheable schema introspection backed by an :class:`AssetsClient`."""

    def __init__(self, client: AssetsClient, cache: TTLCache) -> None:
        self._client = client
        self._cache = cache

    # ── Low-level fetchers (cached) ──────────────────────────────────────

    def fetch_all_schemas(self) -> list[dict]:
        """Return all object schemas in the workspace."""
        return self.fetch_all_schemas_response()["values"]

    def fetch_all_schemas_response(self) -> dict:
        """Return a complete schema-list response, caching only completed scans."""
        cached = self._cache.get("schemas_response")
        if cached is not None:
            return cached

        schemas: list[dict] = []
        start_at = 0
        page_size = 25
        expected_total: int | None = None
        first_page: dict | None = None
        previous_values: list[dict] | None = None

        while True:
            page = self._client.get(
                "/objectschema/list",
                params={"startAt": start_at, "maxResults": page_size},
            )
            if not isinstance(page, dict):
                raise ValueError("Assets schema list response must be an object.")
            if first_page is None:
                first_page = page

            values = page.get("values", page.get("objectSchemas"))
            if not isinstance(values, list):
                raise ValueError("Assets schema list response must contain a schema list.")
            page_start = page.get("startAt", start_at)
            if isinstance(page_start, bool) or not isinstance(page_start, int) or page_start != start_at:
                raise ValueError("Assets schema list page did not advance to the requested offset.")
            if previous_values is not None and values and values == previous_values:
                raise ValueError("Assets schema list returned the same page twice.")

            total = page.get("total")
            if total is not None:
                if isinstance(total, bool) or not isinstance(total, int) or total < 0:
                    raise ValueError("Assets schema list total must be a non-negative integer.")
                if expected_total is not None and total != expected_total:
                    raise ValueError("Assets schema list total changed during pagination.")
                expected_total = total

            schemas.extend(values)
            last = page.get("isLast", page.get("last"))
            if isinstance(last, str):
                last = last.lower() == "true"
            if last is True or (expected_total is not None and len(schemas) >= expected_total):
                if expected_total is not None and len(schemas) != expected_total:
                    raise ValueError("Assets schema list ended before its reported total.")
                break
            if not values:
                if start_at == 0 and expected_total is None and last is None:
                    break
                raise ValueError("Assets schema list returned an empty nonterminal page.")
            if last is None and expected_total is None and len(values) < page_size:
                break

            previous_values = values
            start_at = page_start + len(values)

        response = dict(first_page)
        response["startAt"] = 0
        response["maxResults"] = len(schemas)
        response["total"] = expected_total if expected_total is not None else len(schemas)
        response["values"] = schemas
        if "objectSchemas" in response:
            response["objectSchemas"] = schemas
        response["isLast"] = True
        response["last"] = True
        self._cache.set("schemas_response", response)
        return response

    def fetch_object_types(self, schema_id: str) -> list[dict]:
        """Return all object types for a given schema (flat list)."""
        cache_key = f"objecttypes_{schema_id}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        result = self._client.get(f"/objectschema/{schema_id}/objecttypes/flat")
        self._cache.set(cache_key, result)
        return result

    def fetch_attributes(self, object_type_id: str) -> list[dict]:
        """Return attribute definitions for an object type."""
        cache_key = f"attrs_{object_type_id}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        result = self._client.get(f"/objecttype/{object_type_id}/attributes")
        self._cache.set(cache_key, result)
        return result

    def fetch_status_types(self, schema_id: str | None = None) -> list[dict]:
        """Return status types: global ones when *schema_id* is ``None``,
        otherwise only those defined in that schema."""
        cache_key = f"statustypes_{schema_id or 'global'}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        params = {"objectSchemaId": schema_id} if schema_id else None
        result = self._client.get("/config/statustype", params=params)
        self._cache.set(cache_key, result)
        return result

    def fetch_reference_types(self, schema_id: str | None = None) -> list[dict]:
        """Return reference types: global ones when *schema_id* is ``None``,
        otherwise only those defined in that schema."""
        cache_key = f"referencetypes_{schema_id or 'global'}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        params = {"objectSchemaId": schema_id} if schema_id else None
        result = self._client.get("/config/referencetype", params=params)
        self._cache.set(cache_key, result)
        return result

    def _optional_config_metadata(
        self,
        fetch: Callable[[str | None], list[dict]],
        name: str,
        schema_id: str | None = None,
    ) -> list[dict]:
        """Keep the required schema summary when config metadata is out of scope."""
        try:
            return fetch(schema_id)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in {401, 403}:
                raise
            target = "global" if schema_id is None else f"schema {schema_id}"
            logger.warning(
                "Skipping %s enrichment for %s: Assets config API returned HTTP %s "
                "(requires read:cmdb-config:jira).",
                name, target, exc.response.status_code,
            )
            return []

    # ── High-level summary ───────────────────────────────────────────────

    def build_summary(self) -> str:
        """Build a human-readable summary of the full Assets schema.

        The output is designed to be injected into an LLM prompt so it can
        reason about available object types and attributes when generating
        AQL queries.
        """
        cached = self._cache.get("schema_summary")
        if cached is not None:
            return cached

        lines: list[str] = []

        # Exact status and reference-type names let the translator write
        # `Status = "In Use"` or `refType IN ("Installed")` instead of guessing.
        global_statuses = self._optional_config_metadata(self.fetch_status_types, "status types")
        global_refs = self._optional_config_metadata(self.fetch_reference_types, "reference types")
        if global_statuses:
            lines.append(f"Global status types (all schemas): {_names(global_statuses)}")
        if global_refs:
            lines.append(f"Global reference types (all schemas): {_names(global_refs)}")

        for schema in self.fetch_all_schemas():
            schema_id = schema["id"]
            schema_name = schema.get("name", "Unknown")
            schema_key = schema.get("objectSchemaKey", "N/A")
            lines.append(f"\n## Schema: {schema_name} (ID: {schema_id}, Key: {schema_key})")

            statuses = self._optional_config_metadata(self.fetch_status_types, "status types", schema_id)
            if statuses:
                lines.append(f"Status types: {_names(statuses)}")
            refs = self._optional_config_metadata(self.fetch_reference_types, "reference types", schema_id)
            if refs:
                lines.append(f"Reference types: {_names(refs)}")

            for ot in self.fetch_object_types(schema_id):
                ot_id = ot["id"]
                ot_name = ot.get("name", "Unknown")
                parent = ot.get("parentObjectTypeId", "")
                parent_str = f" (parent: {parent})" if parent else ""
                lines.append(f"\n### Object Type: {ot_name} (ID: {ot_id}){parent_str}")

                for attr in self.fetch_attributes(ot_id):
                    attr_name = attr.get("name", "?")
                    attr_type = attr.get("type", -1)
                    default_type = attr.get("defaultType", {})
                    dt_name = default_type.get("name", "") if default_type else ""

                    type_label = _TYPE_LABELS.get(attr_type, f"Type({attr_type})")
                    if dt_name:
                        type_label = f"{type_label}/{dt_name}"
                    target = (attr.get("referenceObjectType") or {}).get("name")
                    if target:
                        type_label = f"{type_label} -> {target}"

                    lines.append(f"  - {attr_name}: {type_label}")

        summary = "\n".join(lines)
        self._cache.set("schema_summary", summary)
        return summary
