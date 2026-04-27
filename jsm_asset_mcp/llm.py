"""LLM integration — Anthropic client factory and AQL translation.

Encapsulates everything related to the Anthropic SDK: provider switching,
model selection, the AQL system prompt, and response parsing.
"""

from __future__ import annotations

import logging

import anthropic

from jsm_asset_mcp.config import Settings

logger = logging.getLogger(__name__)

# ── AQL System Prompt ────────────────────────────────────────────────────

AQL_SYSTEM_PROMPT = """\
You are an AQL (Asset Query Language) expert for Jira Service Management Assets.
Your job is to translate natural language questions into valid AQL queries.

## AQL Syntax Reference

AQL queries filter objects in Jira Assets. Key syntax:

### Operators
- `=` : Exact match (e.g., `objectType = "Server"`)
- `!=` : Not equal
- `LIKE` : Contains substring, case-insensitive (e.g., `Name LIKE "prod"`)
- `NOT LIKE` : Does not contain
- `STARTS WITH` / `NOT STARTS WITH` : Prefix match
- `>`, `<`, `>=`, `<=` : Numeric/date comparisons
- `IN (val1, val2)` / `NOT IN (val1, val2)` : Set membership
- `IS EMPTY` / `IS NOT EMPTY` : Null checks
- `HAVING` : For referenced objects (e.g., `"OS" HAVING (Name = "Windows")`)

### Logical Operators
- `AND` : Both conditions must match
- `OR` : Either condition can match
- Parentheses `()` for grouping

### Special Attributes
- `objectType` : The type of the object (always use exact match with `=`)
- `objectId` : The unique ID of the object
- `objectSchema` : The schema containing the object
- `Name` / `Label` / `Key` : Common built-in attributes

### Sorting
- `ORDER BY <attribute> ASC|DESC`

### Important Rules
- String values must be in double quotes: `Name = "my-server"`
- Object type names are case-sensitive and must match exactly as defined in the schema
- Use `LIKE` for partial/fuzzy text matching
- Use `=` only when you're confident the user wants an exact value
- For referenced object attributes, use `HAVING`: `"Department" HAVING (Name = "Engineering")`
- When the user mentions a plural form (e.g., "laptops"), map it to the singular object type name from the schema
- If the question is ambiguous about which object type to query, pick the most likely one based on context
- If no object type can be determined, search across all objects using attribute filters only

## Output Format

Respond with ONLY the AQL query string. No explanation, no markdown, no quotes around the entire query.
"""


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
