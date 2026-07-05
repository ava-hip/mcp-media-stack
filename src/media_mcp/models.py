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


# Disk-space honesty for the coordinated purge: library files and torrent files are
# usually the SAME bytes (hardlinked), so their sizes must NOT be summed. Since the purge
# removes BOTH sides (library + torrents + cross-seeds), the content's space is really
# reclaimed this time — roughly one copy, not the sum.
def purge_disk_note() -> str:
    return (
        "Disk space: the library files and the torrent files are usually the SAME bytes "
        "(hardlinked), so the two sizes above must NOT be added together. Because this "
        "purge removes BOTH the library files AND every torrent (cross-seeds included), "
        "the space for this content will actually be reclaimed — roughly the larger of "
        "the two sizes, not their sum."
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
    download_id: str | None = None
    tracked_status: str | None = None  # trackedDownloadStatus (ok/warning/error)
    tracked_state: str | None = None  # trackedDownloadState (e.g. importBlocked)
    status_messages: list[str] = []  # flattened statusMessages ("why is it stuck")
    error_message: str | None = None


# Cap the per-item diagnostic lines so a season pack's per-episode messages stay readable.
_MAX_DIAG_LINES = 4


def _flatten_status_messages(status_messages: list[dict] | None) -> list[str]:
    """Flatten Sonarr/Radarr statusMessages [{title, messages[]}] into readable strings.

    Tolerates a null/absent field and empty message arrays.
    """
    out: list[str] = []
    for sm in status_messages or []:
        title = (sm.get("title") or "").strip()
        msgs = [m for m in (sm.get("messages") or []) if m]
        if msgs:
            out.append(f"{title}: {'; '.join(msgs)}" if title else "; ".join(msgs))
        elif title:
            out.append(title)
    return out


def queue_item_from_record(r: dict) -> "QueueItem":
    return QueueItem(
        id=r["id"],
        title=r.get("title", ""),
        status=r.get("status", ""),
        size_mb=round(r.get("size", 0) / 1_048_576, 1),
        sizeleft_mb=round(r.get("sizeleft", 0) / 1_048_576, 1),
        time_left=r.get("timeleft"),
        download_id=r.get("downloadId"),
        tracked_status=r.get("trackedDownloadStatus"),
        tracked_state=r.get("trackedDownloadState"),
        status_messages=_flatten_status_messages(r.get("statusMessages")),
        error_message=r.get("errorMessage"),
    )


def _diagnostic_lines(item: "QueueItem") -> list[str]:
    out: list[str] = []
    if item.tracked_status or item.tracked_state:
        state = "/".join(x for x in (item.tracked_status, item.tracked_state) if x)
        out.append(f"      tracked: {state}")
    if item.error_message:
        out.append(f"      ! {item.error_message}")
    shown = item.status_messages[:_MAX_DIAG_LINES]
    out.extend(f"      • {line}" for line in shown)
    extra = len(item.status_messages) - len(shown)
    if extra > 0:
        out.append(f"      (+{extra} more)")
    return out


def format_queue(records: list[dict]) -> str:
    """Render a download queue, grouping items that share a downloadId (season pack =
    one torrent, many rows) and surfacing the diagnostic fields (why an item is stuck).
    """
    if not records:
        return "Download queue is empty."
    items = [queue_item_from_record(r) for r in records]

    order: list[str] = []
    groups: dict[str, list[QueueItem]] = {}
    for it in items:
        key = it.download_id or f"__id{it.id}"
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(it)

    lines = [f"Queue ({len(items)} item(s) in {len(order)} group(s)):"]
    for key in order:
        grp = groups[key]
        head = grp[0]
        if len(grp) == 1:
            tag = f"[{head.id}]"
            # Per-item sizes vary within a pack, so only show size/ETA for singletons.
            size_part = (
                f"  {head.sizeleft_mb}/{head.size_mb} MB  ETA={head.time_left or 'unknown'}"
            )
        else:
            tag = f"[×{len(grp)}]"
            size_part = ""
        lines.append(f"  {tag} {head.title[:60]}  status={head.status}{size_part}")
        if head.download_id:
            lines.append(f"      downloadId={head.download_id}")
        lines.extend(_diagnostic_lines(head))
    return "\n".join(lines)


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


class QbitTarget(BaseModel):
    name: str
    hash: str
    category: str
    size: int
    kind: str  # "origin" | "sibling"
    match_type: str | None = None  # content_path | name | release (siblings only)


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
