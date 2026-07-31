from mcp.server.fastmcp import FastMCP

from media_mcp.config import settings
from media_mcp.tools.coordinated_tools import register_coordinated_tools
from media_mcp.tools.jellyfin_tools import register_jellyfin_tools
from media_mcp.tools.prowlarr_tools import register_prowlarr_tools
from media_mcp.tools.qbit_tools import register_qbit_tools
from media_mcp.tools.radarr_tools import register_radarr_tools
from media_mcp.tools.sonarr_tools import register_sonarr_tools

# host/port must be set at construction time; they are ignored by the stdio transport.
mcp = FastMCP("media-mcp", host=settings.host, port=settings.port)

register_sonarr_tools(mcp)
register_radarr_tools(mcp)
register_qbit_tools(mcp)
register_coordinated_tools(mcp)
register_prowlarr_tools(mcp)
register_jellyfin_tools(mcp)
