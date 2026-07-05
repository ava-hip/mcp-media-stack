import json

import pytest
import respx
from httpx import Response
from mcp.server.fastmcp import FastMCP

from media_mcp.clients.base import ArrClientError
from media_mcp.clients.sonarr import SonarrClient
from media_mcp.tools.sonarr_tools import register_sonarr_tools

BASE = "http://sonarr.test:8989/api/v3"


@pytest.fixture
def client():
    return SonarrClient("http://sonarr.test:8989", "test-api-key")


@respx.mock
async def test_system_status(client):
    respx.get(f"{BASE}/system/status").mock(
        return_value=Response(
            200, json={"appName": "Sonarr", "version": "4.0.0", "isDebug": False, "urlBase": ""}
        )
    )
    data = await client.system_status()
    assert data["version"] == "4.0.0"


@respx.mock
async def test_get_series(client):
    payload = [
        {
            "id": 1,
            "title": "Breaking Bad",
            "year": 2008,
            "status": "ended",
            "monitored": True,
            "episodeCount": 62,
            "episodeFileCount": 62,
            "tvdbId": 81189,
        }
    ]
    respx.get(f"{BASE}/series").mock(return_value=Response(200, json=payload))
    data = await client.get_series()
    assert len(data) == 1
    assert data[0]["title"] == "Breaking Bad"


@respx.mock
async def test_get_series_connection_error(client):
    respx.get(f"{BASE}/series").mock(return_value=Response(401, text="Unauthorized"))
    with pytest.raises(ArrClientError):
        await client.get_series()


@respx.mock
async def test_delete_series_dry_run():
    """sonarr_delete_series with confirm=False must not call the API."""
    test_mcp = FastMCP("test")
    register_sonarr_tools(test_mcp)

    delete_fn = test_mcp._tool_manager.get_tool("sonarr_delete_series").fn

    result = await delete_fn(series_id=42, delete_files=True, confirm=False)
    assert "DRY-RUN" in result
    assert "42" in result
    assert "delete files" in result.lower()


@respx.mock
async def test_delete_series_confirm(client):
    respx.delete(f"{BASE}/series/42").mock(return_value=Response(200, json={}))
    await client.delete_series(42, delete_files=False)


@respx.mock
async def test_queue_empty(client):
    respx.get(f"{BASE}/queue").mock(return_value=Response(200, json={"records": []}))
    data = await client.get_queue()
    assert data["records"] == []


@respx.mock
async def test_lookup_series(client):
    payload = [{"title": "The Wire", "year": 2002, "tvdbId": 79126, "overview": "Crime drama."}]
    respx.get(f"{BASE}/series/lookup").mock(return_value=Response(200, json=payload))
    data = await client.lookup_series("The Wire")
    assert data[0]["tvdbId"] == 79126


@respx.mock
async def test_http_error_raises_arr_client_error(client):
    respx.get(f"{BASE}/series").mock(return_value=Response(401, text="Unauthorized"))
    with pytest.raises(ArrClientError, match="401"):
        await client.get_series()


def _series_with_seasons() -> dict:
    return {
        "id": 7,
        "title": "Test Show",
        "seasons": [
            {
                "seasonNumber": 0,
                "monitored": False,
                "statistics": {"episodeFileCount": 2, "totalEpisodeCount": 3},
            },
            {
                "seasonNumber": 1,
                "monitored": True,
                "statistics": {"episodeFileCount": 10, "totalEpisodeCount": 10},
            },
            {
                "seasonNumber": 2,
                "monitored": True,
                "statistics": {"episodeFileCount": 4, "totalEpisodeCount": 8},
            },
        ],
    }


def _seasons_tool(name: str, monkeypatch):
    # Point the tool's client factory at the mocked test server.
    monkeypatch.setattr(
        "media_mcp.tools.sonarr_tools._client",
        lambda: SonarrClient("http://sonarr.test:8989", "test-api-key"),
    )
    test_mcp = FastMCP("test")
    register_sonarr_tools(test_mcp)
    return test_mcp._tool_manager.get_tool(name).fn


