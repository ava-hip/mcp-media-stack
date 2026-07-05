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
