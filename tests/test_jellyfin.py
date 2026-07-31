import json

import pytest
import respx
from httpx import Response
from mcp.server.fastmcp import FastMCP

from media_mcp.clients.jellyfin import (
    JellyfinClient,
    JellyfinClientError,
    JellyfinPluginMissingError,
    clear_user_cache,
    normalize_item_dto,
)
from media_mcp.jellyfin_resolve import (
    JellyfinResolutionError,
    normalize_title,
    resolve_movies,
    resolve_single,
)
from media_mcp.tools.jellyfin_tools import _format_watch_time, register_jellyfin_tools

BASE = "http://jellyfin.test:8096"
API_KEY = "test-jellyfin-key"
ADMIN_ID = "admin000adminidadminid0000000000"

RADARR_URL = "http://radarr.test:7878"
RADARR_BASE = "http://radarr.test:7878/api/v3"
RADARR_KEY = "test-radarr-key"


@pytest.fixture(autouse=True)
def _reset_cache():
    # The admin-userId cache is process-wide; isolate every test from the others.
    clear_user_cache()
    yield
    clear_user_cache()


@pytest.fixture
def client():
    return JellyfinClient(BASE, API_KEY)


@pytest.fixture
def client_radarr():
    return JellyfinClient(BASE, API_KEY, radarr_url=RADARR_URL, radarr_api_key=RADARR_KEY)


def _jellyfin_tool(name, monkeypatch, radarr=False):
    def factory():
        if radarr:
            return JellyfinClient(BASE, API_KEY, radarr_url=RADARR_URL, radarr_api_key=RADARR_KEY)
        return JellyfinClient(BASE, API_KEY)

    monkeypatch.setattr("media_mcp.tools.jellyfin_tools._client", factory)
    m = FastMCP("test")
    register_jellyfin_tools(m)
    return m._tool_manager.get_tool(name).fn


# Real pairs from the user's library: Jellyfin indexes the ENGLISH name, the user asks by the
# FRENCH title, and Radarr is the bridge (it knows the French titles). (french, english, tmdb, id)
_PAIRS = [
    ("Perdrix", "The Bare Necessity", 592798, "aaaa000000000001"),
    ("La Fille du 14 juillet", "The Rendez-Vous of Deja-Vu", 190817, "aaaa000000000002"),
    ("Pendant ce temps sur Terre", "Meanwhile on Earth", 1001083, "aaaa000000000003"),
    ("Le Syndicat du crime", "A Better Tomorrow", 11471, "aaaa000000000004"),
    ("Nuages flottants", "Floating Clouds", 77285, "aaaa000000000005"),
    ("La Pianiste", "The Piano Teacher", 1791, "aaaa000000000006"),
]


def _radarr_movie(title, tmdb, original=None, alternates=None):
    m = {"title": title, "tmdbId": tmdb}
    if original is not None:
        m["originalTitle"] = original
    if alternates is not None:
        m["alternateTitles"] = [{"title": a} for a in alternates]
    return m


def _jf_english():
    return [_movie(jid, en, 2000, tmdb=tmdb) for (_fr, en, tmdb, jid) in _PAIRS]


def _radarr_french():
    return [_radarr_movie(fr, tmdb) for (fr, _en, tmdb, _jid) in _PAIRS]


def _users():
    return [
        {"Id": "user-non-admin", "Name": "kid", "Policy": {"IsAdministrator": False}},
        {"Id": ADMIN_ID, "Name": "boss", "Policy": {"IsAdministrator": True}},
    ]


def _mock_users():
    return respx.get(f"{BASE}/Users").mock(return_value=Response(200, json=_users()))


def _movie(iid, name, year, tmdb=None, original=None):
    item = {"Id": iid, "Name": name, "ProductionYear": year, "Type": "Movie"}
    if tmdb is not None:
        item["ProviderIds"] = {"Tmdb": str(tmdb)}
    if original is not None:
        item["OriginalTitle"] = original
    return item


def _catalog():
    return [
        _movie("1111111111111111111111111111aaaa", "Heat", 1995, tmdb=949),
        _movie("2222222222222222222222222222bbbb", "Collateral", 2004, tmdb=8375),
        _movie("3333333333333333333333333333cccc", "Le Solitaire", 1987, tmdb=111),
        _movie("4444444444444444444444444444dddd", "Batman", 1989, tmdb=268),
        _movie("5555555555555555555555555555eeee", "Batman Begins", 2005, tmdb=272),
    ]


# ── Auth header ───────────────────────────────────────────────────────────────


@respx.mock
async def test_auth_header_exact(client):
    route = respx.get(f"{BASE}/System/Info").mock(
        return_value=Response(200, json={"ServerName": "Home", "Version": "10.11.0"})
    )
    await client.system_info()
    header = route.calls.last.request.headers["authorization"]
    assert header == f'MediaBrowser Token="{API_KEY}"'


@respx.mock
async def test_unauthorized_maps_to_clear_error(client):
    respx.get(f"{BASE}/System/Info").mock(return_value=Response(401, text="no"))
    with pytest.raises(JellyfinClientError, match="Unauthorized"):
        await client.system_info()


async def test_not_configured_raises():
    c = JellyfinClient("", "")
    with pytest.raises(JellyfinClientError, match="not configured"):
        await c.system_info()


# ── User resolution + caching ─────────────────────────────────────────────────


@respx.mock
async def test_resolve_user_picks_first_admin_and_caches(client):
    route = _mock_users()
    uid = await client.resolve_user_id()
    assert uid == ADMIN_ID
    uid2 = await client.resolve_user_id()  # served from cache, no second request
    assert uid2 == ADMIN_ID
    assert route.call_count == 1


@respx.mock
async def test_resolve_user_no_admin_errors(client):
    respx.get(f"{BASE}/Users").mock(
        return_value=Response(
            200, json=[{"Id": "x", "Name": "kid", "Policy": {"IsAdministrator": False}}]
        )
    )
    with pytest.raises(JellyfinClientError, match="administrator"):
        await client.resolve_user_id()


# ── Pagination of /Items ──────────────────────────────────────────────────────


@respx.mock
async def test_get_all_items_paginates(client):
    _mock_users()

    def handler(request):
        assert request.url.params["userId"] == ADMIN_ID
        start = int(request.url.params.get("startIndex", "0"))
        if start == 0:
            page = [_movie("a" * 32, "A", 2001), _movie("b" * 32, "B", 2002)]
        else:
            page = [_movie("c" * 32, "C", 2003)]
        return Response(200, json={"Items": page, "TotalRecordCount": 3})

    route = respx.get(f"{BASE}/Items").mock(side_effect=handler)
    items = await client.get_all_items(
        include_item_types="Movie", fields="ProviderIds", page_size=2
    )
    assert [i["Name"] for i in items] == ["A", "B", "C"]
    assert route.call_count == 2


