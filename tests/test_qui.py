import json

import pytest
import respx
from httpx import Response
from mcp.server.fastmcp import FastMCP

from media_mcp.clients.qui import QuiClient, QuiClientError
from media_mcp.tools.qbit_tools import register_qbit_tools

BASE = "https://qui.test/api"
GIB = 1_073_741_824
API_KEY = "test-qui-key"


@pytest.fixture
def client():
    return QuiClient("https://qui.test", API_KEY)


def _instance(iid: int, name: str, connected: bool = True) -> dict:
    return {"id": iid, "name": name, "host": f"http://host{iid}:8080", "connected": connected}


def _torrent(h: str, name: str, **over) -> dict:
    base = {
        "hash": h,
        "name": name,
        "state": "downloading",
        "progress": 0.5,
        "size": 2 * GIB,
        "ratio": 1.25,
        "category": "tv-sonarr",
    }
    base.update(over)
    return base


def _qbit_tool(name: str, monkeypatch, instance: str = ""):
    # Point the tool's client factory at the mocked test server.
    monkeypatch.setattr(
        "media_mcp.tools.qbit_tools._client",
        lambda: QuiClient("https://qui.test", API_KEY, instance),
    )
    test_mcp = FastMCP("test")
    register_qbit_tools(test_mcp)
    return test_mcp._tool_manager.get_tool(name).fn


# ── Instance resolution ───────────────────────────────────────────────────────


@respx.mock
async def test_resolve_single_instance_auto(client):
    respx.get(f"{BASE}/instances").mock(
        return_value=Response(200, json=[_instance(1, "qbit")])
    )
    assert await client.resolve_instance_id() == 1


@respx.mock
async def test_resolve_multiple_without_instance_errors(client):
    respx.get(f"{BASE}/instances").mock(
        return_value=Response(200, json=[_instance(1, "qbit"), _instance(2, "seedbox")])
    )
    with pytest.raises(QuiClientError, match="Multiple qui instances"):
        await client.resolve_instance_id()


@respx.mock
async def test_resolve_multiple_lists_instances(client):
    respx.get(f"{BASE}/instances").mock(
        return_value=Response(200, json=[_instance(1, "qbit"), _instance(2, "seedbox")])
    )
    try:
        await client.resolve_instance_id()
        raise AssertionError("expected QuiClientError")
    except QuiClientError as e:
        assert "1:qbit" in str(e)
        assert "2:seedbox" in str(e)


@respx.mock
async def test_resolve_by_name_case_insensitive():
    c = QuiClient("https://qui.test", API_KEY, instance="SEEDBOX")
    respx.get(f"{BASE}/instances").mock(
        return_value=Response(200, json=[_instance(1, "qbit"), _instance(2, "seedbox")])
    )
    assert await c.resolve_instance_id() == 2


@respx.mock
async def test_resolve_by_id():
    c = QuiClient("https://qui.test", API_KEY, instance="2")
    respx.get(f"{BASE}/instances").mock(
        return_value=Response(200, json=[_instance(1, "qbit"), _instance(2, "seedbox")])
    )
    assert await c.resolve_instance_id() == 2


@respx.mock
async def test_resolve_unknown_instance_errors():
    c = QuiClient("https://qui.test", API_KEY, instance="nope")
    respx.get(f"{BASE}/instances").mock(
        return_value=Response(200, json=[_instance(1, "qbit")])
    )
    with pytest.raises(QuiClientError, match="not found"):
        await c.resolve_instance_id()


def test_not_configured_raises():
    async def run():
        c = QuiClient("", "")
        with pytest.raises(QuiClientError, match="not configured"):
            await c.list_instances()

    import asyncio

    asyncio.run(run())


# ── Auth header ───────────────────────────────────────────────────────────────


@respx.mock
async def test_api_key_header_sent(client):
    route = respx.get(f"{BASE}/instances").mock(
        return_value=Response(200, json=[_instance(1, "qbit")])
    )
    await client.list_instances()
    assert route.calls.last.request.headers["x-api-key"] == API_KEY


