"""LLM integration — Anthropic client factory and AQL translation.

Encapsulates everything related to the Anthropic SDK: provider switching,
model selection, the AQL system prompt, and response parsing.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import anthropic

from jsm_asset_mcp.config import Settings

logger = logging.getLogger(__name__)

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

Respond with ONLY the AQL query string. No explanation, no markdown, no quotes around the entire query.
"""

SEARCH_PLAN_SYSTEM_PROMPT = AQL_REFERENCE + """\

## Result Limit Planning

In addition to the AQL query, decide how many matching objects the caller needs:
- If the user asks for all, every, complete, full, unlimited, or no-limit results,
  set `fetch_all` to true and `max_results` to null.
- If the user asks to count objects, asks "how many", or asks for a total, set
  `fetch_all` to true and `max_results` to null so the caller can inspect every
  matching object.
- If the user explicitly asks for a specific number of results, set `max_results`
  to that positive integer and `fetch_all` to false.
- If the user does not specify a result limit, set `max_results` to null and
  `fetch_all` to false. The caller will use its default max_results parameter.
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
    },
    "required": ["aql", "max_results", "fetch_all"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class SearchPlan:
    """AQL plus LLM-selected result limit semantics."""

    aql: str
    max_results: int | None = None
    fetch_all: bool = False


# ── Client factory ───────────────────────────────────────────────────────

def get_client(settings: Settings) -> anthropic.Anthropic:
    """Build an Anthropic client for the configured provider.

    Supported providers (set via ``ANTHROPIC_PROVIDER`` env var):

    - ``"anthropic"`` (default) — direct API. Requires ``ANTHROPIC_API_KEY``.
    - ``"vertex"`` — Google Cloud Vertex AI.
      Requires ``ANTHROPIC_VERTEX_PROJECT_ID``, optional ``ANTHROPIC_VERTEX_REGION``.
    - ``"bedrock"`` — Amazon Bedrock.
      Optional ``AWS_REGION`` (defaults to ``us-east-1``).
    """
    provider = settings.anthropic_provider

    if provider == "vertex":
        try:
            from anthropic import AnthropicVertex
        except ImportError:
            raise ImportError(
                "The 'anthropic[vertex]' extra is required for Vertex AI support. "
                "Install it with: pip install 'anthropic[vertex]'"
            )
        if not settings.anthropic_vertex_project_id:
            raise ValueError(
                "ANTHROPIC_VERTEX_PROJECT_ID environment variable is required "
                "when using the 'vertex' provider."
            )
        logger.info(
            "Using Anthropic via Vertex AI (project=%s, region=%s)",
            settings.anthropic_vertex_project_id,
            settings.anthropic_vertex_region,
        )
        return AnthropicVertex(
            project_id=settings.anthropic_vertex_project_id,
            region=settings.anthropic_vertex_region,
        )

    if provider == "bedrock":
        try:
            from anthropic import AnthropicBedrock
        except ImportError:
            raise ImportError(
                "The 'anthropic[bedrock]' extra is required for Bedrock support. "
                "Install it with: pip install 'anthropic[bedrock]'"
            )
        logger.info("Using Anthropic via Amazon Bedrock (region=%s)", settings.aws_region)
        return AnthropicBedrock(aws_region=settings.aws_region)

    if provider == "anthropic":
        logger.info("Using Anthropic API directly")
        if not settings.anthropic_api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY environment variable is required "
                "when using the 'anthropic' provider."
            )
        return anthropic.Anthropic(api_key=settings.anthropic_api_key)

    raise ValueError(
        f"Unknown ANTHROPIC_PROVIDER '{provider}'. "
        f"Supported values: 'anthropic', 'vertex', 'bedrock'."
    )


# ── AQL translation ─────────────────────────────────────────────────────

def _message_text(content: list[object]) -> str:
    text_blocks = []
    for block in content:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            text_blocks.append(text)

    text = "\n".join(text_blocks).strip()
    if not text:
        raise ValueError("Anthropic response did not contain text content.")
    return text


def _strip_markdown_fence(text: str) -> str:
    if not text.startswith("```"):
        return text

    lines = text.splitlines()
    if lines:
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _parse_search_plan(text: str) -> SearchPlan:
    payload = json.loads(_strip_markdown_fence(text))
    if not isinstance(payload, dict):
        raise ValueError("Anthropic response must be a JSON object.")

    aql = payload.get("aql")
    if not isinstance(aql, str) or not aql.strip():
        raise ValueError("Anthropic response did not contain a non-empty AQL query.")

    max_results = payload.get("max_results")
    if max_results is not None:
        if not isinstance(max_results, int) or max_results <= 0:
            raise ValueError("Anthropic response max_results must be a positive integer or null.")

    fetch_all = payload.get("fetch_all", False)
    if not isinstance(fetch_all, bool):
        raise ValueError("Anthropic response fetch_all must be a boolean.")

    return SearchPlan(aql=aql.strip(), max_results=max_results, fetch_all=fetch_all)


def translate_to_aql(
    question: str,
    schema_summary: str,
    settings: Settings,
) -> str:
    """Translate a natural-language question into an AQL query using Claude."""
    client = get_client(settings)

    message = client.messages.create(
        model=settings.model_name,
        max_tokens=512,
        system=AQL_SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": (
                    f"## Available Schema\n\n{schema_summary}\n\n"
                    f"---\n\n"
                    f"Translate this question to AQL:\n{question}"
                ),
            }
        ],
    )

    return _strip_markdown_fence(_message_text(message.content))


def translate_to_search_plan(
    question: str,
    schema_summary: str,
    settings: Settings,
) -> SearchPlan:
    """Translate a natural-language question into AQL and result-limit semantics."""
    client = get_client(settings)

    message = client.messages.create(
        model=settings.model_name,
        max_tokens=768,
        system=SEARCH_PLAN_SYSTEM_PROMPT,
        output_config={
            "format": {
                "type": "json_schema",
                "schema": SEARCH_PLAN_SCHEMA,
            }
        },
        messages=[
            {
                "role": "user",
                "content": (
                    f"## Available Schema\n\n{schema_summary}\n\n"
                    f"---\n\n"
                    f"Translate this question into a search plan:\n{question}"
                ),
            }
        ],
    )

    return _parse_search_plan(_message_text(message.content))
