from typing import Any

import httpx

from .base import ArrClient, ArrClientError


class ProwlarrClient(ArrClient):
    """Async client for the Prowlarr v1 API (Servarr indexer manager).

    Same auth/error handling as ArrClient, but on /api/v1 instead of /api/v3.
    """

    def __init__(self, base_url: str, api_key: str, timeout: float = 30.0) -> None:
        super().__init__(base_url, api_key, timeout=timeout, api_version="v1")

    async def system_status(self) -> dict[str, Any]:
        return await self._get("/system/status")

    async def get_indexers(self) -> list[dict[str, Any]]:
        return await self._get("/indexer")

    async def get_indexer(self, indexer_id: int) -> dict[str, Any]:
        return await self._get(f"/indexer/{indexer_id}")

    async def indexer_status(self) -> list[dict[str, Any]]:
        """Indexers currently failing / temporarily disabled (empty when all healthy).

        Each entry carries indexerId + disabledTill / mostRecentFailure / initialFailure
        (no textual reason), so the tool cross-references the indexer list for names.
        """
        return await self._get("/indexerstatus")

    async def health(self) -> list[dict[str, Any]]:
        return await self._get("/health")

    async def list_download_clients(self) -> list[dict[str, Any]]:
        """Return the download clients configured in Prowlarr (grab destinations)."""
        return await self._get("/downloadclient")

    async def search(
        self,
        query: str,
        indexer_ids: list[int] | None = None,
        categories: list[int] | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Cross-indexer search. Note: Prowlarr's `limit` is not a hard cap, so callers
        should also trim client-side.
        """
        params: dict[str, Any] = {"query": query, "limit": limit}
        if indexer_ids:
            params["indexerIds"] = indexer_ids
        if categories:
            params["categories"] = categories
        return await self._get("/search", **params)

    async def grab(self, guid: str, indexer_id: int) -> dict[str, Any]:
        """Send a release to Prowlarr's download client (POST /search {guid, indexerId}).

        Returns {"ok": bool, "message": str}. A rejected grab (bad/expired guid, etc.)
        comes back as HTTP 400 with validation failures — captured, not raised. Prowlarr
        applies its Mapped Categories to route the download; no category is passed here.
        """
        url = f"{self._base}/search"
        try:
            response = await self._client.post(
                url, json={"guid": guid, "indexerId": indexer_id}
            )
        except httpx.TimeoutException as e:
            raise ArrClientError("Request timed out: POST /search (grab)") from e
        except httpx.ConnectError as e:
            raise ArrClientError(f"Cannot connect to service at {self._base}") from e

        if response.status_code in (200, 201):
            return {"ok": True, "message": "grabbed"}
        if response.status_code == 400:
            try:
                data = response.json()
            except ValueError:
                data = []
            msgs = [
                f.get("errorMessage", "") for f in data if isinstance(f, dict)
            ] if isinstance(data, list) else []
            return {"ok": False, "message": "; ".join(m for m in msgs if m) or "grab rejected"}
        raise ArrClientError(
            f"HTTP {response.status_code} on POST /search (grab): {response.text}"
        )

    async def test_all_indexers(self) -> list[dict[str, Any]]:
        """Test every indexer; returns [{id, isValid, validationFailures[]}]."""
        return await self._post("/indexer/testall", {})

    async def test_indexer(self, indexer: dict[str, Any]) -> dict[str, Any]:
        """Test a single indexer (body = the full indexer object).

        Returns {"is_valid": bool, "failures": [messages]}. A failing test comes back
        as HTTP 400 with validation failures — that is a test result, not a hard error,
        so it is captured rather than raised.
        """
        url = f"{self._base}/indexer/test"
        try:
            response = await self._client.post(url, json=indexer)
        except httpx.TimeoutException as e:
            raise ArrClientError("Request timed out: POST /indexer/test") from e
        except httpx.ConnectError as e:
            raise ArrClientError(f"Cannot connect to service at {self._base}") from e

        if response.status_code == 200:
            return {"is_valid": True, "failures": []}
        if response.status_code == 400:
            try:
                data = response.json()
            except ValueError:
                data = []
            failures = [
                f.get("errorMessage", "") for f in data if isinstance(f, dict)
            ] if isinstance(data, list) else []
            return {"is_valid": False, "failures": [m for m in failures if m] or ["test failed"]}
        raise ArrClientError(
            f"HTTP {response.status_code} on POST /indexer/test: {response.text}"
        )
