# Plex Media Stack - Scheduled Reports
# Runs health checks on a schedule
#
# Usage:
#   python scheduler.py                          # Run once now
#   python scheduler.py --interval=3600          # Run every hour

import sys
import time
import io
import contextlib
from datetime import datetime


def run_health_check():
    """Run all health reports."""
    print(f"\n  [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Running health check...")

    results = {}

    # Sync audit
    try:
        from sync_audit import movie_sync_audit, tv_sync_audit
        f = io.StringIO()
        with contextlib.redirect_stdout(f):
            movie_result = movie_sync_audit()
            tv_result = tv_sync_audit()
        results["sync"] = {
            "radarr_not_plex": len(movie_result.get("in_radarr_not_plex", [])),
            "plex_not_radarr": len(movie_result.get("in_plex_not_radarr", [])),
            "sonarr_not_plex": len(tv_result.get("in_sonarr_not_plex", [])),
        }
    except Exception as e:
        results["sync"] = {"error": str(e)}

    # Quality
    try:
        from quality_scan import scan_movie_quality
        f = io.StringIO()
        with contextlib.redirect_stdout(f):
            quality = scan_movie_quality()
        results["quality"] = {
            "low_res": len(quality.get("low_resolution", [])),
            "no_subs": len(quality.get("no_subtitles", [])),
            "low_bitrate": len(quality.get("low_bitrate", [])),
        }
    except Exception as e:
        results["quality"] = {"error": str(e)}

    # Missing episodes
    try:
        from missing_episodes import missing_episodes_report
        f = io.StringIO()
        with contextlib.redirect_stdout(f):
            episodes = missing_episodes_report()
        results["episodes"] = {
            "shows_with_gaps": episodes.get("shows_with_gaps", 0),
        }
    except Exception as e:
        results["episodes"] = {"error": str(e)}

    # Stale quality
    try:
        from stale_quality import stale_quality_report
        f = io.StringIO()
        with contextlib.redirect_stdout(f):
            stale = stale_quality_report()
        results["stale"] = {
            "cam": len(stale.get("cam", [])),
            "telesync": len(stale.get("telesync", [])),
        }
    except Exception as e:
        results["stale"] = {"error": str(e)}

    # Print summary
    print("  Health check complete:")
    for key, val in results.items():
        if "error" in val:
            print(f"    {key}: ERROR - {val['error']}")
        else:
            details = ", ".join(f"{k}={v}" for k, v in val.items())
            print(f"    {key}: {details}")

    return results


if __name__ == "__main__":
    interval = None

    for arg in sys.argv[1:]:
        if arg.startswith("--interval="):
            interval = int(arg.split("=")[1])

    if interval:
        print(f"  Running health checks every {interval} seconds")
        while True:
            run_health_check()
            print(f"\n  Next check in {interval}s...")
            time.sleep(interval)
    else:
        run_health_check()
