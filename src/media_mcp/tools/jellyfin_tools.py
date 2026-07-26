from mcp.server.fastmcp import FastMCP

from media_mcp.clients.jellyfin import JellyfinClient, JellyfinClientError
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

    # ── Next iteration (placeholder — not implemented yet) ──────────────────────
    # jellyfin_set_collection_image(collection_ref, image, confirm=False): resolve the
    # collection, then upload a poster via the client's future set_primary_image(), which
    # will POST /Items/{id}/Images/Primary with the image base64-encoded in the body and
    # Content-Type set to its real mime type (image/jpeg, image/png...). Left out of this
    # iteration on purpose (poster upload is out of scope for now).
