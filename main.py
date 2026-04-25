"""Entrypoint for the JSM Assets MCP server."""

from jsm_asset_mcp import create_server

if __name__ == "__main__":
    create_server().run()
