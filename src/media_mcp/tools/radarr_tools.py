from mcp.server.fastmcp import FastMCP

from media_mcp.clients.base import ArrClientError
from media_mcp.clients.radarr import RadarrClient
from media_mcp.config import settings
from media_mcp.models import (
    MovieLookupResult,
    MovieSummary,
    QualityProfile,
    QueueItem,
    RootFolder,
    SystemStatus,
)


def _client() -> RadarrClient:
    return RadarrClient(str(settings.radarr_url), settings.radarr_api_key)


def register_radarr_tools(mcp: FastMCP) -> None:
    @mcp.tool()
    async def radarr_system_status() -> str:
        """Return Radarr system status (version, health)."""
        try:
            async with _client() as c:
                data = await c.system_status()
            status = SystemStatus(
                app_name=data.get("appName", "Radarr"),
                version=data.get("version", "unknown"),
                url_base=data.get("urlBase", ""),
                is_debug=data.get("isDebug", False),
            )
            return f"Radarr {status.version} — debug={status.is_debug}"
        except ArrClientError as e:
            return f"Error: {e}"

    @mcp.tool()
    async def radarr_list_movies() -> str:
        """List all movies tracked in Radarr."""
        try:
            async with _client() as c:
                data = await c.get_movies()
            movies = [
                MovieSummary(
                    id=m["id"],
                    title=m["title"],
                    year=m.get("year", 0),
                    status=m.get("status", ""),
                    monitored=m.get("monitored", False),
                    has_file=m.get("hasFile", False),
                    tmdb_id=m.get("tmdbId", 0),
                )
                for m in data
            ]
            if not movies:
                return "No movies found in Radarr."
            lines = [f"{'ID':>5}  {'Title':<40}  {'Year'}  {'Status':<12}  {'Mon':>3}  {'File':>4}"]
            lines.append("-" * 80)
            for m in sorted(movies, key=lambda x: x.title):
                lines.append(
                    f"{m.id:>5}  {m.title:<40}  {m.year}  {m.status:<12}  "
                    f"{'yes' if m.monitored else 'no':>3}  {'yes' if m.has_file else 'no':>4}"
                )
            return "\n".join(lines)
        except ArrClientError as e:
            return f"Error: {e}"

    @mcp.tool()
    async def radarr_lookup_movie(term: str) -> str:
        """Search Radarr for a movie by title (for adding a new movie).

        Returns tmdbId needed for radarr_add_movie.
        """
        try:
            async with _client() as c:
                data = await c.lookup_movie(term)
            results = [
                MovieLookupResult(
                    title=m["title"],
                    year=m.get("year", 0),
                    tmdb_id=m.get("tmdbId", 0),
                    overview=(m.get("overview") or "")[:200],
                )
                for m in data[:10]
            ]
            if not results:
                return f"No results for '{term}'."
            lines = [f"Results for '{term}':"]
            for r in results:
                lines.append(f"  [{r.tmdb_id}] {r.title} ({r.year}) — {r.overview}")
            return "\n".join(lines)
        except ArrClientError as e:
            return f"Error: {e}"

    @mcp.tool()
    async def radarr_quality_profiles() -> str:
        """List available quality profiles in Radarr (id + name needed for radarr_add_movie)."""
        try:
            async with _client() as c:
                data = await c.get_quality_profiles()
            profiles = [QualityProfile(id=p["id"], name=p["name"]) for p in data]
            return "\n".join(f"  {p.id}: {p.name}" for p in profiles)
        except ArrClientError as e:
            return f"Error: {e}"

    @mcp.tool()
    async def radarr_root_folders() -> str:
        """List configured root folders in Radarr (path needed for radarr_add_movie)."""
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
    async def radarr_queue() -> str:
        """Show current download queue in Radarr."""
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
    async def radarr_add_movie(
        tmdb_id: int,
        quality_profile_id: int,
        root_folder_path: str,
        confirm: bool = False,
    ) -> str:
        """Add a movie to Radarr by tmdbId.

        Use radarr_lookup_movie to get tmdb_id, radarr_quality_profiles for
        quality_profile_id, and radarr_root_folders for root_folder_path.

        Set confirm=True to actually add; omit or set False for a dry-run preview.
        """
        if not confirm:
            return (
                f"DRY-RUN: Would add movie tmdbId={tmdb_id} with "
                f"qualityProfileId={quality_profile_id}, rootFolderPath='{root_folder_path}'. "
                "Set confirm=True to proceed."
            )
        try:
            async with _client() as c:
                lookup = await c.lookup_movie(f"tmdb:{tmdb_id}")
                if not lookup:
                    return f"Error: No movie found for tmdbId={tmdb_id}."
                entry = lookup[0]
                body = {
                    **entry,
                    "qualityProfileId": quality_profile_id,
                    "rootFolderPath": root_folder_path,
                    "monitored": True,
                    "addOptions": {"searchForMovie": True},
                }
                result = await c.add_movie(body)
            return f"Added movie '{result['title']}' ({result.get('year', '')}) id={result['id']} to Radarr."
        except ArrClientError as e:
            return f"Error: {e}"

    @mcp.tool()
    async def radarr_delete_movie(
        movie_id: int,
        delete_files: bool = False,
        confirm: bool = False,
    ) -> str:
        """Delete a movie from Radarr by its internal id.

        Set delete_files=True to also remove downloaded files from disk.
        Set confirm=True to actually delete; omit or set False for a dry-run preview.
        """
        if not confirm:
            files_note = " AND delete files from disk" if delete_files else ""
            return (
                f"DRY-RUN: Would delete movie id={movie_id}{files_note}. "
                "Set confirm=True to proceed."
            )
        try:
            async with _client() as c:
                await c.delete_movie(movie_id, delete_files=delete_files)
            return f"Deleted movie id={movie_id} from Radarr (delete_files={delete_files})."
        except ArrClientError as e:
            return f"Error: {e}"
