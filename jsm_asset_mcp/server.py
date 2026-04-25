"""Server factory — wires dependencies and returns a ready-to-run FastMCP instance."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from jsm_asset_mcp import tools
from jsm_asset_mcp.cache import TTLCache
from jsm_asset_mcp.client import AssetsClient
from jsm_asset_mcp.config import Settings
from jsm_asset_mcp.schema import SchemaService


def create_server(settings: Settings | None = None) -> FastMCP:
    """Construct and return a fully-wired MCP server.

    Parameters
    ----------
    settings:
        Optional pre-built settings.  When ``None`` (the default) settings
        are loaded from the environment / ``.env`` file.
    """
    if settings is None:
        settings = Settings.from_env()

    # Build the dependency graph
    cache = TTLCache(ttl=600)
    client = AssetsClient(settings)
    schema = SchemaService(client, cache)

    deps = tools.Dependencies(
        settings=settings,
        client=client,
        schema=schema,
    )
    toolset = tools.Toolset(deps)

    # Create the MCP server and register every tool
    mcp = FastMCP("Jira Assets Server")
    for tool_fn in toolset.all_tools:
        mcp.tool()(tool_fn)

    return mcp
