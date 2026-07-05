import pytest
import respx
from httpx import Response
from mcp.server.fastmcp import FastMCP

from media_mcp.clients.prowlarr import ProwlarrClient
from media_mcp.tools.prowlarr_tools import register_prowlarr_tools

BASE = "https://prowlarr.test/api/v1"
API_KEY = "test-prowlarr-key"


@pytest.fixture
def client():
    return ProwlarrClient("https://prowlarr.test", API_KEY)


def _prowlarr_tool(name, monkeypatch):
    monkeypatch.setattr(
        "media_mcp.tools.prowlarr_tools._client",
        lambda: ProwlarrClient("https://prowlarr.test", API_KEY),
    )
    m = FastMCP("test")
    register_prowlarr_tools(m)
    return m._tool_manager.get_tool(name).fn


def _indexer(iid, name, enable=True, protocol="torrent", privacy="private", tags=None):
    return {
        "id": iid,
        "name": name,
        "enable": enable,
        "protocol": protocol,
        "privacy": privacy,
        "tags": tags or [],
        "capabilities": {"categories": [{"id": 2000, "name": "Movies", "subCategories": []}]},
    }


# ── base path / auth ──────────────────────────────────────────────────────────


@respx.mock
async def test_uses_v1_base_path_and_api_key(client):
    route = respx.get(f"{BASE}/system/status").mock(
        return_value=Response(200, json={"appName": "Prowlarr", "version": "2.4.0"})
    )
    await client.system_status()
    req = route.calls.last.request
    assert req.headers["x-api-key"] == API_KEY
    assert "/api/v1/system/status" in req.url.path
    assert "/api/v3/" not in req.url.path


# ── list_indexers ─────────────────────────────────────────────────────────────


@respx.mock
async def test_list_indexers_summary(monkeypatch):
    payload = [
        _indexer(2, "Zeta", enable=True, protocol="torrent"),
        _indexer(1, "Alpha", enable=False, protocol="usenet"),
    ]
    respx.get(f"{BASE}/indexer").mock(return_value=Response(200, json=payload))

    fn = _prowlarr_tool("prowlarr_list_indexers", monkeypatch)
    result = await fn()

    # Sorted by name -> Alpha before Zeta.
    assert result.index("Alpha") < result.index("Zeta")
    assert "✗ Alpha  (usenet/private)" in result
    assert "✓ Zeta  (torrent/private)" in result
    assert "Movies" in result


@respx.mock
async def test_list_indexers_empty(monkeypatch):
    respx.get(f"{BASE}/indexer").mock(return_value=Response(200, json=[]))
    fn = _prowlarr_tool("prowlarr_list_indexers", monkeypatch)
    assert "no indexers" in (await fn()).lower()


# ── indexer_status ────────────────────────────────────────────────────────────


@respx.mock
async def test_indexer_status_failing(monkeypatch):
    statuses = [
        {
            "id": 5,
            "indexerId": 2,
            "disabledTill": "2026-07-06T10:00:00Z",
            "initialFailure": "2026-07-05T08:00:00Z",
            "mostRecentFailure": "2026-07-05T20:00:00Z",
        }
    ]
    respx.get(f"{BASE}/indexerstatus").mock(return_value=Response(200, json=statuses))
    respx.get(f"{BASE}/indexer").mock(
        return_value=Response(200, json=[_indexer(2, "Torr9")])
    )

    fn = _prowlarr_tool("prowlarr_indexer_status", monkeypatch)
    result = await fn()

    assert "Torr9" in result
    assert "disabled till 2026-07-06T10:00:00Z" in result
    assert "1" in result  # count


@respx.mock
async def test_indexer_status_all_healthy(monkeypatch):
    respx.get(f"{BASE}/indexerstatus").mock(return_value=Response(200, json=[]))
    respx.get(f"{BASE}/indexer").mock(return_value=Response(200, json=[]))

    fn = _prowlarr_tool("prowlarr_indexer_status", monkeypatch)
    result = await fn()

    assert "all indexers healthy" in result.lower()


