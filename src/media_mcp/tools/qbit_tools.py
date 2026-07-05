from mcp.server.fastmcp import FastMCP

from media_mcp.clients.qui import QuiClient, QuiClientError
from media_mcp.config import settings
from media_mcp.models import TorrentSummary, format_size, torrent_hardlink_note


def _client() -> QuiClient:
    return QuiClient(settings.qui_url, settings.qui_api_key, settings.qui_instance)


def _summary(raw: dict) -> TorrentSummary:
    return TorrentSummary(
        name=raw.get("name", ""),
        hash=raw.get("hash", ""),
        state=raw.get("state", ""),
        progress=raw.get("progress", 0.0),
        size=raw.get("size", 0),
        ratio=raw.get("ratio", 0.0),
        category=raw.get("category", ""),
    )


def _torrent_line(t: TorrentSummary) -> str:
    # Show the FULL hash so it can be copied straight into the other qbit_* tools.
    return (
        f"  [{t.state}] {t.name[:55]}  {t.hash}  {t.progress_pct}%  "
        f"{format_size(t.size)}  ratio={t.ratio:.2f}  cat={t.category or '-'}"
    )


def register_qbit_tools(mcp: FastMCP) -> None:
    @mcp.tool()
    async def qbit_list_instances() -> str:
        """List the qBittorrent instances managed by qui (id + name).

        Useful to find the id/name to put in QUI_INSTANCE when several exist.
        Access goes through qui, never qBittorrent directly.
        """
        try:
            async with _client() as c:
                instances = await c.list_instances()
        except QuiClientError as e:
            return f"Error: {e}"
        if not instances:
            return "No qBittorrent instances configured in qui."
        lines = [f"qui instances ({len(instances)}):"]
        for i in instances:
            state = "connected" if i.get("connected") else "disconnected"
            lines.append(
                f"  [{i.get('id')}] {i.get('name')}  {i.get('host', '')}  ({state})"
            )
        return "\n".join(lines)

    @mcp.tool()
    async def qbit_list_torrents(filter: str | None = None) -> str:
        """List torrents of the target qBittorrent instance (via qui).

        Optional `filter` is a free-text search (name, and also matches the hash).
        Each line shows: state, name, short hash, progress %, size, ratio, category.
        """
        try:
            async with _client() as c:
                instance_id = await c.resolve_instance_id()
                data = await c.list_torrents(instance_id, search=filter)
        except QuiClientError as e:
            return f"Error: {e}"
        torrents = [_summary(t) for t in (data.get("torrents") or [])]
        if not torrents:
            suffix = f" (filter='{filter}')" if filter else ""
            return f"No torrents found{suffix}."
        total = data.get("total") or len(torrents)
        header = f"Torrents ({len(torrents)} shown, {total} total):"
        lines = [header]
        lines.extend(_torrent_line(t) for t in torrents)
        return "\n".join(lines)

    @mcp.tool()
    async def qbit_get_torrent(hash: str) -> str:
        """Show a single torrent by its hash (via qui).

        The hash is the same value as the Sonarr/Radarr history downloadId, so this
        is the bridge to locate a release's torrent. Accepts a full hash or a unique
        prefix; matching is case-insensitive.
        """
        try:
            async with _client() as c:
                instance_id = await c.resolve_instance_id()
                raw = await c.resolve_torrent(instance_id, hash)
        except QuiClientError as e:
            return f"Error: {e}"
        t = _summary(raw)
        return (
            f"{t.name}\n"
            f"  hash={t.hash}\n"
            f"  state={t.state}  progress={t.progress_pct}%  size={format_size(t.size)}\n"
            f"  ratio={t.ratio:.2f}  category={t.category or '-'}"
        )

    @mcp.tool()
    async def qbit_pause(hash: str) -> str:
        """Pause a torrent by hash or unique prefix (via qui).

        Reversible, so no confirm required.
        """
        try:
            async with _client() as c:
                instance_id = await c.resolve_instance_id()
                raw = await c.resolve_torrent(instance_id, hash)
                await c.pause_torrent(instance_id, raw["hash"])
        except QuiClientError as e:
            return f"Error: {e}"
        return f"Paused '{_summary(raw).name}' ({raw['hash']})."

    @mcp.tool()
    async def qbit_resume(hash: str) -> str:
        """Resume a torrent by hash or unique prefix (via qui).

        Reversible, so no confirm required.
        """
        try:
            async with _client() as c:
                instance_id = await c.resolve_instance_id()
                raw = await c.resolve_torrent(instance_id, hash)
                await c.resume_torrent(instance_id, raw["hash"])
        except QuiClientError as e:
            return f"Error: {e}"
        return f"Resumed '{_summary(raw).name}' ({raw['hash']})."

    @mcp.tool()
    async def qbit_delete_torrent(
        hash: str,
        delete_files: bool = False,
        confirm: bool = False,
    ) -> str:
        """Remove a torrent from qBittorrent by hash (via qui).

        Accepts a full hash or a unique prefix.
        Set delete_files=True to also delete its downloaded files from disk.
        Set confirm=True to actually remove; omit or set False for a dry-run preview.
        """
        try:
            async with _client() as c:
                instance_id = await c.resolve_instance_id()
                raw = await c.resolve_torrent(instance_id, hash)
                t = _summary(raw)
                if not confirm:
                    effect = "The torrent will be removed from qBittorrent"
                    if delete_files:
                        effect += " AND its downloaded files will be deleted from disk"
                    lines = [
                        f"DRY-RUN: Would remove torrent '{t.name}' "
                        f"({t.hash}, {format_size(t.size)}).",
                        f"  - {effect}.",
                    ]
                    if delete_files:
                        lines.append(f"  {torrent_hardlink_note()}")
                    lines.append("Set confirm=True to proceed.")
                    return "\n".join(lines)
                await c.delete_torrent(instance_id, raw["hash"], delete_files=delete_files)
        except QuiClientError as e:
            return f"Error: {e}"
        files_note = " and its files were deleted from disk" if delete_files else ""
        return f"Removed torrent '{t.name}' ({t.hash}) from qBittorrent{files_note}."
