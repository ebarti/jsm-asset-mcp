"""Validated configuration loaded from environment variables."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field

import httpx
from dotenv import load_dotenv

logger = logging.getLogger(__name__)
_DISCOVERY_TIMEOUT = 30
# Jira Cloud site hostnames. Discovery can send the API token to this host,
# so anything else is refused.
_JIRA_DOMAIN_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.atlassian\.net$")
# Cloud and workspace IDs are interpolated into authenticated API URLs.
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)


def _validate_jira_domain(domain: str) -> str:
    normalized = domain.strip().lower()
    if not _JIRA_DOMAIN_RE.fullmatch(normalized):
        raise ValueError(
            "JIRA_DOMAIN must be a Jira Cloud site hostname such as example.atlassian.net, "
            f"without scheme, path or port; got {domain!r}."
        )
    return normalized


def _validate_uuid(name: str, value: str, source: str) -> str:
    if not _UUID_RE.fullmatch(value):
        raise ValueError(f"{name} from {source} must be a UUID; got {value!r}.")
    return value


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
        jira_domain = os.environ.get("JIRA_DOMAIN", "")
        jira_cloud_id = os.environ.get("JIRA_CLOUD_ID", "")
        jira_workspace_id = os.environ.get("JIRA_WORKSPACE_ID", "")
        # Fail at startup, not at the first tool call.
        if jira_domain:
            jira_domain = _validate_jira_domain(jira_domain)
        if jira_cloud_id:
            _validate_uuid("JIRA_CLOUD_ID", jira_cloud_id, "the environment")
        if jira_workspace_id:
            _validate_uuid("JIRA_WORKSPACE_ID", jira_workspace_id, "the environment")
        return cls(
            jira_domain=jira_domain,
            jira_email=os.environ.get("JIRA_EMAIL", ""),
            jira_api_token=os.environ.get("JIRA_API_TOKEN", ""),
            jira_workspace_id=jira_workspace_id,
            jira_cloud_id=jira_cloud_id,
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

        domain = _validate_jira_domain(self.jira_domain)
        url = f"https://{domain}/_edge/tenant_info"
        response = httpx.get(url, timeout=_DISCOVERY_TIMEOUT)
        response.raise_for_status()
        cloud_id = response.json().get("cloudId")
        if not cloud_id:
            raise ValueError("Could not discover cloudId from Jira. Set JIRA_CLOUD_ID manually.")
        _validate_uuid("cloudId", str(cloud_id), url)

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
        source_url = gateway_url
        try:
            response = httpx.get(gateway_url, **request_options)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            # Classic tokens may still require the site-hosted JSM route.
            if exc.response.status_code not in {401, 403, 404} or not self.jira_domain:
                raise
            domain = _validate_jira_domain(self.jira_domain)
            legacy_url = f"https://{domain}/rest/servicedeskapi/assets/workspace"
            source_url = legacy_url
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
        _validate_uuid("workspaceId", str(workspace_id), source_url)

        self.jira_workspace_id = workspace_id
        logger.info("Auto-discovered workspaceId: %s", workspace_id)
        return workspace_id