# ── list_torrents ─────────────────────────────────────────────────────────────


@respx.mock
async def test_list_torrents_summary(monkeypatch):
    respx.get(f"{BASE}/instances").mock(
        return_value=Response(200, json=[_instance(1, "qbit")])
    )
    payload = {
        "torrents": [
            _torrent("a" * 40, "Some.Show.S01", progress=0.5, size=2 * GIB, ratio=1.25),
        ],
        "total": 1,
    }
    respx.get(f"{BASE}/instances/1/torrents").mock(return_value=Response(200, json=payload))

    fn = _qbit_tool("qbit_list_torrents", monkeypatch)
    result = await fn()

    assert "Some.Show.S01" in result
    # The FULL 40-char hash must be shown so it is copyable into the other tools.
    assert "a" * 40 in result
    assert "50%" in result
    assert "2.0 GB" in result
    assert "ratio=1.25" in result
    assert "tv-sonarr" in result


@respx.mock
async def test_list_torrents_empty(monkeypatch):
    respx.get(f"{BASE}/instances").mock(
        return_value=Response(200, json=[_instance(1, "qbit")])
    )
    respx.get(f"{BASE}/instances/1/torrents").mock(
        return_value=Response(200, json={"torrents": [], "total": 0})
    )

    fn = _qbit_tool("qbit_list_torrents", monkeypatch)
    result = await fn(filter="nope")

    assert "no torrents found" in result.lower()
    assert "nope" in result


@respx.mock
async def test_list_torrents_null_torrents_field(monkeypatch):
    # qui returns "torrents": null (not []) when a filter matches nothing.
    respx.get(f"{BASE}/instances").mock(
        return_value=Response(200, json=[_instance(1, "qbit")])
    )
    respx.get(f"{BASE}/instances/1/torrents").mock(
        return_value=Response(200, json={"torrents": None, "total": None})
    )

    fn = _qbit_tool("qbit_list_torrents", monkeypatch)
    # Must NOT raise "'NoneType' object is not iterable".
    result = await fn(filter="nomatch")

    assert "no torrents found" in result.lower()
    assert "nomatch" in result


@respx.mock
async def test_list_torrents_http_error_still_surfaces(monkeypatch):
    # The or-[] normalization must not swallow real errors.
    respx.get(f"{BASE}/instances").mock(
        return_value=Response(200, json=[_instance(1, "qbit")])
    )
    respx.get(f"{BASE}/instances/1/torrents").mock(return_value=Response(401, text="nope"))

    fn = _qbit_tool("qbit_list_torrents", monkeypatch)
    result = await fn()

    assert result.lower().startswith("error")


# ── resolve_torrent (full hash + prefix) ──────────────────────────────────────


def _mock_instances(client_instances=None):
    respx.get(f"{BASE}/instances").mock(
        return_value=Response(200, json=client_instances or [_instance(1, "qbit")])
    )


@respx.mock
async def test_resolve_torrent_full_hash_exact(client):
    _mock_instances()
    h = "fd3ce77804afd45e34f4412d113b03427d0a39a6"
    respx.get(f"{BASE}/instances/1/torrents").mock(
        return_value=Response(200, json={"torrents": [_torrent(h, "Exact")], "total": 1})
    )
    # Upper-cased full hash (as an *arr downloadId would be) still resolves.
    raw = await client.resolve_torrent(1, h.upper())
    assert raw["hash"] == h


@respx.mock
async def test_resolve_torrent_unique_prefix(client):
    _mock_instances()
    h = "fd3ce77804afd45e34f4412d113b03427d0a39a6"
    respx.get(f"{BASE}/instances/1/torrents").mock(
        return_value=Response(200, json={"torrents": [_torrent(h, "OnlyOne")], "total": 1})
    )
    raw = await client.resolve_torrent(1, "fd3ce778")
    assert raw["hash"] == h  # prefix resolved to the full hash


