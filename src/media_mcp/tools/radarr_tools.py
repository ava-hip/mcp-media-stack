import unicodedata
from datetime import date, timedelta

from mcp.server.fastmcp import FastMCP

from media_mcp.clients.base import ArrClientError
from media_mcp.clients.radarr import RadarrClient
from media_mcp.config import settings
from media_mcp.coordinated import run_delete_queue
from media_mcp.models import (
    CalendarMovie,
    DiskSpaceSummary,
    HealthIssue,
    HistoryRecordSummary,
    MovieFileSummary,
    MovieLookupResult,
    MovieSummary,
    QualityProfile,
    RootFolder,
    SystemStatus,
    format_queue,
    format_size,
    hardlink_note,
)


def _relevant_release(movie: dict) -> tuple[str, str]:
    """Return (release_date, release_type) using digital > physical > cinema priority."""
    for key, label in (
        ("digitalRelease", "digital"),
        ("physicalRelease", "physical"),
        ("inCinemas", "cinema"),
    ):
        value = movie.get(key)
        if value:
            return value[:10], label
    return "", "unknown"


def _client() -> RadarrClient:
    return RadarrClient(str(settings.radarr_url), settings.radarr_api_key)


def _clean_title(title: str) -> str:
    """Strip Unicode format/control characters from a title.

    TMDB ships some titles with invisible bidi marks: tmdbId 980431 comes back as
    "‎Avatar Aang, le dernier maître de l'air", whose leading U+200E defeats any exact
    comparison the caller might make against a release name or another title. Categories Cf
    (format) and Cc (control) are dropped; everything else, accents included, is untouched.
    """
    return "".join(c for c in (title or "") if unicodedata.category(c) not in ("Cf", "Cc")).strip()


