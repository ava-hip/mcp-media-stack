from typing import Any

import httpx


class ArrClientError(Exception):
    pass


# Readable filter alias -> canonical history "eventType" string(s) as returned by the API.
# Sonarr and Radarr share the download-side events; file events differ per service, so the
# "deleted"/"renamed" aliases cover both (only the relevant one ever matches per service).
HISTORY_EVENT_ALIASES: dict[str, set[str]] = {
    "grabbed": {"grabbed"},
    "imported": {"downloadFolderImported"},
    "failed": {"downloadFailed"},
    "deleted": {"episodeFileDeleted", "movieFileDeleted"},
    "renamed": {"episodeFileRenamed", "movieFileRenamed"},
    "ignored": {"downloadIgnored"},
}

_HISTORY_CANONICAL: set[str] = {c for values in HISTORY_EVENT_ALIASES.values() for c in values}


def _resolve_history_event_type(event_type: str) -> set[str]:
    """Map a readable alias or exact canonical eventType (case-insensitive) to a set.

    Raises ValueError listing the accepted values if nothing matches.
    """
    key = event_type.strip().lower()
    if key in HISTORY_EVENT_ALIASES:
        return HISTORY_EVENT_ALIASES[key]
    canonical = {c for c in _HISTORY_CANONICAL if c.lower() == key}
    if canonical:
        return canonical
    aliases = ", ".join(sorted(HISTORY_EVENT_ALIASES))
    canonicals = ", ".join(sorted(_HISTORY_CANONICAL))
    raise ValueError(
        f"Unknown event_type '{event_type}'. Accepted aliases: {aliases}. "
        f"Canonical values also accepted: {canonicals}."
    )


