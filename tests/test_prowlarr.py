import re
from datetime import UTC, datetime, timedelta

import pytest
import respx
from httpx import Response
from mcp.server.fastmcp import FastMCP

from media_mcp.clients.prowlarr import ProwlarrClient
from media_mcp.tools.prowlarr_tools import (
    _group_releases,
    _is_junk,
    _mask_secrets,
    _parse_release_name,
    register_prowlarr_tools,
)

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


# ── recent_releases: scene-name parsing ───────────────────────────────────────
# Every fixture below is a real release name observed on this instance in phase 2.


def test_parse_episode():
    p = _parse_release_name("House.of.the.Dragon.S03E07.MULTi.1080p.WEB.H264-SUPPLY")
    assert (p.kind, p.title, p.season, p.episode) == ("tv", "House of the Dragon", 3, 7)


def test_parse_movie_with_sequel_number_in_title():
    # The "2" is part of the title, not a year: only 4-digit tokens are year candidates.
    p = _parse_release_name(
        "The.Devil.Wears.Prada.2.2026.PROPER.MULTi.VF2.1080p.WEB.H264-SUPPLY"
    )
    assert (p.kind, p.title, p.year) == ("movie", "The Devil Wears Prada 2", 2026)
    assert p.season is None and p.episode is None


def test_parse_season_pack_has_no_episode():
    p = _parse_release_name("Silo.S01.PROPER.MULTI.VFF.1080p.WEB.EAC3.5.1.H265-FW")
    assert (p.kind, p.title, p.season, p.episode) == ("tv", "Silo", 1, None)


def test_parse_truncated_name_with_trailing_dot():
    p = _parse_release_name(
        "Goat.Rever.Plus.Haut.2026.MULTI.VF2.2160p.WEBRip.DV.HDR10Plus.AC3.5.1."
    )
    assert (p.kind, p.title, p.year) == ("movie", "Goat Rever Plus Haut", 2026)


def test_parse_folds_out_the_series_year():
    # Regression, caught live: C411 emits "Futurama.1999.S11E01" and The Old School emits
    # "Futurama.S11E01" for the same episode. Both must resolve to one work.
    c411 = _parse_release_name("Futurama.1999.S11E01.MULTI.VFF.1080p.WEB.DDP.5.1.H264-SUPPLY")
    tos = _parse_release_name("Futurama.S11E01.MULTi.1080p.WEB.H264-SUPPLY")
    assert c411.title == tos.title == "Futurama"
    assert (c411.season, c411.episode) == (11, 1)


def test_parse_keeps_a_year_that_is_the_whole_series_title():
    # "1923" is a real series; folding its year out would leave it nameless.
    p = _parse_release_name("1923.S02E01.MULTi.1080p.WEB.H264-XX")
    assert (p.title, p.season, p.episode) == ("1923", 2, 1)


def test_parse_keeps_an_out_of_range_number_in_a_series_title():
    p = _parse_release_name("Blade.Runner.2049.S01E01.MULTi.1080p.WEB.H264-XX")
    assert p.title == "Blade Runner 2049"


def test_parse_movie_no_year_is_rejected():
    # Matching a film on its title alone is too hazardous, so it is dropped outright.
    assert _parse_release_name("Some.Untitled.Thing.1080p.WEB.H264-NoTag") is None


def test_parse_year_upper_bound_rejects_title_number():
    # 2049 is beyond now+2, so it is not mistaken for a release year.
    assert _parse_release_name("Blade.Runner.2049.MULTi.1080p.WEB.H264-XXX") is None
    # ...but a real year after it wins, and the title keeps the number.
    p = _parse_release_name("Blade.Runner.2049.2017.MULTi.1080p.BluRay.x264-XXX")
    assert (p.title, p.year) == ("Blade Runner 2049", 2017)


# ── recent_releases: junk detection ───────────────────────────────────────────


