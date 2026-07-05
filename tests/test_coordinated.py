import json

import respx
from httpx import Response
from mcp.server.fastmcp import FastMCP

from media_mcp.clients.qui import QuiClient
from media_mcp.clients.radarr import RadarrClient
from media_mcp.clients.sonarr import SonarrClient
from media_mcp.coordinated import extract_download_ids
from media_mcp.tools.coordinated_tools import register_coordinated_tools

SONARR = "http://sonarr.test:8989/api/v3"
RADARR = "http://radarr.test:7878/api/v3"
QUI = "https://qui.test/api"
GIB = 1_073_741_824

ORIGIN = "df30bf6172f77daa070965ba00eebf768a1914b1"
SIB_CP = "cc" * 20  # content_path sibling
SIB_NAME = "bb" * 20  # name sibling (loose)


def _tool(name, monkeypatch):
    monkeypatch.setattr(
        "media_mcp.tools.coordinated_tools._sonarr",
        lambda: SonarrClient("http://sonarr.test:8989", "k"),
    )
    monkeypatch.setattr(
        "media_mcp.tools.coordinated_tools._radarr",
        lambda: RadarrClient("http://radarr.test:7878", "k"),
    )
    monkeypatch.setattr(
        "media_mcp.tools.coordinated_tools._qui",
        lambda: QuiClient("https://qui.test", "qk", ""),
    )
    m = FastMCP("test")
    register_coordinated_tools(m)
    return m._tool_manager.get_tool(name).fn


def _torrent(h, name, **over):
    d = {
        "hash": h,
        "name": name,
        "state": "stalledUP",
        "progress": 1,
        "size": 20 * GIB,
        "ratio": 0.0,
        "category": "tv-sonarr",
    }
    d.update(over)
    return d


def _match(h, name, match_type, **over):
    d = {
        "hash": h,
        "name": name,
        "size": 20 * GIB,
        "category": "tv-sonarr.cross",
        "match_type": match_type,
        "content_path": f"/data/{h}",
        "save_path": "/data",
        "state": "stalledUP",
        "tags": "cross-seed",
    }
    d.update(over)
    return d


def _ep(fid, season, size=GIB):
    return {
        "id": fid,
        "seriesId": 44,
        "seasonNumber": season,
        "relativePath": f"S{season:02d}/e{fid}.mkv",
        "size": size,
    }


def _season_pack_history(download_id=ORIGIN.upper(), n=3):
    # A season pack shares ONE downloadId across all episode events.
    events = []
    for i in range(n):
        events.append(
            {"eventType": "grabbed", "downloadId": download_id, "sourceTitle": f"E{i}"}
        )
        events.append(
            {
                "eventType": "downloadFolderImported",
                "downloadId": download_id,
                "sourceTitle": f"E{i}",
            }
        )
    return events


def _mock_sonarr_series():
    respx.get(f"{SONARR}/series/44").mock(
        return_value=Response(200, json={"id": 44, "title": "Grey's Anatomy", "seasons": []})
    )
    respx.get(f"{SONARR}/episodefile").mock(
        return_value=Response(
            200, json=[_ep(101, 11), _ep(102, 11), _ep(103, 11), _ep(200, 10)]
        )
    )


def _mock_qui_origin_and_siblings(matches):
    respx.get(f"{QUI}/instances").mock(return_value=Response(200, json=[{"id": 1, "name": "qbit"}]))
    respx.get(f"{QUI}/instances/1/torrents").mock(
        return_value=Response(200, json={"torrents": [_torrent(ORIGIN, "Greys.S11")], "total": 1})
    )
    respx.get(f"{QUI}/cross-seed/torrents/1/{ORIGIN}/local-matches").mock(
        return_value=Response(200, json={"matches": matches})
    )


# ── pure helper ───────────────────────────────────────────────────────────────


def test_extract_download_ids_dedups_season_pack():
    ids = extract_download_ids(_season_pack_history(n=25))
    assert ids == [ORIGIN.upper()]  # 50 events, one shared downloadId


def test_extract_download_ids_skips_missing():
    events = [
        {"eventType": "downloadFolderImported", "downloadId": None},
        {"eventType": "grabbed", "downloadId": "ABC"},
        {"eventType": "grabbed", "downloadId": "abc"},  # case-insensitive dup
    ]
    assert extract_download_ids(events) == ["ABC"]


