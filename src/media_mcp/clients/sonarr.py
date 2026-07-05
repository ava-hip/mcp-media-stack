from typing import Any

from .base import ArrClient


class SonarrClient(ArrClient):
    """Async client for the Sonarr v3 API."""

    async def system_status(self) -> dict[str, Any]:
        return await self._get("/system/status")

    async def get_series(self) -> list[dict[str, Any]]:
        return await self._get("/series")

    async def get_series_by_id(self, series_id: int) -> dict[str, Any]:
        """Return the full series object (includes the seasons array)."""
        return await self._get(f"/series/{series_id}")

    async def update_series(self, series_id: int, body: dict[str, Any]) -> dict[str, Any]:
        """PUT the complete series object back to Sonarr (no partial patch)."""
        return await self._put(f"/series/{series_id}", body)

    async def season_search(self, series_id: int, season_number: int) -> dict[str, Any]:
        """Trigger a search/download of a whole season."""
        return await self._post(
            "/command",
            {"name": "SeasonSearch", "seriesId": series_id, "seasonNumber": season_number},
        )

    async def lookup_series(self, term: str) -> list[dict[str, Any]]:
        return await self._get("/series/lookup", term=term)

    async def add_series(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._post("/series", body)

    async def delete_series(self, series_id: int, delete_files: bool = False) -> None:
        await self._delete(f"/series/{series_id}", deleteFiles=str(delete_files).lower())

    async def list_episode_files(self, series_id: int) -> list[dict[str, Any]]:
        """Return all episode files for a series."""
        return await self._get("/episodefile", seriesId=series_id)

    async def get_episode_file(self, file_id: int) -> dict[str, Any]:
        """Return a single episode file by its id."""
        return await self._get(f"/episodefile/{file_id}")

    async def delete_episode_file(self, file_id: int) -> None:
        """Delete a single episode file (from disk + Sonarr database)."""
        await self._delete(f"/episodefile/{file_id}")

    async def history_series(
        self, series_id: int, season_number: int
    ) -> list[dict[str, Any]]:
        """Return history events for a whole season (grab/import/...), including the
        downloadId (the origin torrent hash) on grab and import events.
        """
        return await self._get(
            "/history/series", seriesId=series_id, seasonNumber=season_number
        )

    async def get_calendar(self, start: str, end: str) -> list[dict[str, Any]]:
        return await self._get("/calendar", start=start, end=end)

    async def get_quality_profiles(self) -> list[dict[str, Any]]:
        return await self._get("/qualityprofile")

    async def get_root_folders(self) -> list[dict[str, Any]]:
        return await self._get("/rootfolder")

    async def post_command(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._post("/command", body)