@pytest.mark.parametrize(
    "title",
    [
        "Some.Movie.2026.TS.1080p.x264-XX",
        "Some.Movie.2026.HDCAM.x264-XX",
        "Some.Movie.2026.MULTi.TELESYNC.x264-XX",
        "Some.Movie.2026.DVDSCR.x264-XX",
        "Some.Movie.2026.R5.x264-XX",
    ],
)
def test_is_junk_detects_cinema_rips(title):
    assert _is_junk(title)


@pytest.mark.parametrize(
    "title",
    [
        "House.of.the.Dragon.S03E07.MULTi.1080p.WEB.H264-SUPPLY",
        "Ghosts.S05E01.MULTi.1080p.WEB.H264-XX",  # must NOT fire on "GHOSTS" containing TS
        "Cam.2018.MULTi.1080p.WEB.H264-XX",  # a film actually called "Cam"
    ],
)
def test_is_junk_ignores_clean_names(title):
    assert not _is_junk(title)


# ── recent_releases: grouping ─────────────────────────────────────────────────


def _grp(title, seeders, age, size=1_073_741_824, indexer="C411", tmdb=0, imdb=0):
    return (
        {
            "title": title, "seeders": seeders, "size": size, "indexer": indexer,
            "tmdbId": tmdb, "imdbId": imdb,
        },
        age,
    )


def test_group_never_sums_across_episodes_of_a_season():
    # The regression this guards: merging S03E03..E07 would invent a ~1800-seeder "S03"
    # group matching no real download.
    scored = [
        _grp(f"House.of.the.Dragon.S03E0{n}.MULTi.1080p.WEB.H264-SUPPLY", 300 + n, 5.0)
        for n in range(3, 8)
    ]
    groups = _group_releases(scored)
    assert len(groups) == 5
    assert max(g.seeders for g in groups) == 307
    assert all(g.release_count == 1 for g in groups)
    assert {g.label for g in groups} == {
        f"House of the Dragon S03E0{n}" for n in range(3, 8)
    }


def test_group_sums_seeders_of_the_same_episode_across_qualities():
    scored = [
        _grp("House.of.the.Dragon.S03E07.MULTi.1080p.WEB.H264-SUPPLY", 339, 17.3, tmdb=94997),
        _grp("House.of.the.Dragon.S03E07.MULTi.VFF.2160p.WEBRiP.DV.HDR.EAC3.5.1-XX", 61, 3.1),
    ]
    groups = _group_releases(scored)
    assert len(groups) == 1
    g = groups[0]
    assert g.seeders == 400  # summed: traction splits across qualities
    assert g.release_count == 2
    assert g.newest_age_hours == 3.1  # freshest member
    assert g.best_raw_title.endswith("H264-SUPPLY")  # exemplar = most seeded
    assert g.tmdb_id == 94997  # id picked up from whichever release carries one


def test_group_merges_the_same_episode_named_with_and_without_the_series_year():
    scored = [
        _grp("Futurama.1999.S11E01.MULTI.VFF.1080p.WEB.DDP.5.1.H264-SUPPLY", 498, 11.2),
        _grp("Futurama.S11E01.MULTi.1080p.WEB.H264-SUPPLY", 424, 8.2,
             indexer="The Old School (API)"),
    ]
    groups = _group_releases(scored)
    assert len(groups) == 1
    assert groups[0].label == "Futurama S11E01"
    assert groups[0].seeders == 922  # was 498 + 424 in two separate groups before the fix


def test_group_season_pack_is_distinct_from_its_episodes():
    scored = [
        _grp("Silo.S01.PROPER.MULTI.VFF.1080p.WEB.EAC3.5.1.H265-FW", 50, 2.0),
        _grp("Silo.S01E01.MULTi.1080p.WEB.H264-XX", 20, 2.0),
    ]
    groups = _group_releases(scored)
    assert len(groups) == 2
    labels = {g.label for g in groups}
    assert "Silo S01 (season pack)" in labels
    assert "Silo S01E01" in labels


def test_group_folds_case_and_punctuation_variants_of_one_film():
    scored = [
        _grp("Goat.Rever.Plus.Haut.2026.MULTI.VF2.2160p.WEBRip.DV.HDR10Plus.AC3.5.1.", 12, 4.0),
        _grp("GOAT.Rever.plus.haut.2026.MULTI.VFF.1080p.WEB.H264-XX", 30, 1.0),
    ]
    groups = _group_releases(scored)
    assert len(groups) == 1
    assert groups[0].seeders == 42
    assert groups[0].release_count == 2