# ── dry-run ───────────────────────────────────────────────────────────────────


@respx.mock
async def test_purge_season_dry_run_lists_both_sides_no_delete(monkeypatch):
    _mock_sonarr_series()
    respx.get(f"{SONARR}/history/series").mock(
        return_value=Response(200, json=_season_pack_history())
    )
    _mock_qui_origin_and_siblings([_match(SIB_CP, "Greys.S11.cross", "content_path")])
    ep_delete = respx.delete(url__regex=rf"{SONARR}/episodefile/\d+").mock(
        return_value=Response(200, json={})
    )
    bulk = respx.post(f"{QUI}/instances/1/torrents/bulk-action").mock(return_value=Response(200))

    fn = _tool("sonarr_purge_season", monkeypatch)
    result = await fn(series_id=44, season_number=11, confirm=False)

    # No destructive calls in a dry-run.
    assert not ep_delete.called
    assert not bulk.called
    assert "DRY-RUN" in result
    # Library side: 3 season-11 files (not the season-10 one).
    assert "3 file(s)" in result
    # qBit side: origin + sibling listed with full hashes.
    assert ORIGIN in result
    assert SIB_CP in result
    assert "origin" in result
    assert "sibling (match_type=content_path)" in result
    # Honesty: the disk note is present and sizes are not summed into one figure.
    assert "must NOT be added together" in result


@respx.mock
async def test_purge_season_include_loose_false_excludes_name_match(monkeypatch):
    _mock_sonarr_series()
    respx.get(f"{SONARR}/history/series").mock(
        return_value=Response(200, json=_season_pack_history())
    )
    _mock_qui_origin_and_siblings(
        [
            _match(SIB_CP, "cp", "content_path"),
            _match(SIB_NAME, "name", "name"),
        ]
    )

    fn = _tool("sonarr_purge_season", monkeypatch)
    loose = await fn(series_id=44, season_number=11, include_loose_matches=True, confirm=False)
    strict = await fn(series_id=44, season_number=11, include_loose_matches=False, confirm=False)

    # Loose: both siblings present.
    assert SIB_CP in loose and SIB_NAME in loose
    # Strict: name-matched sibling dropped and counted as excluded.
    assert SIB_NAME not in strict
    assert SIB_CP in strict
    assert "1 loose cross-seed match(es)" in strict


# ── confirm ───────────────────────────────────────────────────────────────────


@respx.mock
async def test_purge_season_confirm_order_and_single_bulk_action(monkeypatch):
    _mock_sonarr_series()
    respx.get(f"{SONARR}/history/series").mock(
        return_value=Response(200, json=_season_pack_history())
    )
    _mock_qui_origin_and_siblings(
        [_match(SIB_CP, "cp", "content_path"), _match(SIB_NAME, "nm", "name")]
    )
    ep_delete = respx.delete(url__regex=rf"{SONARR}/episodefile/\d+").mock(
        return_value=Response(200, json={})
    )
    bulk = respx.post(f"{QUI}/instances/1/torrents/bulk-action").mock(return_value=Response(200))

    fn = _tool("sonarr_purge_season", monkeypatch)
    result = await fn(
        series_id=44, season_number=11, delete_torrent_files=True, confirm=True
    )

    # Library deletions happened for the 3 season-11 files.
    deleted_ids = {int(c.request.url.path.rsplit("/", 1)[1]) for c in ep_delete.calls}
    assert deleted_ids == {101, 102, 103}

    # Exactly ONE bulk-action with ALL hashes and camelCase deleteFiles=true.
    assert bulk.call_count == 1
    body = json.loads(bulk.calls.last.request.content)
    assert body["action"] == "delete"
    assert set(body["hashes"]) == {ORIGIN, SIB_CP, SIB_NAME}
    assert body["deleteFiles"] is True

    # Order: files deleted BEFORE the bulk-action.
    paths = [c.request.url.path for c in respx.calls]
    assert paths.index("/api/instances/1/torrents/bulk-action") > max(
        i for i, p in enumerate(paths) if p.startswith("/api/v3/episodefile/")
    )
    assert "deleted 3/3" in result
    assert "removed 3 torrent(s) (with files)" in result


