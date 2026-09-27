"""Tool-free AQL translation through agent-runtime-kit.

Encapsulates provider environment selection, the AQL system prompt, and
structured-output parsing for supported agent runtimes.
"""

from __future__ import annotations

import asyncio
import importlib.metadata
import json
import logging
import os
import tempfile
import threading
from collections.abc import Awaitable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_runtime_kit import AgentKit, AgentTask, PermissionProfile

from jsm_asset_mcp.config import Settings

logger = logging.getLogger(__name__)
_MISSING = object()

# ── AQL System Prompt ────────────────────────────────────────────────────

AQL_REFERENCE = """\
You are an AQL (Asset Query Language) expert for Jira Service Management Assets.
Your job is to translate natural language questions into valid AQL queries.

## AQL Syntax Reference

AQL queries filter Jira Assets objects. The basic form is:
`<attribute-or-keyword> <operator> <value-or-function>`.

### Exact Syntax Rules
- AQL keywords and operators are not case-sensitive, but always preserve the exact
  object type, attribute, status, user, group, project, and reference type names
  from the schema or user request.
- Quote any attribute or value containing spaces: `"Belongs to Department"`,
  `"Ted Anderson"`, `objectType = "Asset Details"`.
- Escape double quotes inside quoted attributes or values with a backslash.
  Example: `Name = "15\\" Screen"`.
- Use only attributes that exist in the supplied schema, or documented AQL
  keywords/built-in attributes.

### Dot Notation
- Use dot notation to traverse referenced objects:
  `"Belongs to Department".Name = "HR"`.
- Quote any segment that contains spaces.
- Dot notation can also be used in `ORDER BY` for reference attributes.

### Keywords and Built-ins
- `objectSchema`: schema name, e.g. `objectSchema = "ITSM Schema"`.
- `objectSchemaId`: schema IDs, e.g. `objectSchemaId IN (1, 2)`.
- `objectType`: object type name, e.g. `objectType = "Host"`.
- `objectTypeId`: object type IDs, e.g. `objectTypeId IN (1, 2)`.
- `object`: use with reference functions, e.g. `object HAVING inboundReferences()`.
- `objectId`: numeric ID from the object key without the schema prefix.
- `Key`: use the full object key, e.g. `Key = "ITSM-1111"`.
- Common built-ins include `Name`, `Label`, `Key`, `Created`, and `Updated`.

### Operators
- `=`: case-insensitive equality.
- `==`: case-sensitive equality.
- `!=`: inequality.
- `<`, `>`, `<=`, `>=`: numeric or date comparisons.
- `LIKE` / `NOT LIKE`: substring match or exclusion, case-insensitive.
- `IN (...)` / `NOT IN (...)`: set membership.
- `STARTSWITH` / `ENDSWITH`: case-insensitive prefix/suffix match.
- `IS EMPTY` / `IS NOT EMPTY`: missing or present values.
- `HAVING` / `NOT HAVING`: use with reference and user/group functions.
- `.`: traverse referenced object attributes.
- Combine expressions with `AND`, `OR`, and parentheses.

### Date and Time Functions
- Supported functions: `now()`, `startOfDay()`, `endOfDay()`, `startOfWeek()`,
  `endOfWeek()`, `startOfMonth()`, `endOfMonth()`, `startOfYear()`,
  `endOfYear()`.
- Relative offsets use `m`, `h`, `d`, and `w`, e.g. `Created > "now(-2h 15m)"`
  or `"Employment End Date" < endOfMonth(-90d)`.

### User, Group, and Project Functions
- User attributes: `currentUser()`, `currentReporter()`, `user("admin", "manager")`.
- Group lookup for User attributes: `User IN group("jira-users")`.
- User lookup for Group attributes: `Group HAVING user("currentReporter()")`.
- Project attributes: `currentProject()` only when the query runs in a ticket context.

### Reference Functions
- `inboundReferences(AQL)` / `inR(AQL)`: objects with inbound references matching
  the nested AQL. Empty arguments match any inbound reference.
- `inboundReferences(AQL, referenceTypes)` / `inR(AQL, refTypes)`: also limit by
  reference type with `refType IN ("Depends", "Installed")`.
- `outboundReferences(AQL)` / `outR(AQL)`: objects with outbound references
  matching the nested AQL. Empty arguments match any outbound reference.
- `outboundReferences(AQL, referenceTypes)` / `outR(AQL, refTypes)`: also limit
  by reference type with `refType IN ("Location")`.
- Use `object HAVING ...` or `object NOT HAVING ...` around reference functions.
- Reference function AQL arguments can contain other reference functions.

### Jira Ticket and Object-Type Functions
- `connectedTickets(JQL query)`: objects with connected Jira tickets matching the
  JQL, e.g. `object HAVING connectedTickets(labels IS EMPTY)`.
- If the user asks only for objects with any connected Jira ticket, use
  `connectedTickets()` where an empty argument is accepted by the Assets context.
- `objectTypeAndChildren(Name)` or `objectTypeAndChildren(ID)`: include an object
  type and its child object types, e.g.
  `objectType IN objectTypeAndChildren("Asset Details")`.

### Placeholders
- Preserve Assets placeholders exactly when provided by the user or context, such
  as `${MyCustomField${0}}` or `${Portfolios.label${0}}`.
- Placeholders can be combined with reference functions and dot notation.

### Ordering
- Append `ORDER BY <AttributeName|label> ASC|DESC`.
- If omitted, Assets defaults to ascending order by the object type label.
- Use dot notation for reference attributes in ordering.
- Missing values appear first in ascending order.
- The ordered attribute must exist in the schema; if it is not found in the
  result set, Assets may return an arbitrary order.
- Only one order attribute is supported.

### Important Rules
- String values with spaces or special characters must be in double quotes:
  `Name = "my-server"`, `"Operating System" = "Ubuntu (64-bit)"`.
- Use `LIKE` for partial/fuzzy text matching
- Use `=` only when you're confident the user wants an exact value
- Do not invent object schema IDs or object type IDs. Prefer object schema/type
  names unless the user or schema context provides IDs.
- When the user mentions a plural form (e.g., "laptops"), map it to the singular object type name from the schema
- If the question is ambiguous about which object type to query, pick the most likely one based on context
- If no object type can be determined, search across all objects using attribute filters only
"""

