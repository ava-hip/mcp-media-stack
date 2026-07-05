from mcp.server.fastmcp import FastMCP

from media_mcp.tools.coordinated_tools import register_coordinated_tools
from media_mcp.tools.prowlarr_tools import register_prowlarr_tools
from media_mcp.tools.qbit_tools import register_qbit_tools
from media_mcp.tools.radarr_tools import register_radarr_tools
from media_mcp.tools.sonarr_tools import register_sonarr_tools

mcp = FastMCP("media-mcp")

register_sonarr_tools(mcp)
register_radarr_tools(mcp)
register_qbit_tools(mcp)
register_coordinated_tools(mcp)
register_prowlarr_tools(mcp)