@respx.mock
async def test_resolve_torrent_ambiguous_prefix_lists_candidates(client):
    _mock_instances()
    h1 = "fd3ce77800000000000000000000000000000001"
    h2 = "fd3ce77800000000000000000000000000000002"
    payload = {
        "torrents": [_torrent(h1, "Alpha"), _torrent(h2, "Beta")],
        "total": 2,
    }
    respx.get(f"{BASE}/instances/1/torrents").mock(return_value=Response(200, json=payload))
    action = respx.post(f"{BASE}/instances/1/torrents/bulk-action").mock(
        return_value=Response(200)
    )

    with pytest.raises(QuiClientError, match="Ambiguous hash prefix") as exc:
        await client.resolve_torrent(1, "fd3ce778")

    msg = str(exc.value)
    assert h1 in msg and h2 in msg
    assert "Alpha" in msg and "Beta" in msg
    assert not action.called  # never guesses / acts


@respx.mock
async def test_resolve_torrent_unknown(client):
    _mock_instances()
    respx.get(f"{BASE}/instances/1/torrents").mock(
        return_value=Response(200, json={"torrents": [], "total": 0})
    )
    with pytest.raises(QuiClientError, match="No torrent found with hash 'zzz'"):
        await client.resolve_torrent(1, "zzz")


@respx.mock
async def test_resolve_torrent_null_torrents_field(client):
    # "torrents": null must be treated as no match, not crash.
    _mock_instances()
    respx.get(f"{BASE}/instances/1/torrents").mock(
        return_value=Response(200, json={"torrents": None})
    )
    with pytest.raises(QuiClientError, match="No torrent found with hash 'abc'"):
        await client.resolve_torrent(1, "abc")


@respx.mock
async def test_local_matches_null_treated_as_empty(client):
    respx.get(f"{BASE}/cross-seed/torrents/1/deadbeef/local-matches").mock(
        return_value=Response(200, json={"matches": None})
    )
    matches = await client.local_matches(1, "deadbeef")
    assert matches == []


@respx.mock
async def test_delete_via_prefix_confirm(monkeypatch):
    h = "abcdef0000000000000000000000000000000000"
    respx.get(f"{BASE}/instances").mock(
        return_value=Response(200, json=[_instance(1, "qbit")])
    )
    payload = {"torrents": [_torrent(h, "PrefixDel", size=3 * GIB)], "total": 1}
    respx.get(f"{BASE}/instances/1/torrents").mock(return_value=Response(200, json=payload))
    action_route = respx.post(f"{BASE}/instances/1/torrents/bulk-action").mock(
        return_value=Response(200)
    )

    fn = _qbit_tool("qbit_delete_torrent", monkeypatch)
    result = await fn(hash="abcdef", delete_files=True, confirm=True)

    assert action_route.called
    body = json.loads(action_route.calls.last.request.content)
    # The FULL resolved hash is sent, not the prefix.
    assert body["hashes"] == [h]
    assert body["deleteFiles"] is True
    assert h in result


# ── get_torrent tool ──────────────────────────────────────────────────────────


@respx.mock
async def test_get_torrent_found_case_insensitive(monkeypatch):
    h = "fd3ce77804afd45e34f4412d113b03427d0a39a6"
    respx.get(f"{BASE}/instances").mock(
        return_value=Response(200, json=[_instance(1, "qbit")])
    )
    respx.get(f"{BASE}/instances/1/torrents").mock(
        return_value=Response(200, json={"torrents": [_torrent(h, "Movie.2021")], "total": 1})
    )

    fn = _qbit_tool("qbit_get_torrent", monkeypatch)
    # Query with an upper-cased hash (as Sonarr/Radarr downloadId would be).
    result = await fn(hash=h.upper())

    assert "Movie.2021" in result
    assert h in result


@respx.mock
async def test_get_torrent_not_found(monkeypatch):
    respx.get(f"{BASE}/instances").mock(
        return_value=Response(200, json=[_instance(1, "qbit")])
    )
    respx.get(f"{BASE}/instances/1/torrents").mock(
        return_value=Response(200, json={"torrents": [], "total": 0})
    )

    fn = _qbit_tool("qbit_get_torrent", monkeypatch)
    result = await fn(hash="deadbeef")

    assert "no torrent found" in result.lower()


