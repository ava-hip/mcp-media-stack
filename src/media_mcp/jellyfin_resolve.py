"""Fuzzy resolution of user-supplied movie references to Jellyfin items.

The ``movies`` parameter of the write tools accepts a mixed list of strings: a numeric
tmdbId, a Jellyfin item id (or its unique prefix), or an approximate title. Resolution is a
CASCADE, stopping at the first level that yields a single match:

    1. tmdbId (exact, via ProviderIds.Tmdb)
    2. Jellyfin id (exact, or a unique hex prefix)
    3. Jellyfin Name (normalized)
    4. Jellyfin OriginalTitle (normalized)
    5. Radarr fallback — Radarr knows localized/alternate titles the Jellyfin library may
       index only under an English name; match the reference against Radarr's title /
       originalTitle / alternateTitles, take its tmdbId, then map back to Jellyfin by tmdbId.

The rule at EVERY level is identical: exactly one candidate -> matched; several -> ambiguous
(candidates surfaced, never a guess); none -> fall through to the next level.

Levels 1-4 are pure (this module has no HTTP dependency). Level 5 needs Radarr data, injected
as an async ``radarr_fetcher`` so the module stays testable; the fetcher is invoked at most
once per :func:`resolve_movies` call and only if some reference actually reaches level 5.
"""

import re
import unicodedata
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field


class JellyfinResolutionError(Exception):
    """A single reference (collection_ref / item_ref) could not be resolved to one item."""


# Leading articles stripped before matching a title (EN + FR + a few common others), so
# "Le Solitaire" and "Solitaire" collate the same.
_ARTICLES = {
    "the", "a", "an",
    "le", "la", "les", "l", "un", "une", "des", "du",
    "el", "los", "las", "der", "die", "das",
}

# A Jellyfin id is a 32-char hex GUID without dashes; unique prefixes (>= 4 chars) are
# accepted on input, so match anything hex-shaped of a plausible length.
_ID_RE = re.compile(r"[0-9a-fA-F]{4,32}")

# The resolution methods surfaced to the user (dry-run traceability, correctif 2c).
METHOD_TMDB = "tmdb"
METHOD_ID = "id"
METHOD_TITLE = "title"
METHOD_ORIGINAL_TITLE = "original-title"
METHOD_VIA_RADARR = "via-radarr"


