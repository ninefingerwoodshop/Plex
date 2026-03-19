# Plex Media Stack - Storage Forecaster
# Analyzes drive usage and predicts when drives will fill up

import os
import sys
from collections import defaultdict
from api import get_plex_movies, get_plex_shows, get_radarr_movies, get_sonarr_series
from config import PLEX


def get_drive_info():
    """Get disk usage for all Plex library drives."""
    # Movie drives
    movie_drives = ["O:\\movies", "K:\\", "D:\\", "L:\\"]
    tv_drives = ["E:\\", "F:\\", "G:\\", "H:\\", "I:\\", "M:\\", "N:\\"]

    drives = {}
    all_drive_letters = set()

    for path in movie_drives + tv_drives:
        drive = path[:2].upper()
        if drive in all_drive_letters:
            continue
        all_drive_letters.add(drive)

        try:
            import shutil
            usage = shutil.disk_usage(drive + "\\")
            drives[drive] = {
                "path": drive,
                "total_gb": round(usage.total / (1024 ** 3), 1),
                "used_gb": round(usage.used / (1024 ** 3), 1),
                "free_gb": round(usage.free / (1024 ** 3), 1),
                "pct_used": round(100 * usage.used / usage.total, 1),
                "type": "movies" if path in movie_drives else "tv",
            }
        except Exception as e:
            drives[drive] = {
                "path": drive,
                "total_gb": 0,
                "used_gb": 0,
                "free_gb": 0,
                "pct_used": 0,
                "type": "movies" if path in movie_drives else "tv",
                "error": str(e),
            }

    return drives


def storage_report():
    """Generate storage usage report with forecasting."""
    print("\n" + "=" * 60)
    print("  STORAGE FORECASTER")
    print("=" * 60)

    drives = get_drive_info()

    # Get content counts per drive
    movies = get_plex_movies()
    shows = get_plex_shows()

    movie_by_drive = defaultdict(list)
    for m in movies:
        for media in m.get("Media", []):
            for part in media.get("Part", []):
                filepath = part.get("file", "")
                if filepath:
                    drive = filepath[:2].upper()
                    size = part.get("size", 0)
                    movie_by_drive[drive].append({
                        "title": m.get("title", ""),
                        "size_gb": round(size / (1024 ** 3), 2),
                    })

    # Radarr queue (pending downloads)
    radarr = get_radarr_movies()
    pending = [m for m in radarr if not m.get("hasFile") and m.get("monitored")]

    # Sonarr monitored
    sonarr = get_sonarr_series()
    sonarr_monitored = [s for s in sonarr if s.get("monitored")]

    print(f"\n  {'Drive':<6} {'Total':>8} {'Used':>8} {'Free':>8} {'Used%':>6} {'Type':<8} {'Items':>6}")
    print(f"  {'-'*6} {'-'*8} {'-'*8} {'-'*8} {'-'*6} {'-'*8} {'-'*6}")

    total_storage = 0
    total_used = 0
    total_free = 0

    for drive, info in sorted(drives.items()):
        if info.get("error"):
            print(f"  {drive:<6} {'ERROR':>8} -- {info['error']}")
            continue

        items = len(movie_by_drive.get(drive, []))
        bar_len = int(info["pct_used"] / 5)
        bar = "#" * bar_len + "." * (20 - bar_len)

        warning = ""
        if info["pct_used"] > 95:
            warning = " [!!!] CRITICAL"
        elif info["pct_used"] > 90:
            warning = " [!!] LOW SPACE"
        elif info["pct_used"] > 80:
            warning = " [!] FILLING UP"

        print(f"  {drive:<6} {info['total_gb']:>7.1f}G {info['used_gb']:>7.1f}G {info['free_gb']:>7.1f}G {info['pct_used']:>5.1f}% {info['type']:<8} {items:>6}{warning}")
        print(f"         [{bar}]")

        total_storage += info["total_gb"]
        total_used += info["used_gb"]
        total_free += info["free_gb"]

    print(f"\n  --- Totals ---")
    print(f"  Total storage: {total_storage:.1f} GB ({total_storage/1024:.2f} TB)")
    print(f"  Used: {total_used:.1f} GB ({total_used/1024:.2f} TB)")
    print(f"  Free: {total_free:.1f} GB ({total_free/1024:.2f} TB)")
    print(f"  Overall: {100*total_used/total_storage:.1f}% used")

    # Estimate growth rate
    avg_movie_size = 0
    movie_count = 0
    for drive_movies in movie_by_drive.values():
        for m in drive_movies:
            if m["size_gb"] > 0:
                avg_movie_size += m["size_gb"]
                movie_count += 1
    if movie_count:
        avg_movie_size /= movie_count

    print(f"\n  --- Growth Estimates ---")
    print(f"  Average movie size: {avg_movie_size:.1f} GB")
    print(f"  Pending Radarr downloads: {len(pending)} movies")
    if avg_movie_size > 0:
        pending_gb = len(pending) * avg_movie_size
        print(f"  Estimated pending storage needed: {pending_gb:.0f} GB")
        movies_until_full = int(total_free / avg_movie_size) if avg_movie_size else 0
        print(f"  Movies until full (all drives): ~{movies_until_full}")

    # Flag drives that are close to full
    critical = [d for d, i in drives.items() if i.get("pct_used", 0) > 90]
    if critical:
        print(f"\n  WARNING: Drives {', '.join(critical)} are above 90% capacity!")

    return {
        "drives": drives,
        "total_gb": round(total_storage, 1),
        "used_gb": round(total_used, 1),
        "free_gb": round(total_free, 1),
        "avg_movie_gb": round(avg_movie_size, 1),
        "pending_downloads": len(pending),
    }


if __name__ == "__main__":
    storage_report()
