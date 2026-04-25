"""Thin HTTP client for the Jira Service Management Assets REST API."""

from __future__ import annotations

from typing import Any

import httpx

from jsm_asset_mcp.config import Settings

_HEADERS = {"Accept": "application/json"}
_TIMEOUT = 30


class AssetsClient:
    """Wraps all HTTP interactions with the JSM Assets v1 REST API.

    Owns base-URL construction, authentication, and the four REST verbs.
    Uses a persistent ``httpx.Client`` for connection pooling.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._http = httpx.Client(timeout=_TIMEOUT, headers=_HEADERS)

    def close(self) -> None:
        """Close the underlying persistent HTTP client."""
        self._http.close()

    def __enter__(self) -> AssetsClient:
        """Return the client for use as a context manager."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object | None,
    ) -> None:
        """Ensure the underlying HTTP client is closed on context exit."""
        self.close()

    # ── URL helpers ──────────────────────────────────────────────────────

    @property
    def base_url(self) -> str:
        """Build the Assets REST API base URL via ``api.atlassian.com``."""
        cloud_id = self._settings.resolve_cloud_id()
        workspace_id = self._settings.resolve_workspace_id()
        return (
            f"https://api.atlassian.com/ex/jira/{cloud_id}"
            f"/jsm/assets/workspace/{workspace_id}/v1"
        )

    # ── HTTP verbs ───────────────────────────────────────────────────────

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """``GET {base_url}{path}``."""
        response = self._http.get(
            f"{self.base_url}{path}",
            auth=self._settings.auth,
            params=params,
        )
        response.raise_for_status()
        return response.json()

    def post(
        self,
        path: str,
        payload: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """``POST {base_url}{path}``."""
        response = self._http.post(
            f"{self.base_url}{path}",
            auth=self._settings.auth,
            params=params,
            json=payload,
        )
        response.raise_for_status()
        return response.json()

    def put(self, path: str, payload: dict[str, Any]) -> Any:
        """``PUT {base_url}{path}``."""
        response = self._http.put(
            f"{self.base_url}{path}",
            auth=self._settings.auth,
            json=payload,
        )
        response.raise_for_status()
        return response.json()

    def delete(self, path: str) -> Any:
        """``DELETE {base_url}{path}``."""
        response = self._http.delete(
            f"{self.base_url}{path}",
            auth=self._settings.auth,
        )
        response.raise_for_status()
        if response.status_code == 204:
            return {"status": "deleted"}
        return response.json()