def strip_accents(text: str) -> str:
    """Drop diacritics: 'Amélie' -> 'Amelie' (NFKD then remove combining marks)."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def normalize_title(text: str) -> str:
    """Casefold, strip accents/punctuation and a leading article; collapse whitespace."""
    if not text:
        return ""
    lowered = strip_accents(text).casefold()
    # Every run of non-alphanumerics becomes a single space (drops punctuation/apostrophes).
    cleaned = re.sub(r"[^0-9a-z]+", " ", lowered)
    tokens = cleaned.split()
    if len(tokens) > 1 and tokens[0] in _ARTICLES:
        tokens = tokens[1:]
    return " ".join(tokens)


def provider_id(item: dict, provider: str) -> str | None:
    """Return item.ProviderIds[<provider>] (case-insensitive key), or None.

    ProviderIds.Tmdb is the reliable bridge to Radarr's tmdbId — the equivalent of the
    downloadId/qBit-hash link — so titles are never matched on internally when a tmdbId is
    available.
    """
    providers = item.get("ProviderIds")
    if not isinstance(providers, dict):
        return None
    target = provider.casefold()
    for key, value in providers.items():
        if str(key).casefold() == target and value:
            return str(value)
    return None


def short_id(guid: str) -> str:
    """Truncate a 32-char Jellyfin GUID to its first 8 chars for table display."""
    return (guid or "")[:8]


def label_item(item: dict) -> str:
    """Compact 'Title (Year) [shortid]' label used in ambiguity / error messages."""
    year = item.get("ProductionYear") or "????"
    return f"{item.get('Name', '?')} ({year}) [{short_id(str(item.get('Id', '')))}]"


@dataclass
class MatchedMovie:
    """A resolved movie plus HOW it was resolved (for dry-run traceability)."""

    item: dict
    method: str  # tmdb | id | title | original-title | via-radarr
    radarr_title: str | None = None  # the matched Radarr title, only for via-radarr


@dataclass
class Resolution:
    """Outcome of resolving a list of movie references."""

    matched: list[MatchedMovie]
    ambiguous: list[tuple[str, list[dict]]]  # (original reference, candidate items)
    not_found: list[str]

    @property
    def fully_resolved(self) -> bool:
        return not self.ambiguous and not self.not_found


@dataclass
class _RefResult:
    """Internal per-reference resolution result."""

    status: str  # matched | ambiguous | not_found
    method: str | None = None
    candidates: list[dict] = field(default_factory=list)
    radarr_title: str | None = None


def _dedup(items: list[dict]) -> list[dict]:
    """Deduplicate items by Id, preserving first-seen order."""
    seen: set[str] = set()
    out: list[dict] = []
    for it in items:
        iid = str(it.get("Id", ""))
        if iid and iid not in seen:
            seen.add(iid)
            out.append(it)
    return out


class _Index:
    """Lookup structures built once over a fetched catalog of Jellyfin items.

    Name and OriginalTitle are indexed SEPARATELY so the cascade can try Name (level 3)
    before OriginalTitle (level 4) and report which one matched.
    """

    def __init__(self, items: list[dict]) -> None:
        self.by_tmdb: dict[str, list[dict]] = defaultdict(list)
        self.by_id: dict[str, dict] = {}
        self.id_pairs: list[tuple[str, dict]] = []
        self.by_name: dict[str, list[dict]] = defaultdict(list)
        self.by_original: dict[str, list[dict]] = defaultdict(list)
        for it in items:
            iid = str(it.get("Id", "")).lower()
            if iid:
                self.by_id[iid] = it
                self.id_pairs.append((iid, it))
            tmdb = provider_id(it, "Tmdb")
            if tmdb:
                self.by_tmdb[tmdb].append(it)
            name = normalize_title(it.get("Name") or "")
            if name:
                self.by_name[name].append(it)
            original = normalize_title(it.get("OriginalTitle") or "")
            if original:
                self.by_original[original].append(it)


def _title_candidates(index: dict[str, list[dict]], ref: str) -> list[dict]:
    """Exact-normalized match within one title index, else a conservative substring match."""
    qn = normalize_title(ref)
    if not qn:
        return []
    exact = _dedup(index.get(qn, []))
    if exact:
        return exact
    return _dedup(
        [it for title, items in index.items() for it in items if qn in title or title in qn]
    )


def _resolve_jellyfin(index: _Index, ref: str) -> _RefResult:
    """Resolve one reference against Jellyfin data only (cascade levels 1-4)."""
    raw = ref.strip()
    if not raw:
        return _RefResult("not_found")

    # 1. tmdbId (exact) — a purely numeric reference is a tmdbId first of all.
    if raw.isdigit():
        hits = _dedup(index.by_tmdb.get(raw, []))
        if len(hits) == 1:
            return _RefResult("matched", METHOD_TMDB, hits)
        if len(hits) > 1:
            return _RefResult("ambiguous", candidates=hits)
        # Not a known tmdbId: fall through (it may still be an all-digit id/title).

    # 2. Jellyfin id — exact, else a unique hex prefix.
    key = raw.lower()
    if _ID_RE.fullmatch(raw):
        exact = index.by_id.get(key)
        if exact is not None:
            return _RefResult("matched", METHOD_ID, [exact])
        prefix = _dedup([it for pid, it in index.id_pairs if pid.startswith(key)])
        if len(prefix) == 1:
            return _RefResult("matched", METHOD_ID, prefix)
        if len(prefix) > 1:
            return _RefResult("ambiguous", candidates=prefix)
        # No id prefix match: fall through to title matching.

    # 3. Jellyfin Name (normalized).
    name_c = _title_candidates(index.by_name, raw)
    if len(name_c) == 1:
        return _RefResult("matched", METHOD_TITLE, name_c)
    if len(name_c) > 1:
        return _RefResult("ambiguous", candidates=name_c)

    # 4. Jellyfin OriginalTitle (normalized).
    orig_c = _title_candidates(index.by_original, raw)
    if len(orig_c) == 1:
        return _RefResult("matched", METHOD_ORIGINAL_TITLE, orig_c)
    if len(orig_c) > 1:
        return _RefResult("ambiguous", candidates=orig_c)

    return _RefResult("not_found")


def _radarr_titles(movie: dict) -> list[str]:
    """All titles of a Radarr movie: title, originalTitle and alternateTitles[].title."""
    titles: list[str] = [movie.get("title"), movie.get("originalTitle")]
    for alt in movie.get("alternateTitles") or []:
        if isinstance(alt, dict):
            titles.append(alt.get("title"))
        elif isinstance(alt, str):  # defensive: tolerate a plain-string variant
            titles.append(alt)
    return [t for t in titles if t]


def _resolve_via_radarr(index: _Index, radarr_catalog: list[dict], ref: str) -> _RefResult:
    """Level 5: match the reference in Radarr, then map its tmdbId back to Jellyfin."""
    qn = normalize_title(ref)
    if not qn:
        return _RefResult("not_found")

    # Radarr movies whose any title matches the reference exactly (normalized).
    radarr_hits = [
        m for m in radarr_catalog if any(normalize_title(t) == qn for t in _radarr_titles(m))
    ]
    if not radarr_hits:
        return _RefResult("not_found")

    # Map to Jellyfin items by tmdbId, keeping the Radarr title for traceability.
    jf_by_id: dict[str, dict] = {}
    radarr_title_by_jf: dict[str, str] = {}
    for m in radarr_hits:
        tmdb = m.get("tmdbId")
        if not tmdb:
            continue
        for it in index.by_tmdb.get(str(tmdb), []):
            iid = str(it.get("Id", ""))
            if iid and iid not in jf_by_id:
                jf_by_id[iid] = it
                radarr_title_by_jf[iid] = m.get("title") or m.get("originalTitle") or ref
    jf_items = list(jf_by_id.values())
    if len(jf_items) == 1:
        iid = str(jf_items[0].get("Id", ""))
        return _RefResult("matched", METHOD_VIA_RADARR, jf_items, radarr_title_by_jf.get(iid))
    if len(jf_items) > 1:
        return _RefResult("ambiguous", candidates=jf_items)
    # Radarr knew the title but its tmdbId is not in the Jellyfin library.
    return _RefResult("not_found")


async def resolve_movies(
    jellyfin_catalog: list[dict],
    refs: list[str],
    radarr_fetcher: Callable[[], Awaitable[list[dict] | None]] | None = None,
) -> Resolution:
    """Resolve a mixed list of references via the cascade into matched/ambiguous/not_found.

    ``radarr_fetcher`` supplies the Radarr movie catalog for level 5; it is awaited AT MOST
    ONCE (result cached) and only when at least one reference falls through levels 1-4. A
    fetcher returning None (Radarr not configured or unreachable) simply disables level 5 —
    those references stay not_found, no exception.
    """
    index = _Index(jellyfin_catalog)
    radarr_fetched = False
    radarr_catalog: list[dict] | None = None

    matched: list[MatchedMovie] = []
    ambiguous: list[tuple[str, list[dict]]] = []
    not_found: list[str] = []
    seen: set[str] = set()

    for ref in refs:
        result = _resolve_jellyfin(index, ref)
        if result.status == "not_found" and radarr_fetcher is not None:
            if not radarr_fetched:
                radarr_catalog = await radarr_fetcher()  # lazy: only now, only once
                radarr_fetched = True
            if radarr_catalog is not None:
                result = _resolve_via_radarr(index, radarr_catalog, ref)

        if result.status == "matched":
            item = result.candidates[0]
            iid = str(item.get("Id", ""))
            if iid and iid in seen:
                continue  # same movie referenced twice — dedupe silently
            if iid:
                seen.add(iid)
            matched.append(
                MatchedMovie(
                    item=item,
                    method=result.method or "",
                    radarr_title=result.radarr_title,
                )
            )
        elif result.status == "ambiguous":
            ambiguous.append((ref, result.candidates))
        else:
            not_found.append(ref)

    return Resolution(matched=matched, ambiguous=ambiguous, not_found=not_found)


def resolve_single(catalog: list[dict], ref: str, *, kind: str = "item") -> dict:
    """Resolve a single reference (collection_ref / item_ref) to exactly one item.

    Uses the Jellyfin-only cascade (levels 1-4, no Radarr fallback). Raises
    :class:`JellyfinResolutionError`, listing candidates, rather than ever guessing.
    """
    index = _Index(catalog)
    result = _resolve_jellyfin(index, ref)
    if result.status == "matched":
        return result.candidates[0]
    if result.status == "ambiguous":
        listed = "; ".join(label_item(it) for it in result.candidates)
        raise JellyfinResolutionError(
            f"Ambiguous {kind} reference '{ref}' matches {len(result.candidates)}: {listed}. "
            "Use the id (or its 8-char prefix) to disambiguate."
        )
    raise JellyfinResolutionError(f"No {kind} found matching '{ref}'.")
