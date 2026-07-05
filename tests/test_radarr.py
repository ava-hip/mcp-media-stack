import json

import pytest
import respx
from httpx import Response
from mcp.server.fastmcp import FastMCP

from media_mcp.clients.base import ArrClientError
from media_mcp.clients.radarr import RadarrClient
from media_mcp.tools.radarr_tools import register_radarr_tools

BASE = "http://radarr.test:7878/api/v3"

GIB = 1_073_741_824


@pytest.fixture
def client():
    return RadarrClient("http://radarr.test:7878", "test-api-key")


def _radarr_tool(name: str, monkeypatch):
    # Point the tool's client factory at the mocked test server.
    monkeypatch.setattr(
        "media_mcp.tools.radarr_tools._client",
        lambda: RadarrClient("http://radarr.test:7878", "test-api-key"),
    )
    test_mcp = FastMCP("test")
    register_radarr_tools(test_mcp)
    return test_mcp._tool_manager.get_tool(name).fn


def _movie(has_file: bool = True, monitored: bool = True) -> dict:
    movie = {
        "id": 10,
        "title": "Inception",
        "year": 2010,
        "monitored": monitored,
        "hasFile": has_file,
        "qualityProfileId": 1,
        "path": "/movies/Inception (2010)",
    }
    if has_file:
        movie["movieFile"] = {
            "id": 55,
            "relativePath": "Inception.2010.mkv",
            "size": 8 * GIB,
        }
    return movie


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


@respx.mock
async def test_set_movie_monitoring_puts_full_object(monkeypatch):
    respx.get(f"{BASE}/movie/10").mock(return_value=Response(200, json=_movie(monitored=False)))
    put_route = respx.put(f"{BASE}/movie/10").mock(return_value=Response(200, json=_movie()))

    fn = _radarr_tool("radarr_set_movie_monitoring", monkeypatch)
    result = await fn(movie_id=10, monitored=True)

    assert put_route.called
    body = json.loads(put_route.calls.last.request.content)
    assert body["monitored"] is True
    # Rest of the object preserved.
    assert body["title"] == "Inception"
    assert body["qualityProfileId"] == 1
    assert body["path"] == "/movies/Inception (2010)"
    assert "is now monitored" in result.lower()


@respx.mock
async def test_set_movie_monitoring_not_found(monkeypatch):
    respx.get(f"{BASE}/movie/99").mock(return_value=Response(404, text="Not Found"))

    fn = _radarr_tool("radarr_set_movie_monitoring", monkeypatch)
    result = await fn(movie_id=99, monitored=True)

    assert "movie not found" in result.lower()


@respx.mock
async def test_search_movie_dry_run_does_not_post(monkeypatch):
    respx.get(f"{BASE}/movie/10").mock(return_value=Response(200, json=_movie()))
    command_route = respx.post(f"{BASE}/command").mock(return_value=Response(201, json={}))

    fn = _radarr_tool("radarr_search_movie", monkeypatch)
    result = await fn(movie_id=10, confirm=False)

    assert not command_route.called
    assert "DRY-RUN" in result
    assert "Inception" in result


@respx.mock
async def test_search_movie_confirm_posts_command(monkeypatch):
    respx.get(f"{BASE}/movie/10").mock(return_value=Response(200, json=_movie()))
    command_route = respx.post(f"{BASE}/command").mock(return_value=Response(201, json={}))

    fn = _radarr_tool("radarr_search_movie", monkeypatch)
    result = await fn(movie_id=10, confirm=True)

    assert command_route.called
    body = json.loads(command_route.calls.last.request.content)
    assert body == {"name": "MoviesSearch", "movieIds": [10]}
    assert "launched" in result.lower()


@respx.mock
async def test_search_movie_unmonitored_is_flagged(monkeypatch):
    respx.get(f"{BASE}/movie/10").mock(return_value=Response(200, json=_movie(monitored=False)))
    respx.post(f"{BASE}/command").mock(return_value=Response(201, json={}))

    fn = _radarr_tool("radarr_search_movie", monkeypatch)
    result = await fn(movie_id=10, confirm=True)

    assert "not monitored" in result.lower()


@respx.mock
async def test_delete_movie_file_dry_run(monkeypatch):
    respx.get(f"{BASE}/movie/10").mock(return_value=Response(200, json=_movie()))
    delete_route = respx.delete(f"{BASE}/moviefile/55").mock(return_value=Response(200, json={}))

    fn = _radarr_tool("radarr_delete_movie_file", monkeypatch)
    result = await fn(movie_id=10, confirm=False)

    assert not delete_route.called
    assert "DRY-RUN" in result
    assert "Inception.2010.mkv" in result
    assert "8.0 GB" in result
    assert "hardlinked" in result.lower()


@respx.mock
async def test_delete_movie_file_confirm(monkeypatch):
    respx.get(f"{BASE}/movie/10").mock(return_value=Response(200, json=_movie()))
    delete_route = respx.delete(f"{BASE}/moviefile/55").mock(return_value=Response(200, json={}))

    fn = _radarr_tool("radarr_delete_movie_file", monkeypatch)
    result = await fn(movie_id=10, confirm=True)

    assert delete_route.called
    assert "8.0 GB" in result
    assert "still tracked" in result.lower()


@respx.mock
async def test_delete_movie_file_no_file(monkeypatch):
    respx.get(f"{BASE}/movie/10").mock(return_value=Response(200, json=_movie(has_file=False)))
    delete_route = respx.delete(url__regex=rf"{BASE}/moviefile/\d+").mock(
        return_value=Response(200, json={})
    )

    fn = _radarr_tool("radarr_delete_movie_file", monkeypatch)
    result = await fn(movie_id=10, confirm=True)

    assert not delete_route.called
    assert "no file found" in result.lower()


@respx.mock
async def test_upcoming_summary(monkeypatch):
    payload = [
        {
            "title": "Dune",
            "year": 2021,
            "monitored": True,
            "hasFile": False,
            "inCinemas": "2021-09-15T00:00:00Z",
            "digitalRelease": "2021-10-22T00:00:00Z",
        },
        {
            "title": "Tenet",
            "year": 2020,
            "monitored": False,
            "hasFile": True,
            "physicalRelease": "2020-12-15T00:00:00Z",
        },
    ]
    respx.get(f"{BASE}/calendar").mock(return_value=Response(200, json=payload))

    fn = _radarr_tool("radarr_upcoming", monkeypatch)
    result = await fn(days=30)

    # Dune: digital takes priority over cinema; no file.
    assert "2021-10-22 (digital)  Dune (2021)  mon=yes" in result
    # Tenet: physical release; has file marked with ✓.
    assert "2020-12-15 (physical)  Tenet (2020)  mon=no" in result
    assert "[✓]" in result
