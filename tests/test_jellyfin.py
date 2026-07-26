import json

import pytest
import respx
from httpx import Response
from mcp.server.fastmcp import FastMCP

from media_mcp.clients.jellyfin import (
    JellyfinClient,
    JellyfinClientError,
    clear_user_cache,
    normalize_item_dto,
)
from media_mcp.jellyfin_resolve import (
    JellyfinResolutionError,
    normalize_title,
    resolve_movies,
    resolve_single,
)
from media_mcp.tools.jellyfin_tools import register_jellyfin_tools

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
