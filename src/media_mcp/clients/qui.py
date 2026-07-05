from typing import Any

import httpx


class QuiClientError(Exception):
    pass


class QuiClient:
    """Async client for the "qui" web API (autobrr), a multi-instance manager on top
    of qBittorrent.

    Deliberately NOT a subclass of ArrClient: qui uses a different base path (/api),
    a different auth header (X-API-Key) and an instance-oriented routing scheme.
    All torrent operations target a resolved qBittorrent instance id.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        instance: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._base = base_url.rstrip("/") + "/api" if base_url else ""
        self._api_key = api_key
        self._instance = (instance or "").strip()
        self._client = httpx.AsyncClient(
            headers={"X-API-Key": api_key} if api_key else {},
            timeout=timeout,
        )

    def _ensure_configured(self) -> None:
        if not self._base or not self._api_key:
            raise QuiClientError(
                "qui is not configured: set QUI_URL and QUI_API_KEY in the environment."
            )

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        self._ensure_configured()
        try:
            response = await self._client.request(method, f"{self._base}{path}", **kwargs)
            response.raise_for_status()
            return response
        except httpx.TimeoutException as e:
            raise QuiClientError(f"Request timed out: {method} {path}") from e
        except httpx.ConnectError as e:
            raise QuiClientError(f"Cannot connect to qui at {self._base}") from e
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                raise QuiClientError("Unauthorized (401): check QUI_API_KEY.") from e
            raise QuiClientError(
                f"HTTP {e.response.status_code} on {method} {path}: {e.response.text}"
            ) from e

    async def _get(self, path: str, **params: Any) -> Any:
        response = await self._request("GET", path, params=params)
        return response.json()

    async def _post(self, path: str, body: dict[str, Any]) -> Any:
        response = await self._request("POST", path, json=body)
        # bulk-action returns 200 with an empty body; tolerate that.
        return response.json() if response.content else None

    # ── Instances ─────────────────────────────────────────────────────────────

    async def list_instances(self) -> list[dict[str, Any]]:
        """Return the qBittorrent instances managed by qui."""
        return await self._get("/instances")

    async def resolve_instance_id(self) -> int:
        """Resolve the target instance id.

        Uses QUI_INSTANCE (matched against id or name, case-insensitive) when set;
        otherwise auto-selects the single instance. Raises QuiClientError with a
        clear listing when the instance is unknown or ambiguous.
        """
        instances = await self.list_instances()
        if not instances:
            raise QuiClientError("No qBittorrent instances are configured in qui.")

        if self._instance:
            for i in instances:
                name = str(i.get("name", ""))
                if str(i.get("id")) == self._instance or name.lower() == self._instance.lower():
                    return int(i["id"])
            raise QuiClientError(
                f"qui instance '{self._instance}' not found. "
                f"Available: {self._format_instances(instances)}."
            )

        if len(instances) == 1:
            return int(instances[0]["id"])

        raise QuiClientError(
            "Multiple qui instances found; set QUI_INSTANCE to one of: "
            f"{self._format_instances(instances)}."
        )

    @staticmethod
    def _format_instances(instances: list[dict[str, Any]]) -> str:
        return ", ".join(f"{i.get('id')}:{i.get('name')}" for i in instances)

    # ── Torrents ──────────────────────────────────────────────────────────────

    async def list_torrents(
        self,
        instance_id: int,
        search: str | None = None,
        limit: int = 300,
    ) -> dict[str, Any]:
        """Return a page of torrents for an instance (optionally filtered by search)."""
        params: dict[str, Any] = {"limit": limit}
        if search:
            params["search"] = search
        return await self._get(f"/instances/{instance_id}/torrents", **params)

    async def resolve_torrent(self, instance_id: int, value: str) -> dict[str, Any]:
        """Resolve a full/prefix hash to a single torrent (case-insensitive).

        Shared by every hash-taking tool (get/pause/resume/delete). The hash is the
        bridge to the Sonarr/Radarr history downloadId, which is the FULL hash and may
        be upper-cased — the nominal full-hash path keeps working.

        Matching order:
        - exact full-hash match wins;
        - otherwise, treat the input as a hash PREFIX:
            * exactly one match -> use it;
            * none -> QuiClientError "No torrent found with hash '<value>'";
            * several -> QuiClientError listing the candidate full hashes (never guess).
        """
        wanted = value.strip().lower()
        data = await self.list_torrents(instance_id, search=value)
        # qui returns "torrents": null (not []) when nothing matches; normalize.
        torrents = data.get("torrents") or []

        for t in torrents:
            if str(t.get("hash", "")).lower() == wanted:
                return t

        prefix_matches = [
            t for t in torrents if str(t.get("hash", "")).lower().startswith(wanted)
        ]
        if len(prefix_matches) == 1:
            return prefix_matches[0]
        if not prefix_matches:
            raise QuiClientError(f"No torrent found with hash '{value}'.")
        candidates = "; ".join(
            f"{t.get('hash')} ({t.get('name', '')})" for t in prefix_matches
        )
        raise QuiClientError(
            f"Ambiguous hash prefix '{value}' matches {len(prefix_matches)} torrents: "
            f"{candidates}. Provide a longer prefix or the full hash."
        )

    async def local_matches(
        self, instance_id: int, torrent_hash: str, strict: bool = True
    ) -> list[dict[str, Any]]:
        """Return the local cross-seed siblings of a torrent (same content, other hashes).

        Does NOT include the origin torrent itself. Each match carries a `match_type`
        (content_path | name | release). Raises QuiClientError if the cross-seed feature
        is unavailable — callers may treat that as "no siblings known".
        """
        data = await self._get(
            f"/cross-seed/torrents/{instance_id}/{torrent_hash}/local-matches",
            strict=str(strict).lower(),
        )
        # "matches" can be null when there are no siblings; treat as empty.
        return data.get("matches") or []

    async def bulk_action(
        self,
        instance_id: int,
        action: str,
        hashes: list[str],
        delete_files: bool = False,
    ) -> Any:
        """Perform a bulk action (pause/resume/delete/...) on the given hashes."""
        body: dict[str, Any] = {"action": action, "hashes": hashes}
        if action == "delete":
            body["deleteFiles"] = delete_files
        return await self._post(f"/instances/{instance_id}/torrents/bulk-action", body)

    async def delete_torrent(
        self,
        instance_id: int,
        torrent_hash: str,
        delete_files: bool = False,
    ) -> Any:
        return await self.bulk_action(instance_id, "delete", [torrent_hash], delete_files)

    async def pause_torrent(self, instance_id: int, torrent_hash: str) -> Any:
        return await self.bulk_action(instance_id, "pause", [torrent_hash])

    async def resume_torrent(self, instance_id: int, torrent_hash: str) -> Any:
        return await self.bulk_action(instance_id, "resume", [torrent_hash])

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "QuiClient":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.aclose()