@respx.mock
async def test_series_seasons_summary(monkeypatch):
    respx.get(f"{BASE}/series/7").mock(return_value=Response(200, json=_series_with_seasons()))

    fn = _seasons_tool("sonarr_series_seasons", monkeypatch)
    result = await fn(series_id=7)

    assert "Specials" in result
    # Season 1 is complete (10/10), season 2 is incomplete (4/8).
    assert "10/10 episodes  (complete)" in result
    assert "4/8 episodes  (incomplete)" in result
    # Specials is incomplete (2/3).
    assert "2/3 episodes  (incomplete)" in result


@respx.mock
async def test_series_seasons_not_found(monkeypatch):
    respx.get(f"{BASE}/series/99").mock(return_value=Response(404, text="Not Found"))

    fn = _seasons_tool("sonarr_series_seasons", monkeypatch)
    result = await fn(series_id=99)

    assert "series not found" in result.lower()


@respx.mock
async def test_set_season_monitoring_puts_full_object(monkeypatch):
    respx.get(f"{BASE}/series/7").mock(return_value=Response(200, json=_series_with_seasons()))
    put_route = respx.put(f"{BASE}/series/7").mock(
        return_value=Response(200, json=_series_with_seasons())
    )

    fn = _seasons_tool("sonarr_set_season_monitoring", monkeypatch)
    result = await fn(series_id=7, season_number=2, monitored=True)

    assert put_route.called
    body = json.loads(put_route.calls.last.request.content)
    by_number = {s["seasonNumber"]: s for s in body["seasons"]}
    # Target season flipped to monitored=True.
    assert by_number[2]["monitored"] is True
    # Other seasons untouched.
    assert by_number[0]["monitored"] is False
    assert by_number[1]["monitored"] is True
    assert "season 2 is now monitored" in result.lower()


@respx.mock
async def test_set_season_monitoring_unknown_season(monkeypatch):
    respx.get(f"{BASE}/series/7").mock(return_value=Response(200, json=_series_with_seasons()))
    put_route = respx.put(f"{BASE}/series/7").mock(return_value=Response(200, json={}))

    fn = _seasons_tool("sonarr_set_season_monitoring", monkeypatch)
    result = await fn(series_id=7, season_number=9, monitored=True)

    assert not put_route.called
    assert "not found" in result.lower()
    assert "0, 1, 2" in result


@respx.mock
async def test_search_season_dry_run_does_not_post(monkeypatch):
    respx.get(f"{BASE}/series/7").mock(return_value=Response(200, json=_series_with_seasons()))
    command_route = respx.post(f"{BASE}/command").mock(return_value=Response(201, json={}))

    fn = _seasons_tool("sonarr_search_season", monkeypatch)
    result = await fn(series_id=7, season_number=2, confirm=False)

    assert not command_route.called
    assert "DRY-RUN" in result
    assert "8 episodes" in result


@respx.mock
async def test_search_season_confirm_posts_command(monkeypatch):
    respx.get(f"{BASE}/series/7").mock(return_value=Response(200, json=_series_with_seasons()))
    command_route = respx.post(f"{BASE}/command").mock(return_value=Response(201, json={}))

    fn = _seasons_tool("sonarr_search_season", monkeypatch)
    result = await fn(series_id=7, season_number=2, confirm=True)

    assert command_route.called
    body = json.loads(command_route.calls.last.request.content)
    assert body == {"name": "SeasonSearch", "seriesId": 7, "seasonNumber": 2}
    assert "launched" in result.lower()


GIB = 1_073_741_824


def _ep_file(file_id: int, season: int, size: int) -> dict:
    return {
        "id": file_id,
        "seriesId": 7,
        "seasonNumber": season,
        "relativePath": f"S{season:02d}/e{file_id:02d}.mkv",
        "size": size,
    }


def _episode_files() -> list[dict]:
    # Season 1: 2 files (1 GB each); Season 2: 3 files (2 GB each).
    return [
        _ep_file(11, 1, GIB),
        _ep_file(12, 1, GIB),
        _ep_file(21, 2, 2 * GIB),
        _ep_file(22, 2, 2 * GIB),
        _ep_file(23, 2, 2 * GIB),
    ]


