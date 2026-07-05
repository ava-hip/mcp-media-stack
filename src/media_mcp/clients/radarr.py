from typing import Any

from .base import ArrClient


class RadarrClient(ArrClient):
    """Async client for the Radarr v3 API."""

    async def system_status(self) -> dict[str, Any]:
        return await self._get("/system/status")

    async def get_movies(self) -> list[dict[str, Any]]:
        return await self._get("/movie")

    async def get_movie(self, movie_id: int) -> dict[str, Any]:
        """Return the full movie object (includes movieFile when hasFile is true)."""
        return await self._get(f"/movie/{movie_id}")

    async def update_movie(self, movie_id: int, body: dict[str, Any]) -> dict[str, Any]:
        """PUT the complete movie object back to Radarr (no partial patch)."""
        return await self._put(f"/movie/{movie_id}", body)

    async def movie_search(self, movie_id: int) -> dict[str, Any]:
        """Trigger a search/download for an already-added movie."""
        return await self._post("/command", {"name": "MoviesSearch", "movieIds": [movie_id]})

    async def delete_movie_file(self, file_id: int) -> None:
        """Delete a single movie file (from disk + Radarr database)."""
        await self._delete(f"/moviefile/{file_id}")

    async def get_calendar(self, start: str, end: str) -> list[dict[str, Any]]:
        return await self._get("/calendar", start=start, end=end)

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
