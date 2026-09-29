# Jira Service Management Assets MCP server

Connect Jira Cloud Assets to an MCP client. The server exposes **18 tools** for schema discovery, AQL search, object, reference and ticket reads, and object create/update/delete. `search_assets` can translate a natural-language question to AQL through the supported [agent-runtime-kit](https://github.com/ebarti/agent-runtime-kit) runtimes. The MCP host (Claude Desktop, Claude Code, Codex, Gemini CLI, or another stdio client) is independent of the translation provider you select.

Read the [complete tool reference](docs/tools.md) and [26 concrete recipes](docs/recipes.md). The [Python stdio example](docs/examples/stdio_client.py) lists all tools **offline by default**; its two optional Jira commands are read-only.

## Install and check the connection

You need Python 3.10+, [`uv`](https://docs.astral.sh/uv/), a Jira Cloud site with Assets enabled, and a Jira identity permitted to read its workspace. Natural-language search additionally needs credentials for one of the supported translation providers. Direct Jira tools do not require a provider SDK or provider key.

```bash
git clone https://github.com/ebarti/jsm-asset-mcp.git
cd jsm-asset-mcp
uv sync --frozen
uv run --frozen python docs/examples/stdio_client.py
```

The last command starts `main.py` over MCP stdio, initializes a session, and prints 18 tool names. Without Jira credentials it makes no Jira or paid-provider request. `uv run --frozen main.py` starts the same server and waits for an MCP client; it is **not** an interactive prompt or HTTP server. Keep protocol output on stdout and operational logs on stderr.

For explicit read-only requests, copy [the environment template](docs/examples/.env.example) to `.env` in the repository root, replace its placeholder values, and keep that file private. The repo ignores `.env`. Then run:

```bash
uv run --frozen python docs/examples/stdio_client.py schemas
uv run --frozen python docs/examples/stdio_client.py aql 'objectType = "Laptop"'
```

Those commands contact your Jira workspace. The AQL type name must exist in your schema. The example never calls create, update, delete, or `search_assets`.

Set `JIRA_DOMAIN` to the site hostname, for example `example.atlassian.net`, without `https://`. Because discovery can send your API token to that host, the server refuses anything that is not a `<site>.atlassian.net` hostname, and requires the cloud and workspace IDs, set or discovered, to be UUIDs. Set `JIRA_EMAIL` and `JIRA_API_TOKEN` for that Jira identity. The server looks up `JIRA_CLOUD_ID` through the site's tenant-info endpoint and `JIRA_WORKSPACE_ID` through the cloud gateway if you omit them. You may set both IDs explicitly to skip discovery; a cloud ID is not an Atlassian organization ID. The Assets API base URL is `https://api.atlassian.com/ex/jira/{cloudId}/jsm/assets/workspace/{workspaceId}/v1`. For classic tokens, workspace discovery can fall back to the site-hosted JSM route after a 401, 403, or 404 from the gateway. [Atlassian's token guidance](https://support.atlassian.com/user-management/docs/manage-api-tokens-for-service-accounts/) explains token types and scopes.

## Natural-language translation providers

Install the extra for the provider you use and set `LLM_PROVIDER` in your private `.env` or host environment. The names below are the exact supported values; `claude` is a runtime name, **not** an `LLM_PROVIDER` value. Every provider uses its runtime's native model unless `LLM_MODEL` overrides it.

| `LLM_PROVIDER` | Runtime / install extra | Authentication |
| --- | --- | --- |
| `anthropic` (default) | Claude / `claude` | `ANTHROPIC_API_KEY` |
| `anthropic-vertex` | Claude / `claude` | `ANTHROPIC_VERTEX_PROJECT_ID`, optional `ANTHROPIC_VERTEX_REGION` (default `global`), and Google Cloud credentials |
| `anthropic-bedrock` | Claude / `claude` | AWS credentials/role and optional `AWS_REGION` (default `us-east-1`) |
| `gemini` | Antigravity / `gemini` | `GEMINI_API_KEY` for Google AI Studio |
| `codex` | Codex / `codex` | Explicit `OPENAI_API_KEY` |
| `antigravity` | Antigravity / `antigravity` | `GEMINI_API_KEY` or `GOOGLE_API_KEY` for the Google API, or Google ADC with `GOOGLE_CLOUD_PROJECT` and optional `GOOGLE_CLOUD_LOCATION` (default `global`) |

```bash
uv sync --extra claude --frozen       # the three Anthropic settings
uv sync --extra codex --frozen        # codex
uv sync --extra gemini --frozen       # gemini; --extra antigravity is equivalent
uv sync --all-extras --frozen         # all advertised runtimes
```

When an MCP host launches the server for `search_assets`, **keep the selected `--extra` or `--all-extras` in its `uv run` command**. A later `uv run` without it can resync the environment and remove provider packages even after `uv sync --extra`. Keep `--frozen` so the shipped lockfile is used even when a machine has an inherited uv freshness cutoff.

The isolated Codex translation process cannot reuse your interactive Codex CLI login or local Codex settings. It requires `OPENAI_API_KEY` and the pinned `openai-codex` SDK/CLI shipped by the extra. Anthropic Vertex and Bedrock use their respective cloud credential chains. Gemini is the Google AI Studio route; `antigravity` is the more general Google API/ADC route. For the latter, use a Google API key or configure Application Default Credentials and a Vertex project (`gcloud auth application-default login` is one local setup path); set `GOOGLE_CLOUD_LOCATION` if `global` is unsuitable. Provider availability, model access, and billing are determined by those services, not by this repository. The server supports these three kit runtimes and six settings, not arbitrary installed providers.

`search_assets` sends the **complete schema summary and your question** to the configured translation runtime. It does not send object results as translator input. Direct tools make no translation-provider call, although the MCP host may process the returned data with its own model. Each translation has a 90-second deadline and no local tool, inherited MCP, plugin, hook, skill, or setting capability. The result is locally validated before the AQL query executes. Set your host's MCP **tool-call** timeout above 90 seconds—180 seconds is a reasonable starting point, and a large schema or slow Jira pages may need longer.

## Connect an MCP host

Use an absolute path to your checkout or extracted extension. Store credentials in the ignored project `.env` or your host's private environment; do not commit filled-in host configuration. These commands launch the same local stdio server. If you want only direct Jira tools, omit `--extra` and keep `--frozen`.

### Claude Desktop

In the Claude Desktop MCP configuration (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS or `%APPDATA%\Claude\claude_desktop_config.json` on Windows), add:

```json
{
  "mcpServers": {
    "jsm-assets": {
      "command": "uv",
      "args": ["run", "--extra", "claude", "--frozen", "--directory", "/absolute/path/to/jsm-asset-mcp", "main.py"]
    }
  }
}
```

Set `LLM_PROVIDER=anthropic` (or a Vertex/Bedrock alias) and its credentials in the server checkout's private `.env`. Your Claude Desktop login is separate from the server's translation-provider credentials.

### Claude Code

Add this entry to a project `.mcp.json`:

```json
{
  "mcpServers": {
    "jsm-assets": {
      "command": "uv",
      "args": ["run", "--extra", "claude", "--frozen", "--directory", "/absolute/path/to/jsm-asset-mcp", "main.py"]
    }
  }
}
```

Or use the [documented stdio CLI form](https://code.claude.com/docs/en/mcp):

```bash
claude mcp add --transport stdio jsm-assets -- uv run --extra claude --frozen --directory /absolute/path/to/jsm-asset-mcp main.py
```

Protect the private `.env` in the server checkout. If you choose `LLM_PROVIDER=gemini` or `codex` while using Claude Code as the host, change this launcher extra to `gemini` or `codex` and set that provider's credentials.

### Codex

From a terminal, add the stdio server with the [Codex MCP CLI](https://developers.openai.com/codex/mcp):

```bash
codex mcp add jsm-assets -- uv run --extra codex --frozen --directory /absolute/path/to/jsm-asset-mcp main.py
```

Alternatively, use `~/.codex/config.toml` or a project `.codex/config.toml`:

```toml
[mcp_servers.jsm_assets]
command = "uv"
args = ["run", "--extra", "codex", "--frozen", "--directory", "/absolute/path/to/jsm-asset-mcp", "main.py"]
cwd = "/absolute/path/to/jsm-asset-mcp"
startup_timeout_sec = 30
tool_timeout_sec = 180
enabled_tools = ["list_object_schemas", "get_object_schema", "list_object_types", "get_object_type_attributes", "get_schema_summary", "execute_aql", "get_object", "get_object_attributes", "get_object_history", "get_connected_tickets", "search_assets"]
```

That allowlist excludes the three write tools from this Codex host. Configure the server's `LLM_PROVIDER=codex` and `OPENAI_API_KEY` in its private `.env`, or use Codex's `env_vars` forwarding for already-exported variables. The Codex MCP host login does **not** authenticate the server's isolated Codex translator.

### Gemini CLI extension

Install from the repository URL in a terminal, then answer the extension's Jira/provider settings prompts:

```bash
gemini extensions install https://github.com/ebarti/jsm-asset-mcp
gemini extensions list
# For a later published version:
gemini extensions update jsm-asset-mcp
```

[Gemini's extension reference](https://geminicli.com/docs/extensions/reference/) documents install, update, and private settings. The extension's `gemini-extension.json` launches `uv run --all-extras --frozen`, because its settings offer all six provider values. The Gemini CLI host can use `LLM_PROVIDER=anthropic`, `codex`, or another supported route; the host and translation provider need not match. Restart Gemini CLI after changing extension settings or updating it.

## Use the tools responsibly

See [tool arguments and response semantics](docs/tools.md) and [inventory, lifecycle, incident, relationship, audit, and write recipes](docs/recipes.md). The server exposes **15 read-oriented tools and 3 write tools**. Set `JSM_READ_ONLY=true` to leave the write tools unregistered, or `JSM_WRITE_SCHEMA_IDS` to limit writes to listed object schemas; see [the write settings](docs/tools.md). The server never asks for approval before a write, so also restrict the host's tool allowlist and the Jira identity's permissions. The Python example only offers offline listing and two read-only calls.

`execute_aql` runs your AQL directly. Its default is one 25-object page; `fetch_all=true` calls total-count and pages until complete, within `JSM_FETCH_ALL_MAX_OBJECTS` (500 objects) and `JSM_MAX_RESULT_BYTES` (1 MiB), beyond which it returns an error rather than a truncated result. `search_assets` may return objects or an exact count based on the question, and its `_generated_aql` field lets you inspect the translation. A count question returns no object values. There is no separate count, grouping, ranking, export, or scheduling tool. To answer “which owner has the most licenses,” fetch the relevant objects and group their owner values in the host or another program; do not treat a natural-language question as a built-in aggregate query.

`list_object_schemas` and `get_schema_summary` use a 600-second cache **per server process** (`JSM_SCHEMA_CACHE_TTL`). With Jira credentials configured, the server builds the schema summary in the background at startup, so it makes Assets requests before any tool call; set `JSM_SCHEMA_PREFETCH=false` to prevent that. After expiry, the previous summary is served while it is rebuilt in the background. Building the summary also caches its internal type and attribute reads. In contrast, the public `get_object_schema`, `list_object_types`, and `get_object_type_attributes` tools fetch fresh data from Jira on each call; object and AQL results are not cached. After a schema change, those direct tools can show new definitions while the summary remains stale until its cache expires or the server restarts. A server handshake only proves stdio startup; try an explicit read-only schema or AQL call to test Jira access.

## Troubleshooting and verification limits

| Symptom | Check |
| --- | --- |
| `uv` not found or server never starts | Install uv and use its absolute executable path in the host config if the host has a different `PATH`. Use an absolute checkout path and `--frozen`. |
| `401` or `403` from Jira | Check the site hostname, email/token pair, token type and scopes, Assets permissions, and whether the cloud/workspace IDs belong to the same site. Do not replace a cloud ID with an organization ID. |
| Empty type or object results | Confirm the schema ID, exact object type/attribute names, workspace, and AQL. An empty result is not proof of a broken connection. |
| `Unknown LLM_PROVIDER` | Use one of the six exact lowercase values in the table. `claude` is not a provider setting. |
| Missing provider extra or model error | Keep the matching `--extra` in every host `uv run`; check provider credentials and that `LLM_MODEL` is supported by that provider. Omit it to use the runtime default. |
| Natural-language tool times out | Raise the host's tool timeout beyond the translator's 90 seconds, especially for large schemas or multiple Jira pages. Check provider availability separately from Jira. |

Local tests and simulated provider responses cover the runtime integration. A separate read-only Jira check covered discovery, schema/type/attribute reads, AQL/count, object attributes/history, and connected tickets; it did not authorize or exercise live writes. Live translation-provider calls were not part of that check. Replace all fictional names and IDs in the examples with your own schema values.

## Testing the provider contracts

CI runs on pull requests and pushes to `main` with Python 3.13 and the frozen all-extras lockfile. It runs the full unittest suite and six visible provider jobs, one each for `anthropic`, `anthropic-vertex`, `anthropic-bedrock`, `gemini`, `antigravity`, and `codex`. Run the same offline checks locally with:

```bash
uv sync --all-extras --frozen
uv run --all-extras --frozen python -m unittest discover -s tests
PYTHONPATH=.:tests uv run --all-extras --frozen python tests/test_provider_contract.py --provider anthropic
```

Replace `anthropic` with any of the other five exact names to run only that provider's contract; the selector rejects unknown names. Default unittest discovery loads the test guard before the other tests, while CI and release workflows preload it at process startup with `PYTHONPATH=.:tests`. The guard clears inherited provider/Jira credentials, keeps dotenv disabled even when a test clears the environment, and blocks non-loopback Python network connections. The tests use the real application and agent-runtime-kit adapters with simulated vendor responses. The full suite also exercises the bundled Codex process against a loopback Responses API and rejects an injected local command. These checks do not verify live authentication, network service behavior, or model responses for any provider.

## License and release notes

MIT; see [LICENSE](LICENSE). See [CHANGELOG.md](CHANGELOG.md) for v1.2.0 changes since v1.1.0.
