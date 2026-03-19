# Plex Media Stack - Health Monitor Service
# Periodically checks system health and sends Discord notifications.

import os
import time
import json
import shutil
import threading
from datetime import datetime
from api import (
    plex_get, get_plex_movies, get_plex_shows,
    get_plex_movie_details, get_plex_show_seasons, get_plex_season_episodes,
    radarr_get, sonarr_get,
    send_discord,
)
from config import PLEX, STORAGE, HEALTH, NOTIFY

REPORT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "health_report.json")


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def check_disk_space():
    """Check all movie and TV drives for disk usage.

    Returns a list of dicts with drive, total_gb, used_gb, free_gb,
    pct_used, and warning (True if pct_used > threshold).
    """
    threshold = HEALTH.get("disk_warning_pct", 90)
    results = []
    all_drives = list(set(STORAGE.get("movie_drives", []) + STORAGE.get("tv_drives", [])))
    for drive in sorted(all_drives):
        entry = {"drive": drive, "total_gb": 0, "used_gb": 0, "free_gb": 0, "pct_used": 0, "warning": False}
        try:
            if not os.path.exists(drive):
                entry["error"] = "Drive not mounted"
                entry["warning"] = True
                results.append(entry)
                continue
            usage = shutil.disk_usage(drive)
            total_gb = round(usage.total / (1024 ** 3), 1)
            used_gb = round(usage.used / (1024 ** 3), 1)
            free_gb = round(usage.free / (1024 ** 3), 1)
            pct_used = round((usage.used / usage.total) * 100, 1) if usage.total else 0
            entry.update({
                "total_gb": total_gb,
                "used_gb": used_gb,
                "free_gb": free_gb,
                "pct_used": pct_used,
                "warning": pct_used >= threshold,
            })
        except Exception as exc:
            entry["error"] = str(exc)
            entry["warning"] = True
        results.append(entry)
    return results


def check_quality_issues():
    """Scan Plex movies for quality issues.

    Checks for: missing summary, missing poster (thumb), low resolution
    (< 720p height), and missing subtitles.  Returns a dict with counts
    and lists of affected titles.
    """
    issues = {
        "no_summary": [],
        "no_poster": [],
        "low_resolution": [],
        "no_subtitles": [],
        "total_movies": 0,
        "error": None,
    }
    try:
        movies = get_plex_movies()
        issues["total_movies"] = len(movies)
        for movie in movies:
            title = movie.get("title", "Unknown")
            year = movie.get("year", "")
            label = f"{title} ({year})" if year else title

            if not movie.get("summary"):
                issues["no_summary"].append(label)
            if not movie.get("thumb"):
                issues["no_poster"].append(label)

            # Detailed media info requires a per-item lookup
            try:
                details = get_plex_movie_details(movie["ratingKey"])
                media_list = details.get("Media", [])
                # Resolution check: look at the first media item
                if media_list:
                    height = media_list[0].get("videoResolution", "")
                    # videoResolution can be "1080", "720", "4k", "sd", etc.
                    try:
                        height_int = int(height)
                    except (ValueError, TypeError):
                        # Map textual values
                        height_int = {"4k": 2160, "1080": 1080, "720": 720, "sd": 480}.get(
                            str(height).lower(), 0
                        )
                    if 0 < height_int < 720:
                        issues["low_resolution"].append(label)

                    # Subtitle check: walk streams of first media/part
                    parts = media_list[0].get("Part", [])
                    has_subs = False
                    for part in parts:
                        for stream in part.get("Stream", []):
                            if stream.get("streamType") == 3:  # subtitle stream
                                has_subs = True
                                break
                        if has_subs:
                            break
                    if not has_subs:
                        issues["no_subtitles"].append(label)
            except Exception:
                # If detail lookup fails for a single movie, just skip it
                pass
    except Exception as exc:
        issues["error"] = str(exc)

    issues["counts"] = {
        "no_summary": len(issues["no_summary"]),
        "no_poster": len(issues["no_poster"]),
        "low_resolution": len(issues["low_resolution"]),
        "no_subtitles": len(issues["no_subtitles"]),
    }
    return issues