# ── Resolution (exact / ambiguous / not found) ────────────────────────────────


def test_normalize_title_strips_accents_and_article():
    assert normalize_title("Le Solitaire") == "solitaire"
    assert normalize_title("Amélie") == "amelie"
    assert normalize_title("The Dark Knight!") == "dark knight"


async def test_resolve_by_tmdb_exact():
    r = await resolve_movies(_catalog(), ["8375"])
    assert len(r.matched) == 1
    assert r.matched[0].item["Name"] == "Collateral"
    assert r.matched[0].method == "tmdb"


async def test_resolve_by_id_prefix():
    r = await resolve_movies(_catalog(), ["11111111"])
    assert r.matched[0].item["Name"] == "Heat"
    assert r.matched[0].method == "id"


async def test_resolve_by_title_accents_and_article():
    r = await resolve_movies(_catalog(), ["le solitaire", "COLLATERAL"])
    assert {m.item["Name"] for m in r.matched} == {"Le Solitaire", "Collateral"}
    assert all(m.method == "title" for m in r.matched)


async def test_resolve_ambiguous_title_not_chosen():
    # "bat" is a substring of two titles -> surfaced as ambiguous, never guessed.
    r = await resolve_movies(_catalog(), ["bat"])
    assert not r.matched
    assert len(r.ambiguous) == 1
    ref, candidates = r.ambiguous[0]
    assert ref == "bat"
    assert {c["Name"] for c in candidates} == {"Batman", "Batman Begins"}


async def test_resolve_not_found():
    r = await resolve_movies(_catalog(), ["totally unknown title"])
    assert r.not_found == ["totally unknown title"]
    assert not r.matched and not r.ambiguous


# ── Cascade level 4 (OriginalTitle) and level 5 (Radarr fallback) ─────────────


async def test_resolve_via_original_title():
    jf = [
        _movie(
            "bbbb0000000000000000000000000001", "The Piano Teacher", 2001,
            tmdb=1791, original="La Pianiste",
        )
    ]
    r = await resolve_movies(jf, ["la pianiste"])
    assert len(r.matched) == 1
    assert r.matched[0].method == "original-title"


async def test_resolve_via_radarr_fallback():
    async def fetcher():
        return _radarr_french()

    refs = ["Perdrix", "La Fille du 14 juillet", "Le Syndicat du crime"]
    r = await resolve_movies(_jf_english(), refs, radarr_fetcher=fetcher)
    assert len(r.matched) == 3
    by_name = {m.item["Name"]: m for m in r.matched}
    assert by_name["The Bare Necessity"].method == "via-radarr"
    assert by_name["The Bare Necessity"].radarr_title == "Perdrix"


async def test_resolve_via_radarr_alternate_titles():
    # Radarr matches on the alternateTitles array too, not just title / originalTitle.
    async def fetcher():
        return [_radarr_movie("A Better Tomorrow", 11471, alternates=["Le Syndicat du crime"])]

    jf = [_movie("dddd0000000000000000000000000001", "A Better Tomorrow", 1986, tmdb=11471)]
    r = await resolve_movies(jf, ["Le Syndicat du crime"], radarr_fetcher=fetcher)
    assert len(r.matched) == 1
    assert r.matched[0].method == "via-radarr"


async def test_resolve_radarr_unavailable_clean_not_found():
    async def fetcher():
        return None  # Radarr not configured / unreachable

    r = await resolve_movies(_jf_english(), ["Perdrix"], radarr_fetcher=fetcher)
    assert r.not_found == ["Perdrix"]
    assert not r.matched


async def test_resolve_no_radarr_fetcher_is_not_found():
    r = await resolve_movies(_jf_english(), ["Perdrix"])  # no fetcher passed at all
    assert r.not_found == ["Perdrix"]


async def test_radarr_fetched_once_and_lazily():
    calls = {"n": 0}

    async def fetcher():
        calls["n"] += 1
        return _radarr_french()

    refs = [fr for (fr, *_rest) in _PAIRS]  # 6 French refs, all fall through to level 5
    r = await resolve_movies(_jf_english(), refs, radarr_fetcher=fetcher)
    assert len(r.matched) == 6
    assert calls["n"] == 1  # a single fetch serves all six

    calls["n"] = 0
    r2 = await resolve_movies(_jf_english(), ["592798"], radarr_fetcher=fetcher)
    assert len(r2.matched) == 1  # resolves at level 1 (tmdb)
    assert calls["n"] == 0  # never reached level 5 -> Radarr never fetched


async def test_radarr_ambiguity_refused():
    async def fetcher():
        # Two Radarr movies share a title but map to two different Jellyfin items.
        return [_radarr_movie("Solitude", 100), _radarr_movie("Solitude", 200)]

    jf = [
        _movie("cccc0000000000000000000000000001", "Solitude One", 2001, tmdb=100),
        _movie("cccc0000000000000000000000000002", "Solitude Two", 2002, tmdb=200),
    ]
    r = await resolve_movies(jf, ["Solitude"], radarr_fetcher=fetcher)
    assert not r.matched
    assert len(r.ambiguous) == 1  # never an arbitrary pick, even at level 5


def test_resolve_single_ambiguous_raises():
    with pytest.raises(JellyfinResolutionError, match="Ambiguous"):
        resolve_single(_catalog(), "bat", kind="collection")


def test_resolve_single_not_found_raises():
    with pytest.raises(JellyfinResolutionError, match="No collection found"):
        resolve_single(_catalog(), "zzz nope", kind="collection")


# ── DTO null normalization (the module's main failure mode) ───────────────────


def test_normalize_item_dto_no_null_arrays():
    item = {
        "Id": "x", "Name": "n", "Overview": None,
        "Tags": None, "Genres": None, "Studios": None, "People": None,
        "LockedFields": None, "GenreItems": None, "TagItems": None, "ProviderIds": None,
    }
    dto = normalize_item_dto(item)
    for field in ("Tags", "Genres", "Studios", "People", "LockedFields", "GenreItems", "TagItems"):
        assert dto[field] == [], f"{field} must be [] not null"
        assert isinstance(dto[field], list)
    # ProviderIds is a MAP, not a list.
    assert dto["ProviderIds"] == {}
    assert isinstance(dto["ProviderIds"], dict)


