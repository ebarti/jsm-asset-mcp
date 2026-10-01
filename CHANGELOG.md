# Changelog

## Unreleased

- Add six read-only import-monitoring tools: `list_import_sources`, `get_import_source`, `get_import_config_status`, `get_last_import_execution`, `get_import_execution_status`, and `get_import_progress`. They call only `GET` endpoints and stay registered with `JSM_READ_ONLY=true`; starting, cancelling, or feeding an import, and changing its mapping or schedule, are not exposed. `list_import_sources` uses `GET /importsource/objectschema/{id}`, the endpoint behind the Assets UI Import tab, which Atlassian does not document; the other five use documented endpoints. Import responses can carry connection credentials and user records, so import sources are reduced to an allow-list of fields (the import-specific configuration to its key names), user records to their display name, and every response is redacted in linear time, each detected credential masked whole whatever its length, and limited by `JSM_MAX_RESULT_BYTES` before and after redaction.
- Bound AQL results returned to the host. `fetch_all` is refused after the total-count call, before any page request, when more than `JSM_FETCH_ALL_MAX_OBJECTS` objects (default 500) match; `max_results`, including one chosen by the translator, is capped at the same value; final merged/deduplicated JSON, including search metadata, is limited by `JSM_MAX_RESULT_BYTES` (default 1 MiB); and pagination accepts advancing underfilled pages while rejecting repeated pages or totals contradicted by a terminal page. Count quantities are not limited by the object cap.
- Build the schema summary in a background thread at startup when Jira credentials are configured, so the first `search_assets` does not pay for the schema crawl; `JSM_SCHEMA_PREFETCH=false` turns this off. Once expired, the previous summary is served while a background refresh runs.
- Make the schema cache lifetime configurable with `JSM_SCHEMA_CACHE_TTL` (seconds, default 600).
- Add `get_object_reference_info` (inbound reference counts by object type and reference type, for impact analysis), `list_status_types`, `list_reference_types`, and `get_usage`.
- Include global and per-schema status and reference type names, and the target type of reference attributes, in the schema summary used by `search_assets`, so translations can use exact values. Label attribute types 3, 5, and 6 (Confluence, Version, Project).
- Add `JSM_READ_ONLY`: when `true`, `create_object`, `update_object`, and `delete_object` are not registered. Default `false`; unrecognised values fail at startup.
- Add `JSM_WRITE_SCHEMA_IDS`: when set, writes are refused outside the listed object schema IDs, and when the target schema cannot be determined. Unset or `*` keeps today's behaviour.
- Refuse non-numeric object, object type, and schema IDs before interpolating them into Assets API paths, so a value such as `1/../../objectschema/2` or a query string cannot reach another route. The client also refuses any path that is not a plain `/segment/segment` route.
- Validate `JIRA_DOMAIN` as a `<site>.atlassian.net` hostname at startup and before each discovery request, since workspace discovery can send the API token to it. Require `JIRA_CLOUD_ID` and `JIRA_WORKSPACE_ID`, from the environment or discovery, to be UUIDs before they reach an authenticated URL.
- Fix `search_assets` with the Claude runtime (`anthropic`, `anthropic-vertex`, `anthropic-bedrock`), which failed every time with "AQL translator unexpectedly reported tool use": the Claude Agent SDK returns structured output through its built-in `StructuredOutput` tool. That tool is now accepted when it produced the parsed output; any other tool use still fails.
- Declare an 8-day dependency cooldown (`[tool.uv] exclude-newer = "P8D"`) so releases younger than 8 days are never locked, and refresh the lockfile under it: `pip-audit` reports no known vulnerability, against advisories for `cryptography`, `pyjwt`, `starlette`, `python-multipart`, `mcp`, `urllib3`, and others before. The cooldown moves `claude-agent-sdk` back to 0.2.157, `google-antigravity` to 0.1.17, and `uvicorn` to 0.53.0.

## 1.2.0

Changes since v1.1.0:

- Route natural-language AQL translation through `agent-runtime-kit` 0.5.2, with Claude, Codex, and Antigravity runtimes and the existing Anthropic, Vertex, Bedrock, and Gemini provider settings. Each runtime uses its native model unless `LLM_MODEL` is set. Provider SDKs are optional extras.
- Isolate translation from local tools, inherited MCP servers, settings, hooks, skills, and plugins. Validate the structured search plan before sending its AQL to Assets.
- Retrieve every schema page, preserve object-type attribute definitions and pagination metadata across AQL pages, and use the cloud gateway for scoped-token workspace discovery.
- Put the Gemini extension manifest at the archive root and launch it from the frozen lockfile with every advertised provider runtime available.
- Correct the Claude Code project setup path and AQL prefix syntax in the documentation. Add a full tool reference, practical recipes, and an offline-first stdio example.

The direct Jira tools are available with a core install. `search_assets` requires the extra and credentials for its selected provider. This release does not add an aggregate query engine, export scheduler, or new provider runtime beyond those listed above.
