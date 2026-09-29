"""Server factory — wires dependencies and returns a ready-to-run FastMCP instance."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP

from jsm_asset_mcp import tools
from jsm_asset_mcp.cache import TTLCache
from jsm_asset_mcp.client import AssetsClient
from jsm_asset_mcp.config import Settings
from jsm_asset_mcp.schema import SchemaService

logger = logging.getLogger(__name__)


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
    cache = TTLCache(ttl=settings.schema_cache_ttl)
    client = AssetsClient(settings)
    schema = SchemaService(client, cache, summary_ttl=settings.schema_cache_ttl)

    deps = tools.Dependencies(
        settings=settings,
        client=client,
        schema=schema,
    )
    toolset = tools.Toolset(deps)
    if settings.read_only:
        logger.info("Read-only mode: create/update/delete tools are disabled.")
    elif not settings.write_all_schemas:
        logger.info("Write tools are limited to %s.", settings.write_scope)

    @asynccontextmanager
    async def lifespan(_: FastMCP) -> AsyncIterator[dict[str, object]]:
        # Background thread: the MCP handshake must not wait for the schema
        # crawl, but the first search_assets should not pay for it either.
        # Without credentials (e.g. an offline tool listing) it would only
        # fail, so it is skipped and the first tool call reports the error.
        if settings.schema_prefetch and settings.has_jira_credentials:
            schema.warm()
        try:
            yield {}
        finally:
            client.close()

    # Create the MCP server and register every tool
    mcp = FastMCP("Jira Assets Server", lifespan=lifespan)
    for tool_fn in toolset.all_tools:
        mcp.tool()(tool_fn)

    return mcp
