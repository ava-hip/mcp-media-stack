import pytest
import respx
from httpx import Response
from mcp.server.fastmcp import FastMCP

from media_mcp.clients.base import ArrClientError
from media_mcp.clients.radarr import RadarrClient
from media_mcp.tools.radarr_tools import register_radarr_tools

BASE = "http://radarr.test:7878/api/v3"


@pytest.fixture
def client():
    return RadarrClient("http://radarr.test:7878", "test-api-key")


@respx.mock
async def test_system_status(client):
    respx.get(f"{BASE}/system/status").mock(
        return_value=Response(
            200, json={"appName": "Radarr", "version": "5.0.0", "isDebug": False, "urlBase": ""}
        )
    )
    data = await client.system_status()
    assert data["version"] == "5.0.0"


@respx.mock
async def test_get_movies(client):
    payload = [
        {
            "id": 10,
            "title": "Inception",
            "year": 2010,
            "status": "released",
            "monitored": True,
            "hasFile": True,
            "tmdbId": 27205,
        }
    ]
    respx.get(f"{BASE}/movie").mock(return_value=Response(200, json=payload))
    data = await client.get_movies()
    assert len(data) == 1
    assert data[0]["tmdbId"] == 27205


@respx.mock
async def test_delete_movie_dry_run():
    """radarr_delete_movie with confirm=False must not call the API."""
    test_mcp = FastMCP("test")
    register_radarr_tools(test_mcp)

    delete_fn = test_mcp._tool_manager.get_tool("radarr_delete_movie").fn

    result = await delete_fn(movie_id=10, delete_files=False, confirm=False)
    assert "DRY-RUN" in result
    assert "10" in result


@respx.mock
async def test_delete_movie_with_files_dry_run():
    test_mcp = FastMCP("test")
    register_radarr_tools(test_mcp)

    delete_fn = test_mcp._tool_manager.get_tool("radarr_delete_movie").fn

    result = await delete_fn(movie_id=10, delete_files=True, confirm=False)
    assert "DRY-RUN" in result
    assert "delete files" in result.lower()


@respx.mock
async def test_delete_movie_confirm(client):
    respx.delete(f"{BASE}/movie/10").mock(return_value=Response(200, json={}))
    await client.delete_movie(10, delete_files=False)


@respx.mock
async def test_lookup_movie(client):
    payload = [{"title": "Dune", "year": 2021, "tmdbId": 438631, "overview": "Epic sci-fi."}]
    respx.get(f"{BASE}/movie/lookup").mock(return_value=Response(200, json=payload))
    data = await client.lookup_movie("Dune")
    assert data[0]["tmdbId"] == 438631


@respx.mock
async def test_http_error_raises_arr_client_error(client):
    respx.get(f"{BASE}/movie").mock(return_value=Response(403, text="Forbidden"))
    with pytest.raises(ArrClientError, match="403"):
        await client.get_movies()


@respx.mock
async def test_quality_profiles(client):
    payload = [{"id": 1, "name": "HD-1080p"}, {"id": 4, "name": "Any"}]
    respx.get(f"{BASE}/qualityprofile").mock(return_value=Response(200, json=payload))
    data = await client.get_quality_profiles()
    assert len(data) == 2
    assert data[0]["name"] == "HD-1080p"