@respx.mock
async def test_set_item_overview_round_trip_normalizes_nulls(client):
    _mock_users()
    item_id = "col00000000000000000000000000000"
    full_item = {
        "Id": item_id, "Name": "Neo-noir", "Type": "BoxSet", "Overview": None,
        "Tags": None, "Genres": None, "Studios": None, "People": None,
        "LockedFields": None, "GenreItems": None, "TagItems": None, "ProviderIds": None,
    }
    respx.get(f"{BASE}/Users/{ADMIN_ID}/Items/{item_id}").mock(
        return_value=Response(200, json=full_item)
    )
    post = respx.post(f"{BASE}/Items/{item_id}").mock(return_value=Response(204))

    await client.set_item_overview(item_id, "A curated set of neo-noir films.", lock=True)

    assert post.called
    body = json.loads(post.calls.last.request.content)
    # Explicit check: no null leaves in any array-typed field.
    for field in ("Tags", "Genres", "Studios", "People", "LockedFields", "GenreItems", "TagItems"):
        assert body[field] is not None
        assert isinstance(body[field], list)
    assert body["ProviderIds"] == {}
    assert body["Overview"] == "A curated set of neo-noir films."
    assert "Overview" in body["LockedFields"]  # locked by default


@respx.mock
async def test_set_item_overview_no_lock_leaves_fields_unlocked(client):
    _mock_users()
    item_id = "mov00000000000000000000000000000"
    respx.get(f"{BASE}/Users/{ADMIN_ID}/Items/{item_id}").mock(
        return_value=Response(200, json={"Id": item_id, "Name": "Heat", "LockedFields": []})
    )
    post = respx.post(f"{BASE}/Items/{item_id}").mock(return_value=Response(204))

    await client.set_item_overview(item_id, "Cops and thieves.", lock=False)

    body = json.loads(post.calls.last.request.content)
    assert "Overview" not in body["LockedFields"]


# ── Requested `fields` (correctif 1 + OriginalTitle) ─────────────────────────


@respx.mock
async def test_collection_catalog_requests_childcount(client):
    _mock_users()
    route = respx.get(f"{BASE}/Items").mock(
        return_value=Response(200, json={"Items": [], "TotalRecordCount": 0})
    )
    await client.collection_catalog()
    fields = route.calls.last.request.url.params["fields"]
    assert "ChildCount" in fields
    assert "Overview" in fields  # pre-existing field not overwritten


@respx.mock
async def test_movie_catalog_requests_originaltitle(client):
    _mock_users()
    route = respx.get(f"{BASE}/Items").mock(
        return_value=Response(200, json={"Items": [], "TotalRecordCount": 0})
    )
    await client.movie_catalog()
    fields = route.calls.last.request.url.params["fields"]
    assert "OriginalTitle" in fields
    assert "ProviderIds" in fields


# ── Radarr fallback wiring + request counting (correctif 2 / 2b) ──────────────


@respx.mock
async def test_resolve_movies_refs_one_jellyfin_one_radarr_fetch(client_radarr):
    _mock_users()
    jf_route = respx.get(f"{BASE}/Items").mock(
        return_value=Response(
            200, json={"Items": _jf_english(), "TotalRecordCount": len(_jf_english())}
        )
    )
    radarr_route = respx.get(f"{RADARR_BASE}/movie").mock(
        return_value=Response(200, json=_radarr_french())
    )
    refs = [fr for (fr, *_rest) in _PAIRS]  # 6 French references
    r = await client_radarr.resolve_movies_refs(refs)
    assert len(r.matched) == 6
    assert jf_route.call_count == 1  # a single full Jellyfin library fetch
    assert radarr_route.call_count == 1  # a single Radarr fetch, triggered lazily


@respx.mock
async def test_resolve_movies_refs_radarr_not_configured(client):
    _mock_users()
    respx.get(f"{BASE}/Items").mock(
        return_value=Response(
            200, json={"Items": _jf_english(), "TotalRecordCount": len(_jf_english())}
        )
    )
    # No Radarr configured on this client -> level 5 skipped, no HTTP, clean not_found.
    r = await client.resolve_movies_refs(["Perdrix"])
    assert r.not_found == ["Perdrix"]


@respx.mock
async def test_resolve_movies_refs_radarr_unreachable_skips(client_radarr):
    _mock_users()
    respx.get(f"{BASE}/Items").mock(
        return_value=Response(
            200, json={"Items": _jf_english(), "TotalRecordCount": len(_jf_english())}
        )
    )
    radarr_route = respx.get(f"{RADARR_BASE}/movie").mock(return_value=Response(500, text="boom"))
    r = await client_radarr.resolve_movies_refs(["Perdrix"])
    assert radarr_route.called
    assert r.not_found == ["Perdrix"]  # unreachable Radarr -> no exception, just not_found


# ── Read tools ────────────────────────────────────────────────────────────────


@respx.mock
async def test_system_status_tool(monkeypatch):
    respx.get(f"{BASE}/System/Info").mock(
        return_value=Response(200, json={"ServerName": "Home", "Version": "10.11.0"})
    )
    fn = _jellyfin_tool("jellyfin_system_status", monkeypatch)
    result = await fn()
    assert "10.11.0" in result and "Home" in result


@respx.mock
async def test_tool_reports_missing_config(monkeypatch):
    monkeypatch.setattr(
        "media_mcp.tools.jellyfin_tools._client", lambda: JellyfinClient("", "")
    )
    m = FastMCP("test")
    register_jellyfin_tools(m)
    fn = m._tool_manager.get_tool("jellyfin_system_status").fn
    result = await fn()
    assert result.lower().startswith("error")
    assert "not configured" in result.lower()


@respx.mock
async def test_list_movies_table(monkeypatch):
    _mock_users()
    respx.get(f"{BASE}/Items").mock(
        return_value=Response(200, json={"Items": _catalog(), "TotalRecordCount": len(_catalog())})
    )
    fn = _jellyfin_tool("jellyfin_list_movies", monkeypatch)
    result = await fn()
    assert "Collateral" in result
    assert "8375" in result  # tmdbId shown
    assert "11111111" in result  # short id (Heat), truncated to 8 chars


@respx.mock
async def test_list_collections_table(monkeypatch):
    _mock_users()
    cols = [
        {
            "Id": "cabc0000000000000000000000000000",
            "Name": "Neo-noir",
            "ChildCount": 3,
            "Overview": "Dark, moody, modern crime films that run well past fifty chars total.",
        }
    ]
    respx.get(f"{BASE}/Items").mock(
        return_value=Response(200, json={"Items": cols, "TotalRecordCount": 1})
    )
    fn = _jellyfin_tool("jellyfin_list_collections", monkeypatch)
    result = await fn()
    assert "Neo-noir" in result
    assert "cabc0000" in result
    assert "..." in result  # long description truncated


