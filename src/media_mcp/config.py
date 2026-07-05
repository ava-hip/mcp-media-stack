from pydantic import AnyHttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    sonarr_url: AnyHttpUrl = AnyHttpUrl("http://localhost:8989")
    sonarr_api_key: str = ""

    radarr_url: AnyHttpUrl = AnyHttpUrl("http://localhost:7878")
    radarr_api_key: str = ""

    # qBittorrent access goes through "qui" (autobrr), not qBittorrent directly.
    # Empty values mean "not configured" — the QuiClient raises a clear error.
    qui_url: str = ""
    qui_api_key: str = ""
    # Optional target instance (id or name). If empty and a single instance exists,
    # it is selected automatically; if several exist, resolution raises a clear error.
    qui_instance: str = ""


settings = Settings()