AQL_SYSTEM_PROMPT = AQL_REFERENCE + """\

## Output Format

Return the generated AQL in the structured output field. Do not include
explanatory text.
"""

AQL_QUERY_SCHEMA = {
    "type": "object",
    "properties": {
        "aql": {
            "type": "string",
            "minLength": 1,
            "description": "The generated Assets Query Language query.",
        },
    },
    "required": ["aql"],
    "additionalProperties": False,
}

SEARCH_PLAN_SYSTEM_PROMPT = AQL_REFERENCE + """\

## Result Limit Planning

In addition to the AQL query, decide whether the caller needs objects or a count,
and how many matching objects the caller needs:
- If the user asks for all, every, complete, full, unlimited, or no-limit results,
  set `result_type` to "objects", `fetch_all` to true, and `max_results` to null.
- If the user asks to count objects, asks "how many", or asks for a total, set
  `result_type` to "count", `fetch_all` to false, and `max_results` to null so
  the caller can use the Assets total-count endpoint without fetching objects.
- If the user explicitly asks for a specific number of results, set `max_results`
  to that positive integer, `result_type` to "objects", and `fetch_all` to false.
- If the user does not specify a result limit, set `max_results` to null and
  `result_type` to "objects", and `fetch_all` to false. The caller will use its
  default max_results parameter.
"""

