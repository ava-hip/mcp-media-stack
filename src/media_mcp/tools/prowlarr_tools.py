import re
from datetime import UTC, datetime

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel

from media_mcp.clients.base import ArrClientError
from media_mcp.clients.prowlarr import ProwlarrClient
from media_mcp.config import settings
from media_mcp.models import HealthIssue, IndexerSummary, SearchResult, format_size


def _client() -> ProwlarrClient:
    return ProwlarrClient(settings.prowlarr_url, settings.prowlarr_api_key)


# ── prowlarr_recent_releases helpers ──────────────────────────────────────────
# Pure functions, kept at module level so the scene-name parsing and the grouping can be
# unit-tested without a network. Everything below is heuristic by design and stays that way:
# a miss costs one absent work in a digest, which is not worth a real parsing library.

# Newznab top-level categories. ONE request per category is mandatory — see
# ProwlarrClient.recent_feed: the 100-result cap is per request, so asking for both at once
# halves the harvest.
_CAT_MOVIES = 2000
_CAT_TV = 5000
_CAT_LABELS = {_CAT_MOVIES: "movies", _CAT_TV: "tv"}
_KINDS: dict[str, tuple[int, ...]] = {
    "movie": (_CAT_MOVIES,),
    "tv": (_CAT_TV,),
    "all": (_CAT_MOVIES, _CAT_TV),
}

# Hard per-request result cap advertised by every indexer (limits default=100 max=100).
# Used only to detect that a feed never reached back past the window (see the saturation
# warning): when that fires, the escape hatch is to split the saturated category into its
# sub-categories — cat=5000 into 5030/5040/5045/5070 multiplies the budget by one cap per
# extra request. Not done today: on this instance the busiest pair sits at 88/100.
_FEED_CAP = 100

# Query parameters that carry a credential in Prowlarr release payloads. Three distinct
# leaks were observed: a 64-hex tracker `apikey` inside C411's `guid`, a `passkey` inside
# Torr9's `guid`, and — the nasty one — OUR OWN Prowlarr api key inside `downloadUrl` on
# every indexer. This tool emits none of those fields, but the mask still runs over the
# whole formatted output: no indexer whitelist, no assumption about which field is safe.
_SECRET_PARAM_RE = re.compile(
    r"\b(apikey|api_key|passkey|token|rss_key|authkey)=[^&\s\"'<>]*",
    re.IGNORECASE,
)

# Cinema rips: still in theatres, so seeders are artificially high for an unusable picture.
_JUNK_TAGS = frozenset(
    {
        "CAM", "CAMRIP", "HDCAM", "TS", "HDTS", "TELESYNC",
        "TC", "TELECINE", "SCREENER", "SCR", "DVDSCR", "BDSCR", "R5",
    }
)

# Scene-name separators. The junk scan also splits on "-" so a "-GROUP" suffix becomes its
# own token; the structural parse does not, keeping "H264-SUPPLY" in one piece.
_SEPARATOR_RE = re.compile(r"[.\s_]+")
_JUNK_SEPARATOR_RE = re.compile(r"[.\s_+-]+")

_SEASON_EPISODE_RE = re.compile(r"^S(\d{1,2})E(\d{1,3})", re.IGNORECASE)
_SEASON_ONLY_RE = re.compile(r"^S(\d{1,2})$", re.IGNORECASE)
_YEAR_RE = re.compile(r"^(19\d{2}|20\d{2})$")


def _mask_secrets(text: str) -> str:
    """Replace the value of every credential-bearing URL parameter with ``***``."""
    return _SECRET_PARAM_RE.sub(lambda m: f"{m.group(1)}=***", text)


def _is_junk(raw_title: str) -> bool:
    """True when the release name carries a cinema-rip tag.

    Tags are matched as whole scene tokens, never as substrings, so "TS" cannot fire on
    "GHOSTS". The first token is skipped because it is the title: a film actually called
    "Cam" or "TC" would otherwise be dropped on its own name.
    """
    tokens = [t for t in _JUNK_SEPARATOR_RE.split(raw_title) if t]
    return any(t.upper() in _JUNK_TAGS for t in tokens[1:])


