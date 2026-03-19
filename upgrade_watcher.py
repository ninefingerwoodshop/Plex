# Plex Media Stack - Upgrade Watcher & Auto Cleanup
# Monitors Radarr for quality upgrades and cleans up old CAM/TELESYNC copies
# Also watches for completed downloads and triggers Plex scans
#
# Run as: python upgrade_watcher.py --daemon --go
# Or via: python plexhealth.py cleanup

import sys
import time
import os
import json
from datetime import datetime
from api import get_radarr_movies, radarr_get, plex_get
from config import PLEX, RADARR
import requests


POLL_INTERVAL = 60  # seconds between checks
STATE_FILE = os.path.join(os.path.dirname(__file__), "cleanup_state.json")

# Quality tiers - anything at or below this rank gets auto-cleaned when upgraded
CLEANUP_QUALITIES = {"cam", "telesync", "telecine", "ts", "dvd", "dvd-r",
                     "webdl-480p", "webrip-480p"}


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"movie_files": {}, "last_check": None}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def trigger_plex_scan(section_id=None):
    sid = section_id or PLEX["movie_section"]
    requests.get(
        f"{PLEX['url']}/library/sections/{sid}/refresh",
        params={"X-Plex-Token": PLEX["token"]},
    )


def check_for_upgrades():
    """Check if any movies were recently upgraded from low quality."""
    state = load_state()
    prev_files = state.get("movie_files", {})

    movies = get_radarr_movies()
    current_files = {}
    upgrades = []
    new_downloads = []

    for m in movies:
        if not m.get("hasFile"):
            continue

        movie_id = str(m.get("id", ""))
        mf = m.get("movieFile", {})
        file_id = str(mf.get("id", ""))
        quality = mf.get("quality", {}).get("quality", {}).get("name", "").lower()
        path = mf.get("path", "")

        current_files[movie_id] = {
            "file_id": file_id,
            "quality": quality,
            "path": path,
            "title": m.get("title", ""),
        }

        if movie_id in prev_files:
            prev = prev_files[movie_id]
            if prev["file_id"] != file_id:
                old_quality = prev.get("quality", "")
                if old_quality in CLEANUP_QUALITIES:
                    upgrades.append({
                        "title": m.get("title", ""),
                        "year": m.get("year", ""),
                        "old_quality": old_quality,
                        "new_quality": quality,
                        "old_path": prev.get("path", ""),
                        "new_path": path,
                    })
                else:
                    new_downloads.append({
                        "title": m.get("title", ""),
                        "old_quality": old_quality,
                        "new_quality": quality,
                    })
        elif prev_files:
            new_downloads.append({
                "title": m.get("title", ""),
                "old_quality": None,
                "new_quality": quality,
            })

    state["movie_files"] = current_files
    state["last_check"] = datetime.now().isoformat()
    save_state(state)

    return upgrades, new_downloads


def cleanup_old_files(upgrades, dry_run=True):
    if not upgrades:
        return

    for u in upgrades:
        old_path = u["old_path"]
        old_dir = os.path.dirname(old_path)

        print(f"  UPGRADE: {u['title']} ({u.get('year', '')})")
        print(f"    {u['old_quality']} -> {u['new_quality']}")
        print(f"    Old: {old_path}")
        print(f"    New: {u['new_path']}")

        if old_path and os.path.exists(old_path):
            if dry_run:
                print(f"    [DRY RUN] Would delete old file")
            else:
                try:
                    os.remove(old_path)
                    print(f"    Deleted old file")
                    if old_dir and os.path.isdir(old_dir) and not os.listdir(old_dir):
                        os.rmdir(old_dir)
                        print(f"    Removed empty directory")
                except Exception as e:
                    print(f"    Error deleting: {e}")
        else:
            print(f"    Old file already gone")
        print()


def run_once(dry_run=True):
    print(f"\n  [{datetime.now().strftime('%H:%M:%S')}] Checking for upgrades...")
    upgrades, new_downloads = check_for_upgrades()

    if upgrades:
        print(f"\n  Found {len(upgrades)} quality upgrades!")
        cleanup_old_files(upgrades, dry_run=dry_run)
        if not dry_run:
            print("  Triggering Plex scan...")
            trigger_plex_scan()
    else:
        print("  No upgrades detected.")

    if new_downloads:
        print(f"\n  {len(new_downloads)} new downloads since last check:")
        for d in new_downloads:
            print(f"    - {d['title']} ({d['new_quality']})")
        if not dry_run:
            trigger_plex_scan()

    return upgrades, new_downloads


def run_daemon(dry_run=False, interval=None):
    poll = interval or POLL_INTERVAL

    print("\n" + "=" * 60)
    print("  UPGRADE WATCHER DAEMON")
    print("=" * 60)
    print(f"\n  Polling every {poll}s")
    print(f"  Dry run: {dry_run}")
    print(f"  Watching for: CAM, TELESYNC, DVD upgrades")
    print(f"  Actions: delete old file, trigger Plex scan")

    if not os.path.exists(STATE_FILE):
        print("  First run -- capturing current state...")
        check_for_upgrades()
        print("  State saved. Will detect changes on next check.\n")

    while True:
        try:
            run_once(dry_run=dry_run)
        except Exception as e:
            print(f"  Error: {e}")
        print(f"  Next check in {poll}s...")
        time.sleep(poll)


if __name__ == "__main__":
    dry_run = "--go" not in sys.argv
    daemon = "--daemon" in sys.argv
    interval = POLL_INTERVAL

    for arg in sys.argv[1:]:
        if arg.startswith("--interval="):
            interval = int(arg.split("=")[1])

    if daemon:
        run_daemon(dry_run=dry_run, interval=interval)
    else:
        print("\n" + "=" * 60)
        print("  UPGRADE WATCHER CHECK")
        print("=" * 60)
        if dry_run:
            print("  (Dry run -- add --go to actually delete old files)")
        run_once(dry_run=dry_run)
