# Jira Service Management Assets MCP Server

An MCP (Model Context Protocol) server for interfacing with the Jira Cloud Assets REST API. It allows LLMs to query, retrieve, and update Jira Assets (formerly Insight) directly.

## Prerequisites

- Python >= 3.10
- `uv` (recommended) or `pip` for package management
- Jira Cloud account with access to Jira Service Management Premium/Enterprise (Assets feature)
- Jira API token for authentication

## Configuration

The server requires configuration via environment variables. You can provide these by creating a `.env` file in the project root:

```env
JIRA_DOMAIN=your-domain.atlassian.net
JIRA_EMAIL=your-email@example.com
JIRA_API_TOKEN=your_jira_api_token

# Optional: if you know your workspace ID, provide it to skip the discovery request
# JIRA_WORKSPACE_ID=your_workspace_id
```

## Features

- **execute_aql**: Search for objects in Jira Service Management Assets using an AQL (Asset Query Language) query.
- **get_object**: Retrieve the details of a single Jira Service Management Asset object by its unique ID.
- **update_object**: Update an existing object in Jira Service Management Assets.

## Running the Server

Run the server using `uv`:

```bash
uv run main.py
```

Or run via the MCP CLI/extension:
Configure your MCP client to spawn this server via:
```json
{
  "command": "uv",
  "args": ["run", "/path/to/jsm-asset-mcp/main.py"]
}
```
