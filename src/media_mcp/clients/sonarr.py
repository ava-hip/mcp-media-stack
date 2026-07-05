from typing import Any

from .base import ArrClient


class SonarrClient(ArrClient):
    """Async client for the Sonarr v3 API."""

    async def system_status(self) -> dict[str, Any]:
        return await self._get("/system/status")

    async def get_series(self) -> list[dict[str, Any]]:
        return await self._get("/series")

    async def lookup_series(self, term: str) -> list[dict[str, Any]]:
        return await self._get("/series/lookup", term=term)

    async def add_series(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._post("/series", body)

    async def delete_series(self, series_id: int, delete_files: bool = False) -> None:
        await self._delete(f"/series/{series_id}", deleteFiles=str(delete_files).lower())

    async def get_queue(self) -> dict[str, Any]:
        return await self._get("/queue")

    async def get_calendar(self, start: str, end: str) -> list[dict[str, Any]]:
        return await self._get("/calendar", start=start, end=end)

    async def get_quality_profiles(self) -> list[dict[str, Any]]:
        return await self._get("/qualityprofile")

    async def get_root_folders(self) -> list[dict[str, Any]]:
        return await self._get("/rootfolder")

    async def post_command(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._post("/command", body)
