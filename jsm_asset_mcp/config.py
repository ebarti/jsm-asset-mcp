"""Validated configuration loaded from environment variables."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

import httpx
from dotenv import load_dotenv

logger = logging.getLogger(__name__)
_DISCOVERY_TIMEOUT = 30


def _parse_positive_int(name: str, raw: str | None, default: int) -> int:
    value = (raw or "").strip()
    if not value:
        return default
    try:
        parsed = int(value)
    except ValueError:
        raise ValueError(f"{name} must be a positive integer, got {raw!r}.") from None
    if parsed <= 0:
        raise ValueError(f"{name} must be a positive integer, got {raw!r}.")
    return parsed


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

    # Bounds on what an AQL call may return to the host. An object with its
    # attributes is typically 5-15 KiB of JSON, so the byte cap usually
    # applies first; the object cap refuses oversized fetch_all up front.
    fetch_all_max_objects: int = 500
    max_result_bytes: int = 1_048_576

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
        return cls(
            jira_domain=os.environ.get("JIRA_DOMAIN", ""),
            jira_email=os.environ.get("JIRA_EMAIL", ""),
            jira_api_token=os.environ.get("JIRA_API_TOKEN", ""),
            jira_workspace_id=os.environ.get("JIRA_WORKSPACE_ID", ""),
            jira_cloud_id=os.environ.get("JIRA_CLOUD_ID", ""),
            fetch_all_max_objects=_parse_positive_int(
                "JSM_FETCH_ALL_MAX_OBJECTS", os.environ.get("JSM_FETCH_ALL_MAX_OBJECTS"), 500
            ),
            max_result_bytes=_parse_positive_int(
                "JSM_MAX_RESULT_BYTES", os.environ.get("JSM_MAX_RESULT_BYTES"), 1_048_576
            ),
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