SEARCH_PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "aql": {
            "type": "string",
            "minLength": 1,
            "description": "The generated Assets Query Language query.",
        },
        "max_results": {
            "anyOf": [
                {"type": "integer", "minimum": 1},
                {"type": "null"},
            ],
            "description": (
                "Positive integer when the user explicitly asks for a fixed number "
                "of results; null when the caller should use its default or fetch all."
            ),
        },
        "fetch_all": {
            "type": "boolean",
            "description": "True when the user asks for every matching object.",
        },
        "result_type": {
            "type": "string",
            "enum": ["objects", "count"],
            "description": (
                "Use count when the user asks how many matching objects exist; "
                "use objects for object search/list requests."
            ),
        },
    },
    "required": ["aql", "max_results", "fetch_all", "result_type"],
    "additionalProperties": False,
}


def _to_gemini_schema(schema: dict) -> dict:
    """Convert JSON Schema to Gemini's response_schema dialect."""
    result: dict = {}
    for key, value in schema.items():
        if key == "additionalProperties":
            continue

        if key == "anyOf" and isinstance(value, list):
            non_null = [v for v in value if not (isinstance(v, dict) and v.get("type") == "null")]
            has_null = any(isinstance(v, dict) and v.get("type") == "null" for v in value)
            if has_null and len(non_null) == 1 and isinstance(non_null[0], dict):
                result.update(_to_gemini_schema(non_null[0]))
                result["nullable"] = True
                continue
            result[key] = [_to_gemini_schema(v) for v in value]
            continue

        if key == "properties" and isinstance(value, dict):
            result[key] = {k: _to_gemini_schema(v) for k, v in value.items()}
            continue

        if isinstance(value, dict):
            result[key] = _to_gemini_schema(value)
        elif isinstance(value, list):
            result[key] = [_to_gemini_schema(v) if isinstance(v, dict) else v for v in value]
        else:
            result[key] = value

    return result


@dataclass(frozen=True)
class SearchPlan:
    """AQL plus LLM-selected result limit semantics."""

    aql: str
    max_results: int | None = None
    fetch_all: bool = False
    result_type: str = "objects"


# ── Claude Agent SDK helpers ─────────────────────────────────────────────

def _agent_env(settings: Settings) -> dict[str, str]:
    """Build Claude Agent SDK environment overrides for the selected provider."""
    provider = settings.active_llm_provider

    if provider == "anthropic-vertex":
        if not settings.anthropic_vertex_project_id:
            raise ValueError(
                "ANTHROPIC_VERTEX_PROJECT_ID environment variable is required "
                "when using the 'anthropic-vertex' provider."
            )
        logger.info(
            "Using Claude Agent SDK via Vertex AI (project=%s, region=%s)",
            settings.anthropic_vertex_project_id,
            settings.anthropic_vertex_region,
        )
        return {
            "CLAUDE_CODE_USE_VERTEX": "1",
            "ANTHROPIC_VERTEX_PROJECT_ID": settings.anthropic_vertex_project_id,
            "CLOUD_ML_REGION": settings.anthropic_vertex_region,
        }

    if provider == "anthropic-bedrock":
        logger.info("Using Claude Agent SDK via Amazon Bedrock (region=%s)", settings.aws_region)
        return {
            "CLAUDE_CODE_USE_BEDROCK": "1",
            "AWS_REGION": settings.aws_region,
        }

    if provider == "anthropic":
        logger.info("Using Claude Agent SDK via Anthropic API")
        if not settings.anthropic_api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY environment variable is required "
                "when using the 'anthropic' provider."
            )
        return {"ANTHROPIC_API_KEY": settings.anthropic_api_key}

    raise ValueError(
        f"Unknown LLM_PROVIDER '{provider}'. "
        "Supported values: 'anthropic', 'anthropic-vertex', 'anthropic-bedrock', 'gemini'."
    )


# ── Structured output parsing ───────────────────────────────────────────

def _strip_markdown_fence(text: str) -> str:
    if not text.startswith("```"):
        return text

    lines = text.splitlines()
    if lines:
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


_TRANSLATION_TIMEOUT_SECONDS = 90


