from typing import Any

from .base import ArrClient


class RadarrClient(ArrClient):
    """Async client for the Radarr v3 API."""

    async def system_status(self) -> dict[str, Any]:
        return await self._get("/system/status")

    async def get_movies(self) -> list[dict[str, Any]]:
        return await self._get("/movie")

    async def lookup_movie(self, term: str) -> list[dict[str, Any]]:
        return await self._get("/movie/lookup", term=term)

    async def add_movie(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._post("/movie", body)

    async def delete_movie(self, movie_id: int, delete_files: bool = False) -> None:
        await self._delete(f"/movie/{movie_id}", deleteFiles=str(delete_files).lower())

    async def get_queue(self) -> dict[str, Any]:
        return await self._get("/queue")

    async def get_quality_profiles(self) -> list[dict[str, Any]]:
        return await self._get("/qualityprofile")

    async def get_root_folders(self) -> list[dict[str, Any]]:
        return await self._get("/rootfolder")

    async def post_command(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._post("/command", body)
