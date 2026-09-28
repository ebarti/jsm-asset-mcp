"""Validated configuration loaded from environment variables."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

import httpx
from dotenv import load_dotenv

logger = logging.getLogger(__name__)
_DISCOVERY_TIMEOUT = 30
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


def _parse_bool(name: str, raw: str | None, default: bool) -> bool:
    """Parse a boolean environment variable, rejecting unrecognised values.

    Unset or empty means *default*. A typo such as ``JSM_READ_ONLY=ture`` is
    rejected rather than guessed, because this flag guards destructive tools.
    """
    value = (raw or "").strip().lower()
    if not value:
        return default
    if value in _TRUE_VALUES:
        return True
    if value in _FALSE_VALUES:
        return False
    raise ValueError(f"{name} must be one of {sorted(_TRUE_VALUES | _FALSE_VALUES)}, got {raw!r}.")


def _parse_schema_ids(name: str, raw: str | None) -> tuple[frozenset[str], bool]:
    """Parse a comma-separated list of object schema IDs.

    Returns ``(ids, allow_all)``. Unset, empty, or ``*`` allows every schema.
    IDs must be numeric, as returned by the Assets API, so that a schema
    name or key is not mistaken for an ID.
    """
    items = [item.strip() for item in (raw or "").split(",") if item.strip()]
    if not items or items == ["*"]:
        return frozenset(), True
    invalid = [item for item in items if not item.isdigit()]
    if invalid:
        raise ValueError(f"{name} must be comma-separated numeric schema IDs, or * alone; got {invalid!r}.")
    return frozenset(items), False


@dataclass
class Settings:
    """Application settings.

    All values are read from environment variables (or a ``.env`` file) at
    construction time via :meth:`from_env`.

    ``cloud_id`` and ``workspace_id`` support lazy auto-discovery: if they are
    not provided at construction time they will be resolved on first access via
    :meth:`resolve_cloud_id` / :meth:`resolve_workspace_id` and cached on this
    ``Settings`` instance.
    """

    # Jira core
    jira_domain: str = ""
    jira_email: str = ""
    jira_api_token: str = ""
    jira_workspace_id: str = ""
    jira_cloud_id: str = ""

    # When true, create/update/delete tools are not registered at all.
    read_only: bool = False

    # Object schemas the write tools may modify. write_all_schemas is true
    # unless JSM_WRITE_SCHEMA_IDS lists specific schema IDs.
    write_schema_ids: frozenset[str] = frozenset()
    write_all_schemas: bool = True

    # LLM provider selection
    llm_provider: str = ""
    llm_model: str = ""

    # Anthropic API direct
    anthropic_api_key: str = ""

    # Vertex AI
    anthropic_vertex_project_id: str = ""
    anthropic_vertex_region: str = "global"

    # Bedrock
    aws_region: str = "us-east-1"

    # Gemini (Google AI Studio)
    gemini_api_key: str = ""

    # Let each runtime choose its native model unless LLM_MODEL overrides it.
    _model_names: dict[str, str | None] = field(
        default_factory=lambda: {
            "anthropic": None,
            "anthropic-vertex": None,
            "anthropic-bedrock": None,
            "gemini": None,
            "codex": None,
            "antigravity": None,
        },
        repr=False,
    )

    # ── Factories ────────────────────────────────────────────────────────

    @classmethod
    def from_env(cls) -> Settings:
        """Construct settings from environment variables / ``.env`` file."""
        load_dotenv()
        write_schema_ids, write_all_schemas = _parse_schema_ids(
            "JSM_WRITE_SCHEMA_IDS", os.environ.get("JSM_WRITE_SCHEMA_IDS")
        )
        return cls(
            jira_domain=os.environ.get("JIRA_DOMAIN", ""),
            jira_email=os.environ.get("JIRA_EMAIL", ""),
            jira_api_token=os.environ.get("JIRA_API_TOKEN", ""),
            jira_workspace_id=os.environ.get("JIRA_WORKSPACE_ID", ""),
            jira_cloud_id=os.environ.get("JIRA_CLOUD_ID", ""),
            read_only=_parse_bool("JSM_READ_ONLY", os.environ.get("JSM_READ_ONLY"), default=False),
            write_schema_ids=write_schema_ids,
            write_all_schemas=write_all_schemas,
            llm_provider=os.environ.get("LLM_PROVIDER", "anthropic").lower(),
            llm_model=os.environ.get("LLM_MODEL", ""),
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
            anthropic_vertex_project_id=os.environ.get("ANTHROPIC_VERTEX_PROJECT_ID", ""),
            anthropic_vertex_region=os.environ.get("ANTHROPIC_VERTEX_REGION", "global"),
            aws_region=os.environ.get("AWS_REGION", "us-east-1"),
            gemini_api_key=os.environ.get("GEMINI_API_KEY", ""),
        )

    # ── Derived helpers ──────────────────────────────────────────────────

    @property
    def write_scope(self) -> str:
        """Human-readable description of where writes are allowed."""
        if self.write_all_schemas:
            return "all object schemas"
        return "object schemas " + ", ".join(sorted(self.write_schema_ids, key=int))

    @property
    def auth(self) -> tuple[str, str]:
        """HTTP Basic-auth tuple for Jira REST calls."""
        if not self.jira_email or not self.jira_api_token:
            raise ValueError("JIRA_EMAIL and JIRA_API_TOKEN environment variables are required.")
        return (self.jira_email, self.jira_api_token)

    @property
    def active_llm_provider(self) -> str:
        """Return the normalized active LLM provider name."""
        return (self.llm_provider or "anthropic").lower()

    @property
    def model_name(self) -> str | None:
        """Return the model identifier for the active provider."""
        if self.active_llm_provider not in self._model_names:
            raise ValueError(f"Unknown LLM_PROVIDER '{self.active_llm_provider}'. Supported values: {', '.join(sorted(self._model_names))}.")
        return self.llm_model.strip() or self._model_names[self.active_llm_provider]

    def resolve_cloud_id(self) -> str:
        """Return ``cloud_id``, auto-discovering from ``jira_domain`` if needed."""
        if self.jira_cloud_id:
            return self.jira_cloud_id

        if not self.jira_domain:
            raise ValueError("JIRA_DOMAIN environment variable is required if JIRA_CLOUD_ID is not provided.")

        url = f"https://{self.jira_domain}/_edge/tenant_info"
        response = httpx.get(url, timeout=_DISCOVERY_TIMEOUT)
        response.raise_for_status()
        cloud_id = response.json().get("cloudId")
        if not cloud_id:
            raise ValueError("Could not discover cloudId from Jira. Set JIRA_CLOUD_ID manually.")

        self.jira_cloud_id = cloud_id
        logger.info("Auto-discovered cloudId: %s", cloud_id)
        return cloud_id

    def resolve_workspace_id(self) -> str:
        """Return ``workspace_id``, auto-discovering from Jira if needed."""
        if self.jira_workspace_id:
            return self.jira_workspace_id

        cloud_id = self.resolve_cloud_id()
        gateway_url = f"https://api.atlassian.com/ex/jira/{cloud_id}/rest/servicedeskapi/assets/workspace"
        request_options = {
            "auth": self.auth,
            "headers": {"Accept": "application/json"},
            "timeout": _DISCOVERY_TIMEOUT,
        }
        try:
            response = httpx.get(gateway_url, **request_options)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            # Classic tokens may still require the site-hosted JSM route.
            if exc.response.status_code not in {401, 403, 404} or not self.jira_domain:
                raise
            legacy_url = f"https://{self.jira_domain}/rest/servicedeskapi/assets/workspace"
            response = httpx.get(legacy_url, **request_options)
            response.raise_for_status()

        data = response.json()
        workspace_id = (
            data.get("values", [{}])[0].get("workspaceId")
            if data.get("values")
            else data.get("workspaceId")
        )
        if not workspace_id:
            raise ValueError("Could not discover workspaceId from Jira. Set JIRA_WORKSPACE_ID manually.")

        self.jira_workspace_id = workspace_id
        logger.info("Auto-discovered workspaceId: %s", workspace_id)
        return workspace_id