def _claude_runtime(settings: Settings) -> Any:
    try:
        from claude_agent_sdk import ClaudeAgentOptions, query
        from agent_runtime_kit.adapters.claude import ClaudeAgentRuntime
    except ImportError as exc:
        raise ImportError("Claude support requires the 'claude' extra: pip install '.[claude]'.") from exc

    class ToolFreeOptions(ClaudeAgentOptions):
        def __init__(
            self,
            *,
            permission_mode: str,
            allowed_tools: list[str] | None = None,
            disallowed_tools: list[str] | None = None,
            **kwargs: Any,
        ) -> None:
            if allowed_tools or kwargs.get("mcp_servers") or kwargs.get("resume"):
                raise ValueError("AQL translation cannot enable tools, MCP servers, or sessions.")
            super().__init__(
                **kwargs,
                permission_mode=permission_mode,
                tools=[],
                allowed_tools=[],
                disallowed_tools=disallowed_tools or [],
                mcp_servers={},
                strict_mcp_config=True,
                setting_sources=[],
                skills=[],
                plugins=[],
                hooks={},
                agents={},
                extra_args={"bare": None},
                max_turns=1,
            )

    return ClaudeAgentRuntime(
        default_model=settings.model_name,
        env=_agent_env(settings),
        options_cls=ToolFreeOptions,
        query_func=query,
    )


def _antigravity_runtime(settings: Settings, data_dir: Path) -> Any:
    try:
        from google.antigravity import CapabilitiesConfig, LocalAgentConfig, types
        from google.antigravity.agent import Agent
        from google.antigravity.hooks import policy
        from agent_runtime_kit.adapters.antigravity import AntigravityAgentRuntime
    except ImportError as exc:
        raise ImportError(
            "Gemini/Antigravity support requires the 'gemini' or 'antigravity' extra: "
            "pip install '.[gemini]'."
        ) from exc

    if settings.active_llm_provider == "gemini" and not settings.gemini_api_key:
        raise ValueError("GEMINI_API_KEY environment variable is required for the 'gemini' provider.")

    class ToolFreeConfig(LocalAgentConfig):
        def __init__(
            self,
            *,
            capabilities: CapabilitiesConfig,
            policies: list[Any],
            workspaces: list[str] | None = None,
            api_key: str | None = None,
            vertex: bool | None = None,
            **kwargs: Any,
        ) -> None:
            if kwargs.get("mcp_servers") or kwargs.get("tools") or kwargs.get("subagents"):
                raise ValueError("AQL translation cannot enable local tools or MCP servers.")
            response_schema = kwargs.pop("response_schema", None)
            for key in ("mcp_servers", "tools", "subagents", "hooks", "triggers", "skills_paths"):
                kwargs.pop(key, None)
            super().__init__(
                **kwargs,
                api_key=api_key,
                vertex=vertex,
                capabilities=CapabilitiesConfig(enabled_tools=[], enable_subagents=False),
                policies=[],
                workspaces=[],
                tools=[],
                hooks=[],
                triggers=[],
                mcp_servers=[],
                subagents=[],
                skills_paths=[],
                response_schema=_to_gemini_schema(response_schema)
                if response_schema is not None else None,
            )

    return AntigravityAgentRuntime(
        default_model=settings.model_name,
        api_key=settings.gemini_api_key or None,
        vertex=False if settings.active_llm_provider == "gemini" else None,
        data_dir=data_dir,
        agent_cls=Agent,
        config_cls=ToolFreeConfig,
        types_module=types,
        policy_module=policy,
    )


