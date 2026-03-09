# Jira Service Management (JSM) Assets Extension

This extension provides an MCP server for interacting with Jira Service Management (JSM) Assets.

## Core Capabilities
- **Query Assets**: Use `execute_aql` to query objects based on Asset Query Language (AQL).
- **Retrieve Assets**: Use `get_object` to fetch specific details about an asset by ID.
- **Update Assets**: Use `update_object` to modify an existing asset's attributes.

## Usage Guidelines
- Always construct an accurate AQL query when searching.
- Ensure you have the `object_type_id` when updating an asset.
- When you update an object, follow the attribute structure strictly (i.e., array of `{"objectTypeAttributeId": "...", "objectAttributeValues": [{"value": "..."}]}`).