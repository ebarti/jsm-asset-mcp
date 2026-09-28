# Changelog

## Unreleased

- Bound AQL results returned to the host. `fetch_all` is refused after the total-count call, before any page request, when more than `JSM_FETCH_ALL_MAX_OBJECTS` objects (default 500) match; `max_results`, including one chosen by the translator, is capped at the same value; any result above `JSM_MAX_RESULT_BYTES` (default 1 MiB) is refused; and a page-count guard stops a loop on inconsistent pagination. Counts are not limited.

## 1.2.0

Changes since v1.1.0:

- Route natural-language AQL translation through `agent-runtime-kit` 0.5.2, with Claude, Codex, and Antigravity runtimes and the existing Anthropic, Vertex, Bedrock, and Gemini provider settings. Each runtime uses its native model unless `LLM_MODEL` is set. Provider SDKs are optional extras.
- Isolate translation from local tools, inherited MCP servers, settings, hooks, skills, and plugins. Validate the structured search plan before sending its AQL to Assets.
- Retrieve every schema page, preserve object-type attribute definitions and pagination metadata across AQL pages, and use the cloud gateway for scoped-token workspace discovery.
- Put the Gemini extension manifest at the archive root and launch it from the frozen lockfile with every advertised provider runtime available.
- Correct the Claude Code project setup path and AQL prefix syntax in the documentation. Add a full tool reference, practical recipes, and an offline-first stdio example.

The direct Jira tools are available with a core install. `search_assets` requires the extra and credentials for its selected provider. This release does not add an aggregate query engine, export scheduler, or new provider runtime beyond those listed above.