# ── health ────────────────────────────────────────────────────────────────────


@respx.mock
async def test_health_with_and_without(monkeypatch):
    respx.get(f"{BASE}/health").mock(
        return_value=Response(
            200,
            json=[{"type": "warning", "source": "IndexerStatusCheck", "message": "X down"}],
        )
    )
    fn = _prowlarr_tool("prowlarr_health", monkeypatch)
    result = await fn()
    assert "[warning] IndexerStatusCheck: X down" in result


@respx.mock
async def test_health_empty(monkeypatch):
    respx.get(f"{BASE}/health").mock(return_value=Response(200, json=[]))
    fn = _prowlarr_tool("prowlarr_health", monkeypatch)
    assert "no health issues" in (await fn()).lower()


# ── test_indexer (single) ─────────────────────────────────────────────────────


@respx.mock
async def test_test_indexer_pass(monkeypatch):
    respx.get(f"{BASE}/indexer/1").mock(return_value=Response(200, json=_indexer(1, "C411")))
    respx.post(f"{BASE}/indexer/test").mock(return_value=Response(200, json={}))

    fn = _prowlarr_tool("prowlarr_test_indexer", monkeypatch)
    result = await fn(indexer_id=1)

    assert result.startswith("PASS")
    assert "C411" in result


@respx.mock
async def test_test_indexer_fail(monkeypatch):
    respx.get(f"{BASE}/indexer/1").mock(return_value=Response(200, json=_indexer(1, "C411")))
    respx.post(f"{BASE}/indexer/test").mock(
        return_value=Response(
            400,
            json=[{"propertyName": "", "errorMessage": "Unable to connect to indexer"}],
        )
    )

    fn = _prowlarr_tool("prowlarr_test_indexer", monkeypatch)
    result = await fn(indexer_id=1)

    assert result.startswith("FAIL")
    assert "Unable to connect to indexer" in result


@respx.mock
async def test_test_indexer_unknown_id(monkeypatch):
    respx.get(f"{BASE}/indexer/99").mock(return_value=Response(404, text="Not Found"))
    post = respx.post(f"{BASE}/indexer/test").mock(return_value=Response(200, json={}))

    fn = _prowlarr_tool("prowlarr_test_indexer", monkeypatch)
    result = await fn(indexer_id=99)

    assert "indexer not found" in result.lower()
    assert not post.called


# ── test_all_indexers ─────────────────────────────────────────────────────────


@respx.mock
async def test_test_all_indexers_mixed(monkeypatch):
    respx.post(f"{BASE}/indexer/testall").mock(
        return_value=Response(
            200,
            json=[
                {"id": 1, "isValid": True, "validationFailures": []},
                {
                    "id": 2,
                    "isValid": False,
                    "validationFailures": [{"errorMessage": "Auth failed"}],
                },
            ],
        )
    )
    respx.get(f"{BASE}/indexer").mock(
        return_value=Response(200, json=[_indexer(1, "C411"), _indexer(2, "Torr9")])
    )

    fn = _prowlarr_tool("prowlarr_test_all_indexers", monkeypatch)
    result = await fn()

    assert "1 passed, 1 failed" in result
    assert "✗ [2] Torr9 — Auth failed" in result
    assert "✓ [1] C411" in result


# ── search ────────────────────────────────────────────────────────────────────


def _release(title, indexer, indexer_id, seeders, guid, size=1_073_741_824):
    return {
        "title": title, "indexer": indexer, "indexerId": indexer_id,
        "size": size, "seeders": seeders, "leechers": 2, "protocol": "torrent",
        "guid": guid, "age": 10,
        "categories": [{"id": 7020, "name": "Books/EBook", "subCategories": []}],
    }