class _ParsedName(BaseModel):
    """What a scene release name tells us about the work behind it."""

    kind: str  # "movie" | "tv"
    title: str
    year: int | None = None
    season: int | None = None
    episode: int | None = None  # None on a season pack


def _series_title(tokens: list[str], marker_index: int, max_year: int) -> str:
    """Title of a series release, with a disambiguating year folded out.

    C411 names episodes ``Futurama.1999.S11E01`` where The Old School names the very same
    episode ``Futurama.S11E01``. Keeping that year files one episode under two works, splits
    its seeders and burns two digest slots — observed live on Futurama S11E01/E02, whose 922
    real seeders showed up as 498 and 424. Naming a series by its first-air year is a common
    convention, so this is not an edge case.

    The year is only dropped when something is left to name the show: a series actually
    titled by a year keeps it (``1923.S02E01``), and so does a title whose trailing number
    is out of year range (``Blade.Runner.2049.S01E01``). The residual risk is merging two
    same-named shows from different eras, which needs both to publish the same SxxExx on the
    same day — far rarer than the split this prevents.
    """
    parts = tokens[:marker_index]
    if len(parts) > 1 and _YEAR_RE.match(parts[-1]) and int(parts[-1]) <= max_year:
        parts = parts[:-1]
    return " ".join(parts)


def _parse_release_name(raw_title: str) -> _ParsedName | None:
    """Extract (title, year) or (title, season, episode) from a scene release name.

    Returns None when the work cannot be identified — notably a film with no usable year,
    which is dropped rather than matched on title alone (too hazardous).
    """
    tokens = [t for t in _SEPARATOR_RE.split(raw_title) if t]
    max_year = datetime.now(UTC).year + 2

    # Series first: an SxxExx / Sxx marker is unambiguous, a bare 4-digit year is not.
    for i, token in enumerate(tokens):
        if i == 0:
            continue  # the first token is the title, never a marker
        episode_match = _SEASON_EPISODE_RE.match(token)
        if episode_match:
            return _ParsedName(
                kind="tv",
                title=_series_title(tokens, i, max_year),
                season=int(episode_match.group(1)),
                episode=int(episode_match.group(2)),
            )
        season_match = _SEASON_ONLY_RE.match(token)
        if season_match:
            return _ParsedName(
                kind="tv",
                title=_series_title(tokens, i, max_year),
                season=int(season_match.group(1)),
            )

    # Films: the year is the LAST year-shaped token, so "Blade.Runner.2049.2017" resolves to
    # 2017. The upper bound also stops a title number being mistaken for a year — a bare
    # "Blade.Runner.2049" yields no year at all and is dropped rather than dated 2049.
    year_index = None
    for i, token in enumerate(tokens):
        if i > 0 and _YEAR_RE.match(token) and int(token) <= max_year:
            year_index = i
    if year_index is None:
        return None
    return _ParsedName(
        kind="movie",
        title=" ".join(tokens[:year_index]),
        year=int(tokens[year_index]),
    )


def _normalize_title(title: str) -> str:
    """Fold a parsed title for grouping: case and punctuation carry no meaning here.

    This is what makes "GOAT.Rever.plus.haut" and "Goat.Rever.Plus.Haut" one work.
    """
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def _group_key(parsed: _ParsedName) -> str:
    """Identity of the work a release belongs to.

    A season pack gets its own key, distinct from any episode of that season: they are not
    the same download and merging them would invent a group nobody can grab.
    """
    normalized = _normalize_title(parsed.title)
    if parsed.kind == "movie":
        return f"movie|{normalized}|{parsed.year}"
    if parsed.episode is not None:
        return f"tv|{normalized}|s{parsed.season}|e{parsed.episode}"
    return f"tvpack|{normalized}|s{parsed.season}"