def check_sync_status():
    """Compare Plex libraries against Radarr/Sonarr for mismatches.

    Returns a dict describing items present in the *arr but not found in
    Plex and vice-versa.
    """
    result = {
        "radarr_not_in_plex": [],
        "plex_not_in_radarr": [],
        "sonarr_not_in_plex": [],
        "plex_not_in_sonarr": [],
        "error": None,
    }

    # --- Movies ---
    try:
        radarr_movies = radarr_get("/movie")
        radarr_titles = {}
        for m in radarr_movies:
            if m.get("hasFile"):
                radarr_titles[m.get("title", "").lower()] = m.get("title", "Unknown")

        plex_movies = get_plex_movies()
        plex_movie_titles = {m.get("title", "").lower() for m in plex_movies}

        for key, title in radarr_titles.items():
            if key not in plex_movie_titles:
                result["radarr_not_in_plex"].append(title)
        for m in plex_movies:
            key = m.get("title", "").lower()
            if key not in radarr_titles:
                result["plex_not_in_radarr"].append(m.get("title", "Unknown"))
    except Exception as exc:
        result["error"] = f"Movie sync error: {exc}"

    # --- TV Shows ---
    try:
        sonarr_series = sonarr_get("/series")
        sonarr_titles = {}
        for s in sonarr_series:
            sonarr_titles[s.get("title", "").lower()] = s.get("title", "Unknown")

        plex_shows = get_plex_shows()
        plex_show_titles = {s.get("title", "").lower() for s in plex_shows}

        for key, title in sonarr_titles.items():
            if key not in plex_show_titles:
                result["sonarr_not_in_plex"].append(title)
        for s in plex_shows:
            key = s.get("title", "").lower()
            if key not in sonarr_titles:
                result["plex_not_in_sonarr"].append(s.get("title", "Unknown"))
    except Exception as exc:
        existing = result["error"] or ""
        result["error"] = f"{existing}  TV sync error: {exc}".strip()

    return result


def check_service_health():
    """Ping Plex, Sonarr, Radarr, and NZBGet to verify they respond.

    Returns a dict mapping service name -> {"up": bool, "error": str|None}.
    """
    import requests as _requests
    from config import SONARR, RADARR, NZBGET

    services = {}

    # Plex
    try:
        plex_get("/identity")
        services["plex"] = {"up": True, "error": None}
    except Exception as exc:
        services["plex"] = {"up": False, "error": str(exc)}

    # Sonarr
    try:
        sonarr_get("/system/status")
        services["sonarr"] = {"up": True, "error": None}
    except Exception as exc:
        services["sonarr"] = {"up": False, "error": str(exc)}

    # Radarr
    try:
        radarr_get("/system/status")
        services["radarr"] = {"up": True, "error": None}
    except Exception as exc:
        services["radarr"] = {"up": False, "error": str(exc)}

    # NZBGet (uses JSON-RPC at /jsonrpc)
    try:
        r = _requests.get(f"{NZBGET['url']}/jsonrpc/version", timeout=5)
        r.raise_for_status()
        services["nzbget"] = {"up": True, "error": None}
    except Exception as exc:
        services["nzbget"] = {"up": False, "error": str(exc)}

    return services


# ---------------------------------------------------------------------------
# Aggregation & reporting
# ---------------------------------------------------------------------------

def run_full_health_check():
    """Run every check and combine into a single report dict."""
    report = {"timestamp": datetime.now().isoformat(), "checks": {}}

    # Each check is wrapped so a failure in one doesn't block others
    try:
        report["checks"]["disk_space"] = check_disk_space()
    except Exception as exc:
        report["checks"]["disk_space"] = {"error": str(exc)}

    try:
        report["checks"]["quality_issues"] = check_quality_issues()
    except Exception as exc:
        report["checks"]["quality_issues"] = {"error": str(exc)}

    try:
        report["checks"]["sync_status"] = check_sync_status()
    except Exception as exc:
        report["checks"]["sync_status"] = {"error": str(exc)}

    try:
        report["checks"]["service_health"] = check_service_health()
    except Exception as exc:
        report["checks"]["service_health"] = {"error": str(exc)}

    return report


