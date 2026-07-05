from mcp.server.fastmcp import FastMCP

from media_mcp.tools.radarr_tools import register_radarr_tools
from media_mcp.tools.sonarr_tools import register_sonarr_tools

mcp = FastMCP("media-mcp")

register_sonarr_tools(mcp)
register_radarr_tools(mcp)
