"""Schema introspection and human-readable summary builder.

Fetches object schemas, object types, and attribute definitions from the
JSM Assets API and assembles them into a text summary used as LLM context
for natural-language → AQL translation.

Building the summary costs one API call per object type, which can take
about a minute on a large workspace, so it can be prefetched in the
background at startup (:meth:`SchemaService.warm`) and, once expired, is
served stale while a background refresh runs.
"""

from __future__ import annotations

import logging
import threading
import time

from jsm_asset_mcp.cache import TTLCache
from jsm_asset_mcp.client import AssetsClient

logger = logging.getLogger(__name__)

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

    def __init__(self, client: AssetsClient, cache: TTLCache, summary_ttl: float = 600) -> None:
        self._client = client
        self._cache = cache
        self._summary_ttl = summary_ttl
        self._summary: str | None = None
        self._summary_built_at = 0.0
        self._lock = threading.Lock()
        self._refresh_thread: threading.Thread | None = None

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

    # ── High-level summary ───────────────────────────────────────────────

    def build_summary(self) -> str:
        """Return the human-readable summary of the full Assets schema.

        The output is designed to be injected into an LLM prompt so it can
        reason about available object types and attributes when generating
        AQL queries.

        A fresh summary is returned as is. An expired one is still returned,
        and a background refresh is started. With no summary yet, the call
        waits for an in-flight prefetch, or builds synchronously.
        """
        with self._lock:
            if self._summary is not None:
                if time.monotonic() - self._summary_built_at >= self._summary_ttl:
                    self._start_refresh_locked()
                return self._summary
            thread = self._refresh_thread

        if thread is not None:
            thread.join()
            with self._lock:
                if self._summary is not None:
                    return self._summary

        # No summary and no prefetch, or the prefetch failed: build now so
        # the error, if any, reaches the caller.
        return self._refresh()

    def warm(self) -> None:
        """Start building the summary in the background, without blocking."""
        with self._lock:
            self._start_refresh_locked()

    def _start_refresh_locked(self) -> None:
        if self._refresh_thread is not None and self._refresh_thread.is_alive():
            return
        self._refresh_thread = threading.Thread(
            target=self._refresh_in_background,
            name="schema-summary-refresh",
            daemon=True,
        )
        self._refresh_thread.start()

    def _refresh_in_background(self) -> None:
        started = time.monotonic()
        try:
            summary = self._refresh()
        except Exception:
            logger.warning("Background schema summary refresh failed.", exc_info=True)
            return
        logger.info(
            "Schema summary ready: %d characters in %.1fs.",
            len(summary),
            time.monotonic() - started,
        )

    def _refresh(self) -> str:
        summary = self._compute_summary()
        with self._lock:
            self._summary = summary
            self._summary_built_at = time.monotonic()
        return summary

    def _compute_summary(self) -> str:
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

        return "\n".join(lines)
