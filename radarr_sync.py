# Plex Media Stack - Radarr Auto-Add & Quality Upgrade Hunter
# Adds untracked Plex movies to Radarr and triggers quality upgrades

import re
import sys
import requests
from api import get_plex_movies, get_radarr_movies, radarr_get, radarr_post
from config import RADARR


def normalize(t):
    t = t.lower().strip()
    t = re.sub(r"[^\w\s]", "", t)
    t = re.sub(r"\s+", " ", t)
    return t


def find_untracked_movies():
    """Find Plex movies not tracked in Radarr."""
    plex = get_plex_movies()
    radarr = get_radarr_movies()

    radarr_by_tmdb = set()
    radarr_by_title = set()
    for m in radarr:
        radarr_by_tmdb.add(str(m.get("tmdbId", "")))
        title = m.get("title", "")
        year = m.get("year", "")
        radarr_by_title.add(f"{normalize(title)}|{year}")

    untracked = []
    for m in plex:
        title = m.get("title", "")
        year = m.get("year", "")
        key = f"{normalize(title)}|{year}"

        found = key in radarr_by_title
        if not found:
            for guid in m.get("Guid", []):
                gid = guid.get("id", "")
                if gid.startswith("tmdb://"):
                    if gid.replace("tmdb://", "") in radarr_by_tmdb:
                        found = True
                        break

        if not found:
            tmdb_id = None
            for guid in m.get("Guid", []):
                gid = guid.get("id", "")
                if gid.startswith("tmdb://"):
                    tmdb_id = gid.replace("tmdb://", "")
                    break

            file_path = ""
            for media in m.get("Media", []):
                for part in media.get("Part", []):
                    file_path = part.get("file", "")
                    break
                break

            untracked.append({
                "title": title,
                "year": year,
                "tmdbId": tmdb_id,
                "file": file_path,
            })

    return untracked


def lookup_tmdb_via_radarr(title, year=None):
    """Search for a movie's TMDB ID using Radarr's lookup endpoint."""
    try:
        query = f"{title} {year}" if year else title
        results = radarr_get("/movie/lookup", params={"term": query})
        if results:
            # Find best match
            for r in results:
                r_title = r.get("title", "").lower()
                r_year = r.get("year", 0)
                if r_title == title.lower() and (not year or r_year == int(year)):
                    return str(r.get("tmdbId", ""))
            # Fall back to first result
            return str(results[0].get("tmdbId", ""))
    except Exception:
        pass
    return None


def add_to_radarr(tmdb_id, title, year, root_folder=None, quality_profile_id=None):
    """Add a movie to Radarr by TMDB ID."""
    if not tmdb_id:
        return False, "No TMDB ID"

    # Get default root folder and quality profile if not specified
    if not root_folder:
        root_folders = radarr_get("/rootfolder")
        if root_folders:
            root_folder = root_folders[0].get("path", "")
        else:
            return False, "No root folders configured"

    if not quality_profile_id:
        profiles = radarr_get("/qualityprofile")
        if profiles:
            quality_profile_id = profiles[0].get("id", 1)
        else:
            quality_profile_id = 1

    try:
        # Lookup movie on TMDB via Radarr
        lookup = radarr_get(f"/movie/lookup/tmdb", params={"tmdbId": tmdb_id})
        if not lookup:
            return False, "Not found on TMDB"

        movie_data = lookup if isinstance(lookup, dict) else lookup
        if isinstance(movie_data, list):
            movie_data = movie_data[0] if movie_data else {}

        payload = {
            "tmdbId": int(tmdb_id),
            "title": movie_data.get("title", title),
            "year": movie_data.get("year", year),
            "qualityProfileId": quality_profile_id,
            "rootFolderPath": root_folder,
            "monitored": True,
            "addOptions": {
                "searchForMovie": False,  # Don't search, we already have it
            },
        }

        radarr_post("/movie", payload)
        return True, "OK"
    except requests.exceptions.HTTPError as e:
        if e.response and e.response.status_code == 400:
            return False, "Already exists or bad data"
        return False, str(e)
    except Exception as e:
        return False, str(e)


