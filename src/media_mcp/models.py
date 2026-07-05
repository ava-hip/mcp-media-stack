from pydantic import BaseModel


# Static reminder shown in delete previews: Sonarr only removes its own record/file,
# so hardlinked files keep occupying disk until the torrent is removed in the client too.
def hardlink_note(service: str) -> str:
    return (
        f"Note: files are removed from {service} only. If hardlinked to a torrent "
        "client, disk space is NOT freed until the torrent is also removed there."
    )


# Hardlink reminder from the torrent client's point of view (qBittorrent via qui):
# here we delete the torrent AND its files, so the caveat is the reverse of hardlink_note.
def torrent_hardlink_note() -> str:
    return (
        "The torrent's downloaded files will be deleted. If those files are hardlinked "
        "to your Sonarr/Radarr library, the library copy remains and the disk space is "
        "fully reclaimed only once BOTH the library file(s) and this torrent are removed."
    )


def format_size(num_bytes: int) -> str:
    """Format a byte count as a human-readable string (base 1024), e.g. '18.4 GB'."""
    gib = 1_073_741_824
    mib = 1_048_576
    if num_bytes >= gib:
        return f"{num_bytes / gib:.1f} GB"
    return f"{num_bytes / mib:.1f} MB"


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


class DiskSpaceSummary(BaseModel):
    label: str
    path: str
    free: int
    total: int

    @property
    def pct_free(self) -> float:
        return (self.free / self.total * 100) if self.total else 0.0


class HealthIssue(BaseModel):
    type: str
    source: str
    message: str


class HistoryRecordSummary(BaseModel):
    event_type: str
    source_title: str
    date: str
    download_id: str | None


class TorrentSummary(BaseModel):
    name: str
    hash: str
    state: str
    progress: float  # fraction 0..1 as returned by qBittorrent
    size: int
    ratio: float
    category: str

    @property
    def progress_pct(self) -> int:
        return round(self.progress * 100)


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


class EpisodeFileSummary(BaseModel):
    id: int
    season_number: int
    relative_path: str
    size: int


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


class CalendarMovie(BaseModel):
    title: str
    year: int
    release_date: str
    release_type: str
    monitored: bool
    has_file: bool


class MovieFileSummary(BaseModel):
    id: int
    relative_path: str
    size: int
