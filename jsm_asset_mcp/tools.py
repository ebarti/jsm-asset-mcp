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
    ) -> dict:
        """Execute an AQL (Asset Query Language) query to search for objects.

        Args:
            query: The AQL query string (e.g. 'objectType = "Laptop"', 'Name LIKE "server"').
            start_at: Starting index for pagination (default: 0).
            max_results: Maximum results to return (default: 25).
            include_attributes: Include object attributes in response (default: True).
        """
        params = {
            "startAt": start_at,
            "maxResults": max_results,
            "includeAttributes": str(include_attributes).lower(),
        }
        return self.deps.client.post("/object/aql", payload={"qlQuery": query}, params=params)

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

    def search_assets(self, question: str, max_results: int = 25) -> dict:
        """Search assets using natural language. The server translates your question into an
        AQL query by first inspecting the schema to understand available object types and
        attributes, then constructing the appropriate query.

        Examples:
            - "Find all laptops assigned to John"
            - "Show servers in the Sydney data center"
            - "List all software licenses expiring this month"
            - "Which network switches have status Active?"

        Args:
            question: A natural language description of the assets you want to find.
            max_results: Maximum results to return (default: 25).
        """
        schema_summary = self.deps.schema.build_summary()
        aql = llm.translate_to_aql(question, schema_summary, self.deps.settings)

        result = self.deps.client.post("/object/aql", payload={"qlQuery": aql}, params={
            "startAt": 0,
            "maxResults": max_results,
            "includeAttributes": "true",
        })
        result["_generated_aql"] = aql
        result["_original_question"] = question
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
