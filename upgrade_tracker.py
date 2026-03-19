# Intelligent Upgrade Tracker
# Monitors quality upgrades across Radarr and Sonarr, tracks changes over time.

import json
import os
from datetime import datetime
from api import (
    radarr_get, sonarr_get,
    get_plex_movies, get_plex_shows, get_plex_movie_details,
    send_discord,
)
from config import PLEX

HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "upgrade_history.json")

# Quality tiers ordered lowest to highest for comparison
QUALITY_TIERS = {
    "Unknown": 0,
    "SDTV": 1, "DVD": 1, "DVDR": 1, "Bluray-480p": 1,
    "WEBDL-480p": 1, "WEBRip-480p": 1,
    "HDTV-720p": 2, "WEBDL-720p": 2, "WEBRip-720p": 2, "Bluray-720p": 2,
    "HDTV-1080p": 3, "WEBDL-1080p": 3, "WEBRip-1080p": 3, "Bluray-1080p": 3,
    "Remux-1080p": 4,
    "HDTV-2160p": 5, "WEBDL-2160p": 5, "WEBRip-2160p": 5,
    "Bluray-2160p": 5, "Remux-2160p": 6,
}

SD_QUALITIES = {"SDTV", "DVD", "DVDR", "Bluray-480p", "WEBDL-480p", "WEBRip-480p"}
Q720_QUALITIES = {"HDTV-720p", "WEBDL-720p", "WEBRip-720p", "Bluray-720p"}
Q1080_QUALITIES = {"HDTV-1080p", "WEBDL-1080p", "WEBRip-1080p", "Bluray-1080p", "Remux-1080p"}
Q4K_QUALITIES = {"HDTV-2160p", "WEBDL-2160p", "WEBRip-2160p", "Bluray-2160p", "Remux-2160p"}


def _quality_label(name):
    """Classify a quality name into a human-friendly tier label."""
    if name in SD_QUALITIES:
        return "SD"
    if name in Q720_QUALITIES:
        return "720p"
    if name in Q1080_QUALITIES:
        return "1080p"
    if name in Q4K_QUALITIES:
        return "4K"
    return "Unknown"


def _extract_quality(movie_or_file):
    """Pull quality name string from a Radarr movie or episode-file object."""
    try:
        mf = movie_or_file.get("movieFile") or movie_or_file
        return mf["quality"]["quality"]["name"]
    except (KeyError, TypeError):
        return "Unknown"


# ---------------------------------------------------------------------------
# Core data fetchers
# ---------------------------------------------------------------------------

def get_movie_qualities():
    """Get all movies from Radarr with their current file quality info.

    Returns a list of dicts with: title, year, tmdb_id, quality, size_gb,
    path, has_file, monitored.
    """
    try:
        movies = radarr_get("/movie")
    except Exception as exc:
        print(f"[upgrade_tracker] Radarr /movie failed: {exc}")
        return []

    results = []
    for m in movies:
        quality = "Unknown"
        size_gb = 0.0
        has_file = m.get("hasFile", False)
        if has_file and m.get("movieFile"):
            quality = _extract_quality(m)
            size_gb = round(m["movieFile"].get("size", 0) / (1024 ** 3), 2)

        results.append({
            "title": m.get("title", ""),
            "year": m.get("year", 0),
            "tmdb_id": m.get("tmdbId", 0),
            "quality": quality,
            "size_gb": size_gb,
            "path": m.get("path", ""),
            "has_file": has_file,
            "monitored": m.get("monitored", False),
        })
    return results


def get_episode_qualities():
    """Get series from Sonarr with episode quality breakdown.

    Returns a list of dicts with: series_title, tvdb_id, total_episodes,
    episodes_with_files, quality_breakdown (quality_name -> count).
    """
    try:
        series_list = sonarr_get("/series")
    except Exception as exc:
        print(f"[upgrade_tracker] Sonarr /series failed: {exc}")
        return []

    results = []
    for series in series_list:
        sid = series.get("id")
        try:
            episodes = sonarr_get("/episode", {"seriesId": sid})
        except Exception:
            episodes = []

        total = 0
        with_files = 0
        breakdown = {}
        for ep in episodes:
            if not ep.get("monitored", True):
                continue
            total += 1
            if ep.get("hasFile", False):
                with_files += 1
                qname = "Unknown"
                try:
                    ef = ep.get("episodeFile") or {}
                    qname = ef.get("quality", {}).get("quality", {}).get("name", "Unknown")
                except (KeyError, TypeError):
                    pass
                # If episodeFile wasn't embedded, try the episodefile endpoint
                if qname == "Unknown" and ep.get("episodeFileId"):
                    try:
                        ef = sonarr_get(f"/episodefile/{ep['episodeFileId']}")
                        qname = ef.get("quality", {}).get("quality", {}).get("name", "Unknown")
                    except Exception:
                        pass
                breakdown[qname] = breakdown.get(qname, 0) + 1

        results.append({
            "series_title": series.get("title", ""),
            "tvdb_id": series.get("tvdbId", 0),
            "total_episodes": total,
            "episodes_with_files": with_files,
            "quality_breakdown": breakdown,
        })
    return results


