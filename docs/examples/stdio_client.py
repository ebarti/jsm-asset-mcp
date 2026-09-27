"""Read-only MCP stdio example. Default action only lists tool definitions.

Run from the repository root:
    uv run --frozen python docs/examples/stdio_client.py
    uv run --frozen python docs/examples/stdio_client.py schemas
    uv run --frozen python docs/examples/stdio_client.py aql 'objectType = "Laptop"'

The last two commands require Jira credentials. This script never calls a write
tool or a translation provider.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import timedelta
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


ROOT = Path(__file__).resolve().parents[2]


async def run(action: str, query: str | None) -> None:
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[str(ROOT / "main.py")],
        cwd=ROOT,
    )
    async with stdio_client(parameters) as (reader, writer):
        async with ClientSession(
            reader, writer, read_timeout_seconds=timedelta(seconds=120)
        ) as session:
            await session.initialize()
            if action == "list-tools":
                for tool in (await session.list_tools()).tools:
                    print(tool.name)
                return

            # The only reachable call paths are these two read-only tools.
            if action == "schemas":
                result = await session.call_tool("list_object_schemas", {})
            else:
                assert action == "aql" and query is not None
                result = await session.call_tool(
                    "execute_aql", {"query": query, "max_results": 25}
                )
            print(json.dumps(result.model_dump(exclude_none=True), indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="action")
    subcommands.add_parser("list-tools", help="Offline: list MCP tool definitions")
    subcommands.add_parser("schemas", help="Read schemas from your Jira workspace")
    aql = subcommands.add_parser("aql", help="Run a read-only AQL search")
    aql.add_argument("query", help="AQL query using names in your schema")
    args = parser.parse_args()
    asyncio.run(run(args.action or "list-tools", getattr(args, "query", None)))


if __name__ == "__main__":
    main()
