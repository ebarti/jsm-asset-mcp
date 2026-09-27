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
  - OpenAI API key (Codex runtime)

## Setup

### 1. Clone and install

```bash
git clone https://github.com/your-org/jsm-asset-mcp.git
cd jsm-asset-mcp
uv sync
```

The core install starts the 14-tool MCP server without installing an LLM provider.
The `search_assets` tool needs a provider extra before it can translate questions.
Install the extra for the provider you use before calling `search_assets`:
`uv sync --extra claude`, `uv sync --extra codex`, or
`uv sync --extra gemini` (`--extra antigravity` is equivalent for Antigravity).
`uv sync --extra all-providers` installs all three runtimes.

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

The `search_assets` tool uses [agent-runtime-kit](https://github.com/ebarti/agent-runtime-kit) 0.5.2 to translate natural language into AQL. The supported runtimes are Claude, Codex, and Antigravity. Existing `anthropic`, `anthropic-vertex`, and `anthropic-bedrock` settings use Claude; `gemini` uses Antigravity with a Google AI Studio key. Set `LLM_MODEL` to override the selected runtime's model. Codex and Antigravity otherwise use their native defaults.

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
uv sync --extra gemini
# or: pip install '.[gemini]'
```

Get an AI Studio API key from https://aistudio.google.com/apikey — no GCP project needed.

```env
LLM_PROVIDER=gemini
GEMINI_API_KEY=your_gemini_api_key
```

#### Option E: Codex

Install `uv sync --extra codex`, then set:

```env
LLM_PROVIDER=codex
OPENAI_API_KEY=your_openai_api_key
# LLM_MODEL=your_supported_codex_model
```

Each translation uses an isolated temporary Codex home and thread so it cannot
load your Codex login, instructions, tools, or MCP servers. It requires an
explicit API key; an existing interactive Codex login is not reused.

#### Option F: Antigravity

Install `uv sync --extra antigravity` and set `LLM_PROVIDER=antigravity`.
Use `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) for the Google API, or Google
Application Default Credentials with a Vertex AI project and location. Use
`LLM_MODEL` only when you want to override the runtime's native model.

Every translation is bounded to 90 seconds and starts with no local tools,
MCP servers, inherited settings, hooks, skills, or plugins. The server checks
the returned structured AQL or search plan before sending a query to Assets.

**Finding your Cloud ID:** Visit `https://your-domain.atlassian.net/_edge/tenant_info` in your browser — the `cloudId` field is what you need.

**Finding your Workspace ID:** The server discovers this automatically through `https://api.atlassian.com/ex/jira/{cloudId}/rest/servicedeskapi/assets/workspace`, which supports scoped API tokens. If that request returns 401, 403, or 404, it also tries the site-hosted JSM route for classic tokens. Set `JIRA_WORKSPACE_ID` to use a known ID without a discovery request.

## Configuring with Claude

### Claude Desktop

Add this to your Claude Desktop config file (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS, `%APPDATA%\Claude\claude_desktop_config.json` on Windows):

```json
{
  "mcpServers": {
    "jsm-assets": {
      "command": "uv",
      "args": ["run", "--extra", "claude", "--directory", "/absolute/path/to/jsm-asset-mcp", "main.py"],
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

Add the MCP server to `.mcp.json` in your project root:

```json
{
  "mcpServers": {
    "jsm-assets": {
      "command": "uv",
      "args": ["run", "--extra", "claude", "--directory", "/absolute/path/to/jsm-asset-mcp", "main.py"],
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
claude mcp add jsm-assets -- uv run --extra claude --directory /absolute/path/to/jsm-asset-mcp main.py
```

Then set the environment variables in your `.env` file or export them in your shell.

### Gemini

See the included `gemini-extension.json` for extension configuration. Its provider
setting offers Claude, Codex, and Antigravity routes, so the extension installs
all provider extras from the shipped lockfile when it starts.

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

For natural-language searches, the configured provider returns a structured search plan with the AQL query, result type, and intended result limit. If the user asks for a count or total, `search_assets` uses `/object/aql/totalcount` for the exact count. If the user asks for all matching objects, it paginates through each `/object/aql` page until all matches are returned. If the user asks for a specific number, that number is used as the result limit. If no limit is specified, the tool's `max_results` parameter is used as the default.

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
Name STARTSWITH "prod-"                          # Prefix
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