# ── Write tools: dry-runs emit no writes ──────────────────────────────────────


@respx.mock
async def test_create_collection_dry_run_no_write(monkeypatch):
    _mock_users()
    respx.get(f"{BASE}/Items").mock(
        return_value=Response(200, json={"Items": _catalog(), "TotalRecordCount": len(_catalog())})
    )
    create_route = respx.post(f"{BASE}/Collections").mock(
        return_value=Response(200, json={"Id": "x"})
    )
    fn = _jellyfin_tool("jellyfin_create_collection", monkeypatch)
    result = await fn(name="Mann", movies=["8375", "le solitaire", "bat", "nope"], confirm=False)
    assert not create_route.called
    assert "DRY-RUN" in result
    assert "Collateral" in result and "Le Solitaire" in result  # matched
    assert "[via tmdb]" in result and "[via title]" in result  # resolution method column
    assert "Ambiguous" in result and "bat" in result
    assert "Not found" in result and "nope" in result


@respx.mock
async def test_create_collection_dry_run_radarr_traceability(monkeypatch):
    _mock_users()
    respx.get(f"{BASE}/Items").mock(
        return_value=Response(
            200, json={"Items": _jf_english(), "TotalRecordCount": len(_jf_english())}
        )
    )
    respx.get(f"{RADARR_BASE}/movie").mock(return_value=Response(200, json=_radarr_french()))
    create_route = respx.post(f"{BASE}/Collections").mock(
        return_value=Response(200, json={"Id": "x"})
    )
    fn = _jellyfin_tool("jellyfin_create_collection", monkeypatch, radarr=True)
    # "1791" resolves via tmdb (The Piano Teacher); "Perdrix" only via the Radarr bridge.
    result = await fn(name="FR", movies=["Perdrix", "1791"], confirm=False)
    assert not create_route.called
    assert "via radarr: 'Perdrix' -> 'The Bare Necessity'" in result
    assert "[via tmdb]" in result


@respx.mock
async def test_create_collection_confirm_refuses_partial(monkeypatch):
    _mock_users()
    respx.get(f"{BASE}/Items").mock(
        return_value=Response(200, json={"Items": _catalog(), "TotalRecordCount": len(_catalog())})
    )
    create_route = respx.post(f"{BASE}/Collections").mock(
        return_value=Response(200, json={"Id": "x"})
    )
    fn = _jellyfin_tool("jellyfin_create_collection", monkeypatch)
    result = await fn(name="X", movies=["8375", "nope"], confirm=True)
    assert not create_route.called  # unresolved -> refuse, never create partial
    assert result.lower().startswith("error")


@respx.mock
async def test_create_collection_confirm_creates(monkeypatch):
    _mock_users()
    respx.get(f"{BASE}/Items").mock(
        return_value=Response(200, json={"Items": _catalog(), "TotalRecordCount": len(_catalog())})
    )
    create_route = respx.post(f"{BASE}/Collections").mock(
        return_value=Response(200, json={"Id": "newcollid00000000000000000000000"})
    )
    fn = _jellyfin_tool("jellyfin_create_collection", monkeypatch)
    result = await fn(name="Heat-night", movies=["949", "8375"], confirm=True)
    assert create_route.called
    params = create_route.calls.last.request.url.params
    assert params["name"] == "Heat-night"
    assert len(params["ids"].split(",")) == 2
    assert "Created collection" in result


@respx.mock
async def test_create_collection_missing_library_actionable(monkeypatch):
    _mock_users()
    respx.get(f"{BASE}/Items").mock(
        return_value=Response(200, json={"Items": _catalog(), "TotalRecordCount": len(_catalog())})
    )
    respx.post(f"{BASE}/Collections").mock(
        return_value=Response(500, text="Sequence contains no elements")
    )
    fn = _jellyfin_tool("jellyfin_create_collection", monkeypatch)
    result = await fn(name="X", movies=["949"], confirm=True)
    assert "Collections' library" in result
    assert "manually" in result.lower()


@respx.mock
async def test_add_to_collection_dry_run_no_write(monkeypatch):
    _mock_users()
    boxset = {"Id": "box0000000000000000000000000000", "Name": "Michael Mann", "Overview": ""}
    heat = _catalog()[0]

    def items_handler(request):
        params = request.url.params
        if params.get("parentId"):
            return Response(200, json={"Items": [heat], "TotalRecordCount": 1})
        if params.get("includeItemTypes") == "BoxSet":
            return Response(200, json={"Items": [boxset], "TotalRecordCount": 1})
        return Response(200, json={"Items": _catalog(), "TotalRecordCount": len(_catalog())})

    respx.get(f"{BASE}/Items").mock(side_effect=items_handler)
    add_route = respx.post(url__regex=rf"{BASE}/Collections/.+/Items").mock(
        return_value=Response(204)
    )
    fn = _jellyfin_tool("jellyfin_add_to_collection", monkeypatch)
    result = await fn(collection_ref="Michael Mann", movies=["Heat", "Collateral"], confirm=False)
    assert not add_route.called
    assert "DRY-RUN" in result
    assert "Already in collection" in result  # Heat is already a member
    assert "To add" in result  # Collateral


@respx.mock
async def test_add_to_collection_confirm_posts_ids(monkeypatch):
    _mock_users()
    # Jellyfin ids are 32-char hex GUIDs; the tool accepts a unique hex prefix on input.
    boxset = {"Id": "bbbb0000000000000000000000000000", "Name": "Michael Mann"}

    def items_handler(request):
        params = request.url.params
        if params.get("parentId"):
            return Response(200, json={"Items": [], "TotalRecordCount": 0})
        if params.get("includeItemTypes") == "BoxSet":
            return Response(200, json={"Items": [boxset], "TotalRecordCount": 1})
        return Response(200, json={"Items": _catalog(), "TotalRecordCount": len(_catalog())})

    respx.get(f"{BASE}/Items").mock(side_effect=items_handler)
    add_route = respx.post(f"{BASE}/Collections/bbbb0000000000000000000000000000/Items").mock(
        return_value=Response(204)
    )
    fn = _jellyfin_tool("jellyfin_add_to_collection", monkeypatch)
    result = await fn(collection_ref="bbbb0000", movies=["949"], confirm=True)
    assert add_route.called
    assert add_route.calls.last.request.url.params["ids"] == "1111111111111111111111111111aaaa"
    assert "Added 1 movie" in result


