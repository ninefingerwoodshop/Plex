# Plex Media Stack - Subtitle Downloader
# Scans Plex library for movies missing subtitles and downloads them

import os
import sys
from api import get_plex_movies, get_plex_shows, plex_get
from config import PLEX

try:
    from babelfish import Language
    from subliminal import download_best_subtitles, save_subtitles, scan_video
    HAS_SUBLIMINAL = True
except ImportError:
    HAS_SUBLIMINAL = False


def get_movies_without_subs():
    """Find all Plex movies that have no subtitle streams."""
    movies = get_plex_movies()
    no_subs = []

    for m in movies:
        title = m.get("title", "Unknown")
        year = m.get("year", "")
        media_list = m.get("Media", [])
        if not media_list:
            continue

        media = media_list[0]
        has_subs = False
        file_path = None

        for part in media.get("Part", []):
            file_path = part.get("file", "")
            for stream in part.get("Stream", []):
                if stream.get("streamType") == 3:
                    has_subs = True
                    break
            if has_subs:
                break

        if not has_subs and file_path:
            # Check if a .srt already exists next to the file
            base = os.path.splitext(file_path)[0]
            srt_exists = (
                os.path.exists(f"{base}.srt")
                or os.path.exists(f"{base}.en.srt")
                or os.path.exists(f"{base}.eng.srt")
            )
            no_subs.append({
                "title": title,
                "year": year,
                "file": file_path,
                "srt_exists_on_disk": srt_exists,
            })

    return no_subs


def download_subs_for_file(file_path, languages=None):
    """Download subtitles for a single video file using subliminal."""
    if not HAS_SUBLIMINAL:
        print("  ERROR: subliminal not installed. Run: pip install subliminal")
        return False

    if languages is None:
        languages = {Language("eng")}

    try:
        video = scan_video(file_path)
        subtitles = download_best_subtitles([video], languages)
        if subtitles.get(video):
            save_subtitles(video, subtitles[video])
            return True
        return False
    except Exception as e:
        print(f"  Error: {e}")
        return False


def download_all_missing_subs(limit=None, dry_run=False):
    """Download subtitles for all movies missing them."""
    print("\n" + "=" * 60)
    print("  SUBTITLE DOWNLOADER")
    print("=" * 60)

    no_subs = get_movies_without_subs()

    # Separate ones that already have .srt on disk from truly missing
    have_srt = [m for m in no_subs if m["srt_exists_on_disk"]]
    need_srt = [m for m in no_subs if not m["srt_exists_on_disk"]]

    print(f"\n  {len(no_subs)} movies without embedded subtitles")
    if have_srt:
        print(f"  {len(have_srt)} already have .srt files on disk (Plex may need a scan)")
    print(f"  {len(need_srt)} need subtitle downloads")

    if have_srt:
        print(f"\n  [HAVE .SRT ON DISK] These just need a Plex rescan ({len(have_srt)}):")
        for m in sorted(have_srt, key=lambda x: x["title"])[:20]:
            print(f"    - {m['title']} ({m['year']})")
        if len(have_srt) > 20:
            print(f"    ... and {len(have_srt) - 20} more")

    if not need_srt:
        print("\n  All movies have subtitle files! Just need a Plex scan.")
        return {"have_srt": have_srt, "need_srt": [], "downloaded": 0, "failed": 0}

    if dry_run:
        print(f"\n  [DRY RUN] Would download subs for {len(need_srt)} movies:")
        for m in sorted(need_srt, key=lambda x: x["title"]):
            print(f"    - {m['title']} ({m['year']})")
            print(f"      {m['file']}")
        return {"have_srt": have_srt, "need_srt": need_srt, "downloaded": 0, "failed": 0}

    if not HAS_SUBLIMINAL:
        print("\n  ERROR: subliminal not installed. Run: pip install subliminal")
        print("  Listing files that need subtitles instead:\n")
        for m in sorted(need_srt, key=lambda x: x["title"]):
            print(f"    - {m['title']} ({m['year']})")
        return {"have_srt": have_srt, "need_srt": need_srt, "downloaded": 0, "failed": 0}

    # Download
    to_process = need_srt[:limit] if limit else need_srt
    downloaded = 0
    failed = 0

    print(f"\n  Downloading subtitles for {len(to_process)} movies...")
    for i, m in enumerate(sorted(to_process, key=lambda x: x["title"])):
        title = f"{m['title']} ({m['year']})"
        file_path = m["file"]

        if not os.path.exists(file_path):
            print(f"  [{i+1}/{len(to_process)}] SKIP (file not found): {title}")
            failed += 1
            continue

        print(f"  [{i+1}/{len(to_process)}] {title}...", end=" ", flush=True)
        success = download_subs_for_file(file_path)
        if success:
            print("OK")
            downloaded += 1
        else:
            print("NOT FOUND")
            failed += 1

    print(f"\n  Done: {downloaded} downloaded, {failed} not found/failed")

    # Trigger Plex scan so new subs are picked up
    if downloaded > 0:
        print("  Triggering Plex library scan to pick up new subtitles...")
        try:
            import requests
            url = f"{PLEX['url']}/library/sections/{PLEX['movie_section']}/refresh"
            requests.get(url, params={"X-Plex-Token": PLEX["token"]})
            print("  Plex scan triggered.")
        except Exception as e:
            print(f"  Could not trigger Plex scan: {e}")

    return {
        "have_srt": have_srt,
        "need_srt": need_srt,
        "downloaded": downloaded,
        "failed": failed,
    }


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv or "-n" in sys.argv
    limit = None
    for arg in sys.argv[1:]:
        if arg.startswith("--limit="):
            limit = int(arg.split("=")[1])
    download_all_missing_subs(limit=limit, dry_run=dry_run)
