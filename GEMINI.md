# JSM Assets MCP extension

This extension exposes a local stdio MCP server for Jira Service Management Assets. It has 14 tools: 11 for schema/search/object/ticket reads and 3 (`create_object`, `update_object`, `delete_object`) that change Jira data. Tool names and exact JSON inputs are in [docs/tools.md](docs/tools.md); [docs/recipes.md](docs/recipes.md) gives fictional, replaceable examples. Do not infer that an example type or attribute exists in the connected workspace.

The host's Gemini model and this server's optional translation provider are separate. Direct tools such as `list_object_schemas`, `execute_aql`, and `get_object` call Jira without a translation-provider request. `search_assets` gathers the full schema summary, sends it with the question to the selected external `agent-runtime-kit` runtime, validates a structured AQL plan, and then queries Jira. Supported `LLM_PROVIDER` values are `anthropic`, `anthropic-vertex`, `anthropic-bedrock`, `gemini`, `codex`, and `antigravity`; the corresponding runtimes are Claude, Codex, and Antigravity. `LLM_MODEL` is optional. The extension launcher installs all provider extras from its frozen lockfile, but each selected route still needs its own credentials. Isolated Codex translation requires an explicit `OPENAI_API_KEY`; a host Codex/Gemini login is not reused.

Useful read workflow:

1. Call `list_object_schemas`, then `list_object_types` and `get_object_type_attributes` with IDs from the responses. `get_schema_summary` offers a text overview, but can be large.
2. Use `execute_aql` for an exact filter. It returns one page by default; `fetch_all=true` can return a large merged result. For a natural-language question, call `search_assets` and inspect `_generated_aql` before relying on it.
3. Use an object's returned `id` with `get_object`, `get_object_attributes`, `get_object_history`, and `get_connected_tickets` as needed. A display key such as `LAB-1` is not the object ID argument.
4. To summarize or rank data, perform the necessary reads and aggregate in the host. There is no built-in ranking, CSV export, or scheduled workflow.

The server does not enforce read-only access or ask for confirmation before its write tools. Use a host allowlist or a Jira identity without write permission for read-only work. If deliberately creating/updating/deleting a disposable object, discover its type and attribute IDs, verify each write, and delete only the ID returned by your own create call. See the labeled write sequence in the recipes.

Schema lists, type lists, attribute definitions, and summaries are cached for 600 seconds per MCP process. The translation deadline is 90 seconds, so a host tool timeout may need at least 180 seconds when `search_assets` also reads a large schema or multiple Jira pages. A successful MCP connection does not prove Jira or provider credentials work; test those with an explicit read call.

Implementation map: `main.py` starts the server; `server.py` registers tools; `config.py` selects authentication and discovers cloud/workspace IDs; `client.py` calls the Assets REST API; `schema.py` builds cached schema context; `llm.py` uses `agent-runtime-kit` for tool-free AQL translation; and `tools.py` orchestrates the 14 tool calls. No schema or object response should be invented when Jira returns an error or empty result.