@respx.mock
async def test_purge_season_origin_missing_from_qbit(monkeypatch):
    _mock_sonarr_series()
    respx.get(f"{SONARR}/history/series").mock(
        return_value=Response(200, json=_season_pack_history())
    )
    respx.get(f"{QUI}/instances").mock(return_value=Response(200, json=[{"id": 1, "name": "qbit"}]))
    # Torrent list is empty -> resolve_torrent finds nothing -> origin missing.
    respx.get(f"{QUI}/instances/1/torrents").mock(
        return_value=Response(200, json={"torrents": [], "total": 0})
    )
    bulk = respx.post(f"{QUI}/instances/1/torrents/bulk-action").mock(return_value=Response(200))

    fn = _tool("sonarr_purge_season", monkeypatch)
    result = await fn(series_id=44, season_number=11, confirm=False)

    assert "no longer in qBittorrent" in result
    assert ORIGIN.upper() in result  # the missing origin id is surfaced
    assert not bulk.called


@respx.mock
async def test_purge_season_partial_qbit_failure_reported(monkeypatch):
    _mock_sonarr_series()
    respx.get(f"{SONARR}/history/series").mock(
        return_value=Response(200, json=_season_pack_history())
    )
    _mock_qui_origin_and_siblings([_match(SIB_CP, "cp", "content_path")])
    respx.delete(url__regex=rf"{SONARR}/episodefile/\d+").mock(return_value=Response(200, json={}))
    # bulk-action fails.
    respx.post(f"{QUI}/instances/1/torrents/bulk-action").mock(
        return_value=Response(500, text="boom")
    )

    fn = _tool("sonarr_purge_season", monkeypatch)
    result = await fn(series_id=44, season_number=11, confirm=True)

    # Library side succeeded, qBit side reported as failed — no crash.
    assert "deleted 3/3" in result
    assert "FAILED to remove torrents" in result


@respx.mock
async def test_purge_season_no_download_ids(monkeypatch):
    _mock_sonarr_series()
    # History has no downloadId (manually imported season).
    respx.get(f"{SONARR}/history/series").mock(
        return_value=Response(
            200, json=[{"eventType": "downloadFolderImported", "downloadId": None}]
        )
    )
    instances = respx.get(f"{QUI}/instances").mock(
        return_value=Response(200, json=[{"id": 1, "name": "qbit"}])
    )

    fn = _tool("sonarr_purge_season", monkeypatch)
    result = await fn(series_id=44, season_number=11, confirm=False)

    assert "no downloadid" in result.lower()
    # qui is not even contacted when there are no hashes to resolve.
    assert not instances.called


# ── Radarr smoke ──────────────────────────────────────────────────────────────


@respx.mock
async def test_purge_movie_smoke(monkeypatch):
    mid = 51
    mhash = "4b192e260a519d18d9edecec130052a67593114f"
    respx.get(f"{RADARR}/movie/{mid}").mock(
        return_value=Response(
            200,
            json={
                "id": mid,
                "title": "Pacifiction",
                "year": 2022,
                "hasFile": True,
                "movieFile": {"id": 77, "relativePath": "Pacifiction.mkv", "size": 8 * GIB},
            },
        )
    )
    respx.get(f"{RADARR}/history/movie").mock(
        return_value=Response(
            200,
            json=[{"eventType": "grabbed", "downloadId": mhash.upper(), "sourceTitle": "Pac"}],
        )
    )
    respx.get(f"{QUI}/instances").mock(return_value=Response(200, json=[{"id": 1, "name": "qbit"}]))
    respx.get(f"{QUI}/instances/1/torrents").mock(
        return_value=Response(200, json={"torrents": [_torrent(mhash, "Pacifiction")], "total": 1})
    )
    respx.get(f"{QUI}/cross-seed/torrents/1/{mhash}/local-matches").mock(
        return_value=Response(200, json={"matches": [_match(SIB_CP, "Pac.cross", "content_path")]})
    )
    mfile_delete = respx.delete(f"{RADARR}/moviefile/77").mock(return_value=Response(200, json={}))
    bulk = respx.post(f"{QUI}/instances/1/torrents/bulk-action").mock(return_value=Response(200))

    fn = _tool("radarr_purge_movie", monkeypatch)
    result = await fn(movie_id=mid, delete_torrent_files=False, confirm=True)

    assert mfile_delete.called
    assert bulk.call_count == 1
    body = json.loads(bulk.calls.last.request.content)
    assert set(body["hashes"]) == {mhash, SIB_CP}
    assert body["deleteFiles"] is False
    assert "Pacifiction" in result
