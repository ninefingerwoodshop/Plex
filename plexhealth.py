#!/usr/bin/env python3
"""
Plex Health — Library Health & Sync Dashboard
==============================================
Scans your Plex/Sonarr/Radarr stack and reports:
  1. Sync discrepancies (Plex vs Radarr/Sonarr)
  2. Library quality issues (metadata, resolution, subtitles)
  3. Missing episodes

Usage:
    python plexhealth.py                # Run all reports (sync, quality, episodes)
    python plexhealth.py sync           # Sync audit only
    python plexhealth.py quality        # Quality scan only
    python plexhealth.py episodes       # Missing episodes only
    python plexhealth.py subs           # Subtitle dry run
    python plexhealth.py subs --go      # Download missing subtitles
    python plexhealth.py subs --limit=20  # Download subs for 20 movies
    python plexhealth.py collections    # Movie collection dry run
    python plexhealth.py collections --go  # Create movie collections
    python plexhealth.py tv             # TV collections + binge report + upcoming
    python plexhealth.py tv --go        # Create TV collections
    python plexhealth.py diagnose       # Sync mismatch diagnosis
    python plexhealth.py duplicates     # Find duplicate movies
    python plexhealth.py stale          # Find CAM/TELESYNC/low-quality copies
    python plexhealth.py stats          # Library statistics & watch history
    python plexhealth.py radarr-add     # Add untracked Plex movies to Radarr (dry run)
    python plexhealth.py radarr-add --go  # Actually add to Radarr
    python plexhealth.py upgrades       # Find movies needing quality upgrades
    python plexhealth.py webhooks       # Start webhook listener
    python plexhealth.py dashboard      # Launch web dashboard
    python plexhealth.py all            # Run everything (reports only, no actions)
"""

import sys
import time


def banner():
    print("")
    print("  " + "=" * 50)
    print("    PLEX HEALTH DASHBOARD")
    print("    Library Health & Sync Report")
    print("  " + "=" * 50)
    print("")


def run_sync():
    from sync_audit import movie_sync_audit, tv_sync_audit
    movie_sync_audit()
    tv_sync_audit()


def run_quality():
    from quality_scan import scan_movie_quality, scan_show_quality
    scan_movie_quality()
    scan_show_quality()


def run_episodes():
    from missing_episodes import missing_episodes_report
    missing_episodes_report()


def run_subs(flags):
    from subtitle_downloader import download_all_missing_subs
    dry_run = "--go" not in flags
    limit = None
    for a in flags:
        if a.startswith("--limit="):
            limit = int(a.split("=")[1])
    if dry_run:
        print("  (Dry run -- add --go to actually download)")
    download_all_missing_subs(limit=limit, dry_run=dry_run)


def run_diagnose():
    from fix_sync import diagnose_mismatches
    diagnose_mismatches()


def run_collections(flags):
    from collections_builder import build_collections
    dry_run = "--go" not in flags
    if dry_run:
        print("  (Dry run -- add --go to create collections in Plex)")
    build_collections(dry_run=dry_run)


def run_tv(flags):
    from tv_tools import tv_collection_builder, binge_ready_report, upcoming_episodes
    dry_run = "--go" not in flags
    if dry_run:
        print("  (Dry run -- add --go to create TV collections)")
    tv_collection_builder(dry_run=dry_run)
    binge_ready_report()
    upcoming_episodes()


def run_duplicates():
    from duplicates import find_duplicates
    find_duplicates()


def run_stale():
    from stale_quality import stale_quality_report
    stale_quality_report()


def run_stats():
    from watch_stats import movie_stats, tv_stats
    movie_stats()
    tv_stats()


def run_radarr_add(flags):
    from radarr_sync import auto_add_untracked
    dry_run = "--go" not in flags
    if dry_run:
        print("  (Dry run -- add --go to actually add movies to Radarr)")
    auto_add_untracked(dry_run=dry_run)


def run_upgrades():
    from radarr_sync import find_upgradeable
    find_upgradeable()


def run_webhooks(flags):
    from webhooks import start_webhook_server
    port = 5555
    for a in flags:
        if a.startswith("--port="):
            port = int(a.split("=")[1])
    start_webhook_server(port=port)


