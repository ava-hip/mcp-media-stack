"""Entrypoint: ``python -m media_mcp``.

The transport is chosen by ``MCP_TRANSPORT``. It defaults to stdio, so local development
and the existing Claude Desktop config keep working unchanged; the Docker image sets it
to ``http`` to run as a homelab service on ``HOST``/``PORT``.
"""

from media_mcp.config import settings
from media_mcp.server import mcp

# MCP_TRANSPORT value -> FastMCP transport name. "http" is the friendly alias for the
# current streamable-HTTP transport; "sse" is the older one, kept for older clients.
TRANSPORT_ALIASES: dict[str, str] = {
    "stdio": "stdio",
    "http": "streamable-http",
    "streamable-http": "streamable-http",
    "sse": "sse",
}


def resolve_transport(value: str) -> str:
    """Map an ``MCP_TRANSPORT`` value to a FastMCP transport name (case-insensitive).

    Raises ValueError listing the accepted values if nothing matches, so a typo fails
    at startup instead of silently falling back to stdio.
    """
    key = value.strip().lower()
    if key in TRANSPORT_ALIASES:
        return TRANSPORT_ALIASES[key]
    accepted = ", ".join(sorted(TRANSPORT_ALIASES))
    raise ValueError(f"Unknown MCP_TRANSPORT '{value}'. Accepted values: {accepted}.")


def main() -> None:
    mcp.run(transport=resolve_transport(settings.mcp_transport))


if __name__ == "__main__":
    main()