# ---------------------------------------------------------------------------
# Analysis helpers
# ---------------------------------------------------------------------------

def find_upgrade_candidates():
    """Find movies that could benefit from a quality upgrade.

    Candidates are movies currently below 1080p, or WEB-DL copies that
    could become Bluray.  Returns a list of dicts with title, year,
    current_quality, desired_quality, tmdb_id, monitored.
    """
    movies = get_movie_qualities()
    candidates = []
    for m in movies:
        if not m["has_file"] or not m["monitored"]:
            continue
        q = m["quality"]
        tier = QUALITY_TIERS.get(q, 0)
        desired = None

        if tier < 3:  # below 1080p
            desired = "Bluray-1080p"
        elif q in ("WEBDL-1080p", "WEBRip-1080p"):
            desired = "Bluray-1080p"
        elif q in ("WEBDL-2160p", "WEBRip-2160p"):
            desired = "Bluray-2160p"

        if desired:
            candidates.append({
                "title": m["title"],
                "year": m["year"],
                "tmdb_id": m["tmdb_id"],
                "current_quality": q,
                "desired_quality": desired,
            })
    return candidates


def get_upgrade_history(days=30):
    """Check Radarr and Sonarr history APIs for recent quality upgrades.

    Returns a list of dicts: title, old_quality, new_quality, date,
    size_change (in GB, positive = larger).
    """
    results = []
    now = datetime.utcnow()

    # --- Radarr history ---
    try:
        page = 1
        while True:
            data = radarr_get("/history", {
                "page": page,
                "pageSize": 50,
                "sortKey": "date",
                "sortDirection": "descending",
            })
            records = data.get("records", [])
            if not records:
                break
            stop = False
            for rec in records:
                rec_date = rec.get("date", "")
                try:
                    dt = datetime.fromisoformat(rec_date.replace("Z", "+00:00")).replace(tzinfo=None)
                except (ValueError, AttributeError):
                    continue
                if (now - dt).days > days:
                    stop = True
                    break
                if rec.get("eventType") == "downloadFolderImported":
                    dq = rec.get("data", {})
                    old_q = dq.get("droppedPath", "")  # fallback
                    new_q = "Unknown"
                    try:
                        new_q = rec["quality"]["quality"]["name"]
                    except (KeyError, TypeError):
                        pass
                    # Check if this was truly an upgrade (Radarr marks it)
                    old_q_name = dq.get("importedPath", "")
                    # Use sourceTitle / quality fields
                    results.append({
                        "title": rec.get("sourceTitle", "Unknown"),
                        "old_quality": dq.get("reason", ""),
                        "new_quality": new_q,
                        "date": rec_date,
                        "size_change": 0.0,
                        "source": "radarr",
                    })
            if stop or page >= data.get("totalRecords", 0) // 50 + 2:
                break
            page += 1
    except Exception as exc:
        print(f"[upgrade_tracker] Radarr history error: {exc}")

    # --- Sonarr history ---
    try:
        page = 1
        while True:
            data = sonarr_get("/history", {
                "page": page,
                "pageSize": 50,
                "sortKey": "date",
                "sortDirection": "descending",
            })
            records = data.get("records", [])
            if not records:
                break
            stop = False
            for rec in records:
                rec_date = rec.get("date", "")
                try:
                    dt = datetime.fromisoformat(rec_date.replace("Z", "+00:00")).replace(tzinfo=None)
                except (ValueError, AttributeError):
                    continue
                if (now - dt).days > days:
                    stop = True
                    break
                if rec.get("eventType") == "downloadFolderImported":
                    new_q = "Unknown"
                    try:
                        new_q = rec["quality"]["quality"]["name"]
                    except (KeyError, TypeError):
                        pass
                    results.append({
                        "title": rec.get("sourceTitle", "Unknown"),
                        "old_quality": rec.get("data", {}).get("reason", ""),
                        "new_quality": new_q,
                        "date": rec_date,
                        "size_change": 0.0,
                        "source": "sonarr",
                    })
            if stop or page >= data.get("totalRecords", 0) // 50 + 2:
                break
            page += 1
    except Exception as exc:
        print(f"[upgrade_tracker] Sonarr history error: {exc}")

    return results