def format_report(report):
    """Format a health report as a human-readable string."""
    lines = []
    ts = report.get("timestamp", "unknown")
    lines.append(f"=== Plex Health Report  {ts} ===\n")

    checks = report.get("checks", {})

    # --- Services ---
    svc = checks.get("service_health", {})
    if isinstance(svc, dict) and "error" not in svc:
        lines.append("-- Services --")
        for name, info in svc.items():
            status = "UP" if info.get("up") else "DOWN"
            extra = f'  ({info["error"]})' if info.get("error") else ""
            lines.append(f"  {name:10s} {status}{extra}")
        lines.append("")
    elif isinstance(svc, dict) and "error" in svc:
        lines.append(f"-- Services -- ERROR: {svc['error']}\n")

    # --- Disk space ---
    disks = checks.get("disk_space", [])
    if isinstance(disks, list) and disks:
        lines.append("-- Disk Space --")
        for d in disks:
            if d.get("error"):
                lines.append(f"  {d['drive']:5s}  {d['error']}")
            else:
                warn = " *** WARNING ***" if d.get("warning") else ""
                lines.append(
                    f"  {d['drive']:5s}  {d['used_gb']:>8.1f} / {d['total_gb']:.1f} GB  "
                    f"({d['pct_used']:.1f}% used)  {d['free_gb']:.1f} GB free{warn}"
                )
        lines.append("")

    # --- Quality ---
    qi = checks.get("quality_issues", {})
    if isinstance(qi, dict):
        counts = qi.get("counts", {})
        if counts:
            lines.append(f"-- Quality Issues (of {qi.get('total_movies', '?')} movies) --")
            lines.append(f"  No summary:    {counts.get('no_summary', 0)}")
            lines.append(f"  No poster:     {counts.get('no_poster', 0)}")
            lines.append(f"  Low res (<720):{counts.get('low_resolution', 0)}")
            lines.append(f"  No subtitles:  {counts.get('no_subtitles', 0)}")
            lines.append("")
        if qi.get("error"):
            lines.append(f"  Quality check error: {qi['error']}\n")

    # --- Sync ---
    sync = checks.get("sync_status", {})
    if isinstance(sync, dict):
        r_np = sync.get("radarr_not_in_plex", [])
        p_nr = sync.get("plex_not_in_radarr", [])
        s_np = sync.get("sonarr_not_in_plex", [])
        p_ns = sync.get("plex_not_in_sonarr", [])
        if r_np or p_nr or s_np or p_ns:
            lines.append("-- Sync Mismatches --")
            if r_np:
                lines.append(f"  In Radarr but not Plex ({len(r_np)}):")
                for t in r_np[:10]:
                    lines.append(f"    - {t}")
                if len(r_np) > 10:
                    lines.append(f"    ... and {len(r_np) - 10} more")
            if p_nr:
                lines.append(f"  In Plex but not Radarr ({len(p_nr)}):")
                for t in p_nr[:10]:
                    lines.append(f"    - {t}")
                if len(p_nr) > 10:
                    lines.append(f"    ... and {len(p_nr) - 10} more")
            if s_np:
                lines.append(f"  In Sonarr but not Plex ({len(s_np)}):")
                for t in s_np[:10]:
                    lines.append(f"    - {t}")
                if len(s_np) > 10:
                    lines.append(f"    ... and {len(s_np) - 10} more")
            if p_ns:
                lines.append(f"  In Plex but not Sonarr ({len(p_ns)}):")
                for t in p_ns[:10]:
                    lines.append(f"    - {t}")
                if len(p_ns) > 10:
                    lines.append(f"    ... and {len(p_ns) - 10} more")
            lines.append("")
        if sync.get("error"):
            lines.append(f"  Sync check error: {sync['error']}\n")

    return "\n".join(lines)


def alert_if_needed(report):
    """Send a Discord notification if the report contains warnings.

    Triggers on: any disk above threshold, any service down, or check-level
    errors.
    """
    warnings = []
    checks = report.get("checks", {})

    # Disk warnings
    for d in checks.get("disk_space", []):
        if isinstance(d, dict) and d.get("warning"):
            if d.get("error"):
                warnings.append(f"Disk {d['drive']}: {d['error']}")
            else:
                warnings.append(f"Disk {d['drive']}: {d['pct_used']:.1f}% used ({d['free_gb']:.1f} GB free)")

    # Service warnings
    svc = checks.get("service_health", {})
    if isinstance(svc, dict):
        for name, info in svc.items():
            if isinstance(info, dict) and not info.get("up"):
                warnings.append(f"Service DOWN: {name}")

    if not warnings:
        return

    body = "**Health Monitor Alerts**\n" + "\n".join(f"- {w}" for w in warnings)
    send_discord(body, title="Plex Health Alert")


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def _save_report(report):
    """Save report to disk as JSON."""
    try:
        with open(REPORT_FILE, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
    except Exception:
        pass


def get_latest_report():
    """Load and return the last saved report (for dashboard API)."""
    try:
        with open(REPORT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


# ---------------------------------------------------------------------------
# Monitor loop
# ---------------------------------------------------------------------------

def monitor_loop():
    """Run health checks on a recurring interval.

    Saves the latest report to health_report.json and sends Discord
    alerts when warnings are detected.
    """
    interval = HEALTH.get("check_interval_minutes", 60) * 60  # seconds
    print(f"[HealthMonitor] Starting - checking every {HEALTH.get('check_interval_minutes', 60)} minutes")
    while True:
        try:
            print(f"[HealthMonitor] Running check at {datetime.now().isoformat()}")
            report = run_full_health_check()
            _save_report(report)
            print(format_report(report))
            alert_if_needed(report)
            print(f"[HealthMonitor] Next check in {HEALTH.get('check_interval_minutes', 60)} minutes\n")
        except Exception as exc:
            print(f"[HealthMonitor] Unexpected error: {exc}")
        time.sleep(interval)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "serve":
        monitor_loop()
    else:
        report = run_full_health_check()
        print(format_report(report))
