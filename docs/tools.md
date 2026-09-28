# Tool reference

The server exposes these **18** MCP tools. All names below are exact. Inputs are JSON objects; IDs are strings even when Jira displays them as numbers. The object schema, type, and attribute names in [the recipes](recipes.md) are fictional. Discover IDs with `list_object_schemas`, `list_object_types`, and `get_object_type_attributes` before using them. The `id` from an object response is the input to the object read/write tools; an object's display key (for example `LAB-1`) is useful in AQL but is not substituted for an ID in these calls.

| Tool | Inputs | What it returns |
| --- | --- | --- |
| `list_object_schemas` | None (`{}`) | All schema pages merged into one response with `values`, `total`, and pagination fields. |
| `get_object_schema` | `schema_id` (required) | One schema's details. |
| `list_object_types` | `schema_id` (required) | Flat list of types in that schema. |
| `get_object_type_attributes` | `object_type_id` (required) | Attribute definitions, including `id` values needed for writes. |
| `get_schema_summary` | None (`{}`) | Human-readable names and attribute types across all schemas, with each schema's status and reference type names and the target type of reference attributes. It can be large. |
| `list_status_types` | `schema_id=""` | Status types. Empty returns the global ones; a schema ID adds that schema's own. |
| `list_reference_types` | `schema_id=""` | Reference types (the `refType` names). Empty returns the global ones; a schema ID adds that schema's own. |
| `get_usage` | None (`{}`) | Total object count and the object count per schema. |
| `execute_aql` | `query` (required); `start_at=0`, `max_results=25`, `include_attributes=true`, `fetch_all=false` | One Assets AQL result page by default. With `fetch_all=true`, requests all pages and adds `_page_size`, `_page_count`, `_returned_count`, `_total_count`, and `_pagination_complete`. |
| `get_object` | `object_id` (required) | One object record. |
| `get_object_attributes` | `object_id` (required) | That object's attribute values. |
| `get_object_history` | `object_id` (required) | Change-history response for that object. |
| `get_connected_tickets` | `object_id` (required) | Jira tickets connected to that object. |
| `get_object_reference_info` | `object_id` (required) | Counts of objects referencing that object, by object type and reference type. Not the objects themselves. |
| `search_assets` | `question` (required); `max_results=25`, `fetch_all=false` | A structured plan translated to AQL, then object results or an exact count. See below. |
| `create_object` **(write)** | `object_type_id`, `attributes` (both required) | Created object response. |
| `update_object` **(write)** | `object_id`, `object_type_id`, `attributes` (all required) | Updated object response. |
| `delete_object` **(write)** | `object_id` (required) | Delete response; HTTP 204 becomes `{"status":"deleted"}`. |

`execute_aql` and `search_assets` are different. `execute_aql` uses the AQL you supply and makes no translation-provider call. If `fetch_all` is true, it first calls the Assets total-count endpoint, then fetches pages of `max_results` objects. The merged result includes deduplicated `objectTypeAttributes` definitions from all pages and coherent pagination metadata. A normal single-page call returns the API page without that merge. Start with bounded pages; `fetch_all` can return a large result.

`search_assets` first reads the complete schema summary, then sends that summary **and your question** to the selected external translation runtime. It validates the returned AQL and search plan before calling Assets. A count question uses the Assets total-count endpoint and returns `totalCount`, `total`, and an empty `values` list; there is no separate `count_assets` tool. An object question also obtains `total` from that endpoint before fetching the requested page(s). An explicit limit in the question overrides the tool's `max_results` default; the tool's `fetch_all=true` can request all objects. Its response adds `_generated_aql`, `_original_question`, `_llm_max_results`, `_llm_fetch_all`, and `_result_type`. Inspect `_generated_aql` before relying on a natural-language result. Natural-language translation can misread schema names or intent; use `execute_aql` when you need an exact filter.

The server caches `list_object_schemas` and `get_schema_summary` for **600 seconds per running process**. Summary construction also caches its internal type and attribute reads. Public calls to `get_object_schema`, `list_object_types`, and `get_object_type_attributes` fetch fresh data from Jira, as do object and AQL calls. After a schema change, the direct type/attribute tools can be current while the summary remains stale; restart the MCP process or wait for cache expiry before using the summary. Jira and provider requests can fail independently; a successful `list-tools` handshake does not verify Jira credentials or provider access.

For writes, `attributes` is an array such as:

```json
[
  {
    "objectTypeAttributeId": "301",
    "objectAttributeValues": [{"value": "Disposable test laptop"}]
  }
]
```

The attribute ID must belong to the selected object type. The value shape and required attributes vary with your Assets schema. The server forwards creates, updates, and deletes to Jira; it does **not** impose a read-only policy, ask for confirmation, or provide rollback. Use host tool allowlists and an appropriately scoped Jira identity if you need read-only access. See the deliberately labeled [write sequence](recipes.md#controlled-write-example) before trying a mutation.

[AQL syntax reference](https://support.atlassian.com/assets/docs/use-assets-query-language-aql/) · [Assets object REST API](https://developer.atlassian.com/cloud/assets/rest/api-group-object/)
