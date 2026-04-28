# Jira Service Management (JSM) Assets Extension

This extension provides an MCP server for interacting with Jira Service Management (JSM) Assets.

## Project Structure

The codebase is a Python package (`jsm_asset_mcp/`) with the following modules:

| Module | Responsibility |
|---|---|
| `config.py` | `Settings` dataclass loaded from env vars, with cached/discovered Jira IDs |
| `client.py` | `AssetsClient` — thin httpx wrapper with auth and base URL |
| `cache.py` | Generic `TTLCache` — domain-agnostic |
| `schema.py` | `SchemaService` — schema introspection and summary builder |
| `llm.py` | Anthropic client factory + natural-language → AQL translation |
| `tools.py` | All MCP tool definitions (thin orchestration) |
| `server.py` | `create_server()` factory — wires dependencies, returns FastMCP |

Entrypoint: `main.py` calls `create_server().run()`.

## Core Capabilities
- **Query Assets**: Use `execute_aql` to query objects based on Asset Query Language (AQL).
- **Search Assets**: Use `search_assets` for natural-language object searches. Claude decides the AQL query and result limit; if the user asks for all matching objects, the tool paginates through all AQL result pages.
- **Retrieve Assets**: Use `get_object` to fetch specific details about an asset by ID.
- **Update Assets**: Use `update_object` to modify an existing asset's attributes.

## Usage Guidelines
- Always construct an accurate AQL query when searching.
- Ensure you have the `object_type_id` when updating an asset.
- When you update an object, follow the attribute structure strictly (i.e., array of `{"objectTypeAttributeId": "...", "objectAttributeValues": [{"value": "..."}]}`).