@respx.mock
async def test_delete_season_dry_run(monkeypatch):
    respx.get(f"{BASE}/series/7").mock(return_value=Response(200, json=_series_with_seasons()))
    respx.get(f"{BASE}/episodefile").mock(return_value=Response(200, json=_episode_files()))
    delete_route = respx.delete(url__regex=rf"{BASE}/episodefile/\d+").mock(
        return_value=Response(200, json={})
    )

    fn = _seasons_tool("sonarr_delete_season", monkeypatch)
    result = await fn(series_id=7, season_number=2, confirm=False)

    assert not delete_route.called
    assert "DRY-RUN" in result
    # Only season 2's 3 files (6 GB total), not season 1.
    assert "3 episode file(s)" in result
    assert "6.0 GB" in result
    assert "hardlinked" in result.lower()


@respx.mock
async def test_delete_season_confirm_deletes_only_target(monkeypatch):
    respx.get(f"{BASE}/series/7").mock(return_value=Response(200, json=_series_with_seasons()))
    respx.get(f"{BASE}/episodefile").mock(return_value=Response(200, json=_episode_files()))
    delete_route = respx.delete(url__regex=rf"{BASE}/episodefile/\d+").mock(
        return_value=Response(200, json={})
    )

    fn = _seasons_tool("sonarr_delete_season", monkeypatch)
    result = await fn(series_id=7, season_number=2, confirm=True)

    deleted_ids = {int(call.request.url.path.rsplit("/", 1)[1]) for call in delete_route.calls}
    # Exactly season 2's files, none from season 1.
    assert deleted_ids == {21, 22, 23}
    assert "3/3" in result
    assert "6.0 GB" in result


@respx.mock
async def test_delete_season_no_files(monkeypatch):
    respx.get(f"{BASE}/series/7").mock(return_value=Response(200, json=_series_with_seasons()))
    respx.get(f"{BASE}/episodefile").mock(return_value=Response(200, json=_episode_files()))
    delete_route = respx.delete(url__regex=rf"{BASE}/episodefile/\d+").mock(
        return_value=Response(200, json={})
    )

    fn = _seasons_tool("sonarr_delete_season", monkeypatch)
    result = await fn(series_id=7, season_number=5, confirm=True)

    assert not delete_route.called
    assert "no episode files found for season 5" in result.lower()


@respx.mock
async def test_delete_season_series_not_found(monkeypatch):
    respx.get(f"{BASE}/series/99").mock(return_value=Response(404, text="Not Found"))

    fn = _seasons_tool("sonarr_delete_season", monkeypatch)
    result = await fn(series_id=99, season_number=1, confirm=True)

    assert "series not found" in result.lower()


@respx.mock
async def test_delete_episode_file_dry_run(monkeypatch):
    respx.get(f"{BASE}/episodefile/21").mock(
        return_value=Response(200, json=_ep_file(21, 2, 2 * GIB))
    )
    delete_route = respx.delete(f"{BASE}/episodefile/21").mock(return_value=Response(200, json={}))

    fn = _seasons_tool("sonarr_delete_episode_file", monkeypatch)
    result = await fn(episode_file_id=21, confirm=False)

    assert not delete_route.called
    assert "DRY-RUN" in result
    assert "S02/e21.mkv" in result
    assert "2.0 GB" in result
    assert "hardlinked" in result.lower()


@respx.mock
async def test_delete_episode_file_confirm(monkeypatch):
    respx.get(f"{BASE}/episodefile/21").mock(
        return_value=Response(200, json=_ep_file(21, 2, 2 * GIB))
    )
    delete_route = respx.delete(f"{BASE}/episodefile/21").mock(return_value=Response(200, json={}))

    fn = _seasons_tool("sonarr_delete_episode_file", monkeypatch)
    result = await fn(episode_file_id=21, confirm=True)

    assert delete_route.called
    assert "freed" in result.lower()


# ── Observability tools ───────────────────────────────────────────────────────
# The disk_space / health / history / delete_queue_item helpers live in the shared
# ArrClient base, so they are exercised in depth here (Sonarr) and only smoke-tested
# on the Radarr side (see tests/test_radarr.py).


@respx.mock
async def test_disk_space_sorted_and_formatted(monkeypatch):
    payload = [
        {"path": "/media", "label": "Media", "freeSpace": 30 * GIB, "totalSpace": 100 * GIB},
        {
            "path": "/downloads",
            "label": "Downloads",
            "freeSpace": 10 * GIB,
            "totalSpace": 100 * GIB,
        },
    ]
    respx.get(f"{BASE}/diskspace").mock(return_value=Response(200, json=payload))

    fn = _seasons_tool("sonarr_disk_space", monkeypatch)
    result = await fn()

    # Fullest volume (lowest % free) listed first.
    assert result.index("Downloads") < result.index("Media")
    assert "10.0 GB free / 100.0 GB" in result
    assert "(10% free)" in result
    assert "(30% free)" in result


