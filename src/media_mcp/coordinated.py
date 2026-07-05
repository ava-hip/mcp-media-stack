"""Coordinated purge service: delete a season's / movie's library files (Sonarr/Radarr)
AND the matching torrents in qBittorrent-via-qui, cross-seeds included, in one gesture.

Heavy orchestration lives here; the tools in tools/coordinated_tools.py stay thin.
"""

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from pydantic import BaseModel

from media_mcp.clients.base import ArrClientError
from media_mcp.clients.qui import QuiClient, QuiClientError
from media_mcp.models import QbitTarget, format_size, purge_disk_note

LOOSE_MATCH_TYPES = {"name", "release"}


class _HasIdSize(Protocol):
    id: int
    size: int


def extract_download_ids(events: list[dict[str, Any]]) -> list[str]:
    """Extract unique, non-empty downloadId values from history events.

    Deduplicated case-insensitively (a season pack shares one downloadId across all its
    episodes) while preserving first-seen order and the original casing.
    """
    seen: set[str] = set()
    out: list[str] = []
    for e in events:
        dlid = e.get("downloadId")
        if not dlid:
            continue
        key = dlid.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(dlid)
    return out


async def delete_each(
    delete_one: Callable[[int], Awaitable[None]],
    items: list[_HasIdSize],
) -> tuple[int, int, int]:
    """Delete each item by id, tolerating per-item failures.

    Returns (deleted_count, freed_bytes, failed_count). Shared by the standalone
    delete tools and the coordinated purge so the deletion core is not duplicated.
    """
    deleted = freed = failed = 0
    for it in items:
        try:
            await delete_one(it.id)
            deleted += 1
            freed += it.size
        except ArrClientError:
            failed += 1
    return deleted, freed, failed


class QbitTargetPlan(BaseModel):
    targets: list[QbitTarget]
    missing_origins: list[str]  # origin hashes not found in qBittorrent
    excluded_loose: int
    cross_seed_available: bool


async def collect_qbit_targets(
    qui: QuiClient,
    instance_id: int,
    download_ids: list[str],
    include_loose_matches: bool,
) -> QbitTargetPlan:
    """Resolve origin torrents and their cross-seed siblings into a dedup'd target set."""
    by_hash: dict[str, QbitTarget] = {}
    missing: list[str] = []
    cross_ok = True

    for dlid in download_ids:
        try:
            origin = await qui.resolve_torrent(instance_id, dlid)
        except QuiClientError:
            # Origin recorded in history but gone from qBittorrent (already removed).
            missing.append(dlid)
            continue

        oh = str(origin.get("hash", ""))
        key = oh.lower()
        # Origin takes precedence over any prior sibling entry with the same hash.
        by_hash[key] = QbitTarget(
            name=origin.get("name", ""),
            hash=oh,
            category=origin.get("category", ""),
            size=origin.get("size", 0),
            kind="origin",
            match_type=None,
        )

        if not cross_ok:
            continue
        try:
            matches = await qui.local_matches(instance_id, oh, strict=True)
        except QuiClientError:
            cross_ok = False
            continue
        for m in matches:
            mh = str(m.get("hash", ""))
            if not mh:
                continue
            mk = mh.lower()
            if mk in by_hash:  # already an origin or sibling
                continue
            by_hash[mk] = QbitTarget(
                name=m.get("name", ""),
                hash=mh,
                category=m.get("category", ""),
                size=m.get("size", 0),
                kind="sibling",
                match_type=m.get("match_type"),
            )

    targets = list(by_hash.values())
    excluded = 0
    if not include_loose_matches:
        kept: list[QbitTarget] = []
        for t in targets:
            if t.kind == "sibling" and t.match_type in LOOSE_MATCH_TYPES:
                excluded += 1
            else:
                kept.append(t)
        targets = kept

    return QbitTargetPlan(
        targets=targets,
        missing_origins=missing,
        excluded_loose=excluded,
        cross_seed_available=cross_ok,
    )


def _target_line(t: QbitTarget) -> str:
    kind = t.kind if t.kind == "origin" else f"sibling (match_type={t.match_type})"
    return (
        f"  - {kind:<28} {t.hash}  cat={t.category or '-'}  "
        f"{format_size(t.size)}  {t.name[:45]}"
    )


def _qbit_notes(plan: QbitTargetPlan, include_loose_matches: bool) -> list[str]:
    notes: list[str] = []
    if plan.missing_origins:
        notes.append(
            f"  Note: {len(plan.missing_origins)} origin torrent(s) recorded in history "
            "are no longer in qBittorrent (already removed): "
            f"{', '.join(plan.missing_origins)}."
        )
    if plan.excluded_loose:
        notes.append(
            f"  Note: {plan.excluded_loose} loose cross-seed match(es) (match_type "
            "name/release) excluded; set include_loose_matches=True to include them."
        )
    if not plan.cross_seed_available:
        notes.append(
            "  Note: cross-seed lookup unavailable — only origin torrents are targeted."
        )
    return notes