def _codex_runtime(settings: Settings, data_dir: Path) -> Any:
    try:
        from openai_codex import ApprovalMode, AsyncCodex, CodexConfig, Sandbox
        from openai_codex.api import AsyncThread
        from openai_codex._approval_mode import _approval_mode_settings
        from openai_codex._sandbox import _sandbox_mode
        from openai_codex.generated.v2_all import ThreadStartParams
        from agent_runtime_kit.adapters.codex import CodexAgentRuntime
    except ImportError as exc:
        raise ImportError("Codex support requires the 'codex' extra: pip install '.[codex]'.") from exc

    if importlib.metadata.version("openai-codex") != "0.154.0":
        raise RuntimeError("Codex tool isolation is verified only with openai-codex 0.154.0.")

    class IsolatedCodex(AsyncCodex):
        async def thread_start(
            self,
            *,
            approval_mode: ApprovalMode,
            sandbox: Sandbox,
            cwd: str | None = None,
            developer_instructions: str | None = None,
            model: str | None = None,
        ) -> AsyncThread:
            if sandbox != Sandbox.read_only:
                raise ValueError("AQL translation requires a read-only Codex sandbox.")
            await self._ensure_initialized()
            approval_policy, approvals_reviewer = _approval_mode_settings(approval_mode)
            params = ThreadStartParams(
                approval_policy=approval_policy,
                approvals_reviewer=approvals_reviewer,
                cwd=cwd,
                developer_instructions=developer_instructions,
                model=model,
                sandbox=_sandbox_mode(sandbox),
                ephemeral=True,
            ).model_dump(mode="json", by_alias=True, exclude_none=True)
            # Pinned app-server experimental API: an explicit empty selection
            # suppresses every environment and its local tool surface.
            params["environments"] = []
            started = await self._client.thread_start(params)
            return AsyncThread(self, started.thread.id)

        async def thread_resume(self, *args: Any, **kwargs: Any) -> Any:
            raise ValueError("AQL translation must never resume a Codex session.")

    isolated_env = {"CODEX_HOME": str(data_dir / "codex-home")}
    (data_dir / "codex-home").mkdir(mode=0o700)
    return CodexAgentRuntime(
        default_model=settings.model_name,
        codex_cls=IsolatedCodex,
        config_cls=CodexConfig,
        sandbox_cls=Sandbox,
        approval_mode_cls=ApprovalMode,
        env=isolated_env,
        config_overrides=(
            "features.plugins=false",
            "features.shell_tool=false",
            "features.view_image=false",
            "web_search=disabled",
            "mcp_servers={}",
        ),
    )


def _build_runtime(settings: Settings, data_dir: Path) -> Any:
    provider = settings.active_llm_provider
    if provider in {"anthropic", "anthropic-vertex", "anthropic-bedrock"}:
        return _claude_runtime(settings)
    if provider in {"gemini", "antigravity"}:
        return _antigravity_runtime(settings, data_dir)
    if provider == "codex":
        return _codex_runtime(settings, data_dir)
    raise ValueError(f"Unknown LLM_PROVIDER '{provider}'.")


async def _query_structured_output(
    prompt: str,
    system_prompt: str,
    schema: dict[str, Any],
    settings: Settings,
    max_tokens: int,
) -> object:
    del max_tokens  # The kit exposes a task deadline, not a portable output-token cap.
    with tempfile.TemporaryDirectory(prefix="jsm-aql-") as temporary_dir:
        data_dir = Path(temporary_dir)
        runtime = _build_runtime(settings, data_dir)
        try:
            task = AgentTask(
                goal=prompt,
                system=system_prompt,
                model=settings.model_name,
                working_directory=data_dir,
                permissions=PermissionProfile(mode="strict", filesystem="read-only"),
                output_schema=schema,
            )
            result = await AgentKit().run(runtime, task=task, timeout=_TRANSLATION_TIMEOUT_SECONDS)
        finally:
            await runtime.aclose()

    if not result.is_success:
        raise ValueError(f"AQL translation failed: {result.error or result.finish_reason}")
    if result.tool_calls:
        raise ValueError("AQL translator unexpectedly reported tool use.")
    if result.parsed_output_available:
        return result.parsed_output
    if result.output:
        try:
            return json.loads(_strip_markdown_fence(result.output))
        except json.JSONDecodeError as exc:
            raise ValueError("AQL translator did not return valid JSON.") from exc
    raise ValueError("AQL translator did not return structured output.")


def _run_async(awaitable: Awaitable[object]) -> object:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(awaitable)

    result: dict[str, object] = {}
    error: dict[str, BaseException] = {}

    def run_in_thread() -> None:
        try:
            result["value"] = asyncio.run(awaitable)
        except BaseException as exc:
            error["value"] = exc

    thread = threading.Thread(target=run_in_thread, daemon=True)
    thread.start()
    thread.join()

    if "value" in error:
        raise error["value"]
    return result.get("value")