@respx.mock
async def test_set_overview_dry_run_no_write(monkeypatch):
    _mock_users()

    def items_handler(request):
        if request.url.params.get("includeItemTypes") == "BoxSet":
            return Response(200, json={"Items": [], "TotalRecordCount": 0})
        return Response(200, json={"Items": _catalog(), "TotalRecordCount": len(_catalog())})

    respx.get(f"{BASE}/Items").mock(side_effect=items_handler)
    post_route = respx.post(url__regex=rf"{BASE}/Items/.+").mock(return_value=Response(204))
    fn = _jellyfin_tool("jellyfin_set_overview", monkeypatch)
    result = await fn(item_ref="collateral", overview="Great heist thriller.", confirm=False)
    assert not post_route.called
    assert "DRY-RUN" in result
    assert "Collateral" in result


# ── Active sessions ───────────────────────────────────────────────────────────

SESSIONS_URL = f"{BASE}/Sessions"


def _idle_session(user, client_name, device):
    """A connected-but-idle client: NowPlayingItem is ABSENT (live-confirmed shape)."""
    return {
        "Id": f"sess{user}", "UserId": f"uid-{user}", "UserName": user,
        "Client": client_name, "DeviceName": device, "IsActive": True,
        "PlayState": {"CanSeek": False, "IsPaused": False, "IsMuted": False,
                      "RepeatMode": "RepeatNone", "PlaybackOrder": "Default"},
        "NowPlayingQueue": [],
    }


def _playing_session(user, client_name, device, item, position_s, paused, method, transcode=None):
    session = _idle_session(user, client_name, device)
    session["PlayState"] = {
        "CanSeek": True, "IsPaused": paused, "IsMuted": False,
        "PositionTicks": position_s * 10_000_000, "PlayMethod": method,
    }
    session["NowPlayingItem"] = item
    if transcode is not None:
        session["TranscodingInfo"] = transcode
    return session


def _episode_item():
    # Real field names, taken from a live NowPlayingQueueFullItems entry.
    return {
        "Id": "ep000000000000000000000000000001", "Name": "Prendre le mal a la racine",
        "Type": "Episode", "RunTimeTicks": 25851760000,  # 43:05
        "SeriesName": "Grey's Anatomy", "IndexNumber": 6, "ParentIndexNumber": 11,
        "ProductionYear": 2014, "MediaType": "Video",
    }


@respx.mock
async def test_active_sessions_summary(monkeypatch):
    sessions = [
        _idle_session("william", "Jellyfin Web", "Firefox"),
        _playing_session(
            "lau", "SenPlayer", "Apple TV", _episode_item(), 775, False, "DirectStream"
        ),
        _playing_session(
            "hip", "Jellyfin Web", "LG Smart TV",
            {"Id": "mv" + "0" * 30, "Name": "Blue Velvet", "Type": "Movie",
             "ProductionYear": 1986, "RunTimeTicks": 72000000000},  # 2:00:00
            3600, True, "Transcode",
            transcode={"VideoCodec": "h264", "AudioCodec": "aac", "IsVideoDirect": False,
                       "IsAudioDirect": True, "TranscodeReasons": ["VideoCodecNotSupported"]},
        ),
    ]
    respx.get(SESSIONS_URL).mock(return_value=Response(200, json=sessions))
    fn = _jellyfin_tool("jellyfin_active_sessions", monkeypatch)
    result = await fn()

    assert "2 session(s)" in result
    # Episode label: series + SxxEyy + title.
    assert "Grey's Anatomy — S11E06 — Prendre le mal a la racine" in result
    assert "Blue Velvet (1986)" in result
    assert "lau — SenPlayer on Apple TV" in result
    assert "playing" in result and "paused" in result
    assert "12:55 / 43:05 (29%)" in result  # position / runtime / percent
    assert "1:00:00 / 2:00:00 (50%)" in result
    assert "DirectStream" in result
    assert "video=h264" in result and "audio=aac (direct)" in result
    assert "VideoCodecNotSupported" in result
    # The idle client is counted, not listed as playback.
    assert "1 further client(s) connected" in result
    assert "william" not in result


@respx.mock
async def test_active_sessions_none_playing_but_clients_connected(monkeypatch):
    respx.get(SESSIONS_URL).mock(
        return_value=Response(200, json=[_idle_session("lau", "SenPlayer", "Apple TV")])
    )
    fn = _jellyfin_tool("jellyfin_active_sessions", monkeypatch)
    result = await fn()
    assert "No active playback sessions" in result
    assert "1 client(s) connected but idle" in result


@respx.mock
async def test_active_sessions_empty(monkeypatch):
    respx.get(SESSIONS_URL).mock(return_value=Response(200, json=[]))
    fn = _jellyfin_tool("jellyfin_active_sessions", monkeypatch)
    result = await fn()
    assert result == "No active playback sessions."


@respx.mock
async def test_active_sessions_tolerates_missing_playstate_fields(monkeypatch):
    # A client can report an item with no PositionTicks / PlayMethod at all.
    session = _idle_session("lau", "SenPlayer", "Apple TV")
    session["NowPlayingItem"] = {"Id": "x" * 32, "Name": "Heat", "Type": "Movie"}
    respx.get(SESSIONS_URL).mock(return_value=Response(200, json=[session]))
    fn = _jellyfin_tool("jellyfin_active_sessions", monkeypatch)
    result = await fn()
    assert "Heat" in result
    assert "? / ?" in result  # unknown position and runtime, no crash
    assert "unknown" in result  # unknown play method


# ── Library scan ──────────────────────────────────────────────────────────────

VFS_URL = f"{BASE}/Library/VirtualFolders"
FILMS_ID = "db4c1708cbb5dd1676284a40f2950aba"
SERIES_ID = "d565273fd114d77bdf349a2896867069"


def _libraries():
    return [
        {"Name": "Animes", "ItemId": "ca0de50d2c11073f53df7c82dc3fe2a4",
         "CollectionType": "tvshows", "Locations": ["/Media/Animes"]},
        {"Name": "Séries", "ItemId": SERIES_ID,
         "CollectionType": "tvshows", "Locations": ["/Media/TV Shows"]},
        {"Name": "Films", "ItemId": FILMS_ID,
         "CollectionType": "movies", "Locations": ["/Media/Movies"]},
    ]


