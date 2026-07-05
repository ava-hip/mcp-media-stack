from pydantic import BaseModel


class SystemStatus(BaseModel):
    app_name: str
    version: str
    url_base: str
    is_debug: bool


class QueueItem(BaseModel):
    id: int
    title: str
    status: str
    size_mb: float
    sizeleft_mb: float
    time_left: str | None


class QualityProfile(BaseModel):
    id: int
    name: str


class RootFolder(BaseModel):
    id: int
    path: str
    free_space_gb: float


# ── Sonarr ──────────────────────────────────────────────────────────────────

class SeriesSummary(BaseModel):
    id: int
    title: str
    year: int
    status: str
    monitored: bool
    missing_episodes: int
    tvdb_id: int


class SeasonSummary(BaseModel):
    season_number: int
    monitored: bool
    episode_file_count: int
    total_episode_count: int
    is_complete: bool
    is_specials: bool


class SeriesLookupResult(BaseModel):
    title: str
    year: int
    tvdb_id: int
    overview: str


class CalendarEpisode(BaseModel):
    id: int
    series_title: str
    season: int
    episode: int
    title: str
    air_date: str
    has_file: bool


# ── Radarr ───────────────────────────────────────────────────────────────────

class MovieSummary(BaseModel):
    id: int
    title: str
    year: int
    status: str
    monitored: bool
    has_file: bool
    tmdb_id: int


class MovieLookupResult(BaseModel):
    title: str
    year: int
    tmdb_id: int
    overview: str