def get_quality_distribution():
    """Aggregate quality stats for movies and TV episodes.

    Returns a dict with 'movies' and 'episodes' keys, each mapping
    tier labels (SD, 720p, 1080p, 4K, Unknown) to counts.
    """
    movie_dist = {"SD": 0, "720p": 0, "1080p": 0, "4K": 0, "Unknown": 0}
    ep_dist = {"SD": 0, "720p": 0, "1080p": 0, "4K": 0, "Unknown": 0}

    for m in get_movie_qualities():
        if not m["has_file"]:
            continue
        label = _quality_label(m["quality"])
        movie_dist[label] = movie_dist.get(label, 0) + 1

    for s in get_episode_qualities():
        for qname, count in s["quality_breakdown"].items():
            label = _quality_label(qname)
            ep_dist[label] = ep_dist.get(label, 0) + count

    return {"movies": movie_dist, "episodes": ep_dist}


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

def trigger_search_for_upgrades(movie_ids=None):
    """Tell Radarr to search for better quality files.

    If movie_ids is None, searches for all monitored upgrade candidates.
    Returns the number of searches triggered.
    """
    from api import radarr_post

    if movie_ids is None:
        candidates = find_upgrade_candidates()
        movie_ids = []
        for c in candidates:
            try:
                movies = radarr_get("/movie", {"tmdbId": c["tmdb_id"]})
                if movies:
                    movie_ids.append(movies[0]["id"])
            except Exception:
                continue

    if not movie_ids:
        print("[upgrade_tracker] No movies to search for upgrades.")
        return 0

    count = 0
    for mid in movie_ids:
        try:
            radarr_post("/command", {
                "name": "MoviesSearch",
                "movieIds": [mid],
            })
            count += 1
        except Exception as exc:
            print(f"[upgrade_tracker] Search failed for movie {mid}: {exc}")
    print(f"[upgrade_tracker] Triggered {count} upgrade searches.")
    return count


# ---------------------------------------------------------------------------
# Snapshot / comparison
# ---------------------------------------------------------------------------

def _load_history():
    """Load the upgrade history JSON file."""
    if not os.path.exists(HISTORY_FILE):
        return {"snapshots": []}
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return {"snapshots": []}


def _save_history(data):
    """Write the upgrade history JSON file."""
    with open(HISTORY_FILE, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, default=str)


def save_snapshot():
    """Save a timestamped snapshot of current quality state.

    Each snapshot stores per-movie quality and the aggregate distribution.
    """
    movies = get_movie_qualities()
    distribution = {"movies": {}, "episodes": {}}

    # Build movie-level map (tmdb_id -> quality) and distribution
    movie_map = {}
    tier_counts = {"SD": 0, "720p": 0, "1080p": 0, "4K": 0, "Unknown": 0}
    for m in movies:
        if m["has_file"]:
            movie_map[str(m["tmdb_id"])] = {
                "title": m["title"],
                "quality": m["quality"],
                "size_gb": m["size_gb"],
            }
            label = _quality_label(m["quality"])
            tier_counts[label] = tier_counts.get(label, 0) + 1
    distribution["movies"] = tier_counts

    snapshot = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "movie_count": len(movie_map),
        "distribution": distribution,
        "movies": movie_map,
    }

    history = _load_history()
    history["snapshots"].append(snapshot)
    # Keep last 90 snapshots to limit file size
    if len(history["snapshots"]) > 90:
        history["snapshots"] = history["snapshots"][-90:]
    _save_history(history)
    print(f"[upgrade_tracker] Snapshot saved ({len(movie_map)} movies recorded).")
    return snapshot


def compare_snapshots():
    """Compare current quality state vs the most recent saved snapshot.

    Returns a dict with 'upgrades', 'downgrades', and 'new_files' lists.
    Each entry has: title, tmdb_id, old_quality, new_quality.
    """
    history = _load_history()
    if not history["snapshots"]:
        print("[upgrade_tracker] No previous snapshot found. Run save_snapshot() first.")
        return {"upgrades": [], "downgrades": [], "new_files": []}

    previous = history["snapshots"][-1]
    prev_movies = previous.get("movies", {})

    current_movies = get_movie_qualities()

    upgrades = []
    downgrades = []
    new_files = []

    for m in current_movies:
        if not m["has_file"]:
            continue
        key = str(m["tmdb_id"])
        cur_q = m["quality"]
        if key in prev_movies:
            old_q = prev_movies[key]["quality"]
            if old_q != cur_q:
                old_tier = QUALITY_TIERS.get(old_q, 0)
                new_tier = QUALITY_TIERS.get(cur_q, 0)
                entry = {
                    "title": m["title"],
                    "tmdb_id": m["tmdb_id"],
                    "old_quality": old_q,
                    "new_quality": cur_q,
                }
                if new_tier > old_tier:
                    upgrades.append(entry)
                else:
                    downgrades.append(entry)
        else:
            new_files.append({
                "title": m["title"],
                "tmdb_id": m["tmdb_id"],
                "old_quality": None,
                "new_quality": cur_q,
            })

    return {"upgrades": upgrades, "downgrades": downgrades, "new_files": new_files}