async def run_purge(
    *,
    service_name: str,
    library_label: str,
    library_file_count: int,
    library_total_size: int,
    download_ids: list[str],
    delete_library_files: Callable[[], Awaitable[tuple[int, int, int]]],
    make_qui: Callable[[], QuiClient],
    delete_torrent_files: bool,
    include_loose_matches: bool,
    confirm: bool,
) -> str:
    """Build the plan and either preview (dry-run) or execute the coordinated purge."""
    plan: QbitTargetPlan | None = None
    qbit_error: str | None = None
    if download_ids:
        try:
            async with make_qui() as q:
                instance_id = await q.resolve_instance_id()
                plan = await collect_qbit_targets(
                    q, instance_id, download_ids, include_loose_matches
                )
        except QuiClientError as e:
            qbit_error = str(e)

    if not confirm:
        return _render_dry_run(
            service_name=service_name,
            library_label=library_label,
            library_file_count=library_file_count,
            library_total_size=library_total_size,
            download_ids=download_ids,
            plan=plan,
            qbit_error=qbit_error,
            delete_torrent_files=delete_torrent_files,
            include_loose_matches=include_loose_matches,
        )

    # ── Execute: library files first, then a single bulk-action on all hashes ──
    lib_deleted, lib_freed, lib_failed = await delete_library_files()

    qbit_line = ""
    if plan and plan.targets:
        hashes = [t.hash for t in plan.targets]
        try:
            async with make_qui() as q:
                instance_id = await q.resolve_instance_id()
                await q.bulk_action(
                    instance_id, "delete", hashes, delete_files=delete_torrent_files
                )
            suffix = " (with files)" if delete_torrent_files else ""
            qbit_line = f"- qBittorrent: removed {len(hashes)} torrent(s){suffix}."
        except QuiClientError as e:
            qbit_line = f"- qBittorrent: FAILED to remove torrents: {e}"
    elif qbit_error:
        qbit_line = f"- qBittorrent: skipped (could not reach qui: {qbit_error})."
    elif not download_ids:
        qbit_line = (
            "- qBittorrent: skipped — no downloadId in history; manage torrents manually."
        )
    else:
        qbit_line = "- qBittorrent: no torrents matched."

    lines = [f"Purge of {library_label} executed:"]
    lib_summary = (
        f"- {service_name}: deleted {lib_deleted}/{library_file_count} file(s), "
        f"{format_size(lib_freed)} freed"
    )
    if lib_failed:
        lib_summary += f" ({lib_failed} failed)"
    lines.append(lib_summary + ".")
    lines.append(qbit_line)
    if plan:
        lines.extend(_qbit_notes(plan, include_loose_matches))
    lines.append(purge_disk_note())
    return "\n".join(lines)


def _render_dry_run(
    *,
    service_name: str,
    library_label: str,
    library_file_count: int,
    library_total_size: int,
    download_ids: list[str],
    plan: QbitTargetPlan | None,
    qbit_error: str | None,
    delete_torrent_files: bool,
    include_loose_matches: bool,
) -> str:
    lines = [f"DRY-RUN: purge {library_label}."]

    # Library side
    if library_file_count:
        lines.append(
            f"{service_name} library: {library_file_count} file(s), "
            f"{format_size(library_total_size)}."
        )
    else:
        lines.append(f"{service_name} library: no files (nothing to delete on this side).")

    # qBittorrent side
    if not download_ids:
        lines.append(
            "qBittorrent (via qui): no downloadId found in history — cannot locate "
            "torrents. Only the library files will be deleted; manage torrents manually."
        )
    elif qbit_error:
        lines.append(f"qBittorrent (via qui): unavailable ({qbit_error}). No torrents targeted.")
    elif plan is None or not plan.targets:
        lines.append("qBittorrent (via qui): no matching torrents found.")
        if plan:
            lines.extend(_qbit_notes(plan, include_loose_matches))
    else:
        lines.append(f"qBittorrent (via qui): {len(plan.targets)} torrent(s) targeted:")
        lines.extend(_target_line(t) for t in plan.targets)
        lines.extend(_qbit_notes(plan, include_loose_matches))
        effect = (
            "removed from qBittorrent AND their downloaded files deleted from disk"
            if delete_torrent_files
            else "removed from qBittorrent (downloaded files kept)"
        )
        lines.append(f"Effect: torrents will be {effect}.")

    if library_file_count or (plan and plan.targets):
        lines.append(purge_disk_note())
    lines.append("Set confirm=True to proceed.")
    return "\n".join(lines)
