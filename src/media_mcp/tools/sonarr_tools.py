from datetime import date, timedelta

from mcp.server.fastmcp import FastMCP

from media_mcp.clients.base import ArrClientError
from media_mcp.clients.sonarr import SonarrClient
from media_mcp.config import settings
from media_mcp.models import (
    CalendarEpisode,
    EpisodeFileSummary,
    QualityProfile,
    QueueItem,
    RootFolder,
    SeasonSummary,
    SeriesLookupResult,
    SeriesSummary,
    SystemStatus,
    format_size,
    hardlink_note
)


def _season_summary(season: dict) -> SeasonSummary:
    stats = season.get("statistics", {})
    file_count = stats.get("episodeFileCount", 0)
    total_count = stats.get("totalEpisodeCount", 0)
    season_number = season.get("seasonNumber", 0)
    return SeasonSummary(
        season_number=season_number,
        monitored=season.get("monitored", False),
        episode_file_count=file_count,
        total_episode_count=total_count,
        is_complete=total_count > 0 and file_count == total_count,
        is_specials=season_number == 0,
    )


def _client() -> SonarrClient:
    return SonarrClient(str(settings.sonarr_url), settings.sonarr_api_key)


def register_sonarr_tools(mcp: FastMCP) -> None:
    @mcp.tool()
    async def sonarr_system_status() -> str:
        """Return Sonarr system status (version, health)."""
        try:
            async with _client() as c:
                data = await c.system_status()
            status = SystemStatus(
                app_name=data.get("appName", "Sonarr"),
                version=data.get("version", "unknown"),
                url_base=data.get("urlBase", ""),
                is_debug=data.get("isDebug", False),
            )
            return f"Sonarr {status.version} — debug={status.is_debug}"
        except ArrClientError as e:
            return f"Error: {e}"

    @mcp.tool()
    async def sonarr_list_series() -> str:
        """List all monitored and unmonitored series tracked in Sonarr."""
        try:
            async with _client() as c:
                data = await c.get_series()
            series = [
                SeriesSummary(
                    id=s["id"],
                    title=s["title"],
                    year=s.get("year", 0),
                    status=s.get("status", ""),
                    monitored=s.get("monitored", False),
                    missing_episodes=s.get("episodeCount", 0) - s.get("episodeFileCount", 0),
                    tvdb_id=s.get("tvdbId", 0),
                )
                for s in data
            ]
            if not series:
                return "No series found in Sonarr."
            cols = f"{'ID':>5}  {'Title':<40}  {'Year'}  {'Status':<12}  {'Mon':>3}  {'Missing':>7}"
            lines = [cols]
            lines.append("-" * 80)
            for s in sorted(series, key=lambda x: x.title):
                lines.append(
                    f"{s.id:>5}  {s.title:<40}  {s.year}  {s.status:<12}  "
                    f"{'yes' if s.monitored else 'no':>3}  {s.missing_episodes:>7}"
                )
            return "\n".join(lines)
        except ArrClientError as e:
            return f"Error: {e}"

    @mcp.tool()
    async def sonarr_lookup_series(term: str) -> str:
        """Search Sonarr for series by title (for adding a new series).

        Returns tvdbId needed for sonarr_add_series.
        """
        try:
            async with _client() as c:
                data = await c.lookup_series(term)
            results = [
                SeriesLookupResult(
                    title=s["title"],
                    year=s.get("year", 0),
                    tvdb_id=s.get("tvdbId", 0),
                    overview=(s.get("overview") or "")[:200],
                )
                for s in data[:10]
            ]
            if not results:
                return f"No results for '{term}'."
            lines = [f"Results for '{term}':"]
            for r in results:
                lines.append(f"  [{r.tvdb_id}] {r.title} ({r.year}) — {r.overview}")
            return "\n".join(lines)
        except ArrClientError as e:
            return f"Error: {e}"

    @mcp.tool()
    async def sonarr_quality_profiles() -> str:
        """List available quality profiles in Sonarr (id + name needed for sonarr_add_series)."""
        try:
            async with _client() as c:
                data = await c.get_quality_profiles()
            profiles = [QualityProfile(id=p["id"], name=p["name"]) for p in data]
            return "\n".join(f"  {p.id}: {p.name}" for p in profiles)
        except ArrClientError as e:
            return f"Error: {e}"

    @mcp.tool()
    async def sonarr_root_folders() -> str:
        """List configured root folders in Sonarr (path needed for sonarr_add_series)."""
        try:
            async with _client() as c:
                data = await c.get_root_folders()
            folders = [
                RootFolder(
                    id=f["id"],
                    path=f["path"],
                    free_space_gb=round(f.get("freeSpace", 0) / 1_073_741_824, 1),
                )
                for f in data
            ]
            return "\n".join(f"  {f.id}: {f.path}  (free: {f.free_space_gb} GB)" for f in folders)
        except ArrClientError as e:
            return f"Error: {e}"

    @mcp.tool()
    async def sonarr_queue() -> str:
        """Show current download queue in Sonarr."""
        try:
            async with _client() as c:
                data = await c.get_queue()
            records = data.get("records", [])
            if not records:
                return "Download queue is empty."
            items = [
                QueueItem(
                    id=r["id"],
                    title=r.get("title", ""),
                    status=r.get("status", ""),
                    size_mb=round(r.get("size", 0) / 1_048_576, 1),
                    sizeleft_mb=round(r.get("sizeleft", 0) / 1_048_576, 1),
                    time_left=r.get("timeleft"),
                )
                for r in records
            ]
            lines = [f"Queue ({len(items)} items):"]
            for i in items:
                lines.append(
                    f"  [{i.id}] {i.title[:60]}  status={i.status}  "
                    f"{i.sizeleft_mb}/{i.size_mb} MB  ETA={i.time_left or 'unknown'}"
                )
            return "\n".join(lines)
        except ArrClientError as e:
            return f"Error: {e}"

    @mcp.tool()
    async def sonarr_upcoming(days: int = 7) -> str:
        """Show episodes airing in the next N days (default 7) via Sonarr calendar."""
        start = date.today().isoformat()
        end = (date.today() + timedelta(days=days)).isoformat()
        try:
            async with _client() as c:
                data = await c.get_calendar(start=start, end=end)
            if not data:
                return f"No episodes scheduled in the next {days} days."
            episodes = [
                CalendarEpisode(
                    id=e["id"],
                    series_title=e.get("series", {}).get("title", ""),
                    season=e.get("seasonNumber", 0),
                    episode=e.get("episodeNumber", 0),
                    title=e.get("title", ""),
                    air_date=e.get("airDate", ""),
                    has_file=e.get("hasFile", False),
                )
                for e in data
            ]
            lines = [f"Upcoming episodes ({days} days):"]
            for ep in sorted(episodes, key=lambda x: x.air_date):
                flag = "✓" if ep.has_file else " "
                lines.append(
                    f"  [{flag}] {ep.air_date}  {ep.series_title}  "
                    f"S{ep.season:02d}E{ep.episode:02d} — {ep.title}"
                )
            return "\n".join(lines)
        except ArrClientError as e:
            return f"Error: {e}"

    @mcp.tool()
    async def sonarr_add_series(
        tvdb_id: int,
        quality_profile_id: int,
        root_folder_path: str,
        confirm: bool = False,
    ) -> str:
        """Add a series to Sonarr by tvdbId.

        Use sonarr_lookup_series to get tvdb_id, sonarr_quality_profiles for
        quality_profile_id, and sonarr_root_folders for root_folder_path.

        Set confirm=True to actually add; omit or set False for a dry-run preview.
        """
        if not confirm:
            return (
                f"DRY-RUN: Would add series tvdbId={tvdb_id} with "
                f"qualityProfileId={quality_profile_id}, rootFolderPath='{root_folder_path}'. "
                "Set confirm=True to proceed."
            )
        try:
            async with _client() as c:
                lookup = await c.lookup_series(f"tvdb:{tvdb_id}")
                if not lookup:
                    return f"Error: No series found for tvdbId={tvdb_id}."
                entry = lookup[0]
                body = {
                    **entry,
                    "qualityProfileId": quality_profile_id,
                    "rootFolderPath": root_folder_path,
                    "monitored": True,
                    "addOptions": {"searchForMissingEpisodes": True},
                }
                result = await c.add_series(body)
            return f"Added series '{result['title']}' (id={result['id']}) to Sonarr."
        except ArrClientError as e:
            return f"Error: {e}"

    @mcp.tool()
    async def sonarr_delete_series(
        series_id: int,
        delete_files: bool = False,
        confirm: bool = False,
    ) -> str:
        """Delete a series from Sonarr by its internal id.

        Set delete_files=True to also remove downloaded files from disk.
        Set confirm=True to actually delete; omit or set False for a dry-run preview.
        """
        if not confirm:
            files_note = " AND delete files from disk" if delete_files else ""
            return (
                f"DRY-RUN: Would delete series id={series_id}{files_note}. "
                "Set confirm=True to proceed."
            )
        try:
            async with _client() as c:
                await c.delete_series(series_id, delete_files=delete_files)
            return f"Deleted series id={series_id} from Sonarr (delete_files={delete_files})."
        except ArrClientError as e:
            return f"Error: {e}"

    @mcp.tool()
    async def sonarr_series_seasons(series_id: int) -> str:
        """Show a season-by-season breakdown of an already-tracked series.

        For each season: number, whether it is monitored, episodes present/total,
        and whether it is complete. Season 0 is labelled as Specials.
        """
        try:
            async with _client() as c:
                data = await c.get_series_by_id(series_id)
        except ArrClientError as e:
            if "404" in str(e):
                return f"Error: series not found (id={series_id})."
            return f"Error: {e}"

        seasons = [_season_summary(s) for s in data.get("seasons", [])]
        if not seasons:
            return f"Series '{data.get('title', series_id)}' has no seasons."

        lines = [f"Seasons for '{data.get('title', series_id)}' (id={series_id}):"]
        for s in sorted(seasons, key=lambda x: x.season_number):
            label = "Specials" if s.is_specials else f"Season {s.season_number}"
            state = "complete" if s.is_complete else "incomplete"
            lines.append(
                f"  [{'x' if s.monitored else ' '}] {label:<10}  "
                f"{s.episode_file_count}/{s.total_episode_count} episodes  ({state})"
            )
        return "\n".join(lines)

    @mcp.tool()
    async def sonarr_set_season_monitoring(
        series_id: int,
        season_number: int,
        monitored: bool,
    ) -> str:
        """Enable or disable monitoring for a single season of a series.

        Reversible action with no immediate grab, so no confirm is required.
        Returns the season state after the change.
        """
        try:
            async with _client() as c:
                series = await c.get_series_by_id(series_id)
                seasons = series.get("seasons", [])
                target = next(
                    (s for s in seasons if s.get("seasonNumber") == season_number), None
                )
                if target is None:
                    available = ", ".join(str(s.get("seasonNumber")) for s in seasons) or "none"
                    return (
                        f"Error: season {season_number} not found in series id={series_id}. "
                        f"Available seasons: {available}."
                    )
                target["monitored"] = monitored
                await c.update_series(series_id, series)
        except ArrClientError as e:
            if "404" in str(e):
                return f"Error: series not found (id={series_id})."
            return f"Error: {e}"

        label = "Specials" if season_number == 0 else f"season {season_number}"
        state = "monitored" if monitored else "unmonitored"
        return f"'{series.get('title', series_id)}' — {label} is now {state}."

    @mcp.tool()
    async def sonarr_search_season(
        series_id: int,
        season_number: int,
        confirm: bool = False,
    ) -> str:
        """Trigger a search/download for a whole season.

        Set confirm=True to actually launch the search; omit or set False for a
        dry-run preview of what would be searched.
        """
        try:
            async with _client() as c:
                series = await c.get_series_by_id(series_id)
                seasons = series.get("seasons", [])
                target = next(
                    (s for s in seasons if s.get("seasonNumber") == season_number), None
                )
                if target is None:
                    available = ", ".join(str(s.get("seasonNumber")) for s in seasons) or "none"
                    return (
                        f"Error: season {season_number} not found in series id={series_id}. "
                        f"Available seasons: {available}."
                    )
                title = series.get("title", series_id)
                episode_count = target.get("statistics", {}).get("totalEpisodeCount", 0)
                if not confirm:
                    return (
                        f"DRY-RUN: Would search season {season_number} of '{title}' "
                        f"({episode_count} episodes). Set confirm=True to proceed."
                    )
                await c.season_search(series_id, season_number)
        except ArrClientError as e:
            if "404" in str(e):
                return f"Error: series not found (id={series_id})."
            return f"Error: {e}"

        return f"Season search launched for season {season_number} of '{title}'."

    @mcp.tool()
    async def sonarr_delete_season(
        series_id: int,
        season_number: int,
        confirm: bool = False,
    ) -> str:
        """Delete ALL episode files of a single season (destructive).

        Set confirm=True to actually delete; omit or set False for a dry-run preview.
        Handles one season per call. Note: files are removed from Sonarr only —
        hardlinked files are not freed on disk until the torrent is removed too.
        """
        try:
            async with _client() as c:
                series = await c.get_series_by_id(series_id)
                title = series.get("title", series_id)
                files_raw = await c.list_episode_files(series_id)
                files = [
                    EpisodeFileSummary(
                        id=f["id"],
                        season_number=f.get("seasonNumber", -1),
                        relative_path=f.get("relativePath", ""),
                        size=f.get("size", 0),
                    )
                    for f in files_raw
                    if f.get("seasonNumber") == season_number
                ]

                if not files:
                    return f"No episode files found for season {season_number} of '{title}'."

                total_size = sum(f.size for f in files)
                if not confirm:
                    return (
                        f"DRY-RUN: Would delete {len(files)} episode file(s) from "
                        f"season {season_number} of '{title}' — {format_size(total_size)} total.\n"
                        f"{hardlink_note("Sonarr")}\n"
                        "Set confirm=True to proceed."
                    )

                deleted = 0
                freed = 0
                failed = 0
                for f in files:
                    try:
                        await c.delete_episode_file(f.id)
                        deleted += 1
                        freed += f.size
                    except ArrClientError:
                        failed += 1
        except ArrClientError as e:
            if "404" in str(e):
                return f"Error: series not found (id={series_id})."
            return f"Error: {e}"

        summary = (
            f"Deleted {deleted}/{len(files)} episode file(s) from season {season_number} "
            f"of '{title}' — {format_size(freed)} freed in Sonarr."
        )
        if failed:
            summary += f" {failed} file(s) failed."
        return summary

    @mcp.tool()
    async def sonarr_delete_episode_file(
        episode_file_id: int,
        confirm: bool = False,
    ) -> str:
        """Delete a single episode file by its id (destructive).

        Set confirm=True to actually delete; omit or set False for a dry-run preview.
        Note: files are removed from Sonarr only — hardlinked files are not freed on
        disk until the torrent is removed too.
        """
        try:
            async with _client() as c:
                data = await c.get_episode_file(episode_file_id)
                file = EpisodeFileSummary(
                    id=data["id"],
                    season_number=data.get("seasonNumber", -1),
                    relative_path=data.get("relativePath", ""),
                    size=data.get("size", 0),
                )
                if not confirm:
                    return (
                        f"DRY-RUN: Would delete episode file id={episode_file_id} "
                        f"'{file.relative_path}' — {format_size(file.size)}.\n"
                        f"{hardlink_note("Sonarr")}\n"
                        "Set confirm=True to proceed."
                    )
                await c.delete_episode_file(episode_file_id)
        except ArrClientError as e:
            if "404" in str(e):
                return f"Error: episode file not found (id={episode_file_id})."
            return f"Error: {e}"

        return (
            f"Deleted episode file id={episode_file_id} '{file.relative_path}' — "
            f"{format_size(file.size)} freed in Sonarr."
        )