def _mock_libraries():
    return respx.get(VFS_URL).mock(return_value=Response(200, json=_libraries()))


@respx.mock
async def test_scan_library_global_dry_run_no_post(monkeypatch):
    _mock_libraries()
    post = respx.post(f"{BASE}/Library/Refresh").mock(return_value=Response(204))
    fn = _jellyfin_tool("jellyfin_scan_library", monkeypatch)
    result = await fn(confirm=False)
    assert not post.called
    assert "DRY-RUN" in result and "GLOBAL" in result
    assert "Films" in result and "Animes" in result


@respx.mock
async def test_scan_library_global_confirm_posts(monkeypatch):
    _mock_libraries()
    post = respx.post(f"{BASE}/Library/Refresh").mock(return_value=Response(204))
    fn = _jellyfin_tool("jellyfin_scan_library", monkeypatch)
    result = await fn(confirm=True)
    assert post.called
    assert "Global library scan triggered" in result


@respx.mock
async def test_scan_library_targeted_dry_run_no_post(monkeypatch):
    _mock_libraries()
    post = respx.post(f"{BASE}/Items/{FILMS_ID}/Refresh").mock(return_value=Response(204))
    fn = _jellyfin_tool("jellyfin_scan_library", monkeypatch)
    result = await fn(library="Films", confirm=False)
    assert not post.called
    assert "DRY-RUN" in result
    assert "ONLY the library 'Films'" in result
    assert "/Media/Movies" in result


@respx.mock
async def test_scan_library_targeted_confirm_posts_expected_params(monkeypatch):
    _mock_libraries()
    post = respx.post(f"{BASE}/Items/{FILMS_ID}/Refresh").mock(return_value=Response(204))
    global_post = respx.post(f"{BASE}/Library/Refresh").mock(return_value=Response(204))
    fn = _jellyfin_tool("jellyfin_scan_library", monkeypatch)
    result = await fn(library="films", confirm=True)  # case-insensitive name
    assert post.called
    assert not global_post.called  # a targeted scan must never fall back to a global one
    params = post.calls.last.request.url.params
    # Live-confirmed bound params; replace_all_* stay false so metadata is not wiped.
    assert params["metadataRefreshMode"] == "Default"
    assert params["imageRefreshMode"] == "Default"
    assert params["replaceAllMetadata"] == "false"
    assert params["replaceAllImages"] == "false"
    assert "Scan triggered for library 'Films'" in result


@respx.mock
async def test_scan_library_accepts_accented_name_and_id_prefix(monkeypatch):
    _mock_libraries()
    post = respx.post(f"{BASE}/Items/{SERIES_ID}/Refresh").mock(return_value=Response(204))
    fn = _jellyfin_tool("jellyfin_scan_library", monkeypatch)
    # Accent-insensitive: "series" resolves "Séries".
    assert "DRY-RUN" in await fn(library="series", confirm=False)
    result = await fn(library="d565273f", confirm=True)  # 8-char id prefix
    assert post.called
    assert "Séries" in result


@respx.mock
async def test_scan_library_unknown_is_clear_and_lists_available(monkeypatch):
    _mock_libraries()
    post = respx.post(url__regex=rf"{BASE}/Items/.+/Refresh").mock(return_value=Response(204))
    fn = _jellyfin_tool("jellyfin_scan_library", monkeypatch)
    result = await fn(library="Musique", confirm=True)
    assert not post.called
    assert result.lower().startswith("error")
    assert "No library found matching 'Musique'" in result
    assert "Films" in result  # the available libraries are listed


# ── Item playback history ─────────────────────────────────────────────────────

QUERY_URL = f"{BASE}/user_usage_stats/submit_custom_query"
HEAT_ID = "1111111111111111111111111111aaaa"
GREYS_ID = "aaaa1111111111111111111111111111"

# The plugin's real response envelope: "colums" is ITS typo, and errors arrive as HTTP 200.
_HISTORY_COLUMNS = [
    "UserName", "ItemType", "ItemName", "PlaybackMethod",
    "ClientName", "DeviceName", "DateCreated", "PlayDuration",
]


def _query_response(rows, message=""):
    return {"colums": _HISTORY_COLUMNS if rows else [], "results": rows, "message": message}


def _play(user, name, method, client_name, device, date, seconds, kind="Episode"):
    return [user, kind, name, method, client_name, device, date, str(seconds)]


def _mock_media_catalog(items):
    """/Items serves the Movie,Series catalog used by resolve_media_item."""
    return respx.get(f"{BASE}/Items").mock(
        return_value=Response(200, json={"Items": items, "TotalRecordCount": len(items)})
    )


@respx.mock
async def test_item_history_by_id_and_summary(monkeypatch):
    _mock_users()
    _mock_media_catalog([_movie(HEAT_ID, "Heat", 1995, tmdb=949)])
    rows = [
        _play("lau", "Heat", "DirectPlay", "SenPlayer", "Apple TV",
              "2026-07-31 20:13:02.7902664", 7177, kind="Movie"),
        _play("hip", "Heat", "Transcode", "Jellyfin Web", "LG Smart TV",
              "2026-06-01 10:00:00.0000000", 3600, kind="Movie"),
        _play("lau", "Heat", "DirectPlay", "SenPlayer", "Apple TV",
              "2026-05-02 21:00:00.0000000", 1800, kind="Movie"),
    ]
    query = respx.post(QUERY_URL).mock(return_value=Response(200, json=_query_response(rows)))
    fn = _jellyfin_tool("jellyfin_item_history", monkeypatch)
    result = await fn(item="1111111111111111111111111111aaaa", days=90)

    assert "3 play(s)" in result
    assert "'Heat' (Movie)" in result
    # Per-user aggregate: lau 2 plays / 7177+1800 = 8977s = 2h29m; hip 1 play / 1h00m.
    assert "2h29m" in result and "1h00m" in result
    assert "2026-07-31 20:13" in result  # date trimmed to the minute
    assert "SenPlayer / Apple TV" in result
    assert result.index("lau") < result.index("hip")  # sorted by play count

    body = json.loads(query.calls.last.request.content)
    assert body["ReplaceUserId"] is True  # or the UserId column stays a raw GUID
    sql = body["CustomQueryString"]
    assert "SELECT UserId" in sql  # selecting UserName errors: it is not a real column
    assert f"'{HEAT_ID}'" in sql
    assert "datetime('now', '-90 days')" in sql