def _lookup_ratings(movie: dict) -> dict[str, tuple[float, int]]:
    """Pull (value, votes) for imdb and tmdb out of Radarr's nested `ratings` object.

    A source missing, null, or with zero votes is omitted rather than shown as 0 — an
    unrated film must not read as a badly rated one.
    """
    raw = movie.get("ratings") or {}
    out: dict[str, tuple[float, int]] = {}
    for source in ("imdb", "tmdb"):
        entry = raw.get(source) or {}
        value, votes = entry.get("value"), entry.get("votes")
        if value:
            out[source] = (float(value), int(votes or 0))
    return out


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
        """Identify a film via Radarr's TMDB lookup. Returns the tmdbId radarr_add_movie needs.

        `term` accepts a title, but also an exact id: term="tmdb:82170" (or "imdb:tt0140381")
        resolves to that one film with no fuzzy title matching at all. Prefer it whenever you
        already hold an id — a title search on a French title is guesswork, an id is not.

        Why the original title matters: Radarr localises `title` to its configured language,
        so what comes back is a distribution title ("Les Aventuriers De L Arche Perdue") while
        indexer releases, external databases and the caller's own knowledge are usually keyed
        on the original ("Raiders of the Lost Ark"). Both are returned — `orig:` is shown only
        when it actually differs from the localised title. Titles are stripped of Unicode
        format/control characters first: TMDB ships some with invisible bidi marks that break
        exact comparison.

        Per result: tmdbId, localised title, year, original title when different, then studio,
        genres, runtime and imdb/tmdb ratings with their vote counts, then the overview
        (truncated). Fields the lookup does not supply are omitted rather than shown empty.
        Note that Radarr's lookup carries NO credits — there is no director or cast field, so
        do not report one from memory as if it came from here.
        """
        try:
            async with _client() as c:
                data = await c.lookup_movie(term)
            results = [
                MovieLookupResult(
                    title=_clean_title(m.get("title", "")),
                    year=m.get("year", 0),
                    tmdb_id=m.get("tmdbId", 0),
                    overview=(m.get("overview") or "")[:200],
                    original_title=_clean_title(m.get("originalTitle", "")),
                    studio=m.get("studio") or "",
                    genres=m.get("genres") or [],
                    runtime=m.get("runtime") or 0,
                    ratings=_lookup_ratings(m),
                )
                for m in data[:10]
            ]
            if not results:
                return f"No results for '{term}'."
            lines = [f"Results for '{term}':"]
            for r in results:
                head = f"  [{r.tmdb_id}] {r.title} ({r.year})"
                # Only worth a mention when it is not the same string as the localised title.
                if r.original_title and r.original_title != r.title:
                    head += f"  orig: {r.original_title}"
                lines.append(head)
                facts = []
                if r.studio:
                    facts.append(r.studio)
                if r.genres:
                    facts.append(", ".join(r.genres))
                if r.runtime:
                    facts.append(f"{r.runtime} min")
                # Rounded to one decimal: imdb reports 8.4 where tmdb reports 7.926, and the
                # two are only comparable at the same precision.
                facts.extend(
                    f"{source} {value:.1f} ({votes} vote{'' if votes == 1 else 's'})"
                    for source, (value, votes) in r.ratings.items()
                )
                if facts:
                    lines.append(f"     {'  ·  '.join(facts)}")
                if r.overview:
                    lines.append(f"     {r.overview}")
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
        """Show current download queue in Radarr, with diagnostics for stuck items.

        Surfaces trackedDownloadStatus/State and statusMessages/errorMessage (the "why"),
        and groups items sharing a downloadId.
        """
        try:
            async with _client() as c:
                data = await c.get_queue()
        except ArrClientError as e:
            return f"Error: {e}"
        return format_queue(data.get("records") or [])

    @mcp.tool()
    async def radarr_disk_space() -> str:
        """Show free disk space per volume known to Radarr, fullest volume first."""
        try:
            async with _client() as c:
                data = await c.disk_space()
        except ArrClientError as e:
            return f"Error: {e}"
        if not data:
            return "No disk space information reported by Radarr."
        volumes = [
            DiskSpaceSummary(
                label=d.get("label", "") or d.get("path", ""),
                path=d.get("path", ""),
                free=d.get("freeSpace", 0),
                total=d.get("totalSpace", 0),
            )
            for d in data
        ]
        lines = [f"Disk space ({len(volumes)} volume(s)):"]
        for v in sorted(volumes, key=lambda x: x.pct_free):
            label = v.label or v.path
            lines.append(
                f"  {label}  {format_size(v.free)} free / {format_size(v.total)}  "
                f"({v.pct_free:.0f}% free)"
            )
        return "\n".join(lines)

    @mcp.tool()
    async def radarr_health() -> str:
        """Show Radarr instance health checks (notices, warnings, errors)."""
        try:
            async with _client() as c:
                data = await c.health()
        except ArrClientError as e:
            return f"Error: {e}"
        if not data:
            return "No health issues reported by Radarr."
        issues = [
            HealthIssue(
                type=h.get("type", "unknown"),
                source=h.get("source", ""),
                message=h.get("message", ""),
            )
            for h in data
        ]
        lines = [f"Health issues ({len(issues)}):"]
        for i in issues:
            lines.append(f"  [{i.type}] {i.source}: {i.message}")
        return "\n".join(lines)

    @mcp.tool()
    async def radarr_history(limit: int = 20, event_type: str | None = None) -> str:
        """Show recent Radarr history events (grab/import/deletion...).

        Surfaces the downloadId (the torrent hash) when present, which later links a
        release to its torrent in the download client. Optionally filter by event_type,
        accepting a readable alias (grabbed, imported, failed, deleted, renamed, ignored)
        or an exact canonical eventType (e.g. downloadFolderImported). Filtering is done
        client-side, so a filtered result may include a note when the window is not filled.
        """
        try:
            async with _client() as c:
                result = await c.history_events(limit=limit, event_type=event_type)
        except ValueError as e:
            return f"Error: {e}"
        except ArrClientError as e:
            return f"Error: {e}"
        records = result["records"]
        if not records:
            suffix = f" for event_type='{event_type}'" if event_type else ""
            return f"No history events{suffix}."
        events = [
            HistoryRecordSummary(
                event_type=r.get("eventType", ""),
                source_title=r.get("sourceTitle", ""),
                date=r.get("date", ""),
                download_id=r.get("downloadId"),
            )
            for r in records
        ]
        header = f"Recent history ({len(events)} event(s))"
        if event_type:
            header += f" — event_type={event_type}"
        lines = [header + ":"]
        for e in events:
            dl = f"downloadId={e.download_id}" if e.download_id else "no downloadId"
            lines.append(f"  {e.date}  {e.event_type}  {e.source_title}  [{dl}]")
        if result["note"]:
            lines.append(result["note"])
        return "\n".join(lines)

    @mcp.tool()
    async def radarr_delete_queue_item(
        queue_id: int | None = None,
        download_id: str | None = None,
        remove_from_client: bool = True,
        blocklist: bool = False,
        confirm: bool = False,
    ) -> str:
        """Remove item(s) from the Radarr download queue (stuck/failed download).

        Provide EXACTLY ONE of:
        - queue_id: a single queue item;
        - download_id: ALL items sharing that downloadId, removed in one gesture.
        remove_from_client also deletes the download from the torrent/usenet client;
        blocklist prevents the same release from being grabbed again.
        Set confirm=True to actually remove; omit or set False for a dry-run preview.
        """
        async with _client() as c:
            return await run_delete_queue(
                c,
                "Radarr",
                queue_id=queue_id,
                download_id=download_id,
                remove_from_client=remove_from_client,
                blocklist=blocklist,
                confirm=confirm,
            )

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
            title = result["title"]
            year = result.get("year", "")
            mid = result["id"]
            return f"Added movie '{title}' ({year}) id={mid} to Radarr."
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

    @mcp.tool()
    async def radarr_upcoming(days: int = 7) -> str:
        """Show movies releasing in the next N days (default 7) via Radarr calendar.

        Uses the most relevant release date available (digital > physical > cinema).
        """
        start = date.today().isoformat()
        end = (date.today() + timedelta(days=days)).isoformat()
        try:
            async with _client() as c:
                data = await c.get_calendar(start=start, end=end)
        except ArrClientError as e:
            return f"Error: {e}"

        if not data:
            return f"No movies releasing in the next {days} days."
        movies = []
        for m in data:
            release_date, release_type = _relevant_release(m)
            movies.append(
                CalendarMovie(
                    title=m.get("title", ""),
                    year=m.get("year", 0),
                    release_date=release_date,
                    release_type=release_type,
                    monitored=m.get("monitored", False),
                    has_file=m.get("hasFile", False),
                )
            )
        lines = [f"Upcoming movies ({days} days):"]
        for mv in sorted(movies, key=lambda x: x.release_date):
            flag = "✓" if mv.has_file else " "
            lines.append(
                f"  [{flag}] {mv.release_date or '????-??-??'} ({mv.release_type})  "
                f"{mv.title} ({mv.year})  mon={'yes' if mv.monitored else 'no'}"
            )
        return "\n".join(lines)

    @mcp.tool()
    async def radarr_set_movie_monitoring(movie_id: int, monitored: bool) -> str:
        """Enable or disable monitoring for a single movie.

        Reversible action with no immediate grab, so no confirm is required.
        Returns the movie state after the change.
        """
        try:
            async with _client() as c:
                movie = await c.get_movie(movie_id)
                movie["monitored"] = monitored
                await c.update_movie(movie_id, movie)
        except ArrClientError as e:
            if "404" in str(e):
                return f"Error: movie not found (id={movie_id})."
            return f"Error: {e}"

        state = "monitored" if monitored else "unmonitored"
        return f"'{movie.get('title', movie_id)}' (id={movie_id}) is now {state}."

    @mcp.tool()
    async def radarr_search_movie(movie_id: int, confirm: bool = False) -> str:
        """Trigger a search/download for an already-added movie.

        Set confirm=True to actually launch the search; omit or set False for a
        dry-run preview.
        """
        try:
            async with _client() as c:
                movie = await c.get_movie(movie_id)
                title = movie.get("title", movie_id)
                monitored = movie.get("monitored", False)
                mon_state = "monitored" if monitored else "not monitored"
                if not confirm:
                    return (
                        f"DRY-RUN: Would search '{title}' ({mon_state}). "
                        "Set confirm=True to proceed."
                    )
                await c.movie_search(movie_id)
        except ArrClientError as e:
            if "404" in str(e):
                return f"Error: movie not found (id={movie_id})."
            return f"Error: {e}"

        result = f"Search launched for '{title}'."
        if not monitored:
            result += " Note: movie is not monitored."
        return result

    @mcp.tool()
    async def radarr_delete_movie_file(movie_id: int, confirm: bool = False) -> str:
        """Delete the movie's file but KEEP the movie tracked in Radarr (destructive).

        Unlike radarr_delete_movie, this only removes the downloaded file, so the movie
        stays monitored for re-download/upgrade. Set confirm=True to actually delete;
        omit or set False for a dry-run preview.
        """
        try:
            async with _client() as c:
                movie = await c.get_movie(movie_id)
                title = movie.get("title", movie_id)
                movie_file = movie.get("movieFile")
                if not movie.get("hasFile") or not movie_file:
                    return f"No file found for '{title}' (id={movie_id}); nothing to delete."

                file = MovieFileSummary(
                    id=movie_file["id"],
                    relative_path=movie_file.get("relativePath", ""),
                    size=movie_file.get("size", 0),
                )
                if not confirm:
                    return (
                        f"DRY-RUN: Would delete file for '{title}' — "
                        f"'{file.relative_path}' ({format_size(file.size)}).\n"
                        f"{hardlink_note('Radarr')}\n"
                        "Set confirm=True to proceed."
                    )
                await c.delete_movie_file(file.id)
        except ArrClientError as e:
            if "404" in str(e):
                return f"Error: movie not found (id={movie_id})."
            return f"Error: {e}"

        return (
            f"Deleted file for '{title}' — '{file.relative_path}', "
            f"{format_size(file.size)} freed in Radarr. Movie is still tracked."
        )
