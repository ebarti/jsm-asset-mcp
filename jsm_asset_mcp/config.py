"""Validated configuration loaded from environment variables."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

import httpx
from dotenv import load_dotenv

logger = logging.getLogger(__name__)


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

    # Anthropic provider
    anthropic_provider: str = "anthropic"

    # Anthropic direct
    anthropic_api_key: str = ""

    # Vertex AI
    anthropic_vertex_project_id: str = ""
    anthropic_vertex_region: str = "us-east5"

    # Bedrock
    aws_region: str = "us-east-1"

    # Model names per provider (not user-configurable, but here for clarity)
    _model_names: dict[str, str] = field(
        default_factory=lambda: {
            "anthropic": "claude-haiku-4-5-20251001",
            "vertex": "claude-haiku-4-5-20251001",
            "bedrock": "anthropic.claude-haiku-4-5-20251001-v1:0",
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
            anthropic_provider=os.environ.get("ANTHROPIC_PROVIDER", "anthropic").lower(),
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
            anthropic_vertex_project_id=os.environ.get("ANTHROPIC_VERTEX_PROJECT_ID", ""),
            anthropic_vertex_region=os.environ.get("ANTHROPIC_VERTEX_REGION", "us-east5"),
            aws_region=os.environ.get("AWS_REGION", "us-east-1"),
        )

    # ── Derived helpers ──────────────────────────────────────────────────

    @property
    def auth(self) -> tuple[str, str]:
        """HTTP Basic-auth tuple for Jira REST calls."""
        if not self.jira_email or not self.jira_api_token:
            raise ValueError("JIRA_EMAIL and JIRA_API_TOKEN environment variables are required.")
        return (self.jira_email, self.jira_api_token)

    @property
    def model_name(self) -> str:
        """Return the model identifier for the active provider."""
        return self._model_names.get(self.anthropic_provider, self._model_names["anthropic"])

    def resolve_cloud_id(self) -> str:
        """Return ``cloud_id``, auto-discovering from ``jira_domain`` if needed."""
        if self.jira_cloud_id:
            return self.jira_cloud_id

        if not self.jira_domain:
            raise ValueError("JIRA_DOMAIN environment variable is required if JIRA_CLOUD_ID is not provided.")

        url = f"https://{self.jira_domain}/_edge/tenant_info"
        response = httpx.get(url)
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

        if not self.jira_domain:
            raise ValueError("JIRA_DOMAIN environment variable is required if JIRA_WORKSPACE_ID is not provided.")

        url = f"https://{self.jira_domain}/rest/servicedeskapi/assets/workspace"
        response = httpx.get(url, auth=self.auth, headers={"Accept": "application/json"})
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