@respx.mock
async def test_item_history_shows_full_transcode_method(monkeypatch):
    _mock_users()
    _mock_media_catalog([_movie(HEAT_ID, "Heat", 1995, tmdb=949)])
    # Live-observed values: PlaybackMethod is not a bare enum, it can be 29 chars long.
    rows = [
        _play("lau", "Heat", "Transcode (v:direct a:direct)", "Jellyfin iOS",
              "iPhone de lauryn piolet", "2026-07-31 20:13:02.7902664", 2581, kind="Movie"),
    ]
    respx.post(QUERY_URL).mock(return_value=Response(200, json=_query_response(rows)))
    fn = _jellyfin_tool("jellyfin_item_history", monkeypatch)
    result = await fn(item="Heat")
    assert "Transcode (v:direct a:direct)" in result  # never cut mid-value
    assert "Transcode (v:" not in result.replace("Transcode (v:direct a:direct)", "")


@respx.mock
async def test_item_history_by_title(monkeypatch):
    _mock_users()
    _mock_media_catalog(_catalog())
    respx.post(QUERY_URL).mock(
        return_value=Response(
            200,
            json=_query_response(
                [_play("lau", "Collateral", "DirectPlay", "Web", "TV",
                       "2026-07-01 12:00:00.0000000", 5400, kind="Movie")]
            ),
        )
    )
    fn = _jellyfin_tool("jellyfin_item_history", monkeypatch)
    result = await fn(item="collateral")  # case-insensitive title
    assert "Collateral" in result and "1 play(s)" in result


@respx.mock
async def test_item_history_ambiguous_title_lists_candidates(monkeypatch):
    _mock_users()
    _mock_media_catalog(_catalog())
    query = respx.post(QUERY_URL).mock(return_value=Response(200, json=_query_response([])))
    fn = _jellyfin_tool("jellyfin_item_history", monkeypatch)
    result = await fn(item="bat")
    assert not query.called  # nothing queried while the reference is ambiguous
    assert result.lower().startswith("error")
    assert "Ambiguous" in result
    assert "Batman" in result and "Batman Begins" in result
    assert "44444444" in result  # candidate ids given so the user can disambiguate


@respx.mock
async def test_item_history_unknown_item_is_clear(monkeypatch):
    _mock_users()
    _mock_media_catalog(_catalog())
    query = respx.post(QUERY_URL).mock(return_value=Response(200, json=_query_response([])))
    fn = _jellyfin_tool("jellyfin_item_history", monkeypatch)
    result = await fn(item="totally unknown film")
    assert not query.called
    assert result.lower().startswith("error")
    assert "No item found matching" in result


@respx.mock
async def test_item_history_no_plays_is_clear(monkeypatch):
    _mock_users()
    _mock_media_catalog([_movie(HEAT_ID, "Heat", 1995, tmdb=949)])
    # Valid query, zero matching rows: the plugin says so in `message`, not via an error.
    respx.post(QUERY_URL).mock(
        return_value=Response(
            200,
            json={"colums": [], "results": [],
                  "message": "Query executed, no data returned.</br>Number of rows effected : 0"},
        )
    )
    fn = _jellyfin_tool("jellyfin_item_history", monkeypatch)
    result = await fn(item="Heat", days=30)
    assert "No playback recorded" in result
    assert "30 day(s)" in result
    assert "Error" not in result  # an empty result is not a failure


@respx.mock
async def test_item_history_series_expands_to_episode_ids(monkeypatch):
    _mock_users()
    series = {"Id": GREYS_ID, "Name": "Grey's Anatomy", "Type": "Series"}
    episodes = [
        {"Id": f"{i:032x}", "Name": f"Episode {i}", "Type": "Episode"} for i in range(1, 4)
    ]

    def items_handler(request):
        # parentId set -> the children fetch; otherwise the Movie,Series catalog.
        if request.url.params.get("parentId"):
            return Response(200, json={"Items": episodes, "TotalRecordCount": len(episodes)})
        return Response(200, json={"Items": [series], "TotalRecordCount": 1})

    respx.get(f"{BASE}/Items").mock(side_effect=items_handler)
    rows = [
        _play("lau", "Grey's Anatomy - s11e06 - x", "DirectStream", "SenPlayer", "Apple TV",
              "2026-07-31 20:13:02.7902664", 2581),
    ]
    query = respx.post(QUERY_URL).mock(return_value=Response(200, json=_query_response(rows)))
    fn = _jellyfin_tool("jellyfin_item_history", monkeypatch)
    result = await fn(item="Grey's Anatomy", days=90)

    sql = json.loads(query.calls.last.request.content)["CustomQueryString"]
    # A Series id matches ZERO rows in PlaybackActivity — its episodes' ids are what count.
    for ep in episodes:
        assert f"'{ep['Id']}'" in sql
    assert "Covers 4 item id(s)" in result  # the series + its 3 episodes
    assert "1 play(s)" in result


@respx.mock
async def test_item_history_plugin_missing_is_clear(monkeypatch):
    _mock_users()
    _mock_media_catalog([_movie(HEAT_ID, "Heat", 1995, tmdb=949)])
    respx.post(QUERY_URL).mock(return_value=Response(404, text=""))
    fn = _jellyfin_tool("jellyfin_item_history", monkeypatch)
    result = await fn(item="Heat")
    assert "Playback Reporting" in result
    assert "plugin" in result.lower()


@respx.mock
async def test_item_history_query_error_is_clean_without_stack_trace(monkeypatch):
    _mock_users()
    _mock_media_catalog([_movie(HEAT_ID, "Heat", 1995, tmdb=949)])
    # The plugin reports SQL errors as HTTP 200 + a message carrying a .NET stack trace.
    respx.post(QUERY_URL).mock(
        return_value=Response(
            200,
            json={"colums": [], "results": [],
                  "message": "Error Running Query</br>no such column: X<pre>Error: "
                             "SQLitePCL.pretty.SQLiteException: no such column: X\n   at "
                             "SQLitePCL.pretty.SQLiteException.Throw(Int32 rc)</pre>"},
        )
    )
    fn = _jellyfin_tool("jellyfin_item_history", monkeypatch)
    result = await fn(item="Heat")
    assert result.lower().startswith("error")
    assert "no such column: X" in result
    assert "SQLitePCL" not in result  # stack trace stripped
    assert "<pre>" not in result


async def test_item_play_history_rejects_non_guid_ids(client):
    # No HTTP call at all: ids that are not 32-hex never reach the SQL string.
    assert await client.item_play_history(["'; DROP TABLE PlaybackActivity; --"], days=7) == []
    assert await client.item_play_history([], days=7) == []


# ── Playback Reporting (plugin) ───────────────────────────────────────────────

