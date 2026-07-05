from mcp.server.fastmcp import FastMCP

from media_mcp.clients.base import ArrClientError
from media_mcp.clients.qui import QuiClient
from media_mcp.clients.radarr import RadarrClient
from media_mcp.clients.sonarr import SonarrClient
from media_mcp.config import settings
from media_mcp.coordinated import delete_each, extract_download_ids, run_purge
from media_mcp.models import EpisodeFileSummary, MovieFileSummary


def _sonarr() -> SonarrClient:
    return SonarrClient(str(settings.sonarr_url), settings.sonarr_api_key)


def _radarr() -> RadarrClient:
    return RadarrClient(str(settings.radarr_url), settings.radarr_api_key)


def _qui() -> QuiClient:
    return QuiClient(settings.qui_url, settings.qui_api_key, settings.qui_instance)


def register_coordinated_tools(mcp: FastMCP) -> None:
    @mcp.tool()
    async def sonarr_purge_season(
        series_id: int,
        season_number: int,
        delete_torrent_files: bool = True,
        include_loose_matches: bool = True,
        confirm: bool = False,
    ) -> str:
        """Purge a whole season everywhere: delete its episode files in Sonarr AND remove
        the matching torrent(s) in qBittorrent-via-qui, cross-seeds included (destructive).

        The torrents are located via the season's history downloadId(s) and their qui
        cross-seed siblings. include_loose_matches=False drops name/release-matched
        siblings (keeps content_path matches). delete_torrent_files also deletes the
        torrents' files from disk. Set confirm=True to execute; False for a dry-run.
        """
        try:
            async with _sonarr() as c:
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
                events = await c.history_series(series_id, season_number)
        except ArrClientError as e:
            if "404" in str(e):
                return f"Error: series not found (id={series_id})."
            return f"Error: {e}"

        download_ids = extract_download_ids(events)

        async def _delete_library() -> tuple[int, int, int]:
            async with _sonarr() as c:
                return await delete_each(c.delete_episode_file, files)

        return await run_purge(
            service_name="Sonarr",
            library_label=f"season {season_number} of '{title}'",
            library_file_count=len(files),
            library_total_size=sum(f.size for f in files),
            download_ids=download_ids,
            delete_library_files=_delete_library,
            make_qui=_qui,
            delete_torrent_files=delete_torrent_files,
            include_loose_matches=include_loose_matches,
            confirm=confirm,
        )

    @mcp.tool()
    async def radarr_purge_movie(
        movie_id: int,
        delete_torrent_files: bool = True,
        include_loose_matches: bool = True,
        confirm: bool = False,
    ) -> str:
        """Purge a movie everywhere: delete its file in Radarr AND remove the matching
        torrent(s) in qBittorrent-via-qui, cross-seeds included (destructive).

        The torrents are located via the movie's history downloadId(s) and their qui
        cross-seed siblings. include_loose_matches=False drops name/release-matched
        siblings (keeps content_path matches). delete_torrent_files also deletes the
        torrents' files from disk. Set confirm=True to execute; False for a dry-run.
        """
        try:
            async with _radarr() as c:
                movie = await c.get_movie(movie_id)
                title = movie.get("title", movie_id)
                year = movie.get("year", "")
                movie_file = movie.get("movieFile")
                files = []
                if movie.get("hasFile") and movie_file:
                    files = [
                        MovieFileSummary(
                            id=movie_file["id"],
                            relative_path=movie_file.get("relativePath", ""),
                            size=movie_file.get("size", 0),
                        )
                    ]
                events = await c.history_movie(movie_id)
        except ArrClientError as e:
            if "404" in str(e):
                return f"Error: movie not found (id={movie_id})."
            return f"Error: {e}"

        download_ids = extract_download_ids(events)

        async def _delete_library() -> tuple[int, int, int]:
            async with _radarr() as c:
                return await delete_each(c.delete_movie_file, files)

        return await run_purge(
            service_name="Radarr",
            library_label=f"'{title}' ({year})",
            library_file_count=len(files),
            library_total_size=sum(f.size for f in files),
            download_ids=download_ids,
            delete_library_files=_delete_library,
            make_qui=_qui,
            delete_torrent_files=delete_torrent_files,
            include_loose_matches=include_loose_matches,
            confirm=confirm,
        )
