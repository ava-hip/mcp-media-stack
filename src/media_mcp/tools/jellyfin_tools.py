from typing import Any

from mcp.server.fastmcp import FastMCP

from media_mcp.clients.jellyfin import (
    PLAYBACK_HISTORY_MAX_IDS,
    JellyfinClient,
    JellyfinClientError,
    JellyfinPluginMissingError,
)
from media_mcp.config import settings
from media_mcp.jellyfin_resolve import (
    METHOD_VIA_RADARR,
    MatchedMovie,
    Resolution,
    provider_id,
    short_id,
)


def _client() -> JellyfinClient:
    # Radarr config is passed through so resolution can fall back to Radarr's localized
    # titles (level 5); it is optional and skipped when Radarr is not configured.
    return JellyfinClient(
        settings.jellyfin_url,
        settings.jellyfin_api_key,
        radarr_url=str(settings.radarr_url),
        radarr_api_key=settings.radarr_api_key,
    )


def _movie_row(item: dict) -> str:
    """One aligned table row: short id, title, year, tmdbId."""
    tmdb = provider_id(item, "Tmdb") or "-"
    year = item.get("ProductionYear") or "----"
    name = str(item.get("Name", "?"))
    return f"{short_id(str(item.get('Id', ''))):<8}  {name[:45]:<45}  {year!s:>4}  {tmdb}"


def _movie_label(item: dict) -> str:
    """Inline label used in resolution previews: 'Title (Year)  tmdb=..  shortid'."""
    tmdb = provider_id(item, "Tmdb") or "-"
    year = item.get("ProductionYear") or "????"
    return f"{item.get('Name', '?')} ({year})  tmdb={tmdb}  {short_id(str(item.get('Id', '')))}"


def _matched_label(mm: MatchedMovie) -> str:
    """A matched movie plus HOW it was resolved. via-radarr is the most fallible match, so
    it spells out the Radarr title and the Jellyfin title actually retained.
    """
    base = _movie_label(mm.item)
    if mm.method == METHOD_VIA_RADARR:
        jf_name = mm.item.get("Name", "?")
        return f"{base}  [via radarr: '{mm.radarr_title}' -> '{jf_name}']"
    return f"{base}  [via {mm.method}]"


def _ambiguous_notfound_lines(resolution: Resolution) -> list[str]:
    """Render only the ambiguous / not_found buckets (matched is shown by the caller)."""
    lines: list[str] = []
    if resolution.ambiguous:
        lines.append(f"Ambiguous ({len(resolution.ambiguous)}) — NOT resolved, pick one:")
        for ref, candidates in resolution.ambiguous:
            cands = " | ".join(_movie_label(c) for c in candidates)
            lines.append(f"  ? '{ref}' -> {cands}")
    if resolution.not_found:
        lines.append(f"Not found ({len(resolution.not_found)}):")
        lines.extend(f"  x '{ref}'" for ref in resolution.not_found)
    return lines


def _resolution_lines(resolution: Resolution) -> list[str]:
    """Render matched + ambiguous + not_found so a dry-run shows EXACTLY what will happen."""
    lines: list[str] = []
    if resolution.matched:
        lines.append(f"Matched ({len(resolution.matched)}):")
        lines.extend(f"  + {_matched_label(m)}" for m in resolution.matched)
    lines.extend(_ambiguous_notfound_lines(resolution))
    return lines


def _unresolved_error(resolution: Resolution) -> str:
    parts = []
    if resolution.ambiguous:
        parts.append(f"{len(resolution.ambiguous)} ambiguous")
    if resolution.not_found:
        parts.append(f"{len(resolution.not_found)} not found")
    return (
        f"Error: refusing to proceed — {', '.join(parts)} reference(s) unresolved. Run "
        "the same call without confirm to see the details, then fix the references. No "
        "changes were made."
    )


# ── Playback Reporting rendering helpers ──────────────────────────────────────