@respx.mock
async def test_disk_space_empty(monkeypatch):
    respx.get(f"{BASE}/diskspace").mock(return_value=Response(200, json=[]))

    fn = _seasons_tool("sonarr_disk_space", monkeypatch)
    result = await fn()

    assert "no disk space information" in result.lower()


@respx.mock
async def test_health_with_issues(monkeypatch):
    payload = [
        {"source": "DownloadClientCheck", "type": "warning", "message": "No download client"},
        {"source": "IndexerStatusCheck", "type": "error", "message": "Indexers down"},
    ]
    respx.get(f"{BASE}/health").mock(return_value=Response(200, json=payload))

    fn = _seasons_tool("sonarr_health", monkeypatch)
    result = await fn()

    assert "[warning] DownloadClientCheck: No download client" in result
    assert "[error] IndexerStatusCheck: Indexers down" in result


@respx.mock
async def test_health_empty(monkeypatch):
    respx.get(f"{BASE}/health").mock(return_value=Response(200, json=[]))

    fn = _seasons_tool("sonarr_health", monkeypatch)
    result = await fn()

    assert "no health issues" in result.lower()


@respx.mock
async def test_history_pagination_and_download_id(monkeypatch):
    payload = {
        "page": 1,
        "pageSize": 5,
        "totalRecords": 2,
        "records": [
            {
                "eventType": "grabbed",
                "sourceTitle": "Show.S01E01",
                "date": "2026-07-05T10:00:00Z",
                "downloadId": "ABC123HASH",
            },
            {
                "eventType": "seriesFolderImported",
                "sourceTitle": "Show.S01E02",
                "date": "2026-07-05T09:00:00Z",
            },
        ],
    }
    route = respx.get(f"{BASE}/history").mock(return_value=Response(200, json=payload))

    fn = _seasons_tool("sonarr_history", monkeypatch)
    result = await fn(limit=5)

    params = route.calls.last.request.url.params
    assert params["pageSize"] == "5"
    assert params["sortKey"] == "date"
    assert params["sortDirection"] == "descending"
    assert "eventType" not in params  # no filter passed

    # downloadId surfaced when present, handled when absent.
    assert "downloadId=ABC123HASH" in result
    assert "no downloadId" in result


def _history_record(event_type: str, title: str, download_id: str | None = None) -> dict:
    rec = {"eventType": event_type, "sourceTitle": title, "date": "2026-07-05T10:00:00Z"}
    if download_id is not None:
        rec["downloadId"] = download_id
    return rec


def _mixed_history() -> dict:
    records = [
        _history_record("grabbed", "Show.S01E01", "HASH1"),
        _history_record("downloadFolderImported", "Show.S01E01"),
        _history_record("episodeFileDeleted", "Show.S01E02"),
        _history_record("grabbed", "Show.S01E03", "HASH3"),
        _history_record("downloadFailed", "Show.S01E04"),
    ]
    return {"page": 1, "pageSize": 100, "totalRecords": len(records), "records": records}


@respx.mock
async def test_history_filter_by_alias_grabbed(monkeypatch):
    route = respx.get(f"{BASE}/history").mock(return_value=Response(200, json=_mixed_history()))

    fn = _seasons_tool("sonarr_history", monkeypatch)
    result = await fn(limit=20, event_type="grabbed")

    # Filtering is client-side: the API must never receive an eventType query param.
    assert "eventType" not in route.calls.last.request.url.params
    # Only the two grabbed records survive.
    assert "Show.S01E01" in result
    assert "Show.S01E03" in result
    assert "downloadFolderImported" not in result
    assert "episodeFileDeleted" not in result
    assert "downloadFailed" not in result
    # downloadId still surfaced on grabbed records.
    assert "downloadId=HASH1" in result
    assert "downloadId=HASH3" in result


@respx.mock
async def test_history_alias_imported(monkeypatch):
    respx.get(f"{BASE}/history").mock(return_value=Response(200, json=_mixed_history()))

    fn = _seasons_tool("sonarr_history", monkeypatch)
    result = await fn(limit=20, event_type="imported")

    assert "downloadFolderImported" in result
    assert "grabbed" not in result
    assert "episodeFileDeleted" not in result