def _group_label(parsed: _ParsedName) -> str:
    if parsed.kind == "movie":
        return f"{parsed.title} ({parsed.year})"
    if parsed.episode is not None:
        return f"{parsed.title} S{parsed.season:02d}E{parsed.episode:02d}"
    return f"{parsed.title} S{parsed.season:02d} (season pack)"


def _seeders(release: dict) -> int:
    return int(release.get("seeders") or 0)


class _ReleaseGroup(BaseModel):
    """One work, and the best of the releases that carry it.

    Leechers are absent on purpose. They do not survive aggregation: the field is
    ``peers - seeders``, and The Old School reports ``peers == seeders`` so its leechers are
    structurally 0 while C411/Torr9 report a real peer total. Same reasoning as
    ``format_queue`` dropping per-item sizes from a pack — an incoherent field in the output
    gets used by a model sooner or later.
    """

    label: str
    seeders: int  # summed over the group: traction is split across qualities
    release_count: int
    newest_age_hours: float
    best_raw_title: str  # kept WHOLE — see the tool docstring on French titles
    best_size: int
    best_indexer: str
    tmdb_id: int = 0
    imdb_id: int = 0


def _group_releases(scored: list[tuple[dict, float]]) -> list[_ReleaseGroup]:
    """Merge releases of the same work, newest/most-seeded release kept as the exemplar.

    ``scored`` pairs each raw release with its age in hours. Episodes are NEVER summed
    across a season: S03E03..S03E07 of the same show stay five groups, because a single
    1800-seeder "House of the Dragon S03" group would correspond to no real release and
    the unit of a daily digest is the episode that just landed.
    """
    buckets: dict[str, list[tuple[dict, float]]] = {}
    labels: dict[str, str] = {}
    for release, age in scored:
        parsed = _parse_release_name(str(release.get("title", "")))
        if parsed is None:
            continue
        key = _group_key(parsed)
        buckets.setdefault(key, []).append((release, age))
        labels.setdefault(key, _group_label(parsed))

    groups: list[_ReleaseGroup] = []
    for key, members in buckets.items():
        # Exemplar = most seeded, then most recent. Its raw name is what lets the caller
        # recover the original work behind a French distribution title.
        best = max(members, key=lambda m: (_seeders(m[0]), -m[1]))[0]
        groups.append(
            _ReleaseGroup(
                label=labels[key],
                seeders=sum(_seeders(r) for r, _ in members),
                release_count=len(members),
                newest_age_hours=min(age for _, age in members),
                best_raw_title=str(best.get("title", "")),
                best_size=int(best.get("size") or 0),
                best_indexer=str(best.get("indexer", "")),
                # max() picks up an id from whichever release carries one: Torr9 reports 0
                # for every external id, C411 and The Old School report real ones.
                tmdb_id=max(int(r.get("tmdbId") or 0) for r, _ in members),
                imdb_id=max(int(r.get("imdbId") or 0) for r, _ in members),
            )
        )
    # Popularity first; ties broken by the most recent release, never by leechers.
    groups.sort(key=lambda g: (-g.seeders, g.newest_age_hours))
    return groups


def _release_age_hours(release: dict, now: datetime) -> float | None:
    """Age of a release in hours, from ``publishDate`` (100% present on every indexer).

    Never from ``age``, which is rounded to whole days and so cannot express a 24h window
    at all. ``ageMinutes`` is the fallback if a payload ever lacks ``publishDate``.
    """
    published = release.get("publishDate")
    if published:
        try:
            stamp = datetime.fromisoformat(str(published).replace("Z", "+00:00"))
        except ValueError:
            stamp = None
        if stamp is not None:
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=UTC)
            return (now - stamp).total_seconds() / 3600
    try:
        minutes = release.get("ageMinutes")
        return float(minutes) / 60 if minutes is not None else None
    except (TypeError, ValueError):
        return None


