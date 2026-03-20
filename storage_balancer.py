# Plex Media Stack - Smart Storage Balancer
# Balances media across multiple drives, detects orphans, suggests moves.

import os
import sys
import shutil
import json
import logging
from datetime import datetime
from collections import defaultdict
from api import (
    radarr_get, radarr_put, sonarr_get, sonarr_put,
    get_plex_movies, get_plex_shows,
)
from config import STORAGE, PLEX

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_FILE = os.path.join(os.path.dirname(__file__), "storage_balancer.log")
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
)
log = logging.getLogger("storage_balancer")

MOVE_LOG = os.path.join(os.path.dirname(__file__), "move_history.json")

MEDIA_EXTENSIONS = {
    ".mkv", ".mp4", ".avi", ".m4v", ".ts", ".wmv", ".flv", ".mov",
    ".srt", ".sub", ".idx", ".ass", ".ssa", ".nfo",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _drive_letter(path):
    """Extract the drive root (e.g. 'O:\\') from a Windows path."""
    drive = os.path.splitdrive(path)[0]
    if drive:
        return drive.upper() + "\\"
    return ""


def _drive_available(drive):
    """Check whether a drive letter is mounted / accessible on Windows."""
    try:
        shutil.disk_usage(drive)
        return True
    except (OSError, FileNotFoundError):
        return False


def _bytes_to_gb(b):
    return round(b / (1024 ** 3), 2)


def _load_move_history():
    if os.path.exists(MOVE_LOG):
        with open(MOVE_LOG, "r", encoding="utf-8") as fh:
            return json.load(fh)
    return []


def _save_move_history(history):
    with open(MOVE_LOG, "w", encoding="utf-8") as fh:
        json.dump(history, fh, indent=2, default=str)


def _dir_size(path):
    """Total size of a directory tree in bytes."""
    total = 0
    try:
        for dirpath, _dirnames, filenames in os.walk(path):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                try:
                    total += os.path.getsize(fp)
                except OSError:
                    pass
    except OSError:
        pass
    return total


# ---------------------------------------------------------------------------
# 1. Drive usage
# ---------------------------------------------------------------------------

def get_drive_usage():
    """Return disk-usage stats for every configured media drive.

    Returns a list of dicts:
        drive, total_gb, used_gb, free_gb, pct_used, media_type
    """
    results = []
    for media_type, drives in [("movies", STORAGE["movie_drives"]),
                               ("tv", STORAGE["tv_drives"])]:
        for drive in drives:
            if not _drive_available(drive):
                results.append({
                    "drive": drive,
                    "total_gb": 0,
                    "used_gb": 0,
                    "free_gb": 0,
                    "pct_used": 0,
                    "media_type": media_type,
                    "online": False,
                })
                continue
            usage = shutil.disk_usage(drive)
            total = _bytes_to_gb(usage.total)
            used = _bytes_to_gb(usage.used)
            free = _bytes_to_gb(usage.free)
            pct = round(usage.used / usage.total * 100, 1) if usage.total else 0
            results.append({
                "drive": drive,
                "total_gb": total,
                "used_gb": used,
                "free_gb": free,
                "pct_used": pct,
                "media_type": media_type,
                "online": True,
            })
    return results


# ---------------------------------------------------------------------------
# 2. Media per drive
# ---------------------------------------------------------------------------

def _get_radarr_movies_cached():
    """Fetch all Radarr movies (with file info)."""
    return radarr_get("/movie")


def _get_sonarr_series_cached():
    """Fetch all Sonarr series."""
    return sonarr_get("/series")


def get_media_per_drive():
    """Count movies/shows on each drive with total sizes.

    Returns dict:  drive -> {count, size_gb, media_type, items: [{title, size_gb, path}]}
    """
    result = defaultdict(lambda: {"count": 0, "size_gb": 0.0, "media_type": "", "items": []})

    # --- Movies via Radarr ---
    try:
        movies = _get_radarr_movies_cached()
    except Exception as exc:
        log.warning("Could not reach Radarr: %s", exc)
        movies = []

    for m in movies:
        path = m.get("path", "")
        title = m.get("title", "Unknown")
        size_bytes = 0
        if m.get("movieFile"):
            size_bytes = m["movieFile"].get("size", 0)
        elif m.get("sizeOnDisk"):
            size_bytes = m["sizeOnDisk"]

        drive = _drive_letter(path)
        if not drive:
            continue
        size_gb = _bytes_to_gb(size_bytes)
        entry = result[drive]
        entry["count"] += 1
        entry["size_gb"] = round(entry["size_gb"] + size_gb, 2)
        entry["media_type"] = "movies"
        entry["items"].append({"title": title, "size_gb": size_gb, "path": path})

    # --- TV via Sonarr ---
    try:
        series = _get_sonarr_series_cached()
    except Exception as exc:
        log.warning("Could not reach Sonarr: %s", exc)
        series = []

    for s in series:
        path = s.get("path", "")
        title = s.get("title", "Unknown")
        size_bytes = s.get("statistics", {}).get("sizeOnDisk", 0)

        drive = _drive_letter(path)
        if not drive:
            continue
        size_gb = _bytes_to_gb(size_bytes)
        entry = result[drive]
        entry["count"] += 1
        entry["size_gb"] = round(entry["size_gb"] + size_gb, 2)
        entry["media_type"] = "tv"
        entry["items"].append({"title": title, "size_gb": size_gb, "path": path})

    return dict(result)


# ---------------------------------------------------------------------------
# 3. Suggest moves
# ---------------------------------------------------------------------------

def suggest_moves(strategy="balance"):
    """Analyse drives and suggest file moves.

    Strategies:
        "balance"     - move from fullest to emptiest (same media type)
        "consolidate" - move small/scattered items to the primary drive
        "free_space"  - evacuate the most-at-risk drive

    Returns list of dicts:
        source_path, dest_path, title, size_gb, reason
    """
    usage = {d["drive"]: d for d in get_drive_usage() if d["online"]}
    media = get_media_per_drive()
    suggestions = []

    if strategy == "balance":
        suggestions = _strategy_balance(usage, media)
    elif strategy == "consolidate":
        suggestions = _strategy_consolidate(usage, media)
    elif strategy == "free_space":
        suggestions = _strategy_free_space(usage, media)
    else:
        log.warning("Unknown strategy: %s", strategy)

    return suggestions


def _strategy_balance(usage, media):
    """Move items from the fullest drive to the emptiest of the same media type."""
    suggestions = []
    for media_type, drives_cfg in [("movies", STORAGE["movie_drives"]),
                                   ("tv", STORAGE["tv_drives"])]:
        typed = [(d, usage[d]) for d in drives_cfg if d in usage]
        if len(typed) < 2:
            continue

        # Sort by pct_used descending
        typed.sort(key=lambda x: x[1]["pct_used"], reverse=True)
        fullest_drive, fullest_info = typed[0]
        emptiest_drive, emptiest_info = typed[-1]

        spread = fullest_info["pct_used"] - emptiest_info["pct_used"]
        if spread < 10:
            continue  # already balanced enough

        # Pick items from fullest drive, fit into emptiest
        items = media.get(fullest_drive, {}).get("items", [])
        items_sorted = sorted(items, key=lambda x: x["size_gb"], reverse=True)
        available_gb = emptiest_info["free_gb"]

        for item in items_sorted:
            if item["size_gb"] <= 0:
                continue
            if item["size_gb"] > available_gb - 50:  # keep 50 GB headroom
                continue
            folder_name = os.path.basename(item["path"].rstrip("\\/"))
            dest_path = os.path.join(emptiest_drive, folder_name)
            suggestions.append({
                "source_path": item["path"],
                "dest_path": dest_path,
                "title": item["title"],
                "size_gb": item["size_gb"],
                "reason": (f"Balance {media_type}: {fullest_drive} at "
                           f"{fullest_info['pct_used']}% -> {emptiest_drive} at "
                           f"{emptiest_info['pct_used']}%"),
            })
            available_gb -= item["size_gb"]
            # Stop once we would bring them within 10% of each other (approx)
            moved_total = sum(s["size_gb"] for s in suggestions)
            if moved_total > (spread / 100 * fullest_info["total_gb"] / 2):
                break

    return suggestions


def _strategy_consolidate(usage, media):
    """Move small/scattered items to the primary (first) drive of their type."""
    suggestions = []
    for media_type, drives_cfg in [("movies", STORAGE["movie_drives"]),
                                   ("tv", STORAGE["tv_drives"])]:
        primary = drives_cfg[0]
        primary_info = usage.get(primary)
        if not primary_info:
            continue
        available_gb = primary_info["free_gb"]

        for drive in drives_cfg[1:]:
            items = media.get(drive, {}).get("items", [])
            # Sort smallest first for consolidation
            items_sorted = sorted(items, key=lambda x: x["size_gb"])
            for item in items_sorted:
                if item["size_gb"] <= 0:
                    continue
                if item["size_gb"] > available_gb - 50:
                    continue
                folder_name = os.path.basename(item["path"].rstrip("\\/"))
                dest_path = os.path.join(primary, folder_name)
                suggestions.append({
                    "source_path": item["path"],
                    "dest_path": dest_path,
                    "title": item["title"],
                    "size_gb": item["size_gb"],
                    "reason": f"Consolidate to primary {media_type} drive {primary}",
                })
                available_gb -= item["size_gb"]

    return suggestions


def _strategy_free_space(usage, media):
    """Find the most-full drive and suggest moving items off it."""
    suggestions = []
    all_drives = [(d, info) for d, info in usage.items()]
    if not all_drives:
        return suggestions
    all_drives.sort(key=lambda x: x[1]["pct_used"], reverse=True)
    danger_drive, danger_info = all_drives[0]

    media_type = danger_info["media_type"]
    drives_cfg = (STORAGE["movie_drives"] if media_type == "movies"
                  else STORAGE["tv_drives"])

    # Find the emptiest drive of the same type
    candidates = [(d, usage[d]) for d in drives_cfg
                  if d in usage and d != danger_drive]
    if not candidates:
        return suggestions
    candidates.sort(key=lambda x: x[1]["free_gb"], reverse=True)
    target_drive, target_info = candidates[0]

    items = media.get(danger_drive, {}).get("items", [])
    items_sorted = sorted(items, key=lambda x: x["size_gb"], reverse=True)
    available_gb = target_info["free_gb"]
    target_pct = 85  # try to get danger drive below this

    moved_gb = 0
    need_gb = (danger_info["pct_used"] - target_pct) / 100 * danger_info["total_gb"]

    for item in items_sorted:
        if moved_gb >= need_gb:
            break
        if item["size_gb"] <= 0:
            continue
        if item["size_gb"] > available_gb - 50:
            continue
        folder_name = os.path.basename(item["path"].rstrip("\\/"))
        dest_path = os.path.join(target_drive, folder_name)
        suggestions.append({
            "source_path": item["path"],
            "dest_path": dest_path,
            "title": item["title"],
            "size_gb": item["size_gb"],
            "reason": (f"Free space on {danger_drive} "
                       f"({danger_info['pct_used']}% used)"),
        })
        moved_gb += item["size_gb"]
        available_gb -= item["size_gb"]

    return suggestions


# ---------------------------------------------------------------------------
# 4. Execute move
# ---------------------------------------------------------------------------

def execute_move(source_path, dest_drive, update_arr=True, confirmed=False):
    """Move a media folder/file to another drive.

    Args:
        source_path: Full path to the movie/show folder.
        dest_drive:  Target drive root (e.g. "K:\\").
        update_arr:  If True, update Radarr/Sonarr with the new path.
        confirmed:   Must be True to actually perform the move.

    Returns dict with keys: success, source, dest, message
    """
    result = {"success": False, "source": source_path, "dest": "", "message": ""}

    if not os.path.exists(source_path):
        result["message"] = f"Source does not exist: {source_path}"
        log.error(result["message"])
        return result

    if not _drive_available(dest_drive):
        result["message"] = f"Destination drive not available: {dest_drive}"
        log.error(result["message"])
        return result

    folder_name = os.path.basename(source_path.rstrip("\\/"))
    dest_path = os.path.join(dest_drive, folder_name)
    result["dest"] = dest_path

    if os.path.exists(dest_path):
        result["message"] = f"Destination already exists: {dest_path}"
        log.error(result["message"])
        return result

    if not confirmed:
        result["message"] = (
            f"DRY RUN - would move:\n"
            f"  {source_path}\n"
            f"  -> {dest_path}\n"
            f"  Pass confirmed=True to execute."
        )
        log.info("Dry run: %s -> %s", source_path, dest_path)
        return result

    # --- Actually move ---
    log.info("MOVING: %s -> %s", source_path, dest_path)
    try:
        shutil.move(source_path, dest_path)
    except Exception as exc:
        result["message"] = f"Move failed: {exc}"
        log.error(result["message"])
        return result

    log.info("Move complete: %s -> %s", source_path, dest_path)

    # Update Radarr or Sonarr
    if update_arr:
        _update_arr_path(source_path, dest_path)

    # Record in move history
    history = _load_move_history()
    history.append({
        "timestamp": datetime.now().isoformat(),
        "source": source_path,
        "dest": dest_path,
        "updated_arr": update_arr,
    })
    _save_move_history(history)

    result["success"] = True
    result["message"] = f"Successfully moved to {dest_path}"
    return result


def _update_arr_path(old_path, new_path):
    """Update Radarr or Sonarr with a new path after a move."""
    old_drive = _drive_letter(old_path)

    # Check if this is a movie drive or TV drive
    if old_drive in [d.upper() for d in STORAGE["movie_drives"]]:
        _update_radarr_path(old_path, new_path)
    elif old_drive in [d.upper() for d in STORAGE["tv_drives"]]:
        _update_sonarr_path(old_path, new_path)
    else:
        log.warning("Drive %s not in known movie/tv drives, skipping arr update", old_drive)


def _update_radarr_path(old_path, new_path):
    """Find the Radarr movie at old_path and update it to new_path."""
    try:
        movies = radarr_get("/movie")
        for m in movies:
            if os.path.normpath(m.get("path", "")).lower() == os.path.normpath(old_path).lower():
                m["path"] = new_path
                radarr_put(f"/movie/{m['id']}", m)
                log.info("Updated Radarr movie %s -> %s", m.get("title"), new_path)
                return
        log.warning("No Radarr movie found at path: %s", old_path)
    except Exception as exc:
        log.error("Failed to update Radarr: %s", exc)


def _update_sonarr_path(old_path, new_path):
    """Find the Sonarr series at old_path and update it to new_path."""
    try:
        series = sonarr_get("/series")
        for s in series:
            if os.path.normpath(s.get("path", "")).lower() == os.path.normpath(old_path).lower():
                s["path"] = new_path
                sonarr_put(f"/series/{s['id']}", s)
                log.info("Updated Sonarr series %s -> %s", s.get("title"), new_path)
                return
        log.warning("No Sonarr series found at path: %s", old_path)
    except Exception as exc:
        log.error("Failed to update Sonarr: %s", exc)


# ---------------------------------------------------------------------------
# 5. Largest items
# ---------------------------------------------------------------------------

def get_largest_items(media_type="movies", limit=20):
    """Find the largest movies or TV shows by size on disk.

    Returns sorted list of dicts: title, size_gb, path, quality
    """
    items = []

    if media_type == "movies":
        try:
            movies = _get_radarr_movies_cached()
        except Exception:
            movies = []
        for m in movies:
            size_bytes = 0
            quality = "Unknown"
            if m.get("movieFile"):
                size_bytes = m["movieFile"].get("size", 0)
                quality = (m["movieFile"].get("quality", {})
                           .get("quality", {}).get("name", "Unknown"))
            elif m.get("sizeOnDisk"):
                size_bytes = m["sizeOnDisk"]

            if size_bytes > 0:
                items.append({
                    "title": m.get("title", "Unknown"),
                    "size_gb": _bytes_to_gb(size_bytes),
                    "path": m.get("path", ""),
                    "quality": quality,
                })
    else:  # tv
        try:
            series = _get_sonarr_series_cached()
        except Exception:
            series = []
        for s in series:
            stats = s.get("statistics", {})
            size_bytes = stats.get("sizeOnDisk", 0)
            if size_bytes > 0:
                items.append({
                    "title": s.get("title", "Unknown"),
                    "size_gb": _bytes_to_gb(size_bytes),
                    "path": s.get("path", ""),
                    "quality": s.get("qualityProfileId", "Unknown"),
                })

    items.sort(key=lambda x: x["size_gb"], reverse=True)
    return items[:limit]


# ---------------------------------------------------------------------------
# 6. Orphaned files
# ---------------------------------------------------------------------------

def get_orphaned_files():
    """Find media files/folders on drives that are not tracked by Radarr/Sonarr.

    Returns list of dicts: path, size_gb, media_type, drive
    """
    # Build sets of known paths (normalised lowercase)
    known_paths = set()

    try:
        for m in _get_radarr_movies_cached():
            p = m.get("path", "")
            if p:
                known_paths.add(os.path.normpath(p).lower())
    except Exception:
        pass

    try:
        for s in _get_sonarr_series_cached():
            p = s.get("path", "")
            if p:
                known_paths.add(os.path.normpath(p).lower())
    except Exception:
        pass

    orphans = []

    for media_type, drives in [("movies", STORAGE["movie_drives"]),
                               ("tv", STORAGE["tv_drives"])]:
        for drive in drives:
            if not _drive_available(drive):
                continue
            try:
                entries = os.listdir(drive)
            except OSError:
                continue
            for entry in entries:
                full_path = os.path.join(drive, entry)
                if not os.path.isdir(full_path):
                    # Check if it is a media file sitting at root
                    ext = os.path.splitext(entry)[1].lower()
                    if ext not in MEDIA_EXTENSIONS:
                        continue
                norm = os.path.normpath(full_path).lower()
                if norm not in known_paths:
                    # Might be a system folder - skip common ones
                    if entry.lower() in ("system volume information", "$recycle.bin",
                                         "recycler", "found.000", "msdownld.tmp"):
                        continue
                    size_bytes = _dir_size(full_path) if os.path.isdir(full_path) else 0
                    try:
                        if not os.path.isdir(full_path):
                            size_bytes = os.path.getsize(full_path)
                    except OSError:
                        pass
                    orphans.append({
                        "path": full_path,
                        "size_gb": _bytes_to_gb(size_bytes),
                        "media_type": media_type,
                        "drive": drive,
                    })

    orphans.sort(key=lambda x: x["size_gb"], reverse=True)
    return orphans


# ---------------------------------------------------------------------------
# 7. Balance report
# ---------------------------------------------------------------------------

def get_balance_report():
    """Comprehensive storage report.

    Returns dict with:
        drive_usage, imbalance_score (0-100), suggested_moves,
        largest_movies, largest_tv, orphan_count, orphans_size_gb, timestamp
    """
    usage = get_drive_usage()

    # Imbalance score: std-dev of pct_used within each media type, scaled 0-100
    def _imbalance_for(media_type):
        pcts = [d["pct_used"] for d in usage
                if d["media_type"] == media_type and d["online"]]
        if len(pcts) < 2:
            return 0
        mean = sum(pcts) / len(pcts)
        variance = sum((p - mean) ** 2 for p in pcts) / len(pcts)
        std = variance ** 0.5
        # Scale: 0 std = 0 score, 30+ std = 100 score
        return min(100, round(std / 30 * 100))

    movie_imbalance = _imbalance_for("movies")
    tv_imbalance = _imbalance_for("tv")
    overall_imbalance = round((movie_imbalance + tv_imbalance) / 2)

    suggestions = suggest_moves(strategy="balance")
    largest_movies = get_largest_items("movies", 10)
    largest_tv = get_largest_items("tv", 10)
    orphans = get_orphaned_files()
    orphan_size = round(sum(o["size_gb"] for o in orphans), 2)

    return {
        "timestamp": datetime.now().isoformat(),
        "drive_usage": usage,
        "imbalance_score": overall_imbalance,
        "movie_imbalance": movie_imbalance,
        "tv_imbalance": tv_imbalance,
        "suggested_moves": suggestions,
        "largest_movies": largest_movies,
        "largest_tv": largest_tv,
        "orphan_count": len(orphans),
        "orphans_size_gb": orphan_size,
    }


# ---------------------------------------------------------------------------
# 8. Estimate balance after moves
# ---------------------------------------------------------------------------

def estimate_balance_after_moves(moves):
    """Given a list of proposed moves, project drive usage afterwards.

    Args:
        moves: list of dicts with source_path, dest_path, size_gb

    Returns list of drive-usage dicts (same shape as get_drive_usage)
        with projected values.
    """
    usage = {d["drive"]: dict(d) for d in get_drive_usage()}

    for move in moves:
        src_drive = _drive_letter(move["source_path"])
        dst_drive = _drive_letter(move["dest_path"])
        size = move.get("size_gb", 0)

        if src_drive in usage:
            usage[src_drive]["used_gb"] = round(usage[src_drive]["used_gb"] - size, 2)
            usage[src_drive]["free_gb"] = round(usage[src_drive]["free_gb"] + size, 2)
            total = usage[src_drive]["total_gb"]
            usage[src_drive]["pct_used"] = (
                round(usage[src_drive]["used_gb"] / total * 100, 1) if total else 0
            )

        if dst_drive in usage:
            usage[dst_drive]["used_gb"] = round(usage[dst_drive]["used_gb"] + size, 2)
            usage[dst_drive]["free_gb"] = round(usage[dst_drive]["free_gb"] - size, 2)
            total = usage[dst_drive]["total_gb"]
            usage[dst_drive]["pct_used"] = (
                round(usage[dst_drive]["used_gb"] / total * 100, 1) if total else 0
            )

    return list(usage.values())


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_usage_table(drives):
    print(f"\n{'Drive':<8} {'Type':<8} {'Total GB':>10} {'Used GB':>10} "
          f"{'Free GB':>10} {'Used %':>8} {'Status':<8}")
    print("-" * 72)
    for d in drives:
        status = "ONLINE" if d.get("online", True) else "OFFLINE"
        print(f"{d['drive']:<8} {d['media_type']:<8} {d['total_gb']:>10.1f} "
              f"{d['used_gb']:>10.1f} {d['free_gb']:>10.1f} "
              f"{d['pct_used']:>7.1f}% {status:<8}")


def _print_moves(moves):
    if not moves:
        print("\nNo moves suggested - storage is balanced.")
        return
    print(f"\n{'#':<4} {'Title':<40} {'Size GB':>8}  Move")
    print("-" * 100)
    for i, m in enumerate(moves, 1):
        src = _drive_letter(m["source_path"])
        dst = _drive_letter(m["dest_path"])
        print(f"{i:<4} {m['title'][:39]:<40} {m['size_gb']:>8.1f}  "
              f"{src} -> {dst}  ({m['reason']})")
    total = sum(m["size_gb"] for m in moves)
    print(f"\nTotal to move: {total:.1f} GB across {len(moves)} items")


def _print_largest(items, label):
    print(f"\n--- {label} ---")
    print(f"{'#':<4} {'Title':<45} {'Size GB':>8} {'Quality':<15} {'Drive':<6}")
    print("-" * 85)
    for i, item in enumerate(items, 1):
        drive = _drive_letter(item["path"])
        q = str(item["quality"])[:14]
        print(f"{i:<4} {item['title'][:44]:<45} {item['size_gb']:>8.1f} "
              f"{q:<15} {drive:<6}")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"

    if cmd == "report":
        print("=" * 72)
        print("  PLEX STORAGE BALANCE REPORT")
        print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 72)

        report = get_balance_report()
        _print_usage_table(report["drive_usage"])

        print(f"\nImbalance Score: {report['imbalance_score']}/100 "
              f"(movies: {report['movie_imbalance']}, "
              f"tv: {report['tv_imbalance']})")
        print(f"  0 = perfectly balanced, 100 = severely imbalanced")

        _print_moves(report["suggested_moves"])
        _print_largest(report["largest_movies"], "Largest Movies")
        _print_largest(report["largest_tv"], "Largest TV Shows")

        print(f"\nOrphaned items: {report['orphan_count']} "
              f"({report['orphans_size_gb']:.1f} GB)")

    elif cmd == "suggest":
        strategy = sys.argv[2] if len(sys.argv) > 2 else "balance"
        moves = suggest_moves(strategy=strategy)
        _print_moves(moves)

        if moves:
            estimated = estimate_balance_after_moves(moves)
            print("\nProjected usage after moves:")
            _print_usage_table(estimated)

    elif cmd == "orphans":
        orphans = get_orphaned_files()
        if not orphans:
            print("No orphaned files found.")
        else:
            print(f"\n{'#':<4} {'Path':<60} {'Size GB':>8} {'Type':<8}")
            print("-" * 85)
            for i, o in enumerate(orphans, 1):
                print(f"{i:<4} {o['path'][:59]:<60} {o['size_gb']:>8.1f} "
                      f"{o['media_type']:<8}")
            total = sum(o["size_gb"] for o in orphans)
            print(f"\nTotal orphaned: {len(orphans)} items, {total:.1f} GB")

    elif cmd == "largest":
        media = sys.argv[2] if len(sys.argv) > 2 else "movies"
        limit = int(sys.argv[3]) if len(sys.argv) > 3 else 20
        items = get_largest_items(media, limit)
        label = "Largest Movies" if media == "movies" else "Largest TV Shows"
        _print_largest(items, label)

    elif cmd == "usage":
        _print_usage_table(get_drive_usage())

    elif cmd == "drives":
        media = get_media_per_drive()
        for drive, info in sorted(media.items()):
            print(f"\n{drive} ({info['media_type']}) - "
                  f"{info['count']} items, {info['size_gb']:.1f} GB")

    else:
        print("Usage: storage_balancer.py [report|suggest|orphans|largest|usage|drives]")
        print("  report              Full balance report (default)")
        print("  suggest [strategy]  Suggest moves (balance|consolidate|free_space)")
        print("  orphans             List orphaned files not in Radarr/Sonarr")
        print("  largest [type] [n]  Largest items (movies|tv) (default: 20)")
        print("  usage               Drive usage table only")
        print("  drives              Media count per drive")


if __name__ == "__main__":
    main()
