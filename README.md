# Jira Service Management Assets MCP Server

An MCP (Model Context Protocol) server for interacting with the Jira Cloud Assets REST API (formerly Insight). Enables LLMs to query, retrieve, create, update, and delete assets, as well as search using natural language.

## Prerequisites

- Python >= 3.10
- [`uv`](https://docs.astral.sh/uv/) (recommended) or `pip`
- Jira Cloud account with JSM Premium or Enterprise (Assets feature)
- Jira API token ([create one here](https://id.atlassian.com/manage-profile/security/api-tokens))
- **One** of the following for AI-powered natural language search:
  - Anthropic API key (direct API access)
  - Google Cloud project with Vertex AI enabled
  - AWS account with Bedrock access
  - Google AI Studio API key (Gemini)

## Setup

### 1. Clone and install

```bash
git clone https://github.com/your-org/jsm-asset-mcp.git
cd jsm-asset-mcp
uv sync
```

### 2. Configure environment variables

Create a `.env` file in the project root:

```env
JIRA_DOMAIN=your-domain.atlassian.net
JIRA_EMAIL=your-email@example.com
JIRA_API_TOKEN=your_jira_api_token

# Optional — auto-discovered if not set:
# JIRA_CLOUD_ID=your_cloud_id
# JIRA_WORKSPACE_ID=your_workspace_id
```

### 3. Configure LLM provider

The `search_assets` tool uses a structured-output LLM call to translate natural language into AQL queries. You can use Claude via the Claude Agent SDK (direct Anthropic API, Vertex AI, or Bedrock) or Gemini via Google AI Studio.

Set `LLM_PROVIDER` to choose your provider:

#### Option A: Anthropic API (default)

```env
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=your_anthropic_api_key
```

#### Option B: Google Vertex AI

Authenticate with Google Cloud:
```bash
gcloud auth application-default login
```

```env
LLM_PROVIDER=anthropic-vertex
ANTHROPIC_VERTEX_PROJECT_ID=your-gcp-project-id
ANTHROPIC_VERTEX_REGION=global   # optional, defaults to global
```

#### Option C: Amazon Bedrock

Ensure AWS credentials are configured (via `~/.aws/credentials`, env vars, or IAM role).

```env
LLM_PROVIDER=anthropic-bedrock
AWS_REGION=us-east-1   # optional, defaults to us-east-1
```

#### Option D: Google AI Studio (Gemini)

Install the Gemini extra:
```bash
uv pip install '.[gemini]'
# or: pip install '.[gemini]'
```

Get an AI Studio API key from https://aistudio.google.com/apikey — no GCP project needed.

```env
LLM_PROVIDER=gemini
GEMINI_API_KEY=your_gemini_api_key
```

**Finding your Cloud ID:** Visit `https://your-domain.atlassian.net/_edge/tenant_info` in your browser — the `cloudId` field is what you need.

**Finding your Workspace ID:** The server discovers this automatically, but you can also find it via the JSM Assets API: `GET https://your-domain.atlassian.net/rest/servicedeskapi/assets/workspace`

## Configuring with Claude

### Claude Desktop

Add this to your Claude Desktop config file (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS, `%APPDATA%\Claude\claude_desktop_config.json` on Windows):

```json
{
  "mcpServers": {
    "jsm-assets": {
      "command": "uv",
      "args": ["run", "--directory", "/absolute/path/to/jsm-asset-mcp", "main.py"],
      "env": {
        "JIRA_DOMAIN": "your-domain.atlassian.net",
        "JIRA_EMAIL": "your-email@example.com",
        "JIRA_API_TOKEN": "your_jira_api_token",
        "LLM_PROVIDER": "anthropic",
        "ANTHROPIC_API_KEY": "your_anthropic_api_key"
      }
    }
  }
}
```

### Claude Code (CLI)

Add the MCP server to your project settings (`.claude/settings.json`):

```json
{
  "mcpServers": {
    "jsm-assets": {
      "command": "uv",
      "args": ["run", "--directory", "/absolute/path/to/jsm-asset-mcp", "main.py"],
      "env": {
        "JIRA_DOMAIN": "your-domain.atlassian.net",
        "JIRA_EMAIL": "your-email@example.com",
        "JIRA_API_TOKEN": "your_jira_api_token",
        "LLM_PROVIDER": "anthropic",
        "ANTHROPIC_API_KEY": "your_anthropic_api_key"
      }
    }
  }
}
```

Or add it via the CLI:

```bash
claude mcp add jsm-assets -- uv run --directory /absolute/path/to/jsm-asset-mcp main.py
```

Then set the environment variables in your `.env` file or export them in your shell.

### Gemini

See the included `gemini-extension.json` for Gemini-specific configuration.

## Features / Available Tools

### Core CRUD

| Tool | Description |
|------|-------------|
| `execute_aql` | Run an AQL (Asset Query Language) query with pagination |
| `get_object` | Get a single asset object by ID |
| `get_object_attributes` | Get all attributes of a specific object |
| `create_object` | Create a new asset object |
| `update_object` | Update an existing asset object |
| `delete_object` | Delete an asset object |

### Schema Introspection

| Tool | Description |
|------|-------------|
| `list_object_schemas` | List all object schemas in the workspace |
| `get_object_schema` | Get details of a specific schema |
| `list_object_types` | List all object types in a schema |
| `get_object_type_attributes` | Get attribute definitions for an object type |
| `get_schema_summary` | Human-readable summary of all schemas, types, and attributes |

### Natural Language Search

| Tool | Description |
|------|-------------|
| `search_assets` | Search assets using natural language — automatically translates to AQL |

### Related Data

| Tool | Description |
|------|-------------|
| `get_object_history` | Get the change history of an object |
| `get_connected_tickets` | Get Jira tickets linked to an asset |

## Natural Language Search

The `search_assets` tool lets you query assets without knowing AQL syntax. It uses the configured LLM to translate natural language into AQL:

1. Inspects and caches the full schema (object types, attributes, and their data types)
2. Sends the schema context and your question to the configured LLM for AQL generation
3. Executes the generated AQL query
4. Returns results along with the generated AQL for transparency

For natural-language searches, Claude returns a structured search plan with the AQL query, result type, and intended result limit. If the user asks for a count or total, `search_assets` uses `/object/aql/totalcount` for the exact count. If the user asks for all matching objects, it paginates through each `/object/aql` page until all matches are returned. If the user asks for a specific number, that number is used as the result limit. If no limit is specified, the tool's `max_results` parameter is used as the default.

Because the translation is AI-powered, it handles complex queries, synonyms, implied filters, and ambiguous phrasing far better than keyword matching. It understands your schema and can reason about which object types and attributes to query.

**Examples:**

```
"Find all laptops assigned to John"
"Show me servers that haven't been updated in the last 6 months"
"Which departments have the most software licenses?"
"List network equipment in the Sydney office that's currently offline"
```

The generated AQL is included in the response (`_generated_aql` field) so you can verify and refine queries.

## AQL Reference

For direct AQL queries via `execute_aql`, here are common patterns:

```
objectType = "Laptop"                           # All objects of a type
Name = "my-server-01"                           # Exact match
Name LIKE "server"                              # Contains
Name STARTS WITH "prod-"                        # Prefix
objectType = "Server" AND Status = "Active"     # Multiple conditions
objectType = "Server" ORDER BY Name ASC         # Sorting
```

## API Base URL

This server uses the official Atlassian Assets REST API:

```
https://api.atlassian.com/ex/jira/{cloudId}/jsm/assets/workspace/{workspaceId}/v1
```

The `cloudId` and `workspaceId` are auto-discovered from your `JIRA_DOMAIN` if not explicitly set.

## Running Standalone

```bash
uv run main.py
```

## License

MIT