def test_group_sorted_by_seeders_then_recency():
    scored = [
        _grp("Aaa.Film.2026.MULTi.1080p.WEB.H264-XX", 10, 1.0),
        _grp("Bbb.Film.2026.MULTi.1080p.WEB.H264-XX", 10, 0.5),  # same seeders, fresher
        _grp("Ccc.Film.2026.MULTi.1080p.WEB.H264-XX", 99, 50.0),
    ]
    labels = [g.label for g in _group_releases(scored)]
    assert labels == ["Ccc Film (2026)", "Bbb Film (2026)", "Aaa Film (2026)"]


def test_group_drops_unparsable_names():
    scored = [_grp("Totally.Unparsable.Thing.1080p-XX", 500, 1.0)]
    assert _group_releases(scored) == []


# ── recent_releases: secret masking ───────────────────────────────────────────


def test_mask_secrets_covers_every_credential_param():
    raw = (
        "https://c411.org/api?t=get&id=abc&apikey=7f8cf207d1cf595c5fa0fce82ffcc136 "
        "https://api.torr9.net/dl?passkey=2f0736e6147e8fcb54ae3f39b2d44a17 "
        "https://x/y?token=deadbeef&rss_key=aaa&authkey=bbb&api_key=ccc"
    )
    masked = _mask_secrets(raw)
    for param in ("apikey", "passkey", "token", "rss_key", "authkey", "api_key"):
        assert f"{param}=***" in masked
    for secret in ("7f8cf207", "2f0736e6", "deadbeef", "aaa", "bbb", "ccc"):
        assert secret not in masked


def test_mask_secrets_keeps_the_rest_of_the_url():
    assert _mask_secrets("https://c411.org/api?t=get&id=abc&apikey=SECRET") == (
        "https://c411.org/api?t=get&id=abc&apikey=***"
    )


# ── recent_releases: tool behaviour ───────────────────────────────────────────


