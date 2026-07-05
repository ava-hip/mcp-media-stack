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


settings = Settings()