ACTIVITY_URL = f"{BASE}/user_usage_stats/user_activity"


def _activity_row(user, count, seconds, item, client_name, last_seen, latest, play_time):
    """One /user_usage_stats/user_activity row, exactly the live shape (anonymized).

    Confirmed against Playback Reporting 17.0.0.0 / Jellyfin 10.11.10: one row PER USER,
    the item_* / client_name / latest_date / last_seen fields describing only that user's
    most recent play. last_seen and total_play_time really do carry a trailing space.
    """
    return {
        "latest_date": latest,
        "user_id": f"{user}00000000000000000000000000",
        "total_count": count,
        "total_time": seconds,
        "item_name": item,
        "client_name": client_name,
        "user_name": user,
        "has_image": False,
        "last_seen": last_seen,
        "total_play_time": play_time,
    }


def _activity():
    # Deliberately NOT in play-count order, so the tool's sort is what is being asserted.
    return [
        _activity_row(
            "bob", 12, 2548, "Hunter x Hunter - s01e29 - Awakening x And x Potential!",
            "Avalance", "9 hours 40 minutes ", "2026-07-31T11:49:54.7676941Z", "42 minutes ",
        ),
        _activity_row(
            "alice", 37, 68545, "Grey's Anatomy - s11e06 - Prendre le mal a la racine",
            "Apple TV", "1 hour 17 minutes ", "2026-07-31T20:13:02.7902664Z",
            "19 hours 2 minutes ",
        ),
    ]


def test_format_watch_time_units_and_overflow():
    assert _format_watch_time(2548) == "42m"
    assert _format_watch_time(68545) == "19h02m"
    assert _format_watch_time(307802) == "3d13h30m"
    # The plugin's 32-bit counter can wrap to a large negative; never render that as a
    # plausible duration (its own total_play_time says "< 1 minute", which is just as wrong).
    assert _format_watch_time(-2147441290) == "n/a"
    assert _format_watch_time(None) == "n/a"


@respx.mock
async def test_user_activity_sends_days_and_uses_header_auth(client):
    route = respx.get(ACTIVITY_URL).mock(return_value=Response(200, json=_activity()))
    rows = await client.user_activity(days=30)
    assert len(rows) == 2
    request = route.calls.last.request
    assert request.url.params["days"] == "30"
    # Same auth as the rest of the client — the plugin endpoint needs no ?api_key= variant.
    assert request.headers["authorization"] == f'MediaBrowser Token="{API_KEY}"'
    assert "api_key" not in request.url.params


@respx.mock
async def test_user_activity_404_raises_plugin_missing(client):
    respx.get(ACTIVITY_URL).mock(return_value=Response(404, text=""))
    with pytest.raises(JellyfinPluginMissingError, match="Playback Reporting"):
        await client.user_activity(days=7)


@respx.mock
async def test_playback_stats_summary(monkeypatch):
    respx.get(ACTIVITY_URL).mock(return_value=Response(200, json=_activity()))
    fn = _jellyfin_tool("jellyfin_playback_stats", monkeypatch)
    result = await fn(days=7)

    assert "last 7 day(s)" in result
    assert "2 active user(s)" in result
    assert "49 play(s)" in result  # 37 + 12
    # Per-user figures, with the duration formatted from total_time (not the plugin string).
    assert "19h02m" in result and "42m" in result
    assert "Grey's Anatomy" in result and "Hunter x Hunter" in result
    assert "[Apple TV]" in result  # client of the most recent play
    assert "1 hour 18 minutes" not in result  # last_seen is shown verbatim, not recomputed
    assert "1 hour 17 minutes" in result
    # Sorted by play count: alice (37) before bob (12), despite the payload order.
    assert result.index("alice") < result.index("bob")
    assert "n/a" not in result  # both totals are sane here


@respx.mock
async def test_playback_stats_flags_overflowed_watch_time(monkeypatch):
    rows = _activity()
    rows[0]["total_time"] = -2147441290  # live-observed int32 wrap for that user
    rows[0]["total_play_time"] = "< 1 minute"
    respx.get(ACTIVITY_URL).mock(return_value=Response(200, json=rows))
    fn = _jellyfin_tool("jellyfin_playback_stats", monkeypatch)
    result = await fn(days=7)

    assert "n/a" in result
    assert "Watch time unavailable for 1 user(s) (bob)" in result
    assert "< 1 minute" not in result  # the plugin's equally-wrong string is not shown
    assert "12" in result  # the play count is still reported


@respx.mock
async def test_playback_stats_empty_is_clear(monkeypatch):
    respx.get(ACTIVITY_URL).mock(return_value=Response(200, json=[]))
    fn = _jellyfin_tool("jellyfin_playback_stats", monkeypatch)
    result = await fn(days=7)
    assert "no playback activity" in result.lower()
    assert "7 day(s)" in result


@respx.mock
async def test_playback_stats_plugin_missing_is_clear_not_an_exception(monkeypatch):
    respx.get(ACTIVITY_URL).mock(return_value=Response(404, text=""))
    fn = _jellyfin_tool("jellyfin_playback_stats", monkeypatch)
    result = await fn(days=7)  # must not raise
    assert "Playback Reporting" in result
    assert "plugin" in result.lower()
    assert "404" in result


@respx.mock
async def test_playback_stats_rejects_zero_days(monkeypatch):
    route = respx.get(ACTIVITY_URL).mock(return_value=Response(200, json=[]))
    fn = _jellyfin_tool("jellyfin_playback_stats", monkeypatch)
    # days=0 makes the plugin return [] — that would read as "no activity", so it is refused.
    result = await fn(days=0)
    assert not route.called
    assert result.lower().startswith("error")


@respx.mock
async def test_delete_collection_dry_run_and_confirm(monkeypatch):
    _mock_users()
    boxset = {"Id": "dddd0000000000000000000000000000", "Name": "Neo-noir", "ChildCount": 3}
    respx.get(f"{BASE}/Items").mock(
        return_value=Response(200, json={"Items": [boxset], "TotalRecordCount": 1})
    )
    del_route = respx.delete(f"{BASE}/Items/dddd0000000000000000000000000000").mock(
        return_value=Response(204)
    )
    fn = _jellyfin_tool("jellyfin_delete_collection", monkeypatch)

    dry = await fn(collection_ref="Neo-noir", confirm=False)
    assert not del_route.called
    assert "DRY-RUN" in dry and "3 item" in dry

    result = await fn(collection_ref="dddd0000", confirm=True)
    assert del_route.called
    assert "Deleted collection" in result