def _feed_release(title, age_hours, seeders=10, indexer="C411", indexer_id=1,
                  size=1_073_741_824, tmdb=0, imdb=0):
    published = datetime.now(UTC) - timedelta(hours=age_hours)
    return {
        "title": title,
        "indexer": indexer,
        "indexerId": indexer_id,
        "size": size,
        "seeders": seeders,
        "leechers": 3,
        "protocol": "torrent",
        "publishDate": published.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "age": int(age_hours // 24),
        "ageMinutes": age_hours * 60,
        "tmdbId": tmdb,
        "imdbId": imdb,
        "guid": "https://c411.org/api?t=get&id=abc&apikey=LEAKED_TRACKER_KEY",
        "downloadUrl": "https://prowlarr.test/1/download?apikey=LEAKED_PROWLARR_KEY&link=x",
    }


def _mock_feeds(feeds, indexers=None):
    """Mock GET /indexer and route GET /search by (indexerIds, categories)."""
    respx.get(f"{BASE}/indexer").mock(
        return_value=Response(200, json=indexers or [_indexer(1, "C411")])
    )

    def handler(request):
        params = request.url.params
        assert params["query"] == ""  # the empty-query feed, not a keyword search
        assert params["type"] == "search"
        key = (int(params["indexerIds"]), int(params["categories"]))
        return Response(200, json=feeds.get(key, []))

    respx.get(f"{BASE}/search").mock(side_effect=handler)


@respx.mock
async def test_recent_releases_window_and_grouping(monkeypatch):
    _mock_feeds(
        {
            (1, 2000): [
                _feed_release("Hurlevent.2026.MULTi.VF2.1080p.WEB.H264-XX", 2.0, 40, tmdb=1234),
                _feed_release("Hurlevent.2026.MULTi.VFF.2160p.WEB.H265-YY", 6.0, 15),
                _feed_release("Old.Film.2019.MULTi.1080p.WEB.H264-ZZ", 400.0, 900),  # outside
            ],
            (1, 5000): [
                _feed_release(
                    "House.of.the.Dragon.S03E07.MULTi.1080p.WEB.H264-SUPPLY",
                    17.3, 339, indexer="The Old School (API)", tmdb=94997, imdb=26657236,
                ),
            ],
        }
    )
    fn = _prowlarr_tool("prowlarr_recent_releases", monkeypatch)
    result = await fn(hours=24, kind="all", min_seeders=0, top=15)

    # Grouped by work: the two Hurlevent releases merge and their seeders sum.
    assert "Hurlevent (2026)  S=55  ×2" in result
    assert "House of the Dragon S03E07  S=339  ×1" in result
    # The 400h release is outside the window.
    assert "Old Film" not in result
    # External ids surfaced; tvdb never shown.
    assert "tmdb=94997" in result
    assert "imdb=tt26657236" in result
    assert "tvdb" not in result
    # Raw name kept whole, with size and indexer.
    assert "House.of.the.Dragon.S03E07.MULTi.1080p.WEB.H264-SUPPLY  1.0 GB" in result
    assert "The Old School (API)" in result
    # Leechers never rendered — the field does not survive aggregation.
    assert "S/L" not in result and "leech" not in result.lower()


@respx.mock
async def test_recent_releases_never_leaks_a_credential(monkeypatch):
    _mock_feeds(
        {
            # A credential inside the release NAME itself: the mask must run at format
            # time over the whole output, not on a whitelist of fields.
            (1, 2000): [
                _feed_release("Leaky.Film.2026.MULTi.1080p.apikey=SECRETVALUE.H264-XX", 1.0, 5)
            ],
        }
    )
    fn = _prowlarr_tool("prowlarr_recent_releases", monkeypatch)
    result = await fn(hours=24, kind="movie", min_seeders=0)

    assert "SECRETVALUE" not in result
    assert "LEAKED_TRACKER_KEY" not in result
    assert "LEAKED_PROWLARR_KEY" not in result
    assert "apikey=***" in result
    # No occurrence of apikey= followed by anything other than the mask.
    assert re.findall(r"apikey=(?!\*\*\*)", result) == []
    # Grab references are withheld entirely: discovery and acquisition are split.
    assert "guid" not in result.lower()


@respx.mock
async def test_recent_releases_drops_cinema_rips_unless_asked(monkeypatch):
    feeds = {
        (1, 2000): [
            _feed_release("Big.Blockbuster.2026.MULTi.TS.1080p.x264-XX", 3.0, 5000),
            _feed_release("Quiet.Film.2026.MULTi.1080p.WEB.H264-XX", 3.0, 12),
        ]
    }
    fn = _prowlarr_tool("prowlarr_recent_releases", monkeypatch)

    _mock_feeds(feeds)
    default = await fn(hours=24, kind="movie", min_seeders=0)
    assert "Big Blockbuster" not in default
    assert "Quiet Film (2026)" in default
    assert "1 cinema rip(s)" in default

    respx.reset()
    _mock_feeds(feeds)
    with_junk = await fn(hours=24, kind="movie", min_seeders=0, include_junk=True)
    assert "Big Blockbuster (2026)" in with_junk


@respx.mock
async def test_recent_releases_quiet_window_is_not_an_error(monkeypatch):
    _mock_feeds(
        {(1, 2000): [_feed_release("Old.Film.2019.MULTi.1080p.WEB.H264-XX", 400.0, 900)]}
    )
    fn = _prowlarr_tool("prowlarr_recent_releases", monkeypatch)
    result = await fn(hours=24, kind="movie")

    assert "Quiet 24h" in result
    assert "answered normally" in result
    assert "newest 400.0h" in result
    assert "anomaly" not in result


@respx.mock
async def test_recent_releases_zero_raw_is_flagged_as_anomaly(monkeypatch):
    _mock_feeds({})  # indexer answers, but with nothing at all
    fn = _prowlarr_tool("prowlarr_recent_releases", monkeypatch)
    result = await fn(hours=24, kind="movie")

    assert "technical anomaly" in result
    assert "0 raw releases returned" in result
    assert "newest n/a" in result


@respx.mock
async def test_recent_releases_one_failing_indexer_does_not_sink_the_others(monkeypatch):
    respx.get(f"{BASE}/indexer").mock(
        return_value=Response(
            200, json=[_indexer(1, "C411"), _indexer(2, "Torr9")]
        )
    )

    def handler(request):
        params = request.url.params
        if int(params["indexerIds"]) == 2:
            return Response(500, text="tracker exploded")
        return Response(
            200,
            json=[_feed_release("Good.Film.2026.MULTi.1080p.WEB.H264-XX", 1.0, 20)],
        )

    respx.get(f"{BASE}/search").mock(side_effect=handler)

    fn = _prowlarr_tool("prowlarr_recent_releases", monkeypatch)
    result = await fn(hours=24, kind="movie", min_seeders=0)

    assert "Good Film (2026)" in result  # indexer 1 still delivered
    assert "fetch FAILED" in result
    assert "Torr9" in result


@respx.mock
async def test_recent_releases_warns_when_the_feed_cap_truncates_the_window(monkeypatch):
    # 100 releases, every one inside the window: the feed never reached past it, so the
    # window is probably truncated by the per-request cap.
    _mock_feeds(
        {
            (1, 2000): [
                _feed_release(f"Film.Number.{n}.2026.MULTi.1080p.WEB.H264-XX", 1.0, 10)
                for n in range(100)
            ]
        }
    )
    fn = _prowlarr_tool("prowlarr_recent_releases", monkeypatch)
    result = await fn(hours=24, kind="movie", min_seeders=0, top=3)

    assert "TRUNCATED" in result
    assert "at most 100" in result
    assert "97 work(s) below the top 3" in result


@respx.mock
async def test_recent_releases_unknown_indexer_id_makes_no_call(monkeypatch):
    respx.get(f"{BASE}/indexer").mock(
        return_value=Response(200, json=[_indexer(1, "C411")])
    )
    search = respx.get(f"{BASE}/search").mock(return_value=Response(200, json=[]))

    fn = _prowlarr_tool("prowlarr_recent_releases", monkeypatch)
    result = await fn(indexer_ids=[99])

    assert not search.called
    assert "no such indexer id(s): 99" in result
    assert "Configured ids: 1" in result


@respx.mock
async def test_recent_releases_rejects_bad_arguments(monkeypatch):
    search = respx.get(f"{BASE}/search").mock(return_value=Response(200, json=[]))
    fn = _prowlarr_tool("prowlarr_recent_releases", monkeypatch)

    assert "unknown kind" in await fn(kind="films")
    assert "hours must be >= 1" in await fn(hours=0)
    assert "top must be >= 1" in await fn(top=0)
    assert not search.called


@respx.mock
async def test_recent_releases_kind_tv_queries_only_category_5000(monkeypatch):
    seen = []

    respx.get(f"{BASE}/indexer").mock(
        return_value=Response(200, json=[_indexer(1, "C411")])
    )

    def handler(request):
        seen.append(int(request.url.params["categories"]))
        return Response(200, json=[])

    respx.get(f"{BASE}/search").mock(side_effect=handler)

    fn = _prowlarr_tool("prowlarr_recent_releases", monkeypatch)
    await fn(hours=24, kind="tv")

    # One request per category, never a merged categories=[2000,5000] (which would halve
    # the harvest because the 100-result cap is per request).
    assert seen == [5000]


@respx.mock
async def test_recent_releases_falls_back_to_age_minutes(monkeypatch):
    release = _feed_release("Fallback.Film.2026.MULTi.1080p.WEB.H264-XX", 2.0, 30)
    del release["publishDate"]  # some payload without the primary field
    _mock_feeds({(1, 2000): [release]})

    fn = _prowlarr_tool("prowlarr_recent_releases", monkeypatch)
    result = await fn(hours=24, kind="movie", min_seeders=0)

    assert "Fallback Film (2026)" in result
    assert "2.0h" in result
