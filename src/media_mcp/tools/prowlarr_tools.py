from mcp.server.fastmcp import FastMCP

from media_mcp.clients.base import ArrClientError
from media_mcp.clients.prowlarr import ProwlarrClient
from media_mcp.config import settings
from media_mcp.models import HealthIssue, IndexerSummary, SearchResult, format_size


def _client() -> ProwlarrClient:
    return ProwlarrClient(settings.prowlarr_url, settings.prowlarr_api_key)


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