def _parse_aql_payload(payload: object) -> str:
    if not isinstance(payload, dict):
        raise ValueError("Claude Agent SDK response must be a JSON object.")

    expected_keys = set(AQL_QUERY_SCHEMA["required"])
    actual_keys = set(payload)
    missing_keys = expected_keys - actual_keys
    if missing_keys:
        missing = ", ".join(sorted(missing_keys))
        raise ValueError(f"Claude Agent SDK response is missing required AQL fields: {missing}.")

    extra_keys = actual_keys - expected_keys
    if extra_keys:
        extra = ", ".join(sorted(extra_keys))
        raise ValueError(f"Claude Agent SDK response contained unexpected AQL fields: {extra}.")

    aql = payload.get("aql")
    if not isinstance(aql, str) or not aql.strip():
        raise ValueError("Claude Agent SDK response did not contain a non-empty AQL query.")
    return aql.strip()


def _parse_search_plan_payload(payload: object) -> SearchPlan:
    if not isinstance(payload, dict):
        raise ValueError("Claude Agent SDK response must be a JSON object.")

    expected_keys = set(SEARCH_PLAN_SCHEMA["required"])
    actual_keys = set(payload)
    missing_keys = expected_keys - actual_keys
    if missing_keys:
        missing = ", ".join(sorted(missing_keys))
        raise ValueError(
            f"Claude Agent SDK response is missing required search plan fields: {missing}."
        )

    extra_keys = actual_keys - expected_keys
    if extra_keys:
        extra = ", ".join(sorted(extra_keys))
        raise ValueError(
            f"Claude Agent SDK response contained unexpected search plan fields: {extra}."
        )

    aql = payload.get("aql")
    if not isinstance(aql, str) or not aql.strip():
        raise ValueError("Claude Agent SDK response did not contain a non-empty AQL query.")

    max_results = payload.get("max_results")
    if max_results is not None:
        if isinstance(max_results, bool) or not isinstance(max_results, int) or max_results <= 0:
            raise ValueError(
                "Claude Agent SDK response max_results must be a positive integer or null."
            )

    fetch_all = payload.get("fetch_all", False)
    if not isinstance(fetch_all, bool):
        raise ValueError("Claude Agent SDK response fetch_all must be a boolean.")

    result_type = payload.get("result_type")
    if result_type not in {"objects", "count"}:
        raise ValueError("Claude Agent SDK response result_type must be 'objects' or 'count'.")

    return SearchPlan(
        aql=aql.strip(),
        max_results=max_results,
        fetch_all=fetch_all,
        result_type=result_type,
    )


def translate_to_aql(
    question: str,
    schema_summary: str,
    settings: Settings,
) -> str:
    """Translate a natural-language question into an AQL query using Claude."""
    payload = _run_async(
        _query_structured_output(
            prompt=(
                f"## Available Schema\n\n{schema_summary}\n\n"
                f"---\n\n"
                f"Translate this question to AQL:\n{question}"
            ),
            system_prompt=AQL_SYSTEM_PROMPT,
            schema=AQL_QUERY_SCHEMA,
            settings=settings,
            max_tokens=512,
        )
    )
    return _parse_aql_payload(payload)


def translate_to_search_plan(
    question: str,
    schema_summary: str,
    settings: Settings,
) -> SearchPlan:
    """Translate a natural-language question into AQL and result-limit semantics."""
    payload = _run_async(
        _query_structured_output(
            prompt=(
                f"## Available Schema\n\n{schema_summary}\n\n"
                f"---\n\n"
                f"Translate this question into a search plan:\n{question}"
            ),
            system_prompt=SEARCH_PLAN_SYSTEM_PROMPT,
            schema=SEARCH_PLAN_SCHEMA,
            settings=settings,
            max_tokens=768,
        )
    )
    return _parse_search_plan_payload(payload)