def auto_add_untracked(dry_run=True):
    """Add all untracked Plex movies to Radarr."""
    print("\n" + "=" * 60)
    print("  RADARR AUTO-ADD")
    print("=" * 60)

    untracked = find_untracked_movies()
    with_tmdb = [m for m in untracked if m["tmdbId"]]
    without_tmdb = [m for m in untracked if not m["tmdbId"]]

    print(f"\n  {len(untracked)} Plex movies not tracked in Radarr")
    print(f"  {len(with_tmdb)} have TMDB IDs (direct add)")
    print(f"  {len(without_tmdb)} missing TMDB IDs (will search Radarr lookup)")

    # Try to find TMDB IDs for movies without them
    if without_tmdb:
        print(f"\n  Looking up TMDB IDs for {len(without_tmdb)} movies...")
        found = 0
        for m in without_tmdb:
            tmdb_id = lookup_tmdb_via_radarr(m["title"], m.get("year"))
            if tmdb_id:
                m["tmdbId"] = tmdb_id
                with_tmdb.append(m)
                found += 1
        still_missing = [m for m in without_tmdb if not m.get("tmdbId")]
        print(f"  Found {found}/{len(without_tmdb)} via Radarr lookup")

        if still_missing:
            print(f"\n  [MANUAL] Could not find TMDB ID ({len(still_missing)}):")
            for m in sorted(still_missing, key=lambda x: x["title"]):
                print(f"    - {m['title']} ({m['year']})")

    if not with_tmdb:
        print("\n  Nothing to add!")
        return

    if dry_run:
        print(f"\n  [DRY RUN] Would add {len(with_tmdb)} movies to Radarr:")
        for m in sorted(with_tmdb, key=lambda x: x["title"]):
            print(f"    - {m['title']} ({m['year']}) [tmdb:{m['tmdbId']}]")
        print("\n  Use --go to add them.")
        return

    print(f"\n  Adding {len(with_tmdb)} movies to Radarr...")
    added = 0
    failed = 0
    for m in sorted(with_tmdb, key=lambda x: x["title"]):
        label = f"{m['title']} ({m['year']})"
        print(f"  Adding {label}...", end=" ", flush=True)
        success, msg = add_to_radarr(m["tmdbId"], m["title"], m["year"])
        if success:
            print("OK")
            added += 1
        else:
            print(f"SKIP ({msg})")
            failed += 1

    print(f"\n  Done: {added} added, {failed} skipped/failed")


def find_upgradeable():
    """Find Radarr movies that could be upgraded in quality."""
    print("\n" + "=" * 60)
    print("  QUALITY UPGRADE HUNTER")
    print("=" * 60)

    radarr = get_radarr_movies()

    upgradeable = []
    for m in radarr:
        if not m.get("hasFile"):
            continue

        movie_file = m.get("movieFile", {})
        quality_name = movie_file.get("quality", {}).get("quality", {}).get("name", "")
        quality_source = movie_file.get("quality", {}).get("quality", {}).get("source", "")

        # Flag anything below Bluray-1080p as upgradeable
        low_qualities = [
            "cam", "telesync", "telecine", "dvd", "dvd-r",
            "webdl-480p", "webrip-480p", "webdl-720p", "webrip-720p",
            "bluray-720p",
        ]

        is_low = any(q in quality_name.lower() for q in low_qualities)
        if not is_low:
            # Also check resolution
            resolution = movie_file.get("mediaInfo", {}).get("height", 0)
            if resolution and resolution < 1080:
                is_low = True

        if is_low:
            upgradeable.append({
                "id": m.get("id"),
                "title": m.get("title", ""),
                "year": m.get("year", ""),
                "quality": quality_name,
                "monitored": m.get("monitored", False),
            })

    if not upgradeable:
        print("\n  All movies are at good quality! Nothing to upgrade.")
        return

    monitored = [m for m in upgradeable if m["monitored"]]
    unmonitored = [m for m in upgradeable if not m["monitored"]]

    print(f"\n  {len(upgradeable)} movies could be upgraded")
    print(f"  {len(monitored)} monitored (Radarr will auto-upgrade)")
    print(f"  {len(unmonitored)} unmonitored (need manual trigger)")

    if unmonitored:
        print(f"\n  Unmonitored low-quality movies ({len(unmonitored)}):")
        for m in sorted(unmonitored, key=lambda x: x["title"]):
            print(f"    - {m['title']} ({m['year']}) -- {m['quality']}")

    return {"upgradeable": upgradeable, "monitored": monitored, "unmonitored": unmonitored}


def trigger_search(movie_ids, dry_run=True):
    """Trigger Radarr to search for upgrades for given movie IDs."""
    if dry_run:
        print(f"\n  [DRY RUN] Would trigger search for {len(movie_ids)} movies")
        return

    print(f"\n  Triggering search for {len(movie_ids)} movies...")
    try:
        radarr_post("/command", {
            "name": "MoviesSearch",
            "movieIds": movie_ids,
        })
        print("  Search triggered! Radarr will look for upgrades.")
    except Exception as e:
        print(f"  Error: {e}")


if __name__ == "__main__":
    dry_run = "--go" not in sys.argv

    if "--upgrade" in sys.argv:
        result = find_upgradeable()
        if result and result.get("unmonitored") and not dry_run:
            ids = [m["id"] for m in result["unmonitored"]]
            trigger_search(ids, dry_run=False)
    else:
        auto_add_untracked(dry_run=dry_run)