def _as_int(value: Any) -> int:
    """Coerce a plugin-supplied counter to int, defaulting to 0 (never raises on junk)."""
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _format_watch_time(seconds: Any) -> str:
    """Compact "3d13h30m" / "19h02m" / "42m" from a seconds count, or "n/a" if unusable.

    Playback Reporting accumulates play durations in a 32-bit counter, and a single corrupted
    row makes a user's total wrap to a large NEGATIVE number (seen live: -2147441290, i.e.
    int32 min + noise, on a user whose play count was perfectly fine). The plugin's own
    `total_play_time` string is computed from that same value, so it is just as wrong
    ("< 1 minute"); both are rejected here rather than shown as a plausible-looking duration.
    """
    if not isinstance(seconds, int | float) or isinstance(seconds, bool) or seconds < 0:
        return "n/a"
    days, rest = divmod(int(seconds), 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return f"{days}d{hours:02d}h{minutes:02d}m"
    if hours:
        return f"{hours}h{minutes:02d}m"
    return f"{minutes}m"


# ── Session / history rendering helpers ───────────────────────────────────────

_TICKS_PER_SECOND = 10_000_000

# Per-play rows listed by jellyfin_item_history. The aggregate above the list always covers
# EVERY play returned; only this detail list is capped, and the header says so.
_MAX_PLAYS_LISTED = 20

# Widest PlaybackMethod the plugin actually records is "Transcode (v:direct a:direct)" (29).
_METHOD_WIDTH = 29
_METHOD_COL_HEADER = f"{'Method':<{_METHOD_WIDTH}}"


def _ticks_to_seconds(ticks: Any) -> int | None:
    """Jellyfin ticks (100 ns) -> whole seconds, or None when absent/unusable."""
    if not isinstance(ticks, int | float) or isinstance(ticks, bool) or ticks < 0:
        return None
    return int(ticks // _TICKS_PER_SECOND)


def _clock(seconds: int | None) -> str:
    """Seconds -> "H:MM:SS" (or "MM:SS" under an hour); "?" when unknown."""
    if seconds is None:
        return "?"
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def _trunc(text: Any, width: int) -> str:
    """Cut to `width`, marking the cut with an ellipsis so a shortened value is obvious."""
    text = str(text)
    return text if len(text) <= width else text[: width - 1] + "…"


def _sql_int(value: Any) -> int:
    """Playback Reporting returns every column as a STRING, counts included."""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def _sql_date(value: Any) -> str:
    """Trim "2026-07-31 20:13:02.7902664" to the minute; pass anything else through."""
    text = str(value or "").strip()
    return text[:16] if len(text) >= 16 else (text or "?")


def _item_label(item: dict) -> str:
    """Readable title for a NowPlayingItem: episodes get Series + SxxEyy, movies get a year."""
    name = str(item.get("Name") or "?")
    if item.get("Type") == "Episode":
        series = item.get("SeriesName")
        season = item.get("ParentIndexNumber")
        episode = item.get("IndexNumber")
        code = f"S{season:02d}E{episode:02d}" if season is not None and episode is not None else ""
        parts = [p for p in (series, code, name) if p]
        return " — ".join(str(p) for p in parts)
    year = item.get("ProductionYear")
    return f"{name} ({year})" if year else name


def _transcode_detail(session: dict) -> str:
    """One-line direct-play/transcode summary, using only the fields actually present.

    PlayState.PlayMethod is DirectPlay / DirectStream / Transcode. TranscodingInfo only
    exists while transcoding, so every field here is read defensively.
    """
    play_state = session.get("PlayState") or {}
    method = str(play_state.get("PlayMethod") or "").strip() or "unknown"
    info = session.get("TranscodingInfo") or {}
    if not info:
        return method
    bits: list[str] = []
    video, audio = info.get("VideoCodec"), info.get("AudioCodec")
    if video:
        direct = " (direct)" if info.get("IsVideoDirect") else ""
        bits.append(f"video={video}{direct}")
    if audio:
        direct = " (direct)" if info.get("IsAudioDirect") else ""
        bits.append(f"audio={audio}{direct}")
    reasons = info.get("TranscodeReasons") or []
    if isinstance(reasons, str):
        reasons = [reasons]
    if reasons:
        bits.append(f"reason={','.join(str(r) for r in reasons)}")
    return f"{method} ({'; '.join(bits)})" if bits else method


def register_jellyfin_tools(mcp: FastMCP) -> None:
    @mcp.tool()
    async def jellyfin_system_status() -> str:
        """Return Jellyfin server name + version (validates the API key)."""
        try:
            async with _client() as c:
                info = await c.system_info()
        except JellyfinClientError as e:
            return f"Error: {e}"
        return f"Jellyfin {info.get('Version', 'unknown')} — {info.get('ServerName', 'Jellyfin')}"

    @mcp.tool()
    async def jellyfin_list_movies() -> str:
        """List movies in the Jellyfin library (short id, title, year, tmdbId)."""
        try:
            async with _client() as c:
                movies = await c.movie_catalog()
        except JellyfinClientError as e:
            return f"Error: {e}"
        if not movies:
            return "No movies found in Jellyfin."
        movies_sorted = sorted(movies, key=lambda m: str(m.get("Name", "")).lower())
        lines = [
            f"Jellyfin movies ({len(movies)}):",
            f"{'Id':<8}  {'Title':<45}  {'Year':>4}  tmdbId",
            "-" * 78,
        ]
        lines.extend(_movie_row(m) for m in movies_sorted)
        return "\n".join(lines)

    @mcp.tool()
    async def jellyfin_list_collections() -> str:
        """List Jellyfin collections/BoxSets (short id, name, item count, description)."""
        try:
            async with _client() as c:
                collections = await c.collection_catalog()
        except JellyfinClientError as e:
            return f"Error: {e}"
        if not collections:
            return "No collections found in Jellyfin."
        collections_sorted = sorted(collections, key=lambda c: str(c.get("Name", "")).lower())
        lines = [
            f"Jellyfin collections ({len(collections)}):",
            f"{'Id':<8}  {'Name':<30}  {'Items':>5}  Description",
            "-" * 90,
        ]
        for col in collections_sorted:
            count = col.get("ChildCount")
            if count is None:
                count = col.get("RecursiveItemCount")
            count_str = str(count) if count is not None else "?"
            overview = (col.get("Overview") or "").replace("\n", " ")
            desc = (overview[:47] + "...") if len(overview) > 50 else overview
            lines.append(
                f"{short_id(str(col.get('Id', ''))):<8}  {str(col.get('Name', '?'))[:30]:<30}  "
                f"{count_str:>5}  {desc}"
            )
        return "\n".join(lines)

    @mcp.tool()
    async def jellyfin_collection_items(collection_ref: str) -> str:
        """List the movies inside a collection (by collection name or id).

        collection_ref accepts a collection name or a Jellyfin id (or its unique prefix).
        """
        try:
            async with _client() as c:
                boxset = await c.resolve_collection(collection_ref)
                items = await c.get_collection_items(str(boxset["Id"]))
        except JellyfinClientError as e:
            return f"Error: {e}"
        name = boxset.get("Name", collection_ref)
        boxset_short = short_id(str(boxset["Id"]))
        if not items:
            return f"Collection '{name}' ({boxset_short}) is empty."
        items_sorted = sorted(items, key=lambda m: str(m.get("Name", "")).lower())
        lines = [
            f"Collection '{name}' ({boxset_short}) — {len(items)} item(s):",
            f"{'Id':<8}  {'Title':<45}  {'Year':>4}  tmdbId",
            "-" * 78,
        ]
        lines.extend(_movie_row(m) for m in items_sorted)
        return "\n".join(lines)

    @mcp.tool()
    async def jellyfin_active_sessions() -> str:
        """Who is watching what RIGHT NOW: user, device, item, state, progress, play method.

        One block per session that is actually playing something. Connected-but-idle clients
        are counted, not listed. Reports the live state only — it draws no conclusion from it.
        """
        try:
            async with _client() as c:
                sessions = await c.get_sessions()
        except JellyfinClientError as e:
            return f"Error: {e}"

        playing = [s for s in sessions if s.get("NowPlayingItem")]
        if not playing:
            idle = len(sessions)
            suffix = f" ({idle} client(s) connected but idle)" if idle else ""
            return f"No active playback sessions{suffix}."

        lines = [f"Jellyfin active playback — {len(playing)} session(s):"]
        for session in playing:
            item = session.get("NowPlayingItem") or {}
            play_state = session.get("PlayState") or {}
            user = str(session.get("UserName") or session.get("UserId") or "?")
            client_name = str(session.get("Client") or "?")
            device = str(session.get("DeviceName") or "?")
            state = "paused" if play_state.get("IsPaused") else "playing"
            position = _ticks_to_seconds(play_state.get("PositionTicks"))
            runtime = _ticks_to_seconds(item.get("RunTimeTicks"))
            progress = f"{_clock(position)} / {_clock(runtime)}"
            if position is not None and runtime:
                progress += f" ({position * 100 // runtime}%)"
            lines.extend(
                [
                    "",
                    f"{user} — {client_name} on {device}",
                    f"  Item      {_item_label(item)} [{item.get('Type') or '?'}]",
                    f"  State     {state}",
                    f"  Progress  {progress}",
                    f"  Method    {_transcode_detail(session)}",
                ]
            )
        idle = len(sessions) - len(playing)
        if idle > 0:
            lines.append("")
            lines.append(f"({idle} further client(s) connected but not playing anything.)")
        return "\n".join(lines)

    @mcp.tool()
    async def jellyfin_scan_library(library: str | None = None, confirm: bool = False) -> str:
        """Trigger a Jellyfin library scan (all libraries, or just one).

        library=None scans EVERY library; pass a library name or id (or its 8-char prefix) to
        scan just that one. Existing metadata and images are kept — this looks for new, moved
        and removed files. confirm=False previews what would be scanned without launching
        anything; confirm=True triggers it (Jellyfin then runs the scan asynchronously).
        """
        try:
            async with _client() as c:
                if library is None:
                    libraries = await c.get_libraries()
                    if not confirm:
                        names = ", ".join(str(lib.get("Name", "?")) for lib in libraries) or "none"
                        return (
                            f"DRY-RUN: would trigger a GLOBAL scan of all {len(libraries)} "
                            f"librar(ies): {names}. Set confirm=True to proceed."
                        )
                    await c.refresh_all_libraries()
                    return (
                        f"Global library scan triggered for all {len(libraries)} librar(ies). "
                        "Jellyfin runs it asynchronously — check Dashboard > Scheduled Tasks "
                        "for progress."
                    )

                target = await c.resolve_library(library)
                name = str(target.get("Name", library))
                target_id = str(target.get("ItemId", ""))
                kind = target.get("CollectionType") or "mixed"
                paths = ", ".join(target.get("Locations") or []) or "?"
                if not confirm:
                    return (
                        f"DRY-RUN: would scan ONLY the library '{name}' "
                        f"({short_id(target_id)}, {kind}) at {paths}. Existing metadata and "
                        "images are kept. Set confirm=True to proceed."
                    )
                await c.refresh_item(target_id)
            return (
                f"Scan triggered for library '{name}' ({short_id(target_id)}). Jellyfin runs "
                "it asynchronously — check Dashboard > Scheduled Tasks for progress."
            )
        except JellyfinClientError as e:
            return f"Error: {e}"

    @mcp.tool()
    async def jellyfin_item_history(item: str, days: int = 90) -> str:
        """Playback history of ONE item: who watched it, when, how many times, how long.

        `item` accepts a Jellyfin id (or 8-char prefix), a tmdbId, or a movie/series title
        (accents, case and leading articles are normalized). An ambiguous title lists the
        candidates and does nothing. Pointing at a series covers all of its episodes.
        Requires the 'Playback Reporting' plugin, which is what records this history.
        """
        if days < 1:
            return "Error: days must be >= 1."
        try:
            async with _client() as c:
                target = await c.resolve_media_item(item)
                item_id = str(target.get("Id", ""))
                item_type = str(target.get("Type") or "")
                ids = await c.library_descendant_ids(item_id, item_type)
                dropped = max(0, len(ids) - PLAYBACK_HISTORY_MAX_IDS)
                plays = await c.item_play_history(ids[:PLAYBACK_HISTORY_MAX_IDS], days=days)
        except JellyfinPluginMissingError as e:
            return f"Playback Reporting unavailable: {e}"
        except JellyfinClientError as e:
            return f"Error: {e}"

        label = f"'{target.get('Name', item)}' ({item_type or '?'}) [{short_id(item_id)}]"
        if not plays:
            return (
                f"No playback recorded for {label} in the last {days} day(s). "
                "(Playback Reporting only knows what it has recorded since it was installed.)"
            )

        per_user: dict[str, dict[str, Any]] = {}
        for play in plays:
            user = str(play.get("UserName") or play.get("UserId") or "?")
            bucket = per_user.setdefault(user, {"plays": 0, "seconds": 0, "last": ""})
            bucket["plays"] += 1
            bucket["seconds"] += _sql_int(play.get("PlayDuration"))
            bucket["last"] = max(bucket["last"], _sql_date(play.get("DateCreated")))

        lines = [f"Playback history — {label}, last {days} day(s): {len(plays)} play(s)."]
        if len(ids) > 1:
            lines.append(f"Covers {len(ids)} item id(s) (container expanded to its children).")
        if dropped:
            lines.append(
                f"NOTE: {dropped} further child id(s) were NOT queried "
                f"(cap of {PLAYBACK_HISTORY_MAX_IDS} ids per query)."
            )
        lines.extend(
            [
                "",
                f"{'User':<14}  {'Plays':>5}  {'Watch time':>10}  Last play",
                "-" * 60,
            ]
        )
        for user, agg in sorted(per_user.items(), key=lambda kv: (-kv[1]["plays"], kv[0].lower())):
            lines.append(
                f"{_trunc(user, 14):<14}  {agg['plays']:>5}  "
                f"{_format_watch_time(agg['seconds']):>10}  {agg['last']}"
            )

        shown = plays[:_MAX_PLAYS_LISTED]
        lines.extend(
            [
                "",
                f"Plays ({len(plays)}, showing {len(shown)} most recent):",
                f"{'Date':<16}  {'User':<12}  {_METHOD_COL_HEADER}  "
                f"{'Client / Device':<26}  Item",
                "-" * 134,
            ]
        )
        for play in shown:
            user = str(play.get("UserName") or play.get("UserId") or "?")
            source = f"{play.get('ClientName') or '?'} / {play.get('DeviceName') or '?'}"
            # PlaybackMethod is not just DirectPlay/DirectStream/Transcode: the plugin also
            # records "Transcode (v:direct a:aac)" forms, up to 29 chars — shown in full.
            method = str(play.get("PlaybackMethod") or "?")
            lines.append(
                f"{_sql_date(play.get('DateCreated')):<16}  {_trunc(user, 12):<12}  "
                f"{_trunc(method, _METHOD_WIDTH):<{_METHOD_WIDTH}}  "
                f"{_trunc(source, 26):<26}  {_trunc(play.get('ItemName') or '?', 42)}"
            )
        return "\n".join(lines)

    @mcp.tool()
    async def jellyfin_playback_stats(days: int = 7) -> str:
        """Watch statistics per user over the last `days` (needs the Playback Reporting plugin).

        For every user active in the window: number of plays, total watch time, and the most
        recent item they watched (with the client used). The underlying endpoint aggregates
        PER USER — this is a summary, not a per-item history. Requires the 'Playback Reporting'
        plugin on the Jellyfin server; if it is missing the tool says so instead of failing.
        """
        if days < 1:
            return (
                "Error: days must be >= 1 (Playback Reporting returns an empty window for 0)."
            )
        try:
            async with _client() as c:
                rows = await c.user_activity(days=days)
        except JellyfinPluginMissingError as e:
            return f"Playback Reporting unavailable: {e}"
        except JellyfinClientError as e:
            return f"Error: {e}"
        if not rows:
            return f"No playback activity recorded in Jellyfin over the last {days} day(s)."

        rows_sorted = sorted(
            rows,
            key=lambda r: (-_as_int(r.get("total_count")), str(r.get("user_name", "")).lower()),
        )
        total_plays = sum(_as_int(r.get("total_count")) for r in rows_sorted)
        lines = [
            f"Jellyfin playback activity — last {days} day(s), {len(rows_sorted)} active "
            f"user(s), {total_plays} play(s):",
            f"{'User':<14}  {'Plays':>5}  {'Watch time':>10}  {'Last seen':<18}  Last item",
            "-" * 100,
        ]
        no_time: list[str] = []
        for row in rows_sorted:
            name = str(row.get("user_name") or row.get("user_id") or "?")
            watch = _format_watch_time(row.get("total_time"))
            if watch == "n/a":
                no_time.append(name)
            # last_seen comes with padding/newlines from the plugin ("1 hour 18 minutes ").
            last_seen = " ".join(str(row.get("last_seen") or "").split()) or "?"
            item = str(row.get("item_name") or "?")
            client_name = str(row.get("client_name") or "")
            suffix = f"  [{client_name}]" if client_name else ""
            lines.append(
                f"{_trunc(name, 14):<14}  {_as_int(row.get('total_count')):>5}  {watch:>10}  "
                f"{_trunc(last_seen, 18):<18}  {_trunc(item, 45)}{suffix}"
            )
        lines.append(
            "Sorted by play count. 'Last item' is that user's most recent play, not a "
            "per-item breakdown."
        )
        if no_time:
            lines.append(
                f"Watch time unavailable for {len(no_time)} user(s) ({', '.join(no_time)}): "
                "Playback Reporting returned a negative total (known plugin counter "
                "overflow). Their play counts are unaffected."
            )
        return "\n".join(lines)

    @mcp.tool()
    async def jellyfin_create_collection(
        name: str,
        movies: list[str],
        overview: str | None = None,
        confirm: bool = False,
    ) -> str:
        """Create a curated collection (BoxSet) from a list of movie references.

        `movies` accepts a mixed list of tmdbIds, Jellyfin ids (or unique prefixes) and
        approximate titles; each is resolved and, in a dry-run, shown as matched / ambiguous
        / not found. Ambiguous or unknown references are NEVER guessed. With confirm=False
        (default) nothing is written. With confirm=True the collection is created only if
        EVERY reference resolved, so a partial collection is never created silently. Set
        `overview` to also write (and lock) a description on the new collection.
        """
        try:
            async with _client() as c:
                resolution = await c.resolve_movies_refs(movies)
                if not confirm:
                    header = [f"DRY-RUN: would create collection '{name}'."]
                    if overview:
                        header.append(f"Overview would be set and locked ({len(overview)} chars).")
                    body = _resolution_lines(resolution)
                    if not resolution.matched:
                        body.append("No movies resolved — nothing would be created.")
                    elif not resolution.fully_resolved:
                        body.append("On confirm this is refused (no partial collection).")
                    return "\n".join([*header, *body, "Set confirm=True to proceed."])
                if not resolution.matched:
                    return (
                        "Error: no movies resolved from the given references; nothing to "
                        "create. No changes were made."
                    )
                if not resolution.fully_resolved:
                    return _unresolved_error(resolution)
                ids = [str(m.item["Id"]) for m in resolution.matched]
                created = await c.create_collection(name, ids)
                boxset_id = str(created.get("Id", ""))
                overview_note = ""
                if overview and boxset_id:
                    await c.set_item_overview(boxset_id, overview, lock=True)
                    overview_note = " Overview set and locked."
            return (
                f"Created collection '{name}' ({short_id(boxset_id)}) with "
                f"{len(ids)} movie(s).{overview_note}"
            )
        except JellyfinClientError as e:
            return f"Error: {e}"

    @mcp.tool()
    async def jellyfin_add_to_collection(
        collection_ref: str,
        movies: list[str],
        confirm: bool = False,
    ) -> str:
        """Add movies to an existing collection (by collection name or id).

        Same resolution ergonomics as jellyfin_create_collection. Movies already in the
        collection are reported and skipped. With confirm=False nothing is written; with
        confirm=True the add proceeds only if every reference resolved.
        """
        try:
            async with _client() as c:
                boxset = await c.resolve_collection(collection_ref)
                boxset_id = str(boxset["Id"])
                name = boxset.get("Name", collection_ref)
                resolution = await c.resolve_movies_refs(movies)
                current_ids = {str(i.get("Id")) for i in await c.get_collection_items(boxset_id)}
                to_add = [m for m in resolution.matched if str(m.item.get("Id")) not in current_ids]
                already = [m for m in resolution.matched if str(m.item.get("Id")) in current_ids]
                if not confirm:
                    lines = [f"DRY-RUN: would add to collection '{name}' ({short_id(boxset_id)})."]
                    if to_add:
                        lines.append(f"To add ({len(to_add)}):")
                        lines.extend(f"  + {_matched_label(m)}" for m in to_add)
                    if already:
                        lines.append(f"Already in collection ({len(already)}, skipped):")
                        lines.extend(f"  = {_matched_label(m)}" for m in already)
                    lines.extend(_ambiguous_notfound_lines(resolution))
                    if not to_add:
                        lines.append("Nothing new to add.")
                    lines.append("Set confirm=True to proceed.")
                    return "\n".join(lines)
                if not resolution.fully_resolved:
                    return _unresolved_error(resolution)
                if not to_add:
                    return f"Nothing to add: all resolved movies are already in '{name}'."
                await c.add_to_collection(boxset_id, [str(m.item["Id"]) for m in to_add])
            return (
                f"Added {len(to_add)} movie(s) to collection '{name}' ({short_id(boxset_id)})."
            )
        except JellyfinClientError as e:
            return f"Error: {e}"

    @mcp.tool()
    async def jellyfin_remove_from_collection(
        collection_ref: str,
        movies: list[str],
        confirm: bool = False,
    ) -> str:
        """Remove movies from a collection (by collection name or id).

        The movies stay in the library; only their membership in the collection is removed.
        Same resolution ergonomics; references resolving to movies not currently in the
        collection are reported and skipped. confirm=False previews; confirm=True proceeds
        only if every reference resolved.
        """
        try:
            async with _client() as c:
                boxset = await c.resolve_collection(collection_ref)
                boxset_id = str(boxset["Id"])
                name = boxset.get("Name", collection_ref)
                resolution = await c.resolve_movies_refs(movies)
                current_ids = {str(i.get("Id")) for i in await c.get_collection_items(boxset_id)}
                to_remove = [m for m in resolution.matched if str(m.item.get("Id")) in current_ids]
                absent = [m for m in resolution.matched if str(m.item.get("Id")) not in current_ids]
                if not confirm:
                    lines = [
                        f"DRY-RUN: would remove from collection '{name}' ({short_id(boxset_id)})."
                    ]
                    if to_remove:
                        lines.append(f"To remove ({len(to_remove)}):")
                        lines.extend(f"  - {_matched_label(m)}" for m in to_remove)
                    if absent:
                        lines.append(f"Not in collection ({len(absent)}, skipped):")
                        lines.extend(f"  = {_matched_label(m)}" for m in absent)
                    lines.extend(_ambiguous_notfound_lines(resolution))
                    if not to_remove:
                        lines.append("Nothing to remove.")
                    lines.append("Set confirm=True to proceed.")
                    return "\n".join(lines)
                if not resolution.fully_resolved:
                    return _unresolved_error(resolution)
                if not to_remove:
                    return f"Nothing to remove: none of the resolved movies are in '{name}'."
                await c.remove_from_collection(boxset_id, [str(m.item["Id"]) for m in to_remove])
            return (
                f"Removed {len(to_remove)} movie(s) from collection '{name}' "
                f"({short_id(boxset_id)})."
            )
        except JellyfinClientError as e:
            return f"Error: {e}"

    @mcp.tool()
    async def jellyfin_set_overview(
        item_ref: str,
        overview: str,
        lock: bool = True,
        confirm: bool = False,
    ) -> str:
        """Set an item's Overview/description (a collection or a movie, by name or id).

        By default lock=True adds "Overview" to the item's LockedFields so a later metadata
        refresh cannot overwrite the curated text; set lock=False to leave it unlocked.
        confirm=False previews; confirm=True writes.
        """
        try:
            async with _client() as c:
                item = await c.resolve_item(item_ref)
                item_id = str(item["Id"])
                display = item.get("Name", item_ref)
                if not confirm:
                    return (
                        f"DRY-RUN: would set overview on '{display}' ({short_id(item_id)}), "
                        f"{len(overview)} chars, lock={'yes' if lock else 'no'}. "
                        "Set confirm=True to proceed."
                    )
                await c.set_item_overview(item_id, overview, lock=lock)
            note = " (locked)" if lock else ""
            return f"Overview set on '{display}' ({short_id(item_id)}){note}."
        except JellyfinClientError as e:
            return f"Error: {e}"

    @mcp.tool()
    async def jellyfin_delete_collection(collection_ref: str, confirm: bool = False) -> str:
        """Delete a collection/BoxSet (by name or id). The movies themselves are kept.

        confirm=False previews; confirm=True deletes the collection container only.
        """
        try:
            async with _client() as c:
                boxset = await c.resolve_collection(collection_ref)
                boxset_id = str(boxset["Id"])
                name = boxset.get("Name", collection_ref)
                count = boxset.get("ChildCount")
                if count is None:
                    count = boxset.get("RecursiveItemCount")
                if not confirm:
                    count_note = f" ({count} item(s))" if count is not None else ""
                    return (
                        f"DRY-RUN: would delete collection '{name}' ({short_id(boxset_id)})"
                        f"{count_note}. The movies stay in the library; only the collection "
                        "container is removed. Set confirm=True to proceed."
                    )
                await c.delete_item(boxset_id)
            return (
                f"Deleted collection '{name}' ({short_id(boxset_id)}). "
                "The movies remain in the library."
            )
        except JellyfinClientError as e:
            return f"Error: {e}"

    # ── Next iteration (placeholders — not implemented yet) ─────────────────────
    # More Playback Reporting tools (most-watched, "not watched in N days"...) plug onto the
    # client's _playback_report() the same way user_activity() does — the 404 -> "plugin
    # missing" mapping and these rendering helpers are already shared. The routes are
    # confirmed live: /GetTvShowsReport (per-series count+time, and its `time` field is NOT
    # affected by the per-user overflow), /PlayActivity (per-day counts), /HourlyReport.
    #
    # jellyfin_set_collection_image(collection_ref, image, confirm=False): resolve the
    # collection, then upload a poster via the client's future set_primary_image(), which
    # will POST /Items/{id}/Images/Primary with the image base64-encoded in the body and
    # Content-Type set to its real mime type (image/jpeg, image/png...). Left out of this
    # iteration on purpose (poster upload is out of scope for now).
