import pytest
import respx
from httpx import Response

from media_mcp.clients.base import ArrClientError
from media_mcp.clients.sonarr import SonarrClient

BASE = "http://sonarr.test:8989/api/v3"


@pytest.fixture
def client():
    return SonarrClient("http://sonarr.test:8989", "test-api-key")


@respx.mock
async def test_system_status(client):
    respx.get(f"{BASE}/system/status").mock(
        return_value=Response(200, json={"appName": "Sonarr", "version": "4.0.0", "isDebug": False, "urlBase": ""})
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
    respx.get(f"{BASE}/series").mock(side_effect=Exception("connection refused"))
    with pytest.raises(Exception):
        await client.get_series()


@respx.mock
async def test_delete_series_dry_run():
    """sonarr_delete_series with confirm=False must not call the API."""
    from media_mcp.server import mcp  # noqa: F401 — ensure tools are registered

    # No HTTP mock registered — any call would raise
    from media_mcp.tools.sonarr_tools import register_sonarr_tools
    from mcp.server.fastmcp import FastMCP

    test_mcp = FastMCP("test")
    register_sonarr_tools(test_mcp)

    # Extract the tool function by iterating registered tools
    tools = {t.name: t for t in await test_mcp.get_tools()}
    delete_fn = tools["sonarr_delete_series"].fn

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