class ArrClient:
    """Base async HTTP client for *arr services (Sonarr, Radarr)."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout: float = 30.0,
        api_version: str = "v3",
    ) -> None:
        # Sonarr/Radarr use /api/v3; Prowlarr uses /api/v1 — hence the parameter.
        self._base = base_url.rstrip("/") + f"/api/{api_version}"
        self._client = httpx.AsyncClient(
            headers={"X-Api-Key": api_key},
            timeout=timeout,
        )

    async def _get(self, path: str, **params: Any) -> Any:
        try:
            response = await self._client.get(f"{self._base}{path}", params=params)
            response.raise_for_status()
            return response.json()
        except httpx.TimeoutException as e:
            raise ArrClientError(f"Request timed out: GET {path}") from e
        except httpx.ConnectError as e:
            raise ArrClientError(f"Cannot connect to service at {self._base}") from e
        except httpx.HTTPStatusError as e:
            raise ArrClientError(
                f"HTTP {e.response.status_code} on GET {path}: {e.response.text}"
            ) from e

    async def _post(self, path: str, body: dict[str, Any]) -> Any:
        try:
            response = await self._client.post(f"{self._base}{path}", json=body)
            response.raise_for_status()
            return response.json()
        except httpx.TimeoutException as e:
            raise ArrClientError(f"Request timed out: POST {path}") from e
        except httpx.ConnectError as e:
            raise ArrClientError(f"Cannot connect to service at {self._base}") from e
        except httpx.HTTPStatusError as e:
            raise ArrClientError(
                f"HTTP {e.response.status_code} on POST {path}: {e.response.text}"
            ) from e

    async def _put(self, path: str, body: dict[str, Any]) -> Any:
        try:
            response = await self._client.put(f"{self._base}{path}", json=body)
            response.raise_for_status()
            return response.json()
        except httpx.TimeoutException as e:
            raise ArrClientError(f"Request timed out: PUT {path}") from e
        except httpx.ConnectError as e:
            raise ArrClientError(f"Cannot connect to service at {self._base}") from e
        except httpx.HTTPStatusError as e:
            raise ArrClientError(
                f"HTTP {e.response.status_code} on PUT {path}: {e.response.text}"
            ) from e

    async def _delete(self, path: str, **params: Any) -> None:
        try:
            response = await self._client.delete(f"{self._base}{path}", params=params)
            response.raise_for_status()
        except httpx.TimeoutException as e:
            raise ArrClientError(f"Request timed out: DELETE {path}") from e
        except httpx.ConnectError as e:
            raise ArrClientError(f"Cannot connect to service at {self._base}") from e
        except httpx.HTTPStatusError as e:
            raise ArrClientError(
                f"HTTP {e.response.status_code} on DELETE {path}: {e.response.text}"
            ) from e

    # ── Shared observability helpers (same endpoints on Sonarr & Radarr) ──────

    async def disk_space(self) -> list[dict[str, Any]]:
        """Return disk usage per volume: path, label, freeSpace, totalSpace (bytes)."""
        return await self._get("/diskspace")

    async def health(self) -> list[dict[str, Any]]:
        """Return instance health checks (may be an empty list)."""
        return await self._get("/health")

    async def history(
        self,
        page: int = 1,
        page_size: int = 20,
        sort_key: str = "date",
        sort_direction: str = "descending",
    ) -> dict[str, Any]:
        """Return a raw page of history events (grab/import/deletion...).

        Note: the API's ``eventType`` query param expects an integer enum, so we never
        send it; filtering by the readable string is done client-side in
        :meth:`history_events`.
        """
        return await self._get(
            "/history",
            page=page,
            pageSize=page_size,
            sortKey=sort_key,
            sortDirection=sort_direction,
        )

    async def history_events(
        self,
        limit: int = 20,
        event_type: str | None = None,
    ) -> dict[str, Any]:
        """Return recent history events, optionally filtered by a readable event type.

        Returns ``{"records": [...], "note": str | None}``. The note is set when a
        client-side filter could not fill ``limit`` within the fetched window.
        Raises :class:`ValueError` for an unknown ``event_type`` (no API call made).
        """
        if event_type is None:
            data = await self.history(page=1, page_size=limit)
            return {"records": data.get("records", [])[:limit], "note": None}

        wanted = {w.lower() for w in _resolve_history_event_type(event_type)}
        window = max(limit * 5, 100)
        data = await self.history(page=1, page_size=window)
        records = data.get("records", [])
        matched = [r for r in records if str(r.get("eventType", "")).lower() in wanted]
        result = matched[:limit]
        note = None
        if len(result) < limit:
            note = (
                f"showing {len(result)} of up to {limit} "
                f"(searched the {window} most recent events)"
            )
        return {"records": result, "note": note}

    async def get_queue(self, page: int = 1, page_size: int = 100) -> dict[str, Any]:
        """Return a page of the download queue."""
        return await self._get("/queue", page=page, pageSize=page_size)

    async def delete_queue_item(
        self,
        queue_id: int,
        remove_from_client: bool = True,
        blocklist: bool = False,
    ) -> None:
        """Remove one item from the download queue."""
        await self._delete(
            f"/queue/{queue_id}",
            removeFromClient=str(remove_from_client).lower(),
            blocklist=str(blocklist).lower(),
        )

    async def bulk_delete_queue(
        self,
        ids: list[int],
        remove_from_client: bool = True,
        blocklist: bool = False,
    ) -> None:
        """Remove several queue items in one call (DELETE /queue/bulk, ids in the body)."""
        try:
            response = await self._client.request(
                "DELETE",
                f"{self._base}/queue/bulk",
                params={
                    "removeFromClient": str(remove_from_client).lower(),
                    "blocklist": str(blocklist).lower(),
                },
                json={"ids": ids},
            )
            response.raise_for_status()
        except httpx.TimeoutException as e:
            raise ArrClientError("Request timed out: DELETE /queue/bulk") from e
        except httpx.ConnectError as e:
            raise ArrClientError(f"Cannot connect to service at {self._base}") from e
        except httpx.HTTPStatusError as e:
            raise ArrClientError(
                f"HTTP {e.response.status_code} on DELETE /queue/bulk: {e.response.text}"
            ) from e

    async def queue_items_for_download(self, download_id: str) -> list[dict[str, Any]]:
        """Return all queue items sharing a downloadId (case-insensitive).

        A season pack is one torrent (one downloadId) spread over many queue rows;
        this locates them all so they can be removed in one gesture.
        """
        wanted = download_id.strip().lower()
        data = await self.get_queue(page_size=1000)
        return [
            r
            for r in (data.get("records") or [])
            if str(r.get("downloadId", "")).lower() == wanted
        ]

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "ArrClient":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.aclose()
