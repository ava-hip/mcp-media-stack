"""Async client for a Jellyfin server.

Jellyfin is NOT a Servarr API, so this client deliberately does not extend ArrClient:
- endpoints live at the server ROOT (no /api/vN prefix): /System/Info, /Items, /Collections;
- auth is the header  Authorization: MediaBrowser Token="<api_key>"  (the legacy
  X-Emby-Token / ?api_key= variants are deprecated and disabled since Jellyfin 10.11);
- most item endpoints need a userId, resolved once from the first administrator account.

Its shape mirrors QuiClient (standalone, _ensure_configured guard, one central _request),
not ArrClient — Jellyfin simply does not fit the *arr mould.
"""

from typing import Any

import httpx

from media_mcp.clients.base import ArrClientError
from media_mcp.clients.radarr import RadarrClient
from media_mcp.jellyfin_resolve import (
    JellyfinResolutionError,
    Resolution,
    resolve_movies,
    resolve_single,
)


class JellyfinClientError(Exception):
    pass


# BaseItemDto collection fields that Jellyfin passes through .ToList() when handling
# POST /Items/{id}. A null in any of them makes the request 400 AND can corrupt the item
# until the next library rescan (documented Jellyfin bug), so each is coerced to an empty
# list before the DTO is sent back. This is the module's single most important safeguard.
_ARRAY_FIELDS = (
    "Tags",
    "Genres",
    "Studios",
    "People",
    "LockedFields",
    "GenreItems",
    "TagItems",
    "ProductionLocations",
    "RemoteTrailers",
)


def normalize_item_dto(item: dict) -> dict:
    """Return a shallow copy of a BaseItemDto with no null array/collection fields.

    Every field in :data:`_ARRAY_FIELDS` becomes ``[]`` when null/absent. ProviderIds is a
    MAP (not a list), so a null there becomes ``{}`` — never ``[]`` — which keeps the Radarr
    tmdb bridge (ProviderIds.Tmdb) intact.
    """
    dto = dict(item)
    for field in _ARRAY_FIELDS:
        if dto.get(field) is None:
            dto[field] = []
    if dto.get("ProviderIds") is None:
        dto["ProviderIds"] = {}
    return dto


# Process-wide cache of the resolved admin userId, keyed by server base URL. Jellyfin's item
# endpoints want a userId when called with an API key; it never changes for the life of the
# process, so it is resolved once (GET /Users -> first Policy.IsAdministrator) and reused.
_USER_ID_CACHE: dict[str, str] = {}


def clear_user_cache() -> None:
    """Reset the process-wide admin-userId cache (used by tests)."""
    _USER_ID_CACHE.clear()


class JellyfinClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout: float = 30.0,
        radarr_url: str = "",
        radarr_api_key: str = "",
    ) -> None:
        self._base = base_url.rstrip("/") if base_url else ""
        self._api_key = api_key
        self._timeout = timeout
        # Radarr config for the level-5 resolution fallback (Radarr knows the localized
        # titles the Jellyfin library may only index under an English Name). Empty = the
        # fallback is simply skipped. See _fetch_radarr_catalog / resolve_movies_refs.
        self._radarr_url = radarr_url
        self._radarr_api_key = radarr_api_key
        self._client = httpx.AsyncClient(
            headers={"Authorization": f'MediaBrowser Token="{api_key}"'} if api_key else {},
            timeout=timeout,
        )

    def _ensure_configured(self) -> None:
        if not self._base or not self._api_key:
            raise JellyfinClientError(
                "Jellyfin is not configured: set JELLYFIN_URL and JELLYFIN_API_KEY in the "
                "environment (create the key from Jellyfin Dashboard > API Keys)."
            )

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        self._ensure_configured()
        try:
            response = await self._client.request(method, f"{self._base}{path}", **kwargs)
            response.raise_for_status()
            return response
        except httpx.TimeoutException as e:
            raise JellyfinClientError(f"Request timed out: {method} {path}") from e
        except httpx.ConnectError as e:
            raise JellyfinClientError(f"Cannot connect to Jellyfin at {self._base}") from e
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                raise JellyfinClientError("Unauthorized (401): check JELLYFIN_API_KEY.") from e
            raise JellyfinClientError(
                f"HTTP {e.response.status_code} on {method} {path}: {e.response.text}"
            ) from e

    async def _get(self, path: str, **params: Any) -> Any:
        response = await self._request("GET", path, params=params or None)
        return response.json()

    # ── Auth / identity ────────────────────────────────────────────────────────

    async def system_info(self) -> dict[str, Any]:
        """GET /System/Info — non user-specific, the reliable auth sanity check."""
        return await self._get("/System/Info")

    async def get_users(self) -> list[dict[str, Any]]:
        return await self._get("/Users")

    async def resolve_user_id(self) -> str:
        """Return the first administrator's userId, cached process-wide by server URL."""
        self._ensure_configured()
        cached = _USER_ID_CACHE.get(self._base)
        if cached:
            return cached
        users = await self.get_users()
        admin = next((u for u in users if (u.get("Policy") or {}).get("IsAdministrator")), None)
        if admin is None:
            raise JellyfinClientError(
                "No administrator account found on the Jellyfin server; cannot resolve a "
                "userId for item queries."
            )
        user_id = str(admin["Id"])
        _USER_ID_CACHE[self._base] = user_id
        return user_id

    # ── Items ──────────────────────────────────────────────────────────────────

    async def get_items(
        self,
        *,
        user_id: str,
        include_item_types: str | None = None,
        parent_id: str | None = None,
        fields: str | None = None,
        recursive: bool = True,
        start_index: int = 0,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """GET /Items -> {"Items": [...], "TotalRecordCount": n}."""
        params: dict[str, Any] = {"userId": user_id, "startIndex": start_index}
        if recursive:
            params["recursive"] = "true"
        if include_item_types:
            params["includeItemTypes"] = include_item_types
        if parent_id:
            params["parentId"] = parent_id
        if fields:
            params["fields"] = fields
        if limit is not None:
            params["limit"] = limit
        return await self._get("/Items", **params)

    async def get_all_items(
        self,
        *,
        include_item_types: str | None = None,
        parent_id: str | None = None,
        fields: str | None = None,
        recursive: bool = True,
        page_size: int = 200,
    ) -> list[dict[str, Any]]:
        """Page through /Items (startIndex/limit) and return every item."""
        user_id = await self.resolve_user_id()
        collected: list[dict[str, Any]] = []
        start = 0
        while True:
            page = await self.get_items(
                user_id=user_id,
                include_item_types=include_item_types,
                parent_id=parent_id,
                fields=fields,
                recursive=recursive,
                start_index=start,
                limit=page_size,
            )
            batch = page.get("Items") or []
            collected.extend(batch)
            total = page.get("TotalRecordCount")
            start += len(batch)
            if not batch or len(batch) < page_size:
                break
            if total is not None and start >= total:
                break
        return collected

    async def movie_catalog(self) -> list[dict[str, Any]]:
        """All movies, with ProviderIds (tmdb bridge) and OriginalTitle (title matching)."""
        return await self.get_all_items(
            include_item_types="Movie", fields="ProviderIds,OriginalTitle"
        )

    async def collection_catalog(self) -> list[dict[str, Any]]:
        """All BoxSets (collections), with Overview and the child counts.

        ChildCount is NOT returned for BoxSets by default — it must be requested via
        `fields`. RecursiveItemCount is requested too as a fallback; if neither is present
        in the response the tool shows "?" rather than crashing.
        """
        return await self.get_all_items(
            include_item_types="BoxSet", fields="Overview,ChildCount,RecursiveItemCount"
        )

    async def get_collection_items(self, boxset_id: str) -> list[dict[str, Any]]:
        """Direct children of a collection (parentId query, non-recursive)."""
        return await self.get_all_items(
            parent_id=boxset_id, fields="ProviderIds,OriginalTitle", recursive=False
        )

    async def get_item(self, item_id: str) -> dict[str, Any]:
        """Full BaseItemDto for editing (GET /Users/{userId}/Items/{itemId})."""
        user_id = await self.resolve_user_id()
        return await self._get(f"/Users/{user_id}/Items/{item_id}")

    async def update_item(self, item_id: str, dto: dict[str, Any]) -> None:
        """POST the complete BaseItemDto back (there is no PATCH; see normalize_item_dto)."""
        await self._request("POST", f"/Items/{item_id}", json=dto)

    async def set_item_overview(
        self, item_id: str, overview: str, *, lock: bool = True
    ) -> dict[str, Any]:
        """GET-modify-POST an item's Overview, defensively normalizing null arrays first.

        When ``lock`` is True, "Overview" is added to LockedFields so a later metadata
        refresh cannot overwrite the curated description. This is a FIELD-level lock; the
        DTO's global LockData flag is left untouched so only Overview is protected.
        """
        item = await self.get_item(item_id)
        dto = normalize_item_dto(item)
        dto["Overview"] = overview
        if lock and "Overview" not in dto["LockedFields"]:
            dto["LockedFields"] = [*dto["LockedFields"], "Overview"]
        await self.update_item(item_id, dto)
        return dto

    # ── Collections ────────────────────────────────────────────────────────────

    async def create_collection(self, name: str, ids: list[str]) -> dict[str, Any]:
        """POST /Collections?name=&ids=csv -> {"Id": ...}. name/ids are QUERY params.

        A 500 "Sequence contains no elements" means the server has no Collections library
        yet; that is turned into an actionable message instead of a raw error.
        """
        params: dict[str, Any] = {"name": name}
        if ids:
            params["ids"] = ",".join(ids)
        try:
            response = await self._request("POST", "/Collections", params=params)
        except JellyfinClientError as e:
            message = str(e)
            if "Sequence contains no elements" in message or "HTTP 500" in message:
                raise JellyfinClientError(
                    "Could not create the collection: Jellyfin has no 'Collections' library "
                    "yet. Create one collection manually from the web UI first (on any movie: "
                    "the ... menu > Add to collection > New collection), then retry."
                ) from e
            raise
        return response.json() if response.content else {}

    async def add_to_collection(self, boxset_id: str, ids: list[str]) -> None:
        await self._request(
            "POST", f"/Collections/{boxset_id}/Items", params={"ids": ",".join(ids)}
        )

    async def remove_from_collection(self, boxset_id: str, ids: list[str]) -> None:
        await self._request(
            "DELETE", f"/Collections/{boxset_id}/Items", params={"ids": ",".join(ids)}
        )

    async def delete_item(self, item_id: str) -> None:
        """DELETE /Items/{id} — removes the collection itself (its movies are untouched)."""
        await self._request("DELETE", f"/Items/{item_id}")

    # ── Reference resolution (thin async wrappers over the pure resolver) ───────

    async def _fetch_radarr_catalog(self) -> list[dict[str, Any]] | None:
        """Fetch Radarr's movie library for the level-5 fallback, or None if unavailable.

        Reuses the internal RadarrClient (never an HTTP call to our own MCP tools). Returns
        None — so the fallback is silently skipped — when Radarr is not configured or is
        unreachable; it never raises.
        """
        if not self._radarr_url or not self._radarr_api_key:
            return None
        try:
            async with RadarrClient(self._radarr_url, self._radarr_api_key, self._timeout) as rc:
                return await rc.get_movies()
        except ArrClientError:
            return None

    async def resolve_movies_refs(self, refs: list[str]) -> Resolution:
        """Resolve the `movies` list via the cascade, one Jellyfin fetch + lazy Radarr fetch.

        The Radarr catalog is fetched at most once and only if a reference reaches level 5.
        """
        catalog = await self.movie_catalog()
        return await resolve_movies(catalog, refs, radarr_fetcher=self._fetch_radarr_catalog)

    async def resolve_collection(self, ref: str) -> dict[str, Any]:
        try:
            return resolve_single(await self.collection_catalog(), ref, kind="collection")
        except JellyfinResolutionError as e:
            raise JellyfinClientError(str(e)) from e

    async def resolve_item(self, ref: str) -> dict[str, Any]:
        catalog = await self.movie_catalog()
        catalog = catalog + await self.collection_catalog()
        try:
            return resolve_single(catalog, ref, kind="item")
        except JellyfinResolutionError as e:
            raise JellyfinClientError(str(e)) from e

    # ── Placeholder for the next iteration ──────────────────────────────────────
    # jellyfin_set_collection_image will add set_primary_image(item_id, image_bytes, mime):
    #   POST /Items/{id}/Images/Primary, body = the image base64-encoded, Content-Type set
    #   to the real mime type (image/jpeg, image/png...). Intentionally left out of this
    #   iteration (poster upload is out of scope for now); see tools/jellyfin_tools.py.

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "JellyfinClient":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.aclose()