# ── delete_torrent ────────────────────────────────────────────────────────────


def _mock_instance_and_torrent(h: str):
    respx.get(f"{BASE}/instances").mock(
        return_value=Response(200, json=[_instance(1, "qbit")])
    )
    payload = {"torrents": [_torrent(h, "Big.Torrent", size=5 * GIB)], "total": 1}
    respx.get(f"{BASE}/instances/1/torrents").mock(return_value=Response(200, json=payload))


@respx.mock
async def test_delete_dry_run_no_files(monkeypatch):
    h = "b" * 40
    _mock_instance_and_torrent(h)
    action_route = respx.post(f"{BASE}/instances/1/torrents/bulk-action").mock(
        return_value=Response(200)
    )

    fn = _qbit_tool("qbit_delete_torrent", monkeypatch)
    result = await fn(hash=h, delete_files=False, confirm=False)

    assert not action_route.called
    assert "DRY-RUN" in result
    assert "Big.Torrent" in result
    assert "5.0 GB" in result
    assert "removed from qBittorrent" in result
    assert "deleted from disk" not in result  # delete_files is False
    # No hardlink note when no files are deleted.
    assert "hardlinked" not in result.lower()
    assert "library copy remains" not in result


@respx.mock
async def test_delete_dry_run_with_files_shows_torrent_hardlink_note(monkeypatch):
    h = "c" * 40
    _mock_instance_and_torrent(h)
    action_route = respx.post(f"{BASE}/instances/1/torrents/bulk-action").mock(
        return_value=Response(200)
    )

    fn = _qbit_tool("qbit_delete_torrent", monkeypatch)
    result = await fn(hash=h, delete_files=True, confirm=False)

    assert not action_route.called
    assert "deleted from disk" in result
    # New qBit-perspective note, NOT the old library-perspective hardlink_note.
    assert "library copy remains" in result
    assert "BOTH" in result
    assert "removed from Sonarr only" not in result
    assert "removed from Radarr only" not in result


@respx.mock
async def test_delete_confirm_posts_bulk_action(monkeypatch):
    h = "d" * 40
    _mock_instance_and_torrent(h)
    action_route = respx.post(f"{BASE}/instances/1/torrents/bulk-action").mock(
        return_value=Response(200)
    )

    fn = _qbit_tool("qbit_delete_torrent", monkeypatch)
    result = await fn(hash=h, delete_files=True, confirm=True)

    assert action_route.called
    body = json.loads(action_route.calls.last.request.content)
    assert body["action"] == "delete"
    assert body["hashes"] == [h]
    assert body["deleteFiles"] is True
    assert "Removed torrent" in result


@respx.mock
async def test_delete_hash_not_found_no_action(monkeypatch):
    respx.get(f"{BASE}/instances").mock(
        return_value=Response(200, json=[_instance(1, "qbit")])
    )
    respx.get(f"{BASE}/instances/1/torrents").mock(
        return_value=Response(200, json={"torrents": [], "total": 0})
    )
    action_route = respx.post(f"{BASE}/instances/1/torrents/bulk-action").mock(
        return_value=Response(200)
    )

    fn = _qbit_tool("qbit_delete_torrent", monkeypatch)
    result = await fn(hash="missing", confirm=True)

    assert not action_route.called
    assert "no torrent found" in result.lower()


# ── pause / resume ────────────────────────────────────────────────────────────


@respx.mock
async def test_pause_posts_pause_action(monkeypatch):
    h = "e" * 40
    _mock_instance_and_torrent(h)
    action_route = respx.post(f"{BASE}/instances/1/torrents/bulk-action").mock(
        return_value=Response(200)
    )

    fn = _qbit_tool("qbit_pause", monkeypatch)
    result = await fn(hash=h)

    assert action_route.called
    body = json.loads(action_route.calls.last.request.content)
    assert body["action"] == "pause"
    assert body["hashes"] == [h]
    assert "Paused" in result