@respx.mock
async def test_search_sorts_by_seeders_and_shows_grab_ref(monkeypatch):
    payload = [
        _release("Low Seed Release", "C411", 1, 5, "guid-low"),
        _release("High Seed Release", "Torr9", 2, 99, "guid-high"),
    ]
    route = respx.get(f"{BASE}/search").mock(return_value=Response(200, json=payload))

    fn = _prowlarr_tool("prowlarr_search", monkeypatch)
    result = await fn(query="dune")

    # X-Api-Key on the search request.
    assert route.calls.last.request.headers["x-api-key"] == API_KEY
    # Sorted by seeders desc -> High before Low.
    assert result.index("High Seed Release") < result.index("Low Seed Release")
    # Grab reference present for each.
    assert "guid=guid-high  indexerId=2" in result
    assert "guid=guid-low  indexerId=1" in result
    assert "Books/EBook" in result


@respx.mock
async def test_search_empty_query_no_call(monkeypatch):
    route = respx.get(f"{BASE}/search").mock(return_value=Response(200, json=[]))
    fn = _prowlarr_tool("prowlarr_search", monkeypatch)
    result = await fn(query="   ")
    assert not route.called
    assert "empty" in result.lower()


@respx.mock
async def test_search_zero_results(monkeypatch):
    respx.get(f"{BASE}/search").mock(return_value=Response(200, json=[]))
    fn = _prowlarr_tool("prowlarr_search", monkeypatch)
    result = await fn(query="nothingmatches")
    assert "no results" in result.lower()


# ── grab ──────────────────────────────────────────────────────────────────────


def _dlclient(enable=True):
    return [{"id": 1, "name": "qBittorrent", "protocol": "torrent", "enable": enable}]


@respx.mock
async def test_grab_dry_run_shows_destination_no_grab(monkeypatch):
    respx.get(f"{BASE}/downloadclient").mock(return_value=Response(200, json=_dlclient()))
    grab = respx.post(f"{BASE}/search").mock(return_value=Response(200, json={}))

    fn = _prowlarr_tool("prowlarr_grab", monkeypatch)
    result = await fn(guid="g1", indexer_id=2, confirm=False)

    assert not grab.called
    assert "DRY-RUN" in result
    assert "qBittorrent" in result
    assert "Mapped Categories" in result


@respx.mock
async def test_grab_confirm_posts_guid_and_indexer(monkeypatch):
    respx.get(f"{BASE}/downloadclient").mock(return_value=Response(200, json=_dlclient()))
    grab = respx.post(f"{BASE}/search").mock(return_value=Response(200, json={}))

    fn = _prowlarr_tool("prowlarr_grab", monkeypatch)
    result = await fn(guid="g1", indexer_id=2, confirm=True)

    assert grab.called
    import json as _json
    body = _json.loads(grab.calls.last.request.content)
    assert body == {"guid": "g1", "indexerId": 2}
    assert "Grabbed release" in result


@respx.mock
async def test_grab_no_download_client(monkeypatch):
    respx.get(f"{BASE}/downloadclient").mock(return_value=Response(200, json=[]))
    grab = respx.post(f"{BASE}/search").mock(return_value=Response(200, json={}))

    fn = _prowlarr_tool("prowlarr_grab", monkeypatch)
    result = await fn(guid="g1", indexer_id=2, confirm=True)

    assert not grab.called
    assert "no download client" in result.lower()


@respx.mock
async def test_grab_invalid_guid(monkeypatch):
    respx.get(f"{BASE}/downloadclient").mock(return_value=Response(200, json=_dlclient()))
    respx.post(f"{BASE}/search").mock(
        return_value=Response(400, json=[{"errorMessage": "Invalid or expired guid"}])
    )

    fn = _prowlarr_tool("prowlarr_grab", monkeypatch)
    result = await fn(guid="bad", indexer_id=2, confirm=True)

    assert result.startswith("FAIL")
    assert "Invalid or expired guid" in result