def run_dashboard(flags):
    from dashboard import app
    port = 5050
    for a in flags:
        if a.startswith("--port="):
            port = int(a.split("=")[1])
    print(f"  Starting web dashboard on http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)


def run_pick(flags):
    from movie_picker import movie_night, list_genres
    if "--genres" in flags:
        list_genres()
        return
    genre = None
    decade = None
    min_rating = None
    count = 3
    unwatched = "--all" not in flags
    for a in flags:
        if a.startswith("--genre="):
            genre = a.split("=", 1)[1]
        elif a.startswith("--decade="):
            decade = a.split("=", 1)[1]
        elif a.startswith("--rating="):
            min_rating = float(a.split("=")[1])
        elif a.startswith("--count="):
            count = int(a.split("=")[1])
    movie_night(genre=genre, decade=decade, min_rating=min_rating,
                unwatched_only=unwatched, count=count)


def run_storage():
    from storage import storage_report
    storage_report()


def run_similar(flags):
    from recommender import find_similar
    title = None
    unwatched = "--unwatched" in flags
    count = 10
    for a in flags:
        if not a.startswith("--"):
            continue
        if a.startswith("--title="):
            title = a.split("=", 1)[1]
        elif a.startswith("--count="):
            count = int(a.split("=")[1])
    if not title:
        print("  Usage: python plexhealth.py similar --title=\"Movie Name\"")
        return
    find_similar(title, count=count, unwatched_only=unwatched)


def run_tv_audit():
    from tv_audit import tv_file_audit
    tv_file_audit()


def run_posters(flags):
    from poster_upgrade import poster_upgrade_report
    fix = "--go" in flags
    if not fix:
        print("  (Dry run -- add --go to refresh metadata)")
    poster_upgrade_report(fix=fix)


def run_scheduler(flags):
    from scheduler import run_health_check
    interval = None
    for a in flags:
        if a.startswith("--interval="):
            interval = int(a.split("=")[1])

    if interval:
        import time
        print(f"  Running health checks every {interval}s")
        while True:
            run_health_check()
            time.sleep(interval)
    else:
        run_health_check()


def run_recommend(flags):
    from taste_profile import recommend, build_taste_profile, display_profile, TMDB_API_KEY
    import taste_profile
    source = "trending"
    count = 20
    for a in flags:
        if a.startswith("--tmdb-key="):
            taste_profile.TMDB_API_KEY = a.split("=", 1)[1]
        elif a.startswith("--source="):
            source = a.split("=")[1]
        elif a.startswith("--count="):
            count = int(a.split("=")[1])
        elif a == "--profile":
            profile = build_taste_profile()
            display_profile(profile)
            return
    recommend(count=count, source=source)


def run_playlists(flags):
    from smart_playlists import generate_all_playlists
    dry_run = "--go" not in flags
    if dry_run:
        print("  (Dry run -- add --go to create playlists)")
    generate_all_playlists(dry_run=dry_run)


def run_cleanup(flags):
    from upgrade_watcher import run_once, run_daemon
    dry_run = "--go" not in flags
    daemon = "--daemon" in flags
    interval = 60
    for a in flags:
        if a.startswith("--interval="):
            interval = int(a.split("=")[1])
    if daemon:
        run_daemon(dry_run=dry_run, interval=interval)
    else:
        if dry_run:
            print("  (Dry run -- add --go to delete old files)")
        run_once(dry_run=dry_run)


def run_service(flags):
    from service import PlexHealthService, run_with_tray, install_startup, uninstall_startup
    if "--install" in flags:
        install_startup()
    elif "--uninstall" in flags:
        uninstall_startup()
    else:
        service = PlexHealthService()
        run_with_tray(service)


COMMANDS = {
    "sync": run_sync,
    "quality": run_quality,
    "episodes": run_episodes,
    "subs": run_subs,
    "diagnose": run_diagnose,
    "collections": run_collections,
    "tv": run_tv,
    "duplicates": run_duplicates,
    "stale": run_stale,
    "stats": run_stats,
    "radarr-add": run_radarr_add,
    "upgrades": run_upgrades,
    "webhooks": run_webhooks,
    "dashboard": run_dashboard,
    "pick": run_pick,
    "storage": run_storage,
    "similar": run_similar,
    "tv-audit": run_tv_audit,
    "posters": run_posters,
    "schedule": run_scheduler,
    "recommend": run_recommend,
    "playlists": run_playlists,
    "cleanup": run_cleanup,
    "service": run_service,
}

# Commands that accept flags
FLAG_COMMANDS = {
    "subs", "collections", "tv", "radarr-add", "webhooks", "dashboard",
    "pick", "similar", "posters", "schedule",
    "recommend", "playlists", "cleanup", "service",
}

# Report-only commands (safe to run in bulk)
REPORT_COMMANDS = ["sync", "quality", "episodes", "duplicates", "stale", "stats", "storage"]


def main():
    banner()
    start = time.time()

    args = sys.argv[1:]

    # Separate commands from flags
    commands = [a for a in args if not a.startswith("--")]
    flags = [a for a in args if a.startswith("--")]

    if not commands:
        commands = ["sync", "quality", "episodes"]

    if "all" in commands:
        commands = REPORT_COMMANDS

    if "--help" in flags or "-h" in args:
        print(__doc__)
        sys.exit(0)

    for cmd in commands:
        if cmd not in COMMANDS:
            print(f"  Unknown command: {cmd}")
            print(f"  Valid commands: {', '.join(sorted(COMMANDS.keys()))}")
            sys.exit(1)

        if cmd in FLAG_COMMANDS:
            COMMANDS[cmd](flags)
        else:
            COMMANDS[cmd]()

    elapsed = time.time() - start
    print(f"\n{'='*60}")
    print(f"  Done in {elapsed:.1f}s")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