@respx.mock
async def test_history_canonical_string_accepted(monkeypatch):
    respx.get(f"{BASE}/history").mock(return_value=Response(200, json=_mixed_history()))

    fn = _seasons_tool("sonarr_history", monkeypatch)
    result = await fn(limit=20, event_type="downloadFailed")

    assert "downloadFailed" in result
    assert "Show.S01E04" in result
    assert "downloadFolderImported" not in result


@respx.mock
async def test_history_unknown_event_type_no_api_call(monkeypatch):
    route = respx.get(f"{BASE}/history").mock(return_value=Response(200, json=_mixed_history()))

    fn = _seasons_tool("sonarr_history", monkeypatch)
    result = await fn(limit=20, event_type="foo")

    assert not route.called
    assert "unknown event_type 'foo'" in result.lower()
    # The error lists the accepted aliases.
    assert "grabbed" in result
    assert "imported" in result
    assert "deleted" in result


@respx.mock
async def test_history_window_not_filled_adds_note(monkeypatch):
    respx.get(f"{BASE}/history").mock(return_value=Response(200, json=_mixed_history()))

    fn = _seasons_tool("sonarr_history", monkeypatch)
    result = await fn(limit=20, event_type="grabbed")

    # Only 2 grabbed matches against a limit of 20 -> note about the searched window.
    assert "showing 2 of up to 20" in result
    assert "most recent events" in result


@respx.mock
async def test_history_no_filter_no_eventtype_param(monkeypatch):
    respx.get(f"{BASE}/history").mock(return_value=Response(200, json=_mixed_history()))

    fn = _seasons_tool("sonarr_history", monkeypatch)
    result = await fn(limit=20)

    # No filter -> all records returned, no note.
    assert "showing" not in result
    assert "grabbed" in result
    assert "downloadFailed" in result


def _queue_payload() -> dict:
    return {
        "page": 1,
        "pageSize": 100,
        "totalRecords": 1,
        "records": [
            {
                "id": 77,
                "title": "Stuck.Release.S01E01",
                "status": "stalled",
                "size": 2 * GIB,
                "sizeleft": 1 * GIB,
                "timeleft": None,
            }
        ],
    }


@respx.mock
async def test_delete_queue_item_dry_run(monkeypatch):
    respx.get(f"{BASE}/queue").mock(return_value=Response(200, json=_queue_payload()))
    delete_route = respx.delete(url__regex=rf"{BASE}/queue/\d+").mock(
        return_value=Response(200, json={})
    )

    fn = _seasons_tool("sonarr_delete_queue_item", monkeypatch)
    result = await fn(queue_id=77, remove_from_client=True, blocklist=False, confirm=False)

    assert not delete_route.called
    assert "DRY-RUN" in result
    assert "Stuck.Release.S01E01" in result
    assert "status=stalled" in result
    assert "removed from the download client" in result.lower()
    assert "not be blocklisted" in result.lower()


@respx.mock
async def test_delete_queue_item_not_found(monkeypatch):
    respx.get(f"{BASE}/queue").mock(return_value=Response(200, json=_queue_payload()))
    delete_route = respx.delete(url__regex=rf"{BASE}/queue/\d+").mock(
        return_value=Response(200, json={})
    )

    fn = _seasons_tool("sonarr_delete_queue_item", monkeypatch)
    result = await fn(queue_id=999, confirm=True)

    assert not delete_route.called
    assert "not found in queue" in result.lower()


@respx.mock
async def test_delete_queue_item_confirm_passes_query_params(monkeypatch):
    respx.get(f"{BASE}/queue").mock(return_value=Response(200, json=_queue_payload()))
    delete_route = respx.delete(f"{BASE}/queue/77").mock(return_value=Response(200, json={}))

    fn = _seasons_tool("sonarr_delete_queue_item", monkeypatch)
    result = await fn(queue_id=77, remove_from_client=True, blocklist=True, confirm=True)

    assert delete_route.called
    params = delete_route.calls.last.request.url.params
    assert params["removeFromClient"] == "true"
    assert params["blocklist"] == "true"
    assert "Removed queue item [77]" in result
