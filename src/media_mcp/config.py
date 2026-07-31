from pydantic import AnyHttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Transport used by __main__. "stdio" stays the default so local dev and the existing
    # Claude Desktop config keep working untouched; "http"/"sse" are for the container.
    # host/port are only read by the HTTP transports.
    mcp_transport: str = "stdio"
    host: str = "0.0.0.0"
    port: int = 8080

    sonarr_url: AnyHttpUrl = AnyHttpUrl("http://localhost:8989")
    sonarr_api_key: str = ""

    radarr_url: AnyHttpUrl = AnyHttpUrl("http://localhost:7878")
    radarr_api_key: str = ""

    # Prowlarr (indexer manager). Servarr API but on /api/v1 (not v3).
    prowlarr_url: str = ""
    prowlarr_api_key: str = ""

    # qBittorrent access goes through "qui" (autobrr), not qBittorrent directly.
    # Empty values mean "not configured" — the QuiClient raises a clear error.
    qui_url: str = ""
    qui_api_key: str = ""
    # Optional target instance (id or name). If empty and a single instance exists,
    # it is selected automatically; if several exist, resolution raises a clear error.
    qui_instance: str = ""

    # Jellyfin (media server for curated collections / BoxSets). Endpoints live at the
    # server ROOT (no /api/vN prefix); auth is Authorization: MediaBrowser Token="<key>".
    # Empty values mean "not configured" — the JellyfinClient raises a clear error and the
    # server still starts.
    jellyfin_url: str = ""
    jellyfin_api_key: str = ""


settings = Settings()
