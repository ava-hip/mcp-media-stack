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

    async def recent_feed(self, indexer_id: int, category: int) -> list[dict[str, Any]]:
        """Return ONE indexer's latest releases for ONE category, newest-first-ish.

        An empty ``query`` makes Prowlarr hand the request straight to the indexer's
        Torznab endpoint as a "latest releases" feed — the only way to get releases by
        publication date instead of by seeders. Verified on this instance: the empty feed
        is also the *freshest* view obtainable, no keyword search reaches anything newer
        (C411's newest empty-feed item was 0.2h old vs 262h for the best keyword).

        Deliberately NOT folded into :meth:`search`: that one is the seeders-ranked
        keyword search whose signature ``prowlarr_search`` depends on.

        Three caveats, all measured on this instance — do not "simplify" them away:
        - ``indexerIds=-1`` (what the Servarr docs show for "all indexers") makes Prowlarr
          answer HTTP 400 "all selected indexers being unavailable". Passing one explicit
          indexer id per call avoids it *and* yields per-indexer failure detail.
        - every indexer advertises ``limits default=100 max=100``, and ``limit``/``offset``/
          ``sort`` are all ignored (``offset=100`` returns HTTP 200 with zero items), so the
          100-result cap is absolute and there is no paging.
        - the cap applies per REQUEST, so one request per category is mandatory:
          ``categories=[2000, 5000]`` returns 100 rows per indexer TOTAL, i.e. half of what
          two separate calls return.
        """
        return await self._get(
            "/search",
            query="",
            type="search",
            categories=category,
            indexerIds=indexer_id,
        )

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
