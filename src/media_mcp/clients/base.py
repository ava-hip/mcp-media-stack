from typing import Any

import httpx


class ArrClientError(Exception):
    pass


class ArrClient:
    """Base async HTTP client for *arr services (Sonarr, Radarr)."""

    def __init__(self, base_url: str, api_key: str, timeout: float = 30.0) -> None:
        self._base = base_url.rstrip("/") + "/api/v3"
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

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "ArrClient":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.aclose()