def _external_ids(group: _ReleaseGroup) -> str:
    """Render the ids that spare the caller a *arr lookup. tvdbId is never shown: every
    indexer on this instance reports 0 for it, so a column of zeros would be noise.
    """
    parts = []
    if group.tmdb_id:
        parts.append(f"tmdb={group.tmdb_id}")
    if group.imdb_id:
        parts.append(f"imdb=tt{group.imdb_id:07d}")
    return " ".join(parts) or "no external id"


def _top_categories(indexer: dict, limit: int = 5) -> list[str]:
    caps = indexer.get("capabilities") or {}
    cats = caps.get("categories") or [] if isinstance(caps, dict) else []
    names = [c.get("name", "") for c in cats if isinstance(c, dict) and c.get("name")]
    return names[:limit]


def _result_category(release: dict) -> str:
    cats = release.get("categories") or []
    for c in cats:
        if isinstance(c, dict) and c.get("name"):
            return c["name"]
    return "-"


def register_prowlarr_tools(mcp: FastMCP) -> None:
    @mcp.tool()
    async def prowlarr_system_status() -> str:
        """Return Prowlarr system status (version)."""
        try:
            async with _client() as c:
                data = await c.system_status()
        except ArrClientError as e:
            return f"Error: {e}"
        return f"Prowlarr {data.get('version', 'unknown')} ({data.get('appName', 'Prowlarr')})"

    @mcp.tool()
    async def prowlarr_list_indexers() -> str:
        """List indexers configured in Prowlarr.

        Per indexer: id, name, enabled (✓/✗), protocol, privacy, main categories, tags.
        Sorted by name.
        """
        try:
            async with _client() as c:
                data = await c.get_indexers()
        except ArrClientError as e:
            return f"Error: {e}"
        indexers = [
            IndexerSummary(
                id=i["id"],
                name=i.get("name", ""),
                enable=i.get("enable", False),
                protocol=i.get("protocol", ""),
                privacy=i.get("privacy", ""),
                categories=_top_categories(i),
                tags=i.get("tags") or [],
            )
            for i in (data or [])
        ]
        if not indexers:
            return "No indexers configured in Prowlarr."
        lines = [f"Indexers ({len(indexers)}):"]
        for ix in sorted(indexers, key=lambda x: x.name.lower()):
            flag = "✓" if ix.enable else "✗"
            cats = ", ".join(ix.categories) or "-"
            tags = f"  tags={ix.tags}" if ix.tags else ""
            lines.append(
                f"  [{ix.id}] {flag} {ix.name}  ({ix.protocol}/{ix.privacy})  "
                f"cats: {cats}{tags}"
            )
        return "\n".join(lines)

    @mcp.tool()
    async def prowlarr_indexer_status() -> str:
        """List indexers currently failing / temporarily disabled, with the retry time.

        Prowlarr disables an indexer after repeated failures until `disabledTill`. Status
        entries carry no textual reason (see prowlarr_health for that), so timestamps are
        shown. Returns a clear message when every indexer is healthy.
        """
        try:
            async with _client() as c:
                statuses = await c.indexer_status()
                indexers = await c.get_indexers()
        except ArrClientError as e:
            return f"Error: {e}"
        statuses = statuses or []
        if not statuses:
            return "All indexers healthy (none failing or temporarily disabled)."
        names = {i["id"]: i.get("name", "") for i in (indexers or [])}
        lines = [f"Failing / disabled indexers ({len(statuses)}):"]
        for s in statuses:
            iid = s.get("indexerId")
            name = names.get(iid, f"indexer {iid}")
            till = s.get("disabledTill") or "unknown"
            since = s.get("initialFailure") or "?"
            last = s.get("mostRecentFailure") or "?"
            lines.append(
                f"  [{iid}] {name}  disabled till {till}  "
                f"(failing since {since}, last failure {last})"
            )
        return "\n".join(lines)

    @mcp.tool()
    async def prowlarr_health() -> str:
        """Show Prowlarr global health warnings (type/source/message)."""
        try:
            async with _client() as c:
                data = await c.health()
        except ArrClientError as e:
            return f"Error: {e}"
        issues = [
            HealthIssue(
                type=h.get("type", "unknown"),
                source=h.get("source", ""),
                message=h.get("message", ""),
            )
            for h in (data or [])
        ]
        if not issues:
            return "No health issues reported by Prowlarr."
        lines = [f"Health issues ({len(issues)}):"]
        for i in issues:
            lines.append(f"  [{i.type}] {i.source}: {i.message}")
        return "\n".join(lines)

    @mcp.tool()
    async def prowlarr_test_indexer(indexer_id: int) -> str:
        """Test connectivity of a single indexer. Returns pass/fail + message(s).

        Benign side effect (no confirm). Unknown indexer_id -> clear message.
        """
        try:
            async with _client() as c:
                try:
                    indexer = await c.get_indexer(indexer_id)
                except ArrClientError as e:
                    if "404" in str(e):
                        return f"Error: indexer not found (id={indexer_id})."
                    raise
                name = indexer.get("name", indexer_id)
                result = await c.test_indexer(indexer)
        except ArrClientError as e:
            return f"Error: {e}"
        if result["is_valid"]:
            return f"PASS: indexer [{indexer_id}] '{name}' is reachable."
        reasons = "; ".join(result["failures"])
        return f"FAIL: indexer [{indexer_id}] '{name}' — {reasons}"

    @mcp.tool()
    async def prowlarr_test_all_indexers() -> str:
        """Test every indexer and summarize pass/fail, highlighting the failures."""
        try:
            async with _client() as c:
                results = await c.test_all_indexers()
                indexers = await c.get_indexers()
        except ArrClientError as e:
            return f"Error: {e}"
        results = results or []
        if not results:
            return "No indexers to test."
        names = {i["id"]: i.get("name", "") for i in (indexers or [])}
        passed = [r for r in results if r.get("isValid")]
        failed = [r for r in results if not r.get("isValid")]
        lines = [f"Tested {len(results)} indexer(s): {len(passed)} passed, {len(failed)} failed."]
        for r in failed:
            iid = r.get("id")
            name = names.get(iid, f"indexer {iid}")
            msgs = "; ".join(
                f.get("errorMessage", "") for f in (r.get("validationFailures") or [])
            )
            lines.append(f"  ✗ [{iid}] {name} — {msgs or 'test failed'}")
        for r in passed:
            iid = r.get("id")
            lines.append(f"  ✓ [{iid}] {names.get(iid, f'indexer {iid}')}")
        return "\n".join(lines)

    @mcp.tool()
    async def prowlarr_search(
        query: str,
        indexer_ids: list[int] | None = None,
        categories: list[int] | None = None,
        limit: int = 20,
    ) -> str:
        """Cross-indexer search via Prowlarr (works for any content: movies, ebooks,
        manga, software...). Results sorted by seeders descending.

        Per result: title, indexer, size, seeders/leechers, protocol, age, category, and
        the grab reference (guid + indexerId) to pass to prowlarr_grab.
        Optionally restrict to indexer_ids and/or categories (newznab category ids).
        """
        if not query.strip():
            return "Error: query is empty; provide a search term. No search performed."
        try:
            async with _client() as c:
                data = await c.search(query, indexer_ids, categories, limit)
        except ArrClientError as e:
            return f"Error: {e}"
        results = [
            SearchResult(
                title=r.get("title", ""),
                indexer=r.get("indexer", ""),
                indexer_id=r.get("indexerId", 0),
                size=r.get("size", 0),
                seeders=r.get("seeders", 0) or 0,
                leechers=r.get("leechers", 0) or 0,
                protocol=r.get("protocol", ""),
                age_days=r.get("age", 0) or 0,
                category=_result_category(r),
                guid=r.get("guid", ""),
            )
            for r in (data or [])
        ]
        if not results:
            return f"No results for '{query}'."
        # Prowlarr's limit is not a hard cap: sort by seeders desc and trim client-side.
        results.sort(key=lambda x: x.seeders, reverse=True)
        results = results[:limit]
        lines = [f"Results for '{query}' ({len(results)} shown, by seeders):"]
        for r in results:
            lines.append(
                f"  {r.title[:70]}\n"
                f"     {r.indexer}  {format_size(r.size)}  "
                f"S/L={r.seeders}/{r.leechers}  {r.protocol}  {r.age_days}d  cat={r.category}\n"
                f"     grab: guid={r.guid}  indexerId={r.indexer_id}"
            )
        return "\n".join(lines)

    @mcp.tool()
    async def prowlarr_recent_releases(
        hours: int = 24,
        kind: str = "all",
        min_seeders: int = 3,
        top: int = 15,
        indexer_ids: list[int] | None = None,
        include_junk: bool = False,
    ) -> str:
        """What was published in the last N hours, merged per work — the "what's new" feed.

        Answers a question prowlarr_search structurally cannot: prowlarr_search ranks by
        seeders with no notion of date, and Prowlarr's `limit` is not a hard cap, so
        date-filtering its output afterwards returns almost nothing. This tool reads each
        indexer's empty-query feed instead — the indexer's own "latest releases" view,
        ordered by publication — and keeps what falls inside the window. Use prowlarr_search
        to find a KNOWN title; use this to find out WHAT came out.

        Two further differences, both deliberate:

        - Output is one line per WORK, not per release. A film is (title, year); an episode
          is (title, season, episode); a season pack is its own group. Episodes are never
          summed across a season — S03E03..S03E07 stay five entries, since a single
          1800-seeder "S03" group matches no real download and the unit of a daily digest is
          the episode that just landed. A group's popularity is the SUM of its releases'
          seeders, because traction splits across qualities.
        - No grab reference is returned, and this is not an oversight. Discovery and
          acquisition are split: this tool identifies works, then prowlarr_search resolves a
          chosen title into the guid + indexerId that prowlarr_grab needs. The guid and
          downloadUrl fields are withheld because they carry credentials in clear (a
          tracker's apikey or passkey, and Prowlarr's own api key), and this output is built
          to be forwarded to chat clients. Do not add them back to "make it chainable" —
          re-run prowlarr_search on the title you picked.

        Per group: parsed title with year (films) or SxxExx / Sxx (series), total seeders,
        release count, hours since the most recent release, external ids, then the FULL raw
        name of the best release with its size and indexer. Raw names are never truncated:
        these trackers are mostly French (VFF/VF2/VFQ/TRUEFRENCH/MULTi) so the parsed title
        is usually a French distribution title, and the raw name is what lets you recover the
        original work. tmdbId/imdbId appear when the indexer supplies them and let you skip
        radarr_lookup_movie; tvdbId is never shown, every indexer here reports 0.

        Args:
            hours: window width. Only releases published inside it are considered.
            kind: "movie" (cat 2000), "tv" (cat 5000) or "all".
            min_seeders: minimum TOTAL seeders for a group to be listed (0 keeps everything).
            top: how many groups to show, most seeded first.
            indexer_ids: restrict to these indexers; default is every configured indexer.
            include_junk: keep cinema rips (CAM/TS/TC/HDTS/TELESYNC/SCREENER/DVDSCR/R5),
                which are dropped by default — high seeders, unusable picture.

        Read the per-indexer trailer: it reports releases fetched vs kept in window plus the
        age of the newest one. An indexer returning 0 releases is a technical failure (down,
        expired passkey); one returning releases that are all older than the window is just a
        quiet feed. Read-only — no grab, no side effect, no confirm.
        """
        if kind not in _KINDS:
            return (
                f"Error: unknown kind '{kind}'. Use 'movie', 'tv' or 'all'. "
                "No search performed."
            )
        if hours < 1:
            return "Error: hours must be >= 1. No search performed."
        if top < 1:
            return "Error: top must be >= 1. No search performed."
        categories = _KINDS[kind]

        try:
            async with _client() as c:
                data = await c.get_indexers()
                known = {
                    i["id"]: i.get("name", "") or f"indexer {i['id']}" for i in (data or [])
                }
                if not known:
                    return "No indexers configured in Prowlarr."
                if indexer_ids:
                    wanted = [i for i in indexer_ids if i in known]
                    unknown = [i for i in indexer_ids if i not in known]
                    if not wanted:
                        return (
                            "Error: no such indexer id(s): "
                            f"{', '.join(str(i) for i in unknown)}. Configured ids: "
                            f"{', '.join(str(i) for i in sorted(known))}. No search performed."
                        )
                else:
                    wanted, unknown = sorted(known), []

                # Sequential on purpose: 3 indexers x 2 categories measured at ~6s total,
                # well inside the client timeout, and one failing indexer must not abort the
                # others — hence the per-feed except rather than one around the whole loop.
                feeds: dict[tuple[int, int], list[dict]] = {}
                failures: dict[int, str] = {}
                for iid in wanted:
                    for cat in categories:
                        try:
                            feeds[(iid, cat)] = await c.recent_feed(iid, cat) or []
                        except ArrClientError as e:
                            feeds[(iid, cat)] = []
                            failures[iid] = str(e)
        except ArrClientError as e:
            return f"Error: {e}"

        now = datetime.now(UTC)
        in_window: list[tuple[dict, float]] = []
        junk_dropped = 0
        raw_total = 0
        trailer: list[str] = []
        warnings: list[str] = []

        for iid in wanted:
            counts: list[str] = []
            newest: float | None = None
            raw_for_indexer = 0
            for cat in categories:
                rows = feeds[(iid, cat)]
                raw_for_indexer += len(rows)
                ages: list[float] = []
                kept_here = 0
                for release in rows:
                    age = _release_age_hours(release, now)
                    if age is None:
                        continue
                    ages.append(age)
                    if age > hours:
                        continue
                    if not include_junk and _is_junk(str(release.get("title", ""))):
                        junk_dropped += 1
                        continue
                    in_window.append((release, age))
                    kept_here += 1
                counts.append(f"{_CAT_LABELS[cat]} {len(rows)}/{kept_here}")
                if ages:
                    newest = min(ages) if newest is None else min(newest, min(ages))
                    # The feed never reached back past the window AND it is full, so
                    # releases inside the window may exist beyond the cap and are simply
                    # absent. Reported rather than passed off as a complete window.
                    if max(ages) <= hours and len(rows) >= _FEED_CAP:
                        warnings.append(
                            f"[{iid}] {known[iid]} {_CAT_LABELS[cat]}: all {len(rows)} "
                            f"releases fetched are inside the {hours}h window, and a feed "
                            f"returns at most {_FEED_CAP} — this window is probably "
                            "TRUNCATED. Lower `hours` to see the rest."
                        )
            raw_total += raw_for_indexer
            newest_text = f"newest {newest:.1f}h" if newest is not None else "newest n/a"
            trailer.append(f"  [{iid}] {known[iid]}  {'  '.join(counts)}  {newest_text}")
            if iid in failures:
                warnings.append(f"[{iid}] {known[iid]}: fetch FAILED — {failures[iid]}")
            elif raw_for_indexer == 0:
                warnings.append(
                    f"[{iid}] {known[iid]}: 0 raw releases returned. That is a technical "
                    "anomaly (tracker down, expired passkey), not a quiet feed."
                )
        if unknown:
            warnings.append(
                f"ignored unknown indexer id(s): {', '.join(str(i) for i in unknown)}"
            )

        def _trailer(lines: list[str]) -> str:
            lines.append("Indexers (fetched/in-window per category, newest release):")
            lines.extend(trailer)
            if warnings:
                lines.append("Warnings:")
                lines.extend(f"  ! {w}" for w in warnings)
            return _mask_secrets("\n".join(lines))

        # Two empty cases that must not read alike: nothing fetched at all is a failure,
        # whereas nothing recent among what was fetched is an ordinary quiet day.
        if raw_total == 0:
            return _trailer(
                [
                    "No release data at all: every queried indexer returned 0 releases. "
                    "This is a technical anomaly, not a quiet day — check the warnings, "
                    "prowlarr_indexer_status and prowlarr_test_all_indexers.",
                ]
            )
        if not in_window:
            junk_note = f" ({junk_dropped} cinema rip(s) also dropped)" if junk_dropped else ""
            return _trailer(
                [
                    f"Quiet {hours}h: none of the {raw_total} releases fetched was published "
                    f"inside the window{junk_note}. The indexers answered normally — see the "
                    "newest-release age per indexer below.",
                ]
            )

        groups = _group_releases(in_window)
        grouped_releases = sum(g.release_count for g in groups)
        kept = [g for g in groups if g.seeders >= min_seeders]
        shown = kept[:top]

        lines = [
            f"Recent releases — last {hours}h, kind={kind}, min_seeders={min_seeders} — "
            f"{len(shown)} of {len(kept)} work(s), from {len(in_window)} release(s) in "
            f"window / {raw_total} fetched:"
        ]
        for rank, group in enumerate(shown, 1):
            lines.append(
                f"  {rank:>2}. {group.label}  S={group.seeders}  ×{group.release_count}  "
                f"{group.newest_age_hours:.1f}h  {_external_ids(group)}"
            )
            lines.append(
                f"      {group.best_raw_title}  {format_size(group.best_size)}  "
                f"{group.best_indexer}"
            )
        if not shown:
            lines.append(
                f"  (no work reached min_seeders={min_seeders}; {len(groups)} were found — "
                "lower min_seeders to see them)"
            )

        # Anything discarded is stated, so a short list never passes for an exhaustive one.
        dropped: list[str] = []
        if len(kept) > len(shown):
            dropped.append(f"{len(kept) - len(shown)} work(s) below the top {top}")
        if len(groups) > len(kept):
            dropped.append(f"{len(groups) - len(kept)} under min_seeders={min_seeders}")
        if junk_dropped:
            dropped.append(f"{junk_dropped} cinema rip(s)")
        unparsed = len(in_window) - grouped_releases
        if unparsed:
            dropped.append(f"{unparsed} release(s) whose name could not be parsed")
        if dropped:
            lines.append(f"Not shown: {', '.join(dropped)}.")
        return _trailer(lines)

    @mcp.tool()
    async def prowlarr_grab(guid: str, indexer_id: int, confirm: bool = False) -> str:
        """Send a release to Prowlarr's download client (acquisition side effect).

        No category is passed: Prowlarr routes the download and its qBittorrent category
        comes from Prowlarr's Mapped Categories (configured in the Prowlarr UI).
        Set confirm=True to actually grab; omit or set False for a dry-run preview.
        Use the guid + indexerId shown by prowlarr_search.
        """
        try:
            async with _client() as c:
                clients = await c.list_download_clients()
                enabled = [d for d in (clients or []) if d.get("enable")]
                if not enabled:
                    return (
                        "Error: no download client configured in Prowlarr. Add one "
                        "(e.g. qBittorrent) in the Prowlarr UI first — the grab has nowhere "
                        "to go. No action taken."
                    )
                dest = ", ".join(d.get("name", "") for d in enabled)
                if not confirm:
                    return (
                        f"DRY-RUN: Would grab release (guid={guid}) from indexer id={indexer_id} "
                        f"→ Prowlarr download client: {dest}.\n"
                        "The final qBittorrent category is decided by Prowlarr's Mapped "
                        "Categories (books/pc/other…), not by this call.\n"
                        "Set confirm=True to proceed."
                    )
                result = await c.grab(guid, indexer_id)
        except ArrClientError as e:
            return f"Error: {e}"
        if result["ok"]:
            return f"Grabbed release (guid={guid}) → sent to Prowlarr download client ({dest})."
        return f"FAIL: grab rejected — {result['message']}"
