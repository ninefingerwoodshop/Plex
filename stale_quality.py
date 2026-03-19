# Plex Media Stack - Stale Quality Cleaner
# Finds CAM/TELESYNC/low-quality copies that likely have better versions available

from api import get_plex_movies, get_radarr_movies


# Quality tiers (worst to best)
QUALITY_RANK = {
    "cam": 0, "telesync": 1, "telecine": 2, "ts": 1,
    "dvd": 3, "dvd-r": 3,
    "webrip-480p": 4, "webdl-480p": 4,
    "webrip-720p": 5, "webdl-720p": 5,
    "bluray-720p": 6,
    "webrip-1080p": 7, "webdl-1080p": 7,
    "bluray-1080p": 8, "remux-1080p": 9,
    "webdl-2160p": 10, "bluray-2160p": 11, "remux-2160p": 12,
}


def get_quality_rank(filename):
    """Determine quality rank from filename."""
    fn = filename.lower()
    # Check for known quality tags
    if "cam" in fn.split(".") or "cam" in fn.split("-") or fn.endswith("cam.mkv") or fn.endswith("cam.mp4"):
        return 0, "CAM"
    if "telesync" in fn or ".ts." in fn:
        return 1, "TELESYNC"
    if "telecine" in fn:
        return 2, "TELECINE"

    # Check structured quality names
    for quality, rank in sorted(QUALITY_RANK.items(), key=lambda x: -x[1]):
        if quality.lower() in fn.lower():
            return rank, quality.upper()

    return 5, "UNKNOWN"


def stale_quality_report():
    """Find movies with poor quality that might have upgrades available."""
    print("\n" + "=" * 60)
    print("  STALE QUALITY REPORT")
    print("=" * 60)

    movies = get_plex_movies()

    cam_copies = []
    telesync_copies = []
    low_quality = []
    dvd_quality = []

    for m in movies:
        title = m.get("title", "Unknown")
        year = m.get("year", "")
        label = f"{title} ({year})"

        for media in m.get("Media", []):
            height = media.get("height", 0)
            bitrate = media.get("bitrate", 0)
            for part in media.get("Part", []):
                filepath = part.get("file", "")
                rank, quality = get_quality_rank(filepath)
                size_mb = round(part.get("size", 0) / 1024 / 1024) if part.get("size") else 0

                entry = {
                    "title": title,
                    "year": year,
                    "label": label,
                    "quality": quality,
                    "rank": rank,
                    "height": height,
                    "bitrate": bitrate,
                    "size_mb": size_mb,
                    "file": filepath,
                }

                if rank == 0:
                    cam_copies.append(entry)
                elif rank == 1:
                    telesync_copies.append(entry)
                elif rank <= 3:
                    dvd_quality.append(entry)
                elif height > 0 and height < 720:
                    low_quality.append(entry)

    # Check Radarr for upgrade availability
    radarr_movies = get_radarr_movies()
    radarr_monitored = {}
    for rm in radarr_movies:
        title = rm.get("title", "").lower()
        radarr_monitored[title] = rm.get("monitored", False)

    if cam_copies:
        print(f"\n  [!!!] CAM Quality -- upgrade immediately ({len(cam_copies)}):")
        for m in sorted(cam_copies, key=lambda x: x["label"]):
            monitored = radarr_monitored.get(m["title"].lower(), None)
            status = ""
            if monitored is True:
                status = " (Radarr: monitored)"
            elif monitored is False:
                status = " (Radarr: NOT monitored!)"
            else:
                status = " (NOT in Radarr!)"
            print(f"    - {m['label']} -- {m['quality']} {m['height']}p{status}")
            print(f"      {m['file']}")

    if telesync_copies:
        print(f"\n  [!!] TELESYNC Quality -- should upgrade ({len(telesync_copies)}):")
        for m in sorted(telesync_copies, key=lambda x: x["label"]):
            monitored = radarr_monitored.get(m["title"].lower(), None)
            status = ""
            if monitored is True:
                status = " (Radarr: monitored)"
            elif monitored is False:
                status = " (Radarr: NOT monitored!)"
            else:
                status = " (NOT in Radarr!)"
            print(f"    - {m['label']} -- {m['quality']} {m['height']}p{status}")
            print(f"      {m['file']}")

    if dvd_quality:
        print(f"\n  [!] DVD Quality -- Bluray likely available ({len(dvd_quality)}):")
        for m in sorted(dvd_quality, key=lambda x: x["label"]):
            print(f"    - {m['label']} -- {m['quality']} {m['height']}p {m['size_mb']}MB")

    if low_quality:
        print(f"\n  [~] Sub-720p files ({len(low_quality)}):")
        for m in sorted(low_quality, key=lambda x: x["label"]):
            print(f"    - {m['label']} -- {m['height']}p {m['bitrate']}kbps")

    total = len(cam_copies) + len(telesync_copies) + len(dvd_quality) + len(low_quality)
    if total == 0:
        print("\n  All movies are good quality! No stale copies found.")
    else:
        print(f"\n  Summary: {len(cam_copies)} CAM, {len(telesync_copies)} TELESYNC, "
              f"{len(dvd_quality)} DVD, {len(low_quality)} sub-720p")

    return {
        "cam": cam_copies,
        "telesync": telesync_copies,
        "dvd": dvd_quality,
        "low_quality": low_quality,
    }


if __name__ == "__main__":
    stale_quality_report()
