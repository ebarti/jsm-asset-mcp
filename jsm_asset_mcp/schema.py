"""Schema introspection and human-readable summary builder.

Fetches object schemas, object types, and attribute definitions from the
JSM Assets API and assembles them into a text summary used as LLM context
for natural-language → AQL translation.
"""

from __future__ import annotations

from jsm_asset_mcp.cache import TTLCache
from jsm_asset_mcp.client import AssetsClient

# Type-code → human-readable label mapping
_TYPE_LABELS: dict[int, str] = {
    0: "Default",
    1: "Object Reference",
    2: "User",
    4: "Group",
    7: "Status",
}


class SchemaService:
    """Cacheable schema introspection backed by an :class:`AssetsClient`."""

    def __init__(self, client: AssetsClient, cache: TTLCache) -> None:
        self._client = client
        self._cache = cache

    # ── Low-level fetchers (cached) ──────────────────────────────────────

    def fetch_all_schemas(self) -> list[dict]:
        """Return all object schemas in the workspace."""
        cached = self._cache.get("schemas")
        if cached is not None:
            return cached
        result = self._client.get("/objectschema/list")
        schemas = result.get("values", result.get("objectSchemas", []))
        self._cache.set("schemas", schemas)
        return schemas

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

        for schema in self.fetch_all_schemas():
            schema_id = schema["id"]
            schema_name = schema.get("name", "Unknown")
            schema_key = schema.get("objectSchemaKey", "N/A")
            lines.append(f"\n## Schema: {schema_name} (ID: {schema_id}, Key: {schema_key})")

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

                    lines.append(f"  - {attr_name}: {type_label}")

        summary = "\n".join(lines)
        self._cache.set("schema_summary", summary)
        return summary