def get_stale_qualities(days=180):
    """Find items still at low quality (< 1080p) for longer than *days*.

    Checks snapshot history for how long a movie has been at its current
    quality.  Returns list of dicts: title, tmdb_id, quality, days_at_quality.
    """
    history = _load_history()
    snapshots = history.get("snapshots", [])
    current = get_movie_qualities()
    now = datetime.utcnow()

    stale = []
    for m in current:
        if not m["has_file"] or not m["monitored"]:
            continue
        tier = QUALITY_TIERS.get(m["quality"], 0)
        if tier >= 3:  # 1080p or better -- not considered low quality
            continue

        # Walk snapshots in reverse to find when quality last changed
        first_seen_at = now
        key = str(m["tmdb_id"])
        for snap in reversed(snapshots):
            snap_movies = snap.get("movies", {})
            if key in snap_movies and snap_movies[key]["quality"] == m["quality"]:
                try:
                    first_seen_at = datetime.fromisoformat(snap["timestamp"].replace("Z", ""))
                except (ValueError, KeyError):
                    pass
            else:
                break

        days_at = (now - first_seen_at).days
        if days_at >= days:
            stale.append({
                "title": m["title"],
                "tmdb_id": m["tmdb_id"],
                "quality": m["quality"],
                "days_at_quality": days_at,
            })

    # Sort by staleness descending
    stale.sort(key=lambda x: x["days_at_quality"], reverse=True)
    return stale


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 60)
    print("  Plex Upgrade Tracker")
    print("=" * 60)

    # Quality distribution
    print("\n--- Quality Distribution ---")
    dist = get_quality_distribution()
    for category, counts in dist.items():
        total = sum(counts.values())
        print(f"\n  {category.upper()} ({total} total):")
        for tier in ("4K", "1080p", "720p", "SD", "Unknown"):
            c = counts.get(tier, 0)
            if c:
                pct = c / total * 100 if total else 0
                bar = "#" * int(pct / 2)
                print(f"    {tier:>7s}: {c:>5d}  ({pct:5.1f}%)  {bar}")

    # Upgrade candidates
    print("\n--- Upgrade Candidates ---")
    candidates = find_upgrade_candidates()
    if candidates:
        for c in candidates[:25]:
            print(f"  {c['title']} ({c['year']})  {c['current_quality']} -> {c['desired_quality']}")
        if len(candidates) > 25:
            print(f"  ... and {len(candidates) - 25} more")
        print(f"\n  Total candidates: {len(candidates)}")
    else:
        print("  No upgrade candidates found.")

    # Stale qualities
    print("\n--- Stale Low-Quality Items (>180 days) ---")
    stale = get_stale_qualities(days=180)
    if stale:
        for s in stale[:15]:
            print(f"  {s['title']}  [{s['quality']}]  ({s['days_at_quality']} days)")
        if len(stale) > 15:
            print(f"  ... and {len(stale) - 15} more")
    else:
        print("  None found (or no snapshot history yet).")

    # Snapshot comparison
    print("\n--- Changes Since Last Snapshot ---")
    changes = compare_snapshots()
    if changes["upgrades"]:
        print(f"  Upgrades ({len(changes['upgrades'])}):")
        for u in changes["upgrades"][:10]:
            print(f"    {u['title']}  {u['old_quality']} -> {u['new_quality']}")
    if changes["downgrades"]:
        print(f"  Downgrades ({len(changes['downgrades'])}):")
        for d in changes["downgrades"][:10]:
            print(f"    {d['title']}  {d['old_quality']} -> {d['new_quality']}")
    if changes["new_files"]:
        print(f"  New files ({len(changes['new_files'])}):")
        for n in changes["new_files"][:10]:
            print(f"    {n['title']}  [{n['new_quality']}]")
    if not any(changes.values()):
        print("  No changes detected (or no previous snapshot).")

    print()
